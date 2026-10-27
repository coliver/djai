const test = require("node:test");
const assert = require("node:assert/strict");
const { load, setBeat } = require("./helpers");

const A = "[Channel1]";
const B = "[Channel2]";

let engine, midi;
test.beforeEach(() => {
    ({ engine, midi } = load("DJAI-transition.js", "DJAI-bridge.js"));
});

const HEADER = [0xF0, 0x7D, 0x44, 0x4A];
const text = (s) => Array.from(s, (c) => c.charCodeAt(0));
const command = (s) => [...HEADER, 0x43, ...text(s), 0xF7];

// Decode the replies the bridge sent back: one string per complete reply.
function replies() {
    const out = [];
    let pending = "";
    for (const { bytes, length } of midi.sysex) {
        assert.equal(length, bytes.length);
        assert.deepEqual(bytes.slice(0, 4), HEADER);
        assert.equal(bytes[4], 0x52);
        assert.equal(bytes.at(-1), 0xF7);
        pending += String.fromCharCode(...bytes.slice(6, -1));
        if (bytes[5] === 0x2E) {
            out.push(pending);
            pending = "";
        } else {
            assert.equal(bytes[5], 0x2B);
        }
    }
    return out;
}

const send = (s) => DJAIBridge.incomingData(command(s), command(s).length);

test("reply sends short text as one final chunk", () => {
    DJAIBridge.reply("pong");
    assert.deepEqual(replies(), ["pong"]);
    assert.equal(midi.sysex.length, 1);
});

test("reply sends empty text as one empty final chunk", () => {
    DJAIBridge.reply("");
    assert.deepEqual(replies(), [""]);
});

test("reply splits long text into chunks of 200 and marks the last", () => {
    const long = "x".repeat(450);
    DJAIBridge.reply(long);
    assert.equal(midi.sysex.length, 3);
    assert.deepEqual(midi.sysex.map((m) => m.bytes[5]), [0x2B, 0x2B, 0x2E]);
    assert.deepEqual(replies(), [long]);
});

test("reply ends on a final chunk when the text is exactly one chunk", () => {
    DJAIBridge.reply("y".repeat(200));
    assert.equal(midi.sysex.length, 1);
    assert.equal(midi.sysex[0].bytes[5], 0x2E);
});

test("reply masks characters to 7 bits", () => {
    DJAIBridge.reply("é");
    assert.equal(midi.sysex[0].bytes[6], "é".charCodeAt(0) & 0x7F);
});

test("incomingData ignores messages with the wrong header", () => {
    const bad = command("ping");
    bad[2] = 0x00;
    DJAIBridge.incomingData(bad, bad.length);
    assert.deepEqual(midi.sysex, []);
});

test("incomingData ignores replies echoed back by loopMIDI", () => {
    const echo = command("ping");
    echo[4] = 0x52;
    DJAIBridge.incomingData(echo, echo.length);
    assert.deepEqual(midi.sysex, []);
});

test("incomingData accepts an ArrayBuffer", () => {
    const bytes = Uint8Array.from(command("ping"));
    DJAIBridge.incomingData(bytes.buffer, bytes.length);
    assert.deepEqual(replies(), ["pong"]);
});

test("incomingData runs every ';'-separated command and skips empty ones", () => {
    send("set [Channel1] volume 0.5; ;ping;set [Channel2] volume 1;");
    assert.equal(engine.getValue(A, "volume"), 0.5);
    assert.equal(engine.getValue(B, "volume"), 1);
    assert.deepEqual(replies(), ["pong"]);
});

test("incomingData stops reading at the end-of-SysEx byte or the given length", () => {
    const bytes = [...command("set [Channel1] volume 0.25"), ...text("junk")];
    DJAIBridge.incomingData(bytes, bytes.length);
    assert.equal(engine.getValue(A, "volume"), 0.25);
    const short = command("set [Channel1] volume 0.75").slice(0, -1);
    DJAIBridge.incomingData(short, short.length);
    assert.equal(engine.getValue(A, "volume"), 0.75);
});

test("set and param write controls", () => {
    DJAIBridge.run("set [Channel1] rate 0.01");
    DJAIBridge.run("param [Channel1] volume 0.3");
    assert.deepEqual(engine.calls.filter((c) => c[0] !== "stopTimer"), [
        ["setValue", A, "rate", 0.01],
        ["setParameter", A, "volume", 0.3],
    ]);
});

test("get replies with the value", () => {
    engine.set(A, "volume", 0.8);
    DJAIBridge.run("get [Channel1] volume");
    assert.deepEqual(JSON.parse(replies()[0]), { get: "[Channel1],volume", value: 0.8 });
});

test("state replies with a snapshot", () => {
    DJAIBridge.run("state");
    assert.deepEqual(JSON.parse(replies()[0]), { decks: {}, xfader: 0, mixing: null, pending: 0 });
});

test("unknown commands are reported", () => {
    DJAIBridge.run("frobnicate 1 2");
    assert.deepEqual(JSON.parse(replies()[0]), { error: "unknown command: frobnicate 1 2" });
});

test("a failing command is reported with its text and does not throw", () => {
    engine.setValue = () => { throw new Error("boom"); };
    DJAIBridge.run("set [Channel1] volume 1");
    assert.deepEqual(JSON.parse(replies()[0]), { error: "Error: boom", command: "set [Channel1] volume 1" });
});

test("clear drops pending entries", () => {
    DJAIBridge.run("at [Channel1] 4 set [Channel2] play 1");
    DJAIBridge.run("ramp [Channel1] 0 4 [Channel2] volume 0 1");
    assert.equal(DJAIBridge.scheduled.length, 1);
    assert.equal(DJAIBridge.ramps.length, 1);
    DJAIBridge.run("clear");
    assert.deepEqual([DJAIBridge.scheduled, DJAIBridge.ramps], [[], []]);
});

test("mix starts a transition and reports it", () => {
    engine.set(A, "play", 1);
    engine.set(B, "track_loaded", 1);
    setBeat(engine, A, 2);
    DJAIBridge.run("mix [Channel2] [Channel1] 64");
    assert.deepEqual(JSON.parse(replies()[0]), { mix: "started" });
    assert.equal(DJAI.mix.lengthBeats, 64);
});

test("mix reports why it could not start", () => {
    DJAIBridge.run("mix [Channel2] [Channel1] 64");
    assert.match(JSON.parse(replies()[0]).mix, /need \[Channel1\] playing/);
});

test("abort stops a running blend", () => {
    engine.set(A, "play", 1);
    engine.set(B, "track_loaded", 1);
    setBeat(engine, A, 2);
    DJAIBridge.run("mix [Channel2] [Channel1] 64");
    DJAIBridge.run("abort");
    assert.equal(DJAI.mix, null);
});

test("ping replies pong", () => {
    DJAIBridge.run("ping");
    assert.deepEqual(replies(), ["pong"]);
});

test("at and ramp share one timer", () => {
    DJAIBridge.run("at [Channel1] 4 set [Channel2] play 1");
    DJAIBridge.run("ramp [Channel1] 0 4 [Channel2] volume 0 1");
    assert.equal(engine.timers.size, 1);
    assert.equal(engine.timers.get(DJAIBridge.timer).ms, DJAIBridge.tickMs);
});

test("ramp parses '-' as a start value read when the ramp begins", () => {
    DJAIBridge.run("ramp [Channel1] 0 4 [Channel2] volume - 1");
    assert.equal(DJAIBridge.ramps[0].v0, null);
    DJAIBridge.run("ramp [Channel1] 0 4 [Channel2] volume 0.2 1");
    assert.equal(DJAIBridge.ramps[1].v0, 0.2);
});

test("tick runs 'at' entries once their beat is reached, and only those", () => {
    DJAIBridge.run("at [Channel1] 4 set [Channel2] play 1");
    DJAIBridge.run("at [Channel1] 8 set [Channel2] volume 1");
    setBeat(engine, A, 5);
    DJAIBridge.tick();
    assert.equal(engine.getValue(B, "play"), 1);
    assert.equal(engine.getValue(B, "volume"), 0);
    assert.equal(DJAIBridge.scheduled.length, 1);
    assert.equal(engine.timers.size, 1, "the timer keeps running while entries remain");
    setBeat(engine, A, 9);
    DJAIBridge.tick();
    assert.equal(engine.getValue(B, "volume"), 1);
    assert.equal(engine.timers.size, 0);
    assert.equal(DJAIBridge.timer, 0);
});

test("tick moves a ramp: waits, captures '-' start, interpolates, then drops it", () => {
    engine.set(B, "volume", 0.5);
    DJAIBridge.run("ramp [Channel1] 4 8 [Channel2] volume - 1");
    setBeat(engine, A, 2);
    DJAIBridge.tick();
    assert.equal(engine.getValue(B, "volume"), 0.5, "not started yet");
    assert.equal(DJAIBridge.ramps[0].v0, null);

    setBeat(engine, A, 6);
    DJAIBridge.tick();
    assert.equal(DJAIBridge.ramps[0].v0, 0.5);
    assert.equal(engine.getValue(B, "volume"), 0.75);

    engine.set(B, "volume", 0.1);
    setBeat(engine, A, 7);
    DJAIBridge.tick();
    assert.equal(DJAIBridge.ramps[0].v0, 0.5, "start value is read only once");
    assert.equal(engine.getValue(B, "volume"), 0.875);

    setBeat(engine, A, 9);
    DJAIBridge.tick();
    assert.equal(engine.getValue(B, "volume"), 1);
    assert.deepEqual(DJAIBridge.ramps, []);
    assert.equal(engine.timers.size, 0);
});

test("ensureTimer does not start a second timer", () => {
    DJAIBridge.ensureTimer();
    DJAIBridge.ensureTimer();
    assert.equal(engine.timers.size, 1);
});

test("state lists loaded decks only, with the mix and pending counts", () => {
    engine.set(B, "track_loaded", 1);
    setBeat(engine, B, 2);
    engine.set(B, "play", 1);
    engine.set(B, "volume", 0.7);
    engine.set("[EqualizerRack1_[Channel2]_Effect1]", "parameter2", 0.4);
    engine.set("[QuickEffectRack1_[Channel2]]", "super1", 0.6);
    DJAIBridge.run("at [Channel2] 99 ping");
    const state = DJAIBridge.state();
    assert.deepEqual(Object.keys(state.decks), ["2"]);
    const deck = state.decks[2];
    assert.equal(deck.play, 1);
    assert.equal(deck.vol, 0.7);
    assert.deepEqual(deck.eq, [0, 0.4, 0]);
    assert.equal(deck.filter, 0.6);
    assert.ok(Math.abs(deck.beat - 2) < 1e-9);
    assert.equal(state.pending, 1);
    assert.equal(state.mixing, null);

    DJAI.mix = { outgoing: A, incoming: B };
    assert.equal(DJAIBridge.state().mixing, "[Channel1]->[Channel2]");
});

test("init says hello", () => {
    DJAIBridge.init();
    assert.deepEqual(replies(), ["hello"]);
});

test("shutdown stops the mix and the timer", () => {
    DJAIBridge.run("at [Channel1] 99 ping");
    DJAIBridge.shutdown();
    assert.equal(engine.timers.size, 0);
});

test("shutdown without a timer only stops the mix", () => {
    DJAIBridge.shutdown();
    assert.deepEqual(engine.calls.filter((c) => c[0] === "stopTimer"), []);
});

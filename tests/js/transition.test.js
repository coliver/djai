const test = require("node:test");
const assert = require("node:assert/strict");
const { load, setBeat } = require("./helpers");

const A = "[Channel1]";
const B = "[Channel2]";
const EQ_A = "[EqualizerRack1_[Channel1]_Effect1]";
const EQ_B = "[EqualizerRack1_[Channel2]_Effect1]";

let engine, midi;
test.beforeEach(() => {
    ({ engine, midi } = load("DJAI-transition.js"));
});

// Outgoing deck 1 playing at `beat`, incoming deck 2 loaded and stopped.
function ready(beat = 3) {
    engine.set(A, "play", 1);
    engine.set(A, "file_bpm", 120);
    engine.set(B, "track_loaded", 1);
    setBeat(engine, A, beat);
}

test("deckNumber and eqGroup", () => {
    assert.equal(DJAI.deckNumber(B), 2);
    assert.equal(DJAI.eqGroup(A), EQ_A);
});

test("padLight lights both shift layers of the deck, and ignores a null pad", () => {
    DJAI.padLight(A, null, true);
    assert.deepEqual(midi.short, []);
    DJAI.padLight(A, 2, true);
    DJAI.padLight(B, 0, false);
    assert.deepEqual(midi.short, [
        [0x97, 0x12, 0x7F], [0x98, 0x12, 0x7F],
        [0x99, 0x10, 0x00], [0x9A, 0x10, 0x00],
    ]);
});

test("beatPosition counts beats from the main cue", () => {
    setBeat(engine, A, 12.5);
    assert.ok(Math.abs(DJAI.beatPosition(A) - 12.5) < 1e-9);
});

test("beatPosition treats a missing cue (-1) as the start of the file", () => {
    setBeat(engine, A, 4);
    engine.set(A, "cue_point", -1);
    assert.ok(Math.abs(DJAI.beatPosition(A) - 4) < 1e-9);
});

test("ramp is 0 before, 1 after, linear between", () => {
    assert.equal(DJAI.ramp(0, 10, 20), 0);
    assert.equal(DJAI.ramp(25, 10, 20), 1);
    assert.equal(DJAI.ramp(15, 10, 20), 0.5);
});

test("padPressed ignores a release", () => {
    ready();
    DJAI.padPressed(0, 0x10, 0, 0, B);
    assert.equal(DJAI.mix, null);
});

test("padPressed on the abort pad stops the mix", () => {
    ready();
    DJAI.padPressed(0, 0x10, 127, 0, B);
    assert.ok(DJAI.mix);
    DJAI.padPressed(0, 0x17, 127, 0, B);
    assert.equal(DJAI.mix, null);
    assert.match(engine.logs.at(-1), /aborted/);
});

test("padPressed ignores a pad with no blend length", () => {
    ready();
    DJAI.padPressed(0, 0x13, 127, 0, B);
    assert.equal(DJAI.mix, null);
});

test("padPressed picks the other deck as outgoing and the blend length from the pad", () => {
    ready();
    DJAI.padPressed(0, 0x11, 127, 0, B);
    assert.equal(DJAI.mix.outgoing, A);
    assert.equal(DJAI.mix.incoming, B);
    assert.equal(DJAI.mix.lengthBeats, 64);
    assert.equal(DJAI.mix.pad, 1);
});

test("padPressed from deck 1 treats deck 2 as outgoing", () => {
    engine.set(B, "play", 1);
    engine.set(B, "file_bpm", 120);
    engine.set(A, "track_loaded", 1);
    setBeat(engine, B, 1);
    DJAI.padPressed(0, 0x12, 127, 0, A);
    assert.equal(DJAI.mix.outgoing, B);
    assert.equal(DJAI.mix.lengthBeats, 32);
});

test("startTransition refuses while a transition runs", () => {
    ready();
    assert.equal(DJAI.startTransition(B, A, 128, null), null);
    assert.equal(DJAI.startTransition(B, A, 128, null), "a transition is already running");
});

for (const [name, setup] of [
    ["outgoing deck is not playing", () => engine.set(A, "play", 0)],
    ["incoming deck has no track", () => engine.set(B, "track_loaded", 0)],
    ["incoming deck is already playing", () => engine.set(B, "play", 1)],
    ["outgoing deck has no bpm", () => engine.set(A, "file_bpm", 0)],
]) {
    test(`startTransition refuses when the ${name}`, () => {
        ready();
        setup();
        const error = DJAI.startTransition(B, A, 128, null);
        assert.match(error, /need \[Channel1\] playing and a stopped, loaded track on \[Channel2\]/);
        assert.equal(DJAI.mix, null);
        assert.equal(engine.logs.length, 1);
    });
}

test("startTransition prepares the incoming deck and starts on the next phrase", () => {
    ready(3);
    engine.set("[Master]", "crossfader", -1);
    assert.equal(DJAI.startTransition(B, A, 128, 0), null);
    assert.equal(DJAI.mix.startBeat, 32);
    assert.equal(DJAI.mix.crossfaderFrom, -1);
    assert.equal(engine.getValue(B, "volume"), 0);
    assert.equal(engine.getValue(EQ_B, "parameter1"), 0);
    assert.equal(engine.getValue(B, "cue_gotoandstop"), 1);
    assert.equal(engine.getValue(B, "quantize"), 1);
    assert.equal(engine.getValue(A, "sync_enabled"), 1);
    assert.equal(engine.getValue(B, "sync_enabled"), 1);
    assert.equal(engine.timers.get(DJAI.mix.timer).ms, DJAI.tickMs);
    assert.match(engine.logs[0], /32 bars, starting at beat 32/);
});

test("startTransition waits a whole phrase when the lead-in is too short", () => {
    ready(31);
    DJAI.startTransition(B, A, 128, null);
    assert.equal(DJAI.mix.startBeat, 64);
});

test("tick stops the mix when the outgoing deck stops", () => {
    ready();
    DJAI.startTransition(B, A, 128, null);
    engine.set(A, "play", 0);
    DJAI.tick();
    assert.equal(DJAI.mix, null);
    assert.match(engine.logs.at(-1), /outgoing deck stopped/);
});

test("tick while waiting glides the crossfader, blinks the pad, then starts the deck", () => {
    ready(3);
    engine.set("[Master]", "crossfader", -1);
    DJAI.startTransition(B, A, 128, 0);
    const id = DJAI.mix.timer;

    setBeat(engine, A, 16);
    engine.tick(id);
    const glide = (16 - 3) / (31 - 3);
    assert.ok(Math.abs(engine.getValue("[Master]", "crossfader") - -(1 - glide)) < 1e-9);
    assert.equal(DJAI.mix.started, false);
    const lit = midi.short.length;
    assert.ok(lit > 0);

    // Same half-beat: no new pad message.
    setBeat(engine, A, 16.2);
    engine.tick(id);
    assert.equal(midi.short.length, lit);

    // Next half-beat flips the blink.
    setBeat(engine, A, 16.7);
    engine.tick(id);
    assert.equal(midi.short.length, lit + 2);

    setBeat(engine, A, 31.8);
    engine.tick(id);
    assert.equal(DJAI.mix.started, true);
    assert.equal(engine.getValue(B, "play"), 1);
    assert.deepEqual(midi.short.at(-1), [0x9A, 0x10, 0x7F]);
});

test("tick during the blend ramps volume, swaps basslines once, and ends the mix", () => {
    ready(3);
    DJAI.startTransition(B, A, 128, null);
    const id = DJAI.mix.timer;
    DJAI.mix.started = true;
    engine.defaults.set(`${EQ_B}|parameter1`, 0.75);
    engine.defaults.set(`${EQ_A}|parameter1`, 0.25);

    setBeat(engine, A, 32 + 16); // t = 16 of 128: incoming fader at half
    engine.tick(id);
    assert.equal(engine.getValue(B, "volume"), 0.5);
    assert.equal(DJAI.mix.swapped, false);
    assert.equal(engine.getValue(A, "volume"), 1);

    setBeat(engine, A, 32 + 50); // past a third but short of halfway: still no swap
    engine.tick(id);
    assert.equal(DJAI.mix.swapped, false);

    setBeat(engine, A, 32 + 64); // halfway: bass swap
    engine.tick(id);
    assert.equal(DJAI.mix.swapped, true);
    assert.equal(engine.getValue(EQ_A, "parameter1"), 0);
    assert.equal(engine.getValue(EQ_B, "parameter1"), 0.75);

    engine.set(EQ_B, "parameter1", 0.1);
    setBeat(engine, A, 32 + 80); // swapped already: bass is not touched again
    engine.tick(id);
    assert.equal(engine.getValue(EQ_B, "parameter1"), 0.1);
    assert.ok(Math.abs(engine.getValue(A, "volume") - (1 - 16 / 32)) < 1e-9);

    setBeat(engine, A, 32 + 128);
    engine.tick(id);
    assert.equal(engine.getValue(A, "play"), 0);
    assert.equal(DJAI.mix, null);
    assert.equal(engine.getValue(A, "volume"), 1);
    assert.equal(engine.getValue(EQ_A, "parameter1"), 0.25);
    assert.equal(engine.getValue(A, "sync_enabled"), 0);
    assert.equal(engine.getValue(B, "sync_enabled"), 0);
    assert.match(engine.logs.at(-1), /transition done/);
});

test("stop does nothing without a mix", () => {
    DJAI.stop("whatever");
    assert.deepEqual(engine.logs, []);
});

test("stop with another reason leaves levels where they are", () => {
    ready();
    DJAI.startTransition(B, A, 128, 1);
    engine.set(A, "volume", 0.4);
    DJAI.stop("aborted");
    assert.equal(engine.getValue(A, "volume"), 0.4);
    assert.equal(engine.getValue(A, "sync_enabled"), 1);
    assert.equal(DJAI.mix, null);
});

test("init does nothing and shutdown stops a running mix", () => {
    DJAI.init();
    ready();
    DJAI.startTransition(B, A, 128, null);
    DJAI.shutdown();
    assert.equal(DJAI.mix, null);
    assert.match(engine.logs.at(-1), /controller shutdown/);
});

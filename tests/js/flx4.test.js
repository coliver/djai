const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./helpers");

const D1 = "[Channel1]";
const D2 = "[Channel2]";
const FX = "[EffectRack1_EffectUnit1]";
const fx = (n) => `[EffectRack1_EffectUnit1_Effect${n}]`;

let engine, midi, script;
test.beforeEach(() => {
    ({ engine, midi, script } = load("Pioneer-DDJ-FLX4-DJAI-script.js"));
});

const P = () => PioneerDDJFLX4;
const last = () => midi.short.at(-1);

test("sendKeepAlive sends the 12 byte SysEx", () => {
    P().sendKeepAlive();
    assert.equal(midi.sysex.length, 1);
    assert.equal(midi.sysex[0].length, 12);
    assert.equal(midi.sysex[0].bytes[0], 0xF0);
    assert.equal(midi.sysex[0].bytes[11], 0xF7);
});

test("trackLoadedLED lights deck n-1 when loaded, clears it otherwise", () => {
    P().trackLoadedLED(1, D2, "track_loaded");
    P().trackLoadedLED(0, D1, "track_loaded");
    assert.deepEqual(midi.short, [[0x9F, 1, 0x7F], [0x9F, 0, 0]]);
});

test("toggleLight sends full or zero velocity", () => {
    P().toggleLight({ status: 0x94, data1: 0x47 }, true);
    P().toggleLight({ status: 0x94, data1: 0x47 }, false);
    assert.deepEqual(midi.short, [[0x94, 0x47, 0x7F], [0x94, 0x47, 0]]);
});

test("init wires up the controller and raises the sampler count when it is low", () => {
    engine.set("[App]", "num_samplers", 8);
    P().init();
    assert.equal(engine.getValue("[App]", "num_samplers"), 16);
    assert.equal(engine.getValue(FX, "show_focus"), 1);
    const keys = engine.connections.map((c) => `${c.group}|${c.key}`);
    assert.ok(keys.includes("[Channel1]|vu_meter"));
    assert.ok(keys.includes("[Sampler16]|play"));
    assert.ok(keys.includes("[Channel2]|track_loaded"));
    assert.ok(keys.includes(`${FX}|focused_effect`));
    assert.ok(keys.includes(`${fx(3)}|enabled`));
    assert.equal(engine.timers.get(P().keepAliveTimer).ms, 200);
    assert.equal(midi.sysex.length, 1, "the startup query is sent once");
    assert.ok(engine.calls.some((c) => c[0] === "softTakeover" && c[1] === D1 && c[2] === "rate"));
});

test("init leaves the sampler count alone when it is already high enough", () => {
    engine.set("[App]", "num_samplers", 64);
    P().init();
    assert.equal(engine.getValue("[App]", "num_samplers"), 64);
});

test("waveformZoom zooms up on 0x7f and down otherwise", () => {
    P().waveformZoom(0, 0, 0x7f, 0, D1);
    P().waveformZoom(0, 0, 0x01, 0, D1);
    assert.deepEqual(script.calls, [
        ["triggerControl", D1, "waveform_zoom_up", 100],
        ["triggerControl", D1, "waveform_zoom_down", 100],
    ]);
});

test("vuMeterUpdate follows the deck, ignoring other groups", () => {
    P().vuMeterUpdate(0.5, D1);
    P().vuMeterUpdate(1, D2);
    P().vuMeterUpdate(1, "[Master]");
    assert.deepEqual(midi.short, [[0xB0, 0x02, 63.5], [0xB1, 0x02, 127]]);
});

test("focusedFxGroup names the focused effect slot", () => {
    engine.set(FX, "focused_effect", 2);
    assert.equal(P().focusedFxGroup(), fx(2));
});

test("toggleFxLight mirrors the focused effect's state on both beat FX lights", () => {
    engine.set(FX, "focused_effect", 1);
    engine.set(fx(1), "enabled", 1);
    P().toggleFxLight(1, FX, "x");
    assert.deepEqual(midi.short, [[0x94, 0x47, 0x7F], [0x94, 0x43, 0x7F]]);
    engine.set(fx(1), "enabled", 0);
    P().toggleFxLight(0, FX, "x");
    assert.deepEqual(midi.short.slice(2), [[0x94, 0x47, 0], [0x94, 0x43, 0]]);
});

test("beatFxLevelDepthRotate sets the dry/wet mix, or the effect meta with either SHIFT held", () => {
    engine.set(FX, "focused_effect", 1);
    P().beatFxLevelDepthRotate(0, 0, 127);
    assert.deepEqual(engine.calls.at(-1), ["setParameter", FX, "mix", 1]);
    for (const deck of [0, 1]) {
        P().shiftButtonDown = [false, false];
        P().shiftButtonDown[deck] = true;
        P().beatFxLevelDepthRotate(0, 0, 0x7F / 2);
        assert.deepEqual(engine.calls.at(-1), ["setParameter", fx(1), "meta", 0.5]);
    }
});

test("changeFocusedEffectBy wraps in both directions", () => {
    const focus = () => engine.getValue(FX, "focused_effect");
    engine.set(FX, "focused_effect", 3);
    P().changeFocusedEffectBy(1);
    assert.equal(focus(), 1);
    P().changeFocusedEffectBy(-1);
    assert.equal(focus(), 3);
    P().changeFocusedEffectBy(-1);
    assert.equal(focus(), 2);
});

test("beat FX buttons ignore releases", () => {
    for (const name of ["beatFxSelectPressed", "beatFxSelectShiftPressed", "beatFxLeftPressed", "beatFxRightPressed",
        "beatFxOnOffPressed", "beatFxOnOffShiftPressed"]) {
        P()[name](0, 0, 0);
    }
    assert.deepEqual(engine.calls, []);
    assert.deepEqual(midi.short, []);
});

test("beat FX select moves through the effect list", () => {
    engine.set(FX, "focused_effect", 1);
    P().beatFxSelectPressed(0, 0, 127);
    P().beatFxSelectShiftPressed(0, 0, 127);
    assert.equal(engine.getValue(fx(1), "next_effect"), 127);
    assert.equal(engine.getValue(fx(1), "prev_effect"), 127);
});

test("beat FX left and right move the focus", () => {
    engine.set(FX, "focused_effect", 1);
    P().beatFxRightPressed(0, 0, 127);
    assert.equal(engine.getValue(FX, "focused_effect"), 2);
    P().beatFxLeftPressed(0, 0, 127);
    assert.equal(engine.getValue(FX, "focused_effect"), 1);
});

test("beat FX on/off toggles the focused effect", () => {
    engine.set(FX, "focused_effect", 2);
    P().beatFxOnOffPressed(0, 0, 127);
    assert.equal(engine.getValue(fx(2), "enabled"), true);
    P().beatFxOnOffPressed(0, 0, 127);
    assert.equal(engine.getValue(fx(2), "enabled"), false);
});

test("SHIFT + beat FX on/off switches everything off and darkens the lights", () => {
    for (let i = 1; i <= 3; i++) {
        engine.set(fx(i), "enabled", 1);
    }
    P().beatFxOnOffShiftPressed(0, 0, 127);
    for (let i = 1; i <= 3; i++) {
        assert.equal(engine.getValue(fx(i), "enabled"), 0);
    }
    assert.equal(engine.getValue(FX, "mix"), 0);
    assert.deepEqual(midi.short, [[0x94, 0x47, 0], [0x94, 0x43, 0]]);
});

test("beat FX channel assign buttons enable on 0x7f only", () => {
    P().beatFxChannel1(0, 0, 0x7f, 0, FX);
    P().beatFxChannel2(0, 0, 0x7f, 0, FX);
    assert.equal(engine.getValue(FX, "group_[Channel1]_enable"), 1);
    assert.equal(engine.getValue(FX, "group_[Channel2]_enable"), 1);
    P().beatFxChannel1(0, 0, 0, 0, FX);
    P().beatFxChannel2(0, 0, 1, 0, FX);
    assert.equal(engine.getValue(FX, "group_[Channel1]_enable"), 0);
    assert.equal(engine.getValue(FX, "group_[Channel2]_enable"), 0);
});

test("loop adjust buttons toggle in and out exclusively, and ignore releases", () => {
    engine.set(D1, "loop_enabled", 1);
    P().toggleLoopAdjustIn(0, 0, 0, 0, D1);
    P().toggleLoopAdjustOut(0, 0, 0, 0, D1);
    assert.deepEqual([P().loopAdjustIn[0], P().loopAdjustOut[0]], [false, false]);

    P().toggleLoopAdjustIn(0, 0, 127, 0, D1);
    assert.deepEqual([P().loopAdjustIn[0], P().loopAdjustOut[0]], [true, false]);
    P().toggleLoopAdjustOut(0, 0, 127, 0, D1);
    assert.deepEqual([P().loopAdjustIn[0], P().loopAdjustOut[0]], [false, true]);
    P().toggleLoopAdjustIn(0, 0, 127, 0, D1);
    P().toggleLoopAdjustIn(0, 0, 127, 0, D1);
    assert.deepEqual([P().loopAdjustIn[0], P().loopAdjustOut[0]], [false, false]);
});

test("loop adjust buttons do nothing while no loop is enabled", () => {
    P().toggleLoopAdjustIn(0, 0, 127, 0, D1);
    P().toggleLoopAdjustOut(0, 0, 127, 0, D1);
    assert.deepEqual([P().loopAdjustIn[0], P().loopAdjustOut[0]], [false, false]);
});

test("setReloopLight and setLoopButtonLights send to every light of the group", () => {
    P().setReloopLight(0x90, 0x7F);
    assert.deepEqual(midi.short, [[0x90, 0x4D, 0x7F], [0x90, 0x50, 0x7F]]);
    midi.short.length = 0;
    P().setLoopButtonLights(0x91, 0);
    assert.deepEqual(midi.short.map((m) => m[1]), [0x10, 0x11, 0x4E, 0x4C]);
});

test("loop light blink alternates, dimming in/out lights while the other is adjusted", () => {
    P().startLoopLightsBlink(0, "loop_enabled", 0x90, D1);
    const id = P().timers[D1].loop_enabled;
    assert.equal(engine.timers.get(id).ms, 500);
    midi.short.length = 0;

    engine.tick(id); // blink = 0 (0x7F - 0x7F)
    assert.deepEqual(midi.short, [[0x90, 0x10, 0], [0x90, 0x4C, 0], [0x90, 0x11, 0], [0x90, 0x4E, 0]]);

    midi.short.length = 0;
    P().loopAdjustOut[0] = true;
    engine.tick(id); // blink = 0x7F; adjusting out: in light dark, out light blinks
    assert.deepEqual(midi.short, [[0x90, 0x10, 0], [0x90, 0x4C, 0], [0x90, 0x11, 0x7F], [0x90, 0x4E, 0x7F]]);

    midi.short.length = 0;
    P().loopAdjustOut[0] = false;
    P().loopAdjustIn[0] = true;
    engine.tick(id);
    assert.deepEqual(midi.short, [[0x90, 0x10, 0], [0x90, 0x4C, 0], [0x90, 0x11, 0], [0x90, 0x4E, 0]]);

    midi.short.length = 0;
    engine.tick(id);
    assert.deepEqual(midi.short, [[0x90, 0x10, 0x7F], [0x90, 0x4C, 0x7F], [0x90, 0x11, 0], [0x90, 0x4E, 0]]);
});

test("restarting the loop blink stops the old timer first", () => {
    P().startLoopLightsBlink(0, "loop_enabled", 0x90, D1);
    const first = P().timers[D1].loop_enabled;
    P().startLoopLightsBlink(0, "loop_enabled", 0x90, D1);
    assert.equal(engine.timers.has(first), false);
    assert.notEqual(P().timers[D1].loop_enabled, first);
});

test("stopLoopLightsBlink with no timer just relights the buttons", () => {
    P().stopLoopLightsBlink(D1, "loop_enabled", 0x90);
    assert.equal(P().timers[D1].loop_enabled, undefined);
    assert.equal(midi.short.length, 4);
    assert.ok(midi.short.every((m) => m[2] === 0x7F));
});

test("loopToggle: loop on lights reloop and starts blinking; off stops and clears the adjusts", () => {
    P().loopToggle(1, D2, "loop_enabled");
    assert.deepEqual(midi.short.slice(0, 2), [[0x91, 0x4D, 0x7F], [0x91, 0x50, 0x7F]]);
    assert.ok(P().timers[D2].loop_enabled);

    P().loopAdjustIn[1] = true;
    P().loopAdjustOut[1] = true;
    midi.short.length = 0;
    P().loopToggle(0, D2, "loop_enabled");
    assert.deepEqual(midi.short.slice(0, 2), [[0x91, 0x4D, 0], [0x91, 0x50, 0]]);
    assert.equal(P().timers[D2].loop_enabled, undefined);
    assert.deepEqual([P().loopAdjustIn[1], P().loopAdjustOut[1]], [false, false]);

    P().loopToggle(1, D1, "loop_enabled");
    assert.equal(midi.short.at(-1)[0] >= 0x90, true);
    assert.ok(P().timers[D1].loop_enabled);
});

test("CUE/LOOP CALL arrows halve and double the loop on press only", () => {
    P().cueLoopCallLeft(0, 0, 0, 0, D1);
    P().cueLoopCallRight(0, 0, 0, 0, D1);
    assert.deepEqual(engine.calls, []);
    P().cueLoopCallLeft(0, 0, 1, 0, D1);
    P().cueLoopCallRight(0, 0, 1, 0, D1);
    assert.equal(engine.getValue(D1, "loop_scale"), 2);
    assert.deepEqual(engine.writes(D1, "loop_scale"), [0.5, 2]);
});

test("syncPressed toggles sync off when enabled, else beat-syncs", () => {
    engine.set(D1, "sync_enabled", 1);
    P().syncPressed(0, 0, 127, 0, D1);
    assert.equal(engine.getValue(D1, "sync_enabled"), 0);
    P().syncPressed(0, 0, 127, 0, D1);
    assert.equal(engine.getValue(D1, "beatsync"), 127);
    engine.set(D1, "sync_enabled", 1);
    P().syncPressed(0, 0, 0, 0, D1); // release while enabled: beatsync 0
    assert.equal(engine.getValue(D1, "beatsync"), 0);
    assert.equal(engine.getValue(D1, "sync_enabled"), 1);
});

test("syncLongPressed enables sync on press only", () => {
    P().syncLongPressed(0, 0, 0, 0, D1);
    assert.equal(engine.getValue(D1, "sync_enabled"), 0);
    P().syncLongPressed(0, 0, 1, 0, D1);
    assert.equal(engine.getValue(D1, "sync_enabled"), 1);
});

test("cycleTempoRange steps through the ranges, wraps, and restarts from an unknown range", () => {
    const range = () => engine.getValue(D1, "rateRange");
    P().cycleTempoRange(0, 0, 0, 0, D1);
    assert.deepEqual(engine.calls, []);

    engine.set(D1, "rateRange", 0.06);
    P().cycleTempoRange(0, 0, 127, 0, D1);
    assert.equal(range(), 0.10);
    engine.set(D1, "rateRange", 0.25);
    P().cycleTempoRange(0, 0, 127, 0, D1);
    assert.equal(range(), 0.06);
    engine.set(D1, "rateRange", 0.33);
    P().cycleTempoRange(0, 0, 127, 0, D1);
    assert.equal(range(), 0.06);
});

test("jogTurn bends when not scratching and scratches when touched", () => {
    P().jogTurn(0, 0, 74, 0, D1);
    assert.equal(engine.getValue(D1, "jog"), 10 * 0.8);
    engine.scratching = true;
    P().jogTurn(1, 0, 54, 0, D2);
    assert.deepEqual(engine.calls.at(-1), ["scratchTick", 2, -10]);
});

test("jogTurn moves the loop in or out point while adjusting an enabled loop", () => {
    engine.set(D1, "loop_enabled", 1);
    engine.set(D1, "loop_start_position", 1000);
    engine.set(D1, "loop_end_position", 5000);

    P().loopAdjustIn[0] = true;
    P().jogTurn(0, 0, 66, 0, D1);
    assert.equal(engine.getValue(D1, "loop_start_position"), 2 * 50 + 1000);

    P().loopAdjustIn[0] = false;
    P().loopAdjustOut[0] = true;
    P().jogTurn(0, 0, 62, 0, D1);
    assert.equal(engine.getValue(D1, "loop_end_position"), -2 * 50 + 5000);
    assert.equal(engine.getValue(D1, "jog"), 0);
});

test("jogTurn ignores the loop adjust flags when no loop is enabled", () => {
    P().loopAdjustIn[0] = true;
    P().loopAdjustOut[0] = true;
    P().jogTurn(0, 0, 65, 0, D1);
    assert.equal(engine.getValue(D1, "jog"), 0.8);
    assert.equal(engine.getValue(D1, "loop_start_position"), 0);
});

test("jogTurn with an enabled loop but nothing being adjusted still bends", () => {
    engine.set(D1, "loop_enabled", 1);
    P().jogTurn(0, 0, 65, 0, D1);
    assert.equal(engine.getValue(D1, "jog"), 0.8);
});

test("jogSearch seeks fast", () => {
    P().jogSearch(0, 0, 66, 0, D1);
    assert.equal(engine.getValue(D1, "jog"), 2 * 150);
});

test("jogTouch scratches on touch in vinyl mode, releases otherwise", () => {
    P().jogTouch(0, 0, 127);
    assert.deepEqual(engine.calls.at(-1), ["scratchEnable", 1, 720, 33 + 1 / 3, 1 / 8, 1 / 8 / 32]);
    P().jogTouch(1, 0, 0);
    assert.deepEqual(engine.calls.at(-1), ["scratchDisable", 2]);
    P().vinylMode = false;
    P().jogTouch(0, 0, 127);
    assert.deepEqual(engine.calls.at(-1), ["scratchDisable", 1]);
});

test("jogTouch does nothing while a loop point is being adjusted", () => {
    P().loopAdjustIn[0] = true;
    P().jogTouch(0, 0, 127);
    P().loopAdjustIn[0] = false;
    P().loopAdjustOut[0] = true;
    P().jogTouch(0, 0, 127);
    assert.deepEqual(engine.calls, []);
});

test("shiftPressed tracks the SHIFT state per deck", () => {
    P().shiftPressed(0, 0, 0x7F, 0, D1);
    P().shiftPressed(1, 0, 0x01, 0, D2);
    assert.deepEqual(P().shiftButtonDown, [true, false]);
});

test("tempo slider combines MSB and LSB into the rate, inverted", () => {
    P().tempoSliderMSB(0, 0, 64, 0, D1);
    P().tempoSliderLSB(0, 0, 0, 0, D1);
    assert.equal(engine.getValue(D1, "rate"), 0);
    P().tempoSliderMSB(0, 0, 0, 0, D2);
    P().tempoSliderLSB(0, 0, 0, 0, D2);
    assert.equal(engine.getValue(D2, "rate"), 1);
});

test("beatjump pad jumps by the pad's size, signed", () => {
    P().beatjumpPadPressed(0, 0x22, 0, 0, D1);
    assert.deepEqual(engine.calls, []);
    P().beatjumpPadPressed(0, 0x22, 127, 0, D1);
    assert.equal(engine.getValue(D1, "beatjump_size"), 2);
    assert.equal(engine.getValue(D1, "beatjump"), -2);
});

test("increaseBeatjumpSizes multiplies by 16 once, then stops at the limit", () => {
    P().increaseBeatjumpSizes(0, 0, 0, 0, D1);
    assert.equal(P().beatjumpSizeForPad[0x21], 1);
    P().increaseBeatjumpSizes(0, 0, 127, 0, D1);
    assert.equal(P().beatjumpSizeForPad[0x21], 16);
    assert.equal(P().beatjumpSizeForPad[0x20], -16);
    assert.equal(engine.getValue(D1, "beatjump_size"), 16);
    P().increaseBeatjumpSizes(0, 0, 127, 0, D1);
    assert.equal(P().beatjumpSizeForPad[0x21], 16);
});

test("decreaseBeatjumpSizes divides by 16 once, then stops at the limit", () => {
    P().decreaseBeatjumpSizes(0, 0, 0, 0, D1);
    assert.equal(P().beatjumpSizeForPad[0x21], 1);
    P().decreaseBeatjumpSizes(0, 0, 127, 0, D1);
    assert.equal(P().beatjumpSizeForPad[0x21], 1 / 16);
    assert.equal(engine.getValue(D1, "beatjump_size"), 1 / 16);
    P().decreaseBeatjumpSizes(0, 0, 127, 0, D1);
    assert.equal(P().beatjumpSizeForPad[0x21], 1 / 16);
});

test("sampler play output lights the right pad for each of the 16 samplers", () => {
    const status = (n) => {
        midi.short.length = 0;
        engine.timers.clear();
        P().timers = {};
        P().samplerPlayOutputCallbackFunction(1, `[Sampler${n}]`, "play");
        const [id] = engine.timers.keys();
        engine.tick(id);
        return midi.short[0];
    };
    // [status, control] of the first blink message for each sampler
    assert.deepEqual(status(1), [0x97, 0x30, 0]);
    assert.deepEqual(status(4), [0x97, 0x33, 0]);
    assert.deepEqual(status(5), [0x99, 0x30, 0]);
    assert.deepEqual(status(8), [0x99, 0x33, 0]);
    assert.deepEqual(status(9), [0x97, 0x34, 0]);
    assert.deepEqual(status(12), [0x97, 0x37, 0]);
    assert.deepEqual(status(13), [0x99, 0x34, 0]);
    assert.deepEqual(status(16), [0x99, 0x37, 0]);
    assert.deepEqual(status(20), [0x97, 0x30, 0]);
});

test("sampler play output ignores a stop", () => {
    P().samplerPlayOutputCallbackFunction(0, "[Sampler1]", "play");
    assert.equal(engine.timers.size, 0);
});

test("sampler blink flips the pad until the sampler stops, then leaves it lit", () => {
    P().startSamplerBlink(0x97, 0x30, "[Sampler1]");
    const id = P().timers[0x97][0x30];
    assert.equal(engine.timers.get(id).ms, 250);
    engine.set("[Sampler1]", "play", 1);
    engine.tick(id);
    assert.deepEqual(midi.short, [[0x97, 0x30, 0], [0x98, 0x30, 0]]);
    engine.tick(id);
    assert.deepEqual(midi.short.slice(2), [[0x97, 0x30, 0x7f], [0x98, 0x30, 0x7f]]);
    midi.short.length = 0;
    engine.set("[Sampler1]", "play", 0);
    engine.tick(id);
    assert.equal(engine.timers.has(id), false);
    assert.deepEqual(midi.short.slice(-2), [[0x97, 0x30, 0x7f], [0x98, 0x30, 0x7f]]);
    assert.equal(P().timers[0x97][0x30], undefined);
});

test("stopSamplerBlink copes with no timers at all", () => {
    P().stopSamplerBlink(0x97, 0x31);
    assert.deepEqual(P().timers[0x97], {});
});

test("padModeKeyPressed lights the matching mode button on the right deck", () => {
    const modes = {
        0x1B: "hotcueMode", 0x69: "keyboardMode", 0x1E: "padFX1Mode", 0x6B: "padFX2Mode",
        0x20: "beatJumpMode", 0x6D: "beatLoopMode", 0x22: "samplerMode", 0x6F: "keyShiftMode",
    };
    for (const [control, mode] of Object.entries(modes)) {
        for (const [status, deck] of [[0x90, "deck1"], [0x91, "deck2"]]) {
            midi.short.length = 0;
            P().padModeKeyPressed(0, Number(control), 127, status, D1);
            const light = P().lights[deck][mode];
            assert.deepEqual(midi.short, [[light.status, light.data1, 0x7F]]);
            assert.equal(light.status, status);
        }
    }
    midi.short.length = 0;
    P().padModeKeyPressed(0, 0x01, 127, 0x90, D1);
    assert.deepEqual(midi.short, []);
});

test("samplerPadPressed plays a loaded sampler, loads an empty one", () => {
    engine.set("[Sampler1]", "track_loaded", 1);
    P().samplerPadPressed(0, 0, 127, 0, "[Sampler1]");
    assert.equal(engine.getValue("[Sampler1]", "cue_gotoandplay"), 127);
    P().samplerPadPressed(0, 0, 127, 0, "[Sampler2]");
    assert.equal(engine.getValue("[Sampler2]", "LoadSelectedTrack"), 127);
});

test("samplerPadShiftPressed stops a playing sampler, ejects a loaded one, ignores an empty one", () => {
    engine.set("[Sampler1]", "play", 1);
    P().samplerPadShiftPressed(0, 0, 127, 0, "[Sampler1]");
    assert.equal(engine.getValue("[Sampler1]", "cue_gotoandstop"), 127);
    engine.set("[Sampler2]", "track_loaded", 1);
    P().samplerPadShiftPressed(0, 0, 127, 0, "[Sampler2]");
    assert.equal(engine.getValue("[Sampler2]", "eject"), 127);
    engine.calls.length = 0;
    P().samplerPadShiftPressed(0, 0, 127, 0, "[Sampler3]");
    assert.deepEqual(engine.calls, []);
});

test("play brakes a playing deck and starts a stopped one", () => {
    P().playPressed(0, 0, 0, 0, D1);
    assert.deepEqual(engine.calls, []);
    P().playPressed(0, 0, 127, 0, D1);
    assert.equal(engine.getValue(D1, "play"), 1);
    P().playPressed(0, 0, 127, 0, D1);
    assert.deepEqual(engine.calls.at(-1), ["brake", 1, true, 1.0, 0.9]);
    engine.set(D2, "play", 1);
    P().playPressed(0, 0, 127, 0, D2);
    assert.deepEqual(engine.calls.at(-1), ["brake", 2, true, 1.0, 0.9]);
});

test("toggleQuantize toggles on press only", () => {
    P().toggleQuantize(0, 0, 0, 0, D1);
    assert.deepEqual(script.calls, []);
    P().toggleQuantize(0, 0, 1, 0, D1);
    assert.deepEqual(script.calls, [["toggleControl", D1, "quantize"]]);
});

test("quick jumps move 32 beats on press only", () => {
    P().quickJumpForward(0, 0, 0, 0, D1);
    P().quickJumpBack(0, 0, 0, 0, D1);
    assert.deepEqual(engine.calls, []);
    P().quickJumpForward(0, 0, 1, 0, D1);
    P().quickJumpBack(0, 0, 1, 0, D1);
    assert.deepEqual(engine.writes(D1, "beatjump"), [32, -32]);
});

test("shutdown darkens every light and stops the keep-alive", () => {
    P().init();
    midi.short.length = 0;
    const keepAlive = P().keepAliveTimer;
    P().shutdown();
    assert.equal(engine.timers.has(keepAlive), false);
    assert.ok(midi.short.length > 64);
    assert.ok(midi.short.every((m) => m[2] === 0));
    assert.ok(midi.short.some((m) => m[0] === 0x9A && m[1] === 0x37));
    assert.ok(midi.short.some((m) => m[0] === 0x98 && m[1] === 0x07));
});

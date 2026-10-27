// DJAI-transition.js
// ****************************************************************************
// * Phrase-aligned automatic transitions for the DDJ-FLX4 (DJAI mapping).
// *
// * Trigger: put the INCOMING deck in PAD FX1 mode and press a pad.
// *     PAD 1  32-bar blend
// *     PAD 2  16-bar blend
// *     PAD 3   8-bar blend
// *     PAD 8  abort (automation stops, levels stay where they are)
// *
// * The playing deck is the outgoing one. The incoming deck starts from its
// * main cue on the next 8-bar phrase boundary of the outgoing deck, beat
// * matched via sync. Its channel fader rises over the first quarter of the
// * mix with the bass killed, the basslines swap at the halfway point, the
// * outgoing fader falls over the third quarter, and the outgoing deck stops
// * at the end with its fader and EQ put back to neutral.
// *
// * The same engine is loaded by the DJAI Bridge mapping, where Claude starts
// * it remotely through DJAI.startTransition().
// ****************************************************************************

var DJAI = {};

DJAI.phraseBeats = 32; // 8 bars
DJAI.leadInBeats = 2;  // minimum warning before the incoming deck starts
DJAI.tickMs = 20;
DJAI.mixBeatsForPad = {0: 128, 1: 64, 2: 32};
DJAI.abortPad = 7;

DJAI.mix = null;

DJAI.deckNumber = function(group) {
    return parseInt(group.match(script.channelRegEx)[1]);
};

DJAI.eqGroup = function(group) {
    return "[EqualizerRack1_" + group + "_Effect1]";
};

// Pad LED in PAD FX1 mode (pads 0x10-0x17), also lit while SHIFT is held.
DJAI.padLight = function(group, pad, on) {
    if (pad === null) {
        return;
    }
    const status = 0x97 + 2 * (DJAI.deckNumber(group) - 1);
    midi.sendShortMsg(status, 0x10 + pad, on ? 0x7F : 0x00);
    midi.sendShortMsg(status + 1, 0x10 + pad, on ? 0x7F : 0x00);
};

// Position of a deck in beats counted from its main cue (assumed to sit on
// the first downbeat, which is where Mixxx's analyser puts it by default).
DJAI.beatPosition = function(group) {
    const samples = engine.getValue(group, "playposition") * engine.getValue(group, "track_samples");
    const cue = Math.max(engine.getValue(group, "cue_point"), 0);
    const samplesPerBeat = engine.getValue(group, "track_samplerate") * 2 * 60 / engine.getValue(group, "file_bpm");
    return (samples - cue) / samplesPerBeat;
};

// 0 before start, 1 after end, linear in between.
DJAI.ramp = function(beat, start, end) {
    return Math.min(Math.max((beat - start) / (end - start), 0), 1);
};

DJAI.padPressed = function(_channel, control, value, _status, group) {
    if (!value) {
        return;
    }
    const pad = control - 0x10;
    if (pad === DJAI.abortPad) {
        DJAI.stop("aborted");
        return;
    }
    if (DJAI.mixBeatsForPad[pad] === undefined) {
        return;
    }
    const outgoing = group === "[Channel1]" ? "[Channel2]" : "[Channel1]";
    DJAI.startTransition(group, outgoing, DJAI.mixBeatsForPad[pad], pad);
};

// Start a transition from the playing outgoing deck into the stopped incoming
// deck. pad is the PAD FX1 pad to light, or null when triggered remotely.
// Returns an error message, or null on success.
DJAI.startTransition = function(incoming, outgoing, lengthBeats, pad) {
    if (DJAI.mix) {
        return "a transition is already running";
    }
    if (!engine.getValue(outgoing, "play") || !engine.getValue(incoming, "track_loaded")
            || engine.getValue(incoming, "play") || !engine.getValue(outgoing, "file_bpm")) {
        engine.log("DJAI: need " + outgoing + " playing and a stopped, loaded track on " + incoming);
        return "need " + outgoing + " playing and a stopped, loaded track on " + incoming;
    }

    const now = DJAI.beatPosition(outgoing);
    const startBeat = Math.ceil((now + DJAI.leadInBeats) / DJAI.phraseBeats) * DJAI.phraseBeats;

    DJAI.mix = {
        incoming: incoming,
        outgoing: outgoing,
        pad: pad,
        startBeat: startBeat,
        lengthBeats: lengthBeats,
        crossfaderFrom: engine.getValue("[Master]", "crossfader"),
        armedAt: now,
        started: false,
        swapped: false,
    };

    // Prepare the incoming deck: silent, bass killed, parked on its cue, beat matched.
    engine.setValue(incoming, "volume", 0);
    engine.setValue(DJAI.eqGroup(incoming), "parameter1", 0);
    engine.setValue(incoming, "cue_gotoandstop", 1);
    engine.setValue(incoming, "quantize", 1);
    engine.setValue(outgoing, "sync_enabled", 1);
    engine.setValue(incoming, "sync_enabled", 1);

    engine.log("DJAI: " + outgoing + " -> " + incoming + ", " + DJAI.mix.lengthBeats / 4
        + " bars, starting at beat " + startBeat + " (now " + now.toFixed(1) + ")");

    DJAI.mix.timer = engine.beginTimer(DJAI.tickMs, DJAI.tick);
    return null;
};

DJAI.tick = function() {
    const mix = DJAI.mix;
    if (!engine.getValue(mix.outgoing, "play")) {
        DJAI.stop("outgoing deck stopped");
        return;
    }
    const beat = DJAI.beatPosition(mix.outgoing);
    const t = beat - mix.startBeat; // beats into the mix (negative while waiting)
    const len = mix.lengthBeats;

    if (!mix.started) {
        // Glide the crossfader to the centre while waiting; the incoming deck is silent.
        const glide = DJAI.ramp(beat, mix.armedAt, mix.startBeat - 1);
        engine.setValue("[Master]", "crossfader", mix.crossfaderFrom * (1 - glide));
        const blinkOn = Math.floor(beat * 2) % 2 === 0;
        if (blinkOn !== mix.blinkOn) {
            DJAI.padLight(mix.incoming, mix.pad, blinkOn);
            mix.blinkOn = blinkOn;
        }

        // Quantize lines up the phase, so pressing play a fraction early is fine.
        if (t >= -0.25) {
            engine.setValue(mix.incoming, "play", 1);
            mix.started = true;
            DJAI.padLight(mix.incoming, mix.pad, true);
        }
        return;
    }

    engine.setValue(mix.incoming, "volume", DJAI.ramp(t, 0, len / 4));

    if (!mix.swapped && t >= len / 2) {
        engine.setValue(DJAI.eqGroup(mix.outgoing), "parameter1", 0);
        engine.setValue(DJAI.eqGroup(mix.incoming), "parameter1",
            engine.getDefaultValue(DJAI.eqGroup(mix.incoming), "parameter1"));
        mix.swapped = true;
    }

    engine.setValue(mix.outgoing, "volume", 1 - DJAI.ramp(t, len / 2, len * 3 / 4));

    if (t >= len) {
        engine.setValue(mix.outgoing, "play", 0);
        DJAI.stop("done");
    }
};

DJAI.stop = function(reason) {
    const mix = DJAI.mix;
    if (!mix) {
        return;
    }
    engine.stopTimer(mix.timer);
    DJAI.padLight(mix.incoming, mix.pad, false);

    if (reason === "done") {
        // Leave the stopped deck ready for the next track and hand tempo back to the human.
        engine.setValue(mix.outgoing, "volume", 1);
        engine.setValue(DJAI.eqGroup(mix.outgoing), "parameter1",
            engine.getDefaultValue(DJAI.eqGroup(mix.outgoing), "parameter1"));
        engine.setValue(mix.outgoing, "sync_enabled", 0);
        engine.setValue(mix.incoming, "sync_enabled", 0);
    }
    engine.log("DJAI: transition " + reason);
    DJAI.mix = null;
};

DJAI.init = function() {};

DJAI.shutdown = function() {
    DJAI.stop("controller shutdown");
};

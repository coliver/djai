// DJAI-bridge.js
// ****************************************************************************
// * Remote control of Mixxx for Claude, over a loopMIDI port named "DJAI".
// *
// * Messages are SysEx: F0 7D 'D' 'J' <dir> <ASCII text> F7, where <dir> is
// * 'C' for commands to Mixxx and 'R' for replies from Mixxx. loopMIDI echoes
// * everything to every client, so each side ignores its own direction.
// * loopMIDI limits SysEx to 256 bytes, so keep each command message short.
// * Several commands may share one message, separated by ';'.
// *
// *   set G K V                    engine.setValue
// *   param G K V                  engine.setParameter (0..1)
// *   get G K                      reply with the value
// *   state                        reply with a JSON snapshot of decks and mixer
// *   at REF BEAT CMD...           run CMD when deck REF reaches BEAT
// *   ramp REF B0 B1 G K V0 V1     move G,K from V0 to V1 between beats B0 and B1
// *                                of deck REF (V0 '-' = value when the ramp starts)
// *   clear                        drop all pending 'at' and 'ramp' entries
// *   mix IN OUT BEATS             phrase-aligned blend (DJAI-transition.js)
// *   abort                        stop a running blend
// *   ping                         reply "pong"
// *
// * Beats are counted from each deck's main cue, as in DJAI-transition.js.
// ****************************************************************************

var DJAIBridge = {};

DJAIBridge.header = [0xF0, 0x7D, 0x44, 0x4A]; // F0 7D 'D' 'J'
DJAIBridge.tickMs = 20;
DJAIBridge.scheduled = [];
DJAIBridge.ramps = [];
DJAIBridge.timer = 0;

// loopMIDI caps SysEx at 256 bytes, so replies go out in chunks. Each chunk
// starts with '+' (more follows) or '.' (last chunk).
DJAIBridge.chunkSize = 200;

DJAIBridge.reply = function(text) {
    for (let start = 0; start < text.length || start === 0; start += DJAIBridge.chunkSize) {
        const last = start + DJAIBridge.chunkSize >= text.length;
        const bytes = DJAIBridge.header.concat([0x52, last ? 0x2E : 0x2B]); // 'R', '.' or '+'
        const chunk = text.substr(start, DJAIBridge.chunkSize);
        for (let i = 0; i < chunk.length; ++i) {
            bytes.push(chunk.charCodeAt(i) & 0x7F);
        }
        bytes.push(0xF7);
        midi.sendSysexMsg(bytes, bytes.length);
    }
};

DJAIBridge.incomingData = function(data, length) {
    const bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : data;
    const header = DJAIBridge.header;
    for (let i = 0; i < header.length; ++i) {
        if (bytes[i] !== header[i]) {
            return;
        }
    }
    if (bytes[header.length] !== 0x43) { // 'C'; our own 'R' replies echo back too
        return;
    }
    let text = "";
    for (let i = header.length + 1; i < length && bytes[i] !== 0xF7; ++i) {
        text += String.fromCharCode(bytes[i]);
    }
    text.split(";").forEach(function(command) {
        if (command.trim()) {
            DJAIBridge.run(command.trim());
        }
    });
};

DJAIBridge.run = function(command) {
    const words = command.split(/\s+/);
    try {
        switch (words[0]) {
        case "set":
            engine.setValue(words[1], words[2], parseFloat(words[3]));
            break;
        case "param":
            engine.setParameter(words[1], words[2], parseFloat(words[3]));
            break;
        case "get":
            DJAIBridge.reply(JSON.stringify({get: words[1] + "," + words[2], value: engine.getValue(words[1], words[2])}));
            break;
        case "state":
            DJAIBridge.reply(JSON.stringify(DJAIBridge.state()));
            break;
        case "at":
            DJAIBridge.scheduled.push({ref: words[1], beat: parseFloat(words[2]), command: words.slice(3).join(" ")});
            DJAIBridge.ensureTimer();
            break;
        case "ramp":
            DJAIBridge.ramps.push({
                ref: words[1], b0: parseFloat(words[2]), b1: parseFloat(words[3]),
                group: words[4], key: words[5],
                v0: words[6] === "-" ? null : parseFloat(words[6]), v1: parseFloat(words[7]),
            });
            DJAIBridge.ensureTimer();
            break;
        case "clear":
            DJAIBridge.scheduled = [];
            DJAIBridge.ramps = [];
            break;
        case "mix": {
            const error = DJAI.startTransition(words[1], words[2], parseInt(words[3]), null);
            DJAIBridge.reply(JSON.stringify({mix: error === null ? "started" : error}));
            break;
        }
        case "abort":
            DJAI.stop("aborted");
            break;
        case "ping":
            DJAIBridge.reply("pong");
            break;
        default:
            DJAIBridge.reply(JSON.stringify({error: "unknown command: " + command}));
        }
    } catch (e) {
        DJAIBridge.reply(JSON.stringify({error: String(e), command: command}));
    }
};

DJAIBridge.ensureTimer = function() {
    if (!DJAIBridge.timer) {
        DJAIBridge.timer = engine.beginTimer(DJAIBridge.tickMs, DJAIBridge.tick);
    }
};

DJAIBridge.tick = function() {
    const due = DJAIBridge.scheduled.filter(function(entry) {
        return DJAI.beatPosition(entry.ref) >= entry.beat;
    });
    DJAIBridge.scheduled = DJAIBridge.scheduled.filter(function(entry) {
        return due.indexOf(entry) < 0;
    });
    due.forEach(function(entry) {
        DJAIBridge.run(entry.command);
    });

    DJAIBridge.ramps = DJAIBridge.ramps.filter(function(ramp) {
        const beat = DJAI.beatPosition(ramp.ref);
        if (beat < ramp.b0) {
            return true;
        }
        if (ramp.v0 === null) {
            ramp.v0 = engine.getValue(ramp.group, ramp.key);
        }
        const t = DJAI.ramp(beat, ramp.b0, ramp.b1);
        engine.setValue(ramp.group, ramp.key, ramp.v0 + (ramp.v1 - ramp.v0) * t);
        return t < 1;
    });

    if (!DJAIBridge.scheduled.length && !DJAIBridge.ramps.length) {
        engine.stopTimer(DJAIBridge.timer);
        DJAIBridge.timer = 0;
    }
};

DJAIBridge.state = function() {
    const decks = {};
    for (let n = 1; n <= 4; ++n) {
        const g = "[Channel" + n + "]";
        if (!engine.getValue(g, "track_loaded")) {
            continue;
        }
        const eq = "[EqualizerRack1_" + g + "_Effect1]";
        decks[n] = {
            play: engine.getValue(g, "play"),
            pos: engine.getValue(g, "playposition"),
            dur: engine.getValue(g, "duration"),
            beat: DJAI.beatPosition(g),
            bpm: engine.getValue(g, "bpm"),
            file_bpm: engine.getValue(g, "file_bpm"),
            rate: engine.getValue(g, "rate"),
            vol: engine.getValue(g, "volume"),
            gain: engine.getValue(g, "pregain"),
            eq: [1, 2, 3].map(function(i) { return engine.getValue(eq, "parameter" + i); }),
            filter: engine.getValue("[QuickEffectRack1_" + g + "]", "super1"),
            sync: engine.getValue(g, "sync_enabled"),
            pfl: engine.getValue(g, "pfl"),
            key: engine.getValue(g, "key"),
            vu: engine.getValue(g, "vu_meter"),
        };
    }
    return {
        decks: decks,
        xfader: engine.getValue("[Master]", "crossfader"),
        mixing: DJAI.mix ? DJAI.mix.outgoing + "->" + DJAI.mix.incoming : null,
        pending: DJAIBridge.scheduled.length + DJAIBridge.ramps.length,
    };
};

DJAIBridge.init = function() {
    DJAIBridge.reply("hello");
};

DJAIBridge.shutdown = function() {
    DJAI.stop("controller shutdown");
    if (DJAIBridge.timer) {
        engine.stopTimer(DJAIBridge.timer);
    }
};

// Test harness for the Mixxx controller scripts. They are plain scripts that
// expect Mixxx's globals (engine, midi, script), so each test installs mocks
// and evaluates the script fresh, which resets its module-level state.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { pathToFileURL } = require("node:url");

const CONTROLLERS = path.resolve(__dirname, "../../mixxx/controllers");

function makeEngine() {
    const values = new Map();
    const defaults = new Map();
    const engine = {
        values,
        defaults,
        calls: [],
        logs: [],
        timers: new Map(),
        nextTimer: 1,
        connections: [],
        getValue(group, key) {
            const v = values.get(`${group}|${key}`);
            return v === undefined ? 0 : v;
        },
        setValue(group, key, value) {
            values.set(`${group}|${key}`, value);
            engine.calls.push(["setValue", group, key, value]);
        },
        getParameter(group, key) {
            return engine.getValue(group, key);
        },
        setParameter(group, key, value) {
            values.set(`${group}|${key}`, value);
            engine.calls.push(["setParameter", group, key, value]);
        },
        getDefaultValue(group, key) {
            const v = defaults.get(`${group}|${key}`);
            return v === undefined ? 0.5 : v;
        },
        log(message) {
            engine.logs.push(message);
        },
        beginTimer(ms, callback) {
            const id = engine.nextTimer++;
            engine.timers.set(id, { ms, callback });
            return id;
        },
        stopTimer(id) {
            engine.timers.delete(id);
            engine.calls.push(["stopTimer", id]);
        },
        makeConnection(group, key, callback) {
            engine.connections.push({ group, key, callback });
        },
        softTakeover(...args) {
            engine.calls.push(["softTakeover", ...args]);
        },
        softTakeoverIgnoreNextValue(...args) {
            engine.calls.push(["softTakeoverIgnoreNextValue", ...args]);
        },
        scratching: false,
        isScratching() {
            return engine.scratching;
        },
        scratchTick(...args) {
            engine.calls.push(["scratchTick", ...args]);
        },
        scratchEnable(...args) {
            engine.calls.push(["scratchEnable", ...args]);
        },
        scratchDisable(...args) {
            engine.calls.push(["scratchDisable", ...args]);
        },
        brake(...args) {
            engine.calls.push(["brake", ...args]);
        },
        set(group, key, value) {
            values.set(`${group}|${key}`, value);
        },
        // Calls to setValue/setParameter for one control, in order.
        writes(group, key) {
            return engine.calls.filter((c) => c[0] === "setValue" && c[1] === group && c[2] === key).map((c) => c[3]);
        },
        // Run the callback of the only live timer.
        tick(id) {
            engine.timers.get(id).callback();
        },
    };
    return engine;
}

function makeMidi() {
    const midi = {
        short: [],
        sysex: [],
        sendShortMsg(...args) {
            midi.short.push(args);
        },
        sendSysexMsg(bytes, length) {
            midi.sysex.push({ bytes: Array.from(bytes), length });
        },
    };
    return midi;
}

function makeScript() {
    const script = {
        calls: [],
        channelRegEx: /\[Channel(\d+)\]/,
        samplerRegEx: /\[Sampler(\d+)\]/,
        triggerControl(...args) {
            script.calls.push(["triggerControl", ...args]);
        },
        toggleControl(...args) {
            script.calls.push(["toggleControl", ...args]);
        },
    };
    return script;
}

// Install fresh mocks and evaluate the named scripts, in order. Returns the mocks.
function load(...files) {
    const mocks = { engine: makeEngine(), midi: makeMidi(), script: makeScript() };
    Object.assign(globalThis, mocks);
    for (const name of ["DJAI", "DJAIBridge", "PioneerDDJFLX4"]) {
        delete globalThis[name];
    }
    for (const name of files) {
        const file = path.join(CONTROLLERS, name);
        // A file URL as the script name lets the coverage report attribute it.
        vm.runInThisContext(fs.readFileSync(file, "utf8"), { filename: pathToFileURL(file).href });
    }
    return mocks;
}

// Make deck `group` sit `beat` beats after its cue (the bridge's beat count).
function setBeat(engine, group, beat, { bpm = 120, rate = 44100 } = {}) {
    engine.set(group, "track_samples", 10000000);
    engine.set(group, "cue_point", 0);
    engine.set(group, "track_samplerate", rate);
    engine.set(group, "file_bpm", bpm);
    const samplesPerBeat = rate * 2 * 60 / bpm;
    engine.set(group, "playposition", beat * samplesPerBeat / 10000000);
}

module.exports = { load, setBeat };

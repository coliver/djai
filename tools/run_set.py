"""Play and record a planned set in Mixxx through the DJAI Bridge.

    run_set.py sets/plan.json [log file] [--resume N] [--no-record]

Expects Mixxx running with the plan's first two tracks on decks 1 and 2, the
rest queued in Auto DJ (set_autodj_queue.py), and the DJAI Bridge enabled.

Rules this follows (from the user, 2026-10-25):
  * Never touch the tempo of a record that is playing. The incoming deck is
    matched to the outgoing one before it is heard (sync, with the outgoing
    deck as leader), and the set stays at the tempo it opened on.
  * Trance wants long mixes, and nothing may jump or cut. Every blend is made
    of ramps: highs and mids come in first, the basslines trade places over two
    bars, then the outgoing track leaves from the mids up.

Every transition is sent to Mixxx as beat-scheduled commands relative to the
outgoing deck, so this script's own timing never touches the music. It only
prepares decks, sends each transition well ahead of time, and loads the next
track once a transition has finished.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

DJAI = str(Path(__file__).resolve().parent / "djai")
POLL_SECONDS = 4
LOAD_TIMEOUT = 30
STALL_SECONDS = 60
BASS_SWAP_BEATS = 8
REPO = Path(__file__).resolve().parent.parent
GRID_PRESETS = REPO / "sets" / "husten_grids.json"
GRID_BPM_STEP = 0.0103          # BPM per beats_adjust press
GRID_BPM_TOLERANCE = 0.006
GRID_SHIFT_FRAMES = 220         # frames per beats_translate press
GRID_SHIFT_TOLERANCE_MS = 3
GRID_WARN_MS = 8

log_file = None
record = True


def set_recording(on):
    """Start or stop recording only if it is not already in that state."""
    if not record:
        return
    status = get("[Recording]", "status")
    if (status == 2) != on:
        send("set [Recording] toggle_recording 1")
        time.sleep(2)


def log(message):
    line = f"{time.strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    if log_file:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def bridge(kind, text, retries=3):
    for attempt in range(retries):
        result = subprocess.run([DJAI, kind, text], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
        log(f"bridge {kind} failed ({result.stderr.strip()}), attempt {attempt + 1}")
        time.sleep(1)
    raise RuntimeError(f"bridge {kind} {text!r} failed")


def send(*commands):
    bridge("send", "; ".join(commands))


def query_json(text, check, attempts=6):
    """Query the bridge and parse the reply. Replies share one loopMIDI port, so a
    stray reply (or none) is retried rather than allowed to end the set."""
    raw = ""
    for _ in range(attempts):
        try:
            raw = bridge("query", text, retries=1)
            reply = json.loads(raw)
            if check(reply):
                return reply
            log(f"  unexpected reply to {text!r}: {raw[:120]}")
        except (json.JSONDecodeError, RuntimeError) as e:
            log(f"  bad reply to {text!r} ({type(e).__name__}): {raw[:120]!r}")
        time.sleep(1)
    raise RuntimeError(f"bridge query {text!r} kept failing")


def get(group, key):
    wanted = f"{group},{key}".replace("[", "").replace("]", "")
    return query_json(f"get {group} {key}",
                      lambda r: isinstance(r, dict) and r.get("get", "").replace("[", "").replace("]", "") == wanted)["value"]


def state():
    return query_json("state", lambda r: isinstance(r, dict) and "decks" in r)


def deck(n):
    return f"[Channel{n}]"


def eq(n):
    return f"[EqualizerRack1_[Channel{n}]_Effect1]"


def other(n):
    return 2 if n == 1 else 1


def wait_until(predicate, description, timeout=None, interval=POLL_SECONDS):
    start = time.monotonic()
    silent_since = None
    while True:
        s = state()
        if predicate(s):
            return s
        # Nothing playing while the set is meant to be running means someone or
        # something stopped it. Say so loudly; a monitor on the log picks it up.
        if not any(d["play"] for d in s["decks"].values()):
            silent_since = silent_since or time.monotonic()
            if time.monotonic() - silent_since > STALL_SECONDS:
                log(f"STALL: no deck has played for {STALL_SECONDS} s while waiting for {description}")
                silent_since = time.monotonic()
        else:
            silent_since = None
        if timeout and time.monotonic() - start > timeout:
            raise RuntimeError(f"timed out waiting for {description}")
        time.sleep(interval)


def loaded_track(s, n, tracks):
    """Index of the plan track loaded on deck n, matched by duration, or None."""
    d = s["decks"].get(str(n))
    if not d:
        return None
    for i, t in enumerate(tracks):
        if abs(d["dur"] - t["duration"]) < 0.5:
            return i
    return None


def cue_sample(t, bar):
    frames_per_beat = t["samplerate"] * 60 / t["bpm"]
    return (t["first_beat_frame"] + (bar - 1) * 4 * frames_per_beat) * 2


def beats_to(n, frame):
    """The bridge's beat count for deck n when its playhead is at `frame` (audio frames): beats from the
    deck's real main cue, at the deck's real file_bpm. This is the same sum the bridge does, so a beat
    number made here fires at that spot in the audio whatever the grid is doing."""
    cue = max(get(deck(n), "cue_point"), 0)
    samples_per_beat = get(deck(n), "track_samplerate") * 2 * 60 / get(deck(n), "file_bpm")
    return (frame * 2 - cue) / samples_per_beat


def mix_beat(n, t, key, bar_key):
    """Beat (on deck n's counter) of a plan point given either as audio frames or as a bar of the plan grid."""
    if key in t:
        return beats_to(n, t[key])
    return (t[bar_key] - t["start_bar"]) * 4


def press(n, control, times):
    """Press a Mixxx button control on deck n, times over."""
    while times > 0:
        count = min(5, times)
        send(*[f"set {deck(n)} {control} {value}" for _ in range(count) for value in (1, 0)])
        times -= count
        time.sleep(0.2)


def grid_errors(n, preset):
    """Frames the live grid sits after the preset's grid, at the first and last bar it was fitted for."""
    samples = get(deck(n), "track_samples")
    fitted = {"samplerate": 44100, "bpm": preset["bpm"], "first_beat_frame": preset["first_beat_frame"]}
    frames_per_beat = 44100 * 60 / preset["bpm"]
    errors = []
    for bar in preset["bars"]:
        expected = cue_sample(fitted, bar)
        send(f"set {deck(n)} playposition {expected / samples:.9f}")
        time.sleep(0.4)
        error = (get(deck(n), "beat_closest") - expected) / 2
        errors.append((error + frames_per_beat / 2) % frames_per_beat - frames_per_beat / 2)
    return errors


def grid_preset(name):
    return json.load(open(GRID_PRESETS, encoding="utf-8"))["presets"][name]


def walk_bpm(n, preset):
    """Press beats_adjust on deck n until its file BPM is within tolerance of the preset's."""
    for _ in range(40):
        difference = preset["bpm"] - get(deck(n), "file_bpm")
        if abs(difference) < GRID_BPM_TOLERANCE:
            break
        press(n, "beats_adjust_faster" if difference > 0 else "beats_adjust_slower",
              min(20, max(1, round(abs(difference) / GRID_BPM_STEP))))


def shift_grid(n, error):
    """Slide deck n's grid by `error` frames. The live beats are early when it is negative, so move the grid later."""
    press(n, "beats_translate_later" if error < 0 else "beats_translate_earlier",
          max(1, round(abs(error) / GRID_SHIFT_FRAMES)))


def warn_if_off(n, name, off_ms):
    if off_ms > GRID_WARN_MS:
        log(f"  warning: deck {n} grid '{name}' is more than {GRID_WARN_MS} ms off the preset")


def apply_grid(n, name):
    """Give the STOPPED deck n the beat grid saved under `name` in sets/husten_grids.json.

    Husten's tempo creeps, so each transition that uses it has its own grid. The grid is only metadata, so
    the deck stays silent, but it is never changed on a playing deck: the bridge counts beats from the
    deck's BPM, and a synced deck would follow the change. The BPM is walked to the preset with
    beats_adjust presses, then the grid is shifted with beats_translate presses until it sits on the
    preset's beats at both ends of the range it was fitted for. Nothing is pressed if it already does.
    """
    preset = grid_preset(name)
    send(f"set {deck(n)} play 0", f"set {deck(n)} sync_enabled 0", f"set {deck(n)} quantize 0")
    if get(deck(n), "play"):
        log(f"  warning: deck {n} is playing, grid '{name}' not applied")
        return
    walk_bpm(n, preset)
    for _ in range(5):
        errors = grid_errors(n, preset)
        mean = sum(errors) / len(errors)
        if abs(mean) / 44.1 <= GRID_SHIFT_TOLERANCE_MS:
            break
        shift_grid(n, mean)
    errors = grid_errors(n, preset)
    ms = [e / 44.1 for e in errors]
    log(f"  deck {n} grid '{name}': {get(deck(n), 'file_bpm'):.4f} BPM, "
        f"{ms[0]:+.1f} ms and {ms[-1]:+.1f} ms from the preset at bars {preset['bars'][0]} and {preset['bars'][-1]}")
    warn_if_off(n, name, max(abs(m) for m in ms))


def prepare(n, t):
    """Park deck n on the track's start bar, make that the cue, and set it up silent."""
    if t.get("grid_preset"):
        apply_grid(n, t["grid_preset"])
    samples = get(deck(n), "track_samples")
    cue_at = t["in_frame"] * 2 if "in_frame" in t else cue_sample(t, t["start_bar"])
    send(f"set {deck(n)} play 0",
         f"set {deck(n)} sync_enabled 0",
         f"set {deck(n)} keylock 0",
         f"set {deck(n)} quantize 1",
         f"set {deck(n)} rate 0",
         f"set {deck(n)} playposition {cue_at / samples:.9f}")
    time.sleep(0.5)
    send(f"set {deck(n)} cue_set 1")
    time.sleep(0.5)
    cue = get(deck(n), "cue_point")
    expected = cue_at
    if abs(cue - expected) > 2000:
        log(f"  warning: deck {n} cue at {cue:.0f}, expected {expected:.0f}")
    # Silent, bass and mids cut: the blend brings them in.
    send(f"set {deck(n)} volume 0",
         f"set {eq(n)} parameter1 0",
         f"set {eq(n)} parameter2 0",
         f"set {eq(n)} parameter3 1")
    log(f"  deck {n} ready: {t['title']} from {'bar ' + str(t['start_bar']) if 'start_bar' in t else 'frame ' + str(round(t['in_frame']))}")


def schedule_transition(out_n, out_t, in_n, in_t=None):
    """Send the whole blend as beat-scheduled commands on the outgoing deck.

    With T the blend length in beats, from the moment the incoming deck starts:
      0 to T/4     incoming volume rises (highs first), mids join from T/8
      about 2/3 T  basslines trade places over BASS_SWAP_BEATS, on an 8-bar phrase
    The outgoing record is never faded, filtered or stopped while it is still
    playing its lead: after the bass swap it simply plays out over the incoming
    groove until its own end (the user: "you can't cut out of the lead"). Only if
    its outro would run into the next track's loading is it faded and stopped.
    """
    o, i = deck(out_n), deck(in_n)
    if out_t.get("replay"):
        schedule_replay(out_n, out_t, in_n, in_t)
        return
    if out_t.get("grid_preset_live"):
        # This track has two grids (its tempo creeps): move the playing deck to the one the blend was made on.
        regrid_live(out_n, out_t["grid_preset_live"])
    m = mix_beat(out_n, out_t, "mix_frame", "mix_bar")
    T = out_t["overlap"] * 4
    s = BASS_SWAP_BEATS
    swap = max(1, round(0.65 * out_t["overlap"] / 8)) * 32   # beats from the blend start, a multiple of 8 bars
    swap = min(swap, T - s - 8)
    # The outgoing deck leads, so matching tempo never moves the record that is playing.
    send(f"set {o} sync_leader 1", f"set {i} sync_enabled 1")
    if out_t.get("style") == "late":
        # The outgoing record is untouched, bass included, until it is mixed out. The incoming
        # record layers over it without its bass, then at the end of the blend the bass cuts
        # fast from one to the other, and the outgoing lead plays out to its end.
        cut = 2   # beats
        # An incoming lead that would fight the outgoing one (hold_mids / hold_highs on the incoming
        # track) is kept low until the bass swap, then opened with the bass.
        hold_mids = (in_t or {}).get("hold_mids", 1)
        hold_highs = (in_t or {}).get("hold_highs", 1)
        send(f"set {eq(in_n)} parameter3 {hold_highs}",
             f"at {o} {m - 0.25} set {i} play 1",
             f"ramp {o} {m} {m + T / 4} {i} volume 0 1",
             f"ramp {o} {m + T / 8} {m + T / 4} {eq(in_n)} parameter2 0 {hold_mids}",
             f"ramp {o} {m + T - cut / 2} {m + T + cut / 2} {eq(in_n)} parameter1 0 1",
             f"ramp {o} {m + T - cut / 2} {m + T + cut / 2} {eq(out_n)} parameter1 1 0")
        if hold_mids < 1:
            send(f"ramp {o} {m + T - cut / 2} {m + T + cut / 2} {eq(in_n)} parameter2 {hold_mids} 1")
        if hold_highs < 1:
            send(f"ramp {o} {m + T - cut / 2} {m + T + cut / 2} {eq(in_n)} parameter3 {hold_highs} 1")
        log(f"  scheduled (late swap): deck {in_n} starts at outgoing beat {m:.1f}, "
            f"outgoing keeps its bass until beat {m + T:.0f}, then a fast cut to the incoming bass")
        if "cut_bar" in out_t or "cut_frame" in out_t:
            # Mixing out early: the outgoing record is treated as ending at cut_bar and stops there.
            cut_beat = mix_beat(out_n, out_t, "cut_frame", "cut_bar")
            send(f"at {o} {cut_beat:.3f} set {o} play 0")
            log(f"  outgoing deck stops at beat {cut_beat:.1f}")
            if out_t.get("eq_out"):
                # Approaching that end, the outgoing mids come down first, then the highs.
                first = m + T + cut / 2 + 1
                span = cut_beat - first
                send(f"ramp {o} {first} {first + span * 0.5} {eq(out_n)} parameter2 1 0",
                     f"ramp {o} {first + span * 0.45} {cut_beat} {eq(out_n)} parameter3 1 0")
                log(f"  outgoing mids then highs come down between beat {first:.0f} and the stop")
        return
    if out_t.get("style") == "drop":
        # The outgoing record is untouched, bass included, until the incoming one starts. Then
        # the incoming comes in whole and the basslines trade places over BASS_SWAP_BEATS, while
        # the outgoing record's lead plays out to its end.
        send(f"set {eq(in_n)} parameter2 1",
             f"at {o} {m - 0.25} set {i} play 1",
             f"ramp {o} {m} {m + s} {i} volume 0 1",
             f"ramp {o} {m} {m + s} {eq(in_n)} parameter1 0 1",
             f"ramp {o} {m} {m + s} {eq(out_n)} parameter1 1 0")
        log(f"  scheduled (drop): deck {in_n} starts at outgoing beat {m:.0f} (bar {out_t['mix_bar']}), "
            f"basslines trade places right there, outgoing plays out")
        return
    commands = [f"at {o} {m - 0.25} set {i} play 1",
                f"ramp {o} {m} {m + T / 4} {i} volume 0 1",
                f"ramp {o} {m + T / 8} {m + T / 4} {eq(in_n)} parameter2 0 1",
                f"ramp {o} {m + swap} {m + swap + s} {eq(in_n)} parameter1 0 1",
                f"ramp {o} {m + swap} {m + swap + s} {eq(out_n)} parameter1 1 0"]
    note = "outgoing plays out"
    if in_t is not None and "mix_bar" in in_t:
        # Time the outgoing deck may keep playing: until the incoming track's own blend
        # out is close, because the next track has to load onto this deck first.
        room = (in_t["mix_bar"] - in_t["start_bar"] - out_t["overlap"] - 8) * 4
        natural_end = (out_t["bars"] - out_t["start_bar"]) * 4
        stop_at = m + T + max(16, room)
        if stop_at < natural_end - 8:
            commands += [f"ramp {o} {stop_at - 8} {stop_at} {o} volume 1 0", f"at {o} {stop_at} set {o} play 0"]
            note = f"outgoing fades out at beat {stop_at:.0f}"
    send(*commands)
    log(f"  scheduled: deck {in_n} starts at outgoing beat {m:.0f} (bar {out_t['mix_bar']}), "
        f"{out_t['overlap']} bar blend, bass swap at bar {swap // 4}, {note}")


def live_grid_error(n, preset):
    """Frames the live grid of PLAYING deck n sits after the preset's grid, read at the playhead."""
    samplerate = get(deck(n), "track_samplerate")
    frames_per_beat = samplerate * 60 / preset["bpm"]
    closest = get(deck(n), "beat_closest") / 2
    error = (closest - preset["first_beat_frame"]) % frames_per_beat
    return error - frames_per_beat if error > frames_per_beat / 2 else error


def regrid_live(n, name):
    """Move PLAYING deck n onto the beat grid saved as `name`, without touching the audio.

    A grid is only metadata: the playing speed is the deck's rate, which a grid change leaves alone, and
    nothing is synced to this deck while it plays by itself. The bridge's beat counts change with the
    grid, so call this BEFORE scheduling anything on the deck. The tempo is walked to the preset with
    beats_adjust presses, then the grid is slid with beats_translate presses until the nearest live beat
    sits on the preset's beat at the playhead.
    """
    preset = grid_preset(name)
    send(f"set {deck(n)} sync_enabled 0")
    before = get(deck(n), "file_bpm")
    walk_bpm(n, preset)
    for _ in range(6):
        error = live_grid_error(n, preset)
        if abs(error) / 44.1 <= GRID_SHIFT_TOLERANCE_MS:
            break
        shift_grid(n, error)
    error = live_grid_error(n, preset)
    log(f"  deck {n} regridded while playing to '{name}': {before:.4f} -> {get(deck(n), 'file_bpm'):.4f} BPM, "
        f"{error / 44.1:+.1f} ms from the preset")
    warn_if_off(n, name, abs(error) / 44.1)


def schedule_replay(out_n, out_t, in_n, in_t):
    """Replay the user's own hand mix (sets/live/*.jsonl, made by record_mix.py) as the blend out of deck out_n.

    The log counts beats on the outgoing deck from its cue, on the grid the track had then (`grid_preset_live`
    names it, `replay_bar` is the bar the cue sat on). Each control move is turned into the audio frame it
    happened at and from there into this deck's beat count, so it lands on the same bit of music. Before that
    the outgoing deck is moved to the grid it had in the log, because that grid is what the incoming deck's
    sync will lock to.
    """
    o, i = deck(out_n), deck(in_n)
    preset = grid_preset(out_t["grid_preset_live"])
    regrid_live(out_n, out_t["grid_preset_live"])
    rows = [json.loads(line) for line in open(REPO / out_t["replay"], encoding="utf-8")]
    samples = [r for r in rows if "state" in r]
    frames_per_beat = out_t["samplerate"] * 60 / preset["bpm"]
    log_cue = preset["first_beat_frame"] + (out_t["replay_bar"] - 1) * 4 * frames_per_beat

    # Read the live deck once: the bridge's beat count is (samples - cue) / samples per beat.
    live_cue = max(get(deck(out_n), "cue_point"), 0)
    live_spb = get(deck(out_n), "track_samplerate") * 2 * 60 / get(deck(out_n), "file_bpm")

    def live(log_beat):
        return ((log_cue + log_beat * frames_per_beat) * 2 - live_cue) / live_spb

    def d(r, n):
        return r["state"]["decks"].get(str(n))

    next(r for r in samples if d(r, 1)["play"] == 1)
    second = next(r for r in samples if d(r, 2)["play"] == 1)
    ready = {n: d(samples[samples.index(second) - 1], n) for n in (1, 2)}
    names = {1: out_n, 2: in_n}
    # The incoming deck waits with its bass out and its mids where the hand mix had them.
    send(f"set {eq(in_n)} parameter2 {ready[2]['eq'][1]:.3f}", f"set {i} volume 0")
    send(f"set {o} sync_leader 1", f"set {i} sync_enabled 1")
    start = live(round(d(second, 1)["beat"]))
    lines = [f"at {o} {start - 0.25:.3f} set {i} play 1"]
    last = {n: {"vol": ready[n]["vol"], "eq": list(ready[n]["eq"])} for n in (1, 2)}
    for r in samples:
        if r["t"] < second["t"] - 1e-6:
            continue
        beat = live(d(r, 1)["beat"])
        for n in (1, 2):
            x, prev, target = d(r, n), last[n], f"[Channel{names[n]}]"
            if abs(x["vol"] - prev["vol"]) >= 0.01:
                lines.append(f"at {o} {beat:.3f} set {target} volume {x['vol']:.3f}")
                prev["vol"] = x["vol"]
            for k, value in enumerate(x["eq"]):
                if abs(value - prev["eq"][k]) >= 0.01:
                    lines.append(f"at {o} {beat:.3f} set {eq(names[n])} parameter{k + 1} {min(value, 1):.3f}")
                    prev["eq"][k] = value
        if d(r, 1)["play"] == 0:
            break
    path = REPO / "sets" / "logs" / "replay-schedule.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bridge("plan", str(path))
    log(f"  scheduled (hand mix replay): deck {in_n} starts at outgoing beat {start:.1f}, {len(lines)} control moves, "
        f"last at beat {live(d(r, 1)['beat']):.1f}")


def finish_transition(out_n, in_n):
    """Put the stopped deck back to neutral. The playing deck keeps whatever tempo it has."""
    send(f"set {deck(out_n)} volume 1",
         f"set {eq(out_n)} parameter1 1",
         f"set {eq(out_n)} parameter2 1",
         f"set {deck(out_n)} sync_enabled 0",
         f"set {deck(in_n)} sync_enabled 0")


def load_next(n, tracks, expected_index):
    """Use Auto DJ to load the next queued track onto stopped deck n.

    Auto DJ only works with one deck on each side of the crossfader, so the decks
    leave centre for a moment. The crossfader goes to the playing deck's side
    first, so nothing audible changes while Auto DJ does its job.
    """
    playing = other(n)
    sides = {1: 0, 2: 2}
    send(f"set [Master] crossfader {-1.0 if playing == 1 else 1.0}")
    send(f"set {deck(1)} orientation {sides[1]}", f"set {deck(2)} orientation {sides[2]}")
    send("set [AutoDJ] enabled 1")
    try:
        s = wait_until(lambda s: loaded_track(s, n, tracks) == expected_index,
                       f"track {expected_index + 1} on deck {n}", timeout=LOAD_TIMEOUT, interval=1)
    finally:
        send("set [AutoDJ] enabled 0")
        send(f"set {deck(1)} orientation 1", f"set {deck(2)} orientation 1")
    log(f"  loaded track {expected_index + 1} on deck {n}")
    return s


def main(plan_path, resume=None):
    """Run the set. With resume=N, tracks[N] is already playing on a deck and its
    transition is not scheduled yet (send "clear" first), so pick up from there."""
    tracks = json.load(open(plan_path, encoding="utf-8"))
    s = state()
    if resume is None:
        if loaded_track(s, 1, tracks) != 0 or loaded_track(s, 2, tracks) != 1:
            raise SystemExit(f"Expected tracks 1 and 2 on decks 1 and 2, got {s}")

        # The physical crossfader drives Mixxx's, so take both decks off it. Volume
        # and EQ ramps do the blending, and nobody's crossfader position can cut a deck.
        send("set [Master] crossfader 0", f"set {deck(1)} orientation 1", f"set {deck(2)} orientation 1")
        prepare(1, tracks[0])
        prepare(2, tracks[1])
        send(f"set {eq(1)} parameter1 1", f"set {eq(1)} parameter2 1")
        # The set's tempo is chosen once, here, before the first record is heard.
        send(f"set {deck(1)} bpm {tracks[0]['set_bpm']}")

        set_recording(True)
        if record and get("[Recording]", "status") != 2:
            log("  warning: recording did not report status 2")
        log("recording started" if record else "not recording")
        # The opener comes in at full volume from its first beat, no fade-in (the user, 2026-10-26).
        send(f"set {deck(1)} volume 1", f"set {deck(1)} play 1")
        log(f"1. {tracks[0]['title']}")
        playing, first = 1, 0
    else:
        playing = next(n for n in (1, 2) if loaded_track(s, n, tracks) == resume and s["decks"][str(n)]["play"] == 1)
        first = resume
        log(f"resuming at track {resume + 1} on deck {playing}")
        idle = other(playing)
        if resume + 1 < len(tracks) and not s["decks"][str(idle)]["play"]:
            # The next track may not be on the idle deck yet, or not parked: do what the loop would have done.
            if loaded_track(s, idle, tracks) != resume + 1:
                load_next(idle, tracks, resume + 1)
            prepare(idle, tracks[resume + 1])

    for index in range(first, len(tracks) - 1):
        out_n, in_n = playing, other(playing)
        out_t, in_t = tracks[index], tracks[index + 1]
        schedule_transition(out_n, out_t, in_n, in_t)

        wait_until(lambda s: s["decks"][str(in_n)]["play"] == 1, f"deck {in_n} to start")  # noqa: B023
        log(f"{index + 2}. {in_t['title']}")
        s = wait_until(lambda s: s["decks"][str(out_n)]["play"] == 0, f"deck {out_n} to stop")  # noqa: B023
        if s["decks"][str(in_n)]["play"] != 1:
            # Should not happen; keep the music going if the scheduled start was missed.
            log("  incoming deck not playing at end of blend, starting it now")
            send(f"set {deck(in_n)} play 1", f"set {deck(in_n)} volume 1")
        finish_transition(out_n, in_n)
        playing = in_n

        if index + 2 < len(tracks):
            load_next(out_n, tracks, index + 2)
            prepare(out_n, tracks[index + 2])

    last = tracks[-1]
    if "stop_bar" in last:
        # An audition: fade the last track out at stop_bar instead of playing it to the end.
        end = (last["stop_bar"] - last["start_bar"]) * 4
        send(f"ramp {deck(playing)} {end - 16} {end} {deck(playing)} volume 1 0",
             f"at {deck(playing)} {end} set {deck(playing)} play 0")
        log(f"last track playing; fading out at bar {last['stop_bar']}")
    else:
        log("last track playing; waiting for it to end")
    wait_until(lambda s: s["decks"][str(playing)]["play"] == 0, "the last track to end", interval=10)
    time.sleep(2)
    set_recording(False)
    send(f"set {deck(1)} orientation 0", f"set {deck(2)} orientation 2")  # hand the crossfader back
    log("recording stopped. Set complete." if record else "Set complete.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--no-record" in args:
        record = False
        args.remove("--no-record")
    resume = int(args[args.index("--resume") + 1]) if "--resume" in args else None
    if "--resume" in args:
        del args[args.index("--resume"):args.index("--resume") + 2]
    if len(args) > 1:
        log_file = args[1]
    try:
        main(args[0], resume)
    except Exception as e:
        log(f"FAILED: {e!r}")
        raise

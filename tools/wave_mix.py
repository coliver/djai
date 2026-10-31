"""Mix from the waveform instead of the beatgrid.

    wave_mix.py render <audio>                                     whole track, kept in sets/waveforms/
    wave_mix.py render <audio> <out.png> [start] [duration] [tick]  a zoom
    wave_mix.py onsets <audio> <start> <duration> [rise]           kick onsets in a window, to the millisecond
    wave_mix.py run sets/plan.wave.json [log file] [--no-record] [--resume K seconds-since-recording-started]

render draws a track's waveform: the full signal on top, the bass below 120 Hz underneath, with labelled time
ticks. Read sections, breakdowns and drops off the whole track (saved as sets/waveforms/<artist - title>.png, so each
track is drawn once), then zoom in with start and duration: a 0.3 s window shows a kick onset to about a
millisecond. <audio> is a path, or a location relative to music_dir. onsets reads the same bass line as numbers: the
time each kick rises out of the low band, so anchors are not limited by how well a pixel can be read.

run plays a set whose points were read off those pictures. Every time is in seconds of that track's own file,
so no beatgrid is used anywhere: the bridge's beat counter is only a clock here. Each track in the plan has

    title, location, duration
    beat     seconds per beat, from two kick onsets far apart (an onset time difference over the beat count)

The opener has `in`, where it starts at full volume. Every later track has

    drop     the downbeat on this track that lands on the previous track's `swap`, usually where its bass enters

and every track except the last has

    mix      the phrase start on this track where the next track starts, silent, as many beats before its drop
             as there are between this track's mix and swap
    swap     where the basslines trade: over the 2 beats before it, this track's bass goes out, the next one's in.
             Usually where this track's bass ends. The swap and the next drop are the pair the phase is held to.
    end      where this track stops. It fades over end_beats (default 4) before; put it on the last beat.
    eq_out   optional: after the swap, roll this track's mids off, then its highs, before the end
    hold_mids, hold_highs   optional, on the incoming track: keep its lead down until the swap
    jump     optional [from, to]: once this track plays alone, skip from one phrase start to a later one. For a
             transitions-only mix, it cuts the stretch between this track's way in and its way out.

The last track's `end` (optional) fades the set out there. The plan's first two tracks must be on decks 1 and 2
and the rest queued in Auto DJ (set_autodj_queue.py), as for run_set.py.

The opener plays at its own tempo and that is the set's tempo. Each incoming deck is rate-matched before it is
heard, and nothing ever changes the speed of the outgoing deck. During every blend the incoming deck's position
is checked against where the outgoing deck says it should be, and a phase error is pulled back with a short
rate nudge on the incoming deck only, the way a DJ rides the jog wheel. Where each blend sits in the recording
(mix, swap and end of the outgoing track, in seconds) is written next to the plan as <plan>.marks.json, for cutting
the blends out afterwards. While a blend plays, a number of milliseconds written to sets/logs/wave_offset_ms moves
the incoming deck that far ahead (negative: behind) of the computed phase and holds it there, for corrections by ear.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import config
import run_set as rs

WAVEFORMS = rs.REPO / "sets" / "waveforms"
WIDTH = 2400
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SWAP_BEATS = 2
END_BEATS = 4
PHASE_TOLERANCE = 0.003      # seconds of phase error left alone
CONFIRM_TOLERANCE = 0.004    # two readings in a row must agree this well before a nudge
CHECK_SECONDS = 3            # between phase checks during a blend
LEAD_SECONDS = 0.8           # how far ahead a nudge is scheduled, so the bridge gets it in time
REFRACTORY_MS = 250          # one kick per this long at most: a beat is about 430 ms
NUDGE_BIG, NUDGE_SMALL = 0.02, 0.01   # temporary speed change; the big one above 12 ms of error

clocks = {}                  # deck -> (cue in seconds, file_bpm): the bridge counts beats from these
matched = {}                 # deck -> its matched speed, the one a nudge returns to
OFFSET_FILE = rs.REPO / "sets" / "logs" / "wave_offset_ms"   # a by-ear correction for the blend under way
marks = []                   # per blend, where it sits in the recording (seconds from the recording's start)
started = [0.0]              # wall clock when the recording started


def label(t):
    return f"{round(t, 3):g}"


def track_name(path):
    """The file name without its folder, extension or leading track number: "02. A - B.flac" is "A - B"."""
    return re.sub(r"^\d+\.\s*", "", Path(path).stem)


def render(audio, out=None, start=0.0, duration=None, tick=None):
    """Draw the waveform of `audio` from `start` for `duration` seconds into the PNG `out`
    (default: the whole track, into WAVEFORMS under the file's own name)."""
    path = audio if Path(audio).exists() else config.music_path(audio)
    if out is None:
        WAVEFORMS.mkdir(parents=True, exist_ok=True)
        out = str(WAVEFORMS / (track_name(path) + ".png"))
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True, check=True)
    # Past the end of the file the picture would stretch what is left across the full width, so stop there.
    duration = min(duration or float("inf"), float(probe.stdout) - start)
    if tick is None:
        tick = next(t for t in (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60) if duration / t <= 30)
    spacing = WIDTH * tick / duration
    # Trimmed after decoding from the top, which is sample-exact; seeking into a VBR mp3 is only approximate.
    graph = (f"[0:a]atrim=start={start}:duration={duration},asetpts=PTS-STARTPTS,aformat=channel_layouts=mono,asplit[a][b];"
             f"[a]showwavespic=s={WIDTH}x260:colors=#33ccff:scale=lin[t];"
             f"[b]lowpass=f=120,lowpass=f=120,showwavespic=s={WIDTH}x160:colors=#ff9933:scale=lin[l];"
             f"[t][l]vstack,drawgrid=w={spacing}:h=420:t=1:c=white@0.35")
    for i in range(int(duration / tick + 1e-9) + 1):
        graph += f",drawtext=fontfile={FONT}:text='{label(start + i * tick)}':x={int(i * spacing) + 3}:y=2:fontsize=16:fontcolor=white"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-filter_complex", graph, "-frames:v", "1", out], check=True)
    print(f"{out}: {label(start)} to {label(start + duration)} s, ticks every {label(tick)} s")


def onsets(audio, start, duration, rise=0.7):
    """Kick onsets (seconds in the file) between start and start + duration: where the low band (below 150 Hz) climbs
    to `rise` of the window's loudest kick. Each onset is the first millisecond at half that kick's own peak, and nothing
    within REFRACTORY_MS after it counts (a bass note rings for several cycles)."""
    path = audio if Path(audio).exists() else config.music_path(audio)
    rate = 8000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-af",
                          f"atrim=start={start}:duration={duration},asetpts=PTS-STARTPTS,lowpass=f=150,lowpass=f=150",
                          "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    samples = [abs(int.from_bytes(raw[i:i + 2], "little", signed=True)) for i in range(0, len(raw) - 1, 2)]
    ms = rate // 1000
    env = [max(samples[i:i + ms]) for i in range(0, len(samples) - ms + 1, ms)]
    peak = max(env, default=0) or 1
    found, ready = [], 0
    for i, e in enumerate(env):
        if i >= ready and e >= rise * peak:
            half = max(env[i:i + 30]) / 2
            j = i
            while j > 0 and env[j - 1] >= half and i - j < 40:
                j -= 1
            found.append(round(start + j / 1000, 3))
            ready = i + REFRACTORY_MS
    return found


def speeds(tracks):
    """Playing speed of each track: the opener at 1, then each one matched to the beat of the one before."""
    result = [1.0]
    for prev, t in zip(tracks, tracks[1:], strict=False):
        result.append(result[-1] * t["beat"] / prev["beat"])
    return result


def beat(n, t):
    """The bridge's beat count on deck n when its playhead is at second t of the file."""
    cue, bpm = clocks[n]
    return (t - cue) * bpm / 60


def target(out_t, in_t, t_out):
    """Where the incoming track should be when the outgoing one is at t_out: beat for beat from the swap and the drop."""
    return in_t["drop"] + (t_out - out_t["swap"]) * in_t["beat"] / out_t["beat"]


def offset():
    """Milliseconds the user wants the incoming deck ahead of the computed phase (negative: behind), read from
    OFFSET_FILE on every check, so a "push" or "pull" called out during a blend holds instead of being undone."""
    try:
        return float(OFFSET_FILE.read_text().strip()) / 1000
    except (FileNotFoundError, ValueError):
        return 0.0


def start_at(tracks, k):
    """Where track k starts playing: the opener at its `in`, the others where they are when the outgoing track is at its mix."""
    return tracks[0]["in"] if k == 0 else target(tracks[k - 1], tracks[k], tracks[k - 1]["mix"])


def prepare(n, t, speed, start):
    """Park deck n silent at `start`, rate-matched, bass and mids out."""
    d = rs.deck(n)
    rate = (speed - 1) / rs.get(d, "rateRange")
    if abs(rate) > 1:
        raise SystemExit(f"{t['title']} needs {speed:.4f}x, beyond the deck's pitch range")
    rs.send(f"set {d} play 0", f"set {d} sync_enabled 0", f"set {d} quantize 0", f"set {d} keylock 0",
            f"set {d} rate {rate:.6f}", f"set {d} playposition {start / t['duration']:.9f}",
            f"set {d} volume 0", f"set {rs.eq(n)} parameter1 0", f"set {rs.eq(n)} parameter2 0", f"set {rs.eq(n)} parameter3 1")
    rate_check = rs.get(d, "rate")
    if abs(rate_check - rate) > 1e-4:
        rs.log(f"  warning: deck {n} rate reads {rate_check}, expected {rate:.6f}")
    cue = max(rs.get(d, "cue_point"), 0) / 2 / rs.get(d, "track_samplerate")
    clocks[n] = (cue, rs.get(d, "file_bpm"))
    matched[n] = speed
    rs.log(f"  deck {n} ready: {t['title']} from {start:.3f} s at {speed:.5f}x")


def schedule(out_n, out_t, in_n, in_t):
    """Send the whole blend as commands timed on the outgoing deck's clock."""
    o, i = rs.deck(out_n), rs.deck(in_n)
    b = beat_len = out_t["beat"]
    m, swap, end = out_t["mix"], out_t["swap"], out_t["end"]
    quarter = (swap - m) / 4
    hold_mids, hold_highs = in_t.get("hold_mids", 1), in_t.get("hold_highs", 1)
    lines = [f"set {rs.eq(in_n)} parameter3 {hold_highs}",
             f"at {o} {beat(out_n, m):.4f} set {i} play 1",
             f"ramp {o} {beat(out_n, m):.4f} {beat(out_n, m + quarter):.4f} {i} volume 0 1",
             f"ramp {o} {beat(out_n, m + quarter / 2):.4f} {beat(out_n, m + quarter):.4f} {rs.eq(in_n)} parameter2 0 {hold_mids}"]
    s0, s1 = beat(out_n, swap - SWAP_BEATS * b), beat(out_n, swap)
    lines += [f"ramp {o} {s0:.4f} {s1:.4f} {rs.eq(in_n)} parameter1 0 1", f"ramp {o} {s0:.4f} {s1:.4f} {rs.eq(out_n)} parameter1 1 0"]
    if hold_mids < 1:
        lines.append(f"ramp {o} {s0:.4f} {s1:.4f} {rs.eq(in_n)} parameter2 {hold_mids} 1")
    if hold_highs < 1:
        lines.append(f"ramp {o} {s0:.4f} {s1:.4f} {rs.eq(in_n)} parameter3 {hold_highs} 1")
    fade = out_t.get("end_beats", END_BEATS) * beat_len
    if out_t.get("eq_out"):
        span = end - fade - swap
        lines += [f"ramp {o} {beat(out_n, swap):.4f} {beat(out_n, swap + span / 2):.4f} {rs.eq(out_n)} parameter2 1 0",
                  f"ramp {o} {beat(out_n, swap + span * 0.45):.4f} {beat(out_n, end - fade):.4f} {rs.eq(out_n)} parameter3 1 0"]
    lines += [f"ramp {o} {beat(out_n, end - fade):.4f} {beat(out_n, end):.4f} {o} volume - 0",
              f"at {o} {beat(out_n, end):.4f} set {o} play 0"]
    if "jump" in in_t:
        skip_from, skip_to = in_t["jump"]
        lines.append(f"at {i} {beat(in_n, skip_from):.4f} set {i} playposition {skip_to / in_t['duration']:.9f}")
    rs.send(*lines)
    rs.log(f"  scheduled: deck {in_n} in at {m:.3f} s of the outgoing track, bass swap at {swap:.3f}, outgoing ends at {end:.3f}")


def ride(out_n, out_t, in_n, in_t, speed_out):
    """Hold the incoming deck in phase with the outgoing one until the outgoing deck stops."""
    nudged_until = None
    while True:
        s = rs.state()
        d_out, d_in = s["decks"][str(out_n)], s["decks"][str(in_n)]
        if not d_out["play"]:
            return d_in
        t_out = d_out["pos"] * d_out["dur"]
        if not d_in["play"]:
            time.sleep(min(max((out_t["mix"] - t_out) / speed_out - 2, 0.5), 5))
            continue
        if not marks or marks[-1]["in"] != in_t["title"]:
            # The recording clock is the wall clock since it started; the outgoing deck says how long ago the mix began.
            at_mix = time.monotonic() - started[0] - (t_out - out_t["mix"]) / speed_out
            marks.append({"out": out_t["title"], "in": in_t["title"], "mix": round(at_mix, 2),
                          "swap": round(at_mix + (out_t["swap"] - out_t["mix"]) / speed_out, 2),
                          "end": round(at_mix + (out_t["end"] - out_t["mix"]) / speed_out, 2)})
        if nudged_until is not None and t_out < nudged_until:
            time.sleep(0.5)
            continue
        error = d_in["pos"] * d_in["dur"] - target(out_t, in_t, t_out) - offset()
        if abs(error) <= PHASE_TOLERANCE:
            nudged_until = None
            time.sleep(CHECK_SECONDS)
            continue
        # A single reading can be tens of ms off (seen on a damaged mp3), and a nudge on it puts the decks out for
        # real. Only act when a second reading straight after agrees.
        again = rs.state()["decks"]
        t_out = again[str(out_n)]["pos"] * again[str(out_n)]["dur"]
        second = again[str(in_n)]["pos"] * again[str(in_n)]["dur"] - target(out_t, in_t, t_out) - offset()
        if abs(second - error) > CONFIRM_TOLERANCE or not again[str(out_n)]["play"]:
            rs.log(f"  readings disagree ({error * 1000:+.1f} then {second * 1000:+.1f} ms), not nudging")
            time.sleep(0.5)
            continue
        error = (error + second) / 2
        if abs(error) <= PHASE_TOLERANCE:
            continue
        nudge = NUDGE_BIG if abs(error) > 0.012 else NUDGE_SMALL
        seconds = abs(error) / nudge
        base = (matched[in_n] - 1) / rs.get(rs.deck(in_n), "rateRange")
        step = nudge / rs.get(rs.deck(in_n), "rateRange")
        t0 = t_out + LEAD_SECONDS * speed_out
        t1 = t0 + seconds * speed_out
        o, i = rs.deck(out_n), rs.deck(in_n)
        rs.send(f"at {o} {beat(out_n, t0):.4f} set {i} rate {base - step if error > 0 else base + step:.6f}",
                f"at {o} {beat(out_n, t1):.4f} set {i} rate {base:.6f}")
        rs.log(f"  deck {in_n} {abs(error) * 1000:.1f} ms {'ahead' if error > 0 else 'behind'} at {t_out:.2f} s: "
               f"{'slower' if error > 0 else 'faster'} by {nudge:.0%} for {seconds:.2f} s")
        nudged_until = t1 + 0.3 * speed_out


def adopt(n, speed):
    """Take over a deck that is already playing: read its clock and remember its speed."""
    d = rs.deck(n)
    clocks[n] = (max(rs.get(d, "cue_point"), 0) / 2 / rs.get(d, "track_samplerate"), rs.get(d, "file_bpm"))
    matched[n] = speed


def main(plan_path, resume=None, recording_age=0.0):
    """Play the set. With resume=K, track K is already playing (the run before stopped between blends) and the
    recording is `recording_age` seconds old: carry on from track K's blend out, keeping the marks made so far."""
    tracks = json.load(open(plan_path, encoding="utf-8"))
    marks_path = Path(plan_path).with_suffix(".marks.json")
    s = rs.state()
    v = speeds(tracks)
    OFFSET_FILE.unlink(missing_ok=True)
    if resume is None:
        if rs.loaded_track(s, 1, tracks) != 0 or rs.loaded_track(s, 2, tracks) != 1:
            raise SystemExit(f"Expected tracks 1 and 2 on decks 1 and 2, got {s}")
        rs.send("set [Master] crossfader 0", f"set {rs.deck(1)} orientation 1", f"set {rs.deck(2)} orientation 1", "clear")
        for n in (1, 2):
            prepare(n, tracks[n - 1], v[n - 1], start_at(tracks, n - 1))
        rs.send(f"set {rs.eq(1)} parameter1 1", f"set {rs.eq(1)} parameter2 1", f"set {rs.deck(1)} volume 1")
        started[0] = time.monotonic()
        rs.set_recording(True)
        rs.log("recording started" if rs.record else "not recording")
        rs.send(f"set {rs.deck(1)} play 1")
        rs.log(f"1. {tracks[0]['title']}")
        playing, first = 1, 0
    else:
        playing = next((n for n in (1, 2) if rs.loaded_track(s, n, tracks) == resume and s["decks"][str(n)]["play"]), None)
        if playing is None:
            raise SystemExit(f"track {resume + 1} is not playing: {s}")
        started[0] = time.monotonic() - recording_age
        marks[:] = json.loads(marks_path.read_text(encoding="utf-8")) if marks_path.exists() else []
        rs.send("clear")
        adopt(playing, v[resume])
        idle = rs.other(playing)
        if resume + 1 < len(tracks):
            if rs.loaded_track(s, idle, tracks) != resume + 1:
                rs.load_next(idle, tracks, resume + 1)
            prepare(idle, tracks[resume + 1], v[resume + 1], start_at(tracks, resume + 1))
        rs.log(f"resumed at track {resume + 1} on deck {playing}")
        first = resume
    for k in range(first, len(tracks) - 1):
        out_n, in_n = playing, rs.other(playing)
        OFFSET_FILE.unlink(missing_ok=True)
        schedule(out_n, tracks[k], in_n, tracks[k + 1])
        d_in = ride(out_n, tracks[k], in_n, tracks[k + 1], v[k])
        if not d_in["play"]:
            rs.log("  incoming deck not playing when the outgoing one stopped, starting it now")
            rs.send(f"set {rs.deck(in_n)} play 1", f"set {rs.deck(in_n)} volume 1")
        rs.log(f"{k + 2}. {tracks[k + 1]['title']}")
        marks_path.write_text(json.dumps(marks, indent=1), encoding="utf-8")
        rs.finish_transition(out_n, in_n)
        rs.send(f"set {rs.eq(out_n)} parameter3 1")
        playing = in_n
        if k + 2 < len(tracks):
            rs.load_next(out_n, tracks, k + 2)
            prepare(out_n, tracks[k + 2], v[k + 2], start_at(tracks, k + 2))
    last = tracks[-1]
    if "end" in last:
        p = rs.deck(playing)
        fade = last.get("end_beats", END_BEATS) * last["beat"]
        rs.send(f"ramp {p} {beat(playing, last['end'] - fade):.4f} {beat(playing, last['end']):.4f} {p} volume - 0",
                f"at {p} {beat(playing, last['end']):.4f} set {p} play 0")
        rs.log(f"last track playing; fading out at {last['end']:.3f} s")
    rs.wait_until(lambda s: s["decks"][str(playing)]["play"] == 0, "the last track to end", interval=5)
    time.sleep(2)
    rs.set_recording(False)
    rs.send(f"set {rs.deck(1)} orientation 0", f"set {rs.deck(2)} orientation 2")
    rs.log("recording stopped. Set complete." if rs.record else "Set complete.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["render"] and len(args) >= 2:
        render(args[1], *args[2:3], *(float(a) for a in args[3:6]))
        sys.exit(0)
    if args[:1] == ["onsets"] and len(args) in (4, 5):
        times = onsets(args[1], *(float(a) for a in args[2:5]))
        print(" ".join(f"{t:.3f}" for t in times))
        print("gaps", " ".join(f"{b - a:.3f}" for a, b in zip(times, times[1:], strict=False)))
        sys.exit(0)
    if args[:1] != ["run"] or len(args) < 2:
        raise SystemExit(__doc__)
    if "--no-record" in args:
        rs.record = False
        args.remove("--no-record")
    resume = age = None
    if "--resume" in args:
        i = args.index("--resume")
        resume, age = int(args[i + 1]), float(args[i + 2])
        del args[i:i + 3]
    if len(args) > 2:
        rs.log_file = args[2]
    try:
        main(args[1], resume, age or 0.0)
    except Exception as e:
        rs.log(f"FAILED: {e!r}")
        raise

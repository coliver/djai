"""Measure how well each track's Mixxx beatgrid lines up with its kicks.

    check_grid.py sets/plan.json

For every grid beat the kick onset is found in a window around it (the steepest
rise of the low band, below 150 Hz). A line fitted through the offsets gives the
phase error at the start and the tempo error, which shows up as drift along the
track. Output is one line per track, plus the corrected BPM and first beat.
"""
import array
import json
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import config

RATE = 8000
SMOOTH = 40                # samples, 5 ms envelope
WINDOW = 0.07              # seconds either side of the grid beat


def low_band(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(RATE),
                          "-af", "lowpass=f=150,lowpass=f=150", "-f", "s16le", "-"],
                         capture_output=True).stdout
    samples = array.array("h")
    samples.frombytes(raw[:len(raw) // 2 * 2])
    return samples


def envelope(samples):
    out, total = [], 0
    window = [0] * SMOOTH
    for i, s in enumerate(samples):
        v = abs(s)
        total += v - window[i % SMOOTH]
        window[i % SMOOTH] = v
        out.append(total / SMOOTH)
    return out


def offsets(track):
    path = config.music_path(track["location"])
    env = envelope(low_band(path))
    period = 60 / track["bpm"]
    t0 = track["first_beat_frame"] / track["samplerate"]
    half = int(WINDOW * RATE)
    points = []
    beat = 0
    while True:
        centre = int((t0 + beat * period) * RATE)
        if centre + half + 80 >= len(env):
            break
        if centre - half >= 0:
            best, best_i = 0, centre
            for i in range(centre - half, centre + half):
                rise = env[i + 40] - env[i]
                if rise > best:
                    best, best_i = rise, i
            level = statistics.fmean(env[centre - half:centre + half])
            if best > 0.6 * level and level > 300:   # a real kick, not silence or a pad
                # The kick's leading edge: where the envelope first reaches 30% of its peak
                # in the window, which matches where the eye puts the beat on a waveform.
                peak = max(env[centre - half:centre + half + 400])
                edge = next(i for i in range(centre - half, centre + half + 400) if env[i] >= 0.3 * peak)
                points.append((beat * period, (best_i - centre) / RATE * 1000, (edge - centre) / RATE * 1000))
        beat += 1
    return points


def fit(points):
    n = len(points)
    mt = sum(t for t, _ in points) / n
    mo = sum(o for _, o in points) / n
    b = sum((t - mt) * (o - mo) for t, o in points) / sum((t - mt) ** 2 for t, _ in points)
    return mo - b * mt, b  # offset at t=0 (ms), drift (ms per second)


def robust(points):
    """Fit, drop points more than 25 ms off the line, and refit."""
    a, b = fit(points)
    kept = [(t, o) for t, o in points if abs(o - (a + b * t)) < 25]
    if len(kept) >= 64:
        a, b = fit(kept)
    return a, b, len(kept)


def check(track):
    points = offsets(track)
    if len(points) < 64:
        return track, None
    steep = robust([(t, o) for t, o, _ in points])
    edge = robust([(t, e) for t, _, e in points])
    return track, (len(points), steep, edge)


def main(plan_path):
    tracks = json.load(open(plan_path, encoding="utf-8"))
    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(check, tracks))
    report = []
    for track, r in results:
        if r is None:
            print(f"{track['id']:5d}  too few clear kicks to judge   {track['title'][:50]}")
            continue
        n, (a1, b1, k1), (a2, b2, k2) = r
        drift_per_min = b2 * 60
        bpm_fixed = track["bpm"] / (1 + b2 / 1000)
        first_fixed = track["first_beat_frame"] + a2 / 1000 * track["samplerate"]
        print(f"{track['id']:5d} start: steepest {a1:+6.1f} / edge {a2:+6.1f} ms   drift {b1 * 60:+6.1f} / {drift_per_min:+6.1f} ms/min  "
              f"({k2}/{n} kicks)  bpm {track['bpm']:.3f} -> {bpm_fixed:.3f}  {track['title'][:36]}")
        report.append(dict(id=track["id"], title=track["title"], offset_ms=a2, offset_steepest_ms=a1,
                           drift_ms_per_min=drift_per_min, bpm=track["bpm"], bpm_fixed=bpm_fixed,
                           first_beat_frame=track["first_beat_frame"], first_beat_frame_fixed=first_fixed))
    json.dump(report, open(plan_path.replace(".json", ".grid.json"), "w"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1])

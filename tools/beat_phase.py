"""Where, within each beat, does the low band swell? Reported per 8-bar block.

    beat_phase.py sets/plan.json [track id]

Folds the low-band envelope (below 150 Hz) over the beat period of the track's
grid and reports the phase of the loudest point, 0.00 meaning right on the grid
line and 0.50 meaning half a beat after it. A jump from about 0 to about 0.5
along a track means the music shifted against a constant grid (an edit or a
changed bar length), which a single BPM or offset change cannot fix.
"""
import json
import sys

import check_grid as g
import config


def phases(track, bars_per_block=8):
    path = config.music_path(track["location"])
    env = g.envelope(g.low_band(path))
    period = 60 / track["bpm"]
    t0 = track["first_beat_frame"] / track["samplerate"]
    bins = 40
    out = []
    block = bars_per_block * 4
    beat = 0
    last = int((len(env) / g.RATE - t0) / period)
    while beat + block <= last:
        fold = [0.0] * bins
        count = [0] * bins
        for b in range(beat, beat + block):
            start = int((t0 + b * period) * g.RATE)
            n = int(period * g.RATE)
            if start + n >= len(env):
                break
            for i in range(0, n, 8):
                k = i * bins // n
                fold[k] += env[start + i]
                count[k] += 1
        avg = [f / c if c else 0 for f, c in zip(fold, count, strict=True)]
        strength = (max(avg) - min(avg)) / (sum(avg) / bins + 1)
        out.append((beat // 4 + 1, avg.index(max(avg)) / bins, strength))
        beat += block
    return out


def main(argv):
    tracks = json.load(open(argv[1], encoding="utf-8"))
    if len(argv) > 2:
        tracks = [t for t in tracks if t["id"] == int(argv[2])]
    for t in tracks:
        print(f"{t['id']} {t['title'][:50]}")
        line = []
        for bar, phase, strength in phases(t):
            line.append(f"{bar}:{phase:.2f}{'' if strength > 0.5 else '?'}")
        print("  " + "  ".join(line))


if __name__ == "__main__":
    main(sys.argv)

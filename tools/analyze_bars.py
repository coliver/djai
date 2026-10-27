"""Per-bar loudness profile of a track, aligned to its Mixxx beatgrid.

    analyze_bars.py <audio file> <first beat seconds> <bpm> > profile.json

For each 4-beat bar: overall RMS, low band (<120 Hz: kick and bassline) and
high band (>4 kHz: hats), in dBFS. Used to find intros, breakdowns and outros.
"""
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


def levels(path, first_beat, samples_per_bar, band):
    af = (f"atrim=start={first_beat},aformat=channel_layouts=mono,aresample=44100,{band}"
          f"asetnsamples=n={samples_per_bar}:p=0,astats=metadata=1:reset=1,"
          "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-af", af, "-f", "null", "-"],
                         capture_output=True, text=True).stdout
    return [max(float(v), -90.0) if v not in ("-inf", "inf") else -90.0
            for v in re.findall(r"RMS_level=(\S+)", out)]


def main(path, first_beat, bpm):
    first_beat, bpm = float(first_beat), float(bpm)
    samples_per_bar = round(4 * 60 / bpm * 44100)
    bands = {"full": "", "low": "lowpass=f=120,lowpass=f=120,", "high": "highpass=f=4000,"}
    with ThreadPoolExecutor(len(bands)) as pool:   # one ffmpeg decode per band, side by side
        profile = dict(zip(bands, pool.map(lambda band: levels(path, first_beat, samples_per_bar, band), bands.values()), strict=True))
    profile["bar_seconds"] = 4 * 60 / bpm
    json.dump(profile, sys.stdout)


if __name__ == "__main__":
    main(*sys.argv[1:4])

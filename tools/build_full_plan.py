"""Merge the approved per-pair plans (and the new opening tracks) into one full-set plan.

    build_full_plan.py sets/plan.overnight.json

Every approved transition lives in its own `sets/plan.next.*.json` (outgoing track first, incoming second),
each with the grid values that were true when the user approved it. A track shows up in two of them, so
this converts everything to audio frame positions with the grid of the file the number came from:

  in_frame   where the track is cued when it comes in (the incoming entry's start_bar)
  mix_frame  where the blend starts (the outgoing entry's mix_bar)
  cut_frame  where the outgoing deck is stopped early (cut_bar)

run_set.py turns frames into beats from the deck's real cue point, so no grid has to be assumed twice.
The grid fields (bpm, first_beat_frame) are the latest version of each track.
"""
import json
import sys
from pathlib import Path

SETS = Path(__file__).resolve().parent.parent / "sets"
ACID = {t["id"]: t for t in json.load(open(SETS / "pool.acid.json", encoding="utf-8"))}
OPENER_START = 137   # the user starts the mix at his cue point in Fuck The Acid (3:53, a phrase start, drop of the full groove)


def frame(t, bar):
    return t["first_beat_frame"] + (bar - 1) * 4 * t["samplerate"] * 60 / t["bpm"]


def load(name):
    return json.load(open(SETS / name, encoding="utf-8"))


# (outgoing entry, incoming entry, extras for the outgoing, extras for the incoming)
NEW = {
    "ae": ACID[3656], "surprise": ACID[3795], "fta": ACID[3691],
}
pairs = []
# The new opening: Breakdown B Fuck The Acid (Ab) > Fire Wire (Ab), 48 bars.
fire_wire = load("plan.derb.json")[0]
pairs += [
    (dict(NEW["fta"], mix_bar=201, overlap=48), dict(fire_wire, start_bar=8)),
]
approved = [
    ("plan.derb.json", {}, {"grid_preset": "derb_old"}),
    ("plan.next.derb-tutb.json", {"grid_preset_live": "derb_new"}, {}),
    ("plan.next.tutb-jack.json", {}, {}),
    ("plan.next.jack-paranoid.json", {}, {}),
    ("plan.next.paranoid-sq1.json", {}, {}),
    ("plan.next.sq1-u-final.json", {}, {}),
    ("plan.next.u-meteor.json", {}, {}),
    ("plan.next.meteor-reeloop.json", {}, {}),
    ("plan.next.reeloop-husten.json", {"eq_out": True}, {"grid_preset": "start"}),
    ("plan.next.husten-kai.json", {"replay": "sets/live/husten-kai-live-1.jsonl", "grid_preset_live": "end", "replay_bar": 221}, {}),
    ("plan.next.kai-traveller-hold.json", {}, {}),
    ("plan.next.json", {}, {}),
]
for name, out_extra, in_extra in approved:
    a, b = load(name)
    pairs.append((dict(a, **out_extra), dict(b, **in_extra)))

tracks = []          # merged, in order
for i, (out_t, in_t) in enumerate(pairs):
    if i == 0:
        first = dict(out_t)
        first["in_frame"] = frame(first, OPENER_START)
        tracks.append(first)
    cur = tracks[-1]
    # The outgoing side: where the blend starts, how long, and any early stop.
    cur["mix_frame"] = frame(out_t, out_t["mix_bar"])
    cur["overlap"] = out_t["overlap"]
    for key in ("eq_out", "replay", "grid_preset_live", "replay_bar"):
        if key in out_t:
            cur[key] = out_t[key]
    if "cut_bar" in out_t:
        cur["cut_frame"] = frame(out_t, out_t["cut_bar"])
    # The incoming side: where it is cued and anything that keeps it quiet.
    nxt = dict(in_t)
    nxt["in_frame"] = frame(in_t, in_t["start_bar"])
    tracks.append(nxt)

keep = ("id", "title", "bpm", "first_beat_frame", "samplerate", "duration", "key", "location", "grid", "in_frame", "mix_frame",
        "cut_frame", "overlap", "eq_out", "replay", "replay_bar", "grid_preset", "grid_preset_live", "hold_mids", "hold_highs")
out = []
for t in tracks:
    row = {k: t[k] for k in keep if k in t}
    row["set_bpm"] = 140.0
    row["style"] = "late"
    out.append(row)

# The last pair of a file is the latest grid for tracks that appear twice: take the later entry's grid values.
latest = {}
for out_t, in_t in pairs:
    for t in (out_t, in_t):
        latest[t["id"]] = t
for row in out:
    g = latest[row["id"]]
    row["bpm"], row["first_beat_frame"], row["samplerate"] = g["bpm"], g["first_beat_frame"], g["samplerate"]

# Where the playhead is: elapsed time of the set, from the frames.
clock = 0.0
for i, row in enumerate(out):
    start = row["in_frame"] / row["samplerate"]
    if "mix_frame" in row:
        length = row["mix_frame"] / row["samplerate"] - start
    else:
        length = row["duration"] - start
    row["starts_at"] = round(clock, 1)
    clock += length
    print(f"{i + 1:2d}. {row['starts_at'] / 60:5.1f}m {row['key']:>4}  {row['title'][:60]}")
print(f"total about {clock / 60:.1f} min of the plan, at each track's own tempo")
json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), indent=1, ensure_ascii=False)

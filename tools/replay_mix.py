"""Play back a hand mix that record_mix.py logged, by replaying its control moves through the bridge.

    replay_mix.py sets/live/husten-kai-live-1.jsonl sets/plan.next.husten-kai.json [--no-start]

Deck 1 is the reference: every move is scheduled on deck 1's beat count (as the bridge's `at` command),
so the replay follows the record, not the wall clock. Both decks are parked on the plan's start bars first,
sync off, the way they were when the user started. Volume, the three EQ bands, the filter and the play
button are replayed; the changes are thinned to moves of at least 0.01 so the schedule stays short.
Nothing is recorded to audio.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DJAI = str(Path(__file__).resolve().parent / "djai")


def djai(*args):
    return subprocess.run([DJAI, *args], capture_output=True, text=True).stdout


def get(group, key):
    return json.loads(djai("query", f"get {group} {key}"))["value"]


def send(*commands):
    """One bridge call for all of them: each call is a process launch plus a MIDI round-trip."""
    djai("send", "; ".join(commands))


def eq(n):
    return f"[EqualizerRack1_[Channel{n}]_Effect1]"


def cue_sample(t, bar):
    return (t["first_beat_frame"] + (bar - 1) * 4 * t["samplerate"] * 60 / t["bpm"]) * 2


def main(argv):
    args = argv[1:]
    start = "--no-start" not in args
    args = [a for a in args if not a.startswith("--")]
    rows = [json.loads(line) for line in open(args[0], encoding="utf-8")]
    samples = [r for r in rows if "state" in r]
    plan = json.load(open(args[1], encoding="utf-8"))
    ref = 1

    def deck(r, n):
        return r["state"]["decks"].get(str(n))

    # The moment the reference deck was started is the replay's beat zero.
    first_play = next(r for r in samples if deck(r, ref)["play"] == 1)
    second = next(r for r in samples if deck(r, 2)["play"] == 1)
    # How each deck was set up just before the second one started.
    ready = {n: deck(samples[samples.index(second) - 1], n) for n in (1, 2)}

    park = ["clear"]
    for n, t in ((1, plan[0]), (2, plan[1])):
        samples_n = get(f"[Channel{n}]", "track_samples")
        park += [f"set [Channel{n}] play 0", f"set [Channel{n}] sync_enabled 0", f"set [Channel{n}] quantize 1",
                 f"set [Channel{n}] playposition {cue_sample(t, t['start_bar']) / samples_n:.9f}"]
    send(*park)
    time.sleep(0.6)
    send(*[f"set [Channel{n}] cue_set 1" for n in (1, 2)])
    time.sleep(0.4)
    levels = []
    for n in (1, 2):
        state = ready[n]
        levels.append(f"set [Channel{n}] volume {state['vol']:.3f}")
        levels += [f"set {eq(n)} parameter{i} {value:.3f}" for i, value in enumerate(state["eq"], start=1)]
        levels.append(f"set [QuickEffectRack1_[Channel{n}]] super1 {state['filter']:.3f}")
    send(*levels)

    # Build the schedule from the samples after the reference deck started.
    last = {n: {"vol": ready[n]["vol"], "eq": list(ready[n]["eq"]), "filter": ready[n]["filter"], "play": 0}
            for n in (1, 2)}
    lines = []
    for r in samples:
        if r["t"] < first_play["t"]:
            continue
        ref_beat = deck(r, ref)["beat"]
        if ref_beat < 0:
            continue
        for n in (1, 2):
            x = deck(r, n)
            if x is None or (n != ref and r["t"] < second["t"] - 1e-6):
                continue
            prev = last[n]
            if n != ref and x["play"] != prev["play"]:
                # Start a fraction early, the way the planned blends do.
                lines.append(f"at [Channel{ref}] {max(0.0, round(ref_beat - 0.35, 2))} set [Channel{n}] play {x['play']}")
                prev["play"] = x["play"]
            if abs(x["vol"] - prev["vol"]) >= 0.01:
                lines.append(f"at [Channel{ref}] {ref_beat:.2f} set [Channel{n}] volume {x['vol']:.3f}")
                prev["vol"] = x["vol"]
            for i, value in enumerate(x["eq"]):
                if abs(value - prev["eq"][i]) >= 0.01:
                    lines.append(f"at [Channel{ref}] {ref_beat:.2f} set {eq(n)} parameter{i + 1} {value:.3f}")
                    prev["eq"][i] = value
            if abs(x["filter"] - prev["filter"]) >= 0.01:
                lines.append(f"at [Channel{ref}] {ref_beat:.2f} set [QuickEffectRack1_[Channel{n}]] super1 {x['filter']:.3f}")
                prev["filter"] = x["filter"]
        if deck(r, ref)["play"] == 0:
            break
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(lines) + "\n")
    djai("plan", f.name)
    print(f"scheduled {len(lines)} moves")
    if start:
        send(f"set [Channel{ref}] play 1")
        print("playing")


if __name__ == "__main__":
    main(sys.argv)

"""Plan a set from per-bar profiles (see analyze_bars.py).

    plan_set.py sets/pool.json sets/analysis sets/plan.json [--bpm 140] [--opener 755] [--count 14] [--closer id] [--exclude id,id]

The set holds one tempo. A playing record's tempo is never touched, so every
track must sit within MAX_PITCH of the set tempo and is matched to it before
it is heard. Tracks are ordered so neighbouring keys are compatible (after the
small pitch change from the tempo match), because trance wants long blends and
long blends between clashing keys sound wrong. Each blend runs through the
outgoing track's last sustained kick section and the incoming track's first.
"""
import functools
import json
import math
import re
import sys

MAX_PITCH = 0.035   # largest tempo change allowed relative to a track's own BPM
CLOSER_PITCH = 0.065  # the same, for a closing track the user asked for
MIN_RUN = 8         # bars of kick to count as a section
MIN_BLEND = 16      # bars; usually longer (up to MAX_BLEND), but the tracks decide
MIN_SOLO = 32       # bars a track plays alone, from its first breakdown to its own blend out
MAX_BLEND = 64
BEAM = 300

PITCH_CLASS = {"C": 0, "C♯": 1, "D♭": 1, "D": 2, "D♯": 3, "E♭": 3, "E": 4, "F": 5, "F♯": 6, "G♭": 6,
               "G": 7, "G♯": 8, "A♭": 8, "A": 9, "A♯": 10, "B♭": 10, "B": 11}
# Camelot wheel number by pitch class, for minor (A) and major (B) keys.
CAMELOT_MINOR = {8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7, 9: 8, 4: 9, 11: 10, 6: 11, 1: 12}
CAMELOT_MAJOR = {11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7, 0: 8, 7: 9, 2: 10, 9: 11, 4: 12}
WORDS_IGNORED = {"dj", "remix", "mix", "original", "club", "extended", "edit", "version", "feat", "vs", "the", "and",
                 "hq", "rmx", "radio", "vocal"}


def kick_runs(low):
    """1-based inclusive (start, end) bar ranges where the kick/bass is present."""
    ordered = sorted(low)
    reference = ordered[int(len(ordered) * 0.8)]
    kick = [level > reference - 8 for level in low]
    # A single quiet bar (a fill or a drop-out) does not end a section.
    for i in range(1, len(kick) - 1):
        if not kick[i] and kick[i - 1] and kick[i + 1]:
            kick[i] = True
    runs, start = [], None
    for i, k in enumerate(kick + [False]):
        if k and start is None:
            start = i
        elif not k and start is not None:
            if i - start >= MIN_RUN:
                runs.append((start + 1, i))
            start = None
    return runs


def phrase_offset(profile):
    """Bar offset k (0-7) such that 8-bar phrases start at bars 1+k, 9+k, ...

    Chosen where the bar-to-bar energy changes are largest, since sections
    (drops, breakdowns, new elements) begin on phrase boundaries.
    """
    low, high, full = profile["low"], profile["high"], profile["full"]
    novelty = [0.0] + [abs(low[b] - low[b - 1]) + 0.5 * abs(high[b] - high[b - 1]) + 0.5 * abs(full[b] - full[b - 1])
                       for b in range(1, len(low))]
    scores = [sum(novelty[i] for i in range(k, len(novelty), 8)) for k in range(8)]
    return scores.index(max(scores))


def snap(bar, offset):
    """Nearest phrase-start bar (1-based) to bar."""
    return round((bar - 1 - offset) / 8) * 8 + 1 + offset


def camelot(t, set_bpm):
    """(number, letter, fractional pitch offset) of the key as heard at the set tempo."""
    name = t["key"].split("/")[0]
    minor = name.endswith("m")
    pc = PITCH_CLASS[name.rstrip("m")]
    heard = pc + 12 * math.log2(set_bpm / t["bpm"])
    nearest = round(heard)
    table = CAMELOT_MINOR if minor else CAMELOT_MAJOR
    return table[nearest % 12], "A" if minor else "B", abs(heard - nearest)


def compatibility(out, inc, set_bpm):
    """Higher is better. Below 1 the keys are not compatible on the Camelot wheel."""
    (n1, l1, f1), (n2, l2, f2) = camelot(out, set_bpm), camelot(inc, set_bpm)
    step = min((n2 - n1) % 12, (n1 - n2) % 12)
    if l1 == l2:
        score = {0: 3.0, 1: 2.5, 2: 1.0}.get(step, -10.0)
    else:
        score = 2.5 if step == 0 else -10.0
    # A track pitched well away from a true semitone sounds out of tune with its neighbour.
    return score - (1.5 if max(f1, f2) > 0.35 else 0.0)


def describe(t, profile, set_bpm):
    """Find where a track can be blended in (up to its first breakdown) and out (its last kick section)."""
    t["bar_seconds"] = profile["bar_seconds"]
    t["bars"] = len(profile["low"])
    long_runs = [r for r in kick_runs(profile["low"]) if r[1] - r[0] + 1 >= MIN_BLEND]
    if len(long_runs) < 2:
        return False  # one sustained section has no first breakdown to land on
    entry, exit_ = long_runs[0], long_runs[-1]
    offset = phrase_offset(profile)
    entry_start = max(1, snap(entry[0], offset))
    t["break_bar"] = snap(entry[1] + 1, offset)           # first bar of the first breakdown
    t["end_bar"] = min(snap(exit_[1] + 1, offset) - 1, t["bars"])
    t["entry_avail"] = t["break_bar"] - entry_start   # kick bars before the first breakdown
    t["exit_len"] = t["end_bar"] - exit_[0] + 1
    # The blend out must leave MIN_SOLO bars between the first breakdown and its start.
    t["exit_cap"] = min(t["exit_len"], t["end_bar"] - t["break_bar"] + 1 - MIN_SOLO)
    t["start_bar"] = entry_start
    full = profile["full"]
    t["entry_level"] = sum(full[t["break_bar"] - 33:t["break_bar"] - 1]) / 32
    t["exit_level"] = sum(full[t["end_bar"] - 32:t["end_bar"]]) / 32
    return t["entry_avail"] >= MIN_BLEND and t["exit_cap"] >= MIN_BLEND


def blend_bars(out, inc):
    """Blend length for out into inc, or 0 if they cannot be blended for long enough.

    The blend ends as the incoming track reaches its first breakdown, so it can
    be no longer than the kick bars that track has before it.
    """
    n = min(out["exit_cap"], inc["entry_avail"], MAX_BLEND)
    return n // 16 * 16 if n >= MIN_BLEND else 0   # whole 16-bar sections keep both tracks on the phrase


@functools.cache
def words(title):
    return frozenset(w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in WORDS_IGNORED and not w.isdigit())


def same_song(a, b):
    """True when two titles look like versions of one song (artist and name words overlap)."""
    wa, wb = words(a), words(b)
    return len(wa & wb) / max(1, len(wa | wb)) >= 0.4


def order(tracks, opener, count, set_bpm, closer=None):
    """Beam search for the best order. With a closer, the last track is fixed and
    the one before it must blend into it."""
    beam = [([opener], 0.0)]
    steps = count - (2 if closer is not None else 1)
    for step in range(steps):
        grown = []
        final_step = closer is not None and step == steps - 1
        for seq, score in beam:
            last = tracks[seq[-1]]
            for j, t in enumerate(tracks):
                if j in seq or j == closer \
                        or any(same_song(t["title"], tracks[i]["title"]) for i in seq) \
                        or (closer is not None and same_song(t["title"], tracks[closer]["title"])):
                    continue
                c = compatibility(last, t, set_bpm)
                if c < 1.0 or not blend_bars(last, t):
                    continue
                # The last track before the closer has to blend into it.
                if final_step and (compatibility(t, tracks[closer], set_bpm) < 1.0 or not blend_bars(t, tracks[closer])):
                    continue
                level_jump = abs(last["exit_level"] - t["entry_level"])
                grown.append((seq + [j], score + c - 0.3 * level_jump))
        if not grown:
            break
        grown.sort(key=lambda x: -x[1])
        beam = grown[:BEAM]
    if closer is None:
        return beam[0][0]
    finished = []
    for seq, score in beam:
        c = compatibility(tracks[seq[-1]], tracks[closer], set_bpm)
        if c >= 1.0 and blend_bars(tracks[seq[-1]], tracks[closer]):
            finished.append((seq + [closer], score + c))
    if not finished:
        raise SystemExit("No track in the pool blends into the closer in a compatible key; try another opener or tempo")
    return max(finished, key=lambda x: x[1])[0]


def main(pool_path, analysis_dir, plan_path, set_bpm=None, opener_id=755, count=14, exclude=(), closer_id=None):
    pool = json.load(open(pool_path, encoding="utf-8"))
    set_bpm = set_bpm or next(t["bpm"] for t in pool if t["id"] == opener_id)
    tracks = []
    for t in pool:
        # The closer may be pitched further than the rest; it is matched before it is heard.
        limit = CLOSER_PITCH if t["id"] == closer_id else MAX_PITCH
        if abs(set_bpm / t["bpm"] - 1) > limit or t["id"] in exclude:
            continue
        try:
            profile = json.load(open(f"{analysis_dir}/{t['id']}.json"))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        if len(profile.get("low", [])) < 2 * MIN_BLEND:
            continue  # the file would not decode, or is too short to blend
        if describe(t, profile, set_bpm):
            tracks.append(t)
    print(f"{len(tracks)} usable tracks at {set_bpm:.2f} BPM (of {len(pool)} in the pool)")
    opener = next(i for i, t in enumerate(tracks) if t["id"] == opener_id)
    closer = next((i for i, t in enumerate(tracks) if t["id"] == closer_id), None) if closer_id else None
    if closer_id and closer is None:
        raise SystemExit(f"Closer {closer_id} is not usable at {set_bpm:.2f} BPM (not in the pool, no analysis, or no usable structure)")
    sequence = [tracks[i] for i in order(tracks, opener, count, set_bpm, closer)]

    clock = 0.0
    for i, t in enumerate(sequence):
        if i + 1 < len(sequence):
            t["overlap"] = blend_bars(t, sequence[i + 1])
            t["mix_bar"] = t["end_bar"] - t["overlap"] + 1
            sequence[i + 1]["start_bar"] = sequence[i + 1]["break_bar"] - t["overlap"]
            length_bars = t["mix_bar"] - t["start_bar"]
        else:
            length_bars = t["bars"] - t["start_bar"] + 1
        t["starts_at"] = round(clock, 1)
        clock += length_bars * 60 / set_bpm * 4
        n, letter, _ = camelot(t, set_bpm)
        pitch = (set_bpm / t["bpm"] - 1) * 100
        solo = t["mix_bar"] - t["break_bar"] if "mix_bar" in t else t["bars"] - t["break_bar"]
        print(f"{i + 1:2d}. {t['starts_at'] / 60:5.1f}m {t['key']:>7} ({n}{letter}) pitch {pitch:+4.1f}%  "
              f"in at {t['start_bar']:3d} breakdown {t['break_bar']:3d} solo {solo:3d}  mix at {t.get('mix_bar', '-')!s:>3} over {t.get('overlap', '-')!s:>2}  {t['title'][:45]}")
    for t in sequence:
        t["set_bpm"] = set_bpm   # the opener is set to this tempo before it starts; the rest are matched to it
    print(f"total ~{clock / 60:.1f} min at {set_bpm:.2f} BPM")
    json.dump(sequence, open(plan_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    args = sys.argv[1:]
    options = {}
    for flag, name, cast in (("--bpm", "set_bpm", float), ("--opener", "opener_id", int), ("--count", "count", int),
                             ("--closer", "closer_id", int), ("--exclude", "exclude", lambda v: {int(x) for x in v.split(",")})):
        if flag in args:
            i = args.index(flag)
            options[name] = cast(args[i + 1])
            del args[i:i + 2]
    main(*args[:3], **options)

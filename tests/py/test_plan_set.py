import json

import plan_set as p
import pytest
from conftest import write_json

BPM = 140


def profile(runs, bars=200, kick=-20, quiet=-60, bpm=BPM):
    """Per-bar levels with the kick present in the given 0-based [start, end) bar ranges."""
    low = [quiet] * bars
    for a, b in runs:
        for i in range(a, b):
            low[i] = kick
    return {"low": low, "high": [-40] * bars, "full": [v + 5 for v in low], "bar_seconds": 4 * 60 / bpm}


GOOD = [(0, 64), (96, 192)]   # kick, breakdown, kick, outro


def make_track(id=1, title="Alpha One", key="Am", bpm=BPM):
    return {"id": id, "title": title, "bpm": bpm, "key": key}


# --- kick_runs

def test_kick_runs_finds_sustained_sections():
    assert p.kick_runs(profile(GOOD)["low"]) == [(1, 64), (97, 192)]


def test_kick_runs_ignores_a_single_quiet_bar():
    low = profile([(0, 40)], bars=60)["low"]
    low[20] = -60
    assert p.kick_runs(low) == [(1, 40)]


def test_kick_runs_needs_the_low_band_within_8_db_of_the_loud_bars():
    low = profile([(0, 40)], bars=60)["low"]
    for i in range(40, 55):
        low[i] = -29            # 9 dB under the kick level: a pad or bassline tail, not a kick
    assert p.kick_runs(low) == [(1, 40)]
    for i in range(40, 55):
        low[i] = -27            # 7 dB under: still counts
    assert p.kick_runs(low) == [(1, 55)]


def test_kick_runs_drops_sections_shorter_than_the_minimum():
    assert p.kick_runs(profile([(0, 7), (20, 60)])["low"]) == [(21, 60)]


def test_kick_runs_closes_a_section_that_reaches_the_end():
    assert p.kick_runs(profile([(100, 200)])["low"]) == [(101, 200)]


# --- phrase_offset / snap

def test_phrase_offset_is_where_the_energy_changes_land():
    prof = profile([(3, 11), (19, 43), (51, 99)], bars=104)
    assert p.phrase_offset(prof) == 3


def test_phrase_offset_zero_for_phrase_aligned_sections():
    assert p.phrase_offset(profile(GOOD)) == 0


def test_snap_goes_to_the_nearest_phrase_start():
    assert p.snap(1, 0) == 1
    assert p.snap(5, 0) == 1
    assert p.snap(6, 0) == 9
    assert p.snap(10, 3) == 12
    assert p.snap(65, 0) == 65


# --- camelot / compatibility

def test_camelot_minor_and_major_keys():
    assert p.camelot(make_track(key="Am"), BPM)[:2] == (8, "A")
    assert p.camelot(make_track(key="C"), BPM)[:2] == (8, "B")
    assert p.camelot(make_track(key="F♯m/G♭m"), BPM)[:2] == (11, "A")


def test_camelot_follows_the_pitch_change_to_the_set_tempo():
    # +5.95% is one semitone: A minor played faster is heard as B-flat minor.
    n, letter, frac = p.camelot(make_track(key="Am", bpm=BPM / 1.0595), BPM)
    assert (n, letter) == (3, "A")
    assert frac < 0.02


def test_camelot_reports_how_far_from_a_semitone():
    _, _, frac = p.camelot(make_track(key="Am", bpm=BPM / 1.03), BPM)
    assert frac == pytest.approx(0.5, abs=0.05)


@pytest.mark.parametrize("a, b, expected", [
    ("Am", "Am", 3.0),     # same key
    ("Am", "Em", 2.5),     # next on the wheel
    ("Am", "Bm", 1.0),     # two steps
    ("Am", "F♯m", -10.0),  # clash
    ("Am", "C", 2.5),      # relative major
    ("Am", "D", -10.0),
])
def test_compatibility_scores(a, b, expected):
    assert p.compatibility(make_track(key=a), make_track(key=b), BPM) == expected


def test_compatibility_penalises_a_detuned_track():
    slightly_off = make_track(key="Am", bpm=BPM / 1.0234)   # 0.4 semitone sharp
    assert p.compatibility(make_track(key="Am"), slightly_off, BPM) == pytest.approx(1.5)


# --- describe

def test_describe_places_the_blend_points():
    t = make_track()
    assert p.describe(t, profile(GOOD), BPM) is True
    assert t["bars"] == 200
    assert (t["start_bar"], t["break_bar"], t["end_bar"]) == (1, 65, 192)
    assert t["entry_avail"] == 64
    assert t["exit_len"] == 96
    assert t["exit_cap"] == min(96, 192 - 65 + 1 - p.MIN_SOLO)
    assert t["entry_level"] == pytest.approx(-15.0)
    assert t["bar_seconds"] == pytest.approx(4 * 60 / BPM)


def test_describe_rejects_a_track_with_one_sustained_section():
    assert p.describe(make_track(), profile([(0, 150)]), BPM) is False


def test_describe_rejects_when_there_is_no_room_to_blend_in():
    # first kick section is exactly MIN_BLEND-1 bars, so it is not a blend section; only the second counts
    assert p.describe(make_track(), profile([(0, 15), (50, 150)]), BPM) is False


def test_describe_rejects_when_the_solo_part_is_too_short():
    # entry 32 bars, breakdown, and an exit section too close to the breakdown to leave MIN_SOLO bars
    t = make_track()
    assert p.describe(t, profile([(0, 32), (40, 72)], bars=80), BPM) is False
    assert t["exit_cap"] < p.MIN_BLEND


def test_describe_clamps_the_end_to_the_file_length():
    t = make_track()
    assert p.describe(t, profile([(0, 64), (96, 200)], bars=200), BPM)
    assert t["end_bar"] == 200


# --- blend_bars

@pytest.mark.parametrize("exit_cap, entry_avail, expected", [
    (96, 96, 64),   # capped at MAX_BLEND
    (60, 96, 48),   # rounds down to a multiple of 16
    (47, 96, 32),
    (32, 96, 32),
    (31, 96, 16),
    (16, 96, 16),
    (15, 96, 0),
    (96, 20, 16),
    (96, 15, 0),
    (200, 50, 48),
])
def test_blend_bars(exit_cap, entry_avail, expected):
    assert p.blend_bars({"exit_cap": exit_cap}, {"entry_avail": entry_avail}) == expected


# --- title matching

def test_words_drop_noise_and_numbers():
    assert p.words("Skylab - Piece Of Mind (Original Mix) 1999") == {"skylab", "piece", "of", "mind"}


def test_same_song_catches_versions_and_not_other_songs():
    assert p.same_song("Skylab - Piece Of Mind", "Skylab - Piece Of Mind (Extended Mix)")
    assert not p.same_song("Skylab - Piece Of Mind", "Active Edge - Base")
    assert not p.same_song("", "Anything")


# --- order / main

def write_set(tmp_path, tracks, profiles):
    """tracks: list of pool dicts; profiles: {id: profile dict | raw text}."""
    (tmp_path / "analysis").mkdir(exist_ok=True)
    pool = tmp_path / "pool.json"
    write_json(pool, tracks)
    for id, prof in profiles.items():
        path = tmp_path / "analysis" / f"{id}.json"
        if isinstance(prof, str):
            path.write_text(prof, encoding="utf-8")      # raw text, possibly not JSON at all
        else:
            write_json(path, prof)
    return str(pool), str(tmp_path / "analysis"), str(tmp_path / "plan.json")


def pool_of(n, key="Am", **overrides):
    titles = ["Alpha One", "Bravo Two", "Charlie Three", "Delta Four", "Echo Five", "Foxtrot Six", "Golf Seven"]
    return [make_track(id=i + 1, title=titles[i], key=key) for i in range(n)]


def good_profiles(ids):
    return {i: profile(GOOD) for i in ids}


def read_plan(path):
    return json.load(open(path, encoding="utf-8"))


def test_main_plans_a_set_from_the_opener(tmp_path, capsys):
    pool, analysis, out = write_set(tmp_path, pool_of(5), good_profiles(range(1, 6)))
    p.main(pool, analysis, out, opener_id=1, count=4)
    plan = read_plan(out)
    assert len(plan) == 4
    assert plan[0]["id"] == 1
    assert len({t["id"] for t in plan}) == 4
    assert all(t["set_bpm"] == BPM for t in plan)
    for t, nxt in zip(plan, plan[1:], strict=False):
        assert t["overlap"] == p.blend_bars(t, nxt)
        assert t["mix_bar"] == t["end_bar"] - t["overlap"] + 1
        assert nxt["start_bar"] == nxt["break_bar"] - t["overlap"]
    assert "mix_bar" not in plan[-1]
    assert [t["starts_at"] for t in plan] == sorted(t["starts_at"] for t in plan)
    assert plan[0]["starts_at"] == 0
    out_text = capsys.readouterr().out
    assert "5 usable tracks at 140.00 BPM (of 5 in the pool)" in out_text
    assert "total ~" in out_text and "(8A)" in out_text


def test_main_defaults_the_set_tempo_to_the_openers(tmp_path, capsys):
    tracks = pool_of(3)
    tracks[0]["bpm"] = 139.5
    pool, analysis, out = write_set(tmp_path, tracks, good_profiles(range(1, 4)))
    p.main(pool, analysis, out, opener_id=1, count=2)
    assert read_plan(out)[0]["set_bpm"] == 139.5
    assert "at 139.50 BPM" in capsys.readouterr().out


def test_main_leaves_out_tracks_that_cannot_be_used(tmp_path, capsys):
    tracks = pool_of(7)
    tracks[1]["bpm"] = 150               # too far from the set tempo
    profiles = good_profiles(range(1, 8))
    del profiles[3]                      # no analysis file
    profiles[4] = "{ not json"           # unreadable analysis
    profiles[5] = {"low": [-20] * 10, "high": [], "full": [], "bar_seconds": 1}   # too short to blend
    profiles[6] = profile([(0, 150)])    # a single sustained section: nowhere to blend in
    pool, analysis, out = write_set(tmp_path, tracks, profiles)
    p.main(pool, analysis, out, set_bpm=BPM, opener_id=1, count=2, exclude={7})
    assert "1 usable tracks at 140.00 BPM (of 7 in the pool)" in capsys.readouterr().out
    assert [t["id"] for t in read_plan(out)] == [1]


def test_main_stops_early_when_nothing_else_fits(tmp_path):
    tracks = pool_of(3)
    tracks[1]["key"] = "F♯m"
    tracks[2]["key"] = "D"
    pool, analysis, out = write_set(tmp_path, tracks, good_profiles(range(1, 4)))
    p.main(pool, analysis, out, opener_id=1, count=3)
    assert [t["id"] for t in read_plan(out)] == [1]


def test_main_does_not_repeat_versions_of_one_song(tmp_path):
    tracks = pool_of(3)
    tracks[1]["title"] = "Alpha One (Extended Mix)"
    pool, analysis, out = write_set(tmp_path, tracks, good_profiles(range(1, 4)))
    p.main(pool, analysis, out, opener_id=1, count=3)
    assert sorted(t["id"] for t in read_plan(out)) == [1, 3]


def test_main_ends_on_the_closer(tmp_path):
    pool, analysis, out = write_set(tmp_path, pool_of(5), good_profiles(range(1, 6)))
    p.main(pool, analysis, out, opener_id=1, count=4, closer_id=5)
    plan = read_plan(out)
    assert [plan[0]["id"], plan[-1]["id"]] == [1, 5]
    assert len(plan) == 4


def test_the_closer_may_be_pitched_further_than_other_tracks(tmp_path):
    tracks = pool_of(4)
    tracks[3]["bpm"] = BPM / 1.05      # 5% off: beyond MAX_PITCH, inside CLOSER_PITCH
    tracks[3]["key"] = "G♯m"           # sounds like A minor once sped up that far
    tracks[2]["bpm"] = BPM / 1.05      # same pitch but not the closer: left out
    pool, analysis, out = write_set(tmp_path, tracks, good_profiles(range(1, 5)))
    p.main(pool, analysis, out, set_bpm=BPM, opener_id=1, count=3, closer_id=4)
    assert [t["id"] for t in read_plan(out)] == [1, 2, 4]


def test_main_explains_an_unusable_closer(tmp_path):
    pool, analysis, out = write_set(tmp_path, pool_of(3), good_profiles(range(1, 3)))
    with pytest.raises(SystemExit, match="Closer 3 is not usable"):
        p.main(pool, analysis, out, opener_id=1, count=3, closer_id=3)


def test_main_explains_when_nothing_blends_into_the_closer(tmp_path):
    tracks = pool_of(3)
    tracks[2]["key"] = "F♯m"
    pool, analysis, out = write_set(tmp_path, tracks, good_profiles(range(1, 4)))
    with pytest.raises(SystemExit, match="No track in the pool blends into the closer"):
        p.main(pool, analysis, out, opener_id=1, count=3, closer_id=3)


def test_main_closer_must_follow_a_track_that_blends_into_it(tmp_path):
    # Bravo fits Alpha (same key) but not the closer; Charlie fits both. The set has to go through Charlie.
    tracks = pool_of(4)
    tracks[1]["key"] = "Em"            # 9A
    tracks[2]["key"] = "Bm"            # 10A... two steps from 8A, one from Em
    tracks[3]["key"] = "F♯m"           # 11A: one step from Bm, three from Am
    pool, analysis, out = write_set(tmp_path, tracks, good_profiles(range(1, 5)))
    p.main(pool, analysis, out, opener_id=1, count=4, closer_id=4)
    assert [t["id"] for t in read_plan(out)] == [1, 2, 3, 4]


def test_main_prefers_the_smoother_level_change(tmp_path):
    tracks = pool_of(3)
    loud = profile(GOOD, kick=-5)           # entry/exit level far above the others
    profiles = {1: profile(GOOD), 2: loud, 3: profile(GOOD)}
    pool, analysis, out = write_set(tmp_path, tracks, profiles)
    p.main(pool, analysis, out, opener_id=1, count=2)
    assert [t["id"] for t in read_plan(out)] == [1, 3]


def test_script_parses_its_options(tmp_path, run_script, capsys):
    pool, analysis, out = write_set(tmp_path, pool_of(5), good_profiles(range(1, 6)))
    run_script(p, pool, analysis, out, "--bpm", "140", "--opener", "1", "--count", "3",
               "--closer", "5", "--exclude", "2,3")
    assert [t["id"] for t in read_plan(out)] == [1, 4, 5]
    assert "3 usable tracks at 140.00 BPM (of 5 in the pool)" in capsys.readouterr().out


def test_script_without_options_uses_the_defaults(tmp_path, run_script):
    tracks = [make_track(id=755, title="Opener Track")] + [dict(t, id=t["id"] + 10) for t in pool_of(3)]
    pool, analysis, out = write_set(tmp_path, tracks, good_profiles([755, 11, 12, 13]))
    run_script(p, pool, analysis, out)
    plan = read_plan(out)
    assert plan[0]["id"] == 755 and len(plan) == 4

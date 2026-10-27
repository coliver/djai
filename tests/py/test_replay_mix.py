import json
import tempfile

import pytest
import replay_mix
from conftest import done, write_json, write_jsonl

SAMPLES = 1_000_000
PLAN = [
    {"id": 1, "title": "One", "bpm": 120.0, "first_beat_frame": 1000, "samplerate": 44100, "start_bar": 5},
    {"id": 2, "title": "Two", "bpm": 150.0, "first_beat_frame": 2000, "samplerate": 48000, "start_bar": 9},
]


def deck(play=0, beat=0.0, vol=1.0, eq=(1, 1, 1), filter=0.5):
    return {"play": play, "beat": beat, "vol": vol, "eq": list(eq), "filter": filter}


def row(t, d1, d2="absent"):
    decks = {"1": d1}
    if d2 != "absent":
        decks["2"] = d2
    return {"t": t, "state": {"decks": decks}}


def hand_mix():
    return [
        {"header": True, "start": "2026-10-25T00:00:00"},
        row(0.0, deck(0, -4), deck(0)),                                             # before deck 1 started
        row(1.0, deck(1, 0.0), deck(0, vol=0, eq=(0, 0, 1))),                       # deck 1 playing alone
        row(2.0, deck(1, 4.0), deck(0, vol=0, eq=(0, 0, 1))),
        row(3.0, deck(1, 8.0), deck(1, vol=0, eq=(0, 0, 1))),                       # deck 2 starts
        row(4.0, deck(1, 12.0, vol=0.8), deck(1, vol=0.5, eq=(0.5, 0, 1), filter=0.7)),
        row(4.5, deck(1, 13.0, vol=0.8), "absent"),                                  # deck 2 missing from this sample
        row(5.0, deck(1, -1.0), deck(1, vol=0.5)),                                   # reference beat not valid
        row(6.0, deck(1, 14.0, vol=0.8), deck(1, vol=0.505, eq=(0.505, 0, 1))),     # moves under the 0.01 threshold
        row(7.0, deck(0, 20.0, vol=0.8), deck(0, vol=0.5, eq=(0.5, 0, 1))),         # deck 1 stops: end of the replay
        row(8.0, deck(1, 99.0), deck(1)),                                           # after the end: never read
    ]


@pytest.fixture
def files(tmp_path):
    mix = tmp_path / "mix.jsonl"
    write_jsonl(mix, hand_mix())
    plan = tmp_path / "plan.json"
    write_json(plan, PLAN)
    return str(mix), str(plan)


@pytest.fixture
def bridge(fake_run, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    log = {"sent": [], "sends": 0, "plan": None, "queries": []}

    def handler(args, kwargs):
        assert args[0] == replay_mix.DJAI
        kind = args[1]
        if kind == "query":
            log["queries"].append(args[2])
            return done(stdout=json.dumps({"get": "x", "value": SAMPLES}))
        if kind == "send":
            log["sends"] += 1
            log["sent"] += args[2].split("; ")
        elif kind == "plan":
            log["plan"] = open(args[2], encoding="utf-8").read().splitlines()
        return done()
    fake_run(handler)
    return log


def test_helpers(bridge):
    assert replay_mix.eq(2) == "[EqualizerRack1_[Channel2]_Effect1]"
    assert replay_mix.get("[Channel1]", "track_samples") == SAMPLES
    replay_mix.send("clear")
    assert bridge["sent"] == ["clear"]
    assert bridge["queries"] == ["get [Channel1] track_samples"]


def test_cue_sample_converts_a_bar_to_stereo_samples():
    t = PLAN[0]
    # bar 5 is 16 beats in; one beat is 22050 frames at 120 BPM
    assert replay_mix.cue_sample(t, 5) == (1000 + 16 * 22050) * 2
    assert replay_mix.cue_sample(t, 1) == 2000


def test_parks_both_decks_then_schedules_the_hand_mix_and_starts(bridge, files, capsys):
    replay_mix.main(["replay_mix.py", *files])
    sent = bridge["sent"]
    assert sent[0] == "clear"
    for n, t in ((1, PLAN[0]), (2, PLAN[1])):
        position = replay_mix.cue_sample(t, t["start_bar"]) / SAMPLES
        assert f"set [Channel{n}] playposition {position:.9f}" in sent
        assert f"set [Channel{n}] sync_enabled 0" in sent
        assert f"set [Channel{n}] cue_set 1" in sent
    # the decks are put the way they stood just before deck 2 started
    assert "set [Channel2] volume 0.000" in sent
    assert "set [EqualizerRack1_[Channel2]_Effect1] parameter3 1.000" in sent
    assert "set [QuickEffectRack1_[Channel1]] super1 0.500" in sent
    assert sent[-1] == "set [Channel1] play 1"
    assert bridge["sends"] == 4   # park, cue_set, levels, play: one bridge call per phase
    assert capsys.readouterr().out == "scheduled 7 moves\nplaying\n"


def test_schedule_follows_deck_one_beats(bridge, files):
    replay_mix.main(["replay_mix.py", *files])
    assert bridge["plan"] == [
        "at [Channel1] 7.65 set [Channel2] play 1",
        "at [Channel1] 12.00 set [Channel1] volume 0.800",
        "at [Channel1] 12.00 set [Channel2] volume 0.500",
        "at [Channel1] 12.00 set [EqualizerRack1_[Channel2]_Effect1] parameter1 0.500",
        "at [Channel1] 12.00 set [QuickEffectRack1_[Channel2]] super1 0.700",
        "at [Channel1] 14.00 set [QuickEffectRack1_[Channel2]] super1 0.500",
        "at [Channel1] 19.65 set [Channel2] play 0",
    ]


def test_no_start_leaves_the_record_stopped(bridge, files, capsys):
    replay_mix.main(["replay_mix.py", *files, "--no-start"])
    assert "set [Channel1] play 1" not in bridge["sent"]
    assert "playing" not in capsys.readouterr().out
    assert bridge["plan"]


def test_runs_as_a_script(bridge, files, run_script):
    run_script(replay_mix, *files, "--no-start")
    assert bridge["plan"]


def test_replay_that_never_sees_deck_one_stop_schedules_to_the_end_of_the_log(bridge, tmp_path):
    rows = [r for r in hand_mix()[:-3]]
    mix = tmp_path / "open.jsonl"
    write_jsonl(mix, rows)
    plan = tmp_path / "plan2.json"
    write_json(plan, PLAN)
    replay_mix.main(["replay_mix.py", str(mix), str(plan), "--no-start"])
    assert bridge["plan"][-1].startswith("at [Channel1] 12.00 ")

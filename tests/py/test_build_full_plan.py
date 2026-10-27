"""build_full_plan.py is a script: importing it builds the plan from the approved sets/plan.* files,
so these tests run it for real against the repository's own data and check the plan it writes."""
import json
import runpy
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "build_full_plan.py"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("plan") / "full.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["build_full_plan.py", str(out)])
        namespace = runpy.run_path(str(SCRIPT), run_name="__main__")
    return json.loads(out.read_text(encoding="utf-8")), namespace


def test_plan_has_the_opener_the_fire_wire_pair_and_every_approved_pair(built):
    plan, ns = built
    assert len(plan) == len(ns["approved"]) + 2
    assert plan[0]["id"] == ns["NEW"]["fta"]["id"]


def test_every_row_is_ready_for_run_set(built):
    plan, _ = built
    for row in plan:
        assert row["set_bpm"] == 140.0 and row["style"] == "late"
        assert {"id", "title", "bpm", "first_beat_frame", "samplerate", "duration", "in_frame"} <= set(row)
    for row in plan[:-1]:
        assert row["mix_frame"] > row["in_frame"]
        assert row["overlap"] > 0
    assert "mix_frame" not in plan[-1]


def test_the_opener_is_cued_at_the_users_drop(built):
    plan, ns = built
    assert ns["OPENER_START"] == 137   # bar 137 of Fuck The Acid, the drop of the full groove
    assert plan[0]["in_frame"] == pytest.approx(ns["frame"](plan[0], ns["OPENER_START"]))


def test_clock_runs_forward_from_zero(built):
    plan, _ = built
    starts = [row["starts_at"] for row in plan]
    assert starts[0] == 0
    assert starts == sorted(starts)


def test_special_handling_survives_the_merge(built):
    plan, _ = built
    by_key = {key: [row for row in plan if key in row] for key in ("replay", "grid_preset", "grid_preset_live", "eq_out")}
    assert len(by_key["replay"]) == 1 and by_key["replay"][0]["replay_bar"] == 221
    assert by_key["grid_preset"] and by_key["grid_preset_live"] and by_key["eq_out"]


def test_cut_frames_are_converted_when_a_pair_has_a_cut_bar(built):
    plan, _ = built
    cut = [row for row in plan if "cut_frame" in row]
    assert cut
    for row in cut:
        assert row["cut_frame"] > row["mix_frame"]


def test_frame_converts_bars_to_audio_frames(built):
    _, ns = built
    t = {"first_beat_frame": 1000, "samplerate": 44100, "bpm": 120.0}
    assert ns["frame"](t, 1) == 1000
    assert ns["frame"](t, 3) == 1000 + 8 * 22050

import array

import beat_phase
import check_grid as g
from conftest import done, write_json


def steady(bpm=60.0, beats=64, offset=0.0):
    """One burst on every beat, `offset` of a beat late."""
    period = 60 / bpm
    total = int(beats * period * g.RATE)
    samples = array.array("h", [0] * total)
    for b in range(beats):
        start = int((b + offset) * period * g.RATE)
        for i in range(start, min(start + 800, total)):
            samples[i] = 9000
    return samples


def track(bpm=60.0, id=7):
    return {"id": id, "title": "Phase Test", "bpm": bpm, "first_beat_frame": 0, "samplerate": 44100,
            "location": "phase.mp3"}


def test_phases_reports_the_swell_position_per_8_bar_block(monkeypatch):
    monkeypatch.setattr(g, "low_band", lambda path: steady())
    result = beat_phase.phases(track())
    assert [bar for bar, _, _ in result] == [1, 9]
    assert all(phase < 0.1 for _, phase, _ in result)
    assert all(strength > 0.5 for _, _, strength in result)


def test_phases_sees_half_a_beat_of_shift(monkeypatch):
    monkeypatch.setattr(g, "low_band", lambda path: steady(offset=0.5))
    result = beat_phase.phases(track())
    assert all(0.45 < phase < 0.6 for _, phase, _ in result)


def test_phases_with_a_shorter_block(monkeypatch):
    monkeypatch.setattr(g, "low_band", lambda path: steady())
    result = beat_phase.phases(track(), bars_per_block=4)
    assert [bar for bar, _, _ in result][:3] == [1, 5, 9]


def test_phases_is_empty_for_a_file_shorter_than_one_block(monkeypatch):
    monkeypatch.setattr(g, "low_band", lambda path: steady(beats=20))
    assert beat_phase.phases(track()) == []


def run_with_plan(tmp_path, argv_extra, tracks):
    plan = tmp_path / "plan.json"
    write_json(plan, tracks)
    beat_phase.main(["beat_phase.py", str(plan), *argv_extra])


def test_main_prints_every_track_with_marks_for_weak_blocks(fake_run, tmp_path, capsys):
    quiet = steady()
    # A flat low band has no swell at all: its strength is below the 0.5 threshold.
    flat = array.array("h", [3000] * len(quiet))
    data = {"phase.mp3": quiet.tobytes(), "flat.mp3": flat.tobytes()}
    fake_run(lambda args, kw: done(stdout=next(v for k, v in data.items() if any(k in a for a in args))))
    flat_track = dict(track(id=8), location="flat.mp3")
    run_with_plan(tmp_path, [], [track(), flat_track])
    out = capsys.readouterr().out
    assert "7 Phase Test" in out and "8 Track" not in out
    lines = out.splitlines()
    assert lines[1].startswith("  1:0.") and "?" not in lines[1]
    assert "?" in lines[3]


def test_main_can_select_one_track(fake_run, tmp_path, capsys):
    fake_run(lambda args, kw: done(stdout=steady().tobytes()))
    run_with_plan(tmp_path, ["8"], [track(id=7), dict(track(id=8), title="Second")])
    out = capsys.readouterr().out
    assert "Second" in out and "Phase Test" not in out


def test_running_the_file_as_a_script_reaches_main(fake_run, run_script, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    write_json(plan, [track()])
    fake_run(lambda args, kw: done(stdout=steady().tobytes()))
    run_script(beat_phase, str(plan))
    assert "7 Phase Test" in capsys.readouterr().out

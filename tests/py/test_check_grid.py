import array
import json

import check_grid as g
import pytest
from conftest import done, write_json

RATE = g.RATE


def kick_track(bpm=240.0, beats=90, silent_from=None, first_beat_frame=0, shift=0.0):
    """Synthetic low band at RATE: a 600-sample burst on every beat (after `shift` seconds), or silence."""
    period = 60 / bpm
    total = int((first_beat_frame / 44100 + (beats + 2) * period) * RATE)
    samples = array.array("h", [0] * total)
    for b in range(beats):
        if silent_from is not None and b >= silent_from:
            break
        start = int((first_beat_frame / 44100 + b * period + shift) * RATE)
        for i in range(start, min(start + 600, total)):
            samples[i] = 8000
    return samples


def track(bpm=240.0, first_beat_frame=0, id=1):
    return {"id": id, "title": f"Track {id}", "bpm": bpm, "first_beat_frame": first_beat_frame,
            "samplerate": 44100, "location": "a.mp3"}


def test_low_band_decodes_ffmpeg_output(fake_run):
    samples = array.array("h", [1, -2, 3])
    run = fake_run(lambda args, kw: type("R", (), {"stdout": samples.tobytes() + b"\x07"})())
    out = g.low_band("/mnt/c/x.mp3")
    assert list(out) == [1, -2, 3], "a trailing odd byte is dropped"
    assert run.calls[0][0] == "ffmpeg" and "/mnt/c/x.mp3" in run.calls[0]


def test_envelope_is_a_moving_average_of_the_absolute_value():
    env = g.envelope([4000] * 100)
    assert env[0] == 100
    assert env[39] == 4000
    assert env[99] == 4000
    assert g.envelope([-40] * 40)[-1] == 40
    assert g.envelope([]) == []


def test_fit_recovers_a_line():
    points = [(t, 5 + 2 * t) for t in range(10)]
    a, b = g.fit(points)
    assert a == pytest.approx(5) and b == pytest.approx(2)


def test_robust_drops_outliers_when_enough_points_remain():
    points = [(t, 3.0) for t in range(100)] + [(50.5, 200.0)]
    a, b, kept = g.robust(points)
    assert kept == 100
    assert a == pytest.approx(3.0, abs=1e-6) and b == pytest.approx(0, abs=1e-6)


def test_robust_keeps_the_first_fit_when_too_few_points_survive():
    points = [(t, float(t * 40)) for t in range(10)]
    a, b, kept = g.robust(points)
    assert kept < 64
    assert (a, b) == pytest.approx(g.fit(points))


def test_robust_does_not_refit_a_short_track_even_with_an_outlier():
    points = [(t, 3.0) for t in range(20)] + [(10.5, 200.0)]
    a, b, kept = g.robust(points)
    assert (a, b) == pytest.approx(g.fit(points))
    assert (a, b) != pytest.approx((3.0, 0.0), abs=0.5)
    assert kept == 20


def test_offsets_finds_each_kick(monkeypatch):
    monkeypatch.setattr(g, "low_band", lambda path: kick_track(silent_from=80))
    points = g.offsets(track())
    # beat 0 sits at the start of the file and is skipped; beats past the end of the kicks are silence
    assert 70 <= len(points) <= 80
    t, steepest, edge = points[0]
    assert abs(steepest) < 2 and abs(edge) < 3
    assert t == pytest.approx(1 * 0.25)


def test_offsets_measures_a_late_kick_in_milliseconds(monkeypatch):
    monkeypatch.setattr(g, "low_band", lambda path: kick_track(shift=0.010))
    points = g.offsets(track())
    assert points
    assert all(8 < steepest < 12 for _, steepest, _ in points)


def test_offsets_uses_the_location_to_find_the_file(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "low_band", lambda path: seen.append(path) or array.array("h", [0] * 10))
    assert g.offsets(track()) == []
    assert seen == ["/music/a.mp3"]


def test_check_gives_up_on_a_track_without_clear_kicks(monkeypatch):
    monkeypatch.setattr(g, "low_band", lambda path: kick_track(beats=20))
    t, result = g.check(track())
    assert result is None


def test_check_fits_offset_and_drift(monkeypatch):
    monkeypatch.setattr(g, "low_band", lambda path: kick_track(first_beat_frame=4410))
    t, result = g.check(track(first_beat_frame=4410))
    count, steep, edge = result
    assert count > 64
    assert abs(steep[0]) < 2 and abs(edge[0]) < 3
    assert abs(steep[1]) < 0.01


def test_main_reports_every_track_and_writes_the_grid_file(monkeypatch, tmp_path, capsys):
    good, bad = track(id=1), track(id=2)
    bad["location"] = "short.mp3"
    monkeypatch.setattr(g, "low_band", lambda path: kick_track() if "short" not in path else kick_track(beats=10))
    plan = tmp_path / "plan.json"
    write_json(plan, [good, bad])
    g.main(str(plan))
    out = capsys.readouterr().out
    assert "too few clear kicks" in out
    assert out.count("bpm 240.000") == 1
    report = json.loads((tmp_path / "plan.grid.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in report] == [1]
    assert report[0]["bpm_fixed"] == pytest.approx(240.0, rel=1e-3)
    assert report[0]["first_beat_frame_fixed"] == pytest.approx(0, abs=200)


def test_runs_as_a_script(fake_run, run_script, tmp_path):
    samples = kick_track()
    fake_run(lambda args, kw: done(stdout=samples.tobytes()))
    plan = tmp_path / "plan.json"
    write_json(plan, [track()])
    run_script(g, str(plan))
    assert (tmp_path / "plan.grid.json").exists()

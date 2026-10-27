import json

import analyze_bars
from conftest import done


def test_levels_parses_rms_values_and_clamps_silence(fake_run):
    out = "RMS_level=-12.5\nRMS_level=-inf\nRMS_level=inf\nRMS_level=-120\nRMS_level=-3\n"
    run = fake_run(lambda args, kw: done(stdout=out))
    assert analyze_bars.levels("a.mp3", 1.5, 88200, "lowpass=f=120,") == [-12.5, -90.0, -90.0, -90.0, -3.0]
    command = run.calls[0]
    assert command[:3] == ["ffmpeg", "-v", "error"] and "a.mp3" in command
    assert "atrim=start=1.5" in command[command.index("-af") + 1]
    assert "asetnsamples=n=88200" in command[command.index("-af") + 1]


def test_levels_with_no_output(fake_run):
    fake_run(lambda args, kw: done(stdout=""))
    assert analyze_bars.levels("a.mp3", 0, 100, "") == []


def test_main_writes_a_profile_with_three_bands(fake_run, capsys):
    run = fake_run(lambda args, kw: done(stdout="RMS_level=-20\nRMS_level=-21\n"))
    analyze_bars.main("a.mp3", "0.25", "120")
    profile = json.loads(capsys.readouterr().out)
    assert set(profile) == {"full", "low", "high", "bar_seconds"}
    assert profile["full"] == profile["low"] == profile["high"] == [-20.0, -21.0]
    assert profile["bar_seconds"] == 2.0
    filters = [c[c.index("-af") + 1] for c in run.calls]
    assert any("lowpass" in f for f in filters) and any("highpass" in f for f in filters)
    assert all("asetnsamples=n=88200" in f for f in filters)


def test_runs_as_a_script(fake_run, run_script, capsys):
    fake_run(lambda args, kw: done(stdout="RMS_level=-5\n"))
    run_script(analyze_bars, "a.mp3", "0", "140")
    assert json.loads(capsys.readouterr().out)["full"] == [-5.0]

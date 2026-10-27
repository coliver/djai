import json
import sys

import overnight
import pytest
from conftest import done, write_json

TRACKS = [{"duration": 300.0}, {"duration": 400.0}, {"duration": 500.0}]


def state_json(*decks):
    """decks: (play, dur) per deck number from 1."""
    return json.dumps({"decks": {str(i + 1): {"play": play, "dur": dur} for i, (play, dur) in enumerate(decks)}})


class Machine:
    """Stands in for powershell, djai and run_set.py. `exits` is the exit code of each run_set.py run."""

    def __init__(self, exits, states=()):
        self.exits = list(exits)
        self.states = list(states)
        self.calls = []

    def __call__(self, args, kwargs=None):
        self.calls.append(list(args))
        if args[0] == "wslpath":
            return done(stdout="C:\\x\\winvolume.ps1\n")
        if args[0] == "powershell.exe":
            return done()
        if args[0] == overnight.DJAI and args[1] == "send":
            return done()
        if args[0] == overnight.DJAI and args[1] == "query":
            return done(stdout=self.states.pop(0) if self.states else "")
        if args[0] == sys.executable:
            return done(returncode=self.exits.pop(0))
        raise AssertionError(args)

    def runs(self):
        return [c for c in self.calls if c[0] == sys.executable]

    def volumes(self):
        return [c[c.index("-Set") + 1] for c in self.calls if c[0] == "powershell.exe"]


def log_lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def go(fake_run, machine, plan, tmp_path):
    """Run overnight.supervise against the fake machine at the default volume; returns the log path."""
    fake_run(machine)
    log = tmp_path / "log.txt"
    overnight.supervise(str(plan), str(log), 0.03)
    return log


def test_volume_does_nothing_without_a_speaker_match(fake_run, djai_config):
    djai_config.write_text(djai_config.read_text(encoding="utf-8").replace('speaker_match = "Audigy"\n', ""), encoding="utf-8")
    run = fake_run(lambda args, kw: done())
    overnight.volume(0.25)
    assert run.calls == []


def test_volume_runs_the_powershell_script_for_the_audigy(fake_run):
    run = fake_run(lambda args, kw: done(stdout="C:\\p\\winvolume.ps1\n") if args[0] == "wslpath" else done())
    overnight.volume(0.25)
    ps = run.calls[1]
    assert ps[0] == "powershell.exe" and "C:\\p\\winvolume.ps1" in ps
    assert ps[ps.index("-Match") + 1] == "Audigy" and ps[ps.index("-Set") + 1] == "0.25"


def test_playing_index_matches_the_playing_deck_by_duration(fake_run):
    fake_run(lambda args, kw: done(stdout=state_json((1, 400.2), (0, 300.0))))
    assert overnight.playing_index(TRACKS) == 1


def test_playing_index_takes_the_latest_when_both_decks_play(fake_run):
    fake_run(lambda args, kw: done(stdout=state_json((1, 300.0), (1, 500.1))))
    assert overnight.playing_index(TRACKS) == 2


def test_playing_index_is_none_when_nothing_matches(fake_run):
    fake_run(lambda args, kw: done(stdout=state_json((1, 123.0), (0, 300.0))))
    assert overnight.playing_index(TRACKS) is None


def test_playing_index_retries_a_bad_reply(fake_run, clock):
    replies = iter(["", "garbage", state_json((1, 300.0))])
    run = fake_run(lambda args, kw: done(stdout=next(replies)))
    assert overnight.playing_index(TRACKS) == 0
    assert len(run.calls) == 3
    assert clock.sleeps == [1, 1]


def test_playing_index_gives_up_after_five_bad_replies(fake_run):
    run = fake_run(lambda args, kw: done(stdout="nope"))
    assert overnight.playing_index(TRACKS) is None
    assert len(run.calls) == 5


@pytest.fixture
def plan(tmp_path):
    path = tmp_path / "plan.json"
    return write_json(path, TRACKS)


def test_a_clean_run_lowers_and_restores_the_volume(fake_run, plan, tmp_path):
    machine = Machine([0])
    log = go(fake_run, machine, plan, tmp_path)
    assert machine.volumes() == ["0.03", "1.0"]
    assert len(machine.runs()) == 1
    run = machine.runs()[0]
    assert run[1].endswith("run_set.py") and run[2:] == [str(plan), str(log)]
    lines = log_lines(log)
    assert "supervisor: run_set.py exited with 0" in lines[0]
    assert "supervisor: speaker volume restored" in lines[1]


def test_a_crash_resumes_from_the_playing_track(fake_run, plan, tmp_path):
    machine = Machine([1, 0], states=[state_json((1, 400.0), (0, 0))])
    log = go(fake_run, machine, plan, tmp_path)
    first, second = machine.runs()
    assert "--resume" not in first
    assert second[-2:] == ["--resume", "1"]
    assert [c[1] for c in machine.calls if c[0] == overnight.DJAI] == ["send", "query"]
    assert next(c for c in machine.calls if c[0] == overnight.DJAI)[2] == "clear"
    text = "\n".join(log_lines(log))
    assert "exited with 1" in text and "resuming at track 2" in text and "exited with 0" in text


def test_it_gives_up_when_the_music_cannot_be_found(fake_run, plan, tmp_path):
    machine = Machine([1], states=[state_json((0, 0), (0, 0))])
    go(fake_run, machine, plan, tmp_path)
    assert len(machine.runs()) == 1
    assert machine.volumes() == ["0.03", "1.0"]


def test_it_does_not_resume_from_the_last_track(fake_run, plan, tmp_path):
    machine = Machine([1], states=[state_json((1, 500.0), (0, 0))])
    go(fake_run, machine, plan, tmp_path)
    assert len(machine.runs()) == 1


def test_it_stops_after_four_attempts(fake_run, plan, tmp_path):
    machine = Machine([1, 1, 1, 1], states=[state_json((1, 300.0), (0, 0))] * 4)
    go(fake_run, machine, plan, tmp_path)
    assert len(machine.runs()) == 4


def test_the_volume_comes_back_even_if_the_supervisor_dies(fake_run, plan, tmp_path):
    machine = Machine([])

    def handler(args, kwargs):
        if args[0] == sys.executable:
            raise KeyboardInterrupt
        return machine(args, kwargs)
    fake_run(handler)
    with pytest.raises(KeyboardInterrupt):
        overnight.supervise(str(plan), str(tmp_path / "log.txt"), 0.03)
    assert machine.volumes() == ["0.03", "1.0"]


def test_running_the_file_as_a_script_uses_the_default_volume(fake_run, plan, tmp_path, run_script):
    machine = Machine([0])
    fake_run(machine)
    run_script(overnight, str(plan), str(tmp_path / "log.txt"))
    assert machine.volumes() == ["0.03", "1.0"]


def test_main_takes_a_volume_option(fake_run, plan, tmp_path):
    machine = Machine([0])
    fake_run(machine)
    overnight.main(["overnight.py", str(plan), str(tmp_path / "log.txt"), "--volume", "0.2"])
    assert machine.volumes() == ["0.2", "1.0"]

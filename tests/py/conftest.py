import ctypes
import json
import runpy
import subprocess
import sys
import time
from pathlib import Path

import fakewinmm
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

# djai.py loads winmm at import time; there is none on Linux, so hand it the in-memory fake.
ctypes.WinDLL = lambda name: fakewinmm.FakeWinmm()


class Clock:
    """Fake time: sleeping advances monotonic() instantly, so waits and timeouts cost nothing."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []
        self.hooks = []   # run on every sleep: lets a fake finish something "later"

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        for hook in list(self.hooks):
            hook()


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(time, "monotonic", c.monotonic)
    monkeypatch.setattr(time, "sleep", c.sleep)
    return c


@pytest.fixture(autouse=True)
def djai_config(monkeypatch, tmp_path_factory):
    """Every test reads a made-up djai.toml, never the real one."""
    path = tmp_path_factory.mktemp("config") / "djai.toml"
    path.write_text(
        'mixxx_user_dir = "/mixxx"\nmixxx_exe = \'C:\\mixxx.exe\'\nmusic_dir = "/music"\n'
        'recordings_dir = "/rec"\nspeaker_match = "Audigy"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DJAI_CONFIG", str(path))
    return path


@pytest.fixture(autouse=True)
def no_real_subprocess(monkeypatch):
    """Nothing in the suite may reach Mixxx, ffmpeg or PowerShell. Tests that need a process fake it."""
    def refuse(*args, **kwargs):
        raise AssertionError(f"unexpected subprocess call: {args!r}")
    monkeypatch.setattr(subprocess, "run", refuse)


@pytest.fixture
def run_script(monkeypatch):
    """Run a tool's `if __name__ == "__main__"` block with the given argv; returns the script's namespace."""
    def run(module, *args):
        monkeypatch.setattr(sys, "argv", [Path(module.__file__).name, *args])
        return runpy.run_path(module.__file__, run_name="__main__")
    return run


@pytest.fixture
def winmm(monkeypatch, clock):
    """A fresh fake winmm installed as djai.winmm (record_mix imports the same object).
    Scripts re-run through runpy load winmm again, so ctypes.WinDLL hands out this same fake."""
    import djai
    import record_mix
    fake = fakewinmm.FakeWinmm()
    clock.hooks.append(fake.finish_pending)
    monkeypatch.setattr(djai, "winmm", fake)
    monkeypatch.setattr(record_mix, "winmm", fake)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: fake)
    return fake


class Run:
    """Stand-in for subprocess.run that records calls and answers from a handler."""

    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or (lambda args, kwargs: subprocess.CompletedProcess(args, 0, "", ""))

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        return self.handler(args, kwargs)


@pytest.fixture
def fake_run(monkeypatch):
    def install(handler=None):
        run = Run(handler)
        monkeypatch.setattr(subprocess, "run", run)
        return run
    return install


def done(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def write_json(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

import json

import djai
import pytest
import record_mix
from fakewinmm import HEADER, reply_message


def state_reply(n=0):
    return json.dumps({"decks": {"1": {"play": 1, "beat": n}}, "xfader": 0})


@pytest.fixture
def mixxx(winmm):
    """A responder that behaves like the bridge for the commands record_mix sends."""
    class Mixxx:
        recording = 0
        toggles = 0
        samples = 0
        state = staticmethod(state_reply)
        silent = False

    m = Mixxx()

    def respond(text):
        if m.silent:
            return []
        if text.startswith("get [Channel"):
            return [json.dumps({"get": "x", "value": 1000})]
        if text == "get [Recording] status":
            return [json.dumps({"get": "x", "value": m.recording})]
        if text == "set [Recording] toggle_recording 1":
            m.toggles += 1
            m.recording = 0 if m.recording == 2 else 2
            return []
        if text == "state":
            m.samples += 1
            return [reply_message("hello"), m.state(m.samples)]
        return []

    winmm.responder = respond
    m.winmm = winmm
    return m


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_link_get_parses_value_and_returns_none_without_reply(winmm):
    winmm.responder = lambda text: [json.dumps({"value": 5})]
    link = record_mix.Link()
    assert link.get("[Channel1]", "play") == 5
    winmm.responder = lambda text: []
    assert link.ask("state", timeout=0.01) is None
    assert link.get("[Channel1]", "play") is None
    link.close()
    assert winmm.calls[-1] == "midiOutClose"


def test_link_ask_ignores_hello_noise_and_joins_chunks(winmm):
    winmm.responder = lambda text: [
        reply_message("hello"),
        b"\xF0\x01\xF7",
        HEADER + b"Cecho\xF7",
        reply_message("ab", last=False),
        reply_message("cd"),
    ]
    link = record_mix.Link()
    assert link.ask("x") == "abcd"
    link.close()


def test_link_drain_discards_stale_replies(winmm):
    link = record_mix.Link()
    winmm.responder = lambda text: [reply_message("stale")]
    link.send("one")
    assert link.headers[link.next].dwFlags & djai.MHDR_DONE
    winmm.responder = lambda text: [reply_message("fresh")]
    assert link.ask("two") == "fresh"
    link.close()


def test_link_send_waits_for_the_driver(winmm, clock):
    winmm.slow_done = True
    link = record_mix.Link()
    link.send("x")
    assert clock.sleeps == [0.0005]
    link.close()


def test_link_ask_waits_for_a_reply_that_arrives_later(winmm, clock):
    link = record_mix.Link()
    late = []

    def respond(text):
        return []
    winmm.responder = respond

    def deliver():
        if clock.sleeps and not late:
            late.append(True)
            header = winmm.queue.popleft()
            import ctypes
            message = reply_message("late")
            ctypes.memmove(header.lpData, message, len(message))
            header.dwBytesRecorded = len(message)
            header.dwFlags |= djai.MHDR_DONE
    clock.hooks.append(deliver)
    assert link.ask("x") == "late"
    link.close()


def test_records_samples_until_max_seconds(mixxx, tmp_path, clock):
    out = tmp_path / "mix.jsonl"
    record_mix.main(["record_mix.py", str(out), "--hz", "100", "--max-seconds", "1"])
    rows = read_lines(out)
    assert rows[0]["header"] is True
    assert rows[0]["track_samples"] == {"1": 1000, "2": 1000, "3": 1000, "4": 1000}
    assert rows[0]["hz"] == 100.0
    assert "recording" not in rows[0]
    samples = [r for r in rows if "state" in r]
    assert len(samples) > 50, "enough samples to hit the periodic flush"
    assert samples[0]["state"]["decks"]["1"]["beat"] == 1
    assert all(r["t"] <= 1.1 for r in samples)
    assert rows[-1]["footer"] is True and rows[-1]["samples"] == len(samples)
    assert mixxx.toggles == 0


def test_the_file_is_flushed_periodically(mixxx, tmp_path, monkeypatch):
    flushes = []
    real_open = open

    class Spy:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

        def write(self, text):
            return self.f.write(text)

        def flush(self):
            flushes.append(1)
            self.f.flush()

    monkeypatch.setattr(record_mix, "open", lambda *a, **k: Spy(real_open(*a, **k)), raising=False)
    out = tmp_path / "m.jsonl"
    record_mix.main(["record_mix.py", str(out), "--hz", "100", "--max-seconds", "1"])
    samples = len([r for r in read_lines(out) if "state" in r])
    assert samples >= 100
    assert len(flushes) == 1 + samples // record_mix.FLUSH_EVERY   # the header, then one per batch


def test_record_flag_starts_and_stops_audio_recording(mixxx, tmp_path):
    out = tmp_path / "mix.jsonl"
    record_mix.main(["record_mix.py", str(out), "--record", "--hz", "50", "--max-seconds", "0.1"])
    assert read_lines(out)[0]["recording"] is True
    assert mixxx.toggles == 2
    assert mixxx.recording == 0


def test_record_flag_leaves_recording_that_is_already_running_but_still_stops_it(mixxx, tmp_path):
    mixxx.recording = 2
    record_mix.main(["record_mix.py", str(tmp_path / "m.jsonl"), "--record", "--hz", "50", "--max-seconds", "0.05"])
    assert mixxx.toggles == 1


def test_record_flag_when_recording_does_not_start(mixxx, tmp_path):
    mixxx.winmm.responder_base = mixxx.winmm.responder

    def respond(text):
        if text == "set [Recording] toggle_recording 1":
            return []
        return mixxx.winmm.responder_base(text)
    mixxx.winmm.responder = respond
    out = tmp_path / "m.jsonl"
    record_mix.main(["record_mix.py", str(out), "--record", "--hz", "50", "--max-seconds", "0.05"])
    assert read_lines(out)[0]["recording"] is False


def test_stop_file_ends_the_recording_and_is_removed(mixxx, tmp_path):
    stop = tmp_path / "stop"
    out = tmp_path / "m.jsonl"
    original = mixxx.state

    def state(n):
        if n == 3:
            stop.write_text("")
        return original(n)
    mixxx.state = state
    record_mix.main(["record_mix.py", str(out), "--stop-file", str(stop), "--hz", "50"])
    assert not stop.exists()
    samples = [r for r in read_lines(out) if "state" in r]
    assert len(samples) == 3


def test_silent_mixxx_records_no_samples(mixxx, tmp_path):
    out = tmp_path / "m.jsonl"
    mixxx.silent = False
    original = mixxx.winmm.responder

    def respond(text):
        return [] if text == "state" else original(text)
    mixxx.winmm.responder = respond
    record_mix.main(["record_mix.py", str(out), "--hz", "10", "--max-seconds", "0.5"])
    rows = read_lines(out)
    assert [("state" in r) for r in rows] == [False, False]


def test_unparseable_state_is_skipped(mixxx, tmp_path):
    out = tmp_path / "m.jsonl"
    mixxx.state = lambda n: "not json" if n == 1 else state_reply(n)
    record_mix.main(["record_mix.py", str(out), "--hz", "10", "--max-seconds", "0.35"])
    samples = [r for r in read_lines(out) if "state" in r]
    assert samples and samples[0]["state"]["decks"]["1"]["beat"] == 2


def test_ctrl_c_finishes_cleanly_and_stops_recording(mixxx, tmp_path):
    out = tmp_path / "m.jsonl"
    original = mixxx.state

    def state(n):
        if n == 2:
            raise KeyboardInterrupt
        return original(n)
    mixxx.state = state

    def respond_with_interrupt(text, base=mixxx.winmm.responder):
        if text == "state" and mixxx.samples >= 1:
            raise KeyboardInterrupt
        return base(text)
    mixxx.winmm.responder = respond_with_interrupt
    record_mix.main(["record_mix.py", str(out), "--record", "--hz", "50"])
    assert mixxx.recording == 0
    assert mixxx.toggles == 2


def test_runs_as_a_script(mixxx, tmp_path, run_script):
    out = tmp_path / "m.jsonl"
    run_script(record_mix, str(out), "--hz", "50", "--max-seconds", "0.05")
    assert read_lines(out)[0]["header"] is True

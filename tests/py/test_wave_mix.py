import json
import re
import time

import pytest
import run_set as rs
import wave_mix as wm
from conftest import done, write_json

TICK = 0.02     # the bridge's timer


class Sim:
    """A pretend Mixxx that plays: decks move with the fake clock at their rate, and the bridge's `at` and
    `ramp` entries fire on the 20 ms tick, counted the way the bridge counts beats (from the cue, at file_bpm)."""

    def __init__(self, clock, durations, queue=(), file_bpm=120.0, cue=0.0):
        self.clock = clock
        self.last = clock.now
        self.decks = {n: self.fresh(d, file_bpm, cue) for n, d in enumerate(durations, start=1)}
        self.queue = list(queue)
        self.file_bpm = file_bpm
        self.at, self.ramps, self.sent = [], [], []
        self.controls = {"[Recording],status": 0}
        self.drop_starts = False      # lose the scheduled start of the incoming deck
        self.trace = []               # (deck, file time) at every jump
        self.park_error = {}          # deck -> seconds a parked deck lands off where it was put, once
        self.on_tick = None           # called after every tick
        self.glitches = []            # per state reply: {deck: seconds} added to what it reports (a bad reading)

    @staticmethod
    def fresh(duration, file_bpm, cue):
        return {"dur": duration, "t": 0.0, "play": 0, "rate": 0.0, "vol": 1.0, "eq": [1.0, 1.0, 1.0], "cue": cue, "bpm": file_bpm}

    def beat(self, ref):
        d = self.decks[int(ref[8])]
        return (d["t"] - d["cue"] / 2 / 44100) * d["bpm"] / 60

    def advance(self):
        while self.last + TICK <= self.clock.now:
            self.last += TICK
            for d in self.decks.values():
                if d["play"]:
                    d["t"] += TICK * (1 + d["rate"] * 0.08)
                    if d["t"] >= d["dur"]:
                        d["t"], d["play"] = d["dur"], 0
            due = [e for e in self.at if self.beat(e[0]) >= e[1]]
            self.at = [e for e in self.at if e not in due]
            for _, _, command in due:
                if self.drop_starts and command.endswith("play 1"):
                    continue
                self.apply(command)
            for r in list(self.ramps):
                ref, b0, b1, group, key, v0, v1 = r
                b = self.beat(ref)
                if b < b0:
                    continue
                if v0 is None:
                    v0 = r[5] = self.value(group, key)
                f = min((b - b0) / (b1 - b0), 1)
                self.store(group, key, v0 + (v1 - v0) * f)
                if f >= 1:
                    self.ramps.remove(r)
            if self.on_tick:
                self.on_tick()

    def value(self, group, key):
        m = re.match(r"\[EqualizerRack1_\[Channel(\d)\]_Effect1\]", group)
        if m:
            return self.decks[int(m.group(1))]["eq"][int(key[-1]) - 1]
        if group.startswith("[Channel"):
            d = self.decks[int(group[8])]
            return {"play": d["play"], "volume": d["vol"], "rate": d["rate"], "rateRange": 0.08, "cue_point": d["cue"],
                    "playposition": d["t"] / d["dur"],
                    "track_samplerate": 44100, "file_bpm": d["bpm"]}.get(key, 0)
        return self.controls.get(f"{group},{key}", 0)

    def store(self, group, key, value):
        m = re.match(r"\[EqualizerRack1_\[Channel(\d)\]_Effect1\]", group)
        if m:
            self.decks[int(m.group(1))]["eq"][int(key[-1]) - 1] = value
            return
        if not group.startswith("[Channel"):
            if (group, key) == ("[Recording]", "toggle_recording"):
                self.controls["[Recording],status"] = 0 if self.controls["[Recording],status"] == 2 else 2
            elif (group, key, value) == ("[AutoDJ]", "enabled", 1) and self.queue:
                idle = next(n for n, d in self.decks.items() if not d["play"])
                self.decks[idle] = self.fresh(self.queue.pop(0), self.file_bpm, 0.0)
            self.controls[f"{group},{key}"] = value
            return
        n = int(group[8])
        d = self.decks[n]
        if key == "playposition":
            if d["play"]:
                self.trace.append((n, d["t"], value * d["dur"]))
            d["t"] = value * d["dur"] + (0 if d["play"] else self.park_error.pop(n, 0))
        elif key in ("play", "rate"):
            d[key] = value
        elif key == "volume":
            d["vol"] = value

    def apply(self, command):
        self.sent.append(command)
        words = command.split()
        if words[0] == "set":
            self.store(words[1], words[2], float(words[3]))
        elif words[0] == "at":
            self.at.append((words[1], float(words[2]), " ".join(words[3:])))
        elif words[0] == "ramp":
            v0 = None if words[6] == "-" else float(words[6])
            self.ramps.append([words[1], float(words[2]), float(words[3]), words[4], words[5], v0, float(words[7])])
        elif words[0] == "clear":
            self.at, self.ramps = [], []

    def state(self):
        glitch = self.glitches.pop(0) if self.glitches else {}
        return {"decks": {str(n): {"play": d["play"], "pos": (d["t"] + glitch.get(n, 0)) / d["dur"], "dur": d["dur"], "vol": d["vol"],
                                   "eq": list(d["eq"])} for n, d in self.decks.items()}}

    def __call__(self, args, kwargs):
        if args[0] != rs.DJAI:
            raise AssertionError(f"unexpected process {args!r}")
        self.advance()
        kind, text = args[1], args[2]
        if kind == "send":
            for command in text.split("; "):
                self.apply(command)
            return done()
        if text == "state":
            return done(stdout=json.dumps(self.state()))
        _, group, key = text.split(" ")
        return done(stdout=json.dumps({"get": f"{group},{key}", "value": self.value(group, key)}))


def plan():
    return [
        {"title": "A", "duration": 100.0, "beat": 0.5, "in": 10.0, "mix": 20.0, "swap": 36.0, "end": 40.0, "end_beats": 2, "eq_out": True},
        {"title": "B", "duration": 101.0, "beat": 0.5, "drop": 30.0, "jump": [40.0, 60.0],
         "mix": 70.0, "swap": 86.0, "end": 90.0, "hold_mids": 0.25, "hold_highs": 0.4},
        {"title": "C", "duration": 102.0, "beat": 0.49, "drop": 50.0, "end": 60.0},
    ]


@pytest.fixture
def mixxx(clock, fake_run):
    def make(**kwargs):
        sim = Sim(clock, [100.0, 101.0], queue=[102.0], **kwargs)
        fake_run(sim)
        return sim
    return make


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch, tmp_path, clock):
    monkeypatch.setattr(wm, "wall", clock.monotonic)
    monkeypatch.setattr(wm, "clocks", {})
    monkeypatch.setattr(wm, "matched", {})
    monkeypatch.setattr(wm, "marks", [])
    monkeypatch.setattr(wm, "OFFSET_FILE", tmp_path / "offset_ms")
    monkeypatch.setattr(rs, "record", True)
    monkeypatch.setattr(rs, "log_file", None)


def test_speeds_follow_each_beat():
    assert wm.speeds(plan()) == pytest.approx([1.0, 1.0, 0.98])


def test_start_and_target_line_up_the_drop_with_the_swap():
    t = plan()
    assert wm.start_at(t, 0) == 10.0
    assert wm.start_at(t, 1) == pytest.approx(14.0)
    assert wm.start_at(t, 2) == pytest.approx(50 - 16 * 0.98)
    assert wm.target(t[1], t[2], 86.0) == pytest.approx(50.0)


def test_wall_is_the_real_time_clock(monkeypatch):
    monkeypatch.undo()
    assert abs(wm.wall() - time.time()) < 1


def test_track_name_drops_the_track_number():
    assert wm.track_name("/m/x/02. DJ A & DJ B - Access.flac") == "DJ A & DJ B - Access"
    assert wm.track_name("Acid/C - D (Remix) (Acid Trance 1997).mp3") == "C - D (Remix) (Acid Trance 1997)"


def test_full_set(mixxx, tmp_path, capsys):
    sim = mixxx()
    path = write_json(tmp_path / "plan.json", plan())
    wm.main(str(path))
    out = capsys.readouterr().out
    assert "recording started" in out and "Set complete" in out
    assert "1. A" in out and "2. B" in out and "3. C" in out
    assert all(not d["play"] for d in sim.decks.values())
    assert sim.decks[2]["t"] == pytest.approx(90.0, abs=0.1)          # B stopped at its end
    assert sim.decks[1]["t"] == pytest.approx(60.0, abs=0.1)          # C faded out at its end
    assert sim.controls["[Recording],status"] == 0
    # B skipped from its way in to its way out, alone, on the phrase
    assert [(n, round(a), round(b)) for n, a, b in sim.trace] == [(2, 40, 60)]
    # holds and the outgoing eq roll-off were scheduled
    assert any("parameter2 0 0.25" in c for c in sim.sent) and any("parameter3 0.4" in c for c in sim.sent)
    assert any(c.startswith("ramp [Channel1]") and c.endswith("parameter3 1 0") for c in sim.sent)
    # C was matched to B's beat: 0.98x is rate -0.25
    assert "set [Channel1] rate -0.250000" in sim.sent


def test_the_rider_pulls_the_incoming_deck_into_phase(mixxx, tmp_path, capsys):
    sim = mixxx()
    sim.park_error = {2: 0.05}     # the incoming deck sits 50 ms ahead (less the start latency): a big nudge, slower
    errors = []
    t = plan()
    path = write_json(tmp_path / "plan.json", t)

    def watch():
        a, b = sim.decks[1], sim.decks[2]
        if a["dur"] == 100.0 and a["play"] and b["play"] and 26 < a["t"] < 36:
            errors.append(b["t"] - wm.target(t[0], t[1], a["t"]))

    sim.on_tick = watch
    wm.main(str(path))
    out = capsys.readouterr().out
    assert "ahead at" in out and "slower by 2%" in out
    assert max(abs(e) for e in errors) < 0.004


def test_bass_knocked_down_after_the_swap_is_put_back(mixxx, tmp_path, capsys):
    sim = mixxx()
    path = write_json(tmp_path / "plan.json", plan())
    knocked = []

    def knock():
        a, b = sim.decks[1], sim.decks[2]
        if not knocked and a["dur"] == 100.0 and a["t"] > 37:
            b["eq"][0] = 0.0
            knocked.append(a["t"])

    sim.on_tick = knock
    wm.main(str(path))
    assert "deck 2 bass 0.00 and mids 1.00 after the swap, put back up" in capsys.readouterr().out
    assert sim.decks[2]["eq"][0] == 1.0


def test_bass_down_when_the_outgoing_stops_is_put_back(mixxx, tmp_path, capsys):
    sim = mixxx()
    t = plan()
    t[0]["end"] = 36.5
    path = write_json(tmp_path / "plan.json", t)

    def knock():
        a, b = sim.decks[1], sim.decks[2]
        if a["dur"] == 100.0 and a["t"] > 36.1:
            b["eq"][1] = 0.5

    sim.on_tick = knock
    wm.main(str(path))
    assert "deck 2 bass 1.00 and mids 0.50 after the swap, put back up" in capsys.readouterr().out


def test_a_jump_still_to_come_on_the_outgoing_track_survives_the_clear(mixxx, tmp_path):
    sim = mixxx()
    t = plan()
    t[0]["jump"] = [15.0, 17.0]       # the opener skips once while it plays alone, before its blend is scheduled
    path = write_json(tmp_path / "plan.json", t)
    wm.main(str(path))
    assert [(n, round(a), round(b)) for n, a, b in sim.trace] == [(1, 15, 17), (2, 40, 60)]


def test_incoming_that_never_started_is_started(mixxx, tmp_path, capsys):
    sim = mixxx()
    sim.drop_starts = True
    t = plan()[:2]
    del t[1]["jump"]
    path = write_json(tmp_path / "plan.json", t)
    sim.queue = []
    wm.main(str(path))
    assert "starting it now" in capsys.readouterr().out
    assert "set [Channel2] play 1" in sim.sent


def test_pitch_out_of_range_stops(mixxx, tmp_path):
    mixxx()
    t = plan()
    t[1]["beat"] = 0.6        # 1.2x: beyond an 8 percent range
    with pytest.raises(SystemExit, match="beyond the deck's pitch range"):
        wm.main(str(write_json(tmp_path / "plan.json", t)))


def test_wrong_tracks_loaded_stops(clock, fake_run, tmp_path):
    fake_run(Sim(clock, [100.0, 55.0]))
    with pytest.raises(SystemExit, match="Expected tracks 1 and 2"):
        wm.main(str(write_json(tmp_path / "plan.json", plan())))


def test_rate_that_does_not_stick_is_reported(mixxx, tmp_path, capsys):
    sim = mixxx()
    real = sim.value
    sim.value = lambda g, k: 0.5 if k == "rate" else real(g, k)
    wm.prepare(1, plan()[0], 1.0, 10.0)
    assert "rate reads 0.5" in capsys.readouterr().out


def render_calls(fake_run, length="300.5"):
    def handler(args, kwargs):
        return done(stdout=f"{length}\n") if args[0] == "ffprobe" else done()
    return fake_run(handler)


def test_render_whole_track_goes_to_waveforms(fake_run, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wm, "WAVEFORMS", tmp_path / "waveforms")
    run = render_calls(fake_run)
    wm.render("Acid/01. X - Y.mp3")
    probe, ffmpeg = run.calls
    assert probe[-1] == "/music/Acid/01. X - Y.mp3"
    assert ffmpeg[-1] == str(tmp_path / "waveforms" / "X - Y.png")
    graph = ffmpeg[ffmpeg.index("-filter_complex") + 1]
    assert graph.startswith("[0:a]atrim=start=0.0:duration=300.5,")
    assert "text='0'" in graph and "text='300'" in graph and "text='315'" not in graph
    assert "-ss" not in ffmpeg
    assert "ticks every 15 s" in capsys.readouterr().out


def test_render_zoom_stops_at_the_end_of_the_file(fake_run, tmp_path):
    run = render_calls(fake_run, "100.0")
    audio = tmp_path / "a.flac"
    audio.write_bytes(b"")
    wm.render(str(audio), str(tmp_path / "z.png"), 99.0, 4.0, 0.25)
    graph = run.calls[1][run.calls[1].index("-filter_complex") + 1]
    assert run.calls[1][5] == str(audio)
    assert "duration=1.0," in graph and "text='100'" in graph and "text='100.25'" not in graph
    assert "drawgrid=w=600.0:" in graph


def test_cli_render(run_script, fake_run, tmp_path):
    run = render_calls(fake_run)
    with pytest.raises(SystemExit) as e:
        run_script(wm, "render", "x.mp3", str(tmp_path / "o.png"), "10", "2")
    assert e.value.code == 0
    assert "atrim=start=10.0:duration=2.0," in run.calls[1][run.calls[1].index("-filter_complex") + 1]


def test_cli_usage(run_script):
    with pytest.raises(SystemExit, match="wave_mix.py render"):
        run_script(wm, "play")


def test_cli_run_without_recording(run_script, clock, fake_run, tmp_path, monkeypatch):
    sim = Sim(clock, [100.0, 101.0], queue=[102.0])
    fake_run(sim)
    log = tmp_path / "run.log"
    run_script(wm, "run", str(write_json(tmp_path / "plan.json", plan())), str(log), "--no-record")
    assert "not recording" in log.read_text()
    assert sim.controls["[Recording],status"] == 0


def test_cli_run_logs_failure(run_script, tmp_path):
    log = tmp_path / "run.log"
    with pytest.raises(FileNotFoundError):
        run_script(wm, "run", str(tmp_path / "missing.json"), str(log))
    assert "FAILED" in log.read_text()


def test_cli_run_last_track_plays_to_its_end(run_script, clock, fake_run, tmp_path, capsys):
    sim = Sim(clock, [100.0, 101.0])
    fake_run(sim)
    t = plan()[:2]
    for key in ("end", "jump"):
        del t[1][key]
    run_script(wm, "run", str(write_json(tmp_path / "plan.json", t)))
    assert "fading out" not in capsys.readouterr().out
    assert sim.decks[2]["t"] == 101.0 and not sim.decks[2]["play"]


def kicks(times, length, rate=8000, bass_at=()):
    """Raw s16le of 60 Hz kicks starting at `times` (seconds), each ramping up over 8 ms and decaying over 150 ms,
    plus quieter bass notes at `bass_at`."""
    import math
    out = [0] * int(length * rate)
    for t0, amp in [(t, 20000) for t in times] + [(t, 9000) for t in bass_at]:
        for i in range(int(0.2 * rate)):
            t = i / rate
            env = min(t / 0.008, 1) * math.exp(-t / 0.05)
            j = int(t0 * rate) + i
            if j < len(out):
                out[j] += int(amp * env * math.sin(2 * math.pi * 60 * t))
    return b"".join(max(-32768, min(32767, v)).to_bytes(2, "little", signed=True) for v in out)


def test_onsets_find_each_kick_once(fake_run):
    beats = [0.1 + 0.43 * k for k in range(8)]
    run = fake_run(lambda args, kwargs: done(stdout=kicks(beats, 4.0, bass_at=[b + 0.215 for b in beats])))
    found = wm.onsets("Acid/x.mp3", 100.0, 4.0)
    assert len(found) == 8
    assert all(abs(f - (100 + b) - 0.009) <= 0.002 for f, b in zip(found, beats, strict=True))   # half way up each attack
    assert "atrim=start=100.0:duration=4.0" in " ".join(run.calls[0])


def test_onsets_of_silence(fake_run):
    fake_run(lambda args, kwargs: done(stdout=b"\x00\x00" * 800))
    assert wm.onsets("x.mp3", 0.0, 0.1) == []


def test_cli_onsets(run_script, fake_run, capsys):
    fake_run(lambda args, kwargs: done(stdout=kicks([0.1, 0.53, 0.96], 1.5)))
    with pytest.raises(SystemExit) as e:
        run_script(wm, "onsets", "x.mp3", "10", "1.5", "0.6")
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("10.10") and "gaps 0.430 0.430" in out


def test_marks_place_each_blend_in_the_recording(mixxx, tmp_path):
    mixxx()
    path = write_json(tmp_path / "plan.json", plan())
    wm.main(str(path))
    marks = json.loads((tmp_path / "plan.marks.json").read_text())
    assert [(m["out"], m["in"]) for m in marks] == [("A", "B"), ("B", "C")]
    a, b = marks
    assert b["mix"] > a["end"] > a["swap"] > a["mix"] >= 0
    assert a["swap"] - a["mix"] == pytest.approx(16.0, abs=0.01)


def test_a_reading_the_next_one_does_not_confirm_is_ignored(mixxx, tmp_path, capsys):
    sim = mixxx()
    t = plan()[:2]
    del t[1]["jump"]
    sim.queue = []
    real = sim.state
    sent = {"done": False}

    def state():
        a, b = sim.decks[1], sim.decks[2]
        if not sent["done"] and a["play"] and b["play"] and a["t"] > 24:
            sent["done"] = True
            sim.glitches = [{2: 0.06}]       # one reply says the incoming deck is 60 ms ahead, the next one does not
        return real()

    sim.state = state
    wm.main(str(write_json(tmp_path / "plan.json", t)))
    out = capsys.readouterr().out
    assert "readings disagree (+6" in out and "not nudging" in out
    assert "slower by 2%" not in out


def test_an_offset_moves_the_target(mixxx, tmp_path, capsys, monkeypatch):
    mixxx()
    t = plan()[:2]
    del t[1]["jump"]
    real = wm.ride

    def ride(*args):
        wm.OFFSET_FILE.write_text("20")    # the user says push: 20 ms ahead
        return real(*args)

    monkeypatch.setattr(wm, "ride", ride)
    wm.main(str(write_json(tmp_path / "plan.json", t)))
    assert "behind" in capsys.readouterr().out
    wm.OFFSET_FILE.write_text("not a number")
    assert wm.offset() == 0.0


def test_resume_picks_up_from_the_playing_track(clock, fake_run, tmp_path, capsys):
    sim = Sim(clock, [100.0, 55.0], queue=[101.0, 102.0])
    fake_run(sim)
    sim.decks[1]["t"], sim.decks[1]["play"] = 15.0, 1           # A is playing; deck 2 holds something else
    path = write_json(tmp_path / "plan.json", plan())
    write_json(tmp_path / "plan.marks.json", [{"out": "earlier", "in": "A", "mix": 1.0, "swap": 2.0, "end": 3.0}])
    wm.main(str(path), resume=0, recording_age=100.0)
    out = capsys.readouterr().out
    assert "resumed at track 1 on deck 1" in out and "Set complete" in out
    marks = json.loads((tmp_path / "plan.marks.json").read_text())
    assert [m["in"] for m in marks] == ["A", "B", "C"]
    assert marks[1]["mix"] == pytest.approx(105.0, abs=0.1)     # A reaches its mix 5 s in, 100 s into the recording


def test_resume_with_the_next_track_already_parked_and_no_marks(clock, fake_run, tmp_path, capsys):
    sim = Sim(clock, [100.0, 101.0], queue=[102.0])
    fake_run(sim)
    sim.decks[1]["t"], sim.decks[1]["play"] = 15.0, 1
    wm.main(str(write_json(tmp_path / "plan.json", plan())), resume=0)
    assert "loaded track 2" not in capsys.readouterr().out


def test_resume_of_a_track_that_is_not_playing(clock, fake_run, tmp_path):
    fake_run(Sim(clock, [100.0, 101.0]))
    with pytest.raises(SystemExit, match="track 2 is not playing"):
        wm.main(str(write_json(tmp_path / "plan.json", plan())), resume=1)


def test_cli_resume(run_script, clock, fake_run, tmp_path, capsys):
    sim = Sim(clock, [100.0, 101.0], queue=[102.0])
    fake_run(sim)
    sim.decks[2]["t"], sim.decks[2]["play"] = 75.0, 1          # B is playing on deck 2, C is next
    sim.decks[1] = Sim.fresh(102.0, 120.0, 0.0)
    run_script(wm, "run", str(write_json(tmp_path / "plan.json", plan())), "--resume", "1", "50", "--no-record")
    assert "resumed at track 2 on deck 2" in capsys.readouterr().out

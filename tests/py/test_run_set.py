import json

import pytest
import run_set as rs
from conftest import done, write_json, write_jsonl

D1, D2 = "[Channel1]", "[Channel2]"
EQ1, EQ2 = "[EqualizerRack1_[Channel1]_Effect1]", "[EqualizerRack1_[Channel2]_Effect1]"
SAMPLES = 20_000_000


class Mixxx:
    """A pretend Mixxx behind the djai wrapper: records every command sent and answers get/state queries.

    `set G K V` stores the value; a few controls behave: playposition and cue_set (a cue is where the
    deck was parked), and the grid presses (beats_adjust_*, beats_translate_*) which walk the BPM and
    shift the grid, so the code that fits a grid to a preset can converge.
    """

    def __init__(self):
        self.controls = {}
        self.sent = []
        self.queries = []
        self.fail_next = 0           # next N bridge calls exit non-zero
        self.replies = []            # scripted raw replies for the next queries (before normal answers)
        self.offset = {1: 0.0, 2: 0.0}       # frames the live grid sits after the preset grid
        self.stuck = False           # grid presses do nothing
        self.cue_error = 0.0
        self.world = None            # callable -> state dict, or a list of dicts consumed in order
        self.anchor = {1: 0.0, 2: 0.0}
        self.pinned = set()          # controls that ignore `set` (a deck that will not stop)

    def __call__(self, args, kwargs):
        assert args[0] == rs.DJAI
        kind, text = args[1], args[2]
        if self.fail_next:
            self.fail_next -= 1
            return done(returncode=1, stderr="no port")
        if kind == "send":
            for command in text.split("; "):
                self.sent.append(command)
                self.apply(command)
            return done()
        if kind == "plan":
            self.sent.append(f"plan {text}")
            return done()
        self.queries.append(text)
        if self.replies:
            return done(stdout=self.replies.pop(0))
        if text == "state":
            return done(stdout=json.dumps(self.next_state()))
        _, group, key = text.split(" ")
        return done(stdout=json.dumps({"get": f"{group},{key}", "value": self.value(group, key)}))

    def next_state(self):
        if callable(self.world):
            return self.world()
        if isinstance(self.world, list):
            return self.world.pop(0) if len(self.world) > 1 else self.world[0]
        return self.world

    def value(self, group, key):
        n = int(group[8]) if group.startswith("[Channel") else 1
        if key == "beat_closest":
            return self.anchor[n] + 2 * self.offset[n]
        if key == "track_samples":
            return self.controls.get((group, key), SAMPLES)
        if key == "track_samplerate":
            return self.controls.get((group, key), 44100)
        if key == "file_bpm":
            return self.controls.get((group, key), 120.0)
        return self.controls.get((group, key), 0)

    def apply(self, command):
        words = command.split()
        if words[0] != "set":
            return
        group, key, value = words[1], words[2], float(words[3])
        if (group, key) in self.pinned:
            return
        self.controls[(group, key)] = value
        if not group.startswith("[Channel"):
            return
        n = int(group[8])
        if key == "playposition":
            self.anchor[n] = value * SAMPLES
        elif key == "cue_set":
            self.controls[(group, "cue_point")] = self.anchor[n] + self.cue_error
        elif value == 1 and not self.stuck:
            bpm = self.controls.get((group, "file_bpm"), 120.0)
            if key == "beats_adjust_faster":
                self.controls[(group, "file_bpm")] = bpm + rs.GRID_BPM_STEP
            elif key == "beats_adjust_slower":
                self.controls[(group, "file_bpm")] = bpm - rs.GRID_BPM_STEP
            elif key == "beats_translate_later":
                self.offset[n] += rs.GRID_SHIFT_FRAMES
            elif key == "beats_translate_earlier":
                self.offset[n] -= rs.GRID_SHIFT_FRAMES

    def sends(self, *needles):
        return [c for c in self.sent if all(n in c for n in needles)]


@pytest.fixture
def mixxx(fake_run, monkeypatch):
    m = Mixxx()
    fake_run(m)
    monkeypatch.setattr(rs, "log_file", None)
    monkeypatch.setattr(rs, "record", True)
    return m


@pytest.fixture
def logs(monkeypatch):
    lines = []
    monkeypatch.setattr(rs, "log", lines.append)
    return lines


def track(**fields):
    t = {"id": 1, "title": "Track", "bpm": 120.0, "first_beat_frame": 1000, "samplerate": 44100,
         "duration": 300.0, "start_bar": 1, "mix_bar": 33, "overlap": 16, "bars": 100, "set_bpm": 120.0}
    t.update(fields)
    return t


# --- logging and the bridge

def test_log_prints_with_a_timestamp(capsys):
    rs.log("hello")
    out = capsys.readouterr().out
    assert out.endswith(" hello\n") and out[2] == ":" and out[5] == ":"


def test_log_appends_to_the_log_file(monkeypatch, tmp_path, capsys):
    path = tmp_path / "set.log"
    monkeypatch.setattr(rs, "log_file", str(path))
    rs.log("one")
    rs.log("two")
    assert [line.split(" ", 1)[1] for line in path.read_text(encoding="utf-8").splitlines()] == ["one", "two"]


def test_bridge_returns_trimmed_output(mixxx):
    mixxx.replies = ["  pong \n"]
    assert rs.bridge("query", "ping") == "pong"


def test_bridge_retries_a_failing_call(mixxx, logs, clock):
    mixxx.fail_next = 2
    mixxx.replies = ["ok"]
    assert rs.bridge("query", "x") == "ok"
    assert len(logs) == 2 and "failed (no port), attempt 2" in logs[1]
    assert clock.sleeps == [1, 1]


def test_bridge_gives_up_after_the_retries(mixxx, logs):
    mixxx.fail_next = 5
    with pytest.raises(RuntimeError, match="bridge query 'x' failed"):
        rs.bridge("query", "x", retries=2)
    assert len(logs) == 2


def test_send_joins_commands_into_one_call(mixxx):
    rs.send("set a b 1", "set c d 2")
    assert mixxx.sent == ["set a b 1", "set c d 2"]


# --- query_json / get / state

def test_query_json_returns_a_good_reply(mixxx):
    mixxx.replies = ['{"x": 1}']
    assert rs.query_json("q", lambda r: r["x"] == 1) == {"x": 1}


def test_query_json_retries_stray_bad_and_failing_replies(mixxx, logs):
    mixxx.replies = ['{"x": 0}', "not json", '{"x": 1}']
    assert rs.query_json("q", lambda r: r["x"] == 1) == {"x": 1}
    assert "unexpected reply" in logs[0] and "bad reply" in logs[1] and "JSONDecodeError" in logs[1]


def test_query_json_retries_when_the_bridge_itself_fails(mixxx, logs):
    mixxx.fail_next = 1
    mixxx.replies = ['{"x": 1}']
    assert rs.query_json("q", lambda r: True) == {"x": 1}
    assert any("RuntimeError" in line for line in logs)


def test_query_json_gives_up(mixxx, logs):
    mixxx.replies = ["{}"] * 3
    with pytest.raises(RuntimeError, match="kept failing"):
        rs.query_json("q", lambda r: False, attempts=3)


def test_get_returns_the_value_of_the_matching_reply(mixxx):
    mixxx.controls[(D1, "volume")] = 0.5
    assert rs.get(D1, "volume") == 0.5
    assert mixxx.queries == ["get [Channel1] volume"]


def test_get_ignores_a_reply_meant_for_someone_else(mixxx):
    mixxx.replies = [json.dumps({"get": "[Channel2],volume", "value": 9}), json.dumps(["x"])]
    mixxx.controls[(D1, "volume")] = 0.25
    assert rs.get(D1, "volume") == 0.25


def test_state_needs_the_decks(mixxx):
    mixxx.replies = ['{"pong": 1}']
    mixxx.world = {"decks": {}}
    assert rs.state() == {"decks": {}}


def test_name_helpers():
    assert (rs.deck(2), rs.eq(1), rs.other(1), rs.other(2)) == (D2, EQ1, 2, 1)


# --- waiting

def test_wait_until_returns_when_the_predicate_holds(monkeypatch, clock):
    states = iter([{"decks": {"1": {"play": 1}}}, {"decks": {"1": {"play": 0}}}])
    monkeypatch.setattr(rs, "state", lambda: next(states))
    assert rs.wait_until(lambda s: s["decks"]["1"]["play"] == 0, "stop") == {"decks": {"1": {"play": 0}}}
    assert clock.sleeps == [rs.POLL_SECONDS]


def test_wait_until_flags_a_stall_once_nothing_has_played_for_a_minute(monkeypatch, logs, clock):
    calls = {"n": 0}

    def state():
        calls["n"] += 1
        return {"decks": {"1": {"play": 0}}}
    monkeypatch.setattr(rs, "state", state)
    rs.wait_until(lambda s: calls["n"] > 40, "the music", interval=4)
    stalls = [line for line in logs if line.startswith("STALL")]
    assert len(stalls) == 2
    assert "waiting for the music" in stalls[0]


def test_wait_until_forgets_a_stall_when_a_deck_plays_again(monkeypatch, logs):
    states = ([{"decks": {"1": {"play": 0}}}] * 10 + [{"decks": {"1": {"play": 1}}}] + [{"decks": {"1": {"play": 0}}}] * 10
              + [{"decks": {"1": {"play": 1}}}] * 2)
    it = iter(states)
    monkeypatch.setattr(rs, "state", lambda: next(it))
    rs.wait_until(lambda s, c=iter(range(len(states))): next(c) == len(states) - 1, "x", interval=4)  # noqa: B008
    assert logs == []


def test_wait_until_times_out(monkeypatch):
    monkeypatch.setattr(rs, "state", lambda: {"decks": {"1": {"play": 1}}})
    with pytest.raises(RuntimeError, match="timed out waiting for the deck"):
        rs.wait_until(lambda s: False, "the deck", timeout=10, interval=3)


def test_loaded_track_matches_by_duration():
    tracks = [track(duration=300.0), track(duration=400.0)]
    s = {"decks": {"1": {"dur": 400.3}, "2": {"dur": 123.0}}}
    assert rs.loaded_track(s, 1, tracks) == 1
    assert rs.loaded_track(s, 2, tracks) is None
    assert rs.loaded_track(s, 3, tracks) is None
    assert rs.loaded_track({"decks": {"1": None}}, 1, tracks) is None


# --- recording

def test_set_recording_only_toggles_when_the_state_differs(mixxx):
    mixxx.controls[("[Recording]", "status")] = 0
    rs.set_recording(False)
    assert mixxx.sent == []
    rs.set_recording(True)
    assert mixxx.sent == ["set [Recording] toggle_recording 1"]
    mixxx.sent.clear()
    mixxx.controls[("[Recording]", "status")] = 2
    rs.set_recording(True)
    assert mixxx.sent == []
    rs.set_recording(False)
    assert mixxx.sent == ["set [Recording] toggle_recording 1"]


def test_set_recording_does_nothing_with_no_record(mixxx, monkeypatch):
    monkeypatch.setattr(rs, "record", False)
    rs.set_recording(True)
    assert mixxx.sent == [] and mixxx.queries == []


# --- beats and cues

def test_cue_sample_is_in_stereo_samples():
    t = track(first_beat_frame=1000, bpm=120.0)
    assert rs.cue_sample(t, 1) == 2000
    assert rs.cue_sample(t, 3) == (1000 + 8 * 22050) * 2


def test_beats_to_counts_from_the_decks_real_cue(mixxx):
    mixxx.controls[(D1, "cue_point")] = 2000
    mixxx.controls[(D1, "file_bpm")] = 120.0
    # a beat at 120 BPM and 44.1 kHz is 22050 frames = 44100 samples
    assert rs.beats_to(1, 1000 + 4 * 22050) == pytest.approx(4.0)
    mixxx.controls[(D1, "cue_point")] = -1
    assert rs.beats_to(1, 22050 * 2) == pytest.approx(2.0)


def test_mix_beat_uses_frames_when_given_else_the_plan_bar(mixxx):
    t = track(mix_frame=44100 * 4, start_bar=5, mix_bar=9)
    assert rs.mix_beat(1, t, "mix_frame", "mix_bar") == pytest.approx(8.0)
    assert rs.mix_beat(1, track(start_bar=5, mix_bar=9), "mix_frame", "mix_bar") == 16


def test_press_sends_pairs_in_groups_of_five(mixxx, clock):
    rs.press(1, "beats_translate_later", 12)
    assert len(mixxx.sent) == 24
    assert mixxx.sent[:2] == ["set [Channel1] beats_translate_later 1", "set [Channel1] beats_translate_later 0"]
    assert clock.sleeps == [0.2, 0.2, 0.2]


def test_press_zero_times_sends_nothing(mixxx):
    rs.press(1, "x", 0)
    assert mixxx.sent == []


# --- grids

PRESET = {"bpm": 138.0, "first_beat_frame": 5000, "bars": [10, 200]}


@pytest.fixture
def presets(monkeypatch, tmp_path):
    path = tmp_path / "grids.json"
    write_json(path, {"presets": {"p": PRESET}})
    monkeypatch.setattr(rs, "GRID_PRESETS", path)
    return PRESET


def test_grid_errors_measures_the_offset_at_each_fitted_bar(mixxx, presets):
    mixxx.offset[1] = 441.0
    errors = rs.grid_errors(1, presets)
    assert errors == pytest.approx([441.0, 441.0], abs=0.01)
    assert len(mixxx.sends("playposition")) == 2


def test_grid_errors_wraps_to_half_a_beat(mixxx, presets):
    frames_per_beat = 44100 * 60 / presets["bpm"]
    mixxx.offset[1] = frames_per_beat - 100
    assert rs.grid_errors(1, presets) == pytest.approx([-100, -100], abs=0.01)


def test_apply_grid_walks_the_bpm_up_and_shifts_the_grid_onto_the_preset(mixxx, presets, logs):
    mixxx.controls[(D1, "file_bpm")] = 137.5
    mixxx.offset[1] = -1100
    rs.apply_grid(1, "p")
    assert mixxx.controls[(D1, "file_bpm")] == pytest.approx(138.0, abs=rs.GRID_BPM_TOLERANCE)
    assert abs(mixxx.offset[1]) <= rs.GRID_SHIFT_TOLERANCE_MS * 44.1
    assert mixxx.sends("beats_adjust_faster") and not mixxx.sends("beats_adjust_slower")
    assert mixxx.sends("beats_translate_later") and not mixxx.sends("beats_translate_earlier")
    assert mixxx.sent[:3] == ["set [Channel1] play 0", "set [Channel1] sync_enabled 0", "set [Channel1] quantize 0"]
    assert len(logs) == 1 and "deck 1 grid 'p': 138.0" in logs[0] and "warning" not in logs[0]


def test_apply_grid_walks_the_bpm_down_and_shifts_earlier(mixxx, presets, logs):
    mixxx.controls[(D1, "file_bpm")] = 138.3
    mixxx.offset[1] = 880
    rs.apply_grid(1, "p")
    assert mixxx.sends("beats_adjust_slower") and not mixxx.sends("beats_adjust_faster")
    assert mixxx.sends("beats_translate_earlier")
    assert abs(mixxx.offset[1]) <= rs.GRID_SHIFT_TOLERANCE_MS * 44.1


def test_apply_grid_presses_nothing_when_the_grid_already_fits(mixxx, presets, logs):
    mixxx.controls[(D1, "file_bpm")] = 138.0
    rs.apply_grid(1, "p")
    assert not mixxx.sends("beats_")


def test_apply_grid_warns_when_it_cannot_get_close_enough(mixxx, presets, logs):
    mixxx.controls[(D1, "file_bpm")] = 138.0
    mixxx.offset[1] = 3000
    mixxx.stuck = True
    rs.apply_grid(1, "p")
    assert len(logs) == 2 and "more than 8 ms off the preset" in logs[1]


def test_apply_grid_never_touches_a_playing_deck(mixxx, presets, logs):
    mixxx.controls[(D1, "play")] = 1
    mixxx.pinned.add((D1, "play"))       # the stop command is ignored: the deck really is playing
    rs.apply_grid(1, "p")
    assert not mixxx.sends("beats_")
    assert logs == ["  warning: deck 1 is playing, grid 'p' not applied"]


def test_live_grid_error_reads_the_nearest_live_beat(mixxx, presets):
    frames_per_beat = 44100 * 60 / presets["bpm"]
    mixxx.anchor[1] = 2 * (presets["first_beat_frame"] + 7 * frames_per_beat + 300)
    assert rs.live_grid_error(1, presets) == pytest.approx(300)
    mixxx.anchor[1] = 2 * (presets["first_beat_frame"] + 7 * frames_per_beat - 300)
    assert rs.live_grid_error(1, presets) == pytest.approx(-300)


def test_regrid_live_moves_a_playing_deck_without_stopping_it(mixxx, presets, logs):
    mixxx.controls[(D1, "file_bpm")] = 137.4
    mixxx.anchor[1] = 2 * presets["first_beat_frame"]
    mixxx.offset[1] = -660
    rs.regrid_live(1, "p")
    assert not mixxx.sends("play ")
    assert mixxx.sends("beats_adjust_faster") and mixxx.sends("beats_translate_later")
    assert abs(mixxx.offset[1]) <= rs.GRID_SHIFT_TOLERANCE_MS * 44.1
    assert "regridded while playing to 'p': 137.4" in logs[0] and "warning" not in "".join(logs)


def test_regrid_live_walks_down_and_earlier_and_warns_if_stuck(mixxx, presets, logs):
    mixxx.controls[(D1, "file_bpm")] = 138.2
    mixxx.anchor[1] = 2 * presets["first_beat_frame"]
    mixxx.offset[1] = 2000
    mixxx.stuck = True
    rs.regrid_live(1, "p")
    assert mixxx.sends("beats_adjust_slower") and mixxx.sends("beats_translate_earlier")
    assert "more than 8 ms off the preset" in logs[-1]


# --- parking a deck

def test_prepare_parks_the_deck_silent_on_the_start_bar(mixxx, logs):
    t = track(start_bar=5)
    rs.prepare(1, t)
    cue_at = rs.cue_sample(t, 5)
    assert mixxx.sent[:6] == [
        "set [Channel1] play 0", "set [Channel1] sync_enabled 0", "set [Channel1] keylock 0",
        "set [Channel1] quantize 1", "set [Channel1] rate 0",
        f"set [Channel1] playposition {cue_at / SAMPLES:.9f}",
    ]
    assert "set [Channel1] cue_set 1" in mixxx.sent
    assert mixxx.sent[-4:] == ["set [Channel1] volume 0", f"set {EQ1} parameter1 0", f"set {EQ1} parameter2 0",
                               f"set {EQ1} parameter3 1"]
    assert logs == ["  deck 1 ready: Track from bar 5"]


def test_prepare_prefers_an_exact_frame_over_a_bar(mixxx, logs):
    t = track(in_frame=123456.0)
    del t["start_bar"]
    rs.prepare(2, t)
    assert f"set [Channel2] playposition {123456.0 * 2 / SAMPLES:.9f}" in mixxx.sent
    assert logs == ["  deck 2 ready: Track from frame 123456"]


def test_prepare_applies_a_saved_grid_first(mixxx, logs, monkeypatch):
    applied = []
    monkeypatch.setattr(rs, "apply_grid", lambda n, name: applied.append((n, name)))
    rs.prepare(1, track(grid_preset="start"))
    assert applied == [(1, "start")]
    applied.clear()
    rs.prepare(1, track(grid_preset=None))
    assert applied == []


def test_prepare_warns_when_the_cue_landed_somewhere_else(mixxx, logs):
    mixxx.cue_error = 5000
    rs.prepare(1, track())
    assert len(logs) == 2 and logs[0].startswith("  warning: deck 1 cue at ")


def test_finish_transition_resets_the_stopped_deck(mixxx):
    rs.finish_transition(1, 2)
    assert mixxx.sent == ["set [Channel1] volume 1", f"set {EQ1} parameter1 1", f"set {EQ1} parameter2 1",
                          "set [Channel1] sync_enabled 0", "set [Channel2] sync_enabled 0"]


# --- loading the next track

LOADED = {"decks": {"1": {"play": 1, "dur": 300.0}, "2": {"play": 0, "dur": 400.0}}}
TRACKS3 = [track(duration=300.0), track(duration=400.0), track(duration=500.0)]


def test_load_next_uses_auto_dj_then_hands_the_decks_back(mixxx, logs):
    mixxx.world = [{"decks": {"1": {"play": 1, "dur": 300.0}, "2": {"play": 0, "dur": 999.0}}}, LOADED]
    assert rs.load_next(2, TRACKS3, 1) == LOADED
    assert mixxx.sent == [
        "set [Master] crossfader -1.0",
        "set [Channel1] orientation 0", "set [Channel2] orientation 2",
        "set [AutoDJ] enabled 1",
        "set [AutoDJ] enabled 0",
        "set [Channel1] orientation 1", "set [Channel2] orientation 1",
    ]
    assert logs == ["  loaded track 2 on deck 2"]


def test_load_next_moves_the_crossfader_to_the_playing_decks_side(mixxx):
    mixxx.world = {"decks": {"1": {"play": 0, "dur": 400.0}, "2": {"play": 1, "dur": 300.0}}}
    rs.load_next(1, TRACKS3, 1)
    assert mixxx.sent[0] == "set [Master] crossfader 1.0"


def test_load_next_gives_up_but_still_turns_auto_dj_off(mixxx, logs):
    mixxx.world = {"decks": {"1": {"play": 1, "dur": 300.0}, "2": {"play": 0, "dur": 999.0}}}
    with pytest.raises(RuntimeError, match="timed out waiting for track 3 on deck 2"):
        rs.load_next(2, TRACKS3, 2)
    assert mixxx.sent[-3:] == ["set [AutoDJ] enabled 0", "set [Channel1] orientation 1", "set [Channel2] orientation 1"]
    assert logs == []


# --- scheduling a blend

def sched(mixxx, out_t=None, in_t=None, out_n=1, in_n=2):
    out_t = out_t or track()
    rs.schedule_transition(out_n, out_t, in_n, in_t)
    return mixxx.sent


def test_default_blend_is_a_set_of_ramps_on_the_outgoing_decks_beats(mixxx, logs):
    sent = sched(mixxx)
    assert sent == [
        "set [Channel1] sync_leader 1", "set [Channel2] sync_enabled 1",
        "at [Channel1] 127.75 set [Channel2] play 1",
        "ramp [Channel1] 128 144.0 [Channel2] volume 0 1",
        f"ramp [Channel1] 136.0 144.0 {EQ2} parameter2 0 1",
        f"ramp [Channel1] 160 168 {EQ2} parameter1 0 1",
        f"ramp [Channel1] 160 168 {EQ1} parameter1 1 0",
    ]
    assert logs == ["  scheduled: deck 2 starts at outgoing beat 128 (bar 33), 16 bar blend, bass swap at bar 8, "
                    "outgoing plays out"]


def test_default_blend_swaps_bass_on_a_phrase_and_never_after_the_blend_ends(mixxx):
    sent = sched(mixxx, track(mix_bar=33, overlap=8))
    # an 8 bar blend is 32 beats: the swap (32 beats in) is pulled back to leave room for the 8 beat swap itself
    assert f"ramp [Channel1] 144 152 {EQ2} parameter1 0 1" in sent


def test_default_blend_runs_the_outgoing_deck_out_when_the_next_track_is_far_off(mixxx, logs):
    sched(mixxx, track(bars=100), track(mix_bar=101, start_bar=1))
    assert not mixxx.sends("volume 1 0") and logs[-1].endswith("outgoing plays out")


def test_default_blend_fades_and_stops_the_outgoing_deck_when_the_next_blend_is_close(mixxx, logs):
    sched(mixxx, track(bars=100), track(mix_bar=33, start_bar=1))
    assert "ramp [Channel1] 216 224 [Channel1] volume 1 0" in mixxx.sent
    assert "at [Channel1] 224 set [Channel1] play 0" in mixxx.sent
    assert logs[-1].endswith("outgoing fades out at beat 224")


def test_default_blend_keeps_the_outgoing_deck_when_it_would_stop_within_8_beats_of_its_own_end(mixxx, logs):
    # stop_at is beat 224; the track's own end is (59 - 1) * 4 = 232 beats in, exactly 8 beats later
    sched(mixxx, track(bars=59), track(mix_bar=33, start_bar=1))
    assert not mixxx.sends("play 0") and logs[-1].endswith("outgoing plays out")
    mixxx.sent.clear()
    sched(mixxx, track(bars=60), track(mix_bar=33, start_bar=1))
    assert mixxx.sends("at [Channel1] 224 set [Channel1] play 0")


def test_default_blend_ignores_a_next_track_without_a_blend(mixxx, logs):
    sched(mixxx, track(), {"title": "last", "start_bar": 1})
    assert not mixxx.sends("play 0") and logs[-1].endswith("outgoing plays out")


def test_blend_from_deck_two_to_deck_one(mixxx):
    sent = sched(mixxx, out_n=2, in_n=1)
    assert sent[0:2] == ["set [Channel2] sync_leader 1", "set [Channel1] sync_enabled 1"]
    assert f"ramp [Channel2] 160 168 {EQ2} parameter1 1 0" in sent


def test_drop_blend_trades_basslines_at_once(mixxx, logs):
    sent = sched(mixxx, track(style="drop"))
    assert sent == [
        "set [Channel1] sync_leader 1", "set [Channel2] sync_enabled 1",
        f"set {EQ2} parameter2 1",
        "at [Channel1] 127.75 set [Channel2] play 1",
        "ramp [Channel1] 128 136 [Channel2] volume 0 1",
        f"ramp [Channel1] 128 136 {EQ2} parameter1 0 1",
        f"ramp [Channel1] 128 136 {EQ1} parameter1 1 0",
    ]
    assert logs == ["  scheduled (drop): deck 2 starts at outgoing beat 128 (bar 33), basslines trade places right there, "
                    "outgoing plays out"]


def test_late_blend_keeps_the_outgoing_bass_until_the_end(mixxx, logs):
    sent = sched(mixxx, track(style="late"))
    assert sent == [
        "set [Channel1] sync_leader 1", "set [Channel2] sync_enabled 1",
        f"set {EQ2} parameter3 1",
        "at [Channel1] 127.75 set [Channel2] play 1",
        "ramp [Channel1] 128 144.0 [Channel2] volume 0 1",
        f"ramp [Channel1] 136.0 144.0 {EQ2} parameter2 0 1",
        f"ramp [Channel1] 191.0 193.0 {EQ2} parameter1 0 1",
        f"ramp [Channel1] 191.0 193.0 {EQ1} parameter1 1 0",
    ]
    assert logs == ["  scheduled (late swap): deck 2 starts at outgoing beat 128.0, outgoing keeps its bass until beat 192, "
                    "then a fast cut to the incoming bass"]


def test_late_blend_holds_back_an_incoming_lead_until_the_bass_swap(mixxx):
    sent = sched(mixxx, track(style="late"), {"hold_mids": 0.3, "hold_highs": 0.2})
    assert f"set {EQ2} parameter3 0.2" in sent
    assert f"ramp [Channel1] 136.0 144.0 {EQ2} parameter2 0 0.3" in sent
    assert f"ramp [Channel1] 191.0 193.0 {EQ2} parameter2 0.3 1" in sent
    assert f"ramp [Channel1] 191.0 193.0 {EQ2} parameter3 0.2 1" in sent


def test_late_blend_can_stop_the_outgoing_deck_early_and_bring_down_its_mids_and_highs(mixxx, logs):
    sent = sched(mixxx, track(style="late", cut_bar=70, eq_out=True))
    # cut at (70-1)*4 = 276; the blend ends at beat 192, so mids start at 192+1+1 = 194
    assert "at [Channel1] 276.000 set [Channel1] play 0" in sent
    assert f"ramp [Channel1] 194.0 235.0 {EQ1} parameter2 1 0" in sent
    assert f"ramp [Channel1] {194.0 + 82.0 * 0.45} 276 {EQ1} parameter3 1 0" in sent
    assert any("outgoing deck stops at beat 276.0" in line for line in logs)
    assert any("mids then highs come down" in line for line in logs)


def test_late_blend_early_stop_without_eq_out(mixxx):
    sched(mixxx, track(style="late", cut_frame=44100 * 300))
    assert mixxx.sends("at [Channel1] 600.000 set [Channel1] play 0")
    assert not mixxx.sends(" parameter2 1 0")


def test_a_replayed_blend_is_handed_to_the_replay(mixxx, monkeypatch):
    seen = []
    monkeypatch.setattr(rs, "schedule_replay", lambda *a: seen.append(a))
    t = track(replay="sets/live/x.jsonl")
    rs.schedule_transition(1, t, 2, None)
    assert seen == [(1, t, 2, None)]
    assert mixxx.sent == []


def test_a_track_with_two_grids_is_regridded_before_its_blend_is_scheduled(mixxx, monkeypatch):
    calls = []
    monkeypatch.setattr(rs, "regrid_live", lambda n, name: calls.append((n, name, len(mixxx.sent))))
    sched(mixxx, track(grid_preset_live="end"))
    assert calls == [(1, "end", 0)]


def test_blend_points_given_as_frames_use_the_decks_own_beat_count(mixxx):
    mixxx.controls[(D1, "cue_point")] = 44100 * 2   # cue one beat... two beats of samples in
    sent = sched(mixxx, track(mix_frame=44100 * 10))   # 10 beats of frames at 120 BPM / 44.1k: 22050 frames per beat
    # frames*2 = 882000 samples; minus cue 88200; /44100 samples per beat = 18 beats
    assert "at [Channel1] 17.75 set [Channel2] play 1" in sent


# --- replaying a hand mix

def hand_row(t, beat, d1, d2):
    return {"t": t, "state": {"decks": {"1": dict(d1, beat=beat), "2": d2}}}


def hd(play, vol, eq=(1, 1, 1)):
    return {"play": play, "vol": vol, "eq": list(eq)}


@pytest.fixture
def replay_world(mixxx, presets, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    (repo / "sets" / "logs").mkdir(parents=True)
    (repo / "sets" / "live").mkdir()
    rows = [
        {"header": True},
        hand_row(0.0, 0.0, hd(1, 1.0), hd(0, 0.0, (0, 0.4, 1))),
        hand_row(1.0, 16.0, hd(1, 1.0), hd(0, 0.0, (0, 0.4, 1))),
        hand_row(2.0, 32.0, hd(1, 1.0), hd(1, 0.0, (0, 0.4, 1))),             # deck 2 starts here
        hand_row(3.0, 40.0, hd(1, 0.9), hd(1, 0.5, (0.6, 0.4, 1.2))),         # moves; 1.2 is clamped to 1
        hand_row(4.0, 48.0, hd(1, 0.9), hd(1, 0.503, (0.6, 0.4, 1.2))),       # under the threshold: nothing
        hand_row(5.0, 64.0, hd(0, 0.0, (0.2, 1, 1)), hd(1, 1.0, (1, 1, 1))),  # deck 1 stops: last row used
        hand_row(6.0, 80.0, hd(1, 1.0), hd(1, 1.0)),                          # never reached
    ]
    write_jsonl(repo / "sets" / "live" / "mix.jsonl", rows)
    monkeypatch.setattr(rs, "REPO", repo)
    regrids = []
    monkeypatch.setattr(rs, "regrid_live", lambda n, name: regrids.append((n, name)))
    return repo, regrids


def live_beat(out_t, preset, log_beat):
    fpb = out_t["samplerate"] * 60 / preset["bpm"]
    log_cue = preset["first_beat_frame"] + (out_t["replay_bar"] - 1) * 4 * fpb
    return ((log_cue + log_beat * fpb) * 2) / (44100 * 2 * 60 / 120.0)


def test_replay_turns_the_hand_mix_into_beat_scheduled_moves(replay_world, mixxx, logs, presets):
    repo, regrids = replay_world
    out_t = track(replay="sets/live/mix.jsonl", grid_preset_live="p", replay_bar=21, samplerate=44100)
    rs.schedule_replay(1, out_t, 2, None)
    assert regrids == [(1, "p")]
    assert mixxx.sent[:4] == [f"set {EQ2} parameter2 0.400", "set [Channel2] volume 0",
                              "set [Channel1] sync_leader 1", "set [Channel2] sync_enabled 1"]
    lines = (repo / "sets" / "logs" / "replay-schedule.txt").read_text(encoding="utf-8").splitlines()
    def b(log_beat):
        return f"{live_beat(out_t, presets, log_beat):.3f}"

    assert lines == [
        f"at [Channel1] {live_beat(out_t, presets, 32) - 0.25:.3f} set [Channel2] play 1",
        f"at [Channel1] {b(40)} set [Channel1] volume 0.900",
        f"at [Channel1] {b(40)} set [Channel2] volume 0.500",
        f"at [Channel1] {b(40)} set {EQ2} parameter1 0.600",
        f"at [Channel1] {b(40)} set {EQ2} parameter3 1.000",
        f"at [Channel1] {b(64)} set [Channel1] volume 0.000",
        f"at [Channel1] {b(64)} set {EQ1} parameter1 0.200",
        f"at [Channel1] {b(64)} set [Channel2] volume 1.000",
        f"at [Channel1] {b(64)} set {EQ2} parameter1 1.000",
        f"at [Channel1] {b(64)} set {EQ2} parameter2 1.000",
        f"at [Channel1] {b(64)} set {EQ2} parameter3 1.000",   # 1.2 was the stored value, so 1 counts as a move
    ]
    assert mixxx.sends("plan ")
    assert "hand mix replay" in logs[0] and f"{len(lines)} control moves" in logs[0]


# --- main

def on(*playing, durations=(300.0, 400.0)):
    """A state with the given decks playing; deck 1 holds durations[0], deck 2 durations[1]."""
    return {"decks": {str(n): {"play": int(n in playing), "dur": durations[n - 1]} for n in (1, 2)}}


class Floor:
    """Everything main() talks to is replaced by a recorder, so main's own control flow is what gets tested.

    wait_until answers like a real set would: after a start is requested the incoming deck plays, and
    after a stop is requested the outgoing deck has stopped (unless `miss_start` says the scheduled
    start never happened).
    """

    def __init__(self, monkeypatch, initial, miss_start=False):
        self.events = []
        self.initial = initial
        self.miss_start = miss_start
        self.recording_status = 2
        monkeypatch.setattr(rs, "state", lambda: self.initial)
        monkeypatch.setattr(rs, "send", lambda *c: self.events.append(("send", *c)))
        monkeypatch.setattr(rs, "get", lambda g, k: self.recording_status)
        monkeypatch.setattr(rs, "prepare", lambda n, t: self.events.append(("prepare", n, t["title"])))
        monkeypatch.setattr(rs, "schedule_transition", lambda o, t, i, nt: self.events.append(("schedule", o, t["title"], i)))
        monkeypatch.setattr(rs, "load_next", lambda n, t, i: self.events.append(("load", n, i)))
        monkeypatch.setattr(rs, "set_recording", lambda on: self.events.append(("recording", on)))
        monkeypatch.setattr(rs, "wait_until", self.wait_until)

    def wait_until(self, predicate, description, timeout=None, interval=None):
        self.events.append(("wait", description))
        if description.endswith("to start"):
            n = int(description.split()[1])
            world = on(n, 3 - n)
        elif description.endswith("to stop"):
            n = int(description.split()[1])
            world = on() if self.miss_start else on(3 - n)
        else:
            world = on()
        assert predicate(world), description
        return world

    def of(self, kind):
        return [e for e in self.events if e[0] == kind]


def plan_file(tmp_path, tracks):
    path = tmp_path / "plan.json"
    write_json(path, tracks)
    return str(path)


def abc(**extra):
    return [track(title="A", duration=300.0, set_bpm=140.0, **extra), track(title="B", duration=400.0),
            track(title="C", duration=500.0)]


def test_main_runs_a_whole_set(monkeypatch, tmp_path, logs):
    floor = Floor(monkeypatch, on(1))
    rs.main(plan_file(tmp_path, abc()))
    assert floor.events[0] == ("send", "set [Master] crossfader 0", "set [Channel1] orientation 1", "set [Channel2] orientation 1")
    assert [e for e in floor.of("prepare")] == [("prepare", 1, "A"), ("prepare", 2, "B"), ("prepare", 1, "C")]
    assert ("send", "set [Channel1] bpm 140.0") in floor.events
    # recording is on before the first track starts, and the opener comes in at full volume
    assert floor.events.index(("recording", True)) < floor.events.index(
        ("send", "set [Channel1] volume 1", "set [Channel1] play 1"))
    assert floor.of("schedule") == [("schedule", 1, "A", 2), ("schedule", 2, "B", 1)]
    assert floor.of("load") == [("load", 1, 2)]
    assert floor.of("recording") == [("recording", True), ("recording", False)]
    assert floor.events[-1] == ("send", "set [Channel1] orientation 0", "set [Channel2] orientation 2")
    assert logs[:3] == ["recording started", "1. A", "2. B"] and "3. C" in logs
    assert "last track playing; waiting for it to end" in logs
    assert logs[-1] == "recording stopped. Set complete."
    assert "  incoming deck not playing at end of blend, starting it now" not in logs


def test_main_starts_the_incoming_deck_if_the_scheduled_start_was_missed(monkeypatch, tmp_path, logs):
    floor = Floor(monkeypatch, on(1), miss_start=True)
    rs.main(plan_file(tmp_path, abc()))
    assert logs.count("  incoming deck not playing at end of blend, starting it now") == 2
    assert ("send", "set [Channel2] play 1", "set [Channel2] volume 1") in floor.events
    assert ("send", "set [Channel1] play 1", "set [Channel1] volume 1") in floor.events


@pytest.mark.parametrize("state", [on(1, durations=(999.0, 400.0)), on(1, durations=(300.0, 999.0))])
def test_main_refuses_to_start_with_the_wrong_tracks_loaded(monkeypatch, tmp_path, state):
    Floor(monkeypatch, state)
    with pytest.raises(SystemExit, match="Expected tracks 1 and 2 on decks 1 and 2"):
        rs.main(plan_file(tmp_path, abc()))


def test_main_warns_when_recording_did_not_start(monkeypatch, tmp_path, logs):
    floor = Floor(monkeypatch, on(1))
    floor.recording_status = 0
    rs.main(plan_file(tmp_path, abc()[:2]))
    assert "  warning: recording did not report status 2" in logs


def test_main_without_recording(monkeypatch, tmp_path, logs):
    monkeypatch.setattr(rs, "record", False)
    floor = Floor(monkeypatch, on(1))
    floor.recording_status = 0
    rs.main(plan_file(tmp_path, abc()))
    assert "not recording" in logs and logs[-1] == "Set complete."
    assert "  warning: recording did not report status 2" not in logs


def test_main_fades_out_an_audition_at_its_stop_bar(monkeypatch, tmp_path, logs):
    tracks = abc()[:2]
    tracks[1].update(start_bar=5, stop_bar=21)
    floor = Floor(monkeypatch, on(1))
    rs.main(plan_file(tmp_path, tracks))
    end = (21 - 5) * 4
    assert ("send", f"ramp [Channel2] {end - 16} {end} [Channel2] volume 1 0",
            f"at [Channel2] {end} set [Channel2] play 0") in floor.events
    assert "last track playing; fading out at bar 21" in logs


def test_main_resumes_with_the_next_track_already_parked(monkeypatch, tmp_path, logs):
    # B (deck 2) is playing; C is already on deck 1, stopped
    floor = Floor(monkeypatch, on(2, durations=(500.0, 400.0)))
    rs.main(plan_file(tmp_path, abc()), resume=1)
    assert "resuming at track 2 on deck 2" in logs
    assert floor.of("load") == []
    assert floor.of("prepare") == [("prepare", 1, "C"), ]
    assert floor.of("schedule") == [("schedule", 2, "B", 1)]
    assert floor.of("recording") == [("recording", False)]


def test_main_resume_loads_the_next_track_when_it_is_not_on_the_idle_deck(monkeypatch, tmp_path):
    floor = Floor(monkeypatch, on(2, durations=(999.0, 400.0)))
    rs.main(plan_file(tmp_path, abc()), resume=1)
    assert floor.of("load")[0] == ("load", 1, 2)
    assert floor.events.index(("load", 1, 2)) < floor.events.index(("prepare", 1, "C"))


def test_main_resume_leaves_an_already_playing_idle_deck_alone(monkeypatch, tmp_path):
    # both decks are playing (a blend is under way): nothing is loaded or parked up front
    floor = Floor(monkeypatch, on(1, 2, durations=(500.0, 400.0)))
    rs.main(plan_file(tmp_path, abc()), resume=1)
    assert floor.of("load") == [] and floor.of("prepare") == []
    assert floor.of("schedule") == [("schedule", 2, "B", 1)]


def test_main_resume_at_the_last_track_just_plays_it_out(monkeypatch, tmp_path, logs):
    floor = Floor(monkeypatch, on(1, durations=(500.0, 400.0)))
    rs.main(plan_file(tmp_path, abc()), resume=2)
    assert floor.of("prepare") == [] and floor.of("schedule") == []
    assert "resuming at track 3 on deck 1" in logs


# --- the script

def test_script_reports_a_failure_in_the_log_and_reraises(mixxx, run_script, tmp_path, capsys):
    log = tmp_path / "set.log"
    with pytest.raises(FileNotFoundError):
        run_script(rs, str(tmp_path / "missing.json"), str(log))
    assert "FAILED: FileNotFoundError" in log.read_text(encoding="utf-8")


def test_script_options(mixxx, run_script, tmp_path, capsys):
    tracks = [track(title="Only", duration=300.0)]
    mixxx.world = [on(1), on()]
    run_script(rs, "--no-record", "--resume", "0", plan_file(tmp_path, tracks))
    out = capsys.readouterr().out
    assert "resuming at track 1 on deck 1" in out
    assert out.strip().endswith("Set complete.")
    assert mixxx.sends("orientation 0") and mixxx.sends("orientation 2")


def test_script_without_options_needs_the_first_two_tracks_on_the_decks(mixxx, run_script, tmp_path):
    mixxx.world = [on(1, durations=(1.0, 2.0))]
    with pytest.raises(SystemExit, match="Expected tracks 1 and 2"):
        run_script(rs, plan_file(tmp_path, abc()))


def test_apply_grid_stops_walking_the_bpm_after_forty_tries(mixxx, presets, logs):
    mixxx.controls[(D1, "file_bpm")] = 100.0
    mixxx.stuck = True
    rs.apply_grid(1, "p")
    assert len(mixxx.sends("beats_adjust_faster 1")) == 40 * 20


def test_replay_without_a_stop_schedules_every_row(replay_world, mixxx, logs, presets):
    repo, _ = replay_world
    path = repo / "sets" / "live" / "mix.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(rows[:5]), encoding="utf-8")      # the log ends while deck 1 is still playing
    rs.schedule_replay(1, track(replay="sets/live/mix.jsonl", grid_preset_live="p", replay_bar=21, samplerate=44100), 2, None)
    assert "hand mix replay" in logs[0]

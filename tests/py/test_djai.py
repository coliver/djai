
import djai
import pytest
from fakewinmm import HEADER, reply_chunks, reply_message


def test_encode_wraps_text_in_a_sysex_command():
    assert djai.encode("ping") == HEADER + b"Cping\xF7"


def test_encode_replaces_non_ascii():
    assert djai.encode("é") == HEADER + b"C?\xF7"


def test_encode_all_packs_commands_into_one_message_when_they_fit():
    assert djai.encode_all("a; b ;; c") == [djai.encode("a; b; c")]


def test_encode_all_ignores_empty_input():
    assert djai.encode_all(" ; ;") == []


def test_encode_all_splits_at_the_sysex_limit():
    commands = [f"set [Channel1] volume 0.{i:03d}" for i in range(20)]
    messages = djai.encode_all("; ".join(commands))
    assert len(messages) > 1
    assert all(len(m) <= djai.MAX_SYSEX for m in messages)
    texts = [m[len(HEADER) + 1:-1].decode() for m in messages]
    assert "; ".join(texts) == "; ".join(commands)


def test_encode_all_fills_a_message_to_exactly_the_limit_and_no_further():
    room = djai.MAX_SYSEX - len(djai.encode(""))
    exact = "x" * room
    assert djai.encode_all(exact) == [djai.encode(exact)]
    assert len(djai.encode(exact)) == djai.MAX_SYSEX
    # one byte over: the second command starts a new message
    first = "a" * (room - 3)
    messages = djai.encode_all(f"{first}; bc")
    assert [m[len(HEADER) + 1:-1].decode() for m in messages] == [first, "bc"]


def test_encode_all_rejects_a_single_command_that_is_too_long():
    with pytest.raises(SystemExit, match="too long"):
        djai.encode_all("x" * 300)


def test_encode_all_rejects_a_too_long_command_after_a_short_one():
    with pytest.raises(SystemExit, match="too long"):
        djai.encode_all("ok; " + "y" * 300)


def test_check_passes_zero_and_exits_otherwise():
    djai.check(0, "thing")
    with pytest.raises(SystemExit, match="thing failed with MMRESULT 7"):
        djai.check(7, "thing")


def test_find_port_matches_exact_and_numbered_names():
    assert djai.find_port(["Other", "DJAI"], "output") == 1
    assert djai.find_port(["DJAI 1"], "input") == 0


def test_find_port_reports_what_it_found():
    with pytest.raises(SystemExit, match=r"No MIDI input port named \"DJAI\".*\['Other', 'DJAIX'\]"):
        djai.find_port(["Other", "DJAIX"], "input")


def test_port_lists(winmm):
    winmm.in_names = ["A", "DJAI"]
    winmm.out_names = ["B"]
    assert djai.in_ports() == ["A", "DJAI"]
    assert djai.out_ports() == ["B"]


def test_port_names_skips_devices_whose_caps_fail(winmm):
    winmm.fail["midiOutGetDevCapsW"] = 1
    assert djai.out_ports() == []


def test_send_writes_each_message(winmm):
    messages = djai.encode_all("a; b")
    djai.send(messages + [djai.encode("c")])
    assert winmm.sent == messages + [djai.encode("c")]
    assert "midiOutClose" in winmm.calls


def test_send_waits_for_the_driver_to_finish(winmm, clock):
    winmm.slow_done = True
    djai.send([djai.encode("a")])
    assert clock.sleeps == [0.001]


def test_send_closes_the_port_when_a_step_fails(winmm):
    winmm.fail["midiOutLongMsg"] = 5
    with pytest.raises(SystemExit, match="midiOutLongMsg failed with MMRESULT 5"):
        djai.send([djai.encode("a")])
    assert winmm.calls[-1] == "midiOutClose"


def test_send_fails_when_the_port_is_missing(winmm):
    winmm.out_names = ["Other"]
    with pytest.raises(SystemExit, match="No MIDI output port"):
        djai.send([djai.encode("a")])


def test_query_returns_the_first_reply(winmm):
    winmm.responder = lambda text: ['{"value": 1}'] if text == "get a b" else []
    assert djai.query("get a b") == '{"value": 1}'
    assert winmm.sent == [djai.encode("get a b")]
    assert winmm.calls.count("midiInAddBuffer") > djai.BUFFER_COUNT - 1
    assert winmm.calls[-1] == "midiInClose"


def test_query_joins_chunked_replies(winmm):
    winmm.responder = lambda text: reply_chunks("0123456789abcdefghijXYZ", size=10)
    assert djai.query("state") == "0123456789abcdefghijXYZ"


def test_query_skips_the_hello_banner_and_unrelated_traffic(winmm):
    winmm.responder = lambda text: [
        reply_message("hello"),
        HEADER + b"Cecho\xF7",           # our own command echoed back by loopMIDI
        b"\xF0\x01\xF7",                  # somebody else's SysEx
        reply_message("real"),
    ]
    assert djai.query("state") == "real"


def test_query_times_out_with_none(winmm, clock):
    start = clock.now
    assert djai.query("state", timeout=0.5) is None
    assert 0.5 <= clock.now - start < 0.6


def test_query_cleans_up_when_opening_fails(winmm):
    winmm.fail["midiInStart"] = 3
    with pytest.raises(SystemExit, match="midiInStart"):
        djai.query("state")
    assert winmm.calls[-1] == "midiInClose"


def test_main_rejects_unknown_commands():
    with pytest.raises(SystemExit) as e:
        djai.main(["djai.py", "bogus"])
    assert "djai.py send" in str(e.value)
    with pytest.raises(SystemExit):
        djai.main(["djai.py"])


def test_main_ports_lists_both_directions(winmm, capsys):
    djai.main(["djai.py", "ports"])
    out = capsys.readouterr().out
    assert "in:  ['DJAI']" in out and "out: ['DJAI']" in out


def test_main_send_joins_arguments(winmm):
    djai.main(["djai.py", "send", "set", "a", "b", "1"])
    assert winmm.sent == [djai.encode("set a b 1")]


def test_main_query_prints_the_reply(winmm, capsys):
    winmm.responder = lambda text: ["pong"]
    djai.main(["djai.py", "query", "ping"])
    assert capsys.readouterr().out == "pong\n"


def test_main_query_without_a_reply_explains(winmm):
    with pytest.raises(SystemExit, match="No reply from Mixxx"):
        djai.main(["djai.py", "query", "ping"])


def test_main_plan_sends_each_line_but_comments_and_blanks(winmm, tmp_path, capsys):
    plan = tmp_path / "plan.txt"
    plan.write_text("# header\n\nset a b 1\n   # indented comment\nat x 4 set c d 0; set e f 1\n", encoding="utf-8")
    djai.main(["djai.py", "plan", str(plan)])
    assert winmm.sent == [djai.encode("set a b 1"), djai.encode("at x 4 set c d 0; set e f 1")]
    assert capsys.readouterr().out == "sent 2 commands\n"


def test_runs_as_a_script(winmm, run_script, capsys):
    run_script(djai, "ports")
    assert "out:" in capsys.readouterr().out

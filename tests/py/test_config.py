import config
import pytest


def test_get_reads_a_required_setting():
    assert config.get("music_dir") == "/music"
    assert config.get("mixxx_exe") == "C:\\mixxx.exe"


def test_an_optional_setting_has_a_default(djai_config):
    djai_config.write_text(djai_config.read_text(encoding="utf-8").replace('speaker_match = "Audigy"\n', ""), encoding="utf-8")
    assert config.get("speaker_match") == ""


def test_music_path_is_relative_to_music_dir():
    assert config.music_path("Acid/a.mp3") == "/music/Acid/a.mp3"


def test_a_missing_file_says_how_to_make_one(monkeypatch, tmp_path):
    monkeypatch.setenv("DJAI_CONFIG", str(tmp_path / "nope.toml"))
    with pytest.raises(SystemExit, match="djai.example.toml"):
        config.load()


def test_a_missing_setting_is_named(djai_config):
    djai_config.write_text('music_dir = "/music"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="mixxx_user_dir, mixxx_exe, recordings_dir"):
        config.load()


def test_main_prints_one_setting(capsys):
    config.main(["config.py", "recordings_dir"])
    assert capsys.readouterr().out == "/rec\n"


def test_main_rejects_an_unknown_key():
    with pytest.raises(SystemExit, match="usage"):
        config.main(["config.py", "nope"])


def test_running_the_file_as_a_script_prints_the_setting(run_script, capsys):
    run_script(config, "music_dir")
    assert capsys.readouterr().out == "/music\n"

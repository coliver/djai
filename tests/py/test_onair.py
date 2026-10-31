import ctypes
import wave

import onair
import pytest


class FakeWaveOut:
    """winmm's waveOut calls, enough for onair.play: devices by name, and a header that is done once written."""

    def __init__(self, names, open_result=0):
        self.names = names
        self.open_result = open_result
        self.opened = None
        self.written = b""
        self.closed = False

    def waveOutGetNumDevs(self):
        return len(self.names)

    def waveOutGetDevCapsW(self, i, caps, size):
        caps._obj.szPname = self.names[i]
        return 0

    def waveOutOpen(self, handle, device, fmt, callback, instance, flags):
        f = fmt._obj
        self.opened = (device, f.nChannels, f.nSamplesPerSec, f.wBitsPerSample, f.nBlockAlign, f.nAvgBytesPerSec)
        return self.open_result

    def waveOutPrepareHeader(self, handle, header, size):
        return 0

    def waveOutWrite(self, handle, header, size):
        h = header._obj
        self.written = ctypes.string_at(h.lpData, h.dwBufferLength)
        h.dwFlags |= onair.WHDR_DONE
        return 0

    def waveOutUnprepareHeader(self, handle, header, size):
        return 0

    def waveOutClose(self, handle):
        self.closed = True
        return 0


def make_wav(path, seconds=0.5, rate=48000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * 2 * int(rate * seconds))
    return path


def test_plays_into_the_cable(monkeypatch, tmp_path):
    fake = FakeWaveOut(["Speakers (USB)", "CABLE Input (VB-Audio Virtual C", "CABLE In 16ch (VB-Audio Virtual"])
    monkeypatch.setattr(onair, "winmm", fake)
    seconds = onair.play(str(make_wav(tmp_path / "a.wav")))
    assert seconds == pytest.approx(0.5)
    assert fake.opened == (1, 2, 48000, 16, 4, 192000)
    assert len(fake.written) == 48000 * 4 // 2 and fake.closed


def test_names_another_output(monkeypatch, tmp_path):
    fake = FakeWaveOut(["Speakers (USB)", "Line (2- DDJ-FLX4)"])
    monkeypatch.setattr(onair, "winmm", fake)
    onair.play(str(make_wav(tmp_path / "a.wav")), "ddj")
    assert fake.opened[0] == 1


def test_no_cable(monkeypatch, tmp_path):
    monkeypatch.setattr(onair, "winmm", FakeWaveOut(["Speakers (USB)"]))
    with pytest.raises(SystemExit, match="no audio output matching 'CABLE Input'"):
        onair.play(str(make_wav(tmp_path / "a.wav")))


def test_busy_cable_closes_nothing_it_did_not_open(monkeypatch, tmp_path):
    fake = FakeWaveOut(["CABLE Input (VB-Audio Virtual C"], open_result=4)
    monkeypatch.setattr(onair, "winmm", fake)
    with pytest.raises(SystemExit, match="waveOutOpen failed with MMRESULT 4"):
        onair.play(str(make_wav(tmp_path / "a.wav")))
    assert not fake.closed


def test_cli(run_script, monkeypatch, tmp_path, capsys):
    import ctypes as c
    fake = FakeWaveOut(["CABLE Input (VB-Audio Virtual C"])
    monkeypatch.setattr(c, "WinDLL", lambda name: fake)
    run_script(onair, str(make_wav(tmp_path / "a.wav")))
    assert "played 0.5 s" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="onair.py <wav file>"):
        run_script(onair)

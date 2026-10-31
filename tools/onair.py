"""Play a wav file into the VB-Audio Virtual Cable, which Mixxx takes as its microphone, so it goes out on the stream.

    onair.py <wav file> [output name part]

Runs under Windows Python (no third-party packages; talks to winmm via ctypes), like djai.py. Plays a PCM wav
to the first winmm output whose name contains the given part ("CABLE Input" by default) and returns when it has
finished. tools/onair.sh wraps it: speech synthesis, Mixxx talkover on, play, talkover off.
"""
import ctypes
import sys
import time
import wave
from ctypes import wintypes

WHDR_DONE = 0x01
WAVE_FORMAT_PCM = 1
CALLBACK_NULL = 0

winmm = ctypes.WinDLL("winmm")


class WAVEOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
        ("dwSupport", wintypes.DWORD),
    ]


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    pass


WAVEHDR._fields_ = [
    ("lpData", ctypes.c_void_p),
    ("dwBufferLength", wintypes.DWORD),
    ("dwBytesRecorded", wintypes.DWORD),
    ("dwUser", ctypes.c_size_t),
    ("dwFlags", wintypes.DWORD),
    ("dwLoops", wintypes.DWORD),
    ("lpNext", ctypes.POINTER(WAVEHDR)),
    ("reserved", ctypes.c_size_t),
]


def check(result, what):
    if result != 0:
        raise SystemExit(f"{what} failed with MMRESULT {result}")


def outputs():
    names = []
    for i in range(winmm.waveOutGetNumDevs()):
        caps = WAVEOUTCAPSW()
        if winmm.waveOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.append(caps.szPname)
    return names


def find_output(part):
    names = outputs()
    for i, name in enumerate(names):
        if part.lower() in name.lower():
            return i
    raise SystemExit(f"no audio output matching {part!r}; outputs: {names}")


def play(path, part="CABLE Input"):
    with wave.open(path, "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        data = w.readframes(w.getnframes())
    fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, channels, rate, rate * channels * width, channels * width, width * 8, 0)
    handle = wintypes.HANDLE()
    device = find_output(part)
    check(winmm.waveOutOpen(ctypes.byref(handle), device, ctypes.byref(fmt), 0, 0, CALLBACK_NULL), "waveOutOpen")
    buffer = ctypes.create_string_buffer(data, len(data))
    header = WAVEHDR(ctypes.cast(buffer, ctypes.c_void_p), len(data), 0, 0, 0, 0, None, 0)
    try:
        check(winmm.waveOutPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header)), "waveOutPrepareHeader")
        check(winmm.waveOutWrite(handle, ctypes.byref(header), ctypes.sizeof(header)), "waveOutWrite")
        while not header.dwFlags & WHDR_DONE:
            time.sleep(0.05)
        winmm.waveOutUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
    finally:
        winmm.waveOutClose(handle)
    return len(data) / fmt.nAvgBytesPerSec


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    print(f"played {play(*sys.argv[1:3]):.1f} s")

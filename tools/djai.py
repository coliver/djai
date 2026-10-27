"""Send commands to Mixxx's DJAI Bridge mapping over the "DJAI" loopMIDI port.

Runs under Windows Python (no third-party packages; talks to winmm via ctypes).

    djai.py send "set [Channel1] volume 0.5; set [Channel2] play 1"
    djai.py query state          # send, then print the first reply
    djai.py plan cues.txt        # send each non-blank, non-# line
    djai.py ports                # list MIDI ports
"""

import ctypes
import sys
import time
from ctypes import wintypes

PORT_NAME = "DJAI"
HEADER = bytes([0xF0, 0x7D, 0x44, 0x4A])  # F0 7D 'D' 'J'
MHDR_DONE = 0x01
BUFFER_SIZE = 8192
BUFFER_COUNT = 32

winmm = ctypes.WinDLL("winmm")


class MIDIHDR(ctypes.Structure):
    pass


MIDIHDR._fields_ = [
    ("lpData", ctypes.c_void_p),
    ("dwBufferLength", wintypes.DWORD),
    ("dwBytesRecorded", wintypes.DWORD),
    ("dwUser", ctypes.c_size_t),
    ("dwFlags", wintypes.DWORD),
    ("lpNext", ctypes.POINTER(MIDIHDR)),
    ("reserved", ctypes.c_size_t),
    ("dwOffset", wintypes.DWORD),
    ("dwReserved", ctypes.c_size_t * 8),
]


class MIDIINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwSupport", wintypes.DWORD),
    ]


class MIDIOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("wTechnology", wintypes.WORD),
        ("wVoices", wintypes.WORD),
        ("wNotes", wintypes.WORD),
        ("wChannelMask", wintypes.WORD),
        ("dwSupport", wintypes.DWORD),
    ]


def check(result, what):
    if result != 0:
        raise SystemExit(f"{what} failed with MMRESULT {result}")


def port_names(get_num, get_caps, caps_type):
    names = []
    for i in range(get_num()):
        caps = caps_type()
        if get_caps(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.append(caps.szPname)
    return names


def find_port(names, kind):
    for i, name in enumerate(names):
        if name == PORT_NAME or name.startswith(PORT_NAME + " "):
            return i
    raise SystemExit(f'No MIDI {kind} port named "{PORT_NAME}". Is loopMIDI running with that port? Found: {names}')


def in_ports():
    return port_names(winmm.midiInGetNumDevs, winmm.midiInGetDevCapsW, MIDIINCAPSW)


def out_ports():
    return port_names(winmm.midiOutGetNumDevs, winmm.midiOutGetDevCapsW, MIDIOUTCAPSW)


MAX_SYSEX = 256  # loopMIDI's default SysEx limit


def encode(text):
    return HEADER + b"C" + text.encode("ascii", "replace") + b"\xF7"


def encode_all(text):
    """Encode ';'-separated commands into as few messages as fit loopMIDI's limit."""
    messages, current = [], ""
    for command in (c.strip() for c in text.split(";")):
        if not command:
            continue
        candidate = f"{current}; {command}" if current else command
        if len(encode(candidate)) > MAX_SYSEX and current:
            messages.append(encode(current))
            candidate = command
        if len(encode(candidate)) > MAX_SYSEX:
            raise SystemExit(f"Command too long for one SysEx message: {command}")
        current = candidate
    if current:
        messages.append(encode(current))
    return messages


def send(messages):
    handle = ctypes.c_void_p()
    check(winmm.midiOutOpen(ctypes.byref(handle), find_port(out_ports(), "output"), None, None, 0), "midiOutOpen")
    try:
        for message in messages:
            data = ctypes.create_string_buffer(message, len(message))
            header = MIDIHDR(lpData=ctypes.cast(data, ctypes.c_void_p), dwBufferLength=len(message))
            check(winmm.midiOutPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header)), "midiOutPrepareHeader")
            check(winmm.midiOutLongMsg(handle, ctypes.byref(header), ctypes.sizeof(header)), "midiOutLongMsg")
            while not header.dwFlags & MHDR_DONE:
                time.sleep(0.001)
            winmm.midiOutUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
    finally:
        winmm.midiOutClose(handle)


def query(text, timeout=2.0):
    """Send text and return the first reply payload from Mixxx, or None."""
    handle = ctypes.c_void_p()
    check(winmm.midiInOpen(ctypes.byref(handle), find_port(in_ports(), "input"), None, None, 0), "midiInOpen")
    buffers, headers = [], []
    try:
        # One buffer completes per SysEx chunk. A long reply is a burst of chunks, so
        # have plenty of buffers and read them in the order Windows fills them.
        for _ in range(BUFFER_COUNT):
            data = ctypes.create_string_buffer(BUFFER_SIZE)
            header = MIDIHDR(lpData=ctypes.cast(data, ctypes.c_void_p), dwBufferLength=BUFFER_SIZE)
            check(winmm.midiInPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header)), "midiInPrepareHeader")
            check(winmm.midiInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(header)), "midiInAddBuffer")
            buffers.append(data)
            headers.append(header)
        check(winmm.midiInStart(handle), "midiInStart")

        send(encode_all(text))

        deadline = time.monotonic() + timeout
        reply, next_buffer = "", 0
        while time.monotonic() < deadline:
            header, data = headers[next_buffer], buffers[next_buffer]
            if not header.dwFlags & MHDR_DONE:
                time.sleep(0.002)
                continue
            message = data.raw[:header.dwBytesRecorded]
            header.dwFlags = 0
            header.dwBytesRecorded = 0
            winmm.midiInPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
            winmm.midiInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(header))
            next_buffer = (next_buffer + 1) % BUFFER_COUNT
            start = message.find(HEADER)
            if start < 0 or message[start + len(HEADER):start + len(HEADER) + 1] != b"R":
                continue
            # Replies arrive in chunks: '+' means more follows, '.' marks the last.
            body = message[start + len(HEADER) + 1:].rstrip(b"\xF7")
            reply += body[1:].decode("ascii", "replace")
            if body[:1] == b".":
                if reply != "hello":
                    return reply
                reply = ""
        return None
    finally:
        winmm.midiInStop(handle)
        winmm.midiInReset(handle)
        for header in headers:
            winmm.midiInUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
        winmm.midiInClose(handle)


def main(argv):
    if len(argv) < 2 or argv[1] not in ("send", "query", "plan", "ports"):
        raise SystemExit(__doc__)
    command = argv[1]
    if command == "ports":
        print("in: ", in_ports())
        print("out:", out_ports())
    elif command == "send":
        send(encode_all(" ".join(argv[2:])))
    elif command == "query":
        reply = query(" ".join(argv[2:]))
        if reply is None:
            raise SystemExit("No reply from Mixxx. Is the DJAI Bridge mapping enabled for the DJAI port?")
        print(reply)
    elif command == "plan":
        with open(argv[2], encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
        send([message for line in lines for message in encode_all(line)])
        print(f"sent {len(lines)} commands")


if __name__ == "__main__":
    main(sys.argv)

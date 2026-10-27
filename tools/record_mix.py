"""Record the controls of a live mix (the user mixes by hand) to a JSON-lines file.

Runs under Windows Python, like djai.py, and keeps the DJAI port open so it can poll the bridge's
"state" snapshot many times a second. One line per sample:

    {"t": seconds since start, "clock": "HH:MM:SS.mmm", "state": {...}}

    record_mix.py out.jsonl [--record] [--stop-file PATH] [--hz 12] [--max-seconds N]

  --record      also start and stop Mixxx's audio recording, so the log lines up with the audio
  --stop-file   finish when this file appears (the file is deleted)
Ctrl-C also finishes. The first line is a header with the start time and each deck's track length.
"""
import ctypes
import json
import os
import sys
import time
from datetime import datetime

from djai import BUFFER_COUNT, BUFFER_SIZE, HEADER, MHDR_DONE, MIDIHDR, check, encode_all, find_port, in_ports, out_ports, winmm

FLUSH_EVERY = 50   # samples between flushes, so a crash or Ctrl+C loses at most a couple of seconds


class Link:
    """One open input and one open output on the DJAI port."""

    def __init__(self):
        self.inp = ctypes.c_void_p()
        self.out = ctypes.c_void_p()
        check(winmm.midiInOpen(ctypes.byref(self.inp), find_port(in_ports(), "input"), None, None, 0), "midiInOpen")
        check(winmm.midiOutOpen(ctypes.byref(self.out), find_port(out_ports(), "output"), None, None, 0), "midiOutOpen")
        self.buffers, self.headers, self.next = [], [], 0
        for _ in range(BUFFER_COUNT):
            data = ctypes.create_string_buffer(BUFFER_SIZE)
            header = MIDIHDR(lpData=ctypes.cast(data, ctypes.c_void_p), dwBufferLength=BUFFER_SIZE)
            check(winmm.midiInPrepareHeader(self.inp, ctypes.byref(header), ctypes.sizeof(header)), "prepare")
            check(winmm.midiInAddBuffer(self.inp, ctypes.byref(header), ctypes.sizeof(header)), "addbuffer")
            self.buffers.append(data)
            self.headers.append(header)
        check(winmm.midiInStart(self.inp), "midiInStart")

    def send(self, text):
        for message in encode_all(text):
            data = ctypes.create_string_buffer(message, len(message))
            header = MIDIHDR(lpData=ctypes.cast(data, ctypes.c_void_p), dwBufferLength=len(message))
            winmm.midiOutPrepareHeader(self.out, ctypes.byref(header), ctypes.sizeof(header))
            winmm.midiOutLongMsg(self.out, ctypes.byref(header), ctypes.sizeof(header))
            while not header.dwFlags & MHDR_DONE:
                time.sleep(0.0005)
            winmm.midiOutUnprepareHeader(self.out, ctypes.byref(header), ctypes.sizeof(header))

    def drain(self):
        """Drop anything already received, so the next reply is the one asked for."""
        while self.headers[self.next].dwFlags & MHDR_DONE:
            self._recycle()

    def _recycle(self):
        header, data = self.headers[self.next], self.buffers[self.next]
        message = data.raw[:header.dwBytesRecorded]
        header.dwFlags = 0
        header.dwBytesRecorded = 0
        winmm.midiInPrepareHeader(self.inp, ctypes.byref(header), ctypes.sizeof(header))
        winmm.midiInAddBuffer(self.inp, ctypes.byref(header), ctypes.sizeof(header))
        self.next = (self.next + 1) % BUFFER_COUNT
        return message

    def ask(self, text, timeout=1.0):
        self.drain()
        self.send(text)
        deadline = time.monotonic() + timeout
        reply = ""
        while time.monotonic() < deadline:
            if not self.headers[self.next].dwFlags & MHDR_DONE:
                time.sleep(0.0005)
                continue
            message = self._recycle()
            start = message.find(HEADER)
            if start < 0 or message[start + len(HEADER):start + len(HEADER) + 1] != b"R":
                continue
            body = message[start + len(HEADER) + 1:].rstrip(b"\xF7")
            reply += body[1:].decode("ascii", "replace")
            if body[:1] == b".":
                if reply == "hello":
                    reply = ""
                    continue
                return reply
        return None

    def get(self, group, key):
        reply = self.ask(f"get {group} {key}")
        return json.loads(reply)["value"] if reply else None

    def close(self):
        winmm.midiInStop(self.inp)
        winmm.midiInReset(self.inp)
        for header in self.headers:
            winmm.midiInUnprepareHeader(self.inp, ctypes.byref(header), ctypes.sizeof(header))
        winmm.midiInClose(self.inp)
        winmm.midiOutClose(self.out)


def main(argv):
    args = argv[1:]
    record = "--record" in args
    if record:
        args.remove("--record")

    def option(name, default):
        if name in args:
            i = args.index(name)
            value = args[i + 1]
            del args[i:i + 2]
            return value
        return default

    stop_file = option("--stop-file", None)
    hz = float(option("--hz", 12))
    max_seconds = float(option("--max-seconds", 3600))
    out_path = args[0]

    link = Link()
    started = time.monotonic()
    recording_on = False
    try:
        with open(out_path, "w", encoding="utf-8") as out:
            decks = {n: link.get(f"[Channel{n}]", "track_samples") for n in (1, 2, 3, 4)}
            header = {"header": True, "start": datetime.now().isoformat(timespec="milliseconds"),
                      "track_samples": decks, "hz": hz}
            if record:
                if link.get("[Recording]", "status") != 2:
                    link.send("set [Recording] toggle_recording 1")
                    time.sleep(1.0)
                recording_on = link.get("[Recording]", "status") == 2
                header["recording"] = recording_on
            started = time.monotonic()
            out.write(json.dumps(header) + "\n")
            out.flush()
            period = 1.0 / hz
            samples = 0
            while True:
                loop_start = time.monotonic()
                if stop_file and os.path.exists(stop_file):
                    os.remove(stop_file)
                    break
                if loop_start - started > max_seconds:
                    break
                reply = link.ask("state")
                now = time.monotonic()
                if reply:
                    try:
                        state = json.loads(reply)
                    except ValueError:
                        state = None
                    if state is not None:
                        out.write(json.dumps({"t": round(now - started, 3),
                                              "clock": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                                              "state": state}, separators=(",", ":")) + "\n")
                        samples += 1
                        if samples % FLUSH_EVERY == 0:
                            out.flush()
                time.sleep(max(0.0, period - (time.monotonic() - loop_start)))
            out.write(json.dumps({"footer": True, "samples": samples,
                                  "seconds": round(time.monotonic() - started, 1)}) + "\n")
    except KeyboardInterrupt:
        pass
    finally:
        if recording_on and link.get("[Recording]", "status") == 2:
            link.send("set [Recording] toggle_recording 1")
        link.close()


if __name__ == "__main__":
    main(sys.argv)

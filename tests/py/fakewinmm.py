"""An in-memory stand-in for Windows' winmm MIDI API, so djai.py and record_mix.py run on Linux.

Output: every long message the code sends is kept in `sent`, and a `responder` (text -> list of reply
strings) decides what Mixxx would answer. Replies are written into the input buffers the code handed
to midiInAddBuffer, in the order the code will read them.
"""
import ctypes
from collections import deque

DONE = 0x01
HEADER = bytes([0xF0, 0x7D, 0x44, 0x4A])


def reply_message(text, last=True):
    return HEADER + b"R" + (b"." if last else b"+") + text.encode("ascii") + b"\xF7"


def reply_chunks(text, size=10):
    """A reply split into SysEx chunks the way the bridge sends it: '+' while more follows, '.' last."""
    chunks = [text[i:i + size] for i in range(0, len(text), size)] or [""]
    return [reply_message(c, last=i == len(chunks) - 1) for i, c in enumerate(chunks)]


class FakeWinmm:
    def __init__(self, in_names=("DJAI",), out_names=("DJAI",)):
        self.in_names = list(in_names)
        self.out_names = list(out_names)
        self.sent = []          # raw bytes of each long message sent
        self.queue = deque()    # input headers waiting for data
        self.responder = lambda text: []
        self.fail = {}          # function name -> MMRESULT to return
        self.slow_done = False  # finish output messages on the next sleep instead of at once
        self.calls = []
        self._pending = []

    def finish_pending(self):
        """Complete the output messages held back by slow_done (conftest runs this on each fake sleep)."""
        for header in self._pending:
            header.dwFlags |= DONE
        self._pending.clear()

    def _rc(self, name):
        self.calls.append(name)
        return self.fail.get(name, 0)

    @staticmethod
    def _obj(ref):
        return ref._obj

    # --- device lists
    def midiInGetNumDevs(self):
        return len(self.in_names)

    def midiOutGetNumDevs(self):
        return len(self.out_names)

    def midiInGetDevCapsW(self, index, caps_ref, size):
        if self._rc("midiInGetDevCapsW"):
            return 1
        self._obj(caps_ref).szPname = self.in_names[index]
        return 0

    def midiOutGetDevCapsW(self, index, caps_ref, size):
        if self._rc("midiOutGetDevCapsW"):
            return 1
        self._obj(caps_ref).szPname = self.out_names[index]
        return 0

    # --- output
    def midiOutOpen(self, handle_ref, device, a, b, c):
        self._obj(handle_ref).value = 100 + device
        return self._rc("midiOutOpen")

    def midiOutPrepareHeader(self, handle, header_ref, size):
        return self._rc("midiOutPrepareHeader")

    def midiOutLongMsg(self, handle, header_ref, size):
        result = self._rc("midiOutLongMsg")
        if result:
            return result
        header = self._obj(header_ref)
        data = ctypes.string_at(header.lpData, header.dwBufferLength)
        self.sent.append(data)
        if self.slow_done:
            self._pending.append(header)
        else:
            header.dwFlags |= DONE
        self._answer(data)
        return 0

    def midiOutUnprepareHeader(self, handle, header_ref, size):
        return self._rc("midiOutUnprepareHeader")

    def midiOutClose(self, handle):
        return self._rc("midiOutClose")

    def _answer(self, data):
        if not data.startswith(HEADER + b"C"):
            return
        text = data[len(HEADER) + 1:].rstrip(b"\xF7").decode("ascii")
        for reply in self.responder(text):
            message = reply if isinstance(reply, bytes) else reply_message(reply)
            if not self.queue:
                return
            header = self.queue.popleft()
            ctypes.memmove(header.lpData, message, len(message))
            header.dwBytesRecorded = len(message)
            header.dwFlags |= DONE

    # --- input
    def midiInOpen(self, handle_ref, device, a, b, c):
        self._obj(handle_ref).value = 200 + device
        return self._rc("midiInOpen")

    def midiInPrepareHeader(self, handle, header_ref, size):
        return self._rc("midiInPrepareHeader")

    def midiInAddBuffer(self, handle, header_ref, size):
        result = self._rc("midiInAddBuffer")
        if not result:
            self.queue.append(self._obj(header_ref))
        return result

    def midiInStart(self, handle):
        return self._rc("midiInStart")

    def midiInStop(self, handle):
        return self._rc("midiInStop")

    def midiInReset(self, handle):
        return self._rc("midiInReset")

    def midiInUnprepareHeader(self, handle, header_ref, size):
        return self._rc("midiInUnprepareHeader")

    def midiInClose(self, handle):
        return self._rc("midiInClose")

"""Run a whole set unattended: speakers down, run_set.py with recording, restart from where the music is if it dies.

    overnight.py sets/plan.overnight.json sets/logs/overnight.log [--volume 0.03]

The Windows speaker volume (the outputs matching speaker_match in djai.toml, if set) is lowered for the run and restored at the
end, so the room stays quiet while Mixxx still records at full level. If run_set.py exits early, the
supervisor looks at which plan track is playing and resumes there (run_set.py --resume), up to three times.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import config

HERE = Path(__file__).resolve().parent
DJAI = str(HERE / "djai")


def volume(level):
    match = config.get("speaker_match")
    if not match:
        return
    ps = subprocess.run(["wslpath", "-w", str(HERE / "winvolume.ps1")], capture_output=True, text=True).stdout.strip()
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps,
                    "-Match", match, "-Set", str(level)], capture_output=True)


def playing_index(tracks):
    for _ in range(5):
        try:
            state = json.loads(subprocess.run([DJAI, "query", "state"], capture_output=True, text=True).stdout)
            break
        except ValueError:
            time.sleep(1)
    else:
        return None
    found = []
    for d in state["decks"].values():
        if d["play"]:
            for i, t in enumerate(tracks):
                if abs(d["dur"] - t["duration"]) < 0.5:
                    found.append(i)
    return max(found) if found else None


def supervise(plan, log, level):
    tracks = json.load(open(plan, encoding="utf-8"))

    def note(message):
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} supervisor: {message}\n")

    volume(level)
    try:
        resume = None
        for _ in range(4):
            cmd = [sys.executable, str(HERE / "run_set.py"), plan, log] + (["--resume", str(resume)] if resume is not None else [])
            code = subprocess.run(cmd, cwd=HERE).returncode
            note(f"run_set.py exited with {code}")
            if code == 0:
                break
            subprocess.run([DJAI, "send", "clear"], capture_output=True)
            resume = playing_index(tracks)
            if resume is None or resume >= len(tracks) - 1:
                break
            note(f"resuming at track {resume + 1}")
    finally:
        volume(1.0)
        note("speaker volume restored")


def main(argv):
    args = argv[1:]
    level = 0.03
    if "--volume" in args:
        level = float(args[args.index("--volume") + 1])
        del args[args.index("--volume"):args.index("--volume") + 2]
    supervise(args[0], args[1], level)


if __name__ == "__main__":
    main(sys.argv)

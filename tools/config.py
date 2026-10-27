"""Machine-specific settings: where Mixxx, the music and the recordings live. Read from djai.toml at the repo root.

    config.py <key>          print one setting (the shell scripts use this)

Copy djai.example.toml to djai.toml and fill it in. DJAI_CONFIG=<file> points at a different file.
Track locations in the plans and pools are relative to music_dir, so nothing else in the repo names a path outside it.
"""
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("mixxx_user_dir", "mixxx_exe", "music_dir", "recordings_dir")
OPTIONAL = {"speaker_match": ""}


def load():
    path = Path(os.environ.get("DJAI_CONFIG") or ROOT / "djai.toml")
    if not path.exists():
        raise SystemExit(f"{path} not found. Copy djai.example.toml to djai.toml and fill it in.")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    missing = [key for key in REQUIRED if key not in cfg]
    if missing:
        raise SystemExit(f"{path} is missing: {', '.join(missing)}")
    return cfg


def get(key):
    cfg = load()
    return cfg[key] if key in cfg else OPTIONAL[key]


def music_path(location):
    """The file on disk for a track's `location` (relative to music_dir)."""
    return str(Path(get("music_dir")) / location)


def main(argv):
    if len(argv) != 2 or argv[1] not in (*REQUIRED, *OPTIONAL):
        raise SystemExit(f"usage: config.py <{'|'.join((*REQUIRED, *OPTIONAL))}>")
    print(get(argv[1]))


if __name__ == "__main__":
    main(sys.argv)

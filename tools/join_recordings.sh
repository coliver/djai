#!/usr/bin/env bash
# Join the two recording parts of the 2026-10-25 set into one file.
# Part 1 was cut where deck 1 stalled; part 2 starts when it was resumed.
set -euo pipefail
dir="$(python3 "$(dirname "$0")/config.py" recordings_dir)"
part1="$dir/part1-hard-acid-trance.mp3"
part2="$dir/${1:?usage: join_recordings.sh <part 2 file name>}"
out="$dir/DJAI-hard-acid-trance-2026-10-25.mp3"
# Part 2 opens with a moment of silence before the deck restarted; trim it, and
# the trailing silence from after the last track.
ffmpeg -v error -y -i "$part1" -i "$part2" -filter_complex \
  "[1:a]silenceremove=start_periods=1:start_threshold=-50dB[b];[0:a][b]concat=n=2:v=0:a=1,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse[o]" \
  -map "[o]" -c:a libmp3lame -b:a 320k "$out"
ffprobe -v error -show_entries format=duration -of default=nw=1 "$out"
ls -la "$out"

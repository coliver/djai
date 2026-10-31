#!/usr/bin/env bash
# Say something on the stream. Synthesizes the text, turns Mixxx's mic talkover on (it ducks the music), plays the
# speech into the VB-Audio cable that Mixxx takes as its microphone (onair.py), then turns talkover off.
#   onair.sh "text" [voice]
here="$(cd "$(dirname "$0")" && pwd)"
url="$(python3 "$here/config.py" tts_url)" || exit 1
voice="${2:-jarvis-03}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
code="$(curl -s -m 120 -o "$tmp/raw.wav" -w '%{http_code}' -X POST "$url?stream=1&voice=$voice" --data-binary "$1")"
if [[ "$code" != 200 ]]; then
    echo "speech failed (HTTP ${code:-?}): $(head -c 200 "$tmp/raw.wav")" >&2
    exit 1
fi
# Mixxx runs at 48 kHz; hand winmm plain 16-bit stereo at that rate.
ffmpeg -v error -y -i "$tmp/raw.wav" -ar 48000 -ac 2 -sample_fmt s16 "$tmp/say.wav" || exit 1
"$here/djai" send "set [Microphone] talkover 1"
python.exe "$(wslpath -w "$here/onair.py")" "$(wslpath -w "$tmp/say.wav")"
status=$?
"$here/djai" send "set [Microphone] talkover 0"
exit $status

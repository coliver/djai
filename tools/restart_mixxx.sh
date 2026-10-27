#!/usr/bin/env bash
# Restart Mixxx with the given Windows track paths loaded onto decks 1, 2, ...
# and wait until the DJAI Bridge answers.
here="$(cd "$(dirname "$0")" && pwd)"
mixxx_exe="$(python3 "$here/config.py" mixxx_exe)" || exit 1
taskkill.exe /IM mixxx.exe >/dev/null 2>&1
for _ in $(seq 1 15); do
    sleep 1
    tasklist.exe /FI "IMAGENAME eq mixxx.exe" | grep -q -i mixxx || break
done
if tasklist.exe /FI "IMAGENAME eq mixxx.exe" | grep -q -i mixxx; then
    echo "Mixxx did not close (dialog open?), forcing it" >&2
    taskkill.exe /F /IM mixxx.exe >/dev/null
    sleep 2
fi
[[ "$1" == "--closed" ]] && exit 0
(cd /mnt/c && setsid cmd.exe /c start "" "$mixxx_exe" --log-flush-level debug "$@" </dev/null >/dev/null 2>&1 &)
for i in $(seq 1 60); do
    sleep 1
    "$here/djai" query ping >/dev/null 2>&1 && { echo "Mixxx up after ${i}s"; exit 0; }
done
echo "DJAI Bridge did not answer" >&2
exit 1

# DJAI

An AI that DJs for you.

It mixes hard acid trance.
Long, smooth blends. No sudden cuts.

It runs on:

- Mixxx 2.5.6 (the DJ program, on Windows)
- A Pioneer DDJ-FLX4 controller
- WSL (where you run the code)

Two operating systems, on purpose. See "Why Windows and WSL".

---

## The short version

1. Mixxx plays the music.
2. Python tools plan the set.
3. The tools send commands to Mixxx.
4. Mixxx does the mixing.

That is all. Everything below is detail.

---

## Why Windows and WSL

The split is deliberate. Each side does what only it can.

- **Windows** runs Mixxx, the controller, and the audio.
  It also runs the one script that talks MIDI, `tools/djai.py`,
  through a virtual MIDI port from loopMIDI.
- **WSL** runs everything else: planning, analysis, the tests.
  Those tools only read and write files, and they use ffmpeg.

`tools/djai` joins the two.
It runs `djai.py` under Windows Python and holds the lock.

If you were to move to one side only:
Windows-only would drop the wrapper, but the shell scripts would need porting.
Linux-only would mean replacing loopMIDI and winmm, and re-proving the audio.

---

## What you need

On Windows:

- Mixxx 2.5.6
- A DDJ-FLX4
- [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
  with a port named exactly `DJAI`
- Python 3 on the Windows `PATH`, so WSL can call `python.exe`.
  `djai.py` needs nothing but the standard library.

In WSL:

- Python 3.11 or newer (the config uses `tomllib`)
- `ffmpeg`
- Node, for `npm test` and `npm run lint`

---

## Where things are

Only four folders and one file matter.

- `mixxx/` the Mixxx side
- `tools/` the Python and shell tools
- `sets/` track lists, plans, logs, recordings (your own, kept out of git)
- `tests/` the tests
- `djai.toml` your settings (you make this one)

---

## First-time setup

Do these in order. Tick each one off.

- [ ] Copy `djai.example.toml` to `djai.toml`
- [ ] Open `djai.toml` and fill in your paths
- [ ] Run `mixxx/deploy.sh`
- [ ] Restart Mixxx
- [ ] Check the DJAI Bridge is enabled in Mixxx

### What goes in djai.toml

Five settings. Each one is a path or a name.

- `mixxx_user_dir` Mixxx's folder on Windows
- `mixxx_exe` the Mixxx program
- `music_dir` your music folder
- `recordings_dir` where Mixxx saves recordings
- `speaker_match` optional, see "Overnight"

Never type a path into the code.
Add a key to `tools/config.py` instead.

---

## How to run it

Always use `tools/djai`.
Never call `djai.py` directly.

The wrapper makes sure only one thing talks to Mixxx at a time.
Never run two at once.

### Plan a set

```
tools/plan_set.py sets/pool.json sets/analysis sets/plan.json
```

This picks the tracks and the mix points.

### Play a set

1. Close Mixxx.
2. Queue the tracks with `tools/set_autodj_queue.py`.
3. Start Mixxx.
4. Run `tools/run_set.py sets/plan.json`.

Start a watchdog at the same time.
It should shout if no deck plays for 60 seconds.

### Overnight

```
tools/overnight.py sets/plan.overnight.json sets/logs/overnight.log
```

This is the quiet one.

- It turns the speakers down.
- It records at full level.
- If the set dies, it resumes. Up to three times.
- It turns the speakers back up at the end.

---

## What each tool does

One line each.

- `analyze_bars.py` measures loudness bar by bar
- `plan_set.py` picks mix points and track order
- `build_full_plan.py` merges approved pair plans into one plan
- `set_autodj_queue.py` queues a plan's tracks in Mixxx
- `run_set.py` plays and records the set
- `overnight.py` runs a whole set quietly
- `check_grid.py` checks the beatgrid
- `beat_phase.py` checks the beatgrid too
- `record_mix.py` logs a mix you do by hand
- `replay_mix.py` replays a logged mix
- `djai` the WSL wrapper that sends commands to Mixxx (see above)
- `djai.py` the Windows side of that wrapper, never call it directly
- `config.py` reads `djai.toml`
- `restart_mixxx.sh` restarts Mixxx with tracks loaded and waits for the bridge
- `winvolume.ps1` reads or sets an output device's volume (used by `overnight.py`)
- `load_track.ps1`, `screenshot.ps1` debugging helpers for driving the Mixxx window
- `join_recordings.sh` a one-off that joined the two parts of the 2026-10-25 set

Plans use audio frames, not bars.
That way a wrong beatgrid cannot hurt twice.

---

## Gotchas

Read these before you get stuck.

- Mixxx must be **closed** before `set_autodj_queue.py`.
- Check `file_bpm` and `beat_closest` in Mixxx before every run.
  Grids get fixed by hand a lot.
- Judge a grid by where the bass swell lands.
  Not by the kick.
- Mixxx's key numbers:
  - 1 to 12 are major keys, C to B
  - 13 to 24 are minor keys, C to B
- `pkill -f` matches its own command line.
  Keep the pattern out of that command.
- Commit before big changes.
  Logs and Mixxx backups are not in git, so deleting those is permanent.

---

## Testing and lint

Run the tests after any change in `tools/` or `mixxx/controllers/`.

```
npm test
```

Check the style:

```
npm run lint
```

Both tests need 90 percent coverage or they fail.
The tests never touch real Mixxx or real MIDI.

First time? Install the dev tools:

```
.venv/bin/pip install -r requirements-dev.txt
```

---

## If you are stuck

- No sound from Mixxx? Check the DJAI Bridge is on.
- Tools hang? Another caller may hold the lock. Wait, or stop it.
- Controller not changed? Restart Mixxx after `deploy.sh`.
- Wrong blend timing? Re-check the beatgrid.

---

## More

- `AGENTS.md` fuller notes for the AI itself
- `mixxx/stock-2.5.0/` the untouched original mapping, for comparing

---

## Licence

GPL-2.0-or-later. See `LICENSE`.

The FLX4 mapping is based on Mixxx's own mapping.
Mixxx is GPL, so this repo is too.
Keep the author credits at the top of those files.

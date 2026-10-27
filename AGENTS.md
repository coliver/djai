# DJAI

An AI co-DJ for Mixxx 2.5.6 (Windows) and a Pioneer DDJ-FLX4. Claude does the mixing itself, in the style of
hard acid trance: long, smooth, phrase-matched blends. The code is driven from WSL.

## Layout

- `mixxx/controllers/` the Mixxx mappings: `DJAI-Bridge` (SysEx text commands, beat-scheduled `at` / `ramp`),
  `DJAI-transition.js`, and the customised `Pioneer-DDJ-FLX4-DJAI` mapping. `mixxx/deploy.sh` copies them into
  Mixxx's Windows user folder (`mixxx_user_dir` in `djai.toml`); restart Mixxx to load them.
- `mixxx/stock-2.5.0/` the unmodified upstream FLX4 mapping, for diffing. `mixxx/archive/` backups of the Mixxx
  database and config.
- `tools/` the Python and shell tooling (see below).
- `sets/` data, gitignored (only `.gitkeep` is tracked): `pool*.json` candidate tracks, `analysis/` per-bar profiles, `plan.*.json` set plans, `logs/`,
  `live/` recordings of hand mixes, `husten_grids.json` the two grids Husten needs.
- `tests/py`, `tests/js` the test suite.

## Configuration

Everything machine-specific lives in `djai.toml` at the repo root (copy `djai.example.toml`): the Mixxx user folder,
the Mixxx program, the music library root, the recordings folder, and an optional `speaker_match` for the overnight
volume control. `tools/config.py` reads it; shell scripts call `python3 tools/config.py <key>`. Track `location`
fields in `sets/*.json` are relative to `music_dir`, so no file in the repo names a path outside it. Do not hard-code
a path; add a key to `config.py` instead.

## Tools

`tools/djai` is the WSL wrapper that runs `djai.py` under Windows Python, which can reach the loopMIDI port
"DJAI". Do not call `djai.py` directly from WSL, and do not run two callers at once (the wrapper takes a lock).

| Tool | Does |
| --- | --- |
| `analyze_bars.py` | per-bar loudness profile of a track against its Mixxx beatgrid |
| `plan_set.py` | picks phrase-aligned mix points and orders a set |
| `build_full_plan.py` | merges the approved `plan.next.*.json` pair plans into one frame-based plan |
| `set_autodj_queue.py` | puts a plan's tracks on top of the Auto DJ queue (Mixxx must be closed) |
| `run_set.py` | plays and records a planned set through the bridge |
| `overnight.py` | quiet unattended run: lowers the speakers, runs `run_set.py`, resumes if it dies |
| `check_grid.py`, `beat_phase.py` | measure beatgrid accuracy; judge by where the bass swell lands, not kick onset |
| `record_mix.py`, `replay_mix.py` | log a hand mix, and replay its moves later |

Plans use audio frames (`in_frame`, `mix_frame`, `cut_frame`), not bars, so no grid has to be right twice.
Auto DJ is only used as the track loader.

## Mixing rules (from the user)

Rules 1 and 2 are firm. Rules 3 to 5 are defaults: the user breaks them when the tracks call for it, so never treat
them as absolute.

1. Never change the BPM of the record that is playing. Match the incoming deck to the outgoing one before it is
   heard. Small corrections (jog wheel, pitch) go on the incoming deck only.
2. Always mix on the phrase. Trance is built in 16 and 32 bar sections, with 8 bar phrases inside them. Start or
   switch tracks at a phrase boundary, often where a new kick, bassline or melody enters, and keep both tracks'
   phrases lined up for the whole blend. Bar 1 of a grid is not always a phrase start.
3. Blend length depends on the tracks. Usually long (up to 64 bars), but the tracks decide. The planner floor is
   16 bars (`MIN_BLEND` in `plan_set.py`), and blends are whole 16 bar sections so both tracks stay on the phrase.
4. Hard cuts are usually wrong: make EQ and volume moves ramps. It is breakable when the tracks call for it. Avoid
   key clashes by track order first, not by shortening a blend.
5. Usually leave the outgoing record alone while its lead is playing, and only cut its bass (about 2 beats) at the
   swap, placed where that record's bass naturally ends. Not a hard law either.
6. Keep the beat steady: match tempo and line up the kicks before blending, then check they stay aligned all the way
   through the transition.

Read the `feedback-hard-trance-playbook` memory before planning a set.

## Lint and tests

`npm run lint` runs ruff (config in `pyproject.toml`; the code is not auto-formatted, lines go to 180). Dev tools install with
`.venv/bin/pip install -r requirements-dev.txt`.

`npm test` runs everything: JS via `node --test` with coverage, Python via `.venv/bin/pytest` with branch
coverage. Both fail under 90 percent. Run it after any change to `tools/` or `mixxx/controllers/`. The Python
fakes (`tests/py/fakewinmm.py`, `conftest.py`) block real subprocess and MIDI calls, so tests never touch Mixxx.
Shell and PowerShell scripts are not covered.

## Gotchas

- Re-read `file_bpm` and `beat_closest` from Mixxx before every run; the user fixes grids by hand often.
- `pkill -f` and `ps | grep | kill` patterns match your own command line. Keep the pattern out of that command.
- Mixxx's key control is 1-12 major C..B, 13-24 minor C..B.
- Always pair `run_set.py` with a watchdog on "no deck playing for 60 s".
- `.venv/`, `__pycache__/`, `.coverage` are gitignored and regenerable. This folder is a git repository (branch `main`, no
  remote). Everything in `sets/`, `mixxx/archive/` and `djai.toml` are gitignored, so deleting those is permanent.

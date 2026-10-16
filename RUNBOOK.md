# Runbook — Execution & Test Steps

Run all commands from the project root.

**Logs:** add `--log` to any test command to save the full output to `logs/<test>_<date-time>.txt` (console output unchanged). Logs are committed as test history.

## 0. Setup
```powershell
C:\Users\jayak\cv_env\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 1. Synthetic Data — `generate_match.py`
```powershell
python generate_match.py
```
**Expect:** seed, score, event counts, pass accuracy / shots / xG per team.
**Output:** `data/synthetic/match_001.json`, `data/synthetic/match_001_events.csv`

**Last result (16 Oct):** RIV 4–1 NGR · 2,154 events · RIV 508 passes (76%), 24 shots, xG 3.44 · NGR 491 passes (73%), 10 shots, xG 1.10

### Optional: Metrica sample (real data, dev testing only — never published)
```powershell
python fetchdata.py
```
**Output:** `data/raw/metrica/` (git-ignored)

## 2. Live Streamer — `stream_server.py`
```powershell
python stream_server.py --speed 60            # add --autoplay to start immediately, --port to change port
```
**Test in browser:** open http://localhost:8500 → press **Play** (speed, pause, jump-to-minute, restart available)

**Test in terminal** (second terminal, server running):
```powershell
python test_stream.py                         # prints key events live
python test_stream.py --check                 # automated test at 600x
```
**Expect:** `received N/N  in_order=True  max_timing_drift<0.5s` → `PASS`

**Last result (16 Oct):** 2154/2154 received, in order, drift 0.09s → PASS · log: `logs/test_stream_check_2026-10-16_2302.txt`

Note: `--check` restarts the stream and sets speed to 600× for every connected viewer.

## 3. Stats Engine — `stats_engine.py`
```powershell
python test_stats.py                          # synthetic match: exact checks
python test_stats.py --alerts                 # + full alert timeline
python test_stats.py --metrica                # real Metrica match: sense checks (needs fetchdata.py first)
```
**Expect:** half-time & full-time stat tables, alert summary, checks → `8/8 checks passed -> PASS`

**Checks — synthetic:**
1. Score matches generator
2. Shot / pass / tackle / card counts match raw events
3. Possession sums to 100%
4. Pass accuracy & xG match raw events
5. ≥3 of 4 scripted momentum spells detected
6. Insight alerts 1–3 per 10 min (goals, cards, subs excluded) and every goal alerted

**Checks — Metrica:**
7. Possession 35–65%, pass accuracy 65–90%
8. Avg pass distance 12–25 m
9. Avg ball speed 20–110 km/h
10. 1–10 momentum swings

**Last result (16 Oct):**
- Synthetic: 8/8 PASS — 4/4 spells detected, 2.2 insight alerts / 10 min · log: `logs/test_stats_synthetic_2026-10-16_2303.txt`
- Metrica: 8/8 PASS — possession 56/44, pass acc 77%/74%, avg pass 17.6 m, ball speed ~46 km/h, 6 swings · log: `logs/test_stats_metrica_2026-10-16_2303.txt`

**Tuning:** all thresholds in `THRESHOLDS` at the top of `stats_engine.py`.

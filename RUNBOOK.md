# Runbook — Execution & Test Steps

Run all commands from the project root.

**Logs:** add `--log` to any test command to save the full output to `logs/<test>_<date-time>.txt` (console output unchanged). Logs are committed as test history.

**Docs:** metric formulas → [docs/metrics.md](docs/metrics.md) · data formats → [docs/schemas.md](docs/schemas.md)

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

**Last result (16 Oct):** RIV 4–1 NGR (Arribas 4 goals: 2', 17', 35', 56' · Brandt 88') · 2,080 events · RIV 539 passes (78%), 21 shots, xG 3.50 · NGR 455 passes (73%), 10 shots, xG 0.78

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
python test_stream.py --check --log           # automated test at 600x
```
**Expect:** `received N/N  in_order=True  max_timing_drift<0.5s` → `PASS`

**Last result (16 Oct):** 2080/2080 received, in order, drift 0.04s → PASS · log: `logs/test_stream_check_2026-10-16_2316.txt`

Note: `--check` restarts the stream and sets speed to 600× for every connected viewer.

## 3. Stats Engine — `stats_engine.py`
```powershell
python test_stats.py --log                    # synthetic match: exact checks
python test_stats.py --alerts --log           # + full alert timeline
python test_stats.py --metrica --log          # real Metrica match: sense checks (needs fetchdata.py first)
```
**Expect:** half-time & full-time stat tables, alert summary, checks → `N/N checks passed -> PASS`

**Checks — synthetic:**
1. Score matches generator
2. Shot / pass / tackle / card counts match raw events
3. Possession sums to 100%
4. Pass accuracy & xG match raw events
5. ≥3 of 4 scripted momentum spells detected
6. Insight alerts 1–3 per 10 min (goals, cards, subs excluded) and every goal alerted
7. Clean match has no rejected events
8. Snapshot carries `schema_version`
9. Engine survives duplicate / malformed / out-of-order / unknown-team / handler-breaking events and counts them
10. Streamer drops duplicate & malformed events

**Checks — Metrica:**
7. Possession 35–65%, pass accuracy 65–90%
8. Avg pass distance 12–25 m
9. Avg ball speed 20–110 km/h
10. 1–10 momentum swings
11. Pass difficulty computed by the engine (avg 0.15–0.5)
12. Score matches source
13. No rejected events

**Last result (16 Oct):**
- Synthetic: 12/12 PASS — 4/4 spells detected, 2.0 insight alerts / 10 min · log: `logs/test_stats_synthetic_2026-10-16_2316.txt`
- Metrica: 11/11 PASS — possession 56/44, pass acc 77%/74%, avg pass 17.6 m, ball speed ~46 km/h, difficulty 0.26/0.25, 6 swings · log: `logs/test_stats_metrica_2026-10-16_2316.txt`

**Tuning:** all thresholds in `THRESHOLDS` at the top of `stats_engine.py` (documented in docs/metrics.md).

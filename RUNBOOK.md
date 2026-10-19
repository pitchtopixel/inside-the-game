# Runbook — Execution & Test Steps

Run all commands from the project root.

**Logs:** add `--log` to any test command to save the full output to `logs/<test>_<date-time>.txt` (console output unchanged). Logs are committed as test history.

**Docs:** metric formulas → [docs/metrics.md](docs/metrics.md) · data formats → [docs/schemas.md](docs/schemas.md)

## 0. Setup
Project has its own virtual environment (`.venv`, Python 3.13, git-ignored):
```powershell
python -m venv .venv                          # first time only
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 1. Synthetic Data — `generate_match.py`
```powershell
python generate_match.py --all                # every match in MATCHES (generate in this order: names stay unique)
python generate_match.py match_002            # one match
```
**Expect:** per match: seed, score, event counts, pass accuracy / shots / xG per team, goal list.
**Output:** `data/synthetic/<match_id>.json` and `<match_id>_events.csv`

Matches are defined in `MATCHES` (teams, start seed, storyline, match-day metadata); clubs in `CLUBS` (all fictional).
Each match takes the first seed whose result fits its storyline (`STORIES`). Player surnames are unique across the dataset.

**Last result (19 Oct):**
- `match_001` (story *open*): Riverside 4–1 Northgate — Arribas 2', 17', 35', 56' · Sosa 88' · 2,080 events
- `match_002` (story *comeback*): Harbourside 1–4 Eastvale — Balogun 2' · Bittencourt 28', 45+2' · Sampaio 78' · Cordeiro 89' · 1,980 events
- 64 players, 64 unique surnames

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
python test_stats.py --log                    # synthetic match_001: exact checks
python test_stats.py --match match_002 --log  # another synthetic match
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

**Last result (19 Oct):**
- match_001: 12/12 PASS — 4/4 spells detected, 2.0 insight alerts / 10 min · log: `logs/test_stats_match_001_2026-10-19_2113.txt`
- match_002: 12/12 PASS — 3/4 spells detected, 2.1 insight alerts / 10 min · log: `logs/test_stats_match_002_2026-10-19_2114.txt`
- Metrica: 11/11 PASS — possession 56/44, pass acc 77%/74%, avg pass 17.6 m, ball speed ~46 km/h, difficulty 0.26/0.25, 6 swings · log: `logs/test_stats_metrica_2026-10-19_2114.txt`

**Tuning:** all thresholds in `THRESHOLDS` at the top of `stats_engine.py` (documented in docs/metrics.md).

## 4. Agents — `orchestrator.py` + `agents/` (Microsoft Agent Framework)
Architecture: [docs/agents.md](docs/agents.md). Model: `LLM_PROVIDER` in `.env` (copy `.env.example`); default `mock` = offline, free.
```powershell
python test_agents.py --log                   # mock model: full match, 3 viewer profiles, + failure modes
python test_agents.py --match match_002 --log # another match (profiles: away-club fan, top scorer focus)
python test_agents.py --show                  # + print sample overlays
python test_agents.py --foundry --log         # real Foundry model (needs .env + az login; a few cents)
```
**Expect:** run summary (LLM calls, fallbacks, latency, overlays), checks → `21/21 checks passed -> PASS`

**Checks:**
1. Every alert became a moment the analyst sees
2. Every important moment (importance ≥ 45) got an explanation (version 2)
3. Explanations came from the LLM agents, no fallbacks
4. Instant (template) overlays p95 < 50 ms
5. Half-time & full-time recaps for every viewer
6. Every overlay matches the schema
7. LLM cost bounded (< 150 calls per match)
8. Fan sees fewer, bigger moments than analyst
9. Spanish viewer gets Spanish overlays (mock marks `[es]`; real translation needs Foundry)
10. Favourite-club highlight correct
11. Player focus: all of the player's moments + tracker every 5 min
12. Single-metric ticker for metric viewers
13. Ingest agent rejects a duplicate event
14–20. Model fails / returns bad JSON / invents numbers / invents names / makes out-of-scope claims /
contradicts the data / is too slow → template fallback, nothing lost, no invented text on screen
21. Contradiction rules: 6 wrong sentences caught, 5 true sentences pass (no false alarms)

**Last result (20 Oct, mock):** 21/21 PASS on match_001 and match_002 · logs: `logs/test_agents_mock_match_001_2026-10-20_0042.txt`, `logs/test_agents_mock_match_002_2026-10-20_0042.txt`

## 5. App (UI) — `app.py` + `static/`
```powershell
python app.py                                 # then open http://localhost:8600 and press Play
python app.py --speed 20 --autoplay           # start playing immediately
python app.py --match data/synthetic/match_002.json   # another match
```
- Pitch: name tag on the player with the ball; shot speed + xG on every shot and sprint speeds for all viewers; pass distance / difficulty / ball speed for analysts
- **Compare viewers** button (or open http://localhost:8600/?compare) → Analyst vs Fan side by side
- Each pane: preset or custom viewer (mode, language, favourite club, player focus, metric)
- Click the timeline or a key-moment chip to jump; badges show **AI · grounded**, **LIVE** (template) or **AUTO** (fallback)
- `--mock-latency 1.0` (default) delays the mock model so the template → AI upgrade is visible; ignored with Foundry
- Port 8600 (the streamer test page stays on 8500)

**Automated test** (starts its own server on port 8601):
```powershell
python test_app.py --log
python test_app.py --match match_002 --log
```
**Expect:** `10/10 checks passed -> PASS`

**Checks:**
1. Viewer receives every match event
2. Live stats snapshots stream
3. Each viewer only gets its own profile's overlays
4. Fan sees fewer moments than analyst
5. Template overlays upgrade to AI versions (v1 → v2)
6. Fan overlays personalized (Spanish, club highlight, metric)
7. Both viewers get half-time & full-time recaps
8. Metric ticker reaches the fan viewer
9. Timeline items stream to viewers
10. Seek to 60' rebuilds state and timeline

**Last result (19 Oct, mock):**
- match_001: 10/10 PASS · log: `logs/test_app_match_001_2026-10-19_2113.txt` · screenshot: `docs/img/ui_compare.png`
- match_002: 10/10 PASS · log: `logs/test_app_match_002_2026-10-19_2114.txt` · screenshot: `docs/img/ui_match002_pitch.png`

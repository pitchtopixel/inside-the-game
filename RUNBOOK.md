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

### Validate the generated matches — `validate_match.py`
```powershell
python validate_match.py --self-test --log    # every match; --self-test proves each check can fail
python validate_match.py match_002            # one match
```
**Expect:** per match `10/10 checks passed, 0 warnings -> PASS` and 10 self-test lines PASS.
Checks: fields & ids · time order · teams/players · pitch bounds · goals follow scoring shots & score adds up ·
shot outcomes · pass geometry & difficulty · clock rules · substitutions · kick-offs & cards. Realism ranges = warnings.

**Last result (20 Oct):** match_001 and match_002 10/10, 0 warnings; self-test 10/10 · log: `logs/validate_all_2026-10-20_0058.txt`

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
5b. "On top" calls agree with the generator's true hidden momentum ≥ 70% of the time
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

**Last result (20 Oct):**
- match_001: 13/13 PASS — 4/4 spells detected, "on top" right 78% vs true momentum, 1.8 insight alerts / 10 min · log: `logs/test_stats_match_001_2026-10-20_0059.txt`
- match_002: 13/13 PASS — 3/4 spells detected, "on top" right 79%, 1.9 insight alerts / 10 min · log: `logs/test_stats_match_002_2026-10-20_0100.txt`
- Metrica: 11/11 PASS — possession 56/44, pass acc 77%/74%, avg pass 17.6 m, ball speed ~46 km/h, difficulty 0.26/0.25, 4 swings · log: `logs/test_stats_metrica_2026-10-20_0100.txt`

**Tuning:** all thresholds in `THRESHOLDS` at the top of `stats_engine.py` (documented in docs/metrics.md).

## 4. Agents — `orchestrator.py` + `agents/` (Microsoft Agent Framework)
Architecture: [docs/agents.md](docs/agents.md). Model: `LLM_PROVIDER` in `.env` (copy `.env.example`); default `mock` = offline, free.
```powershell
python test_agents.py --log                   # mock model: full match, 3 viewer profiles, + failure modes
python test_agents.py --match match_002 --log # another match (profiles: away-club fan, top scorer focus)
python test_agents.py --show                  # + print sample overlays
python test_agents.py --foundry --log         # real Foundry model (needs .env + az login; a few cents)
```
**Expect:** run summary (LLM calls, cache hits, blocked drafts, fallbacks, rejected names, latency, overlays), checks → `23/23 checks passed -> PASS` (mock)

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
22. Name rules: invented names caught, known names and possessives ("Riverside's") pass
23. Lower-cased names get their capitals back (a real model once wrote a whole recap in lower case)

**Last result (22 Oct, mock):** 23/23 PASS on match_001 and match_002 · logs: `logs/test_agents_mock_match_001_2026-10-22_1350.txt`, `logs/test_agents_mock_match_002_2026-10-22_1350.txt`

### Foundry setup (done 20 Oct)
1. Azure free trial ($200 credit, ~30 days). Never click "Upgrade to pay-as-you-go"; the spending limit stops charges at $200.
2. Budget alerts: Cost Management → Budgets → `hackathon-budget`, ₹4,200 monthly, alerts at 20% (₹840) and 100%.
3. Foundry project (ai.azure.com, New Foundry): `inside-the-game`, region East US 2, new resource group (delete it to remove everything), App Insights on.
4. Model: `gpt-4.1-mini`, Global Standard (gpt-4o-mini is deprecated). Limits: 100 requests/min, 100k tokens/min.
5. Azure CLI: `winget install -e --id Microsoft.AzureCLI`, then `az login` (new terminal; in an old VS Code terminal refresh PATH first:
   `$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")`).
6. `.env` (git-ignored, copy of `.env.example`): `FOUNDRY_PROJECT_ENDPOINT` = Project endpoint from the Foundry project home page, `FOUNDRY_MODEL=gpt-4.1-mini`. No API key: login via `az login`.

**Foundry results (match_001)** - what we found and fixed: [docs/real_model_findings.md](docs/real_model_findings.md)

| Run | Change | LLM explanations | Fallbacks | Calls | p95 latency | Log |
|---|---|---|---|---|---|---|
| 1 | first real run | 24% | 19 (18 unknown_name) | 97 | 4.7 s | `logs/test_agents_foundry_match_001_2026-10-20_2356.txt` |
| 2 | possessives allowed | 62% | 10 (Title Case words) | 83 | 3.9 s | `logs/test_agents_foundry_match_001_2026-10-21_0000.txt` |
| 3 | sentence case + feedback retry | **95%** | 1 (contradiction) | 72 | 3.5 s | `logs/test_agents_foundry_match_001_2026-10-21_0003.txt` |

Check 3 with Foundry: ≥ 90% from the LLM (mock: 100%). Rejected answers are printed under "Rejected answers" with the reason and text.

**Last result (21 Oct, Foundry):**
- match_001: **15/15 PASS** - 100% LLM, 0 fallbacks, 71 calls, p95 3.1 s; 3 answers rejected then fixed by feedback · log: `logs/test_agents_foundry_match_001_2026-10-21_0015.txt`
- match_002: **15/15 PASS** - 100% LLM, 0 fallbacks, 100 calls, p95 4.6 s; 2 answers rejected then fixed · log: `logs/test_agents_foundry_match_002_2026-10-21_0013.txt`

## 5. App (UI) — `app.py` + `static/`
```powershell
python app.py                                 # then open http://localhost:8600 and press Play
python app.py --speed 20 --autoplay           # start playing immediately
python app.py --match data/synthetic/match_002.json   # another match
```
- Pitch: name tag on the player with the ball; shot speed + xG on every shot and sprint speeds for all viewers; pass distance / difficulty / ball speed for analysts
- **Compare viewers** button (or open http://localhost:8600/?compare) → Analyst vs Fan side by side
- Each pane: preset or custom viewer (mode, language, favourite club, player focus, metric)
- First screen: fixture card with **▶ Play replay**; empty panels say what will appear
- **AI match story** under each pitch: latest key moment (bigger moments stay on top), *Why it matters*, evidence (analyst),
  and a **Verified** box built from the real grounding check (numbers matched, names checked, no out-of-scope claims,
  no contradictions); earlier moments listed below it
- Click the timeline or a key-moment chip to jump **and** show that moment's explanation in the story panel;
  badges show **AI · grounded**, **LIVE** (template) or **AUTO** (fallback)
- Momentum card: what "threat share" means + **What changed** (latest momentum / pressure / rhythm moment); stat definitions on hover
- Agents card: model calls, answers from cache, AI drafts blocked & rewritten, template fallbacks
- Full-time recap stays until closed; **Match recap** button reopens it. Recaps get momentum per 15 minutes (`momentum_by_15_min`)
- Compare viewers = Analyst · English vs Fan · English (same facts, different depth); Spanish / Hindi / player focus are presets
- `--mock-latency 1.0` (default) delays the mock model so the template → AI upgrade is visible; ignored with Foundry
- Port 8600 (the streamer test page stays on 8500); `--host 0.0.0.0` inside a container
- Each browser gets its own replay (max 5 at once, `--max-sessions`); a replay closes after 10 min with no clicks while paused
- Answer cache: identical agent tasks are answered once and shared by every viewer (only checked answers are kept).
  With Foundry, the app preloads `data/cache/answers.json`, so the preset viewers cost no model calls

**Prebake the answer cache** (real model; reuses saved answers, only new tasks call the model). Rerun after **any** change
that alters what an agent receives - prompts, facts, presets, or post-processing of answers (an explanation is part of the
narrator's task, which is part of the personalizer's, so changes cascade). Then check: presets must show 0 model calls.
```powershell
python prebake_cache.py --log                 # every match x the app's preset viewers -> data/cache/answers.json
```
**Last result (22 Oct):** 364 answers saved (6 preset viewers incl. Fan · English; 154 new calls after the name-case fix) ·
check: live replay with the presets = 59 cache hits, 0 model calls · log: `logs/prebake_cache_2026-10-22_1354.txt`

**Automated test** (starts its own server on port 8601):
```powershell
python test_app.py --log
python test_app.py --match match_002 --log
```
**Expect:** `13/13 checks passed -> PASS`

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
11. Each browser has its own replay (one viewer's seek does not reach another)
12. A later viewer of the same match reuses cached answers (0 model calls)
13. A sixth browser at once is told the demo is busy

**Last result (22 Oct, mock):**
- match_001: 13/13 PASS (later viewer: 0 model calls, 44 cache hits) · log: `logs/test_app_match_001_2026-10-22_1350.txt`
- match_002: 13/13 PASS · log: `logs/test_app_match_002_2026-10-22_1351.txt`
- Screenshots (Foundry): `docs/img/ui_start.png` (first screen), `docs/img/ui_story_compare.png` (analyst vs fan, AI story + Verified box)

## 6. Container (local)
```powershell
docker build -t inside-the-game .
docker run --rm -p 8600:8600 inside-the-game          # mock model; open http://localhost:8600
```
Image: python:3.13-slim, ~360 MB; contains the app, `static/`, `data/synthetic/`, `data/cache/` - never `.env`, logs or raw data.
**Last result (22 Oct):** page served (HTTP 200), cache + matches present, no `.env` in the image

## 7. Azure Container Apps (live demo)
**URL:** https://inside-the-game.delightfulsand-21c23dc9.eastus2.azurecontainerapps.io/

Set up once (22 Oct). `<rg>` = the Foundry resource group, `<foundry-resource>` = the Foundry account (both in the Azure portal):
1. Image → GitHub Container Registry (private until the repo goes public):
   ```powershell
   docker build -t ghcr.io/pitchtopixel/inside-the-game:v1 .
   docker login ghcr.io -u pitchtopixel                 # classic token with write:packages
   docker push ghcr.io/pitchtopixel/inside-the-game:v1
   ```
2. Environment without Log Analytics (no log charges):
   `az provider register -n Microsoft.App` · `az containerapp env create -n itg-env -g <rg> -l eastus2 --logs-destination none`
3. App (token: classic, `read:packages` + `repo`; read it with `$t = (Get-Clipboard).Trim()` - pasting into a hidden prompt fails in VS Code):
   ```powershell
   az containerapp create -n inside-the-game -g <rg> --environment itg-env --image ghcr.io/pitchtopixel/inside-the-game:v1 `
     --registry-server ghcr.io --registry-username pitchtopixel --registry-password $t --target-port 8600 --ingress external `
     --cpu 0.5 --memory 1.0Gi --min-replicas 0 --max-replicas 1 --system-assigned `
     --env-vars LLM_PROVIDER=foundry FOUNDRY_MODEL=gpt-4.1-mini "FOUNDRY_PROJECT_ENDPOINT=$ep"
   ```
4. Managed identity → role **Foundry User** (formerly "Azure AI User") on `<foundry-resource>`; no keys anywhere.
   In Git Bash set `MSYS_NO_PATHCONV=1` first, or the `/subscriptions/...` scope is mangled.

**Update after a code change:** rebuild + push a new tag (`:v2`), then
`az containerapp update -n inside-the-game -g <rg> --image ghcr.io/pitchtopixel/inside-the-game:v2`.
Rerun `prebake_cache.py` first if prompts, agents or matches changed.

**Cost controls:** scale to zero (min 0 / max 1 replica, 0.5 vCPU / 1 GiB - inside the free monthly grant), no Log Analytics,
GHCR instead of Azure Container Registry, preset viewers answered from the prebaked cache, max 5 viewers, idle replays closed after 10 min.

**Last result (22 Oct, image v3):** page HTTP 200 (0.8 s warm, ~22 s after scale-to-zero) · presets (analyst + English fan):
**0 model calls**, 59 cache hits, 36 verified AI overlays, 4 recaps · custom viewer (English fan of Riverside): 16 Foundry calls
via managed identity, 1 draft blocked & rewritten, 0 fallbacks

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
**Expect:** run summary (LLM calls, cache hits, blocked drafts, fallbacks, rejected names, latency, overlays), checks → `26/26 checks passed -> PASS` (mock), `17/17` (Foundry, 22 Oct - before checks 21 update and 26)

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
21. Contradiction rules: 9 wrong sentences caught, 12 true sentences pass (no false alarms). Level score judged by grammar
    (13i): an intent word just before ("push to equalise", "chasing an equaliser", "nearly equalised", "will they equalise")
    or a modal just after ("an equaliser would...") makes it an aim; "came back to equalise" stays a claim
22. Name rules: invented names caught, known names and possessives ("Riverside's") pass
23. Lower-cased names get their capitals back (a real model once wrote a whole recap in lower case)
24. Arabic / Hindi answers must keep names in Latin letters (a real model transliterated them, and translated "Northgate")
25. Every option value works: 7 combination viewers (all 3 modes; French, Portuguese, Arabic, Spanish, Hindi; home / away club;
    player focus incl. a substitute; all 8 metrics) - language, club highlight, metric line, player moments, recaps.
    Prints one sample caption per language to read for quality
26. Broadcaster view: fewer moments than an analyst, more than a fan (importance ≥ 40), rewritten as on-air captions
    (neutral headline ≤ 8 words, one sentence to read on air, a talking point for the commentator)

**Last result (24 Oct, mock):** 26/26 PASS on match_001 and match_002 (logs `logs/test_agents_mock_match_00*_2026-10-24_1934.txt`)
**Last result (22 Oct, Foundry, match_001):** 17/17 PASS · combination viewers rerun after the name fix: all 6 ok, Arabic and Hindi
keep "Arribas" / "Riverside" in Latin letters · logs: `logs/test_agents_foundry_match_001_2026-10-22_2003.txt`,
`logs/test_agents_foundry_combos_match_001_2026-10-22_2015.txt`

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

### Tracing + model swap (24 Oct) - details: [docs/model_comparison.md](docs/model_comparison.md)
1. `pip install -r requirements.txt` (adds `azure-monitor-opentelemetry`).
2. Tracing: `$env:TRACING="1"` (+ `$env:TRACING_CONTENT="1"` to record prompts/answers), then any Foundry run.
   Traces go to App Insights `inside-the-game-resource-jayak-appinsights` (rg `rg-jayakarayil123-3358`) → Foundry portal → **Tracing**.
3. Model swap: `python test_agents.py --foundry --model gpt-5-mini --log` (no code change).
   Second deployment: `az cognitiveservices account deployment create -n inside-the-game-resource-jayak -g rg-jayakarayil123-3358 --deployment-name gpt-5-mini --model-name gpt-5-mini --model-version 2025-08-07 --model-format OpenAI --sku-name GlobalStandard --sku-capacity 100`
   (`gpt-4.1-nano`: 0 quota on the free trial; `gpt-4o-mini`: retired.)

| Model | Checks | LLM | Fallbacks | Calls | p95 | Log |
|---|---|---|---|---|---|---|
| gpt-4.1-mini | **19/19 PASS** | 100% | 0 | 70 | 3.6 s | `logs/test_agents_foundry_gpt-4.1-mini_match_001_2026-10-24_2008.txt` |
| gpt-5-mini | 16/19 (expected) | 0% | 47 (44 timeouts) | 121 | 7.5 s | `logs/test_agents_foundry_gpt-5-mini_match_001_2026-10-24_2013.txt` |

Screenshots (take in the Foundry portal): `docs/img/foundry_tracing.png`, `docs/img/foundry_trace_detail.png`, `docs/img/foundry_models.png`.

### Fallback-rate alert (24 Oct)
1. Code: every model answer logs `llm_answer` / `llm_fallback` (`agents/common.py`) → App Insights `traces` when `TRACING=1`.
2. Action group (email): `az monitor action-group create -g rg-jayakarayil123-3358 -n itg-alerts --short-name itgalerts --action email owner <email>`
3. Alert rule (Git Bash: `export MSYS_NO_PATHCONV=1` first; `az config set extension.use_dynamic_install=yes_without_prompt` once):
   `az monitor scheduled-query create -g rg-jayakarayil123-3358 -n itg-llm-fallback-rate --scopes <appinsights-id> --condition "max fallback_pct from 'FB' > 10" --condition-query FB="<KQL in docs/model_comparison.md §3>" --window-size 15m --evaluation-frequency 15m --severity 2 --action-groups <action-group-id> --auto-mitigate true`
4. Test: `$env:TRACING="1"; python test_agents.py --foundry --model gpt-5-mini --log` → 86.1% fallbacks → alert fires within ~15 min.
   Log: `logs/test_agents_foundry_gpt-5-mini_match_001_2026-10-24_2039.txt`
5. Production (image v10 = tracing + fallback logs), run yourself:
   `az containerapp update -n inside-the-game -g rg-jayakarayil123-3358 --image ghcr.io/pitchtopixel/inside-the-game:v10 --set-env-vars TRACING=1`

**Last result (24 Oct):** alert fired 20:55 IST (86.1% fallbacks, gpt-5-mini test) · live v10 (revision 0000009): HTTP 200 (0.8 s),
startup log "answer cache: 329 checked answers preloaded" + "tracing: on (Foundry portal > Tracing)"

### Groundedness report (13g) — `eval_groundedness.py`
Every saved answer judged by the Foundry **Groundedness evaluator** (azure-ai-evaluation, gpt-4.1-mini as judge) against what its
agent was given; personalizer rewrites against the overlay they rewrote (so translations are checked for meaning). Offline only.
```powershell
pip install -r requirements-eval.txt          # evaluation SDK - not in the app / container
python eval_groundedness.py --limit 5         # quick try
python eval_groundedness.py --log             # all answers -> docs/groundedness_report.md (~330 judge calls)
```
**Last result (24 Oct):** 321/329 grounded (98%), average 4.82/5, 0 judge errors · 8 below 4: score direction wrong
("Eastvale trailing 2-1" while leading 1-2 - the trail rule only knew "Eastvale trail / are trailing"), a Hindi rewrite gave
Bittencourt's goal to the wrong club · report: [docs/groundedness_report.md](docs/groundedness_report.md) · log:
`logs/eval_groundedness_2026-10-24_2130.txt`

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
- Compare viewers = Analyst · English vs Fan · English (same facts, different depth); Broadcaster / Spanish / Hindi / player focus are presets
- **Broadcaster** mode: neutral on-air caption, one sentence to read on air and a *Talking point* for the commentator;
  its own prompt (so adding it kept every saved fan / analyst answer)
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
Cache keys include a fingerprint of each agent's instructions, so a prompt change never reuses old answers.
**Last result (24 Oct):** 329 answers saved (65 new, Broadcaster · English preset; everything else reused) · 1 broadcaster
draft blocked, template shown · live presets 0 model calls · log: `logs/prebake_cache_2026-10-24_1935.txt`

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

**Last result (24 Oct, mock):**
- match_001: 13/13 PASS · log: `logs/test_app_match_001_2026-10-24_1934.txt`
- match_002: 13/13 PASS · log: `logs/test_app_match_002_2026-10-24_1935.txt`
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

**Last result (22 Oct, image v8 - no-cache headers; player-focus preset = most shots; player tracker in player focus; names kept in Arabic / Hindi):** player-focus preset 0 model calls, 53 cache hits · page HTTP 200 (0.8 s warm, ~22 s after scale-to-zero) · presets (analyst + English fan):
**0 model calls**, 59 cache hits, 36 verified AI overlays, 4 recaps · custom viewer (English fan of Riverside): 16 Foundry calls
via managed identity, 1 draft blocked & rewritten, 0 fallbacks

**Last result (24 Oct, image v9 - Broadcaster mode + preset; 13i level-score fix):** Broadcaster · English + Player focus:
**0 model calls**, 76 cache hits, 23 broadcaster AI captions

## 8. Release gate (GitHub Actions)
`.github/workflows/release-gate.yml` runs on every push to `main`, every PR, or by hand (Actions → release-gate → Run workflow):
1. `tests`: `test_stats.py` → `stream_server.py` + `test_stream.py --check` → `test_agents.py` → `test_app.py` (mock model, no Azure).
2. `image`: `docker build` only if all tests passed. Deploy stays manual (§7).

Same run locally (Git Bash):
```bash
python test_stats.py && (python stream_server.py & sleep 3; python test_stream.py --check) && python test_agents.py && python test_app.py
```
**Last result (24 Oct, local):** stats 13/13 (1 s), stream PASS 2080/2080 (10 s), agents 26/26 (6 s), app 13/13 (28 s).
**GitHub (24 Oct):** release-gate #1 green, commit 7858c32, tests + image in 2m 4s · screenshot `docs/img/release_gate.png`

## 9. Single agent vs multi-agent — `eval_single_agent.py`
Same match, moments and viewers through the real pipeline and through one single agent (same fact sheet, same checks, no retry / template).
```powershell
python eval_single_agent.py --mock --limit 6   # plumbing check, free (the mock cannot answer the single-agent task)
python eval_single_agent.py --log              # real Foundry model, ~160 calls (~₹5); single agent with and without retry
```
**Last result (24 Oct 22:42, gpt-4.1-mini, match_001, 41 overlays)** - multi / single / single + retry:
viewer sees the moment p95 4.7 ms / 3.2 s / 4.2 s · still wrong 0 / 11 / 1 · calls 69 / 41 / 53 · tokens 50K / 34K / 45K for 3 profiles;
single + retry costlier from ~3 distinct profiles (2.6x at 200) · log: `logs/eval_single_agent_match_001_2026-10-24_2242.txt` · details: docs/agents.md

## 10. Infrastructure as code — `infra/main.bicep`
Describes the Container Apps environment, the app (managed identity, **HTTP autoscale 0→3 replicas at 4 viewers each**,
**liveness + readiness probes** on `/`), the "Foundry User" role, the e-mail action group and the fallback-rate alert.
Foundry and Application Insights are referenced as existing. Token and e-mail are deploy-time parameters (not in the repo).
```powershell
az bicep install                                   # once
$t = (Get-Clipboard).Trim()                        # GHCR token, classic, read:packages only
curl.exe -s -u "pitchtopixel:$t" "https://ghcr.io/token?scope=repository:pitchtopixel/inside-the-game:pull"   # must print {"token":...}
az deployment group what-if -g rg-jayakarayil123-3358 -f infra/main.bicep -p infra/main.parameters.json -p registryPassword=$t alertEmail=<email>
az deployment group create  -g rg-jayakarayil123-3358 -f infra/main.bicep -p infra/main.parameters.json -p registryPassword=$t alertEmail=<email>
```
**Last result (24 Oct):** what-if = only max replicas 1→3, scale rule `viewers`, probes (rest: Azure defaults) · deployed 23:08 IST.

**Incident (24 Oct, ~25 min down):** the deploy replaced the registry secret with a token GHCR rejected → new revision
`ImagePullFailure` / `ActivationFailed`, old revision could not pull either. Diagnosed with
`az containerapp logs show --type system`. Fixed: new read-only token (`read:packages`) →
`az containerapp secret set ... registry-password=$t` → `az containerapp update ... --revision-suffix bicep2` (fresh revision; a
restart did not retry the failed one). Result: `inside-the-game--bicep2` Running · Healthy, HTTP 200, 329 answers, tracing on.
**Lesson:** test the token before every deploy (curl line above); after 26 Oct make the package public and drop the registry login.

**Incident (27 Oct, found ~11:50 IST):** the live URL did not load. System log: liveness and readiness probes failed, container
restarted 5 times. Console log stopped after `answer cache: 329 ... preloaded`: startup was stuck in tracing setup (Foundry
never returned the Application Insights connection string; the same call also hung from the laptop, and answered in 5 s an hour
later). The web server starts only after that step, so every cold start from 0 replicas failed. Fixed live:
`az containerapp update -n inside-the-game -g $rg --set-env-vars TRACING=0` → revision `0000011` Running · Healthy, HTTP 200 in ~1 s.
**Code fix (GitHub, not deployed):** `start_tracing()` in `llm.py` gives tracing setup 15 s, then the app starts without it
(`tracing: off ...`). Tested: simulated hang → gives up on time; real setup → tracing on in 4.4 s; `test_app.py` 13/13.
**Lesson:** nothing optional may block startup; check the live URL after any cold start.

## 11. Load test — `load_test.py`
Ramps viewers on the live app (preset profiles → 0 model calls); `busy` viewers retry every 5 s; replicas counted every ~15 s.
```powershell
python load_test.py --log                                   # live: 5 -> 10 -> 15 -> 20 viewers, 90 s each (~6 min)
python load_test.py --url ws://127.0.0.1:8600/ws --no-replicas --steps 2 4 --step-s 15   # local check
```
**Last result (24 Oct 23:54, cold start from 0 replicas):** replicas 0→1→2→3, 15/15 viewers watching, 20 → 5 wait (`busy`), 0 dropped,
instant overlay p95 10 ms (worst 16 ms), time to get in median 15.7 s / max 35 s, 606 overlays, 0 model calls / 510 cache hits;
scaled 3→0 within ~6 min after the first run · log: `logs/load_test_2026-10-24_2354.txt` (warm run: `..._2342.txt`) · details: docs/scale_test.md

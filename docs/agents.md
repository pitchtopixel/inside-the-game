# Agent Architecture

Built with **Microsoft Agent Framework** (workflows + agents) and **Microsoft Foundry** (models).
Code: [`orchestrator.py`](../orchestrator.py), [`agents/`](../agents), [`llm.py`](../llm.py).

## Agents
| Agent | Kind | Role |
|---|---|---|
| **Ingest** | rules | Rejects malformed and duplicate events before they reach the stats engine |
| **Stats** | deterministic | Live state, pass quality, speed, momentum, rhythm, alerts ([metrics](metrics.md)) |
| **Moments** | rules | Ranks alerts into key moments (importance 0–100, category, turning point / milestone / late tags); decides what deserves an LLM explanation |
| **Explainer** | LLM (Foundry) | Explains **why** a moment matters — scoreline, control vs chaos, pressure, momentum — from a fact sheet only |
| **Narrator** | template + LLM | Instant template line (no LLM); broadcast commentary line; half-time / full-time recaps with player of the period |
| **Personalizer** | rules + LLM | Rules decide **what** each viewer sees (mode, club, player focus, metric); LLM changes **how** it reads (fan tone, language) |

## Workflows (Agent Framework `WorkflowBuilder`)
```
Live (every event, no LLM, ~6 ms p95):
  Ingest → Stats → Moments → Narrator(instant) → Personalizer(instant)      ⇒ version-1 overlays

Enrichment (per key moment, background, max 4 in parallel):
  Explainer → Narrator(commentary) → Personalizer(tone/language)              ⇒ version-2 overlays (replace v1)

Recap (half-time, full-time):
  Narrator(recap) → Personalizer                                               ⇒ recap overlays
```
**Shared state:** `MatchContext` — stats engine, key moments, viewer profiles, LLM metrics.
**Handoffs:** typed messages (`StatsUpdate → MomentBatch → OverlayBatch`, `EnrichRequest → Explained → Narrated`).

## Why template first, LLM second
A live overlay cannot wait 1–3 s for a model. Viewers get a factual template overlay in milliseconds;
the explained, personalized version replaces it (same `overlay_id`, `version` 2) when ready.

## Reliability & trust (every LLM step)
1. **Fact sheet only** — the model receives the computed stats for that moment, nothing else.
2. **Timeout + 1 retry.**
3. **JSON validation** — required keys present.
4. **Hallucination (grounding) checks** — the answer is rejected if it contains:
   - a **number** not in the fact sheet (normal rounding allowed),
   - a **name** (capitalised word mid-sentence) not in the fact sheet — e.g. an invented scorer or competition,
   - an **out-of-scope claim** about things we have no data on (season, career, injury, transfer, crowd, history…),
   - a **contradiction** of the data in plain words: a team "leads"/"trails" against the score, "all square" when
     it is not, a team "on top" against the momentum, "header" vs foot, "saved" vs missed.

   Other plain-word claims (e.g. "beat two defenders") are not machine-checked; prompts forbid invented facts,
   and an AI groundedness evaluator (Microsoft Foundry) is planned for the top moments. No check can guarantee
   zero hallucination — these layers make it rare, detectable and visible (`source`, `fallback_reason`).
5. **Template fallback** — on any failure the overlay is built from templates; `source: "fallback"` and `fallback_reason` record why.
6. **Metrics** — LLM calls per agent, fallbacks by reason, latency; shown in tests.

Tested failure modes (mock model): unavailable, malformed JSON, invented numbers, invented names,
out-of-scope claims, contradictions, too slow → all degrade to template overlays, none lost, no invented
text on screen. The contradiction rules are also tested directly on hand-written true and false sentences.

## Cost control
- Only moments with importance ≥ 45 are explained (~23 per match); low-priority alerts stay template-only.
- Analyst + English needs no personalizer call; personalizer results are cached per (overlay, mode, language, club).
- ~74 LLM calls per full match with 3 viewer profiles.

## Mock model
`LLM_PROVIDER=mock` (default) runs the same agents against an offline model that answers from the fact sheet.
It cannot translate — it marks text as `[es] …`; real translation needs Foundry. Failure modes via `MOCK_MODE`.

## Single agent vs multi-agent (measured)
`python eval_single_agent.py --log` · 24 Oct 2026 · match_001 · `gpt-4.1-mini` on Microsoft Foundry ·
log: `logs/eval_single_agent_match_001_2026-10-24_2242.txt` (first run, without the retry column: `..._2235.txt`)

Same match, same 21 explained moments, same 3 viewers (analyst EN, Spanish fan of the away club, player focus) →
41 viewer × moment overlays. The single agent gets the **same fact sheet** plus the viewer profile and must explain,
narrate and personalise in one answer. Same model, same grounding checks, same 8 s budget. It runs twice: once with no retry,
and once with the **same retry-with-feedback** our agents use (so the comparison is not stacked against it).

| Measure | Multi-agent | Single agent | Single agent + retry |
|---|---|---|---|
| Time until the viewer sees the moment (p95) | **4.7 ms** (instant overlay, AI version follows) | 3.2 s | 4.2 s |
| Drafts rejected by the checks / model calls | **1 / 69** | 11 / 41 | 13 / 53 |
| Answers still rejected (wrong text, or blank) | **0** (rewritten, else template) | 11 | 1 |
| Model calls / tokens per match (3 profiles) | 69 / 50K | 41 / 34K | 53 / 45K |

**Why the single agent fails more:** one prompt doing four jobs. It wrote Title Case headlines with unknown capitalised words,
invented a club name ("Rovers"), and claimed "Riverside are on top" against the momentum data.
A retry with feedback fixes most of these (11 → 1), at the cost of more calls and more waiting. Each specialist agent has
one job and a short prompt: 1 rejected draft in 69 calls.

**Cost as the audience grows (tokens per moment, measured):**
the Explainer + Narrator work (~1,900 tokens) is paid **once per moment** and shared by every viewer; each extra
viewer profile only adds a short Personalizer rewrite (~420 tokens). A single agent pays its full prompt for every profile
(~840 tokens, ~1,110 with retries).

| Distinct viewer profiles | Multi-agent | Single agent | Single agent + retry |
|---|---|---|---|
| 1 | 2,325 | 839 (0.4x) | 1,108 (0.5x) |
| 3 | 3,168 | 2,516 (0.8x) | 3,324 (1.0x) |
| 10 | 6,119 | 8,387 (1.4x) | 11,079 (1.8x) |
| 50 | 22,981 | 41,937 (1.8x) | 55,396 (2.4x) |
| 200 | 86,212 | 167,746 (1.9x) | 221,585 (2.6x) |

**Honest reading:**
- A single agent **with retry** gets close on correctness (1 answer still rejected vs 0), but every viewer waits ~4 s for each moment.
- For 1–2 viewer profiles a single agent uses fewer tokens. A single agent that is as correct (with retry) costs more from
  **~3 distinct profiles**, and **2.6x** at broadcast scale.
- Viewers with the same profile cost nothing extra in either design (answer cache). Narrator figures include the two recaps.
  Results vary slightly between runs (the first run: 32% vs 3% rejected drafts, same conclusions).

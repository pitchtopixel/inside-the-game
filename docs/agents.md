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
   - an **out-of-scope claim** about things we have no data on (season, career, injury, transfer, crowd, history…).

   Words beyond these are not machine-checked; prompts forbid invented facts, and an AI groundedness
   evaluator (Microsoft Foundry) is planned for the top moments.
5. **Template fallback** — on any failure the overlay is built from templates; `source: "fallback"` and `fallback_reason` record why.
6. **Metrics** — LLM calls per agent, fallbacks by reason, latency; shown in tests.

Tested failure modes (mock model): unavailable, malformed JSON, invented numbers, invented names,
out-of-scope claims, too slow → all degrade to template overlays, none lost, no invented text on screen.

## Cost control
- Only moments with importance ≥ 45 are explained (~23 per match); low-priority alerts stay template-only.
- Analyst + English needs no personalizer call; personalizer results are cached per (overlay, mode, language, club).
- ~74 LLM calls per full match with 3 viewer profiles.

## Mock model
`LLM_PROVIDER=mock` (default) runs the same agents against an offline model that answers from the fact sheet.
It cannot translate — it marks text as `[es] …`; real translation needs Foundry. Failure modes via `MOCK_MODE`.

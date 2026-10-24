# Model Card: Inside the Game match-intelligence agents

A one-page fact sheet on the AI in this product, written for AI governance, risk and procurement reviewers.
Last updated: 24 Oct 2026.

## 1. Models
| Model | Where | Status | Why |
|---|---|---|---|
| `gpt-4.1-mini` (2025-04-14) | Microsoft Foundry, Global Standard, East US 2 | **Production** | 100% AI answers within the live budget, p95 3.6 s |
| `gpt-5-mini` (2025-08-07) | Microsoft Foundry, Global Standard | Tested, **not used** | Reasoning model; p95 7.5 s, too slow for the 8 s live budget ([model_comparison.md](model_comparison.md)) |
| Mock model | Offline, in code | Development and CI | Same tasks, deterministic, free; simulates failures |

Swapping models is a configuration change (`FOUNDRY_MODEL`), gated by the same test suite.

## 2. Intended use
- **For:** explaining *why* a football moment matters (scoreline, pressure, momentum, chances) and rewording it per viewer
  (fan / analyst / broadcaster, 6 languages, favourite club, player focus).
- **Not for:** betting or odds advice, judging or rating individual players for contracts or selection, medical/injury claims,
  or any decision about a person. Facts the system has no data on (season, career, transfers, crowd) are out of scope and blocked.

## 3. Inputs and outputs
- **Input:** a fact sheet computed by our own stats engine (score, clock, team/player stats, momentum, the moment itself).
  No personal data. The model never sees raw video or anything outside the fact sheet.
- **Output:** JSON with a headline and short text (plus a talking point for broadcasters). Numbers in the output must come from the fact sheet.
- **Agents that call the model:** Explainer (why it matters), Narrator (commentary, recaps), Personalizer (tone, language, focus).
  Ingest, Stats and Moments are deterministic code, not AI.

## 4. Safeguards
1. **Fact sheet only:** the model is given computed facts, nothing else.
2. **Grounding checks** on every answer: rejected if it has a number not in the facts, an unknown name, an out-of-scope topic,
   or a claim that contradicts the data (e.g. "leads" when trailing). Arabic/Hindi answers must keep names in Latin letters.
3. **Retry with feedback:** a rejected answer is retried once, told why it was rejected.
4. **Template fallback:** if still rejected, slow (timeout) or malformed, the viewer sees a factual template instead. Nothing is lost.
5. **Content filter:** Foundry default guardrails (`Microsoft.DefaultV2`) on both deployments: hate, sexual, violence, self-harm, jailbreak.
6. **Monitoring:** every answer logged (`llm_answer` / `llm_fallback`); Azure Monitor alert above 10% fallbacks in 15 min; Foundry tracing.
7. **Access:** no API keys; Entra ID (managed identity + "Foundry User" role).

## 5. Evaluation results
| Test | Result | Source |
|---|---|---|
| Real model, match_001 / match_002 | **100% AI** answers, 0 fallbacks, 15/15 and 15/15 checks | [real_model_findings.md](real_model_findings.md) |
| Real model, 24 Oct (19 checks) | 19/19, 100% AI, 2 drafts blocked and rewritten | [model_comparison.md](model_comparison.md) |
| Grounding rules | contradictions caught 7/7, invented names 2/2, 0 false alarms on the test set | `test_agents.py` checks 21–24 |
| Failure modes (mock) | unavailable, bad JSON, invented numbers/names, off-topic, contradiction, slow → all fall back, 0 lost | `test_agents.py` |
| Slow model (gpt-5-mini) | 44 timeouts → templates, 0 lost overlays, alert fired | [model_comparison.md](model_comparison.md) |
| Foundry Groundedness evaluator (AI judge), all 329 saved answers | **321/329 grounded (98%)**, avg 4.82/5 | [groundedness_report.md](groundedness_report.md) |

Real examples the checks blocked: "Riverside dominate" while the momentum data had Northgate on top; a recap with a number (20) not in the facts.

## 6. Known limits
- **Synthetic data:** matches are simulated (hackathon rule). A real-data adapter exists (`metrica_adapter.py`) but is dev-only.
- **Not every claim is rule-checked:** plain-word claims like "beat two defenders" rely on the prompt live. Offline, the Foundry
  Groundedness evaluator judges every saved answer: 321/329 grounded (98%) ([groundedness_report.md](groundedness_report.md)). It is not run live (cost, latency).
- **Checks are English-first:** number and name checks work in every language; contradiction phrases are checked in English.
- **False alarms happen:** e.g. "pushing to equalise" was once read as "the score is level". The strict reading is the safe one; the viewer sees a template.
- **Latency:** AI text arrives 1–4 s after the moment; the instant template covers the gap.

## 7. Disclosure to users
Every overlay shows whether it is AI-written, a template, or a fallback. See the label policy in [enterprise_readiness.md](enterprise_readiness.md#5-ai-label-policy).

# Enterprise Readiness

How *Inside the Game* gets from hackathon demo to production at a broadcaster, league or club, and the evidence that it is
reliable, transparent and controlled today. Last updated: 24 Oct 2026.

## 1. Who buys it, and why
| Buyer | Problem | What they get |
|---|---|---|
| **Broadcasters** | Graphics teams can't explain every moment live, in every language | On-air captions, a sentence to read, a talking point, seconds after the moment |
| **Leagues / competitions** | One feed, global fans with different languages and knowledge | Personalised second-screen overlays per viewer (fan, analyst, broadcaster; 6 languages) |
| **Clubs** | Fans want *their* team and player, not a generic feed | Favourite-club highlighting, player focus, club-language commentary |

## 2. Adoption path
| Stage | Scope | What changes | Success measure |
|---|---|---|---|
| **0. Today** | Synthetic matches, live demo on Azure | - | 100% AI answers, 0 lost overlays, alert tested |
| **1. Pilot** (4–6 weeks) | 1 partner, replays of real matches | Swap the synthetic feed for the partner's event feed (adapter pattern already used for Metrica data) | Editors rate ≥ 90% of explanations correct; fallback rate < 10% |
| **2. Live pilot** | 1 competition, 1 language, second screen | Raise replicas, partner's own Foundry project (their tenant, their region) | p95 overlay latency, viewer engagement vs control group |
| **3. Rollout** | Whole league, all languages, broadcaster mode | Event Hubs ingest, multi-region, CI/CD to staging then production | Cost per match, minutes watched, languages served |

## 3. Cost per match
From Foundry's model monitor (`docs/img/foundry_model_monitor.png`): 1.76K requests cost ₹56.22, about **₹0.03 per model call** (~524 input + ~77 output tokens).

| Usage | Model calls | Model cost |
|---|---|---|
| Preset viewers (fan, analyst, broadcaster) | **0** (answers prebaked and cached) | ₹0 |
| One custom viewer (other language / club / player) | ~15 | ~₹0.5 |
| Full match, 3 different viewer profiles | ~70 | ~₹2 |

Cost grows with **distinct viewer profiles**, not with viewers: everyone with the same profile shares one cached answer.
Only moments with importance ≥ 45 are explained (~21 per match). Hosting: Container Apps, scale to zero, inside the free grant today.

## 4. Reliability, transparency, controls: the evidence
| Need | What we built | Proof |
|---|---|---|
| **Never lose an overlay** | Instant template first (~7 ms), AI version replaces it; timeout, retry with feedback, template fallback | gpt-5-mini run: 44 timeouts, 160 overlays, 0 lost |
| **No invented facts** | Model sees a fact sheet only; grounding checks on numbers, names, topics, contradictions; Foundry Groundedness evaluator: 98% of saved answers grounded | [real_model_findings.md](real_model_findings.md), [groundedness_report.md](groundedness_report.md), [model_card.md](model_card.md) |
| **Know when quality drops** | Every answer logged; alert above 10% fallbacks in 15 min; Foundry tracing | `docs/img/alert_fired.png`, `docs/img/foundry_tracing.png` |
| **No secrets** | Entra ID: `az login` locally, managed identity + "Foundry User" role in Azure; no `.env` in the image | RUNBOOK §7 |
| **Content safety** | Foundry default guardrails (`Microsoft.DefaultV2`) on every deployment | `docs/img/foundry_guardrails.png` |
| **Safe changes** | GitHub Actions release gate: 4 test suites (stats, stream, agents, app) must pass before the image builds | `.github/workflows/release-gate.yml` |
| **Safe model changes** | A new model is just a deployment name; the same tests decide | [model_comparison.md](model_comparison.md) |
| **Cost control** | Answer cache, importance threshold, budget alerts, scale to zero | Foundry model monitor |
| **Explainability** | Every number traceable to the stats engine; "Verified" box shows which checks passed | UI, [model_card.md](model_card.md) |
| **Global** | 6 languages (en, es, fr, pt, hi, ar); names kept in Latin letters in Arabic/Hindi | `test_agents.py` check 24–25 |

## 5. AI label policy
1. **Every overlay is labelled** with its source: **✦ AI · grounded** (written by the agents and verified), **● LIVE** (instant template,
   AI version on its way) or **● AUTO** (template fallback: the AI answer did not pass the checks, so it was not shown). The badge tooltip explains each one.
2. **AI text is shown only after it passes the grounding checks.** Unverified AI text never reaches the screen.
3. **Numbers never come from the model.** Every number is computed by the stats engine and checked in the answer.
4. **"✔ Verified against the match data"** appears on AI explanations and lists the checks that passed.
5. **Broadcaster mode** captions are AI-assisted; the on-air decision stays with the human producer.
6. Partners can add their own on-screen disclosure (e.g. "AI-assisted commentary") in their graphics style.

## 6. Scale: what is a setting, and what is work
| Today | Why | To scale |
|---|---|---|
| Autoscale 0→3 replicas (5 viewers each), health probes | Free-trial budget | `maxReplicas` in `infra/main.bicep`: one setting. The answer cache moves to Redis so replicas share it |
| One region (East US 2) | Demo | Foundry and Container Apps in the partner's region (data residency) |
| Replay feed over WebSocket | Hackathon | Azure Event Hubs for live ingest |
| Manual deploy from a Bicep template (`what-if` first) | Review step | Release gate already builds the image; add staging + approval to deploy |

## 7. What's next
1. Foundry Groundedness evaluator: offline today (321/329 grounded, 98%, [groundedness_report.md](groundedness_report.md)); next, run it on a sample of live answers each match.
2. Partner pilot on real match data.
3. Staging environment and an approval gate for production deploys.
4. Accessibility pass (ARIA labels, contrast, keyboard).

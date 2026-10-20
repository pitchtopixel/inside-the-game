# Real-Model Findings: Mock → Microsoft Foundry

What happened when the agents met a real model (`gpt-4.1-mini` on Microsoft Foundry), what we measured, and what we changed.
Date: 20–21 Oct 2026 · match_001 · `python test_agents.py --foundry --log`

## Summary
The offline mock model passed every check. The first real run showed only **24%** of explanations from the LLM, and the rest fell back to templates.
**The model was not inventing facts.** Our hallucination check was too strict for how a real model *writes*.
Three small fixes raised it to **95%**, with no relaxing of the rules on invented numbers, names or claims.

| Run | Change | LLM explanations | Fallbacks | LLM calls | p95 latency |
|---|---|---|---|---|---|
| 1 | first real run | 24% | 19 | 97 | 4.7 s |
| 2 | possessives allowed | 62% | 10 | 83 | 3.9 s |
| 3 | sentence case + feedback retry | **95%** | **1** | **72** | **3.5 s** |

Instant (template) overlays stayed at ~7 ms in every run, so the viewer never waited. Every overlay matched the schema, and there were 0 workflow errors.
Logs: `logs/test_agents_foundry_match_001_2026-10-20_2356.txt`, `..._2026-10-21_0000.txt`, `..._2026-10-21_0003.txt`.

## Finding 1: Possessives looked like unknown names
- **Problem:** the name check flags any capitalised word mid-sentence that is not in the facts, so an invented player like "Vargas" is caught.
  The real model writes "It was **Riverside's** third goal", and the check looked up `Riverside's`, not `Riverside`.
- **Evidence:** 18 of 19 fallbacks were `unknown_name`. An offline test reproduced it: "Riverside's" was flagged, "Riverside" was not.
- **Why tests missed it:** the mock model never writes possessives.
- **Fix:** strip a trailing `'s` / `’s` before the lookup (`agents/common.py`). New **check 22** proves invented names are still caught and possessives pass.
- **Result:** 24% → 62%.

## Finding 2: Title Case headlines
- **Problem:** the run now records *which* words were rejected (`rejected_names` in the summary). All 40 were ordinary words from
  Title Case headlines: "Riverside **Dominates Possession Despite Trailing**". Each capital looked like a name.
  None was an invented player or team.
- **Fix:** one rule in the explainer, narrator and personalizer instructions: *sentence case, capitalise only the first word and names.*
  It also matches broadcast-graphics style.
- **Kept strict:** we did not add common words to an allow-list. That would be endless and could let real inventions through.

## Finding 3: A blind retry wastes the second chance
- **Problem:** on rejection, the agent retried with the same prompt and often made the same mistake.
- **Fix:** the retry now carries **feedback** on why the answer was rejected, e.g.
  *"Your previous answer was rejected - capitalised words that are not names in the facts: ['Despite'] - use only those names, sentence case. Rewrite it using only the facts."*
  This works for every check: invented numbers, unknown names, out-of-scope topics and contradictions (`grounding_feedback` in `agents/common.py`).
- **Result (with Finding 2):** 62% → **95%**, 0 rejected names, and **fewer** LLM calls (83 → 72), because answers now pass first time more often.

## Final results (21 Oct, after all fixes)
| Match | LLM explanations | Fallbacks | Answers rejected then fixed by feedback | LLM calls | p95 LLM / instant | Checks |
|---|---|---|---|---|---|---|
| match_001 | **100%** | 0 | 3 | 71 | 3.1 s / 4 ms | **15/15** |
| match_002 | **100%** | 0 | 2 | 100 | 4.6 s / 3.7 ms | **15/15** |

Logs: `logs/test_agents_foundry_match_001_2026-10-21_0015.txt`, `logs/test_agents_foundry_match_002_2026-10-21_0013.txt`.
Check 3 now asks for 100% with the mock and **≥ 90%** with a real model; the rest may be safe template fallbacks.
Results vary slightly between runs (run 3 on match_001 had 1 fallback, 95%).

## What the checks blocked (real examples, match_001)
Every rejected answer is now printed by `test_agents.py` with the reason and the text. All three were rewritten correctly on the retry:

| Agent | Model wrote | Check said | Verdict |
|---|---|---|---|
| narrator | "Riverside **dominate** 2-0, but Northgate's Sosa picks up a yellow card…" | Riverside are not on top (the momentum data had Northgate on top) | **Real catch:** the claim contradicted the data |
| personalizer | "Arribas' early second goal puts **Riverside in control**" | Riverside are not on top | Borderline: true of the scoreline, not of the momentum data. The strict reading is the safe one |
| narrator | "Northgate… pressing hard **to equalize**… trailing 1-0" | the score is not level | **False alarm:** "to equalize" is an aim, not a claim the score is level |

On match_002, "El" (Spanish article) and "Scorer" (Title Case) were rejected, then fixed on retry.

## What is left
- The "to equalize" false alarm: the level-score rule should ignore "to equalise / equalize" (an aim). It is harmless today, because the retry fixed it.
- Rules are English-only. Translated text ("El") can trip the name check; the retry covers it.
- Watch: one rejected draft wrote the team name in lower case ("northgate surges"), possibly from over-applying the sentence-case rule. It was not shown on screen; keep an eye on it in the UI.

## Talking points for judges
1. **"We test against a real model, not just a mock."** The mock passed everything; the real model exposed issues the mock could not. We measured them and fixed them.
2. **"Strict by design, tuned by evidence."** We never loosened the rules on invented facts. We fixed false alarms only after logging exactly which words were rejected.
3. **"Agents that correct themselves."** When the grounding check rejects an answer, it tells the agent why, and the agent rewrites it. That took us from 62% to 95–100% and *reduced* calls.
   Example: the narrator wrote "Riverside dominate" when the data had Northgate on top. It was blocked, given feedback, and rewritten. The viewer never saw it.
4. **"The viewer never waits and never sees an unchecked claim."** Template overlays appear in ~7 ms; AI text replaces them only after it passes every check.

## Honest limits
- The checks are rule-based and written for English; translated text relies on the personalizer keeping names exactly as given.
- Words starting a sentence are not name-checked (they are capitalised anyway).
- Results are from two matches and one model; the numbers vary slightly between runs.

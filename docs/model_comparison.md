# Foundry: Model Swap and Tracing

Two pieces of evidence that Microsoft Foundry speeds up the work: one setting swaps the model, and one setting turns on tracing.
Date: 24 Oct 2026 · match_001 · `python test_agents.py --foundry --model <deployment> --log` with `TRACING=1`

## 1. Model swap: same agents, a different model, no code change
Both models are deployed in the same Foundry project. The only difference between the runs is `--model` (it sets `FOUNDRY_MODEL`).

| Model | Checks | LLM explanations | Fallbacks | LLM calls (main run) | p95 LLM latency | Instant overlays p95 | Workflow errors |
|---|---|---|---|---|---|---|---|
| `gpt-4.1-mini` | **19/19 PASS** | **100%** | 0 | 70 | **3.6 s** | 6.8 ms | 0 |
| `gpt-5-mini` (reasoning) | 16/19 | 0% | 47 (44 timeouts) | 121 | 7.5 s | 6.2 ms | 0 |

Logs: `logs/test_agents_foundry_gpt-4.1-mini_match_001_2026-10-24_2008.txt`, `logs/test_agents_foundry_gpt-5-mini_match_001_2026-10-24_2013.txt`.

**What it shows**
- **Swap time:** deploying a second model took 1 CLI command (`az cognitiveservices account deployment create`), and running it took 1 flag. Agents, prompts and checks did not change.
- **Data-driven choice:** `gpt-5-mini` reasons before answering, so it is slower (p95 7.5 s). Most calls hit our 8 s live timeout. `gpt-4.1-mini` stays the production model.
- **Safety net held:** with the slow model, every viewer still got every overlay. Templates took over (160 overlays, 0 bad, 0 workflow errors, instant p95 6 ms).
  Swapping in a model that does not fit cannot break the live experience.
- **Quota reality (free trial):** `gpt-4.1-nano` had 0 quota and `gpt-4o-mini` is retired, so `gpt-5-mini` was the model with quota available.

## 2. Tracing: inside every agent, no logging code
`TRACING=1` → `llm.enable_tracing()` → `FoundryChatClient.configure_azure_monitor()`. It gets the Application Insights
connection string from the Foundry project itself (no new secret). Agent Framework then sends OpenTelemetry spans for every
workflow, agent and model call. `TRACING_CONTENT=1` also records prompts and answers (synthetic data, demo only).

What one run shows (App Insights `inside-the-game-resource-jayak-appinsights`, span names):
`workflow.build` → `workflow.run` → `executor.process explainer|narrator|personalizer` → `invoke_agent …` → `chat gpt-4.1-mini` → `POST …/openai/v1/responses`

Per model call (from the traces, KQL below):

| Model | p50 call | p95 call | Input tokens / call | Output tokens / call |
|---|---|---|---|---|
| `gpt-4.1-mini` | 1.6 s | 3.6 s | ~500 | ~75 |
| `gpt-5-mini` | 8.0 s (cut at timeout) | 8.0 s | n/a (cancelled calls have no usage) | n/a |

Azure Monitor samples traces (about 5 per second by default), so take call **counts** from the test logs and use the traces for per-call detail.

```kusto
dependencies | where name startswith "chat"
| summarize calls=count(), tin=avg(toint(customDimensions['gen_ai.usage.input_tokens'])),
            tout=avg(toint(customDimensions['gen_ai.usage.output_tokens'])),
            p50=percentile(duration,50), p95=percentile(duration,95) by name
```

## 3. Monitoring: an alert when quality drops
A slow or failing model does not break the app (templates take over), but viewers get plainer text. Someone must notice.
1. **Signal:** every model answer logs one record, `llm_answer` or `llm_fallback` (agent, reason, model) (`agents/common.py`).
   With `TRACING=1` these reach the `traces` table in App Insights.
2. **Alert rule** `itg-llm-fallback-rate` (Azure Monitor log alert, rg `rg-jayakarayil123-3358`): every 15 min, fires when more than **10%**
   of model answers in the last 15 min were replaced by templates (at least 10 answers). Severity 2, auto-resolves.
3. **Notify:** action group `itg-alerts` → email to the Azure account owner.

```kusto
traces | where message in ('llm_answer','llm_fallback')
| summarize calls=count(), fallbacks=countif(message=='llm_fallback') | where calls >= 10
| extend fallback_pct=100.0*fallbacks/calls
```

**Test (24 Oct, 20:39):** the `gpt-5-mini` run logged 187 answers, 161 fallbacks → **86.1%** → alert fired.
Log: `logs/test_agents_foundry_gpt-5-mini_match_001_2026-10-24_2039.txt`. The `gpt-4.1-mini` baseline is 0% (no alert).

## Screenshots (for the README / video)
| File | What to capture |
|---|---|
| `docs/img/foundry_tracing.png` | Foundry portal → project → **Tracing**: one `workflow.run` expanded (Explainer → Narrator → Personalizer → `chat gpt-4.1-mini`) |
| `docs/img/foundry_trace_detail.png` | One `chat` span: tokens, latency, prompt and answer (run with `TRACING_CONTENT=1`) |
| `docs/img/foundry_models.png` | Foundry portal → **Models + endpoints**: `gpt-4.1-mini` and `gpt-5-mini` side by side |
| `docs/img/alert_fired.png` | Azure portal → **Monitor → Alerts**: `itg-llm-fallback-rate` fired (or the alert email) |
| `docs/img/foundry_model_monitor.png` | Foundry → Models → `gpt-4.1-mini` → **Monitor**: requests, tokens, estimated cost (no code) |

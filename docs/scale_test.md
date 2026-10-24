# Scale Test: Azure Container Apps Autoscale

How the live app behaves as viewers arrive, measured on Azure, not estimated.
Date: 24 Oct 2026, 23:54 IST · `python load_test.py --log` · log: `logs/load_test_2026-10-24_2354.txt`

## Setup
- **App:** Azure Container Apps, revision `bicep2`, 0.5 vCPU / 1 GiB per replica, deployed from `infra/main.bicep`.
- **Scale rule:** HTTP, add a replica at 4 concurrent viewers; **0 → 3 replicas**; each replica serves up to 5 viewers (`busy` above that).
- **Health probes:** liveness and readiness on `/`.
- **Load:** a ramp of 5 → 10 → 15 → 20 viewers, 90 s per step, each watching its own replay at 20× speed with a preset profile
  (answers from the prebaked cache, so **0 model calls**). A viewer told `busy` retries every 5 s, like a person pressing reload.
- **Start:** cold, **0 replicas** (scaled to zero).

## Timeline
| Time | Viewers | Watching | Waiting | Replicas |
|---|---|---|---|---|
| 6 s | 5 | 0 | 5 | **1** (cold start) |
| 25 s | 5 | 0 | 5 | **2** |
| 44 s | 5 | 5 | 0 | 2 |
| 100 s | 10 | 8 | 2 | 2 |
| 118 s | 10 | 10 | 0 | **3** |
| 193 s | 15 | 12 | 3 | 3 |
| 211 s | 15 | **15** | 0 | 3 |
| 284–358 s | 20 | 15 | 5 | 3 (max) |

After the first run the app scaled back from 3 to **0 replicas** by itself within about 6 minutes (no viewers, no cost).

## Results
| Measure | Value |
|---|---|
| Viewers served at full capacity (3 replicas × 5) | **15 of 15** |
| Over capacity (20 viewers) | 5 wait politely with `busy`; nobody is dropped |
| Dropped while watching | **0** |
| Instant overlay latency p95 per viewer, under load | **10 ms** median, 16 ms worst |
| Time to get in: median / worst | 15.7 s / 35 s (cold start + waiting for a new replica) |
| Overlays delivered | 606 |
| Model calls / cache hits | **0** / 510 |

An earlier run starting from 3 warm replicas (`logs/load_test_2026-10-24_2342.txt`) gave the same picture: 15 of 15,
0 dropped, instant p95 9.7 ms, median time to get in 9.4 s.

## What it shows
1. **Elastic:** replicas follow the viewers (0 → 1 → 2 → 3 → 0). We pay only while someone watches.
2. **Latency does not degrade:** the live path (Ingest → Stats → Moments → instant overlay) stays around 10 ms with 15 replays running.
3. **Graceful at the limit:** above capacity, viewers are told to wait. The app does not slow down for the viewers already watching.
4. **Cost does not grow with viewers:** preset viewers reuse cached, verified answers (0 model calls for 15 viewers).

## Limits and next steps
- **Start-up wait:** a new replica takes about 20–40 s to be ready, so a sudden crowd waits that long. Fixes: `minReplicas: 1`
  (always warm, small cost) or a lower scale-out threshold.
- **Capacity is a budget setting:** `maxReplicas: 3` in `infra/main.bicep`. More replicas means more viewers.
- **Per-replica cache:** each replica keeps its own cache of custom-viewer answers; a shared cache (Redis) would avoid repeat model calls.

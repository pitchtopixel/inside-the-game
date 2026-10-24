"""
Load test: how the live app scales with concurrent viewers (Azure Container Apps HTTP autoscale, 0-3 replicas, 5 viewers each).

Ramps up viewers in steps (default 5 -> 10 -> 15 -> 20, 90 s each) on the live app. Every viewer uses a preset profile,
so the answers come from the prebaked cache (0 model calls, no Foundry cost). A viewer told "busy" (replica full) retries
every 5 s, like a person pressing reload. Replicas are counted every 15 s with the Azure CLI.

    python load_test.py --log                     # live app (needs `az login` for the replica count)
    python load_test.py --steps 2 4 --step-s 30   # quick try
    python load_test.py --url ws://127.0.0.1:8600/ws --no-replicas   # against a local `python app.py`

Prints a timeline (viewers watching / waiting, replicas) and a summary per step.
"""

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time

import websockets

from testlog import start_log

sys.stdout.reconfigure(encoding="utf-8")
LIVE = "wss://inside-the-game.delightfulsand-21c23dc9.eastus2.azurecontainerapps.io/ws"
RG, APP = "rg-jayakarayil123-3358", "inside-the-game"
RETRY_S = 5


class Viewer:
    def __init__(self, n, step, url, speed, t0):
        self.n, self.step, self.url, self.speed, self.t0 = n, step, url, speed, t0
        self.busy = 0                      # "busy" answers before getting in
        self.connect_s = None              # from first try to watching (incl. busy retries)
        self.first_overlay_s = None        # from pressing play to the first overlay
        self.overlays = 0
        self.model_calls = 0               # this viewer's replay: model calls (presets: 0)
        self.cache_hits = 0
        self.instant_ms = None             # server: instant overlay latency p95 for this replay
        self.state = "waiting"             # waiting -> watching -> done | dropped | gave_up
        self.error = None

    async def run(self, stop, deadline):
        start = time.monotonic()
        while not stop.is_set():
            try:
                async with websockets.connect(self.url, max_size=None, open_timeout=60) as ws:
                    meta = json.loads(await asyncio.wait_for(ws.recv(), 60))
                    if meta.get("kind") == "busy":
                        self.busy += 1
                        raise ConnectionRefusedError("busy")
                    self.connect_s = time.monotonic() - start
                    self.state = "watching"
                    presets = meta["presets"]
                    profile = presets[self.n % len(presets)]["profile"]
                    await ws.send(json.dumps({"cmd": "set_profiles", "profiles": [profile]}))
                    await ws.send(json.dumps({"cmd": "speed", "value": self.speed}))
                    await ws.send(json.dumps({"cmd": "play"}))
                    played = time.monotonic()
                    while not stop.is_set():
                        try:
                            msg = json.loads(await asyncio.wait_for(ws.recv(), 1))
                        except asyncio.TimeoutError:
                            continue
                        if msg["kind"] == "overlays" and msg.get("items"):
                            self.overlays += len(msg["items"])
                            if self.first_overlay_s is None:
                                self.first_overlay_s = time.monotonic() - played
                        elif msg["kind"] == "snapshot":
                            ag = msg.get("agents", {})
                            self.model_calls = sum(ag.get("llm_calls", {}).values())
                            self.cache_hits = ag.get("cache_hits", 0)
                            self.instant_ms = ag.get("instant_latency_p95_ms")
                        elif msg["kind"] == "end":
                            await ws.send(json.dumps({"cmd": "restart"}))
                            await ws.send(json.dumps({"cmd": "play"}))
                    self.state = "done"
                    return
            except ConnectionRefusedError:
                pass
            except Exception as e:  # dropped connection or failed handshake
                if self.state == "watching":
                    self.state, self.error = "dropped", repr(e)[:120]
                    return
                self.error = repr(e)[:120]
            if time.monotonic() > deadline:
                self.state = "gave_up"
                return
            await asyncio.sleep(RETRY_S)


def replica_count():
    az = shutil.which("az") or shutil.which("az.cmd")
    try:
        out = subprocess.run([az, "containerapp", "replica", "list", "-n", APP, "-g", RG, "-o", "json"],
                             capture_output=True, text=True, timeout=60).stdout
        return len(json.loads(out))  # counted here: a JMESPath query breaks Windows cmd quoting
    except Exception:
        return None


async def amain(args):
    stop, viewers, tasks, timeline = asyncio.Event(), [], [], []
    t0 = time.monotonic()
    replicas = {"now": None}

    async def poll():
        while not stop.is_set():
            if not args.no_replicas:
                replicas["now"] = await asyncio.to_thread(replica_count)
            watching = sum(v.state == "watching" for v in viewers)
            waiting = sum(v.state == "waiting" for v in viewers)
            row = (round(time.monotonic() - t0), len(viewers), watching, waiting, replicas["now"])
            timeline.append(row)
            print(f"  t={row[0]:>4}s  viewers {row[1]:>2}  watching {row[2]:>2}  waiting {row[3]:>2}  replicas {row[4]}")
            try:
                await asyncio.wait_for(stop.wait(), 15)
            except asyncio.TimeoutError:
                pass

    print(f"url {args.url} · steps {args.steps} · {args.step_s}s each · speed {args.speed}")
    if not args.no_replicas:
        print(f"replicas before start: {replica_count()}")
    poller = asyncio.create_task(poll())
    step_end = {}
    for i, target in enumerate(args.steps):
        while len(viewers) < target:
            v = Viewer(len(viewers), i, args.url, args.speed, t0)
            viewers.append(v)
            deadline = time.monotonic() + args.step_s * (len(args.steps) - i) + 60
            tasks.append(asyncio.create_task(v.run(stop, deadline)))
        await asyncio.sleep(args.step_s)
        step_end[target] = (sum(v.state == "watching" for v in viewers), sum(v.state == "waiting" for v in viewers),
                            replicas["now"])
    stop.set()
    await asyncio.gather(*tasks, poller)

    print("\n== Per step (at the end of each step) ==")
    print("| Viewers | Watching | Still waiting | Replicas |")
    print("|---|---|---|---|")
    for target, (w, q, r) in step_end.items():
        print(f"| {target} | {w} | {q} | {r} |")

    got_in = [v for v in viewers if v.connect_s is not None]
    waits = sorted(v.connect_s for v in got_in)
    firsts = sorted(v.first_overlay_s for v in got_in if v.first_overlay_s is not None)
    pct = lambda xs, q: xs[int(q * (len(xs) - 1))] if xs else 0
    print("\n== Summary ==")
    print("| Measure | Value |")
    print("|---|---|")
    print(f"| Viewers who got in | {len(got_in)} of {len(viewers)} |")
    print(f"| Viewers who had to retry (\"busy\") | {sum(v.busy > 0 for v in viewers)} ({sum(v.busy for v in viewers)} retries) |")
    print(f"| Time to get in: median / p95 / max | {pct(waits, .5):.1f} s / {pct(waits, .95):.1f} s / {max(waits, default=0):.1f} s |")
    print(f"| First overlay after play (first moment at 2:26 match time) | {pct(firsts, .5):.1f} s median |")
    inst = sorted(v.instant_ms for v in got_in if v.instant_ms is not None)
    print(f"| Instant overlay latency p95 (server, per viewer): median / worst | {pct(inst, .5):.1f} ms / {max(inst, default=0):.1f} ms |")
    print(f"| Dropped while watching | {sum(v.state == 'dropped' for v in viewers)} |")
    print(f"| Gave up (never got in) | {sum(v.state == 'gave_up' for v in viewers)} |")
    print(f"| Overlays received | {sum(v.overlays for v in viewers)} |")
    print(f"| Model calls (all viewers) / cache hits | {sum(v.model_calls for v in viewers)} / {sum(v.cache_hits for v in viewers)} |")
    print(f"| Max replicas seen | {max((r[4] for r in timeline if r[4] is not None), default=None)} |")
    errors = {v.error for v in viewers if v.error and v.state in ('dropped', 'gave_up')}
    if errors:
        print(f"\nerrors: {errors}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=LIVE)
    ap.add_argument("--steps", type=int, nargs="+", default=[5, 10, 15, 20], help="total viewers at each step")
    ap.add_argument("--step-s", type=int, default=90, help="seconds per step")
    ap.add_argument("--speed", type=float, default=20.0, help="replay speed (1 = real time)")
    ap.add_argument("--no-replicas", action="store_true", help="skip the Azure replica count (local runs)")
    ap.add_argument("--log", action="store_true", help="also save output to logs/")
    args = ap.parse_args()
    if args.log:
        start_log("load_test")
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()

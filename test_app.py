"""
End-to-end test of the app: starts app.py on a test port, connects two viewers with different
profiles, plays the whole match at 600x and checks what each viewer receives.

    python test_app.py [--log]
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections import Counter

import websockets

from testlog import start_log

sys.stdout.reconfigure(encoding="utf-8")
PORT = 8601
URL = f"ws://127.0.0.1:{PORT}/ws"
ANALYST = {"mode": "analyst", "language": "en"}
FAN = {"mode": "fan", "language": "es", "club": "NGR", "metric": "possession"}
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


async def viewer(profile, inbox, ready):
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.send(json.dumps({"cmd": "set_profiles", "profiles": [profile]}))
        ready.set()
        try:
            async for raw in ws:
                inbox.append(json.loads(raw))
        except websockets.ConnectionClosed:
            pass


async def controller(inboxes):
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.send(json.dumps({"cmd": "speed", "value": 600}))
        await ws.send(json.dumps({"cmd": "restart"}))
        await asyncio.sleep(0.5)
        for box in inboxes:
            box.clear()                      # count from kick-off only
        await ws.send(json.dumps({"cmd": "play"}))
        t0 = time.time()
        async for raw in ws:
            if json.loads(raw)["kind"] == "end" or time.time() - t0 > 60:
                break
        await asyncio.sleep(2.0)             # let background agents finish (recaps)
        await ws.send(json.dumps({"cmd": "seek", "minute": 60}))
        await asyncio.sleep(1.5)


async def run():
    a, f = [], []
    ready_a, ready_f = asyncio.Event(), asyncio.Event()
    tasks = [asyncio.create_task(viewer(ANALYST, a, ready_a)), asyncio.create_task(viewer(FAN, f, ready_f))]
    await ready_a.wait()
    await ready_f.wait()
    await asyncio.sleep(0.3)
    await controller([a, f])
    for t in tasks:
        t.cancel()
    return a, f


def overlays(box):
    return [o for m in box if m["kind"] == "overlays" for o in m["items"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    if ap.parse_args().log:
        start_log("test_app")
    env = {**os.environ, "LLM_PROVIDER": "mock"}
    server = subprocess.Popen([sys.executable, "app.py", "--port", str(PORT), "--mock-latency", "0"], env=env)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        a, f = asyncio.run(run())
    finally:
        server.terminate()

    total = json.load(open(os.path.join("data", "synthetic", "match_001.json"), encoding="utf-8"))["events"]
    kinds_a = Counter(m["kind"] for m in a)
    oa, of = overlays(a), overlays(f)
    pid_a = {o["profile_id"] for o in oa}
    pid_f = {o["profile_id"] for o in of}
    print(f"\n== Viewer messages ==\nanalyst: {dict(kinds_a)}\nfan: {dict(Counter(m['kind'] for m in f))}")

    print("\n== Checks ==")
    events = sum(m["kind"] == "event" for m in a)
    check("1. viewer receives every match event", events == len(total), f"{events}/{len(total)}")
    check("2. live stats snapshots stream (>= 100)", kinds_a["snapshot"] >= 100, str(kinds_a["snapshot"]))
    check("3. each viewer only gets its own profile's overlays", len(pid_a) == 1 and len(pid_f) == 1 and pid_a != pid_f,
          f"{pid_a} / {pid_f}")
    ma = [o for o in oa if o["kind"] == "moment"]
    mf = [o for o in of if o["kind"] == "moment"]
    check("4. fan sees fewer moments than analyst", 0 < len({o['overlay_id'] for o in mf}) < len({o['overlay_id'] for o in ma}),
          f"fan {len({o['overlay_id'] for o in mf})} vs analyst {len({o['overlay_id'] for o in ma})}")
    upgraded = {o["overlay_id"] for o in ma if o["version"] == 1} & {o["overlay_id"] for o in ma if o["version"] == 2}
    check("5. template overlays upgraded to AI versions (v1 -> v2)", len(upgraded) >= 15, f"{len(upgraded)} upgraded")
    check("6. fan overlays personalized (Spanish, club, metric)",
          all(o["language"] == "es" for o in mf if o["version"] == 2) and any(o["highlight"]["your_club"] for o in mf)
          and all(o.get("metric") for o in mf), f"{len(mf)} overlays")
    recaps = Counter((o["recap_type"]) for o in oa + of if o["kind"] == "recap")
    check("7. both viewers get half-time and full-time recaps", recaps == {"half_time": 2, "full_time": 2}, str(dict(recaps)))
    tickers = sum(o["kind"] == "metric_ticker" for o in of)
    check("8. metric ticker reaches the fan viewer", tickers >= 18, str(tickers))
    items = sum(m["kind"] == "timeline_item" for m in a)
    check("9. timeline items stream to viewers", items >= 30, str(items))
    resets = [i for i, m in enumerate(a) if m["kind"] == "reset"]
    after = a[resets[-1]:] if resets else []
    tl = next((m for m in after if m["kind"] == "timeline"), None)
    snap = next((m for m in after if m["kind"] == "snapshot"), None)
    ok_seek = bool(tl and snap and 55 <= minute(snap["clock"]) <= 60 and
                   tl["items"] and max(minute(o["clock"]) for o in tl["items"]) <= 60)
    check("10. seek to 60' rebuilds state and timeline", ok_seek,
          f"snapshot {snap and snap['clock']}, timeline {tl and len(tl['items'])} moments")
    print(f"\n{sum(results)}/{len(results)} checks passed -> {'PASS' if all(results) else 'FAIL'}")
    raise SystemExit(0 if all(results) else 1)


def minute(clock):
    return int(clock.split(":")[0].split("+")[0])


if __name__ == "__main__":
    main()

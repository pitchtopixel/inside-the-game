"""
Terminal client for the live streamer.

Watch:  python test_stream.py                 (prints key events as they arrive)
Check:  python test_stream.py --check         (streams at 600x and verifies count, order, timing)
Server must be running: python stream_server.py
"""

import argparse
import asyncio
import json
import time

import websockets

from testlog import start_log

KEY_TYPES = {"goal", "shot", "card", "substitution", "kick_off", "period_end", "match_end", "sprint", "save"}


async def watch(url, speed):
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"cmd": "speed", "value": speed}))
        await ws.send(json.dumps({"cmd": "play"}))
        async for raw in ws:
            m = json.loads(raw)
            if m["kind"] == "event" and m["event"]["type"] in KEY_TYPES:
                e = m["event"]
                print(f"{e['clock']:>8}  {e['team'] or '':4} {e['type']:<13} {e['player_name'] or ''} "
                      f"{e['outcome'] or ''}")
            elif m["kind"] == "end":
                print("== stream ended ==")
                return


async def check(url, speed=600.0):
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"cmd": "speed", "value": speed}))
        await ws.send(json.dumps({"cmd": "restart"}))
        received, total = [], None
        async for raw in ws:  # drain until the restart's catchup arrives
            m = json.loads(raw)
            if m["kind"] == "catchup" and m.get("reset") and not m["events"]:
                break
        await ws.send(json.dumps({"cmd": "play"}))
        start = time.monotonic()
        async for raw in ws:
            m = json.loads(raw)
            if m["kind"] == "status":
                total = m["total"]
            elif m["kind"] == "event":
                received.append((time.monotonic() - start, m["event"]))
            elif m["kind"] == "end":
                break

    times = [e["elapsed_s"] for _, e in received]
    in_order = all(a <= b for a, b in zip(times, times[1:]))
    # Real arrival time vs expected (match time / speed); allow a few ticks of jitter.
    drift = max(abs(t - (e["elapsed_s"] - times[0]) / speed) for t, e in received)
    print(f"received {len(received)}/{total}  in_order={in_order}  max_timing_drift={drift:.2f}s")
    ok = len(received) == total and in_order and drift < 0.5
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8500/ws")
    ap.add_argument("--speed", type=float, default=60.0)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--log", action="store_true", help="also save output to logs/")
    args = ap.parse_args()
    if args.log:
        start_log("test_stream_check" if args.check else "test_stream_watch")
    if args.check:
        raise SystemExit(0 if asyncio.run(check(args.url)) else 1)
    asyncio.run(watch(args.url, args.speed))


if __name__ == "__main__":
    main()

"""
Live event streamer: replays a synthetic match over WebSocket as if it were happening now.

Run:   python stream_server.py --speed 60 --autoplay
Open:  http://localhost:8500  (test page)   ws://localhost:8500/ws  (stream)

Server -> client messages (JSON):
  {"kind": "meta", "match": {...}, "teams": {...}}          on connect
  {"kind": "catchup", "events": [...]}                       on connect, events already played
  {"kind": "event", "event": {...}}                          each live event
  {"kind": "status", "playing", "speed", "clock_s", "index", "total"}
  {"kind": "end"}                                            match finished
Client -> server commands (JSON):
  {"cmd": "play"} | {"cmd": "pause"} | {"cmd": "speed", "value": 60}
  {"cmd": "seek", "minute": 70} | {"cmd": "restart"}
"""

import argparse
import asyncio
import bisect
import json
import os
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

TICK_S = 0.05
REQUIRED = {"event_id": str, "type": str, "period": int, "elapsed_s": (int, float), "details": dict}


def validate(events):
    """Drop malformed and duplicate events, sort by match time, return (clean, rejected_count)."""
    clean, seen = [], set()
    for e in events:
        if isinstance(e, dict) and all(isinstance(e.get(k), t) for k, t in REQUIRED.items()) \
                and e["event_id"] not in seen:
            seen.add(e["event_id"])
            clean.append(e)
    clean.sort(key=lambda e: e["elapsed_s"])
    return clean, len(events) - len(clean)


class Replayer:
    def __init__(self, path, speed):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.meta = {"match": data["match"], "teams": data["teams"]}
        self.events, rejected = validate(data["events"])
        self.times = [e["elapsed_s"] for e in self.events]
        print(f"loaded {len(self.events)} events ({rejected} rejected) from {path}")
        self.speed, self.playing = speed, False
        self.clock, self.index = 0.0, 0  # match clock (s) and next event to send
        self.clients = set()

    def status(self):
        return {"kind": "status", "playing": self.playing, "speed": self.speed, "clock_s": round(self.clock, 1),
                "index": self.index, "total": len(self.events)}

    async def broadcast(self, msg):
        text = json.dumps(msg, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                self.clients.discard(ws)

    async def handle(self, cmd):
        c = cmd.get("cmd")
        if c == "play":
            self.playing = self.index < len(self.events)
        elif c == "pause":
            self.playing = False
        elif c == "speed":
            self.speed = max(0.1, float(cmd.get("value", 1)))
        elif c in ("seek", "restart"):
            minute = 0 if c == "restart" else int(cmd.get("minute", 0))
            target = next((e["elapsed_s"] for e in self.events if e.get("minute", 0) >= minute), self.times[-1])
            self.index = bisect.bisect_left(self.times, target)
            self.clock = target
            await self.broadcast({"kind": "catchup", "reset": True, "events": self.events[:self.index]})
        await self.broadcast(self.status())

    async def run(self):
        last = time.monotonic()
        while True:
            await asyncio.sleep(TICK_S)
            now = time.monotonic()
            dt, last = now - last, now
            if not self.playing:
                continue
            self.clock += dt * self.speed
            while self.index < len(self.events) and self.times[self.index] <= self.clock:
                await self.broadcast({"kind": "event", "event": self.events[self.index]})
                self.index += 1
            if self.index >= len(self.events):
                self.playing = False
                await self.broadcast({"kind": "end"})
                await self.broadcast(self.status())


def create_app(replayer, autoplay):
    @asynccontextmanager
    async def lifespan(app):
        replayer.playing = autoplay
        task = asyncio.create_task(replayer.run())
        yield
        task.cancel()

    app = FastAPI(title="Match Event Streamer", lifespan=lifespan)

    @app.get("/")
    def page():
        return FileResponse(os.path.join("static", "stream_test.html"))

    @app.get("/match")
    def match():
        return replayer.meta

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text(json.dumps({"kind": "meta", **replayer.meta}, ensure_ascii=False))
        await socket.send_text(json.dumps({"kind": "catchup", "reset": True,
                                           "events": replayer.events[:replayer.index]}, ensure_ascii=False))
        await socket.send_text(json.dumps(replayer.status()))
        replayer.clients.add(socket)
        try:
            while True:
                await replayer.handle(json.loads(await socket.receive_text()))
        except WebSocketDisconnect:
            replayer.clients.discard(socket)

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", default=os.path.join("data", "synthetic", "match_001.json"))
    ap.add_argument("--speed", type=float, default=10.0, help="1 = real time, 60 = one match minute per second")
    ap.add_argument("--port", type=int, default=8500)
    ap.add_argument("--autoplay", action="store_true", help="start streaming immediately")
    args = ap.parse_args()
    app = create_app(Replayer(args.match, args.speed), args.autoplay)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

"""
Match Intelligence app: replays a synthetic match through the multi-agent pipeline and pushes
personalized overlays, live stats and key moments to every connected viewer.

Run:   python app.py --speed 20            ->  http://localhost:8600
       (LLM_PROVIDER in .env: mock by default; --mock-latency makes the template -> AI upgrade visible)

Server -> client (JSON "kind"):
  meta        teams, rosters, profile options and presets         (on connect)
  status      playing, speed, clock_s, index, total
  event       compact match event (for the pitch view)
  snapshot    score, team stats, momentum, rhythm + agent metrics  (every 5 match-seconds and on alerts)
  overlays    personalized overlays for this viewer's profiles
  timeline    all key moments so far (on connect / reset); timeline_item for each new one
  profiles    profile ids assigned to this viewer's panes
  reset       the replay jumped (seek / restart)
  end         match finished
Client -> server: play | pause | speed {value} | seek {minute} | restart | set_profiles {profiles: [...]}
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
from fastapi.staticfiles import StaticFiles

from agents.personalizer import LANGUAGES, PLAYER_METRICS, TEAM_METRICS
from llm import get_chat_client
from orchestrator import Orchestrator
from stream_server import validate

TICK_S = 0.05
SNAPSHOT_EVERY_S = 5.0
TIMELINE = {"profile_id": "_timeline", "mode": "analyst", "language": "en"}
EVENT_KEYS = ("event_id", "type", "period", "clock", "elapsed_s", "team", "player_name", "x", "y", "end_x", "end_y",
              "outcome")
SNAP_TEAM_KEYS = ("name", "goals", "possession_pct", "shots", "shots_on_target", "xg", "big_chances", "passes",
                  "pass_accuracy_pct", "avg_pass_difficulty", "final_third_passes", "tackles_won", "interceptions",
                  "pressures", "fouls", "yellow_cards", "corners", "fastest_shot_kmh", "avg_ball_speed_kmh")


def profile_id(p):
    return "p-" + "-".join(str(p.get(k) or "_") for k in ("mode", "language", "club", "player", "metric"))


class Session:
    def __init__(self, path, speed, client):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.meta = {"match": data["match"], "teams": data["teams"]}
        self.events, _ = validate(data["events"])
        self.times = [e["elapsed_s"] for e in self.events]
        self.client, self.speed, self.playing = client, speed, False
        self.viewers = {}                       # websocket -> [profile ids, one per pane]
        self.profiles = {TIMELINE["profile_id"]: TIMELINE}
        self.generation, self.index, self.clock, self.last_snap = 0, 0, 0.0, -1e9
        self.orch, self.fast_forwarding = None, False

    # ---------- messaging ----------
    async def send(self, ws, msg):
        try:
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            self.viewers.pop(ws, None)

    async def broadcast(self, msg):
        for ws in list(self.viewers):
            await self.send(ws, msg)

    def status(self):
        return {"kind": "status", "playing": self.playing, "speed": self.speed, "clock_s": round(self.clock, 1),
                "index": self.index, "total": len(self.events)}

    def snapshot_msg(self):
        snap = self.orch.mc.state.snapshot()
        return {"kind": "snapshot", "clock": snap["clock"], "period": snap["period"], "score": snap["score"],
                "elapsed_s": snap["elapsed_s"],
                "teams": {t: {k: s[k] for k in SNAP_TEAM_KEYS} for t, s in snap["teams"].items()},
                "momentum": snap["momentum"], "rhythm": snap["rhythm"], "agents": self.orch.summary()}

    def timeline(self):
        best = {}
        for o in self.orch.outputs:
            if o["profile_id"] == "_timeline" and o["kind"] == "moment":
                if o["version"] >= best.get(o["overlay_id"], {}).get("version", 0):
                    best[o["overlay_id"]] = o
        return sorted(best.values(), key=lambda o: o["elapsed_s"])

    def meta_msg(self):
        teams = {side: {k: t[k] for k in ("team_id", "name", "short_name", "colors", "formation")} |
                 {"players": [{k: p[k] for k in ("player_id", "name", "short_name", "number", "position")}
                              for p in t["players"]]}
                 for side, t in self.meta["teams"].items()}
        home, away = teams["home"]["team_id"], teams["away"]["team_id"]
        star = max(self.meta["teams"]["home"]["players"] + self.meta["teams"]["away"]["players"],
                   key=lambda p: p["attributes"]["shot_skill"])
        presets = [
            {"label": "Analyst · English", "profile": {"mode": "analyst", "language": "en"}},
            {"label": f"Fan · Español · {teams['away']['short_name']}",
             "profile": {"mode": "fan", "language": "es", "club": away, "metric": "possession"}},
            {"label": f"Player focus · {star['short_name']}",
             "profile": {"mode": "fan", "language": "en", "player": star["player_id"], "metric": "xg"}},
            {"label": f"Fan · हिन्दी · {teams['home']['short_name']}", "profile": {"mode": "fan", "language": "hi", "club": home}},
        ]
        return {"kind": "meta", "match": self.meta["match"], "teams": teams, "presets": presets,
                "llm_provider": os.getenv("LLM_PROVIDER", "mock"),
                "options": {"languages": LANGUAGES, "team_metrics": {k: v[0] for k, v in TEAM_METRICS.items()},
                            "player_metrics": {k: v[0] for k, v in PLAYER_METRICS.items()}}}

    # ---------- pipeline ----------
    def _make_output_handler(self, gen):
        async def on_output(overlays):
            if gen != self.generation or self.fast_forwarding:
                return  # stale (before a seek) or replaying history silently
            for ws, pids in list(self.viewers.items()):
                mine = [o for o in overlays if o["profile_id"] in pids]
                if mine:
                    await self.send(ws, {"kind": "overlays", "items": mine})
            for o in overlays:
                if o["profile_id"] == "_timeline" and o["kind"] == "moment":
                    await self.broadcast({"kind": "timeline_item", "item": o})
        return on_output

    async def reset_to(self, index):
        """(Re)build the pipeline and fast-forward to `index` without LLM work."""
        self.generation += 1
        self.fast_forwarding = True
        self.orch = Orchestrator(self.meta, list(self.profiles.values()), client=self.client,
                                 on_output=self._make_output_handler(self.generation))
        self.orch.enrich = False
        for ev in self.events[:index]:
            await self.orch.process(ev)
        self.orch.enrich, self.fast_forwarding = True, False
        self.index = index
        self.clock = self.times[index - 1] if index else 0.0
        self.last_snap = self.clock
        await self.broadcast({"kind": "reset", "clock_s": self.clock})
        await self.broadcast({"kind": "timeline", "items": self.timeline()})
        await self.broadcast(self.snapshot_msg())
        await self.broadcast(self.status())

    async def handle(self, ws, cmd):
        c = cmd.get("cmd")
        if c == "play":
            self.playing = self.index < len(self.events)
        elif c == "pause":
            self.playing = False
        elif c == "speed":
            self.speed = max(0.5, min(600.0, float(cmd.get("value", 1))))
        elif c in ("seek", "restart"):
            minute = 0 if c == "restart" else int(cmd.get("minute", 0))
            target = next((e["elapsed_s"] for e in self.events if e.get("minute", 0) >= minute), self.times[-1])
            await self.reset_to(bisect.bisect_left(self.times, target))
        elif c == "set_profiles":
            ids = []
            for p in cmd.get("profiles", [])[:2]:
                p = {k: p.get(k) or None for k in ("mode", "language", "club", "player", "metric")}
                p["mode"], p["language"] = p["mode"] or "analyst", p["language"] or "en"
                p["profile_id"] = profile_id(p)
                if p["profile_id"] not in self.profiles:
                    self.profiles[p["profile_id"]] = p
                    self.orch.add_profile(p)
                ids.append(p["profile_id"])
            self.viewers[ws] = ids
            await self.send(ws, {"kind": "profiles", "ids": ids})
            return
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
            alerted = False
            while self.index < len(self.events) and self.times[self.index] <= self.clock:
                ev = self.events[self.index]
                self.index += 1
                await self.broadcast({"kind": "event", "event": {k: ev.get(k) for k in EVENT_KEYS}})
                if await self.orch.process(ev):
                    alerted = True
            if alerted or self.clock - self.last_snap >= SNAPSHOT_EVERY_S:
                self.last_snap = self.clock
                await self.broadcast(self.snapshot_msg())
                await self.broadcast(self.status())
            if self.index >= len(self.events):
                self.playing = False
                await self.broadcast({"kind": "end"})
                await self.broadcast(self.status())


def create_app(session, autoplay):
    @asynccontextmanager
    async def lifespan(app):
        await session.reset_to(0)
        session.playing = autoplay
        task = asyncio.create_task(session.run())
        yield
        task.cancel()

    app = FastAPI(title="Inside the Match", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join("static", "index.html"))

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        session.viewers[socket] = []
        await session.send(socket, session.meta_msg())
        await session.send(socket, {"kind": "timeline", "items": session.timeline()})
        await session.send(socket, session.snapshot_msg())
        await session.send(socket, session.status())
        try:
            while True:
                await session.handle(socket, json.loads(await socket.receive_text()))
        except WebSocketDisconnect:
            session.viewers.pop(socket, None)

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", default=os.path.join("data", "synthetic", "match_001.json"))
    ap.add_argument("--speed", type=float, default=20.0, help="1 = real time")
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--autoplay", action="store_true")
    ap.add_argument("--mock-latency", type=float, default=1.0,
                    help="mock model delay in seconds (shows the template -> AI upgrade); ignored for Foundry")
    args = ap.parse_args()
    session = Session(args.match, args.speed, get_chat_client(latency_s=args.mock_latency))
    uvicorn.run(create_app(session, args.autoplay), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

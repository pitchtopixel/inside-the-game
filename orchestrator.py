"""
Orchestrator: coordinates the match-intelligence agents with Microsoft Agent Framework workflows.

Live workflow - every event, no LLM, milliseconds:
    Ingest -> Stats -> Moments -> Narrator (instant) -> Personalizer (instant)   => version-1 overlays
Enrichment workflow - per key moment, in the background, LLM via Microsoft Foundry:
    Explainer -> Narrator (commentary) -> Personalizer (tone / language)          => version-2 overlays
Recap workflow - half-time and full-time:
    Narrator (recap) -> Personalizer

Shared state lives in MatchContext (stats engine, key moments, viewer profiles, LLM metrics).
Every LLM step has a timeout, a retry, JSON + grounding validation and a template fallback,
so a viewer never loses an overlay because a model call failed.
"""

import asyncio
import time
from dataclasses import dataclass

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from agents.common import Metrics, moment_facts
from agents.explainer import Explainer
from agents.moments import MomentsAgent
from agents.narrator import Narrator
from agents.personalizer import Personalizer
from llm import get_chat_client
from stats_engine import MatchState

REQUIRED_FIELDS = {"event_id": str, "type": str, "period": int, "elapsed_s": (int, float)}


# ---------- messages passed between agents ----------
@dataclass
class StatsUpdate:
    event: dict
    alerts: list
    snapshot: dict


@dataclass
class MomentBatch:
    event: dict
    moments: list
    snapshot: dict


@dataclass
class OverlayBatch:
    event: dict
    overlays: list
    snapshot: dict


@dataclass
class EnrichRequest:
    moment: dict
    facts: dict
    snapshot: dict


@dataclass
class Explained:
    moment: dict
    facts: dict
    snapshot: dict
    explanation: dict
    source: str
    reason: str | None


@dataclass
class Narrated:
    overlay: dict
    facts: dict
    snapshot: dict


@dataclass
class RecapRequest:
    kind: str
    snapshot: dict
    key_moments: list
    momentum: list = None


def momentum_blocks(state, first_half_s):
    """Average threat share per 15 minutes of play, for the recap: how momentum moved over the match.
    Added time joins the last block of its half (45+ -> 30-45, 90+ -> 75-90)."""
    blocks = {}
    for now, home_share, _ in state.momentum_timeline:
        b = min(int(now // 900), 2) if now < first_half_s else 3 + min(int((now - first_half_s) // 900), 2)
        blocks.setdefault(b, []).append(home_share)
    out = []
    for b, shares in sorted(blocks.items()):
        home = round(100 * sum(shares) / len(shares))
        out.append({"minutes": f"{b * 15}-{b * 15 + 15}",
                    "threat_share_pct": {state.short[state.home]: home, state.short[state.away]: 100 - home}})
    return out


class MatchContext:
    """Shared state for all agents in one match."""

    def __init__(self, meta, profiles, client, timeout_s):
        self.state = MatchState(meta)
        self.first_half_s = meta["match"]["periods"][0]["duration_s"]
        self.window_s = self.state.th["window_s"]
        self.metrics = Metrics()
        team_names = {t["team_id"]: t.get("short_name", t["team_id"]) for t in meta["teams"].values()}
        self.moments = MomentsAgent()
        self.explainer = Explainer(client, self.metrics, timeout_s)
        self.narrator = Narrator(client, self.metrics, timeout_s)
        player_names = {p["player_id"]: p["name"] for t in meta["teams"].values() for p in t["players"]}
        self.personalizer = Personalizer(client, self.metrics, timeout_s, profiles, team_names, player_names)
        self.seen, self.rejected = set(), 0
        self.schedule = None  # set by Orchestrator: hands work to the background workflows


# ---------- live workflow executors ----------
class IngestExecutor(Executor):
    """Ingest agent: rejects malformed / duplicate events before they reach the stats engine."""

    def __init__(self, mc):
        super().__init__(id="ingest")
        self.mc = mc

    @handler
    async def run(self, event: dict, ctx: WorkflowContext[dict]) -> None:
        ok = isinstance(event, dict) and all(isinstance(event.get(k), t) for k, t in REQUIRED_FIELDS.items())
        if not ok or event["event_id"] in self.mc.seen:
            self.mc.rejected += 1
            return
        self.mc.seen.add(event["event_id"])
        await ctx.send_message(event)


class StatsExecutor(Executor):
    """Stats agent (deterministic): live state, metrics, patterns and alerts."""

    def __init__(self, mc):
        super().__init__(id="stats")
        self.mc = mc

    @handler
    async def run(self, event: dict, ctx: WorkflowContext[StatsUpdate]) -> None:
        alerts = self.mc.state.update(event)
        if alerts or event["type"] in ("physical_update", "period_end"):
            await ctx.send_message(StatsUpdate(event, alerts, self.mc.state.snapshot()))


class MomentsExecutor(Executor):
    """Moments agent: ranks alerts into key moments and hands the important ones to the LLM agents."""

    def __init__(self, mc):
        super().__init__(id="moments")
        self.mc = mc

    @handler
    async def run(self, msg: StatsUpdate, ctx: WorkflowContext[MomentBatch]) -> None:
        moments = [self.mc.moments.assess(a, msg.event, msg.snapshot) for a in msg.alerts]
        for m in moments:
            if m["explain"]:
                self.mc.schedule(EnrichRequest(m, moment_facts(m, msg.snapshot, self.mc.window_s), msg.snapshot))
        if msg.event["type"] == "period_end":
            kind = "half_time" if msg.event["period"] == 1 else "full_time"
            keys = [m for m in self.mc.moments.key_moments if kind == "full_time" or m["period"] == 1]
            self.mc.schedule(RecapRequest(kind, msg.snapshot, keys,
                                          momentum_blocks(self.mc.state, self.mc.first_half_s)))
        await ctx.send_message(MomentBatch(msg.event, moments, msg.snapshot))


class NarratorInstantExecutor(Executor):
    """Narrator agent, instant path: template overlay per moment (no LLM)."""

    def __init__(self, mc):
        super().__init__(id="narrator_instant")
        self.mc = mc

    @handler
    async def run(self, msg: MomentBatch, ctx: WorkflowContext[OverlayBatch]) -> None:
        overlays = [self.mc.narrator.instant(m) for m in msg.moments]
        await ctx.send_message(OverlayBatch(msg.event, overlays, msg.snapshot))


class PersonalizerInstantExecutor(Executor):
    """Personalizer agent, instant path: filter + decorate per viewer, plus player / metric tickers."""

    def __init__(self, mc):
        super().__init__(id="personalizer_instant")
        self.mc = mc

    @handler
    async def run(self, msg: OverlayBatch, ctx: WorkflowContext[None, list]) -> None:
        out = [o for ov in msg.overlays for o in self.mc.personalizer.instant(ov, msg.snapshot)]
        if msg.event["type"] == "physical_update":
            out += self.mc.personalizer.tickers(msg.snapshot)
        if out:
            await ctx.yield_output(out)


# ---------- enrichment / recap workflow executors ----------
class ExplainerExecutor(Executor):
    def __init__(self, mc):
        super().__init__(id="explainer")
        self.mc = mc

    @handler
    async def run(self, req: EnrichRequest, ctx: WorkflowContext[Explained]) -> None:
        expl, source, reason = await self.mc.explainer.explain(req.facts)
        await ctx.send_message(Explained(req.moment, req.facts, req.snapshot, expl, source, reason))


class NarratorExecutor(Executor):
    def __init__(self, mc):
        super().__init__(id="narrator")
        self.mc = mc

    @handler
    async def commentary(self, msg: Explained, ctx: WorkflowContext[Narrated]) -> None:
        ov = await self.mc.narrator.enrich(msg.moment, msg.facts, msg.explanation, msg.source, msg.reason)
        await ctx.send_message(Narrated(ov, msg.facts, msg.snapshot))

    @handler
    async def recap(self, req: RecapRequest, ctx: WorkflowContext[Narrated]) -> None:
        ov, facts = await self.mc.narrator.recap(req.kind, req.snapshot, req.key_moments, self.mc.window_s,
                                                 req.momentum)
        await ctx.send_message(Narrated(ov, facts, req.snapshot))


class PersonalizerExecutor(Executor):
    def __init__(self, mc):
        super().__init__(id="personalizer")
        self.mc = mc

    @handler
    async def run(self, msg: Narrated, ctx: WorkflowContext[None, list]) -> None:
        await ctx.yield_output(list(await self.mc.personalizer.personalize(msg.overlay, msg.snapshot, msg.facts)))


# ---------- orchestrator ----------
class Orchestrator:
    def __init__(self, meta, profiles, client=None, timeout_s=8.0, max_concurrency=4, on_output=None):
        self.mc = MatchContext(meta, profiles, client or get_chat_client(), timeout_s)
        self.mc.schedule = self._schedule
        self.on_output = on_output
        self.enrich = True  # False while fast-forwarding (seek): no LLM work for past moments
        self.outputs, self.instant_latency_s, self.workflow_errors = [], [], 0
        self.sem = asyncio.Semaphore(max_concurrency)
        self.tasks = set()
        ingest, stats, moments = IngestExecutor(self.mc), StatsExecutor(self.mc), MomentsExecutor(self.mc)
        narr, pers = NarratorInstantExecutor(self.mc), PersonalizerInstantExecutor(self.mc)
        self.live = (WorkflowBuilder(name="live", start_executor=ingest, output_from=[pers])
                     .add_chain([ingest, stats, moments, narr, pers]).build())

    def _background_workflow(self, start_with_explainer):
        narr, pers = NarratorExecutor(self.mc), PersonalizerExecutor(self.mc)
        if start_with_explainer:
            expl = ExplainerExecutor(self.mc)
            return (WorkflowBuilder(name="enrich", start_executor=expl, output_from=[pers])
                    .add_chain([expl, narr, pers]).build())
        return WorkflowBuilder(name="recap", start_executor=narr, output_from=[pers]).add_edge(narr, pers).build()

    async def process(self, event):
        """Feed one live event. Returns the instant (version-1) overlays; enrichment follows in the background."""
        t0 = time.perf_counter()
        result = await self.live.run(event)
        overlays = [o for batch in result.get_outputs() for o in batch]
        if overlays:
            self.instant_latency_s.append(time.perf_counter() - t0)
            await self._emit(overlays)
        return overlays

    def add_profile(self, profile):
        """Register a viewer profile mid-match; it receives overlays from now on."""
        self.mc.personalizer.add_profile(profile)

    def _schedule(self, request):
        if not self.enrich:
            return
        task = asyncio.create_task(self._run_background(request))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _run_background(self, request):
        async with self.sem:
            try:
                wf = self._background_workflow(isinstance(request, EnrichRequest))
                result = await wf.run(request)
                await self._emit([o for batch in result.get_outputs() for o in batch])
            except Exception:
                self.workflow_errors += 1  # the version-1 overlay stays on screen

    async def _emit(self, overlays):
        self.outputs.extend(overlays)
        if self.on_output and overlays:
            await self.on_output(overlays)

    async def drain(self):
        """Wait for all background enrichment and recaps (tests, end of match)."""
        while self.tasks:
            await asyncio.gather(*list(self.tasks))

    def summary(self):
        lat = sorted(self.instant_latency_s)
        p95 = lat[int(0.95 * (len(lat) - 1))] if lat else 0.0
        return {**self.mc.metrics.summary(), "instant_latency_p95_ms": round(1000 * p95, 2),
                "overlays": len(self.outputs), "rejected_events": self.mc.rejected,
                "workflow_errors": self.workflow_errors}

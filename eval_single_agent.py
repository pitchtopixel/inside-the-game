"""
Single agent vs multi-agent, measured on the real Foundry model (same match, same moments, same viewers).

Multi-agent: the normal pipeline (instant template, then Explainer -> Narrator -> Personalizer, checked, retry with feedback).
Single agent: one agent, one prompt per moment per viewer - gets the same fact sheet and the viewer profile and must explain,
narrate and personalise in one answer. Its answers go through the same grounding checks, run twice: with no retry
(a rejected answer is one it would have put on screen, or shown nothing) and with the same retry-with-feedback as our agents.

    python eval_single_agent.py --log             # needs .env (Foundry endpoint + model) and `az login`; ~160 model calls

Prints a comparison table and a token projection as the number of distinct viewer profiles grows.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter, defaultdict

from agent_framework import Agent

import test_agents
from agents.common import Metrics, call_agent
from agents.personalizer import LANGUAGES
from llm import get_chat_client
from orchestrator import EnrichRequest, Orchestrator
from testlog import start_log

sys.stdout.reconfigure(encoding="utf-8")

INSTRUCTIONS = """You are a football match assistant for live TV graphics, writing for one viewer.
You receive JSON: {"task": "single", "facts": {...}, "viewer": {...}}. "facts.moment" is what just happened.
In ONE answer: explain WHY the moment matters (scoreline, control vs chaos, pressure, momentum, rhythm), write a
commentary line, and adapt it to the viewer: mode (fan = short and emotional, analyst = precise, broadcaster = on-air
sentence), language (write headline and text in that language), favourite club, focused player.

Rules:
- Use ONLY numbers that appear in "facts". Never invent statistics, history, records or names.
- Mention only players and teams named in "facts"; keep names exactly as written, in Latin letters.
  We have no data on seasons, careers, injuries, transfers, crowds or past matches - never mention them.
- headline: at most 8 words. text: at most 2 short sentences. Sentence case.
Reply with JSON only: {"headline": str, "text": str, "why": str}"""
REQUIRED = ("headline", "text", "why")

USAGE = defaultdict(Counter)  # agent name -> calls / input / output tokens (every model call, incl. rejected drafts)


def count_tokens():
    """Wrap Agent.run once so every model call adds its token usage to USAGE under the agent's name."""
    run = Agent.run

    async def counted(self, *args, **kwargs):
        resp = await run(self, *args, **kwargs)
        u = getattr(resp, "usage_details", None) or {}
        USAGE[self.name].update({"calls": 1, "in": u.get("input_token_count") or 0,
                                 "out": u.get("output_token_count") or 0})
        return resp
    Agent.run = counted


def viewer(profile, data):
    teams = {t["team_id"]: t["name"] for t in data["teams"].values()}
    players = {p["player_id"]: p["name"] for t in data["teams"].values() for p in t["players"]}
    v = {"mode": profile["mode"], "language": LANGUAGES[profile["language"]]}
    if profile.get("club"):
        v["favourite_club"] = teams[profile["club"]]
    if profile.get("player"):
        v["focus_player"] = players[profile["player"]]
    return v


async def multi_agent(data, client):
    """The real pipeline. Returns the orchestrator, the fact sheet of every explained moment and wall time."""
    orch = Orchestrator({"match": data["match"], "teams": data["teams"]}, test_agents.PROFILES, client=client)
    facts, schedule = {}, orch.mc.schedule

    def capture(req):
        if isinstance(req, EnrichRequest):
            facts[req.moment["moment_id"]] = req.facts
        schedule(req)
    orch.mc.schedule = capture
    t0 = time.perf_counter()
    for ev in data["events"]:
        await orch.process(ev)
    await orch.drain()
    return orch, facts, time.perf_counter() - t0


async def single_agent(pairs, facts, data, client, timeout_s, name, retries):
    """One agent per viewer x moment. retries=1 gives it the same retry-with-feedback as our agents."""
    agent = Agent(client, INSTRUCTIONS, name=name)
    metrics, sem, results = Metrics(), asyncio.Semaphore(4), []
    profiles = {p["profile_id"]: p for p in test_agents.PROFILES}

    async def one(mid, pid):
        async with sem:
            t0 = time.perf_counter()
            payload = {"task": "single", "facts": facts[mid], "viewer": viewer(profiles[pid], data)}
            ans, reason = await call_agent(agent, payload, REQUIRED, facts[mid], metrics, timeout_s, retries=retries)
            results.append({"moment": mid, "profile": pid, "ok": ans is not None, "reason": reason,
                            "wait_s": time.perf_counter() - t0, "answer": ans})
    await asyncio.gather(*(one(m, p) for m, p in pairs))
    return {"name": name, "results": results, "metrics": metrics}


def tokens(name):
    return USAGE[name]["in"] + USAGE[name]["out"]


def per_call(name):
    return tokens(name) / USAGE[name]["calls"] if USAGE[name]["calls"] else 0


def p95(values):
    v = sorted(values)
    return v[int(0.95 * (len(v) - 1))] if v else 0


def report(orch, seconds, runs, n_moments):
    s = orch.summary()
    multi_calls = sum(s["llm_calls"].values())
    multi_tokens = sum(tokens(a) for a in ("explainer", "narrator", "personalizer"))
    n = len(runs[0]["results"])
    cols = ["Single agent", "Single agent + retry"]
    row = lambda label, multi, *single: print(f"| {label} | {multi} | " + " | ".join(single) + " |")

    print("\n== Single agent vs multi-agent (same moments, same viewers, same checks) ==")
    print("| Measure | Multi-agent | " + " | ".join(cols) + " |")
    print("|---|---|---|---|")
    row("Viewer x moment overlays compared", n, *(str(n) for _ in runs))
    row("Time until the viewer sees the moment (p95)", f"{s['instant_latency_p95_ms']} ms (instant overlay)",
        *(f"{p95([r['wait_s'] for r in run['results']]):.1f} s (waits for the model)" for run in runs))
    row("Drafts rejected by the checks / model calls", f"{len(orch.mc.metrics.rejected)} / {multi_calls}",
        *(f"{len(run['metrics'].rejected)} / {sum(run['metrics'].calls.values())}" for run in runs))
    row("Answers still rejected (wrong text, or blank / template)", "0 (template stays)",
        *(str(sum(not r["ok"] for r in run["results"])) for run in runs))
    row("Model calls", multi_calls, *(str(sum(run["metrics"].calls.values())) for run in runs))
    row("Tokens", f"{multi_tokens:,}", *(f"{tokens(run['name']):,}" for run in runs))
    for run in runs:
        bad = [r for r in run["results"] if not r["ok"]]
        print(f"\n{run['name']}: still rejected by reason {dict(Counter(r['reason'] for r in bad))}")
        for r in run["metrics"].rejected[:4]:
            print(f"  rejected [{r['problem']}] {r['why'][:140]}\n    text: {r['text'][:160]}")

    # Projection: shared work (Explainer + Narrator) is paid once per moment; only the short Personalizer rewrite is
    # paid per distinct viewer profile. A single agent pays its full prompt (incl. retries) per profile.
    shared = (tokens("explainer") + tokens("narrator")) / max(n_moments, 1)
    pers = per_call("personalizer")
    single = [tokens(run["name"]) / max(n, 1) for run in runs]
    print(f"\n== Tokens per moment as distinct viewer profiles grow (measured: shared {shared:.0f}, "
          f"personalizer {pers:.0f}/profile, " + ", ".join(f"{c} {t:.0f}/profile" for c, t in zip(cols, single)) + ") ==")
    print("| Distinct profiles | Multi-agent | " + " | ".join(cols) + " |")
    print("|---|---|---|---|")
    for p in (1, 3, 10, 50, 200):
        m = shared + pers * p
        print(f"| {p} | {m:,.0f} | " + " | ".join(f"{t * p:,.0f} ({t * p / m if m else 0:.1f}x)" for t in single) + " |")
    print(f"\nwall time multi-agent match: {seconds:.0f} s · usage: {json.dumps({k: dict(v) for k, v in USAGE.items()})}")


async def amain(args):
    data = test_agents.load(args.match)
    test_agents.setup_profiles(data)
    provider = "mock" if args.mock else "foundry"
    count_tokens()
    print(f"match {args.match}, model {os.getenv('FOUNDRY_MODEL')}, viewers {[p['profile_id'] for p in test_agents.PROFILES]}")
    orch, facts, seconds = await multi_agent(data, get_chat_client(provider))
    pairs = sorted({(o["moment_id"], o["profile_id"]) for o in orch.outputs
                    if o["kind"] == "moment" and o["version"] == 2 and o["moment_id"] in facts})
    if args.limit:
        pairs = pairs[:args.limit]
    print(f"multi-agent done: {len(facts)} explained moments, {len(pairs)} viewer x moment overlays")
    runs = [await single_agent(pairs, facts, data, get_chat_client(provider), args.timeout, "single_agent", 0),
            await single_agent(pairs, facts, data, get_chat_client(provider), args.timeout, "single_agent_retry", 1)]
    report(orch, seconds, runs, len(facts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", default="match_001")
    ap.add_argument("--timeout", type=float, default=8.0, help="same live budget as the agents")
    ap.add_argument("--limit", type=int, help="only the first N viewer x moment pairs for the single agent")
    ap.add_argument("--mock", action="store_true", help="offline mock model (checks the plumbing, no cost)")
    ap.add_argument("--log", action="store_true", help="also save output to logs/")
    args = ap.parse_args()
    if args.log:
        start_log(f"eval_single_agent_{args.match}")
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()

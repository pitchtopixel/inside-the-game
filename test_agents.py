"""
Test the multi-agent pipeline end to end (mock model by default - no Azure cost).

    python test_agents.py                 full match, 3 viewer profiles, + failure-mode runs
    python test_agents.py --foundry       same checks against the real Foundry model (costs a few cents)
    add --match match_002 for another match, --log to save output to logs/, --show to print sample overlays
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter

from llm import MockChatClient, get_chat_client
from orchestrator import Orchestrator
from testlog import start_log

sys.stdout.reconfigure(encoding="utf-8")
PROFILES, CLUB, FOCUS = [], None, None   # set per match by setup_profiles()
OVERLAY_KEYS = {"overlay_id", "version", "kind", "source", "profile_id", "clock", "elapsed_s", "period",
                "headline", "text", "display"}
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def load(match_id):
    with open(os.path.join("data", "synthetic", f"{match_id}.json"), encoding="utf-8") as f:
        return json.load(f)


def setup_profiles(data):
    """Analyst; Spanish fan of the away club; player focus on the match's top scorer."""
    global PROFILES, CLUB, FOCUS
    CLUB = data["teams"]["away"]["team_id"]
    scorers = Counter(e["player_id"] for e in data["events"] if e["type"] == "goal")
    FOCUS = scorers.most_common(1)[0][0]
    PROFILES = [
        {"profile_id": "analyst_en", "mode": "analyst", "language": "en"},
        {"profile_id": "fan_es_club", "mode": "fan", "language": "es", "club": CLUB, "metric": "possession"},
        {"profile_id": "focus_player", "mode": "fan", "language": "en", "player": FOCUS, "metric": "xg"},
    ]


async def run_match(data, client, events=None, timeout_s=8.0):
    orch = Orchestrator({"match": data["match"], "teams": data["teams"]}, PROFILES, client=client, timeout_s=timeout_s)
    for ev in events or data["events"]:
        await orch.process(ev)
    await orch.drain()
    return orch


def latest(orch, profile_id):
    """Final version of each overlay a viewer ends up seeing."""
    best = {}
    for o in orch.outputs:
        if o["profile_id"] == profile_id and o["version"] >= best.get(o["overlay_id"], {}).get("version", 0):
            best[o["overlay_id"]] = o
    return list(best.values())


async def main_run(data, client, show):
    orch = await run_match(data, client)
    s = orch.summary()
    print(f"\n== Full match ({'mock' if isinstance(client, MockChatClient) else 'foundry'}) ==")
    print(json.dumps(s))
    rejected = orch.mc.metrics.rejected
    if rejected:
        print(f"\n== Rejected answers ({len(rejected)}; retried with feedback, template shown if still rejected) ==")
        for r in rejected:
            print(f"  {r['agent']} [{r['problem']}] {r['why']}\n    text: {r['text'][:200]}")
    moments =[m for m in orch.mc.moments.key_moments]
    explainable = {o["moment_id"] for o in orch.outputs if o["kind"] == "moment" and o["importance"] >= 45}
    analyst = latest(orch, "analyst_en")
    fan = latest(orch, "fan_es_club")
    focus = latest(orch, "focus_player")

    print("\n== Checks: pipeline ==")
    alerts = len(orch.mc.state.alerts)
    analyst_moments = [o for o in analyst if o["kind"] == "moment"]
    check("1. every alert became a moment the analyst sees", len(analyst_moments) == alerts,
          f"{len(analyst_moments)}/{alerts}")
    enriched = {o["moment_id"] for o in analyst_moments if o["version"] == 2}
    check("2. every important moment got an explanation (v2)", explainable <= enriched,
          f"{len(enriched & explainable)}/{len(explainable)}")
    llm_share = sum(o["source"] == "llm" for o in analyst_moments if o["version"] == 2) / max(len(enriched), 1)
    if isinstance(client, MockChatClient):
        check("3. explanations come from the LLM agents (no fallbacks)", not s["fallbacks"] and llm_share == 1.0,
              f"llm {llm_share:.0%}, fallbacks {s['fallbacks']}")
    else:  # a real model may need the safety net now and then: rejected answers fall back to checked templates
        check("3. explanations come from the LLM agents (>= 90%, rest safe template fallbacks)", llm_share >= 0.9,
              f"llm {llm_share:.0%}, fallbacks {s['fallbacks']}")
    check("4. instant overlays p95 < 50 ms", s["instant_latency_p95_ms"] < 50, f"{s['instant_latency_p95_ms']} ms")
    recaps = Counter(o["recap_type"] for o in orch.outputs if o["kind"] == "recap")
    check("5. half-time and full-time recaps for every viewer",
          recaps == {"half_time": len(PROFILES), "full_time": len(PROFILES)}, str(dict(recaps)))
    missing = [o["overlay_id"] for o in orch.outputs if not OVERLAY_KEYS <= o.keys()]
    check("6. every overlay matches the schema", not missing, f"{len(orch.outputs)} overlays, {len(missing)} bad")
    calls = sum(s["llm_calls"].values())
    check("7. LLM cost bounded (< 150 calls per match)", calls < 150, f"{calls} calls")

    print("\n== Checks: personalization ==")
    fan_moments = [o for o in fan if o["kind"] == "moment"]
    low = [o for o in fan_moments if o["importance"] < 60 and o["alert_type"] != "goal"]
    check("8. fan sees fewer, bigger moments than analyst", len(fan_moments) < len(analyst_moments) and not low,
          f"fan {len(fan_moments)} vs analyst {len(analyst_moments)}, low-importance shown {len(low)}")
    es = [o for o in fan_moments if o["version"] == 2]
    check("9. Spanish viewer gets Spanish (v2) overlays", es and all(o["language"] == "es" for o in es),
          f"{len(es)} overlays")
    wrong = [o for o in fan_moments if o["highlight"]["your_club"] != (o["team"] == CLUB)]
    check("10. favourite-club highlight correct", not wrong, f"{sum(o['highlight']['your_club'] for o in fan_moments)} highlighted")
    focus_moments = {o["moment_id"] for o in analyst_moments if o.get("player_id") == FOCUS}
    seen = {o["moment_id"] for o in focus if o["kind"] == "moment"}
    trackers = [o for o in focus if o["kind"] == "player_tracker"]
    check(f"11. player focus ({FOCUS}): all their moments + tracker every 5 min",
          focus_moments <= seen and len(trackers) >= 18,
          f"{len(focus_moments & seen)}/{len(focus_moments)} moments, {len(trackers)} trackers")
    tickers = Counter(o["profile_id"] for o in orch.outputs if o["kind"] == "metric_ticker")
    check("12. single-metric ticker for metric viewers", tickers.get("fan_es_club", 0) >= 18 and tickers.get("focus_player", 0) >= 18
          and "analyst_en" not in tickers, str(dict(tickers)))

    print("\n== Checks: ingest ==")
    dup = await orch.process(data["events"][100])
    check("13. ingest agent rejects a replayed (duplicate) event", dup == [] and orch.mc.rejected == 1, f"rejected {orch.mc.rejected}")

    if show:
        for o in [o for o in analyst_moments if o["alert_type"] == "goal"][:1] + \
                 [o for o in fan_moments if o["version"] == 2][:1] + trackers[:1] + \
                 [o for o in analyst if o["kind"] == "recap"][:1]:
            print("\n" + json.dumps(o, ensure_ascii=False, indent=1)[:1500])
    return orch


async def failure_runs(data):
    """Each failure mode must degrade to template overlays - never crash, never lose an overlay."""
    print("\n== Checks: failure recovery (first half, mock failure modes) ==")
    first_half = [e for e in data["events"] if e["period"] == 1]
    for n, (mode, expect) in enumerate([("fail", "error"), ("bad_json", "bad_json"),
                                        ("hallucinate", "ungrounded"), ("wrong_name", "unknown_name"),
                                        ("off_topic", "out_of_scope"), ("contradict", "contradiction"),
                                        ("slow", "timeout")], start=14):
        orch = await run_match(data, MockChatClient(mode=mode), first_half, timeout_s=0.05)
        s = orch.summary()
        moments = [o for o in latest(orch, "analyst_en") if o["kind"] == "moment"]
        v2 = [o for o in moments if o["version"] == 2]
        explainable = [o for o in moments if o["importance"] >= 45]
        reasons = {k.split(":")[1] for k in s["fallbacks"]}
        leaked = [o for o in orch.outputs
                  if any(s in json.dumps(o, ensure_ascii=False)
                         for s in ("47th", "99,999", "Vargas", "season", "lead the game"))]
        ok = (s["workflow_errors"] == 0 and len(v2) == len(explainable) and all(o["source"] == "fallback" for o in v2)
              and reasons == {expect} and not leaked)
        check(f"{n}. model '{mode}' -> template fallback, nothing lost", ok,
              f"v2 {len(v2)}/{len(explainable)}, reasons {sorted(reasons)}, leaked {len(leaked)}")


def contradiction_rules():
    """Hand-written sentences against a known fact sheet: wrong claims flagged, true ones pass."""
    from agents.common import contradictions
    print("\n== Checks: contradiction rules ==")
    facts = {"score": {"Riverside": 1, "Northgate": 0}, "momentum": {"on_top": "Riverside"},
             "moment": {"type": "goal", "team_name": "Riverside", "data": {"body_part": "right_foot"}}}
    saved = {**facts, "moment": {"type": "big_chance", "team_name": "Riverside", "data": {"outcome": "off_target"}}}
    wrong = [("Northgate lead the game.", facts), ("Riverside trail after the break.", facts),
             ("A thumping header from Arribas!", facts), ("Northgate are on top now.", facts),
             ("It finishes all square.", facts), ("Brilliant save by the keeper.", saved)]
    right = [("Riverside lead 1-0.", facts), ("Northgate trail and need a response.", facts),
             ("Arribas fires it in with his right foot.", facts), ("Riverside are on top.", facts),
             ("The shot goes wide.", saved)]
    missed = [s for s, f in wrong if not contradictions({"t": s}, f)]
    false_alarms = [s for s, f in right if contradictions({"t": s}, f)]
    check("21. contradictions caught, true claims pass", not missed and not false_alarms,
          f"caught {len(wrong) - len(missed)}/{len(wrong)}, false alarms {len(false_alarms)} {missed + false_alarms}")


def name_rules():
    """Name check: invented names flagged, known names (incl. possessives, as real models write them) pass."""
    from agents.common import unknown_names
    print("\n== Checks: name rules ==")
    facts = {"teams": {"Riverside": {}, "Northgate": {}}, "player": {"name": "Iker Arribas"}}
    wrong = ["A clever cross from Vargas.", "A typical Premier League finish."]
    right = ["It was Riverside's third goal.", "The ball fell to Arribas’s left foot.",
             "Arribas puts Riverside ahead.", "Northgate's defence opened up."]
    missed = [s for s in wrong if not unknown_names({"t": s}, facts)]
    false_alarms = [s for s in right if unknown_names({"t": s}, facts)]
    check("22. invented names caught, known names and possessives pass", not missed and not false_alarms,
          f"caught {len(wrong) - len(missed)}/{len(wrong)}, false alarms {len(false_alarms)} {missed + false_alarms}")


async def amain(args):
    data = load(args.match)
    setup_profiles(data)
    print(f"match {args.match}: {data['match']['final_score']}, club {CLUB}, player focus {FOCUS}")
    client = get_chat_client("foundry") if args.foundry else MockChatClient()
    await main_run(data, client, args.show)
    if not args.foundry:
        await failure_runs(data)
    contradiction_rules()
    name_rules()
    print(f"\n{sum(results)}/{len(results)} checks passed -> {'PASS' if all(results) else 'FAIL'}")
    return all(results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", default="match_001")
    ap.add_argument("--foundry", action="store_true", help="use the real Foundry model")
    ap.add_argument("--show", action="store_true", help="print sample overlays")
    ap.add_argument("--log", action="store_true", help="also save output to logs/")
    args = ap.parse_args()
    if args.log:
        start_log(f"test_agents_{'foundry' if args.foundry else 'mock'}_{args.match}")
    raise SystemExit(0 if asyncio.run(amain(args)) else 1)


if __name__ == "__main__":
    main()

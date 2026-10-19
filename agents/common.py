"""Shared agent plumbing: fact sheets, grounding check, and a safe JSON call to an LLM agent."""

import asyncio
import json
import re
import time
from collections import defaultdict

TEAM_FACT_KEYS = ("possession_pct", "shots", "shots_on_target", "xg", "pass_accuracy_pct", "final_third_passes",
                  "pressures", "turnovers", "fouls", "corners")
PLAYER_FACT_KEYS = ("goals", "assists", "shots", "xg", "passes", "pass_accuracy_pct", "touches", "tackles_won",
                    "interceptions", "top_speed_kmh", "distance_km", "sprints")


class Metrics:
    """LLM call accounting shared by all agents (shown in tests and the UI)."""

    def __init__(self):
        self.calls = defaultdict(int)       # agent -> calls
        self.fallbacks = defaultdict(int)   # reason -> count
        self.latency_s = []

    def summary(self):
        lat = sorted(self.latency_s)
        p95 = lat[int(0.95 * (len(lat) - 1))] if lat else 0.0
        return {"llm_calls": dict(self.calls), "fallbacks": dict(self.fallbacks),
                "llm_latency_p95_s": round(p95, 3)}


# ---------- fact sheets (the only data an LLM may use) ----------
def match_facts(snap, names, window_s):
    tname = {tid: t["name"] for tid, t in snap["teams"].items()}
    h, a = list(snap["teams"])
    return {
        "clock": snap["clock"], "period": snap["period"],
        "score": {tname[t]: snap["score"][t] for t in (h, a)},
        "score_line": f"{tname[h]} {snap['score'][h]}-{snap['score'][a]} {tname[a]}",
        "window_min": window_s // 60,
        "teams": {tname[t]: {k: snap["teams"][t][k] for k in TEAM_FACT_KEYS} for t in (h, a)},
        "momentum": {"on_top": tname.get(snap["momentum"]["on_top"]),
                     "threat_share_pct": {tname[t]: v for t, v in snap["momentum"]["threat_share_pct"].items()}},
        "rhythm": {"state": snap["rhythm"]["state"],
                   "turnovers_per_min": snap["rhythm"].get("turnovers_per_min"),
                   "possession_share_pct": {tname[t]: v for t, v in
                                            snap["rhythm"].get("possession_share", {}).items()}},
    }


def player_facts(snap, pid):
    p = snap["players"].get(pid)
    if not p:
        return None
    return {"name": p["name"], **{k: p.get(k) for k in PLAYER_FACT_KEYS}}


def moment_facts(moment, snap, window_s):
    f = match_facts(snap, None, window_s)
    tname = {tid: t["name"] for tid, t in snap["teams"].items()}
    team = moment.get("team")
    opp = next((t for t in tname if t != team), None) if team else None
    data = dict(moment["alert"].get("data") or {})
    if "xg" in data:
        data["xg_pct"] = round(100 * data["xg"])
    f["moment"] = {"type": moment["type"], "category": moment["category"], "tags": moment["tags"],
                   "text": moment["alert"]["text"], "team_name": tname.get(team), "opponent_name": tname.get(opp),
                   "player_name": moment["alert"].get("player_name"), "data": data}
    if moment.get("player_id"):
        f["player"] = player_facts(snap, moment["player_id"])
    return f


# ---------- grounding ----------
NUM = re.compile(r"\d+(?:\.\d+)?")


def _numbers(obj, out):
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        out.update(float(n) for n in NUM.findall(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _numbers(k, out)
            _numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, out)
    return out


def ungrounded_numbers(output, facts):
    """Numbers in the output that do not appear in the facts (allowing normal rounding)."""
    allowed = _numbers(facts, set())
    bad = []
    for n in sorted(_numbers(output, set())):
        if n.is_integer():
            ok = any(n == a or n == round(a) for a in allowed)
        else:
            ok = any(abs(n - a) <= 0.051 for a in allowed)
        if not ok:
            bad.append(n)
    return bad


# ---------- safe LLM call ----------
def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[1] if "\n" in text else text.strip("`")
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


async def call_agent(agent, payload, required, facts, metrics, timeout_s, retries=1):
    """
    Run an Agent Framework agent on a JSON task and validate the answer.
    Returns (data, None) on success or (None, reason) - callers then fall back to templates.
    """
    reason = "unknown"
    for _ in range(retries + 1):
        metrics.calls[agent.name] += 1
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(agent.run(json.dumps(payload, ensure_ascii=False)), timeout_s)
            metrics.latency_s.append(time.perf_counter() - t0)
            data = parse_json(resp.text)
            missing = [k for k in required if k not in data]
            if missing:
                reason = "missing_keys"
                continue
            bad = ungrounded_numbers({k: data[k] for k in required}, facts)
            if bad:
                reason = "ungrounded"
                continue
            return data, None
        except asyncio.TimeoutError:
            reason = "timeout"
        except (ValueError, json.JSONDecodeError):
            reason = "bad_json"
        except Exception:
            reason = "error"
    metrics.fallbacks[f"{agent.name}:{reason}"] += 1
    return None, reason

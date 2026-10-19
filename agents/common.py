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


def _texts(obj, out):
    """All strings in a nested structure, dict keys included (team names are keys in the fact sheet)."""
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _texts(k, out)
            _texts(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _texts(v, out)
    return out


WORD = re.compile(r"[\wÀ-ÿ'’-]+")
CAPITALISED = re.compile(r"(?<![\w'’-])[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’-]*[a-zà-öø-ÿ]")  # whole word, e.g. "Vargas"
SENTENCE_START = re.compile(r"(^|[.!?¡¿:;\-–—\]\"“(])\s*$")
NAME_ALLOW = {"xG"}


def unknown_names(output, facts):
    """
    Capitalised words mid-sentence (names of players, teams, places, competitions) that do not
    appear in the facts - e.g. an invented scorer or "Premier League".
    """
    known = {w.lower() for s in _texts(facts, []) for w in WORD.findall(s)}
    bad = []
    for s in _texts(output, []):
        for m in CAPITALISED.finditer(s):
            if SENTENCE_START.search(s[:m.start()]):
                continue  # first word of a sentence is capitalised anyway
            if m.group(0).lower() not in known and m.group(0) not in NAME_ALLOW:
                bad.append(m.group(0))
    return sorted(set(bad))


# Topics we have no data for: any mention is an invented claim unless the facts contain it.
OUT_OF_SCOPE = re.compile(
    r"\b(seasons?|career|campaign|last week|history|historic|all-time|injur\w*|transfers?|contract|league table|"
    r"title race|unbeaten|previous match|last match|last game|crowd|attendance|derby|trophy|trophies)\b", re.I)


def out_of_scope_claims(output, facts):
    fact_text = " ".join(_texts(facts, [])).lower()
    found = {m.group(0).lower() for s in _texts(output, []) for m in OUT_OF_SCOPE.finditer(s)}
    return sorted(t for t in found if t not in fact_text)


def _find(facts, key):
    """First value for `key` anywhere in a nested fact sheet."""
    if isinstance(facts, dict):
        if key in facts:
            return facts[key]
        for v in facts.values():
            found = _find(v, key)
            if found is not None:
                return found
    return None


LEAD = r"(lead|leads|are leading|go ahead|goes ahead|are ahead|in front)"
TRAIL = r"(trail|trails|are trailing|are behind|fall behind)"
ON_TOP = r"(on top|in control|take control|takes control|dominat\w*|in the ascendancy)"
HEADER = re.compile(r"\b(header|headed|heads (it|home|in)|with (his|her|a) head)\b", re.I)
FOOT = re.compile(r"\b(left|right)[- ]?foot(ed)?\b|\bvolley\w*\b", re.I)
SAVE = re.compile(r"\b(saves?|saved|parried|stopped by the (keeper|goalkeeper))\b", re.I)
MISS = re.compile(r"\b(wide|off target|over the bar|hits? the (post|bar)|blocked)\b", re.I)
LEVEL = re.compile(r"\b(all square|level at|on level terms|equalis\w*|equaliz\w*)\b", re.I)
MOMENTUM_TYPES = ("momentum", "rhythm", "pressure_spell", "possession_streak")


def contradictions(output, facts):
    """
    Plain-word claims that contradict the data (English): who leads, who is on top,
    header vs foot, and how a chance ended. Returns a list of short descriptions.
    """
    text = " ".join(_texts(output, []))
    score = _find(facts, "score") or {}
    moment = _find(facts, "moment") or {}
    data = moment.get("data") or {}
    bad = []
    for team, goals in score.items():
        others = [g for t, g in score.items() if t != team]
        if not others:
            continue
        t = re.escape(team)
        if re.search(rf"\b{t}\s+{LEAD}\b", text, re.I) and not goals > max(others):
            bad.append(f"{team} do not lead")
        if re.search(rf"\b{t}\s+{TRAIL}\b", text, re.I) and not goals < min(others):
            bad.append(f"{team} do not trail")
        on_top = (_find(facts, "momentum") or {}).get("on_top")
        claims_on_top = re.search(rf"\b{t}\s+(are\s+|is\s+)?{ON_TOP}", text, re.I)
        about_this = moment.get("team_name") == team and moment.get("type") in MOMENTUM_TYPES
        is_recap = _find(facts, "key_moments") is not None  # recaps retell earlier phases
        if claims_on_top and on_top != team and not about_this and not is_recap:
            bad.append(f"{team} are not on top")
    if LEVEL.search(text) and len(set(score.values())) > 1:
        bad.append("the score is not level")
    body = data.get("body_part")
    if body == "head" and FOOT.search(text):
        bad.append("it was a header")
    if body in ("left_foot", "right_foot") and HEADER.search(text):
        bad.append("it was not a header")
    outcome = data.get("outcome")
    if outcome in ("off_target", "post", "blocked") and SAVE.search(text):
        bad.append(f"the shot was not saved ({outcome})")
    if outcome == "saved" and MISS.search(text):
        bad.append("the shot was saved")
    return bad


def grounding_problem(output, facts):
    """None if the output is grounded in the facts, else the reason it is not."""
    if ungrounded_numbers(output, facts):
        return "ungrounded"
    if unknown_names(output, facts):
        return "unknown_name"
    if out_of_scope_claims(output, facts):
        return "out_of_scope"
    if contradictions(output, facts):
        return "contradiction"
    return None


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
            problem = grounding_problem({k: data[k] for k in required}, facts)
            if problem:
                reason = problem
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

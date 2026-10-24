"""Shared agent plumbing: fact sheets, grounding check, and a safe JSON call to an LLM agent."""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import weakref
from collections import defaultdict

# One log record per model answer (llm / fallback); with TRACING=1 they reach App Insights for the fallback-rate alert
log = logging.getLogger("itg.llm")
log.setLevel(logging.INFO)
log.addHandler(logging.NullHandler())  # nothing printed locally

TEAM_FACT_KEYS = ("possession_pct", "shots", "shots_on_target", "xg", "pass_accuracy_pct", "final_third_passes",
                  "pressures", "turnovers", "fouls", "corners")
PLAYER_FACT_KEYS = ("goals", "assists", "shots", "xg", "passes", "pass_accuracy_pct", "touches", "tackles_won",
                    "interceptions", "top_speed_kmh", "distance_km", "sprints")


class Metrics:
    """LLM call accounting shared by all agents (shown in tests and the UI)."""

    def __init__(self):
        self.calls = defaultdict(int)       # agent -> calls
        self.fallbacks = defaultdict(int)   # reason -> count
        self.rejected_names = defaultdict(int)  # word -> times an answer was rejected for it
        self.rejected = []  # every rejected answer: agent, problem, why, text (shown by test_agents.py)
        self.latency_s = []
        self.cache_hits = 0  # answers reused from another viewer's replay (no model call)

    def summary(self):
        lat = sorted(self.latency_s)
        p95 = lat[int(0.95 * (len(lat) - 1))] if lat else 0.0
        return {"llm_calls": dict(self.calls), "cache_hits": self.cache_hits, "blocked_drafts": len(self.rejected),
                "fallbacks": dict(self.fallbacks),
                "rejected_names": dict(self.rejected_names), "llm_latency_p95_s": round(p95, 3)}


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
POSSESSIVE = re.compile(r"['’]s$")  # "Riverside's" -> "Riverside"
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
            word = POSSESSIVE.sub("", m.group(0))
            if word.lower() not in known and word not in NAME_ALLOW:
                bad.append(word)
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
# Aim vs claim, by grammar rather than fixed phrases: a level-score word is an aim when an intent / possibility
# word comes just before it ("push to equalise", "chasing an equaliser", "could level it", "nearly equalised")
# or a modal just after it ("an equaliser would change everything"); "came back to equalise" stays a claim.
AIM_BEFORE = {"to", "for", "need", "needs", "needing", "push", "pushes", "pushing", "chase", "chases", "chasing",
              "seek", "seeks", "seeking", "hunt", "hunts", "hunting", "search", "searching", "look", "looks", "looking",
              "hope", "hopes", "hoping", "try", "tries", "trying", "bid", "aim", "aims", "aiming", "want", "wants",
              "could", "would", "might", "may", "will", "can", "nearly", "almost", "chance", "fail", "fails", "failed"}
AIM_AFTER = {"would", "could", "might", "will", "can"}
DONE_TO = re.compile(r"\b(came|come|comes|fought|fight|fights|hit|hits|bounced|bounce|bounces) back to$|"
                     r"\b(rallied|rally|rallies|recovered|recover|recovers|rose|rise|rises|went on|go on|goes on) to$")
MOMENTUM_TYPES = ("momentum", "rhythm", "pressure_spell", "possession_streak")


def _level_claim(text, m):
    """True if this level-score word states the score IS level (a claim), False if it is only an aim."""
    before = re.split(r"[.!?;:]", text[:m.start()])[-1].lower().strip()
    after = re.split(r"[.!?;:]", text[m.end():])[0].lower().split()
    if DONE_TO.search(before):
        return True
    return not (set(before.replace(",", " ").split()[-3:]) & AIM_BEFORE or set(after[:2]) & AIM_AFTER)


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
    if len(set(score.values())) > 1 and any(_level_claim(text, m) for m in LEVEL.finditer(text)):
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


def grounding_feedback(output, facts, problem):
    """Short note telling the model why its answer was rejected (sent with the retry)."""
    detail = {"ungrounded": lambda: f"numbers not in the facts: {ungrounded_numbers(output, facts)}",
              "unknown_name": lambda: f"capitalised words that are not names in the facts: {unknown_names(output, facts)}"
                                      " - use only those names, sentence case",
              "out_of_scope": lambda: f"topics we have no data on: {out_of_scope_claims(output, facts)}",
              "contradiction": lambda: f"claims that contradict the data: {contradictions(output, facts)}"}
    return f"Your previous answer was rejected - {detail[problem]()}. Rewrite it using only the facts."


def verification(output, facts):
    """What the grounding check verified in an answer shown on screen (for the viewer's "Verified" box)."""
    names = {POSSESSIVE.sub("", m.group(0)) for t in _texts(output, []) for m in CAPITALISED.finditer(t)
             if not SENTENCE_START.search(t[:m.start()])}
    return {"passed": grounding_problem(output, facts) is None, "numbers": len(_numbers(output, set())),
            "names": sorted(names - NAME_ALLOW)}


def missing_names(output, names):
    """Names that must appear unchanged (Latin letters) but are missing - e.g. transliterated into Arabic or Hindi."""
    text = " ".join(_texts(output, []))
    return [n for n in names if not re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", text)]


def headline_names(fields, facts):
    """Single-word team / player names in an overlay headline (the subject a translation must keep)."""
    return sorted(n for n in _proper_names(facts, set())
                  if " " not in n and re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", fields.get("headline", "")))


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


# Checked answers, per model client: the same match gives the same facts, so every viewer of a replay
# reuses the first viewer's answers. Keyed by the exact task; only answers that passed the checks are kept.
ANSWER_CACHE = weakref.WeakKeyDictionary()  # client -> {task key: answer | in-flight future}
CACHE_MAX = 5000


NAME_KEYS = ("name", "player_name", "team_name", "opponent_name")
SENTENCE_FIRST = re.compile(r"(^|[.!?]\s+)([a-zà-ÿ])")


def _proper_names(facts, out):
    """Team and player names in the facts (team names are the keys of "score")."""
    if isinstance(facts, dict):
        for k, v in facts.items():
            if k in NAME_KEYS and isinstance(v, str):
                words = v.split()
                out.update(words if k != "name" or len(words) == 1 else words[-1:] + [v])
            elif k == "score" and isinstance(v, dict):
                out.update(w for t in v for w in t.split())
            else:
                _proper_names(v, out)
    elif isinstance(facts, list):
        for v in facts:
            _proper_names(v, out)
    return out


def restore_case(output, facts):
    """Undo over-applied sentence case: names from the facts get their capitals back, sentences start with one."""
    names = sorted(_proper_names(facts, set()), key=len, reverse=True)
    fix = re.compile(r"(?<![\w])(" + "|".join(re.escape(n) for n in names) + r")(?![\w])", re.I) if names else None
    proper = {n.lower(): n for n in names}

    def one(v):
        if isinstance(v, str):
            if fix:
                v = fix.sub(lambda m: proper.get(m.group(0).lower(), m.group(0)), v)
            return SENTENCE_FIRST.sub(lambda m: m.group(1) + m.group(2).upper(), v)
        if isinstance(v, dict):
            return {k: one(x) for k, x in v.items()}
        if isinstance(v, list):
            return [one(x) for x in v]
        return v
    return one(output)


def save_answer_cache(client, path, model):
    """Write the client's checked answers to a JSON file (shipped with the app: preset viewers cost nothing)."""
    entries = [[*key, data] for key, data in ANSWER_CACHE.get(client, {}).items() if isinstance(data, dict)]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"model": model, "answers": entries}, f, ensure_ascii=False, indent=0)
    return len(entries)


def load_answer_cache(client, path, model):
    """Preload answers saved for the same model. Returns how many were loaded (0 if none / other model)."""
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    if saved.get("model") != model:
        return 0
    cache = ANSWER_CACHE.setdefault(client, {})
    for *key, data in saved["answers"]:
        cache[tuple(key)] = data
    return len(saved["answers"])


def task_key(agent, payload):
    """Cache key: agent, a fingerprint of its instructions (a prompt change invalidates old answers), the task."""
    instr = (getattr(agent, "default_options", None) or {}).get("instructions") or ""
    return (agent.name, hashlib.sha1(instr.encode("utf-8")).hexdigest()[:10],
            json.dumps(payload, sort_keys=True, ensure_ascii=False))


async def call_agent(agent, payload, required, facts, metrics, timeout_s, retries=1, keep_names=()):
    """
    Run an Agent Framework agent on a JSON task and validate the answer.
    Returns (data, None) on success or (None, reason) - callers then fall back to templates.
    Identical tasks are answered from the cache (or wait for the identical call already in flight).
    """
    cache = ANSWER_CACHE.setdefault(agent.client, {})
    key = task_key(agent, payload)
    hit = cache.get(key)
    if hit is not None:
        data = await asyncio.shield(hit) if isinstance(hit, asyncio.Future) else hit
        if data is not None:
            metrics.cache_hits += 1
            return restore_case(data, facts), None
    if len(cache) >= CACHE_MAX:
        cache.clear()
    pending = asyncio.get_running_loop().create_future()
    cache[key] = pending
    data, reason = None, "error"
    try:
        data, reason = await _call_model(agent, payload, required, facts, metrics, timeout_s, retries, keep_names)
    finally:
        pending.set_result(data)
        if data is None:
            cache.pop(key, None)  # failed: the next viewer asks the model again
        else:
            cache[key] = data
    return (restore_case(data, facts) if data is not None else None), reason


async def _call_model(agent, payload, required, facts, metrics, timeout_s, retries, keep_names=()):
    """One task, with a retry: a grounding rejection is explained to the model so it can correct itself."""
    reason, feedback = "unknown", None
    for _ in range(retries + 1):
        metrics.calls[agent.name] += 1
        t0 = time.perf_counter()
        task = {**payload, "feedback": feedback} if feedback else payload
        try:
            resp = await asyncio.wait_for(agent.run(json.dumps(task, ensure_ascii=False)), timeout_s)
            metrics.latency_s.append(time.perf_counter() - t0)
            data = parse_json(resp.text)
            missing = [k for k in required if k not in data]
            if missing:
                reason = "missing_keys"
                continue
            answer = {k: data[k] for k in required}
            problem = grounding_problem(answer, facts)
            renamed = [] if problem else missing_names(answer, keep_names)
            if renamed:
                problem = "renamed"
            if problem:
                reason = problem
                feedback = (f"Your previous answer was rejected - keep these names exactly as written, in Latin letters: "
                            f"{renamed}. Rewrite it using only the facts." if renamed
                            else grounding_feedback(answer, facts, problem))
                metrics.rejected.append({"agent": agent.name, "problem": problem, "why": feedback,
                                         "text": " | ".join(s for s in _texts(answer, []) if s)})
                if problem == "unknown_name":
                    for w in unknown_names(answer, facts):
                        metrics.rejected_names[w] += 1
                continue
            log.info("llm_answer", extra={"agent": agent.name, "outcome": "llm", "model": os.getenv("FOUNDRY_MODEL")})
            return data, None
        except asyncio.TimeoutError:
            reason = "timeout"
        except (ValueError, json.JSONDecodeError):
            reason = "bad_json"
        except Exception:
            reason = "error"
    metrics.fallbacks[f"{agent.name}:{reason}"] += 1
    log.warning("llm_fallback", extra={"agent": agent.name, "outcome": "fallback", "reason": reason,
                                       "model": os.getenv("FOUNDRY_MODEL")})
    return None, reason

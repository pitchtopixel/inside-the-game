"""
Narrator agent: instant template overlays (no LLM), LLM commentary lines, and half-time / full-time recaps.
"""

from agent_framework import Agent

from agents import templates
from agents.common import call_agent, match_facts

INSTRUCTIONS = """You are a TV football commentator writing on-screen text.
Tasks (JSON input, JSON output only):
- {"task": "commentary", "facts": {...}, "explanation": {...}} -> {"line": str}
  One vivid broadcast line, at most 25 words, starting with the match clock.
- {"task": "recap", "facts": {...}} -> {"title": str, "summary": str, "key_moments": [str],
  "player_of_the_period": {"name": str, "reason": str}}
  summary: 3-4 sentences on how the period was won or lost (control, chances, momentum swings).
  key_moments: one short line per moment in facts.key_moments, in order.
  player_of_the_period: choose from facts.top_players only.
Use ONLY numbers and names that appear in the input. Never invent facts."""

DISPLAY = {"high": ("full_banner", 8), "medium": ("lower_third", 6), "low": ("side_panel", 5)}


def base_overlay(moment, version, source, headline, text, why=None, evidence=None, tag=None, reason=None):
    a = moment["alert"]
    slot, secs = DISPLAY.get(a["priority"], ("side_panel", 5))
    if moment["type"] == "goal":
        slot, secs = "full_banner", 10
    return {"overlay_id": f"ov-{moment['moment_id']}", "version": version, "source": source,
            "fallback_reason": reason, "kind": "moment", "moment_id": moment["moment_id"],
            "event_id": moment["event_id"], "alert_type": moment["type"], "category": moment["category"],
            "tags": moment["tags"], "importance": moment["importance"], "priority": a["priority"],
            "clock": moment["clock"], "elapsed_s": moment["elapsed_s"], "period": moment["period"],
            "team": moment["team"], "player_id": moment["player_id"], "player_name": a.get("player_name"),
            "headline": headline, "text": text, "why": why, "evidence": evidence or [], "tag": tag,
            "display": {"slot": slot, "duration_s": secs, "show_at_elapsed_s": moment["elapsed_s"]}}


class Narrator:
    def __init__(self, client, metrics, timeout_s):
        self.agent = Agent(client, INSTRUCTIONS, name="narrator")
        self.metrics, self.timeout_s = metrics, timeout_s

    def instant(self, moment):
        """Template overlay - shown immediately, before any LLM work."""
        return base_overlay(moment, 1, "template", moment["alert"]["text"], moment["alert"]["text"])

    async def enrich(self, moment, facts, explanation, expl_source, expl_reason):
        payload = {"task": "commentary", "facts": facts, "explanation": explanation}
        data, reason = await call_agent(self.agent, payload, ("line",), {"facts": facts, "e": explanation},
                                        self.metrics, self.timeout_s)
        line = data["line"] if data else templates.commentary(facts, explanation)
        source = "llm" if data and expl_source == "llm" else "fallback"
        return base_overlay(moment, 2, source, explanation["headline"], line, explanation["why"],
                            explanation.get("evidence"), explanation.get("tag"), expl_reason or reason)

    async def recap(self, kind, snap, key_moments, window_s):
        facts = match_facts(snap, None, window_s)
        facts["kind"] = kind
        facts["key_moments"] = [{"clock": m["clock"], "text": m["alert"]["text"]} for m in key_moments]
        ranked = sorted(snap["players"].values(),
                        key=lambda p: (p["goals"] * 3 + p["assists"] * 2 + p["xg"] + p["tackles_won"] * 0.2),
                        reverse=True)[:3]
        facts["top_players"] = [{"name": p["name"], "team": snap["teams"][p["team"]]["name"], "goals": p["goals"],
                                 "assists": p["assists"], "shots": p["shots"], "xg": p["xg"],
                                 "pass_accuracy_pct": p["pass_accuracy_pct"], "distance_km": p["distance_km"]}
                                for p in ranked if p.get("team") in snap["teams"]]
        data, reason = await call_agent(self.agent, {"task": "recap", "facts": facts},
                                        ("title", "summary", "key_moments", "player_of_the_period"), facts,
                                        self.metrics, self.timeout_s * 2)
        out = data or templates.recap(facts)
        return {"overlay_id": f"recap-{kind}", "version": 2, "kind": "recap", "recap_type": kind,
                "source": "llm" if data else "fallback", "fallback_reason": reason,
                "clock": snap["clock"], "elapsed_s": snap["elapsed_s"], "period": snap["period"],
                "headline": out["title"], "text": out["summary"], "why": None,
                "key_moments": out["key_moments"], "player_of_the_period": out["player_of_the_period"],
                "display": {"slot": "full_screen", "duration_s": 20, "show_at_elapsed_s": snap["elapsed_s"]}}, facts

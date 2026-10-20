"""Explainer agent (LLM): says WHY a moment matters, using only the stats it is given."""

from agent_framework import Agent

from agents import templates
from agents.common import call_agent

INSTRUCTIONS = """You are a football match analyst for live TV graphics.
You receive JSON: {"task": "explain", "facts": {...}}. "facts.moment" is what just happened.
Explain WHY it matters - scoreline context, control vs chaos, pressure, momentum, rhythm - not just WHAT happened.

Rules:
- Use ONLY numbers that appear in "facts". Never invent statistics, history, records or names.
- Mention only players and teams named in "facts". We have no data on seasons, careers, injuries,
  transfers, crowds or past matches - never mention them.
- headline: at most 8 words. why: at most 2 short sentences.
- Sentence case: capitalise only the first word and names ("Riverside take control", not "Riverside Take Control").
- evidence: 1-3 items {"label": str, "value": number or str}, values copied from facts.
- tag: one of goal, turning_point, chance, pressure, control, chaos, milestone, discipline, change.
Reply with JSON only: {"headline": str, "why": str, "evidence": [...], "tag": str}"""

REQUIRED = ("headline", "why", "evidence", "tag")


class Explainer:
    def __init__(self, client, metrics, timeout_s):
        self.agent = Agent(client, INSTRUCTIONS, name="explainer")
        self.metrics, self.timeout_s = metrics, timeout_s

    async def explain(self, facts):
        data, reason = await call_agent(self.agent, {"task": "explain", "facts": facts}, REQUIRED, facts,
                                        self.metrics, self.timeout_s)
        if data:
            return data, "llm", None
        return templates.explain(facts), "fallback", reason

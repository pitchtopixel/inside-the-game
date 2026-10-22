"""
Personalizer agent: one stream of intelligence, a different experience per viewer.

Profile: {"profile_id", "mode": "analyst"|"fan", "language": "en"|"es"|"hi"|"ar"|...,
          "club": team_id|None, "player": player_id|None, "metric": "xg"|"possession"|...|None}
Rules decide WHAT a viewer sees (filtering, highlights, metric, player tracker); the LLM rewrites HOW it
reads (fan tone, translation). Analyst + English needs no LLM call.
"""

import asyncio

from agent_framework import Agent

from agents.common import call_agent, headline_names, verification

LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French", "pt": "Portuguese", "hi": "Hindi", "ar": "Arabic"}
TEAM_METRICS = {"xg": ("xG", "xg"), "possession": ("Possession %", "possession_pct"),
                "pass_accuracy": ("Pass accuracy %", "pass_accuracy_pct"), "shots": ("Shots", "shots"),
                "pressures": ("Pressures", "pressures")}
PLAYER_METRICS = {"top_speed": ("Top speed km/h", "top_speed_kmh"), "distance": ("Distance km", "distance_km"),
                  "pass_accuracy_player": ("Pass accuracy %", "pass_accuracy_pct")}
NON_LATIN = {"ar", "hi"}   # scripts where a model tends to transliterate names - those answers must keep them
DEFAULT_PROFILE = {"mode": "analyst", "language": "en", "club": None, "player": None, "metric": None}
FAN_MIN_IMPORTANCE = 60
FOCUS_MIN_IMPORTANCE = 70   # player-focus mode: other moments only if big

INSTRUCTIONS = """You adapt live football graphics text for one viewer.
Input JSON: {"task": "personalize", "profile": {"mode", "language", "club_name", "player_name"},
             "overlay": {"headline", "text", "why"}}
- mode "fan": simple, warm, no jargon, at most 20 words per field; keep the score and at most one number.
- mode "analyst": keep the stats and precise wording.
- If club_name is set, write from that club's supporters' point of view (still factual).
- Translate every field into the profile language (natural broadcast style).
- Never add facts, numbers or names that are not in the overlay; keep player and team names exactly as given,
  in Latin letters, in every language (Arabic and Hindi too): write "Arribas", never translate or transliterate a name.
- Sentence case: capitalise only the first word and names.
Reply with JSON only: {"headline": str, "text": str, "why": str}"""


class Personalizer:
    def __init__(self, client, metrics, timeout_s, profiles, team_names, player_names=None):
        self.agent = Agent(client, INSTRUCTIONS, name="personalizer")
        self.metrics, self.timeout_s = metrics, timeout_s
        self.profiles = {p["profile_id"]: {**DEFAULT_PROFILE, **p} for p in profiles}
        self.team_names = team_names
        self.player_names = player_names or {}
        self.cache = {}

    def add_profile(self, profile):
        self.profiles[profile["profile_id"]] = {**DEFAULT_PROFILE, **profile}

    # ---------- rules ----------
    def visible(self, ov, prof):
        if ov["kind"] != "moment":
            return True
        if prof["player"] and ov.get("player_id") == prof["player"]:
            return True
        if ov["alert_type"] == "goal":
            return True
        need = FAN_MIN_IMPORTANCE if prof["mode"] == "fan" else 0
        if prof["player"]:
            need = max(need, FOCUS_MIN_IMPORTANCE)
        return ov["importance"] >= need

    def metric_line(self, prof, snap):
        m = prof["metric"]
        if m in TEAM_METRICS:
            label, key = TEAM_METRICS[m]
            return {"label": label, "values": {t["name"]: t[key] for t in snap["teams"].values()}}
        if m in PLAYER_METRICS and prof["player"]:
            label, key = PLAYER_METRICS[m]
            p = snap["players"].get(prof["player"])
            if p:
                return {"label": label, "values": {p["name"]: p[key]}}
            # not in the stats yet (on the bench / no touch yet): show the name with a dash, not nothing
            return {"label": label, "values": {self.player_names.get(prof["player"], prof["player"]): "–"}}
        return None

    def decorate(self, ov, prof, snap):
        out = dict(ov, profile_id=prof["profile_id"], mode=prof["mode"], language="en",
                   pending_translation=prof["language"] != "en")
        out["highlight"] = {"your_club": bool(prof["club"]) and ov.get("team") == prof["club"],
                            "your_player": bool(prof["player"]) and ov.get("player_id") == prof["player"]}
        out["metric"] = self.metric_line(prof, snap)
        if prof["mode"] == "fan":
            out["evidence"] = []
        return out

    # ---------- outputs ----------
    def instant(self, ov, snap):
        """Version-1 overlays for every viewer who should see this - no LLM, immediate."""
        return [self.decorate(ov, p, snap) for p in self.profiles.values() if self.visible(ov, p)]

    async def personalize(self, ov, snap, facts):
        """Version-2 overlays: LLM rewrite / translation where the profile needs it, in parallel."""
        targets = [p for p in self.profiles.values() if self.visible(ov, p)]
        return await asyncio.gather(*(self._one(ov, p, snap, facts) for p in targets))

    async def _one(self, ov, prof, snap, facts):
        out = self.decorate(ov, prof, snap)
        if prof["mode"] == "analyst" and prof["language"] == "en":
            return out
        key = (ov["overlay_id"], ov["version"], prof["mode"], prof["language"], prof["club"], prof["player"])
        fields = {"headline": ov["headline"], "text": ov["text"], "why": ov.get("why") or ""}
        if key not in self.cache:
            payload = {"task": "personalize", "overlay": fields,
                       "profile": {"mode": prof["mode"], "language": LANGUAGES.get(prof["language"], prof["language"]),
                                   "language_code": prof["language"],
                                   "club_name": self.team_names.get(prof["club"]),
                                   "player_name": snap["players"].get(prof["player"], {}).get("name")}}
            keep = headline_names(fields, facts) if prof["language"] in NON_LATIN else ()
            data, reason = await call_agent(self.agent, payload, ("headline", "text", "why"),
                                            {"overlay": fields, "facts": facts}, self.metrics, self.timeout_s,
                                            keep_names=keep)
            self.cache[key] = (data, reason)
        data, reason = self.cache[key]
        if data:
            out.update(headline=data["headline"], text=data["text"], why=data["why"] or None,
                       language=prof["language"], pending_translation=False)
            if ov.get("source") == "llm":
                out["verified"] = verification({k: data[k] for k in ("headline", "text", "why")},
                                               {"overlay": fields, "facts": facts})
        else:
            out.update(source="fallback", fallback_reason=f"personalizer:{reason}", pending_translation=False)
        return out

    def tickers(self, snap):
        """Every 5 minutes: player tracker (player-focus mode) and single-metric ticker, template only."""
        out = []
        for prof in self.profiles.values():
            p = snap["players"].get(prof["player"]) if prof["player"] else None
            if p:
                text = (f"{p['name']}: {p['touches']} touches, {p['passes']} passes ({p['pass_accuracy_pct']}%), "
                        f"{p['shots']} shots, {p['goals']} goals, {p['distance_km']} km, top speed {p['top_speed_kmh']} km/h")
                out.append(self._ticker("player_tracker", prof, snap, f"{p['name']} tracker", text))
            metric = self.metric_line(prof, snap)
            if metric:
                text = " | ".join(f"{k} {v}" for k, v in metric["values"].items())
                out.append(self._ticker("metric_ticker", prof, snap, metric["label"], text))
        return out

    @staticmethod
    def _ticker(kind, prof, snap, headline, text):
        return {"overlay_id": f"{kind}-{prof['profile_id']}-{int(snap['elapsed_s'])}", "version": 1, "kind": kind,
                "source": "template", "profile_id": prof["profile_id"], "mode": prof["mode"], "language": "en",
                "clock": snap["clock"], "elapsed_s": snap["elapsed_s"], "period": snap["period"],
                "headline": headline, "text": text,
                "display": {"slot": "side_panel", "duration_s": 8, "show_at_elapsed_s": snap["elapsed_s"]}}

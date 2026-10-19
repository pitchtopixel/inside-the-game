"""
Moments agent (rules): turns stats-engine alerts into ranked key moments.
Decides importance, category and whether a moment is worth an LLM explanation.
"""

IMPORTANCE = {"goal": 95, "big_chance": 70, "momentum": 65, "pressure_spell": 55, "rhythm": 55, "card": 50,
              "possession_streak": 45, "sprint_record": 40, "shot_speed_record": 40, "distance_milestone": 35,
              "substitution": 25}
CATEGORY = {"goal": "goal", "big_chance": "chance", "momentum": "turning_point", "pressure_spell": "pressure",
            "card": "discipline", "possession_streak": "control", "sprint_record": "milestone",
            "shot_speed_record": "milestone", "distance_milestone": "milestone", "substitution": "change"}
EXPLAIN_MIN = 45      # moments below this get the instant template only (saves LLM cost)
KEY_MOMENT_MIN = 60   # moments at or above this go into recaps


class MomentsAgent:
    def __init__(self):
        self.key_moments = []
        self.score = None

    def assess(self, alert, event, snap):
        typ, tags = alert["type"], []
        importance = IMPORTANCE.get(typ, 30)
        category = CATEGORY.get(typ) or (alert["data"].get("state") if typ == "rhythm" else "info")
        if alert["priority"] == "high" and typ != "goal":
            importance = max(importance, 85)  # e.g. red card

        if typ == "goal":
            before, after = self.score or {t: 0 for t in snap["score"]}, alert["data"]["score"]
            team, opp = alert["team"], next(t for t in after if t != alert["team"])
            lead_before = before[team] - before[opp]
            if lead_before in (-1, 0):     # equaliser or go-ahead goal
                tags.append("turning_point")
                importance = 100
            if alert["data"].get("player_goals", 0) >= 2:
                tags.append("milestone")
        if typ == "goal" or "score" in alert.get("data", {}):
            self.score = dict(alert["data"].get("score", snap["score"]))
        if event["minute"] >= 80 and typ in ("goal", "big_chance", "momentum"):
            tags.append("late")
            importance = min(100, importance + 5)

        moment = {"moment_id": f"m-{alert['alert_id']}", "alert": alert, "type": typ, "category": category,
                  "tags": tags, "importance": importance, "explain": importance >= EXPLAIN_MIN,
                  "event_id": event["event_id"], "clock": alert["clock"], "minute": event["minute"],
                  "elapsed_s": alert["elapsed_s"], "period": alert["period"], "team": alert["team"],
                  "player_id": alert.get("player_id")}
        if importance >= KEY_MOMENT_MIN:
            self.key_moments.append(moment)
        return moment

"""
Deterministic text built only from the facts given. Used for instant overlays, as the fallback
whenever an LLM call fails or is not grounded, and by the offline mock model.
"""


def _first_sentence(text):
    return text.split(". ")[0].rstrip(".") + "."


def _lead_phrase(f, team, opp):
    s = f["score"]
    if s[team] > s[opp]:
        return f"{team} lead {s[team]}-{s[opp]}"
    if s[team] < s[opp]:
        return f"{team} trail {s[team]}-{s[opp]}"
    return f"All square at {s[team]}-{s[opp]}"


def explain(f):
    m = f["moment"]
    typ, d = m["type"], m.get("data") or {}
    team, opp, player = m.get("team_name"), m.get("opponent_name"), m.get("player_name")
    ts = f["teams"].get(team, {}) if team else {}
    os_ = f["teams"].get(opp, {}) if opp else {}
    w = f["window_min"]
    evidence, tag = [], m.get("category", "info")

    if typ == "goal":
        headline = f"{player} scores for {team}"
        why = (f"{_lead_phrase(f, team, opp)}. {team} have {ts['shots']} shots and {ts['xg']} xG "
               f"to {os_['shots']} and {os_['xg']} for {opp}.")
        goals = (f.get("player") or {}).get("goals", 0)
        if goals >= 2:
            why += f" That is goal number {goals} for {player} today."
        evidence = [{"label": f"{team} xG", "value": ts["xg"]}, {"label": f"{opp} xG", "value": os_["xg"]}]
        tag = "turning_point" if "turning_point" in m.get("tags", []) else "goal"
    elif typ == "momentum":
        share = d.get("share")
        headline = f"{team} take control"
        why = (f"{team} hold {share}% of the attacking threat over the last {w} minutes. "
               f"{opp} are being pinned back and need to break the pressure.")
        evidence = [{"label": f"{team} threat share, last {w} min", "value": f"{share}%"},
                    {"label": f"{team} final-third passes", "value": ts.get("final_third_passes")}]
        tag = "pressure"
    elif typ == "big_chance":
        headline = f"Big chance for {player}"
        why = (f"A chance worth {d.get('xg')} xG, the kind scored about {d.get('xg_pct')}% of the time. "
               f"{team} now have {ts.get('xg')} xG in total.")
        evidence = [{"label": "Chance xG", "value": d.get("xg")}, {"label": f"{team} total xG", "value": ts.get("xg")}]
        tag = "chance"
    elif typ == "rhythm" and d.get("state") == "chaos":
        headline = "The game opens up"
        why = (f"The ball is changing hands {d.get('turnovers_per_min')} times a minute. Nobody is in control, "
               f"so quick transitions will decide the next phase.")
        evidence = [{"label": "Turnovers per minute", "value": d.get("turnovers_per_min")}]
        tag = "chaos"
    elif typ == "rhythm":
        share = f["rhythm"]["possession_share_pct"].get(team)
        headline = f"{team} dictate the tempo"
        why = (f"{team} have {share}% of the ball over the last {w} minutes and the ball changes hands only "
               f"{d.get('turnovers_per_min')} times a minute. {opp} are chasing.")
        evidence = [{"label": f"{team} possession, last {w} min", "value": f"{share}%"},
                    {"label": "Turnovers per minute", "value": d.get("turnovers_per_min")}]
        tag = "control"
    elif typ == "pressure_spell":
        headline = f"{team} turn up the press"
        why = (f"{team} have made {d.get('pressures')} pressures in {w} minutes against {d.get('opponent_pressures')} "
               f"by {opp}, forcing them into rushed decisions.")
        evidence = [{"label": f"{team} pressures, last {w} min", "value": d.get("pressures")}]
        tag = "pressure"
    elif typ == "possession_streak":
        headline = f"{team} keep the ball"
        why = f"{d.get('passes')} passes in a row: {team} are controlling the rhythm and making {opp} chase."
        evidence = [{"label": "Passes in the move", "value": d.get("passes")}]
        tag = "control"
    elif typ == "sprint_record":
        headline = f"{player} at full speed"
        why = f"{player} reaches {d.get('top_speed_kmh')} km/h, the fastest sprint of the match so far."
        evidence = [{"label": "Top speed (km/h)", "value": d.get("top_speed_kmh")}]
        tag = "milestone"
    elif typ == "shot_speed_record":
        headline = f"Rocket from {player}"
        why = f"{player} strikes it at {d.get('shot_speed_kmh')} km/h, the fastest shot of the match."
        evidence = [{"label": "Shot speed (km/h)", "value": d.get("shot_speed_kmh")}]
        tag = "milestone"
    elif typ == "distance_milestone":
        headline = f"{player} leads the running"
        why = f"{player} has covered {d.get('distance_km')} km, the first player to reach this mark today."
        evidence = [{"label": "Distance (km)", "value": d.get("distance_km")}]
        tag = "milestone"
    elif typ == "card":
        headline = f"Booking for {player}"
        why = f"{player} goes into the book. {team} have now committed {ts.get('fouls')} fouls."
        evidence = [{"label": f"{team} fouls", "value": ts.get("fouls")}]
        tag = "discipline"
    elif typ == "substitution":
        headline = f"Change for {team}"
        why = f"{d.get('player_in_name')} replaces {player} for {team}. {_lead_phrase(f, team, opp)}."
        tag = "change"
    else:
        headline, why = m.get("text", "Match update"), m.get("text", "")
    return {"headline": headline, "why": why, "evidence": evidence, "tag": tag}


def commentary(f, explanation):
    return f"{f['clock']} - {explanation['headline']}! {_first_sentence(explanation['why'])}"


def recap(f):
    names = list(f["teams"])
    h, a = f["teams"][names[0]], f["teams"][names[1]]
    period = "Half-time" if f["kind"] == "half_time" else "Full-time"
    summary = (f"{period}: {f['score_line']}. {names[0]} had {h['possession_pct']}% of the ball and "
               f"{h['shots']} shots ({h['xg']} xG) against {a['shots']} shots ({a['xg']} xG) for {names[1]}. "
               f"Pass accuracy was {h['pass_accuracy_pct']}% to {a['pass_accuracy_pct']}%.")
    star = f["top_players"][0] if f["top_players"] else None
    pick = None
    if star:
        pick = {"name": star["name"],
                "reason": f"{star['goals']} goals, {star['shots']} shots and {star['xg']} xG for {star['team']}."}
    return {"title": f"{period}: {f['score_line']}", "summary": summary,
            "key_moments": [f"{k['clock']} {k['text']}" for k in f["key_moments"]],
            "player_of_the_period": pick}

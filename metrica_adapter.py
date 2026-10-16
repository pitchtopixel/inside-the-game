"""
Dev-only: convert the Metrica sample match (real, anonymised) into our event schema so the stats
engine can be sanity-checked on unscripted football. Not part of the submission - real data stays local.

Not available in Metrica: pressure events, sprints, physical data, shot speed. xG uses our own model.
"""

import math
import os

import pandas as pd

from generate_match import HALF
from metrics import L, W, xg_model

SRC = os.path.join("data", "raw", "metrica", "Sample_Game_1_RawEventsData.csv")
TEAM_IDS = {"Home": "HOM", "Away": "AWY"}
SET_PIECES = {"CORNER KICK": "corner", "THROW IN": "throw_in", "GOAL KICK": "goal_kick", "FREE KICK": "free_kick"}


def pid(name):
    return None if pd.isna(name) else name.replace("Player", "P")


def convert(path=SRC):
    df = pd.read_csv(path)
    # Orient so home attacks towards x=105 in period 1 (our convention); teams swap ends at half-time.
    home_p1 = df[(df.Type == "SHOT") & (df.Team == "Home") & (df.Period == 1)]
    flip = home_p1["Start X"].mean() < 0.5
    for c in ("Start X", "End X"):
        df[c] = (1 - df[c] if flip else df[c]) * L
    for c in ("Start Y", "End Y"):
        df[c] = (1 - df[c] if flip else df[c]) * W
    p_start = df.groupby("Period")["Start Time [s]"].min().to_dict()

    events, score = [], {"HOM": 0, "AWY": 0}

    def add(row, etype, team, player, outcome=None, end=True, **details):
        period, t_abs = int(row["Period"]), float(row["Start Time [s]"])
        t = t_abs - p_start[period]
        base = 45 * (period - 1)
        if t < HALF:
            s = int(t) + base * 60
            clock, minute, added = f"{s // 60:02d}:{s % 60:02d}", base + int(t // 60) + 1, 0
        else:
            extra = int(t - HALF)
            clock, minute, added = f"{base + 45}+{extra // 60}:{extra % 60:02d}", base + 45, extra // 60 + 1
        has_end = end and not pd.isna(row["End X"])
        events.append({
            "event_id": f"metrica-e{len(events) + 1:04d}", "type": etype, "period": period, "clock": clock,
            "minute": minute, "added_minute": added, "elapsed_s": round(t_abs, 2), "team": team,
            "player_id": player, "player_name": player,
            "x": round(row["Start X"], 1) if not pd.isna(row["Start X"]) else None,
            "y": round(row["Start Y"], 1) if not pd.isna(row["Start Y"]) else None,
            "end_x": round(row["End X"], 1) if has_end else None, "end_y": round(row["End Y"], 1) if has_end else None,
            "outcome": outcome, "details": details,
        })

    last_period = None
    for _, r in df.iterrows():
        team, other = TEAM_IDS[r["Team"]], TEAM_IDS["Away" if r["Team"] == "Home" else "Home"]
        typ, sub = r["Type"], "" if pd.isna(r["Subtype"]) else str(r["Subtype"])
        frm, to = pid(r["From"]), pid(r["To"])
        if r["Period"] != last_period:
            if last_period is not None:
                add(prev, "period_end", None, None, end=False, score=dict(score))
            add(r, "period_start", None, None, end=False)
            last_period = r["Period"]
        prev = r

        dist = math.hypot(r["End X"] - r["Start X"], r["End Y"] - r["Start Y"]) if not pd.isna(r["End X"]) else None
        dur = r["End Time [s]"] - r["Start Time [s]"]
        speed = round(dist / dur * 3.6, 1) if dist and dur > 0.2 else None

        if typ in ("PASS", "BALL LOST"):
            add(r, "pass", team, frm, "complete" if typ == "PASS" else "incomplete",
                distance_m=round(dist, 1) if dist else None, ball_speed_kmh=speed, recipient_id=to,
                subtype=sub.lower() or None)
        elif typ == "SHOT":
            outcome = ("goal" if "GOAL" in sub else "saved" if "SAVED" in sub else "blocked" if "BLOCKED" in sub
                       else "post" if "WOODWORK" in sub else "off_target")
            attacks_right = (team == "HOM") == (r["Period"] == 1)
            x_rel = r["Start X"] if attacks_right else L - r["Start X"]
            y_rel = r["Start Y"] if attacks_right else W - r["Start Y"]
            xg = xg_model(x_rel, y_rel, header="HEAD" in sub)
            add(r, "shot", team, frm, outcome, xg=xg, big_chance=xg >= 0.3, distance_m=round(math.hypot(L - x_rel, W / 2 - y_rel), 1))
            if outcome == "goal":
                score[team] += 1
                add(r, "goal", team, frm, end=False, score=dict(score), xg=xg)
        elif typ == "RECOVERY":
            add(r, "interception" if "INTERCEPTION" in sub else "possession_change", team, frm, end=False)
        elif typ == "CHALLENGE" and "TACKLE" in sub:
            add(r, "tackle", team, frm, "won" if "WON" in sub else "lost", end=False)
        elif typ == "SET PIECE":
            if sub == "KICK OFF":
                add(r, "kick_off", team, frm, end=False)
            else:
                add(r, "set_piece", team, frm, end=False, subtype=SET_PIECES.get(sub, sub.lower()))
        elif typ == "FAULT RECEIVED":
            add(r, "foul", other, None, end=False, fouled_player_id=frm)
        elif typ == "CARD":
            add(r, "card", team, frm, end=False, card="red" if "RED" in sub else "yellow")
        elif typ == "BALL OUT":
            add(r, "ball_out", team, frm, end=False)
    add(prev, "period_end", None, None, end=False, score=dict(score))
    add(prev, "match_end", None, None, end=False, score=dict(score))

    players = {}
    for e in events:
        if e["player_id"]:
            players.setdefault(e["player_id"], e["team"])
    teams = {side: {"team_id": tid, "name": f"Metrica {side.title()}", "short_name": side.title(),
                    "players": [{"player_id": p, "name": p, "short_name": p} for p, t in players.items() if t == tid]}
             for side, tid in (("home", "HOM"), ("away", "AWY"))}
    match = {"match_id": "metrica_sample_1", "synthetic": False, "final_score": score}
    return {"match": match, "teams": teams, "events": events}


if __name__ == "__main__":
    data = convert()
    print(f"converted {len(data['events'])} events, score {data['match']['final_score']}")

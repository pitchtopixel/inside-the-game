"""
Stats engine: turns the live event stream into match state, stats, patterns and alerts.
Pure Python, no I/O - feed it events one at a time.

    state = MatchState(meta)        # meta = {"match": ..., "teams": ...} (the streamer's "meta" message)
    alerts = state.update(event)    # list of new alerts (often empty)
    state.snapshot()                # full current stats as a dict

Coordinates follow the generator: home attacks towards x=105 in period 1, towards x=0 in period 2.
"""

import math
from collections import defaultdict, deque

from metrics import L, W, pass_difficulty, xg_model

SCHEMA_VERSION = "1.0"  # snapshot + alert format, see docs/schemas.md

THRESHOLDS = {
    "window_s": 300,                  # rolling window for momentum / rhythm / pressure
    "check_every_s": 60,              # how often patterns are evaluated
    "warmup_s": 180,                  # no pattern alerts before this much play
    "momentum_share": 0.65,           # share of threat needed to be "on top"
    "momentum_min_threat": 14.0,      # ignore quiet windows (tuned on both matches vs true momentum)
    "momentum_confirm_checks": 2,     # consecutive checks before a swing is confirmed
    "big_chance_xg": 0.3,
    "final_third_x": 70.0,
    "hard_pass_difficulty": 0.6,
    "sprint_record_kmh": 33.0,        # only alert new match-record sprints above this
    "shot_record_kmh": 100.0,         # only alert new match-record shots above this
    "distance_milestone_km": 10.0,    # first player to reach this
    "possession_streak_passes": 12,
    "control_min_share": 0.6,         # control phase needs one side holding this much of the ball
    "pressure_spell_count": 15,       # pressures in window
    "pressure_spell_ratio": 2.0,      # vs opponent
    "chaos_turnovers_per_min": 4.0,
    "control_turnovers_per_min": 2.0,
    "low_priority_gap_s": 240,        # anti-spam: min gap between low-priority alerts
    "cooldown_s": {"momentum": 480, "rhythm": 600, "pressure_spell": 600, "possession_streak": 300},
}

ON_BALL = {"pass", "shot", "set_piece", "kick_off", "possession_change", "interception", "clearance", "save"}


def new_team():
    return dict(goals=0, shots=0, shots_on_target=0, xg=0.0, big_chances=0, passes=0, passes_complete=0,
                pass_distance_m=0.0, pass_difficulty=0.0, pass_difficulty_n=0, hard_passes=0,
                hard_passes_complete=0, final_third_passes=0, tackles=0, tackles_won=0, interceptions=0,
                pressures=0, fouls=0, yellow_cards=0, red_cards=0, corners=0, sprints=0, fastest_shot_kmh=0.0,
                ball_speed_sum=0.0, ball_speed_n=0, possession_s=0.0, turnovers=0)


def new_player():
    return dict(touches=0, passes=0, passes_complete=0, pass_distance_m=0.0, pass_difficulty=0.0,
                pass_difficulty_n=0, shots=0, goals=0, assists=0, xg=0.0, tackles_won=0, interceptions=0,
                pressures=0, sprints=0, top_speed_kmh=0.0, distance_km=0.0, yellow_cards=0)


def pct(a, b):
    return round(100 * a / b, 1) if b else 0.0


class MatchState:
    def __init__(self, meta, thresholds=None):
        self.th = {**THRESHOLDS, **(thresholds or {})}
        teams = meta["teams"]
        self.home, self.away = teams["home"]["team_id"], teams["away"]["team_id"]
        self.short = {t["team_id"]: t.get("short_name", t["team_id"]) for t in teams.values()}
        self.names, self.player_team = {}, {}
        for t in teams.values():
            for p in t.get("players", []):
                self.names[p["player_id"]] = p.get("short_name") or p["name"]
                self.player_team[p["player_id"]] = t["team_id"]
        self.teams = {self.home: new_team(), self.away: new_team()}
        self.players = defaultdict(new_player)
        self.score = {self.home: 0, self.away: 0}
        self.period, self.clock, self.now = 1, "00:00", 0.0
        self.poss_team, self.last_t = None, None
        self.spell_passes, self.spell_alerted = 0, False
        self.window = deque()  # (elapsed, team, metric, value)
        self.next_check = self.th["check_every_s"]
        self.dominant, self.candidate, self.candidate_n = None, None, 0
        self.last_on_top = None  # last team announced as on top (no repeat alerts for the same team)
        self.rhythm, self.rhythm_info = "balanced", {}
        self.momentum_share = {self.home: 50.0, self.away: 50.0}
        self.momentum_timeline = []  # (elapsed, home_share, dominant)
        self.last_alert, self.last_low = {}, -1e9
        self.records = {"sprint": 0.0, "shot": 0.0}
        self.km_milestone_done = False
        self.last_body_part = None
        self.alerts, self._new = [], []
        self.period_start_t = 0.0
        self.seen, self.rejected = set(), defaultdict(int)  # bad input is skipped and counted, never fatal

    # ---------- helpers ----------
    def other(self, team):
        return self.away if team == self.home else self.home

    def x_rel(self, team, x):
        """x in the team's attacking frame (always attacking towards 105)."""
        return x if (team == self.home) == (self.period == 1) else L - x

    def y_rel(self, team, y):
        return y if (team == self.home) == (self.period == 1) else W - y

    def name(self, pid):
        return self.names.get(pid, pid)

    def _alert(self, kind, priority, text, team=None, pid=None, **data):
        cd = self.th["cooldown_s"].get(kind)
        if cd and self.now - self.last_alert.get((kind, team), -1e9) < cd:
            return
        if priority == "low" and self.now - self.last_low < self.th["low_priority_gap_s"]:
            return
        self.last_alert[(kind, team)] = self.now
        if priority == "low":
            self.last_low = self.now
        a = {"alert_id": f"a{len(self.alerts) + 1:03d}", "type": kind, "priority": priority,
             "elapsed_s": self.now, "clock": self.clock, "period": self.period, "team": team,
             "player_id": pid, "player_name": self.names.get(pid) if pid else None, "text": text, "data": data}
        self.alerts.append(a)
        self._new.append(a)

    def _possess(self, team):
        if team is None or team == self.poss_team:
            return
        if self.poss_team is not None:
            self.teams[self.poss_team]["turnovers"] += 1
            self.window.append((self.now, self.poss_team, "turnover", 1))
        self.poss_team, self.spell_passes, self.spell_alerted = team, 0, False

    # ---------- main entry ----------
    def _reject_reason(self, ev):
        if not isinstance(ev, dict):
            return "not_an_object"
        if not isinstance(ev.get("event_id"), str) or not isinstance(ev.get("type"), str):
            return "malformed"
        if not isinstance(ev.get("elapsed_s"), (int, float)) or not isinstance(ev.get("period"), int):
            return "malformed"
        if ev["event_id"] in self.seen:
            return "duplicate"
        if self.last_t is not None and ev["elapsed_s"] < self.last_t - 1.0:
            return "out_of_order"
        if ev.get("team") is not None and ev["team"] not in self.teams:
            return "unknown_team"
        return None

    def update(self, ev):
        self._new = []
        reason = self._reject_reason(ev)
        if reason:
            self.rejected[reason] += 1
            return []
        self.seen.add(ev["event_id"])
        try:
            return self._apply(ev)
        except (KeyError, TypeError, ValueError, AttributeError):
            self.rejected["handler_error"] += 1
            return self._new

    def _apply(self, ev):
        t = ev["elapsed_s"]
        if self.poss_team is not None and self.last_t is not None and t > self.last_t:
            self.teams[self.poss_team]["possession_s"] += t - self.last_t
            self.window.append((t, self.poss_team, "poss", t - self.last_t))
        self.last_t = t
        while t >= self.next_check:
            self.now = self.next_check
            self._evaluate()
            self.next_check += self.th["check_every_s"]
        self.period, self.clock, self.now = ev["period"], ev["clock"], t

        etype, team, pid = ev["type"], ev.get("team"), ev.get("player_id")
        d = ev.get("details") or {}
        if pid and team:
            self.player_team.setdefault(pid, team)
        if etype in ON_BALL or (etype == "tackle" and ev.get("outcome") == "won"):
            self._possess(team)
            if pid:
                self.players[pid]["touches"] += 1
        handler = getattr(self, f"_on_{etype}", None)
        if handler:
            handler(ev, team, pid, d)
        return self._new

    # ---------- event handlers ----------
    def _on_period_start(self, ev, team, pid, d):
        # New half: ends swap and play restarts, so rolling patterns start fresh.
        self.poss_team = None
        self.window.clear()
        self.candidate, self.candidate_n = None, 0
        self.period_start_t = ev["elapsed_s"]

    def _on_pass(self, ev, team, pid, d):
        ts, ps = self.teams[team], self.players[pid]
        complete = ev.get("outcome") == "complete"
        dist, diff = d.get("distance_m"), None
        if None not in (ev.get("x"), ev.get("y"), ev.get("end_x"), ev.get("end_y")):
            dist = dist or math.hypot(ev["end_x"] - ev["x"], ev["end_y"] - ev["y"])
            sx, ex = self.x_rel(team, ev["x"]), self.x_rel(team, ev["end_x"])
            diff = pass_difficulty(dist, ex - sx, bool(d.get("pressured")), ex, d.get("subtype") == "cross")
        dist = dist or 0.0
        for s in (ts, ps):
            s["passes"] += 1
            s["passes_complete"] += complete
            s["pass_distance_m"] += dist
            if diff is not None:
                s["pass_difficulty"] += diff
                s["pass_difficulty_n"] += 1
        if diff is not None and diff >= self.th["hard_pass_difficulty"]:
            ts["hard_passes"] += 1
            ts["hard_passes_complete"] += complete
        if d.get("ball_speed_kmh"):
            ts["ball_speed_sum"] += d["ball_speed_kmh"]
            ts["ball_speed_n"] += 1
        if not complete:
            return
        self.spell_passes += 1
        if ev.get("end_x") is not None and self.x_rel(team, ev["end_x"]) >= self.th["final_third_x"]:
            ts["final_third_passes"] += 1
            self.window.append((self.now, team, "threat", 1.0))
        if self.spell_passes >= self.th["possession_streak_passes"] and not self.spell_alerted:
            self.spell_alerted = True
            self._alert("possession_streak", "low",
                        f"{self.short[team]} keep the ball: {self.spell_passes} passes in a row", team,
                        passes=self.spell_passes)

    def _on_shot(self, ev, team, pid, d):
        ts, ps = self.teams[team], self.players[pid]
        xg, speed, outcome = d.get("xg"), d.get("shot_speed_kmh") or 0.0, ev.get("outcome") or "unknown"
        if xg is None and ev.get("x") is not None:
            xg = xg_model(self.x_rel(team, ev["x"]), self.y_rel(team, ev["y"]), d.get("body_part") == "head")
        xg = xg or 0.0
        self.last_body_part = d.get("body_part")  # the goal event that follows reuses it
        for s in (ts, ps):
            s["shots"] += 1
            s["xg"] += xg
        ts["shots_on_target"] += outcome in ("goal", "saved")
        ts["fastest_shot_kmh"] = max(ts["fastest_shot_kmh"], speed)
        self.window.append((self.now, team, "threat", 2.0 + 8.0 * xg))
        if xg >= self.th["big_chance_xg"]:
            ts["big_chances"] += 1
            if outcome != "goal":
                self._alert("big_chance", "medium",
                            f"Big chance for {self.name(pid)} ({self.short[team]}) - xG {xg:.2f}, {outcome.replace('_', ' ')}",
                            team, pid, xg=xg, outcome=outcome, shot_speed_kmh=speed or None,
                            body_part=d.get("body_part"))
        if speed >= self.th["shot_record_kmh"] and speed > self.records["shot"]:
            self.records["shot"] = speed
            self._alert("shot_speed_record", "low", f"Fastest shot of the match: {self.name(pid)}, {speed:.0f} km/h",
                        team, pid, shot_speed_kmh=speed)

    def _on_goal(self, ev, team, pid, d):
        self.score[team] += 1
        self.teams[team]["goals"] += 1
        ps = self.players[pid]
        ps["goals"] += 1
        if d.get("assist_id"):
            self.players[d["assist_id"]]["assists"] += 1
        tag = {2: " - that's a brace", 3: " - HAT-TRICK"}.get(ps["goals"], "")
        h, a = self.home, self.away
        self._alert("goal", "high",
                    f"GOAL! {self.name(pid)} scores for {self.short[team]}{tag}. "
                    f"{self.short[h]} {self.score[h]}-{self.score[a]} {self.short[a]}",
                    team, pid, score=dict(self.score), player_goals=ps["goals"], assist_id=d.get("assist_id"),
                    xg=d.get("xg"), shot_speed_kmh=d.get("shot_speed_kmh"),
                    body_part=self.last_body_part)

    def _on_tackle(self, ev, team, pid, d):
        won = ev.get("outcome") == "won"
        self.teams[team]["tackles"] += 1
        self.teams[team]["tackles_won"] += won
        if pid:
            self.players[pid]["tackles_won"] += won

    def _on_interception(self, ev, team, pid, d):
        self.teams[team]["interceptions"] += 1
        if pid:
            self.players[pid]["interceptions"] += 1

    def _on_pressure(self, ev, team, pid, d):
        self.teams[team]["pressures"] += 1
        if pid:
            self.players[pid]["pressures"] += 1
        self.window.append((self.now, team, "pressure", 1))
        if ev.get("x") is not None and self.x_rel(team, ev["x"]) > L / 2:
            self.window.append((self.now, team, "threat", 0.3))

    def _on_foul(self, ev, team, pid, d):
        self.teams[team]["fouls"] += 1

    def _on_card(self, ev, team, pid, d):
        red = d.get("card") == "red"
        self.teams[team]["red_cards" if red else "yellow_cards"] += 1
        if pid:
            self.players[pid]["yellow_cards"] += not red
        self._alert("card", "high" if red else "medium",
                    f"{'Red' if red else 'Yellow'} card: {self.name(pid)} ({self.short[team]})", team, pid,
                    card=d.get("card"))

    def _on_set_piece(self, ev, team, pid, d):
        if d.get("subtype") == "corner":
            self.teams[team]["corners"] += 1

    def _on_sprint(self, ev, team, pid, d):
        speed = d.get("top_speed_kmh") or 0.0
        self.teams[team]["sprints"] += 1
        ps = self.players[pid]
        ps["sprints"] += 1
        ps["top_speed_kmh"] = max(ps["top_speed_kmh"], speed)
        if speed >= self.th["sprint_record_kmh"] and speed > self.records["sprint"]:
            self.records["sprint"] = speed
            self._alert("sprint_record", "low", f"Top speed of the match: {self.name(pid)} hits {speed:.1f} km/h",
                        team, pid, top_speed_kmh=speed)

    def _on_physical_update(self, ev, team, pid, d):
        for row in d.get("players", []):
            ps = self.players[row["player_id"]]
            ps["distance_km"] = row["distance_km"]
            ps["top_speed_kmh"] = max(ps["top_speed_kmh"], row.get("top_speed_kmh") or 0.0)
            if not self.km_milestone_done and row["distance_km"] >= self.th["distance_milestone_km"]:
                self.km_milestone_done = True
                p = row["player_id"]
                self._alert("distance_milestone", "low",
                            f"{self.name(p)} is the first to cover {self.th['distance_milestone_km']:.0f} km",
                            self.player_team.get(p), p, distance_km=row["distance_km"])

    def _on_substitution(self, ev, team, pid, d):
        self._alert("substitution", "medium",
                    f"Substitution {self.short[team]}: {d.get('player_in_name')} on for {self.name(pid)}",
                    team, pid, player_in_id=d.get("player_in_id"))

    # ---------- rolling patterns ----------
    def _evaluate(self):
        start = self.now - self.th["window_s"]
        while self.window and self.window[0][0] < start:
            self.window.popleft()
        if self.now - self.period_start_t < self.th["warmup_s"]:
            return
        agg = defaultdict(float)
        for _, team, metric, value in self.window:
            agg[(team, metric)] += value
        h, a = self.home, self.away

        # Momentum: who is generating the threat?
        th_h, th_a = agg[(h, "threat")], agg[(a, "threat")]
        total = th_h + th_a
        share = th_h / total if total else 0.5
        self.momentum_share = {h: round(100 * share, 1), a: round(100 * (1 - share), 1)}
        state = None
        if total >= self.th["momentum_min_threat"]:
            state = h if share >= self.th["momentum_share"] else a if share <= 1 - self.th["momentum_share"] else None
        if state == self.candidate:
            self.candidate_n += 1
        else:
            self.candidate, self.candidate_n = state, 1
        if self.candidate_n >= self.th["momentum_confirm_checks"] and state != self.dominant:
            self.dominant = state
            if state and state != self.last_on_top:
                self.last_on_top = state
                self._alert("momentum", "medium",
                            f"Momentum shift: {self.short[state]} on top - {self.momentum_share[state]:.0f}% of the "
                            f"threat in the last {self.th['window_s'] // 60} minutes", state,
                            share=self.momentum_share[state])
        self.momentum_timeline.append((self.now, round(share, 3), self.dominant))

        # Pressure spells
        for team in (h, a):
            p, po = agg[(team, "pressure")], agg[(self.other(team), "pressure")]
            if p >= self.th["pressure_spell_count"] and p >= self.th["pressure_spell_ratio"] * max(po, 1):
                self._alert("pressure_spell", "low",
                            f"{self.short[team]} pressing hard: {p:.0f} pressures in {self.th['window_s'] // 60} minutes",
                            team, pressures=int(p), opponent_pressures=int(po))

        # Rhythm: control vs chaos
        mins = min(self.th["window_s"], self.now - self.period_start_t) / 60
        tpm = (agg[(h, "turnover")] + agg[(a, "turnover")]) / mins
        poss_h, poss_a = agg[(h, "poss")], agg[(a, "poss")]
        poss_share_h = poss_h / (poss_h + poss_a) if poss_h + poss_a else 0.5
        holder_share = max(poss_share_h, 1 - poss_share_h)
        rhythm = ("chaos" if tpm >= self.th["chaos_turnovers_per_min"]
                  else "control" if tpm <= self.th["control_turnovers_per_min"]
                  and holder_share >= self.th["control_min_share"] else "balanced")
        self.rhythm_info = {"turnovers_per_min": round(tpm, 2), "avg_spell_s": round(60 / tpm, 1) if tpm else None,
                            "possession_share": {h: round(100 * poss_share_h, 1), a: round(100 * (1 - poss_share_h), 1)}}
        if rhythm != self.rhythm:
            self.rhythm = rhythm
            if rhythm == "chaos":
                self._alert("rhythm", "low", f"Chaos phase: the ball is changing hands {tpm:.1f} times a minute",
                            None, state=rhythm, turnovers_per_min=round(tpm, 2))
            elif rhythm == "control":
                lead = h if poss_share_h >= 0.5 else a
                self._alert("rhythm", "low",
                            f"Control phase: {self.short[lead]} dictating with "
                            f"{self.rhythm_info['possession_share'][lead]:.0f}% of the ball", lead,
                            state=rhythm, turnovers_per_min=round(tpm, 2))

    # ---------- output ----------
    def snapshot(self):
        total_poss = sum(s["possession_s"] for s in self.teams.values())
        teams = {}
        for tid, s in self.teams.items():
            teams[tid] = {
                "name": self.short[tid], "goals": s["goals"],
                "possession_pct": pct(s["possession_s"], total_poss),
                "shots": s["shots"], "shots_on_target": s["shots_on_target"], "xg": round(s["xg"], 2),
                "big_chances": s["big_chances"], "passes": s["passes"],
                "pass_accuracy_pct": pct(s["passes_complete"], s["passes"]),
                "avg_pass_distance_m": round(s["pass_distance_m"] / s["passes"], 1) if s["passes"] else 0.0,
                "avg_pass_difficulty": round(s["pass_difficulty"] / s["pass_difficulty_n"], 2) if s["pass_difficulty_n"] else None,
                "hard_passes_completed": f"{s['hard_passes_complete']}/{s['hard_passes']}",
                "final_third_passes": s["final_third_passes"], "tackles_won": f"{s['tackles_won']}/{s['tackles']}",
                "interceptions": s["interceptions"], "pressures": s["pressures"], "fouls": s["fouls"],
                "yellow_cards": s["yellow_cards"], "red_cards": s["red_cards"], "corners": s["corners"],
                "sprints": s["sprints"], "turnovers": s["turnovers"],
                "fastest_shot_kmh": round(s["fastest_shot_kmh"], 1) or None,
                "avg_ball_speed_kmh": round(s["ball_speed_sum"] / s["ball_speed_n"], 1) if s["ball_speed_n"] else None,
            }
        players = {}
        for pid, p in self.players.items():
            players[pid] = {"name": self.name(pid), "team": self.player_team.get(pid), **p,
                            "xg": round(p["xg"], 2), "pass_distance_m": round(p["pass_distance_m"], 1),
                            "pass_accuracy_pct": pct(p["passes_complete"], p["passes"]),
                            "avg_pass_difficulty": round(p["pass_difficulty"] / p["pass_difficulty_n"], 2) if p["pass_difficulty_n"] else None}
            del players[pid]["pass_difficulty"], players[pid]["pass_difficulty_n"]
        return {"schema_version": SCHEMA_VERSION, "period": self.period, "clock": self.clock,
                "elapsed_s": self.now, "score": dict(self.score),
                "teams": teams,
                "momentum": {"window_s": self.th["window_s"], "threat_share_pct": self.momentum_share,
                             "on_top": self.dominant},
                "rhythm": {"state": self.rhythm, **self.rhythm_info},
                "players": players, "alerts_total": len(self.alerts), "rejected_events": dict(self.rejected)}

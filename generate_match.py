"""
Generate one synthetic, football-realistic match: metadata, rosters and a timed event stream.
Output: data/synthetic/match_001.json and data/synthetic/match_001_events.csv

Coordinates are absolute metres on a 105 x 68 pitch:
home attacks towards x=105 in period 1 and towards x=0 in period 2.
Event mix is loosely calibrated on the Metrica sample (see fetchdata.py).
"""

import csv
import json
import math
import os
import random

from metrics import pass_difficulty, xg_model

SEED = 7
MATCH_ID = "match_001"
OUT_DIR = os.path.join("data", "synthetic")
L, W = 105.0, 68.0
HALF = 2700  # seconds of regular time per period

TEAMS = {
    "home": {"team_id": "RIV", "name": "Riverside Athletic", "short_name": "Riverside",
             "formation": "4-3-3", "colors": {"primary": "#0B3D91", "secondary": "#FFFFFF"},
             "coach": "Marta Lindqvist", "nations": ["ENG", "ESP", "FRA", "NGA", "NED", "JPN"]},
    "away": {"team_id": "NGR", "name": "Northgate Rovers", "short_name": "Northgate",
             "formation": "4-2-3-1", "colors": {"primary": "#C8102E", "secondary": "#1C1C1C"},
             "coach": "Daniel Okafor", "nations": ["ENG", "BRA", "GER", "ARG", "IND", "FRA"]},
}

# Base positions in a team-relative frame (own goal at x=0, attacking towards x=105).
FORMATIONS = {
    "4-3-3": [("GK", 5, 34), ("RB", 32, 8), ("CB", 24, 26), ("CB", 24, 42), ("LB", 32, 60),
              ("DM", 42, 34), ("CM", 54, 22), ("CM", 54, 46), ("RW", 74, 10), ("ST", 80, 34), ("LW", 74, 58)],
    "4-2-3-1": [("GK", 5, 34), ("RB", 32, 8), ("CB", 24, 26), ("CB", 24, 42), ("LB", 32, 60),
                ("DM", 44, 26), ("DM", 44, 42), ("RW", 68, 12), ("AM", 66, 34), ("LW", 68, 56), ("ST", 80, 34)],
}
SUB_ROLES = ["GK", "CB", "CM", "LW", "ST"]
ROLE_GROUP = {"GK": "GK", "RB": "DEF", "CB": "DEF", "LB": "DEF", "DM": "MID", "CM": "MID", "AM": "MID",
              "RW": "WIDE", "LW": "WIDE", "ST": "FWD"}
KM_PER_90 = {"GK": 5.5, "DEF": 10.2, "MID": 11.6, "WIDE": 10.8, "FWD": 10.0}
ATTR_RANGES = {  # pass_skill, shot_skill, tackle_skill, top_speed_kmh
    "GK": ((0.62, 0.75), (0.05, 0.10), (0.30, 0.40), (26.0, 29.0)),
    "DEF": ((0.72, 0.86), (0.30, 0.45), (0.70, 0.90), (30.0, 33.5)),
    "MID": ((0.80, 0.93), (0.45, 0.65), (0.60, 0.80), (29.5, 33.0)),
    "WIDE": ((0.74, 0.86), (0.55, 0.75), (0.35, 0.50), (32.5, 36.0)),
    "FWD": ((0.68, 0.80), (0.70, 0.90), (0.30, 0.45), (31.5, 35.0)),
}
NAMES = {
    "ENG": (["Jack", "Harry", "Callum", "Owen", "Reece", "Liam"], ["Hartley", "Pembroke", "Ashworth", "Calloway", "Fenwick", "Thornbury"]),
    "ESP": (["Iker", "Pablo", "Álvaro", "Hugo", "Mateo"], ["Ferrandis", "Olmedo", "Arribas", "Castañar", "Velilla"]),
    "FRA": (["Théo", "Lucas", "Maxime", "Yanis", "Bastien"], ["Delcourt", "Marchand", "Lefèvre", "Rousselin", "Duverger"]),
    "BRA": (["Thiago", "Rafael", "Caio", "Davi", "Igor"], ["Albuquerque", "Teixeira", "Moraes", "Bastos", "Quintela"]),
    "NGA": (["Chidi", "Tunde", "Emeka", "Seun"], ["Adeyemo", "Okonkwo", "Nwankwo", "Balogun"]),
    "GER": (["Jonas", "Felix", "Lukas", "Niklas"], ["Brandt", "Hollmann", "Kessler", "Ziegler"]),
    "NED": (["Daan", "Sem", "Milan", "Bram"], ["Verhoek", "de Ridder", "Brouwer", "van Dalen"]),
    "IND": (["Arjun", "Rohan", "Kabir", "Vikram"], ["Menon", "Nair", "Rao", "Iyer"]),
    "JPN": (["Haruto", "Ren", "Sota", "Yuki"], ["Takeda", "Morimoto", "Kanda", "Fujii"]),
    "ARG": (["Bruno", "Facundo", "Tomás", "Nicolás"], ["Gallardo", "Ibarra", "Ferreyra", "Sosa"]),
}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def other(side):
    return "away" if side == "home" else "home"


class Sim:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.seed = seed
        self.players, self.slots, self.bench, self.phys = {}, {}, {}, {}
        for side in ("home", "away"):
            self._build_team(side)
        self.events = []
        self.score = {"home": 0, "away": 0}
        self.yellow = set()
        self.mom = 0.0  # >0 favours home, <0 favours away
        self.spells = self._make_spells()
        self.stoppage = {1: self.rng.randint(1, 3), 2: self.rng.randint(3, 6)}
        self.period_len = {p: HALF + m * 60 + self.rng.randint(0, 50) for p, m in self.stoppage.items()}
        self.sub_times = {s: sorted(self.rng.uniform(780, 2400) for _ in range(3)) for s in ("home", "away")}

    # ---------- setup ----------
    def _build_team(self, side):
        cfg, rng = TEAMS[side], self.rng
        pool = [(n, f, l) for n in cfg["nations"] for f in NAMES[n][0] for l in NAMES[n][1]]
        rng.shuffle(pool)
        used, names = set(), []
        for nat, first, last in pool:
            if last not in used:
                used.add(last)
                names.append((nat, first, last))
        numbers = iter(rng.sample([n for n in range(2, 40) if n != 13], 20))
        formation = FORMATIONS[cfg["formation"]]
        roles = [r for r, _, _ in formation] + SUB_ROLES
        self.slots[side], self.bench[side] = [], []
        for i, role in enumerate(roles):
            starter = i < 11
            number = (1 if starter else 13) if role == "GK" else next(numbers)
            nat, first, last = names[i]
            pid = f"{cfg['team_id']}{number:02d}"
            pr, sr, tr, vr = ATTR_RANGES[ROLE_GROUP[role]]
            attrs = {"pass_skill": round(rng.uniform(*pr), 2), "shot_skill": round(rng.uniform(*sr), 2),
                     "tackle_skill": round(rng.uniform(*tr), 2), "top_speed_kmh": round(rng.uniform(*vr), 1),
                     "work_rate": round(rng.uniform(0.92, 1.08), 2)}
            self.players[pid] = {"player_id": pid, "name": f"{first} {last}", "short_name": last, "number": number,
                                 "position": role, "nationality": nat, "team_id": cfg["team_id"],
                                 "starter": starter, "attributes": attrs}
            self.phys[pid] = {"on": 0.0 if starter else None, "secs": 0.0, "sprints": 0, "sprint_m": 0.0,
                              "top": round(attrs["top_speed_kmh"] * rng.uniform(0.7, 0.8), 1)}
            if starter:
                _, bx, by = formation[i]
                self.slots[side].append({"role": role, "bx": bx, "by": by, "pid": pid})
            else:
                self.bench[side].append(pid)

    def _make_spells(self):
        # Hidden momentum spells: windows where one side dominates (ground truth for pattern detection).
        starts = sorted(self.rng.sample(range(480, 5100, 60), 4))
        return [(s, s + self.rng.randint(360, 600), self.rng.choice(["home", "away"])) for s in starts]

    # ---------- helpers ----------
    def elapsed(self):
        return self.base + self.t

    def intent(self, side):
        return self.mom if side == "home" else -self.mom

    def to_abs(self, side, x, y):
        if (side == "home") == (self.period == 1):
            return round(x, 1), round(y, 1)
        return round(L - x, 1), round(W - y, 1)

    def clock(self):
        base_min = 45 * (self.period - 1)
        if self.t < HALF:
            s = int(self.t) + base_min * 60
            return f"{s // 60:02d}:{s % 60:02d}", base_min + int(self.t // 60) + 1, 0
        extra = int(self.t - HALF)
        return f"{base_min + 45}+{extra // 60}:{extra % 60:02d}", base_min + 45, extra // 60 + 1

    def pos(self, side, slot, bx, by):
        gk = slot["role"] == "GK"
        x = slot["bx"] + (bx - L / 2) * (0.12 if gk else 0.5)
        y = slot["by"] + (by - W / 2) * (0.1 if gk else 0.3)
        return clamp(x, 1, L - 1), clamp(y, 1, W - 1)

    def nearest(self, side, x, y, mirror=False, exclude_gk=False):
        """Nearest slot of `side` to (x, y). With mirror=True, (x, y) and the ball are in the other side's frame."""
        bx, by = self.ball
        if mirror:
            bx, by, x, y = L - bx, W - by, L - x, W - y
        slots = [s for s in self.slots[side] if not (exclude_gk and s["role"] == "GK")]
        return min(slots, key=lambda s: math.hypot(self.pos(side, s, bx, by)[0] - x, self.pos(side, s, bx, by)[1] - y))

    def slot_of(self, side, role_groups):
        return self.rng.choice([s for s in self.slots[side] if ROLE_GROUP[s["role"]] in role_groups])

    def score_dict(self):
        return {TEAMS[s]["team_id"]: v for s, v in self.score.items()}

    def emit(self, etype, side=None, pid=None, xy=None, end=None, outcome=None, **details):
        clock, minute, added = self.clock()
        x, y = self.to_abs(side, *xy) if xy else (None, None)
        ex, ey = self.to_abs(side, *end) if end else (None, None)
        self.events.append({
            "event_id": f"{MATCH_ID}-e{len(self.events) + 1:04d}", "type": etype, "period": self.period,
            "clock": clock, "minute": minute, "added_minute": added, "elapsed_s": round(self.elapsed(), 1),
            "team": TEAMS[side]["team_id"] if side else None,
            "player_id": pid, "player_name": self.players[pid]["name"] if pid else None,
            "x": x, "y": y, "end_x": ex, "end_y": ey, "outcome": outcome, "details": details,
        })

    # ---------- match flow ----------
    def run(self):
        self.base, self.next_minute, self.next_phys = 0.0, 60, 300
        for period in (1, 2):
            self.period, self.t, self.forced = period, 0.0, None
            self.side = "home" if period == 1 else "away"
            self.emit("period_start")
            self.kickoff(self.side)
            while self.t < self.period_len[period]:
                self.tick()
                self.step()
            self.period_len[period] = round(self.t, 1)  # actual length: the last action can run past the plan
            self.emit("period_end", score=self.score_dict())
            if period == 1:
                self.base += self.t
        self.physical_update()
        self.emit("match_end", score=self.score_dict())

    def tick(self):
        while self.elapsed() >= self.next_minute:
            bias = sum(0.6 if sd == "home" else -0.6 for a, b, sd in self.spells if a <= self.next_minute < b)
            self.mom = clamp(0.75 * self.mom + 0.25 * (bias + 0.05) + self.rng.gauss(0, 0.15), -1, 1)
            self.next_minute += 60
        while self.elapsed() >= self.next_phys:
            self.physical_update()
            self.next_phys += 300

    def kickoff(self, side):
        st = next(s for s in self.slots[side] if s["role"] == "ST")
        self.side, self.carrier, self.ball = side, st["pid"], (L / 2, W / 2)
        self.spell_start, self.spell_passes, self.last_passer = self.elapsed(), 0, None
        self.emit("kick_off", side, st["pid"], self.ball)
        self.forced = "kickoff"

    def step(self):
        side, opp, rng = self.side, other(self.side), self.rng
        p = self.players[self.carrier]
        bx, by = self.ball
        forced, self.forced = self.forced, None
        if forced == "shot":
            return self.shoot("free_kick")
        if forced == "header":
            return self.shoot("header")
        if forced:
            return self.pass_ball(mode=forced)

        pressured = False
        if rng.random() < 0.2 + 0.15 * self.intent(opp) + (0.1 if bx > 70 else 0):
            s = self.nearest(opp, bx, by, mirror=True, exclude_gk=True)
            gap = round(rng.uniform(0.5, 5.0), 1)
            self.emit("pressure", opp, s["pid"], (L - bx, W - by), target_player_id=self.carrier,
                      distance_m=gap, intensity="high" if gap < 1.5 else "medium" if gap < 3 else "low")
            self.t += 0.4
            pressured = True

        if rng.random() < 0.06 + (0.08 if pressured else 0):
            return self.tackle()

        d = math.hypot(L - bx, W / 2 - by)
        if bx > 75 and d < 32 and p["position"] != "GK":
            ps = (0.02 + 0.4 * math.exp(-d / 9)) * (1 + 0.4 * self.intent(side))
            if ROLE_GROUP[p["position"]] == "DEF":
                ps *= 0.5
            if rng.random() < ps:
                return self.shoot("open_play")
        self.pass_ball(pressured=pressured)

    def tackle(self):
        side, opp, rng = self.side, other(self.side), self.rng
        bx, by = self.ball
        tk = self.nearest(opp, bx, by, mirror=True, exclude_gk=True)["pid"]
        loc = (L - bx, W - by)
        r = rng.random()
        if r < 0.26:
            self.emit("foul", opp, tk, loc, fouled_player_id=self.carrier)
            if tk not in self.yellow and rng.random() < 0.22:
                self.yellow.add(tk)
                self.emit("card", opp, tk, loc, card="yellow", reason="reckless challenge")
            self.restart("free_kick", side, bx, by)
        elif r < 0.26 + 0.45 * self.players[tk]["attributes"]["tackle_skill"]:
            self.emit("tackle", opp, tk, loc, outcome="won", target_player_id=self.carrier)
            self.t += rng.uniform(0.8, 1.8)
            self.turnover(opp, tk, *loc, "tackle")
        else:
            self.emit("tackle", opp, tk, loc, outcome="lost", target_player_id=self.carrier)
            self.ball = (clamp(bx + rng.uniform(2, 7), 1, L - 1), by)
            self.t += rng.uniform(1.0, 2.5)

    def pass_ball(self, mode=None, pressured=False):
        side, rng = self.side, self.rng
        pid = self.carrier
        bx, by = self.ball
        m = self.intent(side)
        cross = mode == "cross" or (mode is None and bx > 78 and abs(by - W / 2) > 18 and rng.random() < 0.4)
        if cross:
            tx, ty = rng.uniform(93, 101), W / 2 + rng.gauss(0, 6)
        elif mode == "kickoff":
            tx, ty = bx - 12, by + rng.gauss(0, 8)
        elif mode == "long":
            tx, ty = bx + rng.uniform(35, 55), by + rng.gauss(0, 15)
        elif mode == "short":
            tx, ty = bx + rng.gauss(2, 6), by + rng.gauss(0, 8)
        else:
            fwd = rng.gauss(-1, 8) if bx > 85 else rng.gauss(5 + 7 * m, 11)
            tx, ty = bx + fwd, by + rng.gauss(0, 14)
        tx, ty = clamp(tx, 1, L - 1), clamp(ty, 1, W - 1)

        cands = [(s, *self.pos(side, s, bx, by)) for s in self.slots[side] if s["pid"] != pid]
        weights = [math.exp(-math.hypot(x - tx, y - ty) / 7) * (0.15 if s["role"] == "GK" else 1) for s, x, y in cands]
        rs, rx, ry = rng.choices(cands, weights)[0]
        if cross:
            ex, ey = tx, ty
        else:
            ex, ey = clamp(rx + rng.gauss(0, 2.5), 1, L - 1), clamp(ry + rng.gauss(0, 2.5), 1, W - 1)

        dist, dx = math.hypot(ex - bx, ey - by), ex - bx
        if cross:
            sub = "cross"
        elif dx > 15 and ex > 70:
            sub = "through_ball"
        elif abs(ey - by) > 30 and dist > 30:
            sub = "switch"
        elif dx < -5:
            sub = "back"
        else:
            sub = "long" if dist >= 30 else "medium" if dist >= 15 else "short"
        high = sub in ("cross", "switch", "long") and rng.random() < 0.85
        noise = rng.uniform(-0.04, 0.04)  # unseen factors: affects the outcome, not the reported metric
        difficulty = pass_difficulty(dist, dx, pressured, ex, cross)
        skill = self.players[pid]["attributes"]["pass_skill"]
        p_success = clamp(1.25 - 1.58 * pass_difficulty(dist, dx, pressured, ex, cross, noise)
                          + (skill - 0.8) * 0.8 + 0.05 * m, 0.1, 0.98)
        speed = round(clamp((45 + dist * 0.85 if high else 22 + dist * 1.25) + rng.gauss(0, 5), 15, 115), 1)
        complete = rng.random() < p_success

        self.emit("pass", side, pid, (bx, by), (ex, ey), "complete" if complete else "incomplete",
                  subtype=sub, height="high" if high else "ground", distance_m=round(dist, 1),
                  difficulty=difficulty, ball_speed_kmh=speed, pressured=pressured, recipient_id=rs["pid"])
        self.t += dist / (speed / 3.6) * (1.25 if high else 1.0)

        if complete:
            self.spell_passes += 1
            self.last_passer, self.carrier = pid, rs["pid"]
            carry = 0 if cross else (rng.uniform(0, 2) if ex > 85 else rng.uniform(0, 5) * (1 + m))
            self.ball = (clamp(ex + carry, 1, L - 1), ey)
            self.t += rng.uniform(1.2, 3.8)
            if sub == "through_ball" and rng.random() < 0.7:
                self.sprint(side, rs["pid"], (ex, ey))
            if cross and rng.random() < 0.7:
                self.forced = "header"
            return

        opp = other(side)
        if cross:
            if rng.random() < 0.3:
                self.emit("ball_out", side, pid, (L - 0.5, ey))
                self.restart("corner", side, L - 0.5, by)
            else:
                self.ball = (ex, ey)
                s = self.nearest(opp, ex, ey, mirror=True)
                self.emit("clearance", opp, s["pid"], (L - ex, W - ey))
                self.turnover(opp, s["pid"], L - ex, W - ey, "clearance")
        elif ex > L - 2:
            self.emit("ball_out", side, pid, (ex, ey))
            self.restart("goal_kick", opp, 5.5, W / 2)
        elif ey < 2 or ey > W - 2 or rng.random() < 0.18:
            ey = 0.5 if ey < W / 2 else W - 0.5
            self.emit("ball_out", side, pid, (ex, ey))
            self.restart("throw_in", opp, L - ex, W - ey)
        else:
            self.ball = (ex, ey)
            s = self.nearest(opp, ex, ey, mirror=True, exclude_gk=ex < 88)
            self.emit("interception", opp, s["pid"], (L - ex, W - ey))
            self.t += 0.5
            self.turnover(opp, s["pid"], L - ex, W - ey, "interception")

    def shoot(self, subtype):
        side, opp, rng = self.side, other(self.side), self.rng
        pid = self.carrier
        bx, by = self.ball
        header = subtype == "header"
        d = math.hypot(L - bx, W / 2 - by)
        xg = xg_model(bx, by, header)
        skill = self.players[pid]["attributes"]["shot_skill"]
        speed = round(rng.uniform(40, 70) if header else clamp(70 + min(d, 30) * 0.9 + rng.gauss(0, 8), 55, 125), 1)
        if rng.random() < xg * (0.75 + 0.5 * skill):
            outcome = "goal"
        else:
            outcome = rng.choices(["saved", "off_target", "blocked", "post"],
                                  [0.33 + 0.2 * skill, 0.38, 0.05 if header or d < 12 else 0.22, 0.04])[0]
        on_target = outcome in ("goal", "saved")
        end = (L, W / 2 + rng.gauss(0, 2.2 if on_target else 6))
        self.emit("shot", side, pid, (bx, by), end, outcome, subtype=subtype, xg=xg, big_chance=xg >= 0.3,
                  shot_speed_kmh=speed, distance_m=round(d, 1),
                  body_part="head" if header else rng.choice(["right_foot", "right_foot", "left_foot"]))
        self.t += 1.0
        gk = next(s["pid"] for s in self.slots[opp] if s["role"] == "GK")

        if outcome == "goal":
            self.score[side] += 1
            self.emit("goal", side, pid, (bx, by), assist_id=self.last_passer, score=self.score_dict(),
                      xg=xg, shot_speed_kmh=speed)
            self.t += rng.uniform(55, 80)
            self.kickoff(opp)
        elif outcome == "saved":
            self.emit("save", opp, gk, (L - end[0] + 1, W - end[1]), shot_speed_kmh=speed, xg_faced=xg)
            if rng.random() < 0.3:
                self.restart("corner", side, L - 0.5, by)
            else:
                self.turnover(opp, gk, 6.0, W / 2, "save")
        elif outcome == "blocked":
            if rng.random() < 0.35:
                self.restart("corner", side, L - 0.5, by)
            else:
                s = self.nearest(opp, bx, by, mirror=True, exclude_gk=True)
                self.turnover(opp, s["pid"], L - bx, W - by, "block")
        else:
            self.emit("ball_out", side, pid, end)
            self.restart("goal_kick", opp, 5.5, W / 2)

    def restart(self, kind, side, x, y):
        """Dead-ball restart for `side` at (x, y) in that side's frame."""
        rng = self.rng
        self.do_subs()
        if kind == "throw_in":
            x, y = clamp(x, 2, L - 2), 0.5 if y < W / 2 else W - 0.5
            self.t += rng.uniform(8, 18)
            self.forced = "short"
        elif kind == "goal_kick":
            self.t += rng.uniform(18, 32)
            self.forced = "long" if rng.random() < 0.45 else "short"
        elif kind == "corner":
            y = 0.5 if y < W / 2 else W - 0.5
            self.t += rng.uniform(22, 38)
            self.forced = "cross"
        else:  # free_kick
            self.t += rng.uniform(14, 30)
            d = math.hypot(L - x, W / 2 - y)
            if x > 75 and d < 30 and abs(y - W / 2) < 20 and rng.random() < 0.35:
                self.forced = "shot"
            elif x > 70 and rng.random() < 0.5:
                self.forced = "cross"

        if kind == "goal_kick":
            taker = next(s["pid"] for s in self.slots[side] if s["role"] == "GK")
        elif kind == "corner":
            taker = self.slot_of(side, ("WIDE", "MID"))["pid"]
        elif self.forced == "shot":
            taker = max((s["pid"] for s in self.slots[side] if s["role"] != "GK"),
                        key=lambda p: self.players[p]["attributes"]["shot_skill"])
        else:
            mirror = side != self.side
            taker = self.nearest(side, x if not mirror else L - x, y if not mirror else W - y,
                                 mirror=mirror, exclude_gk=True)["pid"]

        if side != self.side:
            self.turnover(side, taker, x, y, kind)
        else:
            self.carrier, self.ball = taker, (x, y)
        self.emit("set_piece", side, taker, (x, y), subtype=kind)

    def turnover(self, new_side, pid, x, y, reason):
        old = self.side
        self.emit("possession_change", new_side, pid, (x, y), reason=reason, from_team=TEAMS[old]["team_id"],
                  previous_spell_s=round(self.elapsed() - self.spell_start, 1),
                  previous_spell_passes=self.spell_passes)
        self.side, self.carrier, self.ball = new_side, pid, (x, y)
        self.spell_start, self.spell_passes, self.last_passer = self.elapsed(), 0, None
        if x < 50 and self.intent(new_side) > 0.2 and self.rng.random() < 0.35:
            runner = self.slot_of(new_side, ("WIDE", "FWD"))
            self.sprint(new_side, runner["pid"], self.pos(new_side, runner, x, y))

    def sprint(self, side, pid, xy):
        speed = round(self.players[pid]["attributes"]["top_speed_kmh"] * self.rng.uniform(0.88, 1.0), 1)
        dist = round(self.rng.uniform(15, 45), 1)
        ph = self.phys[pid]
        ph["top"], ph["sprints"], ph["sprint_m"] = max(ph["top"], speed), ph["sprints"] + 1, ph["sprint_m"] + dist
        self.emit("sprint", side, pid, xy, top_speed_kmh=speed, distance_m=dist,
                  duration_s=round(dist / (speed / 3.6 * 0.85), 1))

    def do_subs(self):
        if self.period != 2:
            return
        for side in ("home", "away"):
            while self.sub_times[side] and self.t >= self.sub_times[side][0]:
                self.sub_times[side].pop(0)
                self.substitute(side)

    def substitute(self, side):
        bench = [p for p in self.bench[side] if self.players[p]["position"] != "GK"]
        if not bench:
            return
        inn = self.rng.choice(bench)
        group = ROLE_GROUP[self.players[inn]["position"]]
        slot = self.rng.choice([s for s in self.slots[side] if ROLE_GROUP[s["role"]] == group]
                               or [s for s in self.slots[side] if s["role"] != "GK"])
        out, e = slot["pid"], self.elapsed()
        slot["pid"] = inn
        self.bench[side].remove(inn)
        self.phys[out]["secs"] += e - self.phys[out]["on"]
        self.phys[out]["on"], self.phys[inn]["on"] = None, e
        if self.carrier == out:
            self.carrier = inn
        self.emit("substitution", side, out, player_in_id=inn, player_in_name=self.players[inn]["name"],
                  role=slot["role"])

    def physical_update(self):
        e, rows = self.elapsed(), []
        for pid, ph in self.phys.items():
            secs = ph["secs"] + (e - ph["on"] if ph["on"] is not None else 0)
            if secs <= 0:
                continue
            p = self.players[pid]
            km = KM_PER_90[ROLE_GROUP[p["position"]]] * secs / 5400 * p["attributes"]["work_rate"] + ph["sprint_m"] * 0.3 / 1000
            rows.append({"player_id": pid, "team": p["team_id"], "minutes": round(secs / 60, 1),
                         "distance_km": round(km, 2), "top_speed_kmh": ph["top"], "sprints": ph["sprints"]})
        self.emit("physical_update", players=rows)


def build_output(sim):
    teams = {}
    for side, cfg in TEAMS.items():
        team = {k: v for k, v in cfg.items() if k != "nations"}
        team["side"] = side
        team["players"] = [p for p in sim.players.values() if p["team_id"] == cfg["team_id"]]
        teams[side] = team
    match = {
        "match_id": MATCH_ID, "synthetic": True, "competition": "Synthetic Super League", "season": "2026/27",
        "matchday": 9, "date": "2026-10-18", "kickoff_local": "17:30", "timezone": "Europe/London",
        "venue": {"name": "Riverside Park", "city": "Ashbridge", "capacity": 41250}, "attendance": 38764,
        "referee": "Paolo Venturi", "weather": {"condition": "light rain", "temperature_c": 11, "wind_kmh": 14},
        "pitch": {"length_m": L, "width_m": W,
                  "coordinates": "absolute metres; home attacks towards x=105 in period 1 and towards x=0 in period 2"},
        "periods": [{"period": p, "duration_s": sim.period_len[p], "added_time_min": sim.stoppage[p]} for p in (1, 2)],
        "final_score": sim.score_dict(),
    }
    return {"schema_version": "1.0", "match": match, "teams": teams, "events": sim.events,
            "generator": {"seed": sim.seed, "momentum_spells": [
                {"start_elapsed_s": a, "end_elapsed_s": b, "team": TEAMS[s]["team_id"]} for a, b, s in sim.spells]}}


def write_csv(events, path):
    cols = ["event_id", "type", "period", "clock", "minute", "added_minute", "elapsed_s", "team",
            "player_id", "player_name", "x", "y", "end_x", "end_y", "outcome"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols + ["details"])
        for ev in events:
            w.writerow([ev[c] for c in cols] + [json.dumps(ev["details"], ensure_ascii=False)])


def main():
    # Pick the first seed that gives a watchable result (both teams score, 3-5 goals).
    for seed in range(SEED, SEED + 200):
        sim = Sim(seed)
        sim.run()
        if min(sim.score.values()) >= 1 and 3 <= sum(sim.score.values()) <= 5:
            break

    os.makedirs(OUT_DIR, exist_ok=True)
    json_path = os.path.join(OUT_DIR, f"{MATCH_ID}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(build_output(sim), f, ensure_ascii=False, indent=1)
    write_csv(sim.events, os.path.join(OUT_DIR, f"{MATCH_ID}_events.csv"))

    counts = {}
    for ev in sim.events:
        counts[ev["type"]] = counts.get(ev["type"], 0) + 1
    print(f"seed {sim.seed}  score {sim.score_dict()}  events {len(sim.events)}")
    print(dict(sorted(counts.items(), key=lambda kv: -kv[1])))
    for side in ("home", "away"):
        tid = TEAMS[side]["team_id"]
        passes = [e for e in sim.events if e["type"] == "pass" and e["team"] == tid]
        shots = [e for e in sim.events if e["type"] == "shot" and e["team"] == tid]
        acc = sum(e["outcome"] == "complete" for e in passes) / max(len(passes), 1)
        print(f"{tid}: passes {len(passes)} ({acc:.0%})  shots {len(shots)}  xG {sum(e['details']['xg'] for e in shots):.2f}")
    print(f"saved: {json_path}")


if __name__ == "__main__":
    main()

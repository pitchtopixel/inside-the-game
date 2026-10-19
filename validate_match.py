"""
Validate a generated match file: structure, internal consistency and football rules.
Hard checks must pass; realism ranges are reported as warnings (controlled-test data, not a calibrated model).

    python validate_match.py                    every match in data/synthetic/
    python validate_match.py match_002 --log
"""

import argparse
import glob
import json
import math
import os
import sys
from collections import Counter

from testlog import start_log

sys.stdout.reconfigure(encoding="utf-8")
L, W, HALF = 105.0, 68.0, 2700
REQUIRED = {"event_id": str, "type": str, "period": int, "clock": str, "minute": int, "elapsed_s": (int, float),
            "details": dict}
# Realism ranges per team (warnings only)
RANGES = {"passes": (350, 650), "pass_accuracy_pct": (65, 90), "shots": (3, 30), "fouls": (3, 25)}


def validate(data):
    """Returns (checks, warnings): checks = [(name, ok, detail)]."""
    ev, checks, warnings = data["events"], [], []
    add = lambda name, bad, detail="": checks.append((name, not bad, detail or (f"{len(bad)} problems: {bad[:3]}" if bad else "ok")))
    teams = {t["team_id"]: t for t in data["teams"].values()}
    roster = {p["player_id"]: tid for tid, t in teams.items() for p in t["players"]}

    # 1. structure
    bad = [e.get("event_id", f"#{i}") for i, e in enumerate(ev)
           if not isinstance(e, dict) or not all(isinstance(e.get(k), t) for k, t in REQUIRED.items())]
    dup = [k for k, n in Counter(e.get("event_id") for e in ev if isinstance(e, dict)).items() if n > 1]
    add("1. every event has the required fields; ids unique", bad + dup)
    if bad:  # later checks assume well-formed events
        checks += [(f"{n}. (skipped: structure invalid)", False, "") for n in range(2, 11)]
        return checks, warnings

    # 2. time order and periods
    back = [ev[i]["event_id"] for i in range(1, len(ev)) if ev[i]["elapsed_s"] < ev[i - 1]["elapsed_s"]]
    per = [ev[i]["event_id"] for i in range(1, len(ev)) if ev[i]["period"] < ev[i - 1]["period"]]
    shape = [e["type"] for e in ev if e["type"] in ("period_start", "period_end", "match_end")]
    add("2. time never goes backwards; periods in order", back + per +
        ([] if shape == ["period_start", "period_end", "period_start", "period_end", "match_end"] else [str(shape)]))

    # 3. teams and players
    bad = []
    for e in ev:
        if e.get("team") is not None and e["team"] not in teams:
            bad.append(f"{e['event_id']} team {e['team']}")
        pid = e.get("player_id")
        if pid is not None and (pid not in roster or roster[pid] != e["team"]):
            bad.append(f"{e['event_id']} player {pid}")
        rid = e["details"].get("recipient_id")
        if rid is not None and roster.get(rid) != e["team"]:
            bad.append(f"{e['event_id']} recipient {rid}")
    add("3. teams and players exist; players act for their own team", bad)

    # 4. coordinates on the pitch
    bad = [e["event_id"] for e in ev for k, lim in (("x", L), ("end_x", L), ("y", W), ("end_y", W))
           if e.get(k) is not None and not 0 <= e[k] <= lim]
    add("4. all coordinates inside the 105 x 68 pitch", bad)

    # 5. goals follow scoring shots; score adds up
    bad, running = [], {t: 0 for t in teams}
    for i, e in enumerate(ev):
        if e["type"] == "goal":
            prev = [p for p in ev[max(0, i - 3):i] if p["type"] == "shot"]
            if not prev or prev[-1]["outcome"] != "goal" or prev[-1]["player_id"] != e["player_id"]:
                bad.append(f"{e['event_id']} not after a scoring shot")
            running[e["team"]] += 1
            if e["details"].get("score") != running:
                bad.append(f"{e['event_id']} score {e['details'].get('score')} vs {running}")
    scoring_shots = sum(e["type"] == "shot" and e["outcome"] == "goal" for e in ev)
    goals = sum(e["type"] == "goal" for e in ev)
    if scoring_shots != goals:
        bad.append(f"{scoring_shots} scoring shots vs {goals} goals")
    if data["match"]["final_score"] != running or ev[-1]["details"].get("score") != running:
        bad.append(f"final score {data['match']['final_score']} vs goals {running}")
    add("5. every goal follows a scoring shot; score adds up to the final score", bad)

    # 6. shot outcomes consistent with what follows
    bad = []
    for i, e in enumerate(ev):
        if e["type"] != "shot":
            continue
        nxt = ev[i + 1] if i + 1 < len(ev) else {}
        if e["outcome"] == "goal" and nxt.get("type") != "goal":
            bad.append(f"{e['event_id']} goal shot not followed by goal")
        if e["outcome"] == "saved" and (nxt.get("type") != "save" or nxt.get("team") == e["team"]):
            bad.append(f"{e['event_id']} saved shot not followed by opponent save")
        if e["outcome"] not in ("goal", "saved", "off_target", "blocked", "post"):
            bad.append(f"{e['event_id']} outcome {e['outcome']}")
        if not 0 <= e["details"].get("xg", -1) <= 1:
            bad.append(f"{e['event_id']} xg {e['details'].get('xg')}")
    add("6. shot outcomes consistent (goal -> goal event, saved -> opponent save, xG 0-1)", bad)

    # 7. passes
    bad = []
    for e in ev:
        if e["type"] != "pass":
            continue
        if e["outcome"] not in ("complete", "incomplete") or e.get("end_x") is None:
            bad.append(f"{e['event_id']} outcome/end")
            continue
        dist = math.hypot(e["end_x"] - e["x"], e["end_y"] - e["y"])
        if abs(dist - e["details"]["distance_m"]) > 0.6:
            bad.append(f"{e['event_id']} distance {e['details']['distance_m']} vs {dist:.1f}")
        if not 0.05 <= e["details"]["difficulty"] <= 0.95:
            bad.append(f"{e['event_id']} difficulty {e['details']['difficulty']}")
    add("7. passes have start, end, outcome; distance matches coordinates; difficulty 0.05-0.95", bad)

    # 8. match clock and stoppage time
    bad = []
    for p in data["match"]["periods"]:
        lo, hi = HALF, HALF + p["added_time_min"] * 60 + 120
        if not lo <= p["duration_s"] <= hi:
            bad.append(f"period {p['period']} lasts {p['duration_s']} s")
    for e in ev:
        lo, hi = (1, 45) if e["period"] == 1 else (46, 90)
        if not lo <= e["minute"] <= hi or (e["added_minute"] and e["minute"] != hi):
            bad.append(f"{e['event_id']} minute {e['minute']}+{e['added_minute']} in period {e['period']}")
    add("8. period lengths and minutes follow the clock rules (stoppage shown as 45+ / 90+)", bad)

    # 9. substitutions
    bad, subs = [], Counter()
    on_at, off_at = {}, {}
    for i, e in enumerate(ev):
        if e["type"] == "substitution":
            subs[e["team"]] += 1
            off_at[e["player_id"]], on_at[e["details"]["player_in_id"]] = i, i
    starters = {p["player_id"] for t in teams.values() for p in t["players"] if p["starter"]}
    for i, e in enumerate(ev):
        pid = e.get("player_id")
        if pid is None or e["type"] in ("substitution", "physical_update"):
            continue
        if pid in off_at and i > off_at[pid]:
            bad.append(f"{e['event_id']} {pid} acts after being subbed off")
        if pid not in starters and (pid not in on_at or i < on_at[pid]):
            bad.append(f"{e['event_id']} {pid} acts before coming on")
    bad += [f"{t} made {n} subs" for t, n in subs.items() if n > 5]
    add("9. substitutes only play after coming on; subbed-off players stop; max 5 subs", bad)

    # 10. kick-offs and cards
    bad = []
    for i, e in enumerate(ev):
        if e["type"] == "goal":
            ko = next((k for k in ev[i + 1:] if k["type"] == "kick_off"), None)
            if ko and ko["period"] == e["period"] and ko["team"] == e["team"]:
                bad.append(f"{ko['event_id']} kick-off by the scoring team")
    yellows = Counter(e["player_id"] for e in ev if e["type"] == "card" and e["details"].get("card") == "yellow")
    reds = {e["player_id"] for e in ev if e["type"] == "card" and e["details"].get("card") == "red"}
    bad += [f"{p} two yellows, no red" for p, n in yellows.items() if n >= 2 and p not in reds]
    add("10. conceding team kicks off after a goal; two yellows mean a red", bad)

    # realism ranges (warnings)
    for tid in teams:
        passes = [e for e in ev if e["type"] == "pass" and e["team"] == tid]
        stats = {"passes": len(passes),
                 "pass_accuracy_pct": 100 * sum(e["outcome"] == "complete" for e in passes) / max(len(passes), 1),
                 "shots": sum(e["type"] == "shot" and e["team"] == tid for e in ev),
                 "fouls": sum(e["type"] == "foul" and e["team"] == tid for e in ev)}
        for k, (lo, hi) in RANGES.items():
            if not lo <= stats[k] <= hi:
                warnings.append(f"{tid} {k} {stats[k]:.0f} outside typical {lo}-{hi}")
    return checks, warnings


def self_test(data):
    """Break a copy of the match in 10 ways; each break must be caught by the matching check."""
    def first(typ, **kw):
        return next(i for i, e in enumerate(data["events"]) if e["type"] == typ and all(e.get(k) == v for k, v in kw.items()))

    def other_team(tid):
        return next(t["team_id"] for t in data["teams"].values() if t["team_id"] != tid)

    goal = first("goal")
    sub = first("substitution")
    mutations = {
        1: lambda ev: ev[5].pop("event_id"),
        2: lambda ev: ev[200].update(elapsed_s=1.0),
        3: lambda ev: ev[goal - 1].update(team=other_team(ev[goal - 1]["team"])),
        4: lambda ev: ev[300].update(x=120.0),
        5: lambda ev: ev[goal - 1].update(outcome="saved"),
        6: lambda ev: ev[first("shot", outcome="off_target")]["details"].update(xg=1.7),
        7: lambda ev: ev[first("pass")]["details"].update(distance_m=99.0),
        8: lambda ev: ev[10].update(minute=70),
        9: lambda ev: ev.append({**ev[sub + 5], "event_id": "x-sub", "player_id": ev[sub]["player_id"],
                                 "team": ev[sub]["team"]}) or ev.insert(-2, ev.pop()),
        10: lambda ev: ev.insert(goal + 1, {**ev[first("card")], "event_id": "x-card"}) or
        ev.insert(goal + 1, {**ev[first("card")], "event_id": "x-card2"}),
    }
    caught = 0
    for n, mutate in mutations.items():
        broken = json.loads(json.dumps(data))
        mutate(broken["events"])
        checks, _ = validate(broken)
        hit = not checks[n - 1][1]
        caught += hit
        print(f"  [{'PASS' if hit else 'FAIL'}] self-test: break rule {n} -> check {n} fails")
    return caught == len(mutations)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match_id", nargs="?")
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--self-test", action="store_true", help="prove every check can fail")
    args = ap.parse_args()
    if args.log:
        start_log(f"validate_{args.match_id or 'all'}")
    paths = ([os.path.join("data", "synthetic", f"{args.match_id}.json")] if args.match_id
             else sorted(p for p in glob.glob(os.path.join("data", "synthetic", "match_*.json"))))
    all_ok = True
    for path in paths:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        checks, warnings = validate(data)
        print(f"\n== {os.path.basename(path)}  {data['match']['final_score']}  {len(data['events'])} events ==")
        for name, ok, detail in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")
        for w in warnings:
            print(f"  [WARN] {w}")
        ok = all(c[1] for c in checks)
        if args.self_test:
            ok &= self_test(data)
        all_ok &= ok
        print(f"  {sum(c[1] for c in checks)}/{len(checks)} checks passed, {len(warnings)} warnings -> {'PASS' if ok else 'FAIL'}")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()

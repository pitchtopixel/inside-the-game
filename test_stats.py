"""
Test the stats engine by replaying a match offline.

    python test_stats.py              synthetic match: exact checks vs raw events + generator ground truth
    python test_stats.py --metrica    real Metrica match: sense checks (are the numbers football-realistic?)
    add --alerts to print the full alert timeline
"""

import argparse
import json
import os
import sys
from collections import Counter

from stats_engine import MatchState
from testlog import start_log

sys.stdout.reconfigure(encoding="utf-8")
ICONS = {"goal": "⚽", "big_chance": "🎯", "card": "🟨", "substitution": "🔁", "momentum": "⚡", "rhythm": "🌀",
         "pressure_spell": "🔥", "possession_streak": "🔗", "sprint_record": "🏃", "shot_speed_record": "💥",
         "distance_milestone": "📏"}
ROWS = ["goals", "possession_pct", "shots", "shots_on_target", "xg", "big_chances", "passes", "pass_accuracy_pct",
        "avg_pass_distance_m", "avg_pass_difficulty", "hard_passes_completed", "final_third_passes", "tackles_won",
        "interceptions", "pressures", "fouls", "yellow_cards", "corners", "turnovers", "fastest_shot_kmh",
        "avg_ball_speed_kmh"]
FACTUAL = {"goal", "card", "substitution"}  # always shown on broadcast; not counted as "insight" noise
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def replay(data):
    st = MatchState({"match": data["match"], "teams": data["teams"]})
    ht = None
    for ev in data["events"]:
        st.update(ev)
        if ev["type"] == "period_end" and ev["period"] == 1:
            ht = st.snapshot()
    return st, ht, st.snapshot()


def print_table(title, snap):
    h, a = list(snap["teams"])
    th, ta = snap["teams"][h], snap["teams"][a]
    print(f"\n== {title}  ({snap['clock']}) ==")
    print(f"{'':24}{th['name']:>14}{ta['name']:>14}")
    for r in ROWS:
        print(f"{r:24}{str(th[r]):>14}{str(ta[r]):>14}")
    print(f"momentum (last 5'): {snap['momentum']['threat_share_pct']}  on top: {snap['momentum']['on_top']}")
    print(f"rhythm: {snap['rhythm']}")


def print_alerts(st, full):
    by_type = Counter(a["type"] for a in st.alerts)
    print(f"\n== Alerts: {len(st.alerts)} total ==  {dict(by_type)}")
    minutes = st.now / 60
    per10 = Counter(int(a["elapsed_s"] // 600) for a in st.alerts if a["type"] not in FACTUAL)
    print("insight alerts per 10 min:", [per10.get(i, 0) for i in range(int(minutes // 10) + 1)])
    if full:
        for a in st.alerts:
            print(f"  {a['clock']:>8}  {ICONS.get(a['type'], '•')} {a['text']}")


def alert_rate(st):
    return sum(a["type"] not in FACTUAL for a in st.alerts) / (st.now / 600)


def test_synthetic(full):
    with open(os.path.join("data", "synthetic", "match_001.json"), encoding="utf-8") as f:
        data = json.load(f)
    st, ht, ft = replay(data)
    print_table("Half-time", ht)
    print_table("Full-time", ft)
    print_alerts(st, full)

    print("\n== Checks (synthetic) ==")
    ev = data["events"]
    check("1. score matches generator", ft["score"] == data["match"]["final_score"], str(ft["score"]))
    for tid, s in ft["teams"].items():
        raw = Counter(e["type"] for e in ev if e["team"] == tid)
        got = (s["shots"], s["passes"], int(s["tackles_won"].split("/")[1]), s["yellow_cards"] + s["red_cards"])
        want = (raw["shot"], raw["pass"], raw["tackle"], raw["card"])
        check(f"2. {tid} counts (shots, passes, tackles, cards)", got == want, f"{got} vs {want}")
    total = sum(s["possession_pct"] for s in ft["teams"].values())
    check("3. possession sums to 100", abs(total - 100) < 0.3, f"{total}")
    for tid, s in ft["teams"].items():
        passes = [e for e in ev if e["type"] == "pass" and e["team"] == tid]
        acc = round(100 * sum(e["outcome"] == "complete" for e in passes) / len(passes), 1)
        xg = round(sum(e["details"]["xg"] for e in ev if e["type"] == "shot" and e["team"] == tid), 2)
        check(f"4. {tid} pass accuracy & xG", (s["pass_accuracy_pct"], s["xg"]) == (acc, xg),
              f"{s['pass_accuracy_pct']}% / {s['xg']} vs {acc}% / {xg}")

    spells = data["generator"]["momentum_spells"]
    hits = 0
    for sp in spells:
        lo, hi = sp["start_elapsed_s"], sp["end_elapsed_s"] + 180
        mins = [dom for t, _, dom in st.momentum_timeline if lo <= t <= hi]
        frac = sum(dom == sp["team"] for dom in mins) / max(len(mins), 1)
        hit = frac >= 0.3
        hits += hit
        print(f"     spell {sp['team']} {sp['start_elapsed_s'] // 60}'-{sp['end_elapsed_s'] // 60}': "
              f"on top {frac:.0%} of the time -> {'detected' if hit else 'missed'}")
    check("5. momentum spells detected (>=3 of 4)", hits >= 3, f"{hits}/{len(spells)}")
    goals_alerted = sum(a["type"] == "goal" for a in st.alerts)
    rate = alert_rate(st)
    check("6. insight alert rate 1-3 per 10 min, all goals alerted",
          1 <= rate <= 3 and goals_alerted == sum(ft["score"].values()), f"{rate:.1f}/10min, goals {goals_alerted}")
    check("7. clean match has no rejected events", not ft["rejected_events"], str(ft["rejected_events"]))
    check("8. snapshot carries schema version", ft.get("schema_version") == "1.0", str(ft.get("schema_version")))
    test_bad_input(data)


def test_bad_input(data):
    """Duplicates, malformed, out-of-order and unknown-team events: skipped and counted, never fatal."""
    from stream_server import validate
    st = MatchState({"match": data["match"], "teams": data["teams"]})
    good = data["events"][:50]
    bad = [
        good[10],                                            # duplicate
        {"type": "pass"},                                    # malformed
        "not an event",                                      # not an object
        {**good[49], "event_id": "x-1", "elapsed_s": 1.0},   # out of order
        {**good[49], "event_id": "x-2", "team": "ZZZ"},      # unknown team
        {**good[49], "event_id": "x-3", "type": "pass", "x": None,
         "details": {"distance_m": "far"}},                  # valid shape, bad value: breaks a handler
    ]
    try:
        for ev in good + bad:
            st.update(ev)
        crashed = False
    except Exception as e:
        crashed = repr(e)
    rej = dict(st.rejected)
    check("9. engine survives bad input and counts it",
          not crashed and sum(rej.values()) == len(bad), f"crash={crashed} rejected={rej}")
    clean, n_rej = validate(good + [good[10], {"type": "pass"}, "junk"])
    check("10. streamer drops duplicates & malformed", len(clean) == 50 and n_rej == 3, f"kept {len(clean)}, rejected {n_rej}")


def test_metrica(full):
    from metrica_adapter import convert
    data = convert()
    st, ht, ft = replay(data)
    print_table("Half-time", ht)
    print_table("Full-time", ft)
    print_alerts(st, full)

    print("\n== Checks (Metrica, real football) ==")
    for tid, s in ft["teams"].items():
        check(f"7. {tid} possession 35-65% & pass accuracy 65-90%",
              35 <= s["possession_pct"] <= 65 and 65 <= s["pass_accuracy_pct"] <= 90,
              f"{s['possession_pct']}% / {s['pass_accuracy_pct']}%")
        check(f"8. {tid} avg pass distance 12-25 m", 12 <= s["avg_pass_distance_m"] <= 25, f"{s['avg_pass_distance_m']} m")
        check(f"9. {tid} avg ball speed 20-110 km/h", 20 <= (s["avg_ball_speed_kmh"] or 0) <= 110,
              f"{s['avg_ball_speed_kmh']} km/h")
        check(f"11. {tid} avg pass difficulty computed (0.15-0.5)",
              0.15 <= (s["avg_pass_difficulty"] or 0) <= 0.5, f"{s['avg_pass_difficulty']}")
    swings = sum(a["type"] == "momentum" for a in st.alerts)
    check("10. momentum swings 1-10", 1 <= swings <= 10, f"{swings}")
    check("12. score matches source", ft["score"] == data["match"]["final_score"], str(ft["score"]))
    check("13. no rejected events", not ft["rejected_events"], str(ft["rejected_events"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrica", action="store_true")
    ap.add_argument("--alerts", action="store_true", help="print full alert timeline")
    ap.add_argument("--log", action="store_true", help="also save output to logs/")
    args = ap.parse_args()
    if args.log:
        start_log("test_stats_metrica" if args.metrica else "test_stats_synthetic")
    (test_metrica if args.metrica else test_synthetic)(args.alerts)
    print(f"\n{sum(results)}/{len(results)} checks passed -> {'PASS' if all(results) else 'FAIL'}")
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

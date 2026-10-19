# Metric Definitions

Every number on screen comes from a formula below, so every insight can be explained.
Code: [`metrics.py`](../metrics.py) (formulas), [`stats_engine.py`](../stats_engine.py) (aggregation, patterns, alerts).

## Pitch & direction
- 105 × 68 m, absolute coordinates. Home attacks towards x = 105 in period 1 and towards x = 0 in period 2.
- All formulas use the **attacking team's frame** (attacking towards x = 105), so half-time end swaps are handled.

## Expected goals (xG)
Probability that a shot becomes a goal, from shot location only:

```
xG = 1 / (1 + e^-(-1.2 + 1.4·angle - 0.11·distance))      × 0.65 for headers
```
- `distance` = metres from shot location to goal centre (105, 34)
- `angle` = angle (radians) the 7.32 m goal mouth subtends from the shot location
- Examples: penalty spot (11 m, central) ≈ 0.18 · 6 m central ≈ 0.42 · 20 m central ≈ 0.05
- **Big chance** = xG ≥ 0.30

## Pass difficulty (0.05 – 0.95)
```
difficulty = 0.10
           + 0.35 × min(distance, 60) / 60     longer passes are harder
           + 0.20 × max(forward, 0) / 45       forward passes break lines
           + 0.18 if the passer is under pressure
           + 0.12 if the pass ends in the box zone (x > 88)
           + 0.10 if it is a cross
```
- `forward` = metres gained towards the opponent's goal
- **Hard pass** = difficulty ≥ 0.60
- Difficulty is a **rating, not a failure probability**. In the generator, the chance a pass is completed is
  `clamp(1.25 − 1.58 × difficulty' + 0.8 × (pass_skill − 0.8) + 0.05 × momentum, 0.10, 0.98)`, where
  `difficulty'` adds ±0.04 of unseen noise. For an average passer (skill 0.8, even momentum):

  | Difficulty | 0.20 | 0.40 | 0.60 | 0.81 |
  |---|---|---|---|---|
  | Completion chance | ~93% | ~62% | ~30% | ~10% |
- Example: a 40 m forward through ball under pressure into the box = 0.10 + 0.23 + 0.18 + 0.18 + 0.12 = **0.81**

## Pass quality
- **Pass accuracy** = completed passes / all passes
- **Average pass distance** = mean start-to-end distance (m)
- **Final-third pass** = completed pass ending at x ≥ 70 (attacking frame)

## Speed
- **Ball speed (km/h)** = pass distance / flight time × 3.6
- **Shot speed (km/h)** — from the event feed (synthetic) or n/a (Metrica)
- **Player top speed, sprints, distance covered** — from the **tracking-provider feed** (`sprint` and `physical_update` events, every 5 minutes). The generator simulates this feed as an optical-tracking provider would deliver it; it is not derived from per-frame player positions.

## Possession
- Time-based: the clock runs for whichever team last made an on-ball action (pass, shot, set piece, kick-off, interception, clearance, save, won tackle).
- **Turnover** = possession switches team.

## Rolling patterns (5-minute window of match time, evaluated every 60 s)
The window resets at each half; no pattern alerts in the first 3 minutes of a half.

| Pattern | Definition |
|---|---|
| **Threat** | +1 per final-third pass, +2 + 8×xG per shot, +0.3 per pressure in the opponent's half |
| **Momentum / on top** | A team has ≥ 65% of the window's threat (minimum 14 threat in total), confirmed on 2 consecutive checks. Evaluated against the generator's hidden momentum: right 78–79% of the time |
| **Pressure spell** | ≥ 15 pressures in the window and ≥ 2× the opponent's |
| **Possession streak** | ≥ 12 consecutive completed passes in one spell |
| **Chaos** | ≥ 4.0 turnovers per minute |
| **Control** | ≤ 2.0 turnovers per minute **and** one team holds ≥ 60% of the ball |

## Alerts & anti-spam
| Alert | Trigger | Priority |
|---|---|---|
| Goal | every goal (brace / hat-trick tagged) | high |
| Card | every card | medium (red: high) |
| Substitution | every substitution | medium |
| Big chance | xG ≥ 0.30, not scored | medium |
| Momentum shift | a **different** team goes on top | medium |
| Sprint record | new match-top sprint ≥ 33 km/h | low |
| Shot speed record | new match-fastest shot ≥ 100 km/h | low |
| Distance milestone | first player to 10 km | low |
| Possession streak / Pressure spell / Rhythm | see patterns | low |

- **Cooldowns:** momentum 8 min, rhythm 10 min, pressure spell 10 min, possession streak 5 min (per team)
- **Low-priority gap:** at most one low-priority alert every 4 minutes
- **Hysteresis:** momentum needs 2 consecutive checks; no repeat alert for the team already on top
- All thresholds: `THRESHOLDS` in `stats_engine.py`

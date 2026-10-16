"""
Metric definitions shared by the generator, the stats engine and the docs (docs/metrics.md).
All inputs are in the attacking team's frame: metres on a 105 x 68 pitch, attacking towards x=105.
"""

import math

L, W = 105.0, 68.0
GOAL_WIDTH = 7.32


def goal_angle(x, y):
    """Angle (radians) subtended by the goal mouth from (x, y)."""
    dx, dy = L - x, y - W / 2
    a = math.atan2(GOAL_WIDTH * dx, dx * dx + dy * dy - (GOAL_WIDTH / 2) ** 2)
    return a + math.pi if a < 0 else a


def xg_model(x, y, header=False):
    """xG = logistic(-1.2 + 1.4*angle - 0.11*distance), x0.65 for headers."""
    d = math.hypot(L - x, W / 2 - y)
    xg = 1 / (1 + math.exp(-(-1.2 + 1.4 * goal_angle(x, y) - 0.11 * d)))
    return round(xg * (0.65 if header else 1.0), 3)


def pass_difficulty(distance_m, forward_m, pressured, end_x, cross, noise=0.0):
    """
    0.10 base
    + 0.35 * min(distance, 60) / 60      longer passes are harder
    + 0.20 * max(forward, 0) / 45        forward passes break lines
    + 0.18 if passer under pressure
    + 0.12 if the pass ends in the box zone (x > 88)
    + 0.10 if it is a cross
    clamped to 0.05-0.95.
    """
    d = (0.10 + min(distance_m, 60) / 60 * 0.35 + max(0.0, forward_m) / 45 * 0.20
         + (0.18 if pressured else 0) + (0.12 if end_x > 88 else 0) + (0.10 if cross else 0) + noise)
    return round(max(0.05, min(0.95, d)), 2)

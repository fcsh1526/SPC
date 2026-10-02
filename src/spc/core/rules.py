"""Stability criteria (AIAG-VDA SPC draft, 7.5 and 10.2.2).

Every criterion is switched on explicitly. The manual warns against stacking criteria:
each extra criterion finds special causes faster and also raises the Type I error.
The default rule set therefore checks only the control limits.

Counting rules chosen here, because the manual leaves them open:

* run   : `run_length` consecutive points on the same side of the center line
* trend : `trend_length` consecutive points that all rise, or all fall (strictly)
* A violation is reported at the index of the point that completes the pattern
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Draft values, for use when a criterion is enabled.
DRAFT_RUN_LENGTH = 7
DRAFT_TREND_LENGTH = 7
DRAFT_WE_RUN_LENGTH = 9


@dataclass(frozen=True)
class RuleSet:
    beyond_limits: bool = True
    run_length: int | None = None
    trend_length: int | None = None
    middle_third: bool = False
    two_of_three_beyond_2s: bool = False
    four_of_five_beyond_1s: bool = False
    fifteen_within_1s: bool = False
    middle_third_window: int = 25
    middle_third_low: int = 11
    middle_third_high: int = 23

    @classmethod
    def draft_basic(cls) -> "RuleSet":
        """Limits, runs, trends and middle third with the draft's usual counts."""
        return cls(run_length=DRAFT_RUN_LENGTH, trend_length=DRAFT_TREND_LENGTH, middle_third=True)

    @property
    def needs_sigma(self) -> bool:
        return self.two_of_three_beyond_2s or self.four_of_five_beyond_1s or self.fifteen_within_1s


@dataclass(frozen=True)
class Violation:
    index: int
    rule: str


@dataclass(frozen=True)
class RuleResult:
    violations: tuple[Violation, ...] = field(default_factory=tuple)

    @property
    def indices(self) -> tuple[int, ...]:
        return tuple(sorted({v.index for v in self.violations}))

    @property
    def n_alarm_points(self) -> int:
        return len(self.indices)

    def by_rule(self, rule: str) -> tuple[int, ...]:
        return tuple(v.index for v in self.violations if v.rule == rule)


def evaluate(
    values,
    center: float,
    lcl,
    ucl,
    rules: RuleSet = RuleSet(),
    sigma: float | None = None,
) -> RuleResult:
    """Apply the enabled criteria to a series of plotted values.

    `lcl` and `ucl` may be scalars or per-point arrays. `sigma` is the standard deviation of the
    plotted statistic. It is needed for the Western Electric style criteria.
    """
    v = np.asarray(values, dtype=float)
    n = v.size
    lo = np.broadcast_to(np.asarray(lcl, dtype=float), (n,))
    hi = np.broadcast_to(np.asarray(ucl, dtype=float), (n,))
    if rules.needs_sigma and (sigma is None or sigma <= 0):
        raise ValueError("sigma of the plotted statistic is needed for the sigma-based criteria")

    found: list[Violation] = []

    if rules.beyond_limits:
        for i in np.flatnonzero((v > hi) | (v < lo)):
            found.append(Violation(int(i), "beyond_limits"))

    if rules.run_length:
        side = np.sign(v - center)
        run = 0
        prev = 0.0
        for i in range(n):
            s = side[i]
            run = run + 1 if (s != 0 and s == prev) else (1 if s != 0 else 0)
            prev = s
            if run >= rules.run_length:
                found.append(Violation(i, "run"))

    if rules.trend_length:
        count = 1
        direction = 0
        for i in range(1, n):
            step = np.sign(v[i] - v[i - 1])
            if step != 0 and step == direction:
                count += 1
            elif step != 0:
                count = 2
                direction = step
            else:
                count = 1
                direction = 0
            if count >= rules.trend_length:
                found.append(Violation(i, "trend"))

    if rules.middle_third:
        w = rules.middle_third_window
        for i in range(w - 1, n):
            window = slice(i - w + 1, i + 1)
            mid = (hi[window] + lo[window]) / 2.0
            third = (hi[window] - lo[window]) / 6.0
            inside = int(np.sum(np.abs(v[window] - mid) <= third))
            if inside < rules.middle_third_low or inside > rules.middle_third_high:
                found.append(Violation(i, "middle_third"))

    if rules.two_of_three_beyond_2s:
        z = (v - center) / sigma
        for i in range(2, n):
            w = z[i - 2 : i + 1]
            if np.sum(w > 2) >= 2 or np.sum(w < -2) >= 2:
                found.append(Violation(i, "two_of_three_beyond_2s"))

    if rules.four_of_five_beyond_1s:
        z = (v - center) / sigma
        for i in range(4, n):
            w = z[i - 4 : i + 1]
            if np.sum(w > 1) >= 4 or np.sum(w < -1) >= 4:
                found.append(Violation(i, "four_of_five_beyond_1s"))

    if rules.fifteen_within_1s:
        z = (v - center) / sigma
        for i in range(14, n):
            if np.all(np.abs(z[i - 14 : i + 1]) < 1):
                found.append(Violation(i, "fifteen_within_1s"))

    found.sort(key=lambda x: (x.index, x.rule))
    return RuleResult(tuple(found))

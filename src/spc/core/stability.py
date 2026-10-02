"""Stability decision for an analysis (retrospective) control chart.

Naming of the capability indices depends on this decision (draft Table 7-1):

* Pp/Ppk : stability not investigated, not provable, or not met
* Cp/Cpk : stable, either in statistical control or controlled stable ("in control")

Only models A1 and A2 are in statistical control (draft Table 9-2). Other models may be
"controlled stable": all values stay within the control limits, sufficiently inside the
tolerance, and operators follow procedures to monitor and adjust. The caller states that
with `controlled_stable`. This code cannot know it from the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from scipy.stats import binom

STATISTICAL_CONTROL_MODELS = frozenset({"A1", "A2"})
ALL_MODELS = frozenset({"A1", "A2", "B", "C1", "C2", "C3", "C4", "D"})

# Draft 10.3.2.3: with k >= 300 subgroups the random range of the expected false alarms is small.
LARGE_K = 300


class Stability(str, Enum):
    UNKNOWN = "unknown"  # not investigated, or data cannot show it
    STATISTICAL_CONTROL = "statistical_control"
    IN_CONTROL = "in_control"  # controlled stable
    UNSTABLE = "unstable"  # stability requirements not met


@dataclass(frozen=True)
class ChartStability:
    stable: bool
    n_alarms: int
    expected_false_alarms: float
    threshold: int
    mode: str


def assess_analysis_chart(
    n_alarm_points: int,
    k: int,
    alpha_total: float,
    mode: str = "strict",
    confidence: float = 0.99,
) -> ChartStability:
    """Decide whether the alarms on an analysis chart still allow a stable process.

    mode "strict"        : any alarm means unstable (conservative, default)
    mode "random_range"  : unstable only when the alarms exceed what k subgroups produce by chance
                           at the given confidence. This follows the draft's wording, which does not
                           fix the confidence. The expected number is alpha_total * k.

    `alpha_total` is the false-alarm probability per subgroup for all enabled criteria together.
    """
    if k < 1 or n_alarm_points < 0:
        raise ValueError("k must be >= 1 and the alarm count must be >= 0")
    if not 0.0 < alpha_total < 1.0:
        raise ValueError("alpha_total must be strictly between 0 and 1")
    expected = alpha_total * k
    if mode == "strict":
        threshold = 0
    elif mode == "random_range":
        threshold = int(binom.ppf(confidence, k, alpha_total))
    else:
        raise ValueError(f"unknown mode {mode!r}")
    return ChartStability(n_alarm_points <= threshold, n_alarm_points, expected, threshold, mode)


def classify_stability(
    chart_stable: bool | None,
    model: str | None = None,
    controlled_stable: bool = False,
) -> Stability:
    """Map the chart result and the time-dependent model to a stability class.

    chart_stable None means stability was not investigated.
    """
    if chart_stable is None:
        return Stability.UNKNOWN
    if model is not None and model not in ALL_MODELS:
        raise ValueError(f"unknown time-dependent model {model!r}")
    if not chart_stable:
        return Stability.UNSTABLE
    if model in STATISTICAL_CONTROL_MODELS:
        return Stability.STATISTICAL_CONTROL
    if controlled_stable:
        return Stability.IN_CONTROL
    if model is None:
        return Stability.UNKNOWN  # chart is quiet, but the time-dependent model is not established
    return Stability.UNSTABLE  # models B..D are not in statistical control and no controlled-stable evidence

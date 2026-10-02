"""Target values and their adjustment for small samples (AIAG-VDA SPC draft, 8.4 and 9.5).

Fewer samples give a wider confidence interval. To keep the same confidence, the target rises:

    target_adjusted = target * sqrt( [chi2_a(nu_base) / nu_base] * [nu_sample / chi2_a(nu_sample)] )

with nu = n - 1 and a = 1 - confidence (lower tail). The draft uses one factor for both the
potential index (Pm, Pp) and the location index (Pmk, Ppk). Checked against draft Table 8-1 (24 cells)
and Table 9-3 (16 cells). Differences stay below 0.005, which is table rounding.

Editions
--------
"draft" : no bias term. Verified against the draft tables.
"final" : location indices also get (1 + 1/2n_sample) / (1 + 1/2n_base). This comes from secondary
          articles about the July 2026 release and is NOT verified against a primary source.

Target values are examples. The real values come from the customer agreement, so the table
can be replaced per customer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType

from scipy.stats import chi2

from spc.core.capability.naming import Stage

CLASSES = ("critical", "major", "minor", "others")

# (p target, pk target) per stage and class. Draft Table 8-1 and Table 9-3 (examples).
DEFAULT_TARGETS = MappingProxyType(
    {
        Stage.MACHINE: MappingProxyType(
            {"critical": (2.33, 2.00), "major": (2.00, 1.67), "minor": (1.67, 1.33), "others": (1.00, 1.00)}
        ),
        Stage.PRELIMINARY: MappingProxyType(
            {"critical": (2.00, 1.67), "major": (1.67, 1.33), "minor": (1.33, 1.00), "others": (1.33, 1.00)}
        ),
        Stage.PRODUCTION: MappingProxyType(
            {"critical": (1.67, 1.67), "major": (1.33, 1.33), "minor": (1.00, 1.00), "others": (1.00, 1.00)}
        ),
    }
)

BASE_SAMPLE_SIZE = {Stage.MACHINE: 50, Stage.PRELIMINARY: 125, Stage.PRODUCTION: 125}

DEFAULT_ESTIMATE_CONFIDENCE = 0.95  # confidence interval of the index estimate
DEFAULT_ADJUSTMENT_CONFIDENCE = 0.9999  # confidence of the target adjustment (draft 8.4, 9.5)


class TargetAdjustmentNotAllowed(ValueError):
    """The draft gives no adjusted target for this case (Critical machine studies with n < 50)."""


@dataclass(frozen=True)
class Targets:
    p: float
    pk: float
    n_base: int
    n: int
    adjusted: bool
    confidence: float
    edition: str


def adjusted_target(
    target: float,
    n_base: int,
    n: int,
    confidence: float = DEFAULT_ADJUSTMENT_CONFIDENCE,
    *,
    location_index: bool = False,
    edition: str = "draft",
) -> float:
    """Raise `target` for a sample of n parts instead of n_base. Returns `target` when n >= n_base."""
    if edition not in ("draft", "final"):
        raise ValueError(f"unknown edition {edition!r}")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be strictly between 0 and 1")
    if n_base < 2 or n < 2:
        raise ValueError("sample sizes must be at least 2")
    if n >= n_base:
        return target
    alpha = 1.0 - confidence
    nu_b, nu_s = n_base - 1, n - 1
    factor = math.sqrt((chi2.ppf(alpha, nu_b) / nu_b) * (nu_s / chi2.ppf(alpha, nu_s)))
    if edition == "final" and location_index:
        factor *= (1.0 + 1.0 / (2.0 * n)) / (1.0 + 1.0 / (2.0 * n_base))
    return target * factor


def required_targets(
    stage: Stage,
    characteristic_class: str,
    n: int,
    confidence: float = DEFAULT_ADJUSTMENT_CONFIDENCE,
    *,
    edition: str = "draft",
    table=None,
    n_base: int | None = None,
) -> Targets:
    """Target pair for a stage, characteristic class and sample size.

    `table` replaces the default example values with customer-agreed ones. It maps
    stage -> class -> (p target, pk target).
    """
    stage = Stage(stage)
    cls = characteristic_class.lower()
    values = (table or DEFAULT_TARGETS)[stage]
    if cls not in values:
        raise ValueError(f"unknown characteristic class {characteristic_class!r}")
    base = n_base or BASE_SAMPLE_SIZE[stage]
    p0, pk0 = values[cls]
    if n >= base:
        return Targets(p0, pk0, base, n, False, confidence, edition)
    if stage is Stage.MACHINE and cls == "critical":
        raise TargetAdjustmentNotAllowed(
            "critical characteristics need a full machine study (n >= %d); the draft allows no reduced sample" % base
        )
    return Targets(
        adjusted_target(p0, base, n, confidence, location_index=False, edition=edition),
        adjusted_target(pk0, base, n, confidence, location_index=True, edition=edition),
        base,
        n,
        True,
        confidence,
        edition,
    )

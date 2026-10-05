"""Configuration, limits and the check of one new point of an SPC monitor.

An SPC control chart controls the process directly (draft 10.2.1, control loop 1). Differences from the
analysis chart of a study:

* The limits are FIXED. They come from a reference (a data set, or expected process parameters) and stay
  until somebody sets new ones with a reason (a new limits revision). A new point never changes them.
* The specification limits are never part of the chart (draft 10.3.1). They only serve the ongoing
  report, which is a loop 2/3 matter.
* Every violation of a control limit or of an enabled criterion must be followed by the out-of-control
  action plan (OCAP). Warning limits are optional and only warn.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
from scipy.stats import chi2

from spc.core.charts.attribute import exact_limits
from spc.core.charts.variable import imr, median_r, xbar_r, xbar_s
from spc.core.constants import ALPHA_3SIGMA, c4, cn, d2, u_quantile, w_quantile
from spc.core.rules import RuleSet, evaluate

SUBGROUP_KINDS = ("xbar-s", "xbar-r", "median-r")
ATTRIBUTE_KINDS = ("p", "np", "c", "u")
KINDS = (*SUBGROUP_KINDS, "imr", *ATTRIBUTE_KINDS)
ATTRIBUTE_RULES = ("beyond_limits", "run", "trend")  # the criteria that make sense for counts: no sigma, no middle third
MAX_COUNT_SIZE = 1_000_000
STEPS = ("resample", "adjust_parameters", "verify_sample", "adjust_elements", "root_cause", "containment", "other")
ACTION_STEPS = ("adjust_parameters", "adjust_elements", "root_cause", "containment", "other")
EVENT_KINDS = ("ack", "action", "observation", "escalation")
OUTCOMES = ("recovered", "invalid_sample", "escalated")
RULE_NAMES = ("beyond_limits", "run", "trend", "middle_third", "two_of_three_beyond_2s", "four_of_five_beyond_1s",
              "fifteen_within_1s")
CONTEXT_POINTS = 100  # the criteria look at the latest points of the current limits revision
MAX_N = 25
TEXT = 2000


class MonitorError(ValueError):
    """A request that breaks the rules of the monitor. `code` is the stable message code of the API."""

    def __init__(self, code: str, message: str = "", status: int = 400, **params):
        super().__init__(message or code)
        self.code, self.status, self.params = code, status, params


def _text(data: Mapping, key: str, limit: int, required=False) -> str:
    v = data.get(key, "")
    if not isinstance(v, str) or len(v) > limit or (required and not v.strip()):
        raise ValueError(f"{key} must be a text of at most {limit} characters" + (" and must not be empty" if required else ""))
    return v.strip()


def _step_config(data: Any, where: str) -> dict:
    d = dict(data or {})
    if set(d) - {"operator_action", "responsible", "escalate_to", "escalate_after_min"}:
        raise ValueError(f"{where}: unknown setting(s)")
    minutes = d.get("escalate_after_min", 0)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 10080:
        raise ValueError(f"{where}.escalate_after_min must be a whole number of minutes, 0 = never")
    return {"operator_action": _text(d, "operator_action", TEXT), "responsible": _text(d, "responsible", 200),
            "escalate_to": _text(d, "escalate_to", 200), "escalate_after_min": minutes}


def validate_config(data: Mapping[str, Any]) -> dict:
    """Check and normalise the configuration of a monitor. Raises ValueError that names the field."""
    d = dict(data)
    allowed = {"name", "process", "characteristic", "unit", "line", "kind", "n", "alpha", "warn_alpha", "rules", "specs",
               "ocap", "require_ack", "active"}
    if set(d) - allowed:
        raise ValueError(f"unknown setting(s): {sorted(set(d) - allowed)}")
    out: dict[str, Any] = {"name": _text(d, "name", 100, required=True), "process": _text(d, "process", 200),
                           "characteristic": _text(d, "characteristic", 200, required=True), "unit": _text(d, "unit", 40),
                           "line": _text(d, "line", 200)}
    if d.get("kind") not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    out["kind"] = d["kind"]
    kind = d["kind"]
    n = d.get("n", 1 if kind in ("imr", "c") else None)
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("n must be a whole number")
    if kind in ("imr", "c") and n != 1:
        raise ValueError("this monitor takes one value per sample (a count per inspection unit, or an individual value): n must be 1")
    if kind in SUBGROUP_KINDS and not 2 <= n <= MAX_N:
        raise ValueError(f"n must be between 2 and {MAX_N} for a subgroup chart")
    if kind == "xbar-r" and n >= 10:
        raise ValueError("the range chart is meant for subgroup sizes below 10")
    if kind == "median-r" and n > 10:
        raise ValueError("the median chart is defined for subgroup sizes 2 to 10")
    if kind in ("p", "np", "u") and not 1 <= n <= MAX_COUNT_SIZE:
        raise ValueError(f"n (the usual sample size) must be between 1 and {MAX_COUNT_SIZE}")
    out["n"] = n
    for key, default in (("alpha", ALPHA_3SIGMA), ("warn_alpha", None)):
        v = d.get(key, default)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v < 1):
            raise ValueError(f"{key} must be between 0 and 1")
        out[key] = None if v is None else float(v)
    if out["warn_alpha"] is not None and not out["warn_alpha"] > out["alpha"]:
        raise ValueError("warn_alpha must be larger than alpha: warning limits lie inside the control limits")
    try:
        out["rules"] = RuleSet(**(d.get("rules") or {})).__dict__.copy()
    except TypeError as exc:
        raise ValueError(f"rules: {exc}") from None
    if kind in ATTRIBUTE_KINDS:
        extra = [name for name in RULE_NAMES if name not in ATTRIBUTE_RULES and out["rules"].get(name)]
        if extra:
            raise ValueError(f"for counts only the control limits, runs and trends apply (not {extra}): the distribution is not normal")
    specs = dict(d.get("specs") or {})
    if set(specs) - {"lsl", "usl", "target_class", "model", "controlled_stable", "edition"}:
        raise ValueError("specs: unknown setting(s)")
    for key in ("lsl", "usl"):
        if specs.get(key) is not None and (isinstance(specs[key], bool) or not isinstance(specs[key], (int, float)) or not math.isfinite(specs[key])):
            raise ValueError(f"specs.{key} must be a number")
    if specs.get("lsl") is not None and specs.get("usl") is not None and not specs["lsl"] < specs["usl"]:
        raise ValueError("specs: lsl must be below usl")
    if specs.get("target_class") not in (None, "critical", "major", "minor", "others"):
        raise ValueError("specs.target_class must be critical, major, minor or others")
    if specs.get("model") not in (None, "A1", "A2", "B", "C1", "C2", "C3", "C4", "D"):
        raise ValueError("specs.model must be one of A1 .. D")
    if specs.get("edition", "draft") not in ("draft", "final"):
        raise ValueError("specs.edition must be 'draft' or 'final'")
    if kind in ATTRIBUTE_KINDS and any(specs.get(k) is not None for k in ("lsl", "usl", "target_class", "model")):
        raise ValueError("an attribute monitor has no specification limits and no capability indices")
    out["specs"] = {"lsl": specs.get("lsl"), "usl": specs.get("usl"), "target_class": specs.get("target_class"),
                    "model": specs.get("model"), "controlled_stable": bool(specs.get("controlled_stable", False)),
                    "edition": specs.get("edition", "draft")}
    ocap = dict(d.get("ocap") or {})
    if set(ocap) - {"default", "rules"}:
        raise ValueError("ocap: unknown setting(s)")
    rules = dict(ocap.get("rules") or {})
    bad = [r for r in rules if r not in RULE_NAMES]
    if bad:
        raise ValueError(f"ocap.rules: unknown criteria {bad}")
    out["ocap"] = {"default": _step_config(ocap.get("default"), "ocap.default"),
                   "rules": {r: _step_config(c, f"ocap.rules.{r}") for r, c in rules.items()}}
    out["require_ack"] = bool(d.get("require_ack", True))
    out["active"] = bool(d.get("active", True))
    return out


def ocap_for(config: Mapping, rule: str) -> dict:
    """The adjustment instructions for a violated criterion: its own entry, else the default one."""
    return config["ocap"]["rules"].get(rule) or config["ocap"]["default"]


# ---------------------------------------------------------------------------------------- limits

def _band(kind: str, n: int, alpha: float, mu: float, sigma: float) -> dict[str, float]:
    u = u_quantile(alpha)
    half = u * (cn(n) if kind == "median-r" else 1.0) * sigma / math.sqrt(n)
    if kind == "xbar-s":
        f = n - 1
        v_lo = math.sqrt(chi2.ppf(alpha / 2.0, f) / f) * sigma
        v_hi = math.sqrt(chi2.ppf(1.0 - alpha / 2.0, f) / f) * sigma
    else:
        m = 2 if kind == "imr" else n
        v_lo, v_hi = w_quantile(m, alpha / 2.0) * sigma, w_quantile(m, 1.0 - alpha / 2.0) * sigma
    return {"loc_lcl": mu - half, "loc_ucl": mu + half, "var_lcl": v_lo, "var_ucl": v_hi}


def compute_limits(kind: str, n: int, alpha: float, warn_alpha: float | None, mu: float, sigma: float,
                   var_center: float | None = None) -> dict:
    """Fixed limits from a location mu and a standard deviation sigma of the individual values.

    The centre of the variation chart is the expected value of the plotted statistic (c4 * sigma for s,
    d2 * sigma for R and MR) unless the reference gave its own mean (`var_center`).
    """
    if not sigma > 0:
        raise ValueError("the standard deviation of the reference must be positive")
    band = _band(kind, n, alpha, mu, sigma)
    center = c4(n) * sigma if kind == "xbar-s" else (d2(n) if kind in ("xbar-r", "median-r") else d2(2)) * sigma
    out = {"mu": float(mu), "sigma": float(sigma), "alpha": alpha, "warn_alpha": warn_alpha,
           "location": {"lcl": band["loc_lcl"], "cl": float(mu), "ucl": band["loc_ucl"]},
           "variation": {"lcl": band["var_lcl"], "cl": float(var_center if var_center is not None else center), "ucl": band["var_ucl"]}}
    if warn_alpha:
        w = _band(kind, n, warn_alpha, mu, sigma)
        out["location"].update(wlcl=w["loc_lcl"], wucl=w["loc_ucl"])
        out["variation"].update(wlcl=w["var_lcl"], wucl=w["var_ucl"])
    return {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in out.items()}


def limits_from_values(kind: str, n: int, alpha: float, warn_alpha: float | None, matrix) -> dict:
    """Limits from reference data: a matrix of subgroups (k, n), or a 1-D array of individual values."""
    if kind == "imr":
        chart = imr(np.asarray(matrix, dtype=float).ravel(), alpha)
    else:
        m = np.asarray(matrix, dtype=float)
        if m.ndim != 2 or m.shape[1] != n:
            raise ValueError(f"the reference needs subgroups of {n} values")
        chart = {"xbar-s": xbar_s, "xbar-r": xbar_r, "median-r": median_r}[kind](m, alpha)
    return compute_limits(kind, n, alpha, warn_alpha, chart.mu_hat, chart.sigma_hat, chart.variation.center)


# ---- attribute charts (draft 10.3.6): exact binomial and Poisson limits, one chart, no variation chart

def _center_in_plot_units(kind: str, n: int, rate: float) -> float:
    return rate * n if kind == "np" else rate  # the line of an np chart is in counts, the others in the plotted unit


def band(kind: str, limits: Mapping, size: float | None = None) -> dict[str, float]:
    """Centre line and limits of one sample, in the unit that the chart plots. `size` is the sample size (p, u);
    for np and c the size is fixed. Limits depend on the size, so every plotted point has its own."""
    n = limits["n"]
    s = n if kind in ("np", "c") else size
    cl = _center_in_plot_units(kind, n, limits["center"])
    out = {"cl": cl}
    lo, hi = exact_limits(kind, limits["center"], s, limits["alpha"])
    out.update(lcl=lo, ucl=hi)
    if limits.get("warn_alpha"):
        wlo, whi = exact_limits(kind, limits["center"], s, limits["warn_alpha"])
        out.update(wlcl=wlo, wucl=whi)
    return out


def attribute_limits(kind: str, n: int, alpha: float, warn_alpha: float | None, center: float) -> dict:
    """Fixed limits from a reference level: p-bar (p, np), counts per unit (c), nonconformities per unit (u)."""
    if kind in ("p", "np") and not 0 < center < 1:
        raise ValueError("the reference proportion of nonconforming units must be between 0 and 1: a chart needs some nonconforming units")
    if kind in ("c", "u") and not center > 0:
        raise ValueError("the reference number of nonconformities must be positive: a chart needs some nonconformities")
    out = {"center": float(center), "n": n, "alpha": alpha, "warn_alpha": warn_alpha}
    out["location"] = band(kind, out, n)
    return out


def limits_from_counts(kind: str, n: int, alpha: float, warn_alpha: float | None, counts, sizes=None) -> dict:
    """Limits from reference samples: counts (and sample sizes for p and u). p-bar = total / total size."""
    x = np.asarray(counts, dtype=float)
    if x.ndim != 1 or x.size < 20 or np.any(x < 0) or np.any(x != np.floor(x)):
        raise ValueError("the reference needs at least 20 samples with whole, non-negative counts")
    if kind in ("p", "u"):
        z = np.asarray(sizes if sizes is not None else [], dtype=float)
        if z.shape != x.shape or np.any(z <= 0):
            raise ValueError("the reference needs a positive sample size for every count")
        if kind == "p" and np.any(x > z):
            raise ValueError("a count cannot exceed its sample size")
        center = float(x.sum() / z.sum())
    elif kind == "np":
        if np.any(x > n):
            raise ValueError("a count cannot exceed the sample size")
        center = float(x.sum() / (n * x.size))
    else:
        center = float(x.mean())
    return attribute_limits(kind, n, alpha, warn_alpha, center)


def check_values(config: Mapping, values) -> list[float]:
    """The measured values of one sample, checked for the kind of monitor. Returns them as floats."""
    kind, n = config["kind"], config["n"]
    need = 2 if kind in ("p", "u") else (n if kind in SUBGROUP_KINDS else 1)
    if not isinstance(values, (list, tuple)) or len(values) != need or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
        raise MonitorError("wrong_value_count", f"this monitor takes {need} value(s) per sample", need=need, n=need)
    x = [float(v) for v in values]
    if kind in ATTRIBUTE_KINDS:
        if x[0] < 0 or x[0] != math.floor(x[0]):
            raise MonitorError("bad_count", "the count must be a whole number, 0 or more")
        if kind in ("p", "u"):
            if not x[1] > 0 or x[1] > MAX_COUNT_SIZE * (1 if kind == "u" else 1) or (kind == "p" and x[1] != math.floor(x[1])):
                raise MonitorError("bad_count", "the sample size must be positive" + (" and whole" if kind == "p" else ""))
        if kind == "p" and x[0] > x[1]:
            raise MonitorError("bad_count", "there cannot be more nonconforming units than units in the sample")
        if kind == "np" and x[0] > n:
            raise MonitorError("bad_count", f"there cannot be more nonconforming units than the sample size {n}")
    return x


# ---------------------------------------------------------------------------------------- one point

def statistic(kind: str, values, previous: float | None) -> tuple[float, float | None]:
    """(location statistic, variation statistic) of a new sample. For individual values the variation
    statistic is the moving range to the previous valid value (None for the first value). For counts
    the plotted value is the count (np, c) or the proportion (p, u), and there is no variation chart."""
    x = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(x)):
        raise ValueError("measured values must be finite numbers")
    if kind in ("p", "u"):
        return float(x[0] / x[1]), None
    if kind in ("np", "c"):
        return float(x[0]), None
    if kind == "imr":
        return float(x[0]), None if previous is None else abs(float(x[0]) - previous)
    if kind == "xbar-s":
        return float(x.mean()), float(x.std(ddof=1))
    if kind == "median-r":
        return float(np.median(x)), float(x.max() - x.min())
    return float(x.mean()), float(x.max() - x.min())


def check_attribute_point(config: Mapping, limits: Mapping, history, loc: float, size: float | None):
    """Like `check_point` for a count chart. `history` holds (plotted value, sample size) of the latest valid points
    of the current limits revision. The limits of each point follow its own sample size."""
    kind = config["kind"]
    rules = RuleSet(**config["rules"])
    pts = list(history)[-(CONTEXT_POINTS - 1):] + [(loc, size)]
    bands = [band(kind, limits, s) for _, s in pts]
    values = np.array([v for v, _ in pts], dtype=float)
    lcl, ucl = np.array([b["lcl"] for b in bands]), np.array([b["ucl"] for b in bands])
    res = evaluate(values, bands[-1]["cl"], lcl, ucl, rules)
    alarms = [{"chart": "location", "rule": v.rule} for v in res.violations if v.index == values.size - 1]
    warnings = []
    last = bands[-1]
    if "wucl" in last and (loc > last["wucl"] or loc < last["wlcl"]) and not (loc > last["ucl"] or loc < last["lcl"]):
        warnings.append({"chart": "location", "rule": "warning_limits"})
    return alarms, warnings


def check_point(config: Mapping, limits: Mapping, loc_history, var_history, loc: float, var: float | None):
    """Which criteria does the new point violate? Returns (alarms, warnings), lists of {chart, rule}.

    The criteria look at the latest points of the current limits revision, but only a violation that the NEW
    point completes counts. Sigma based criteria are applied to the location chart only.
    """
    rules = RuleSet(**config["rules"])
    sigma_loc = limits["sigma"] * (cn(config["n"]) if config["kind"] == "median-r" else 1.0) / math.sqrt(config["n"])
    alarms, warnings = [], []
    lo, hi = limits["location"]["lcl"], limits["location"]["ucl"]
    series = np.append(np.asarray(loc_history, dtype=float)[-(CONTEXT_POINTS - 1):], loc)
    res = evaluate(series, limits["location"]["cl"], lo, hi, rules, sigma=sigma_loc if rules.needs_sigma else None)
    alarms += [{"chart": "location", "rule": v.rule} for v in res.violations if v.index == series.size - 1]
    if var is not None:
        var_rules = RuleSet(**{**rules.__dict__, "two_of_three_beyond_2s": False, "four_of_five_beyond_1s": False,
                               "fifteen_within_1s": False})
        vs = np.append(np.asarray(var_history, dtype=float)[-(CONTEXT_POINTS - 1):], var)
        res = evaluate(vs, limits["variation"]["cl"], limits["variation"]["lcl"], limits["variation"]["ucl"], var_rules)
        alarms += [{"chart": "variation", "rule": v.rule} for v in res.violations if v.index == vs.size - 1]
    if "wucl" in limits["location"]:
        if (loc > limits["location"]["wucl"] or loc < limits["location"]["wlcl"]) and not (loc > hi or loc < lo):
            warnings.append({"chart": "location", "rule": "warning_limits"})
        if var is not None and (var > limits["variation"]["wucl"] or var < limits["variation"]["wlcl"]) and not (
                var > limits["variation"]["ucl"] or var < limits["variation"]["lcl"]):
            warnings.append({"chart": "variation", "rule": "warning_limits"})
    return alarms, warnings

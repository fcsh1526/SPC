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

from spc.core.charts.variable import imr, xbar_r, xbar_s
from spc.core.constants import ALPHA_3SIGMA, c4, d2, u_quantile, w_quantile
from spc.core.rules import RuleSet, evaluate

KINDS = ("xbar-s", "xbar-r", "imr")
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
    n = d.get("n", 1 if d["kind"] == "imr" else None)
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("n (subgroup size) must be a whole number")
    if d["kind"] == "imr" and n != 1:
        raise ValueError("an I-MR monitor takes individual values: n must be 1")
    if d["kind"] != "imr" and not 2 <= n <= MAX_N:
        raise ValueError(f"n must be between 2 and {MAX_N} for a subgroup chart")
    if d["kind"] == "xbar-r" and n >= 10:
        raise ValueError("the range chart is meant for subgroup sizes below 10")
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
    half = u * sigma / math.sqrt(n)
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
    center = c4(n) * sigma if kind == "xbar-s" else (d2(n) if kind == "xbar-r" else d2(2)) * sigma
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
        chart = (xbar_s if kind == "xbar-s" else xbar_r)(m, alpha)
    return compute_limits(kind, n, alpha, warn_alpha, chart.mu_hat, chart.sigma_hat, chart.variation.center)


# ---------------------------------------------------------------------------------------- one point

def statistic(kind: str, values, previous: float | None) -> tuple[float, float | None]:
    """(location statistic, variation statistic) of a new sample. For individual values the variation
    statistic is the moving range to the previous valid value (None for the first value)."""
    x = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(x)):
        raise ValueError("measured values must be finite numbers")
    if kind == "imr":
        return float(x[0]), None if previous is None else abs(float(x[0]) - previous)
    if kind == "xbar-s":
        return float(x.mean()), float(x.std(ddof=1))
    return float(x.mean()), float(x.max() - x.min())


def check_point(config: Mapping, limits: Mapping, loc_history, var_history, loc: float, var: float | None):
    """Which criteria does the new point violate? Returns (alarms, warnings), lists of {chart, rule}.

    The criteria look at the latest points of the current limits revision, but only a violation that the NEW
    point completes counts. Sigma based criteria are applied to the location chart only.
    """
    rules = RuleSet(**config["rules"])
    sigma_loc = limits["sigma"] / math.sqrt(config["n"])
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

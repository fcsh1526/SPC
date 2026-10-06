"""ISO/TR 11462-3: the eleven examples for the validation of SPC software.

The data and the expected results are sold with the standard and are not in this program. What is here:
* a catalogue of the eleven examples, with what is known of each from the sources at hand (the draft names the report, other texts
  describe examples 1, 2, 3, 4, 7, 8 and 11; the others are listed as unknown and are not made up),
* the name under which the user enters an example as a reference case, so the validation run can tell which examples are covered,
* scenario checks: data of the same kind as the described examples, made here, with references that do not come from this program.
  They show that the program handles those kinds of process. They do NOT replace the data of the standard.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

from spc.core import multistate as ms
from spc.data import Dataset
from spc.service import AnalysisRequest, analyze
from spc.service.model_suggestion import suggest_for_dataset
from spc.validation.checks import Check

CASE_PREFIX = "ISO/TR 11462-3 example "
SOURCE = "ISO/TR 11462-3:2020, example {n}: data and expected results as entered from the standard"

# n: number of observations when known; request: settings that follow from what is known; features: where the program is involved
CATALOGUE = (
    {"number": 1, "known": True, "n": 125, "request": {"stage": "production", "distribution": "normal"}, "features": ["indices", "chart", "histogram"]},
    {"number": 2, "known": True, "n": 600, "request": {"stage": "production", "distribution": "weibull", "method": "G"}, "features": ["distribution", "indices"]},
    {"number": 3, "known": True, "n": 1000, "request": {"stage": "production"}, "features": ["time_model", "distribution", "indices"]},
    {"number": 4, "known": True, "n": None, "request": {"stage": "production", "model": "C1"}, "features": ["time_model", "chart", "indices"]},
    {"number": 5, "known": False, "n": None, "request": {"stage": "production"}, "features": []},
    {"number": 6, "known": False, "n": None, "request": {"stage": "production"}, "features": []},
    {"number": 7, "known": True, "n": 500, "request": {"stage": "production", "model": "C4"}, "features": ["time_model", "chart"]},
    {"number": 8, "known": True, "n": 500, "request": {"stage": "production", "model": "D"}, "features": ["time_model", "chart"]},
    {"number": 9, "known": False, "n": None, "request": {"stage": "production"}, "features": []},
    {"number": 10, "known": False, "n": None, "request": {"stage": "production"}, "features": []},
    {"number": 11, "known": True, "n": None, "request": {"stage": "production"}, "features": ["state_tests"]},
)


def case_name(number: int) -> str:
    return f"{CASE_PREFIX}{number}"


def _ds(v, sub=None):
    return Dataset.from_values([float(x) for x in np.asarray(v).ravel()], subgroup=sub)


def scenarios() -> list[Check]:
    out: list[Check] = []
    # example 1: normal, 125 values: the index with its closed formula, and no signal in a stable process
    v = np.random.default_rng(101).normal(50, 2, 125)
    r = analyze(_ds(v), AnalysisRequest(stage="production", lsl=42, usl=58, distribution="normal"))
    m, s = v.mean(), v.std(ddof=1)
    out.append(Check("S01-ppk", "iso11462", "req.scenario_normal", "constructed like example 1 (normal, 125 values); closed formula with numpy", float(min(58 - m, m - 42) / (3 * s)), r["indices"]["pk"], 1e-12))
    # example 2: skewed (Weibull), 600 values: the quantile method against the true quantiles of the distribution the data came from
    shape, scale = 1.8, 10.0
    w = stats.weibull_min.rvs(shape, scale=scale, size=600, random_state=202)
    r = analyze(_ds(w), AnalysisRequest(stage="production", lsl=0.5, usl=30.0, distribution="weibull", method="G", bootstrap_n=0))
    q = stats.weibull_min.ppf([0.00135, 0.5, 0.99865], shape, scale=scale)
    true_pp = (30.0 - 0.5) / (q[2] - q[0])
    out.append(Check("S02-pp", "iso11462", "req.scenario_skew", "constructed like example 2 (Weibull, 600 values); quantile method with the true Weibull quantiles; sampling error of the fit allowed (10 %)",
                     float(true_pp), r["indices"]["p"], 0.10))
    # example 3: a drifting location, 1000 values: the model with a trend is suggested
    d = np.linspace(0, 8, 1000) + np.random.default_rng(303).gamma(3, 1, 1000)
    out.append(Check("S03-model", "iso11462", "req.scenario_drift", "constructed like example 3 (drift, 1000 values); the draft's model C3 is a trend by wear", "C3", suggest_for_dataset(_ds(d), 5)["model"]))
    # example 4: random shifts of the location between lots: model C1
    c = (np.random.default_rng(404).normal(0, 1.5, (200, 1)) + np.random.default_rng(405).normal(0, 1, (200, 5))).ravel()
    out.append(Check("S04-model", "iso11462", "req.scenario_drift", "constructed like example 4 (random shifts of the location); the draft's model C1", "C1", suggest_for_dataset(_ds(c), 5)["model"]))
    # examples 7 and 8: a sawtooth (tool change) and a multi-stream process: a model with a changing location is suggested, not a stable one
    saw = np.array([(i % 50) * 0.06 for i in range(500)]) + np.random.default_rng(707).normal(0, 0.5, 500)
    k = 100
    ms8 = (np.tile([0, 5, 10], k)[:k, None] + np.tile([0.5, 1.5, 1.0], k)[:k, None] * np.random.default_rng(808).normal(0, 1, (k, 5))).ravel()
    for tag, data, label in (("S07", saw, "example 7 (sawtooth by tool change)"), ("S08", ms8, "example 8 (several streams)")):
        model = suggest_for_dataset(_ds(data), 5)["model"]
        out.append(Check(f"{tag}-changing", "iso11462", "req.scenario_changing", f"constructed like {label}: the location changes, so a stable-location model (A1, A2, B) must not be suggested; "
                         "which of C1 to D the standard names is for the data of the standard to show", True, model not in ("A1", "A2", "B", None), None, f"suggested {model}"))
    # example 11: several states: the three tests against scipy and published critical values of Grubbs
    rng = np.random.default_rng(1111)
    states = {"state 1": rng.normal(10, 0.2, 40), "state 2": rng.normal(10.1, 0.5, 35), "state 3": rng.normal(10.0, 0.2, 30)}
    states["state 3"][7] = 12.5
    t = ms.state_tests(states)
    g = list(states.values())
    out += [Check("S11-bartlett", "iso11462", "req.scenario_states", "scipy.stats.bartlett", float(stats.bartlett(*g).pvalue), t["bartlett"]["p"], 1e-9),
            Check("S11-fisher", "iso11462", "req.scenario_states", "scipy.stats.f_oneway", float(stats.f_oneway(*g).pvalue), t["fisher"]["p"], 1e-9),
            Check("S11-grubbs-found", "iso11462", "req.scenario_states", "an outlier of 12.5 set into a state of σ 0.2 (constructed)", ["state 3"], t["outliers"]),
            Check("S11-grubbs-10", "iso11462", "req.scenario_states", "published two-sided critical value of the Grubbs test, α = 0.05, n = 10 (NIST)", 2.2900, ms.grubbs_critical(10), 1e-4),
            Check("S11-grubbs-25", "iso11462", "req.scenario_states", "published two-sided critical value of the Grubbs test, α = 0.05, n = 25 (NIST)", 2.8217, ms.grubbs_critical(25), 1e-4)]
    return out

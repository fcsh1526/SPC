"""The checks of a validation run.

Draft 11.2 separates two things, after ISO 9000:
* verification: the specified requirements are fulfilled. Here: built-in checks of the calculation against references that do
  not come from this program (published tables, closed formulas evaluated with numpy and scipy, constructed data with a known answer).
* validation: the software fits the intended use. Here: reference cases of the user (their own data, settings and the results
  they expect from a source they name), run with exactly the settings they will use.
ISO/TR 11462-3 data is sold with the standard, so its eleven examples are not built in: they can be entered as reference cases.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from scipy import stats

from spc.core import constants as K
from spc.data import Dataset
from spc.service import AnalysisRequest, analyze


@dataclass
class Check:
    id: str
    area: str
    requirement: str  # what must hold, as a key of the report texts
    reference: str  # where the expected value comes from
    expected: Any
    actual: Any
    tol: float | None = None  # relative tolerance for numbers; None = must be equal
    note: str = ""
    abs_tol: float = 0.0  # absolute tolerance too (a published value is rounded to its printed digits)
    level: str = "must"  # "must": a difference fails the run. "known": the reference itself is inconsistent, shown with the evidence in `note`. "info": shown, not judged

    def with_abs(self, value: float) -> "Check":
        self.abs_tol = value
        self.tol = 0.0
        return self

    @property
    def ok(self) -> bool:
        a, e = self.actual, self.expected
        if isinstance(e, bool) or self.tol is None or e is None or a is None:
            return a == e
        return math.isfinite(a) and abs(a - e) <= max(self.tol * abs(e), self.abs_tol, 1e-12)

    @property
    def status(self) -> str:
        """pass | fail | known | info. A check that matches passes whatever its level."""
        if self.ok:
            return "pass"
        return {"known": "known", "info": "info"}.get(self.level, "fail")

    def to_dict(self) -> dict:
        return {"id": self.id, "area": self.area, "requirement": self.requirement, "reference": self.reference, "expected": _plain(self.expected),
                "actual": _plain(self.actual), "tol": self.tol, "abs_tol": self.abs_tol, "note": self.note, "ok": bool(self.ok),
                "status": self.status}


def _plain(x):
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, (list, tuple)):
        return [_plain(i) for i in x]
    return x


def _data(seed: int, n: int, mu=10.0, sd=0.1, size: int | None = 5, scale=1.0, offset=0.0):
    v = np.random.default_rng(seed).normal(mu, sd, n)
    values = [float(x) for x in v * scale + offset]
    sub = [str(i // size) for i in range(n)] if size else None
    return v, Dataset.from_values(values, subgroup=sub)


# ----------------------------------------------------------------------------------------------- groups

def constants() -> list[Check]:
    pub_d2 = {2: 1.128, 3: 1.693, 4: 2.059, 5: 2.326, 6: 2.534, 7: 2.704, 8: 2.847, 9: 2.970, 10: 3.078}
    pub_c4 = {2: 0.7979, 3: 0.8862, 4: 0.9213, 5: 0.9400, 6: 0.9515, 7: 0.9594, 8: 0.9650, 9: 0.9693, 10: 0.9727}
    out = [Check(f"V01-d2-{n}", "constants", "req.constant", "Montgomery / ASTM E2587 table of control chart factors", v, K.d2(n), 6e-4, f"d2, n={n}") for n, v in pub_d2.items()]
    out += [Check(f"V01-c4-{n}", "constants", "req.constant", "Montgomery / ASTM E2587 table of control chart factors", v, K.c4(n), 2e-4, f"c4, n={n}") for n, v in pub_c4.items()]
    out.append(Check("V01-alpha", "constants", "req.constant", "2·Φ(−3) from scipy", float(2 * stats.norm.sf(3)), K.ALPHA_3SIGMA, 1e-9, "alpha of the 3σ limits"))
    out.append(Check("V01-u", "constants", "req.constant", "scipy.stats.norm.ppf", float(stats.norm.ppf(1 - 0.0027 / 2)), K.u_quantile(0.0027), 1e-9, "u(1−α/2)"))
    return out


def indices() -> list[Check]:
    v, ds = _data(11, 100)
    lsl, usl = 9.7, 10.3
    r = analyze(ds, AnalysisRequest(stage="production", lsl=lsl, usl=usl))["indices"]
    m, s, n = v.mean(), v.std(ddof=1), len(v)
    pp, ppk = (usl - lsl) / (6 * s), min(usl - m, m - lsl) / (3 * s)
    ref = "closed formula evaluated with numpy (sample standard deviation, n−1)"
    lo = math.sqrt(stats.chi2.ppf(0.025, n - 1) / (n - 1))
    hi = math.sqrt(stats.chi2.ppf(0.975, n - 1) / (n - 1))
    ppm = 1e6 * (stats.norm.cdf((lsl - m) / s) + stats.norm.sf((usl - m) / s))
    return [Check("V02-mean", "indices", "req.mean", ref, float(m), r["mean"], 1e-12), Check("V02-sd", "indices", "req.sd", ref, float(s), r["sd"], 1e-12),
            Check("V02-pp", "indices", "req.pp", ref, float(pp), r["p"], 1e-12), Check("V02-ppk", "indices", "req.ppk", ref, float(ppk), r["pk"], 1e-12),
            Check("V02-ci-lo", "indices", "req.ci", "χ² interval of Pp with scipy.stats.chi2", float(pp * lo), r["ci_p"][0], 1e-9),
            Check("V02-ci-hi", "indices", "req.ci", "χ² interval of Pp with scipy.stats.chi2", float(pp * hi), r["ci_p"][1], 1e-9),
            Check("V02-ppm", "indices", "req.ppm", "normal tails with scipy.stats.norm", float(ppm), r["ppm"], 1e-6)]


def precision() -> list[Check]:
    """Draft 11.2: the precision of calculations with large and small numbers. The index does not depend on unit or offset."""
    out = []
    v0, ds0 = _data(5, 60)
    base = analyze(ds0, AnalysisRequest(stage="production", lsl=9.7, usl=10.3))["indices"]
    for label, scale, off in (("1e6", 1e6, 0.0), ("1e-6", 1e-6, 0.0)):
        _, ds = _data(5, 60, scale=scale, offset=off)
        r = analyze(ds, AnalysisRequest(stage="production", lsl=9.7 * scale, usl=10.3 * scale))["indices"]
        out += [Check(f"V03-pp-{label}", "precision", "req.scale", "the index is a ratio: the same data in another unit gives the same value", base["p"], r["p"], 1e-9, f"unit ×{label}"),
                Check(f"V03-ppk-{label}", "precision", "req.scale", "the index is a ratio: the same data in another unit gives the same value", base["pk"], r["pk"], 1e-9, f"unit ×{label}")]
    big = np.random.default_rng(6).normal(0.0, 1e-3, 80)
    values = [float(1e6 + x) for x in big]
    r = analyze(Dataset.from_values(values), AnalysisRequest(stage="production", lsl=1e6 - 0.006, usl=1e6 + 0.006))["indices"]
    s = float(np.std(big, ddof=1))
    out.append(Check("V03-offset-sd", "precision", "req.offset", "numpy on the centred values (no loss of digits from a large offset)", s, r["sd"], 1e-6, "mean 1 000 000, σ 0.001"))
    return out


def charts() -> list[Check]:
    from spc.core.charts.attribute import exact_limits

    v, ds = _data(21, 100)
    r = analyze(ds, AnalysisRequest(stage="production", lsl=9.7, usl=10.3))["chart"]
    xb = np.array([v[i:i + 5].mean() for i in range(0, 100, 5)])
    sb = np.array([v[i:i + 5].std(ddof=1) for i in range(0, 100, 5)])
    a3 = 1.427  # published factor A3 for n = 5
    out = [Check("V04-xbar-center", "charts", "req.limits", "grand mean of the subgroup means (numpy)", float(xb.mean()), r["location"]["center"], 1e-9),
           Check("V04-xbar-halfwidth", "charts", "req.limits", "3 · pooled s / √n (the estimator the program states), numpy", float(3 * math.sqrt((sb ** 2).mean()) / math.sqrt(5)), (r["location"]["ucl"] - r["location"]["lcl"]) / 2, 1e-9),
           Check("V04-xbar-a3", "charts", "req.limits", "A3 · s-bar with the published A3 = 1.427 (n = 5): another estimator, so close, not equal (within 2 %)", float(a3 * sb.mean()), (r["location"]["ucl"] - r["location"]["lcl"]) / 2, 2e-2)]
    vi, dsi = _data(22, 60, size=None)
    ri = analyze(dsi, AnalysisRequest(stage="production", lsl=9.7, usl=10.3))["chart"]
    mr = np.abs(np.diff(vi)).mean()
    out.append(Check("V04-imr-halfwidth", "charts", "req.limits", "2.660 · MR-bar (published factor 3/d2 for individuals)", float(2.660 * mr), (ri["location"]["ucl"] - ri["location"]["lcl"]) / 2, 1e-3))
    lcl, ucl = exact_limits("p", 0.04, 50)
    out += [Check("V04-p-lcl", "charts", "req.limits", "binomial quantile with scipy.stats.binom (n = 50, p = 0.04)", float(stats.binom.ppf(0.0027 / 2, 50, 0.04) / 50), lcl, 1e-9),
            Check("V04-p-ucl", "charts", "req.limits", "binomial quantile with scipy.stats.binom (n = 50, p = 0.04)", float(stats.binom.ppf(1 - 0.0027 / 2, 50, 0.04) / 50), ucl, 1e-9)]
    return out


def signals() -> list[Check]:
    """Draft 11.2: identification of out-of-control situations. Constructed data with a known answer."""
    v = np.random.default_rng(31).normal(10.0, 0.1, 60)
    v[37] = 11.0
    r = analyze(Dataset.from_values([float(x) for x in v]), AnalysisRequest(stage="production", lsl=9.7, usl=10.3))["chart"]["location"]
    found = sorted(a["index"] for a in r["alarms"])
    clean = analyze(Dataset.from_values([float(x) for x in np.random.default_rng(32).normal(10.0, 0.1, 60)]), AnalysisRequest(stage="production", lsl=9.7, usl=10.3))
    return [Check("V05-outlier", "signals", "req.signal", "one value set 10 σ away at position 37 (constructed)", [37], found),
            Check("V05-quiet", "signals", "req.no_signal", "60 values of a stable normal process, fixed seed (constructed)", [], [a["index"] for a in clean["chart"]["location"]["alarms"]])]


def sequential() -> list[Check]:
    from spc.core.charts.sequential import cusum_arl, cusum_h

    h = cusum_h(0.5, 370.4)
    draft = {0.0: 370.4, 0.4: 54.5, 1.0: 9.9, 2.0: 3.9, 3.0: 2.5}
    out = [Check("V06-h", "sequential", "req.arl", "CUSUM ARL table of the draft (k = 0.5, ARL0 = 370.4)", 4.775, float(h), 1e-3, "decision interval h")]
    out += [Check(f"V06-arl-{s}", "sequential", "req.arl", "CUSUM ARL table of the draft (k = 0.5, h = 4.775)", v, float(cusum_arl(h, 0.5, s)), None, f"shift {s} σ (the draft prints one decimal)").with_abs(0.06) for s, v in draft.items()]
    return out


def msa() -> list[Check]:
    from spc.core import msa as M

    A = [[.29, .41, .64], [-.56, -.68, -.58], [1.34, 1.17, 1.27], [.47, .50, .64], [-.80, -.92, -.84], [.02, -.11, -.21], [.59, .75, .66], [-.31, -.20, -.17], [2.26, 1.99, 2.01], [-1.36, -1.25, -1.31]]
    B = [[.08, .25, .07], [-.47, -1.22, -.68], [1.19, .94, 1.34], [.01, 1.03, .20], [-.56, -1.20, -1.28], [-.20, .22, .06], [.47, .55, .83], [-.63, .08, -.34], [1.80, 2.12, 2.19], [-1.68, -1.62, -1.50]]
    C = [[.04, -.11, -.15], [-1.38, -1.13, -.96], [.88, 1.09, .67], [.14, .20, .11], [-1.46, -1.07, -1.45], [-.29, -.67, -.49], [.02, .01, .21], [-.46, -.56, -.49], [1.77, 1.45, 1.87], [-1.49, -1.77, -2.16]]
    data = np.stack([A, B, C], axis=1)
    g = M.grr(data, 6.0)
    ev_range = float(np.ptp(data, axis=2).mean() * 0.5908)  # R-bar x K1 for three trials, AIAG MSA 4th edition
    return [Check("V07-ev-range", "msa", "req.grr", "AIAG MSA 4th edition, repeatability by the range method: published 0.20188", 0.20188, ev_range, 1e-3),
            Check("V07-ev-anova", "msa", "req.grr", "analysis of variance close to the range method of the same data (within 5 %)", ev_range, float(g["sigma"]["ev"]), 5e-2),
            Check("V07-interaction", "msa", "req.grr", "AIAG MSA 4th edition example: interaction part × operator, p = 0.974", 0.974, float(g["interaction_p"]), 5e-3)]


def agreement() -> list[Check]:
    from spc.core import msa_attribute as MA

    a = [1] * 25 + [0] * 25
    b = [1] * 20 + [0] * 5 + [1] * 10 + [0] * 15  # 20 both yes, 5 yes/no, 10 no/yes, 15 both no: po = 0.7, pe = 0.5
    table = np.array([[0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1], [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7]])
    return [Check("V10-cohen", "msa", "req.agreement", "Cohen's kappa, the textbook example of 50 proposals: 0.4", 0.4, float(MA.cohen_kappa(a, b)), 1e-9),
            Check("V10-fleiss", "msa", "req.agreement", "Fleiss (1971), 10 patients, 14 raters, 5 categories: kappa = 0.210", 0.210, float(MA.fleiss_kappa(table)), None, "printed with three decimals").with_abs(5e-4)]


def doe() -> list[Check]:
    from spc.core import doe as D

    A = [-1] * 3 + [1] * 3 + [-1] * 3 + [1] * 3
    B = [-1] * 6 + [1] * 6
    y = [28, 25, 27, 36, 32, 32, 18, 19, 23, 31, 30, 29]
    r = D.factorial(y, {"A": A, "B": B})
    t = {x["term"]: x for x in r["terms"]}
    ref = "Montgomery, Design and Analysis of Experiments, example 6.1 (2² with 3 replicates)"
    out = [Check(f"V11-effect-{k}", "doe", "req.doe", ref + ": effect", v, float(t[k]["effect"]), None).with_abs(5e-3) for k, v in (("A", 8.33), ("B", -5.00), ("A:B", 1.67))]
    out += [Check(f"V11-ss-{k}", "doe", "req.doe", ref + ": sum of squares", v, float(t[k]["ss"]), None).with_abs(5e-3) for k, v in (("A", 208.33), ("B", 75.00), ("A:B", 8.33))]
    out.append(Check("V11-sse", "doe", "req.doe", ref + ": error sum of squares", 31.33, float(r["sse"]), None).with_abs(5e-3))
    return out


def transparency() -> list[Check]:
    _, ds = _data(41, 50)
    r = analyze(ds, AnalysisRequest(stage="production", lsl=9.7, usl=10.3))
    keys = ["alpha", "estimate_confidence", "target_confidence", "edition", "rules", "stability_mode", "stability_confidence", "customer", "fingerprint", "engine_version"]
    again = analyze(ds, AnalysisRequest(stage="production", lsl=9.7, usl=10.3))
    changed = analyze(ds, AnalysisRequest(stage="production", lsl=9.7, usl=10.3, alpha=0.01))
    return [Check("V08-params", "transparency", "req.params", "parameter transparency, draft 11.2: every setting is part of the result", [], [k for k in keys if k not in r["params"]]),
            Check("V08-repeat", "transparency", "req.repeat", "the same data and settings give the same result", True, r == again),
            Check("V08-fingerprint", "transparency", "req.fingerprint", "another setting gives another fingerprint", True, r["params"]["fingerprint"] != changed["params"]["fingerprint"])]


def archive() -> list[Check]:
    from spc.report import generate, reproduce, verify_archive

    _, ds = _data(51, 60)
    g = generate(ds, AnalysisRequest(stage="preliminary", lsl=9.7, usl=10.3), language="en", now="2026-01-01T00:00:00+00:00", report_id="validation")
    arc = g.archive
    tampered = {**arc, "result": {**arc["result"], "indices": {**arc["result"]["indices"], "p": 9.99}}}
    return [Check("V09-verify", "archive", "req.archive", "the digest matches the stored content", True, verify_archive(arc)),
            Check("V09-tamper", "archive", "req.archive_tamper", "a changed number breaks the digest", False, verify_archive(tampered)),
            Check("V09-reproduce", "archive", "req.reproduce", "the analysis run again from the stored data gives the stored result", True, bool(reproduce(arc).reproduced))]


def _iso() -> list[Check]:
    from spc.validation.iso11462 import scenarios

    return scenarios()


def _iso22514() -> list[Check]:
    from spc.validation.iso22514 import scenarios

    return scenarios()


def _iso22514_7() -> list[Check]:
    from spc.validation.iso22514_7 import scenarios

    return scenarios()


GROUPS: tuple[tuple[str, Callable[[], list[Check]]], ...] = (
    ("constants", constants), ("indices", indices), ("precision", precision), ("charts", charts), ("signals", signals),
    ("sequential", sequential), ("msa", msa), ("agreement", agreement), ("doe", doe), ("transparency", transparency), ("archive", archive), ("iso11462", lambda: _iso()), ("iso22514", lambda: _iso22514()), ("iso22514_7", lambda: _iso22514_7()),
)


def run_builtin() -> list[Check]:
    out: list[Check] = []
    for name, fn in GROUPS:
        try:
            out += fn()
        except Exception as exc:  # a check that cannot run is a failed check, with the reason
            out.append(Check(f"{name}-error", name, "req.runs", "the check must run", "ran", "error", None, f"{type(exc).__name__}: {exc}"))
    return out

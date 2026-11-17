"""The checks of the SPC health check. They judge how SPC is used on a characteristic, with the data and the limits of the file.

What is plotted: with subgroups the subgroup mean, without subgroups the individual value. The control limits of the file are taken
as the limits of that plotted value; the report says so, because the file cannot.

Reference limits (what limits from these data would be):
  subgroups, variation between the subgroups as the variation within predicts   X-bar limits of the program (spc.service.analysis)
  subgroups, more variation between the subgroups                               limits of an individuals chart of the subgroup means
                                                                                (mean +- u * mean moving range / d2): they hold the
                                                                                variation between the subgroups (lots, panels, set-ups)
  individuals                                                                   individuals chart of the program (I-MR)

Every threshold is a field of `Settings` and is printed in the report (parameter transparency, AIAG-VDA SPC draft chapter 11).
Clauses named in `REFS` are those of the AIAG-VDA SPC draft that the program already follows; a check without a clause is practice, not the manual.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from scipy import stats

from spc.core import time_model
from spc.core.charts.extended import within_between
from spc.core.constants import ALPHA_3SIGMA, d2
from spc.data import Dataset
from spc.healthcheck.reader import Series
from spc.service.analysis import AnalysisRequest, analyze

SEVERITIES = ("high", "medium", "low", "info")

# check -> clause of the AIAG-VDA SPC draft that the program follows for it ("" = practice)
REFS = {
    "between_subgroups": "9.4, 10.3.5.3",
    "individuals_not_independent": "9.4, 10.3.3.5",
    "level_shift": "10.2.2",
    "limits_are_spec": "10.3.2.7",
    "limits_too_wide": "10.3.3",
    "limits_too_narrow": "10.3.3",
    "limits_off_centre": "10.3.3",
    "too_many_alarms": "10.2.2",
    "alarms_without_action": "5.4",
    "unstable": "10.3.2.3",
    "index_from_unstable_process": "7.2, table 7-1",
    "capability_poor": "9.5",
    "capability_below_target": "9.5",
    "not_normal": "9.4",
    "resolution_vs_tolerance": "8.2.3",
    "resolution_vs_variation": "8.2.3",
}


@dataclass(frozen=True)
class Settings:
    alpha: float = ALPHA_3SIGMA            # false alarm probability of a control limit pair (3 sigma limits)
    min_subgroups: int = 20                # fewer subgroups: the statistical checks are not made
    min_individuals: int = 50              # fewer individual values: the statistical checks are not made
    between_ratio: float = 1.5             # sd of the subgroup means / (sigma within / sqrt(n)) at or above this: limits from the within variation are too narrow
    between_alpha: float = 0.001           # significance of the analysis of variance for the same finding
    independence_ratio: float = 1.5        # individuals: sd of all values / sigma from the moving range at or above this: the level moves
    shift_min_part: float = 0.1            # a level shift needs at least this share of the points (and 5 points) on each side
    spec_tolerance: float = 0.02           # control limits within this share of the tolerance of the specification limits count as copied from them
    width_wide: float = 1.5                # width of the limits in use / width of the reference limits at or above this: too wide
    width_narrow: float = 0.67             # ... at or below this: too narrow
    centre_shift: float = 1.5              # centre in use against the centre of the data, in standard deviations of the plotted value
    alarm_rate_high: float = 0.05          # share of plotted points with an alarm at or above this: too many alarms
    min_alarms_for_actions: int = 3        # fewer alarms: the share without an action is not judged
    no_action_share: float = 0.5           # share of alarms without a recorded action at or above this
    target_pk: float = 1.33                # target of the critical index (a major characteristic in series production, draft table 9-3)
    poor_pk: float = 1.0                   # below this the index is poor whatever the class
    resolution_tolerance: float = 0.05     # resolution / tolerance above this (the policy of the MSA gate of the program)
    resolution_sigma: float = 1.0          # resolution / standard deviation above this: the gauge cannot see the variation
    repeat_run: int = 8                    # the same value this many times in a row
    normality_alpha: float = 0.01          # significance of the normality test


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return REFS.get(self.code, "")

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "params": dict(self.params), "ref": self.ref}


def _f(x) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else float(x)


def resolution(values) -> float | None:
    """The step of the values: the largest number of which all differences are whole multiples, for values written with up to 6 decimals.
    None when the values are all equal or are not on a decimal grid (computed values)."""
    x = np.asarray(values, dtype=float)
    for k in range(0, 7):
        scaled = x * 10**k
        whole = np.round(scaled)
        if np.all(np.abs(scaled - whole) <= 1e-9 * np.maximum(1.0, np.abs(scaled))):
            ints = (whole - whole.min()).astype(np.int64)
            g = int(np.gcd.reduce(ints))
            return g / 10**k if g > 0 else None
    return None


def longest_run(values) -> int:
    x = np.asarray(values, dtype=float)
    best = run = 1
    for a, b in zip(x[:-1], x[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    return best if len(x) else 0


def _subgroups(s: Series) -> tuple[np.ndarray | None, list[str], int, list[np.ndarray]]:
    """(matrix k x n of the complete subgroups, their labels, number of subgroups left out, row positions of each kept subgroup)."""
    if s.subgroup is None:
        return None, [], 0, []
    labels, groups = [], {}
    for i, lab in enumerate(s.subgroup):
        if lab not in groups:
            groups[lab] = []
            labels.append(lab)
        groups[lab].append(i)
    sizes = [len(groups[lab]) for lab in labels]
    n = max(set(sizes), key=sizes.count)  # the usual size
    if n < 2:
        return None, [], 0, []
    kept = [lab for lab in labels if len(groups[lab]) == n]
    pos = [np.array(groups[lab]) for lab in kept]
    return np.array([s.values[p] for p in pos]), [str(lab) for lab in kept], len(labels) - len(kept), pos


def step(values, min_part: int) -> tuple[int, float, float] | None:
    """The one split that separates two levels best: (index of the first point after it, mean before, mean after).
    The split maximises the squared difference of the means weighted by the sizes (the between sum of squares of two groups)."""
    x = np.asarray(values, dtype=float)
    n = len(x)
    if n < 2 * min_part:
        return None
    csum = np.cumsum(x)
    best, at = -1.0, None
    for i in range(min_part, n - min_part + 1):
        a, b = csum[i - 1] / i, (csum[-1] - csum[i - 1]) / (n - i)
        score = (a - b) ** 2 * i * (n - i) / n
        if score > best:
            best, at = score, (i, float(a), float(b))
    return at


def _between(matrix: np.ndarray) -> tuple[float, float, float, float]:
    """(sigma within, sigma between, ratio of the sd of the means to what the within variation predicts, p of the analysis of variance)."""
    k, n = matrix.shape
    _, s_in, s_out, means = within_between(matrix)
    expected = s_in / math.sqrt(n)
    ratio = float(means.std(ddof=1)) / expected if expected > 0 else math.inf
    f_stat = n * float(means.var(ddof=1)) / (s_in**2) if s_in > 0 else math.inf
    return s_in, s_out, ratio, float(stats.f.sf(f_stat, k - 1, k * (n - 1)))


def _imr_limits(x: np.ndarray, u: float) -> tuple[float, float, float, float]:
    """(centre, lcl, ucl, sigma) of an individuals chart: sigma = mean moving range / d2(2)."""
    sigma = float(np.abs(np.diff(x)).mean() / d2(2))
    centre = float(x.mean())
    return centre, centre - u * sigma, centre + u * sigma, sigma


def check_series(s: Series, settings: Settings | None = None) -> dict:
    """All checks of one characteristic. Returns a plain dict (JSON)."""
    st = settings or Settings()
    u = float(stats.norm.ppf(1 - st.alpha / 2))
    findings: list[Finding] = []
    add = lambda code, severity, **params: findings.append(Finding(code, severity, params))  # noqa: E731

    lim = dict(s.limits)
    lsl, usl, lcl, ucl = lim.get("lsl"), lim.get("usl"), lim.get("lcl"), lim.get("ucl")
    tolerance = usl - lsl if lsl is not None and usl is not None and usl > lsl else None
    matrix, labels, dropped, pos = _subgroups(s)
    by_subgroup = matrix is not None
    if by_subgroup:
        plotted = matrix.mean(axis=1)
        n, k = matrix.shape[1], matrix.shape[0]
    else:
        plotted = s.values
        n, k = 1, len(s.values)
        labels = [str(int(x)) for x in s.lines]
        pos = [np.array([i]) for i in range(k)]
    enough = k >= (st.min_subgroups if by_subgroup else st.min_individuals)
    sd_total = float(np.std(s.values, ddof=1)) if len(s.values) > 1 else 0.0

    out: dict[str, Any] = {
        "name": s.name, "n_values": int(len(s.values)), "layout": "subgroups" if by_subgroup else "individuals", "n": int(n), "k": int(k),
        "dropped_subgroups": int(dropped), "skipped_rows": int(getattr(s, "skipped_rows", 0)),
        "limits": {key: _f(v) for key, v in lim.items()}, "limit_changes": dict(s.limit_changes),
        "mean": float(np.mean(s.values)), "sd_total": sd_total,
    }
    if s.time is not None and np.any(~np.isnat(s.time)):
        t = s.time[~np.isnat(s.time)]
        out["period"] = [str(t.min()), str(t.max())]
    if s.subgroup is not None and not by_subgroup:
        add("subgroups_of_one", "low")
    if dropped:
        add("subgroup_sizes_differ", "low", dropped=int(dropped), n=int(n))

    # ------------------------------------------------------------ the measurement (first: a coarse gauge explains other findings)
    res = resolution(s.values)
    out["resolution"] = res
    coarse = False
    if res is not None:
        if tolerance is not None and res / tolerance > st.resolution_tolerance:
            add("resolution_vs_tolerance", "high", resolution=res, share=res / tolerance)
        if sd_total > 0 and res / sd_total > st.resolution_sigma:
            coarse = True
            add("resolution_vs_variation", "medium", resolution=res, sd=sd_total, distinct=int(len(np.unique(s.values))))
    run = longest_run(s.values)
    out["longest_run"] = int(run)
    if run >= st.repeat_run and not coarse:
        add("repeated_values", "medium", run=int(run))

    # ------------------------------------------------------------ reference limits from the data
    ref: dict[str, Any] = {}
    analysis = None
    between = drift = shifted = False
    if enough and sd_total > 0:
        ds = Dataset.from_values(matrix.ravel(), subgroup=np.repeat(np.arange(k), n)) if by_subgroup else Dataset.from_values(s.values)
        analysis = analyze(ds, AnalysisRequest(lsl=lsl, usl=usl, alpha=st.alpha))
        loc = analysis["chart"]["location"]
        ref = {"method": analysis["chart"]["kind"], "centre": loc["center"], "lcl": loc["lcl"], "ucl": loc["ucl"],
               "sigma_plot": (loc["ucl"] - loc["center"]) / u}
        own_alarms = sorted({a["index"] for a in loc["alarms"]})
        min_part = max(5, int(math.ceil(st.shift_min_part * k)))
        cut = step(plotted, min_part)
        if by_subgroup:
            s_in, s_out, ratio, p = _between(matrix)
            ref.update(sigma_within=s_in, sigma_between=s_out, between_ratio=ratio, between_p=p)
            if p < st.between_alpha and ratio >= st.between_ratio:
                # one change of the level, or a level that moves all the time? Take the two levels out and look again.
                if cut is not None:
                    i, before, after = cut
                    flat = matrix.copy()
                    flat[:i] -= before
                    flat[i:] -= after
                    _, _, ratio2, p2 = _between(flat)
                    shifted = not (p2 < st.between_alpha and ratio2 >= st.between_ratio)
                if shifted:
                    sigma = s_in / math.sqrt(n)
                    add("level_shift", "medium", at=labels[i], before=before, after=after, sigmas=(after - before) / sigma, points_after=int(k - i))
                    ref.update(method="xbar-after-shift", centre=after, lcl=after - u * sigma, ucl=after + u * sigma, sigma_plot=sigma)
                else:
                    between = True
                    model = time_model.suggest(matrix)["model"]
                    add("between_subgroups", "high" if len(own_alarms) / k >= st.alarm_rate_high else "medium", ratio=ratio, n=int(n),
                        share_between=s_out**2 / (s_out**2 + s_in**2), alarms=len(own_alarms), k=int(k), model=model,
                        chart=time_model.recommendation(model)["analysis"]["chart"])
                    centre, lo, hi, sigma = _imr_limits(matrix.mean(axis=1), u)
                    ref.update(method="imr-of-means", centre=centre, lcl=lo, ucl=hi, sigma_plot=sigma)
        else:
            sigma_mr = ref["sigma_plot"]
            ratio = sd_total / sigma_mr if sigma_mr > 0 else math.inf
            r1 = float(np.corrcoef(s.values[:-1], s.values[1:])[0, 1]) if k > 2 else 0.0
            ref.update(independence_ratio=ratio, lag1=r1)
            if ratio >= st.independence_ratio:
                if cut is not None:
                    i, before, after = cut
                    flat = np.r_[s.values[:i] - before, s.values[i:] - after]
                    shifted = float(np.std(flat, ddof=1)) / sigma_mr < st.independence_ratio
                if shifted:
                    centre, lo, hi, sigma = _imr_limits(s.values[i:], u)
                    add("level_shift", "medium", at=labels[i], before=before, after=after, sigmas=(after - before) / sigma, points_after=int(k - i))
                    ref.update(method="imr-after-shift", centre=centre, lcl=lo, ucl=hi, sigma_plot=sigma)
                else:
                    drift = True
                    add("individuals_not_independent", "medium", ratio=ratio, lag1=r1, alarms=len(own_alarms), k=int(k))
        ref["alarms"] = [i for i, v in enumerate(plotted) if v < ref["lcl"] or v > ref["ucl"]]
    elif not enough:
        add("too_few_data", "info", k=int(k), need=int(st.min_subgroups if by_subgroup else st.min_individuals), subgroups=by_subgroup)
    out["reference"] = {key: (_f(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for key, v in ref.items()}

    # ------------------------------------------------------------ the control limits in use
    used_alarm = None
    if lcl is not None and ucl is not None and ucl > lcl:
        used_alarm = (plotted < lcl) | (plotted > ucl)
        n_used = int(used_alarm.sum())
        centre_used = lim.get("cl") if lim.get("cl") is not None else (lcl + ucl) / 2
        copied = tolerance is not None and abs(ucl - usl) <= st.spec_tolerance * tolerance and abs(lcl - lsl) <= st.spec_tolerance * tolerance
        if copied:
            add("limits_are_spec", "high", alarms=n_used, k=int(k))
        if ref:
            width = (ucl - lcl) / (ref["ucl"] - ref["lcl"])
            out["used"] = {"width_ratio": width, "centre_shift": (centre_used - ref["centre"]) / ref["sigma_plot"], "alarms": n_used}
            if not copied and not drift and width >= st.width_wide:
                add("limits_too_wide", "high" if width >= 2 else "medium", ratio=width, alarms=n_used, k=int(k))
            if width <= st.width_narrow and not between:  # with variation between the subgroups that finding already says it
                add("limits_too_narrow", "medium", ratio=width, alarms=n_used, k=int(k))
            if not copied and abs(out["used"]["centre_shift"]) >= st.centre_shift:
                add("limits_off_centre", "medium", shift=out["used"]["centre_shift"], centre_used=centre_used, centre=ref["centre"])
        explained = between or shifted or any(f.code == "limits_too_narrow" for f in findings)
        if n_used / k >= st.alarm_rate_high and not explained:
            add("too_many_alarms", "high", alarms=n_used, k=int(k), share=n_used / k)
        changes = max(s.limit_changes.get("lcl", 0), s.limit_changes.get("ucl", 0))
        if changes > 1:
            add("limits_changed", "info", times=int(changes))
    elif lcl is not None or ucl is not None:
        add("limits_incomplete", "low")
    else:
        add("no_limits_in_file", "info")

    # ------------------------------------------------------------ alarms and what was done about them
    alarm_points = None
    if s.alarm is not None:
        alarm_points = np.array([bool(s.alarm[p].any()) for p in pos])
        out["alarm_source"] = "file"
    elif used_alarm is not None:
        alarm_points = used_alarm
        out["alarm_source"] = "limits"
    if alarm_points is not None:
        n_alarm = int(alarm_points.sum())
        out["alarms"] = n_alarm
        if s.alarm is not None and n_alarm / k >= st.alarm_rate_high and not (between or shifted) and not any(f.code in ("too_many_alarms", "limits_too_narrow") for f in findings):
            add("too_many_alarms", "high", alarms=n_alarm, k=int(k), share=n_alarm / k)
        if s.action is not None:
            acted = np.array([bool(s.action[p].any()) for p in pos])
            without = int((alarm_points & ~acted).sum())
            out["alarms_without_action"] = without
            if n_alarm >= st.min_alarms_for_actions and without / n_alarm >= st.no_action_share:
                add("alarms_without_action", "high", without=without, alarms=n_alarm, share=without / n_alarm)
        elif n_alarm:
            add("no_action_column", "info", alarms=n_alarm)

    # ------------------------------------------------------------ stability and capability
    if analysis is not None:
        stab = analysis["stability"]
        unstable = not stab["chart_stable"]
        if unstable and not (between or drift or shifted or coarse):
            add("unstable", "medium", alarms=int(stab["n_alarm_points"]), threshold=int(stab["threshold"]))
        ind = analysis.get("indices")
        if ind and ind.get("pk") is not None:
            pk, name = float(ind["pk"]), analysis["names"]["pk"]
            out["index"] = {"name": name, "pk": pk, "p": _f(ind.get("p")), "ci_pk": [_f(v) for v in (ind.get("ci_pk") or [None, None])], "ppm": _f(ind.get("ppm"))}
            if unstable or between or drift or shifted:
                add("index_from_unstable_process", "medium", pk=pk)
            if pk < st.poor_pk:
                add("capability_poor", "high", pk=pk, name=name)
            elif pk < st.target_pk:
                add("capability_below_target", "medium", pk=pk, name=name, target=st.target_pk)
        else:
            add("no_specification", "info")
        norm = analysis.get("normality")
        if norm and norm.get("p_value") is not None and norm["p_value"] < st.normality_alpha:
            add("not_normal", "low", p=float(norm["p_value"]))
    elif lsl is None and usl is None:
        add("no_specification", "info")

    findings.sort(key=lambda f: SEVERITIES.index(f.severity))
    out["findings"] = [f.to_dict() for f in findings]
    out["worst"] = findings[0].severity if findings else "ok"
    if out["worst"] == "info":
        out["worst"] = "ok"
    out["chart"] = {"labels": labels, "values": [float(v) for v in plotted],
                    "alarm": [bool(v) for v in alarm_points] if alarm_points is not None else None,
                    "used": {"lcl": _f(lcl), "ucl": _f(ucl), "cl": _f(lim.get("cl"))},
                    "reference": {key: _f(ref.get(key)) for key in ("lcl", "centre", "ucl")} if ref else None,
                    "spec": {"lsl": _f(lsl), "usl": _f(usl)} if by_subgroup is False else None}
    return out


# what a finding means for the chart as a tool
BLIND = {"limits_are_spec", "limits_too_wide"}                       # the chart cannot see a change of the process
NOISY = {"between_subgroups", "limits_too_narrow", "too_many_alarms", "individuals_not_independent"}  # alarms come from the set-up of the chart
MEASUREMENT = {"resolution_vs_tolerance", "resolution_vs_variation", "repeated_values"}
CAPABILITY = {"index_from_unstable_process", "capability_poor", "capability_below_target"}


def check_all(series: list[Series], settings: Settings | None = None) -> dict:
    """The checks of every characteristic and the summary for the first page."""
    st = settings or Settings()
    results = [check_series(s, st) for s in series]

    def count(codes: set[str]) -> int:
        return sum(1 for r in results if any(f["code"] in codes for f in r["findings"]))

    summary = {
        "characteristics": len(results),
        "by_worst": {sev: sum(1 for r in results if r["worst"] == sev) for sev in ("high", "medium", "low", "ok")},
        "blind": count(BLIND), "noisy": count(NOISY), "measurement": count(MEASUREMENT), "capability": count(CAPABILITY),
        "with_limits": sum(1 for r in results if r["chart"]["used"]["lcl"] is not None and r["chart"]["used"]["ucl"] is not None),
        "alarms": sum(r.get("alarms", 0) for r in results),
        "alarms_without_action": sum(r.get("alarms_without_action", 0) for r in results),
        "action_records": any("alarms_without_action" in r for r in results),
        "too_few_data": count({"too_few_data"}),
    }
    return {"settings": asdict(st), "summary": summary, "results": results}

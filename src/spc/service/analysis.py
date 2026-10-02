"""One analysis run: chart, criteria, stability, index names, indices, targets.

The result is a plain dict that can go straight to JSON. Messages are codes with parameters, so a
user interface can show them in any language. Nothing here is translated.

Assumptions of this version, stated in the result as warnings:

* Capability indices assume a normal distribution. A normality test is run and reported. The
  fitted-distribution methods (.G / .Z for non-normal data) are not available yet.
* The time-dependent model (A1 .. D) is chosen by the user. It is not detected.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

import numpy as np
from scipy.stats import norm, normaltest, shapiro

from spc.core.capability import (
    Stage,
    TargetAdjustmentNotAllowed,
    cp_confidence_interval,
    cpk_confidence_interval,
    index_names,
    overall_indices,
    ppm_out_of_spec,
    required_targets,
    within_indices,
)
from spc.core.capability.target import BASE_SAMPLE_SIZE
from spc.core.charts.variable import SubgroupChart, imr, xbar_r, xbar_s
from spc.core.constants import ALPHA_3SIGMA
from spc.core.rules import RuleSet, evaluate
from spc.core.stability import Stability, assess_analysis_chart, classify_stability
from spc.data import Dataset
from spc.params import AnalysisParams

CHARTS = ("auto", "xbar-s", "xbar-r", "imr")
STAGES = ("machine", "preliminary", "production")
XBAR_S_MIN_N = 5  # draft figure 10-5 as reported by the 2026-09 article; n < 5 uses X̄-R


@dataclass(frozen=True)
class AnalysisRequest:
    stage: str = "production"
    chart: str = "auto"
    subgroup_size: int | None = None  # only for data without subgroup labels
    lsl: float | None = None
    usl: float | None = None
    alpha: float = ALPHA_3SIGMA
    rules: dict[str, Any] = field(default_factory=dict)
    stability_mode: str = "random_range"
    stability_confidence: float = 0.99
    model: str | None = None  # time-dependent model A1..D, chosen by the user
    controlled_stable: bool = False
    characteristic_class: str | None = None
    edition: str = "draft"
    estimate_confidence: float = 0.95
    target_confidence: float = 0.9999
    incomplete: str = "drop"
    customer: str | None = None


def rules_from_dict(data: dict[str, Any]) -> RuleSet:
    allowed = {f.name for f in fields(RuleSet)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown rule setting(s): {sorted(unknown)}")
    return RuleSet(**data)


def _f(x) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _list(a) -> list:
    return [_f(v) for v in np.asarray(a, dtype=float).tolist()]


def _warn(out: list, code: str, **params) -> None:
    out.append({"code": code, "params": params})


def _normality(x: np.ndarray) -> dict | None:
    if x.size < 3 or float(np.ptp(x)) == 0.0:
        return None
    if x.size <= 5000:
        stat, p = shapiro(x)
        test = "shapiro-wilk"
    else:
        stat, p = normaltest(x)
        test = "dagostino-pearson"
    return {"test": test, "statistic": _f(stat), "p_value": _f(p), "n": int(x.size)}


def _choose_chart(requested: str, n: int) -> str:
    if requested not in CHARTS:
        raise ValueError(f"chart must be one of {CHARTS}")
    if requested == "auto":
        return "imr" if n == 1 else ("xbar-s" if n >= XBAR_S_MIN_N else "xbar-r")
    if requested == "imr" and n != 1:
        raise ValueError("the I-MR chart needs individual values (subgroup size 1)")
    if requested != "imr" and n < 2:
        raise ValueError(f"the {requested} chart needs subgroups of at least 2 values")
    return requested


def _chart_json(chart: SubgroupChart, loc_labels, var_labels, loc_pos, var_pos) -> dict:
    def part(limits, values, labels, positions, alarms):
        return {
            "lcl": _f(limits.lcl),
            "center": _f(limits.center),
            "ucl": _f(limits.ucl),
            "values": _list(values),
            "labels": [str(s) for s in labels],
            "positions": positions,
            "alarms": alarms,
        }

    return {
        "kind": chart.kind,
        "n": chart.n,
        "k": chart.k,
        "alpha": chart.alpha,
        "mu_hat": _f(chart.mu_hat),
        "sigma_hat": _f(chart.sigma_hat),
        "location": part(chart.location, chart.location_values, loc_labels, loc_pos, []),
        "variation": part(chart.variation, chart.variation_values, var_labels, var_pos, []),
    }


def _verdict(estimate: float | None, lower: float | None, target: float | None) -> str | None:
    if estimate is None or target is None:
        return None
    if lower is not None and lower >= target:
        return "meets"
    if estimate >= target:
        return "meets_estimate_only"  # estimate reaches the target, the lower confidence bound does not
    return "fails"


def analyze(dataset: Dataset, req: AnalysisRequest) -> dict:
    if req.stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    if req.lsl is not None and req.usl is not None and not req.lsl < req.usl:
        raise ValueError("lsl must be below usl")
    params = AnalysisParams(
        alpha=req.alpha,
        estimate_confidence=req.estimate_confidence,
        target_confidence=req.target_confidence,
        edition=req.edition,
        rules=rules_from_dict(req.rules),
        stability_mode=req.stability_mode,
        stability_confidence=req.stability_confidence,
        customer=req.customer,
    )
    rules = params.rules
    warnings: list[dict] = []
    for note in dataset.warnings:
        _warn(warnings, f"data_{note.code}", **note.params)
    if req.edition == "final":
        _warn(warnings, "edition_final_unverified")

    # ---- structure of the data
    dropped = []
    if req.stage == "machine":
        values, positions = dataset.individuals()
        n_sub, matrix, labels, pos_rows = 1, None, None, None
    else:
        has_labels = dataset.subgroup is not None
        size = req.subgroup_size
        if not has_labels and (size is None or size == 1):
            values, positions = dataset.individuals()
            n_sub, matrix, labels, pos_rows = 1, None, None, None
        else:
            sg = dataset.subgroups(size=None if has_labels else size, incomplete=req.incomplete)
            matrix, labels, pos_rows, n_sub = sg.matrix, list(sg.labels), sg.positions, sg.nominal_size
            dropped = list(sg.dropped)
            values, positions = matrix.ravel(), pos_rows.ravel()
            if dropped:
                _warn(warnings, "dropped_subgroups", count=len(dropped), labels=[d.label for d in dropped[:5]])

    # ---- chart
    kind = _choose_chart(req.chart if req.stage != "machine" else "imr", n_sub)
    if kind == "imr":
        v_all, p_all = dataset.individuals() if matrix is None else (values, positions)
        chart = imr(v_all, req.alpha)
        src_rows = dataset.source_rows[p_all]
        loc_labels = [str(r) for r in src_rows]
        loc_pos = [[int(p)] for p in p_all]
        var_labels, var_pos = loc_labels[1:], [[int(p_all[i]), int(p_all[i + 1])] for i in range(len(p_all) - 1)]
        sigma_loc = chart.sigma_hat
    else:
        chart = (xbar_s if kind == "xbar-s" else xbar_r)(matrix, req.alpha)
        loc_labels = var_labels = labels
        loc_pos = var_pos = [[int(p) for p in row] for row in pos_rows]
        sigma_loc = chart.sigma_hat / math.sqrt(chart.n)
    out_chart = _chart_json(chart, loc_labels, var_labels, loc_pos, var_pos)

    # ---- criteria on both charts. Sigma-based criteria only make sense on the location chart.
    var_rules = replace(rules, two_of_three_beyond_2s=False, four_of_five_beyond_1s=False, fifteen_within_1s=False)
    loc_res = evaluate(chart.location_values, chart.location.center, chart.location.lcl, chart.location.ucl,
                       rules, sigma=sigma_loc if rules.needs_sigma else None)
    var_res = evaluate(chart.variation_values, chart.variation.center, chart.variation.lcl, chart.variation.ucl,
                       var_rules)
    out_chart["location"]["alarms"] = [{"index": v.index, "rule": v.rule} for v in loc_res.violations]
    out_chart["variation"]["alarms"] = [{"index": v.index, "rule": v.rule} for v in var_res.violations]
    n_alarms = loc_res.n_alarm_points + var_res.n_alarm_points

    # ---- stability
    enabled = sum(
        bool(x)
        for x in (rules.beyond_limits, rules.run_length, rules.trend_length, rules.middle_third,
                  rules.two_of_three_beyond_2s, rules.four_of_five_beyond_1s, rules.fifteen_within_1s)
    )
    # Rough false-alarm probability per checked point: each enabled criterion counted as one independent
    # test at risk alpha. Run-type criteria are not independent, so this is an approximation.
    alpha_point = 1.0 - (1.0 - req.alpha) ** max(enabled, 1)
    k_points = len(chart.location_values) + len(chart.variation_values)  # both charts are checked
    alpha_total = alpha_point
    stability_block: dict[str, Any] = {"assessed": req.stage != "machine"}
    if req.stage == "machine":
        stability = Stability.UNKNOWN
    else:
        res = assess_analysis_chart(n_alarms, k_points, alpha_point, req.stability_mode, req.stability_confidence)
        stability = classify_stability(res.stable, req.model, req.controlled_stable)
        stability_block.update(
            chart_stable=res.stable, n_alarm_points=n_alarms, expected_false_alarms=_f(res.expected_false_alarms),
            threshold=res.threshold, mode=res.mode, alpha_point_approx=_f(alpha_total), n_points_checked=k_points,
            model=req.model,
            controlled_stable=req.controlled_stable,
        )
        if chart.k < 25:
            _warn(warnings, "few_subgroups", k=chart.k)
    stability_block["class"] = stability.value
    names = index_names(Stage(req.stage), stability)

    # ---- indices
    x = np.asarray(values, dtype=float)
    result: dict[str, Any] = {
        "stage": req.stage,
        "counts": {**dataset.summary(), "n_used": int(x.size), "subgroup_size": n_sub},
        "chart": out_chart,
        "stability": stability_block,
        "names": {"p": names.p, "pk": names.pk, "reason": names.reason},
        "warnings": warnings,
        "params": {**params.to_dict(), "fingerprint": params.fingerprint()},
        "source": None if dataset.source is None else asdict(dataset.source),
        "indices": None,
        "targets": None,
        "normality": None,
        "diagnosis": None,
    }
    base =BASE_SAMPLE_SIZE[Stage(req.stage)]
    if x.size < base:
        _warn(warnings, "sample_below_base", n=int(x.size), base=base)
    if req.stage == "production" and stability not in (Stability.STATISTICAL_CONTROL, Stability.IN_CONTROL):
        _warn(warnings, "named_pp_not_cp", reason=stability.value)

    if req.lsl is None and req.usl is None:
        _warn(warnings, "spec_missing")
        return result

    norm_res = _normality(x)
    result["normality"] = norm_res
    if norm_res and norm_res["p_value"] is not None and norm_res["p_value"] < 0.05:
        _warn(warnings, "non_normal", p_value=norm_res["p_value"], test=norm_res["test"])

    idx = overall_indices(x, req.lsl, req.usl)
    mean, sd = float(x.mean()), float(x.std(ddof=1))
    dist = norm(mean, sd)
    ci_p = cp_confidence_interval(idx.p, idx.n, req.estimate_confidence) if idx.p is not None else None
    ci_pk = cpk_confidence_interval(idx.pk, idx.n, req.estimate_confidence)
    result["indices"] = {
        "p": _f(idx.p), "pk": _f(idx.pk), "pu": _f(idx.pu), "pl": _f(idx.pl),
        "ci_p": None if ci_p is None else [_f(ci_p[0]), _f(ci_p[1])],
        "ci_pk": [_f(ci_pk[0]), _f(ci_pk[1])],
        "ci_confidence": req.estimate_confidence,
        "mean": _f(mean), "sd": _f(sd), "n": idx.n,
        "ppm": _f(ppm_out_of_spec(dist, req.lsl, req.usl)),
        "method": "normal, total standard deviation",
    }
    if idx.p is None:
        _warn(warnings, "one_sided_no_p")

    if req.stage != "machine" and n_sub >= 2:
        cw = within_indices(matrix, req.lsl, req.usl)
        result["diagnosis"] = {"name_p": "Cw" if cw.p is not None else None, "name_pk": "Cwk",
                               "p": _f(cw.p), "pk": _f(cw.pk), "not_for_reports": True}

    # ---- targets
    if req.characteristic_class:
        try:
            t = required_targets(Stage(req.stage), req.characteristic_class, idx.n, req.target_confidence,
                                 edition=req.edition)
            result["targets"] = {
                "class": req.characteristic_class.lower(), "p": _f(t.p), "pk": _f(t.pk), "n_base": t.n_base,
                "n": t.n, "adjusted": t.adjusted, "confidence": t.confidence, "edition": t.edition,
                "verdict_pk": _verdict(idx.pk, ci_pk[0], t.pk),
                "verdict_p": _verdict(idx.p, ci_p[0] if ci_p else None, t.p) if idx.p is not None else None,
                "blocked": False,
            }
        except TargetAdjustmentNotAllowed:
            result["targets"] = {"class": req.characteristic_class.lower(), "blocked": True, "n": idx.n,
                                 "n_base": base}
            _warn(warnings, "target_not_allowed", n=idx.n, base=base)
    return result

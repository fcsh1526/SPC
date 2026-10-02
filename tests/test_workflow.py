"""End-to-end use of the phase 1 modules: chart -> rules -> stability -> naming -> targets -> interval."""

import numpy as np

from spc.core.capability import (
    Stage,
    cp_confidence_interval,
    cpk_confidence_interval,
    index_names,
    overall_indices,
    required_targets,
    within_indices,
)
from spc.core.charts.variable import xbar_s
from spc.core.rules import RuleSet, evaluate
from spc.core.stability import assess_analysis_chart, classify_stability
from spc.params import AnalysisParams


def _study(seed: int, drift: float = 0.0):
    rng = np.random.default_rng(seed)
    base = 10.0 + np.linspace(0, drift, 25)[:, None]
    return base + rng.normal(0, 0.1, (25, 5))


def test_stable_production_study_is_reported_as_cp_cpk():
    params = AnalysisParams()
    data = _study(21)
    chart = xbar_s(data, params.alpha)

    sigma_mean = chart.sigma_hat / np.sqrt(chart.n)
    loc = evaluate(chart.location_values, chart.location.center, chart.location.lcl, chart.location.ucl,
                   params.rules, sigma=sigma_mean)
    var = evaluate(chart.variation_values, chart.variation.center, chart.variation.lcl, chart.variation.ucl,
                   params.rules)
    alarms = loc.n_alarm_points + var.n_alarm_points
    assert alarms == 0

    result = assess_analysis_chart(alarms, chart.k, params.alpha, mode=params.stability_mode)
    stability = classify_stability(result.stable, model="A1")
    names = index_names(Stage.PRODUCTION, stability)
    assert (names.p, names.pk) == ("Cp", "Cpk")

    idx = overall_indices(data, 9.0, 11.0)
    targets = required_targets(Stage.PRODUCTION, "major", data.size, params.target_confidence)
    assert not targets.adjusted  # 125 values meet the base sample size
    assert idx.pk > targets.pk
    lo, _ = cpk_confidence_interval(idx.pk, idx.n, params.estimate_confidence)
    assert lo > targets.pk  # the lower confidence bound still meets the target
    lo_p, hi_p = cp_confidence_interval(idx.p, idx.n)
    assert lo_p < idx.p < hi_p


def test_drifting_process_is_not_called_capable_and_cwk_shows_the_drift():
    params = AnalysisParams(rules=RuleSet(run_length=7))
    data = _study(22, drift=0.8)
    chart = xbar_s(data, params.alpha)
    loc = evaluate(chart.location_values, chart.location.center, chart.location.lcl, chart.location.ucl,
                   params.rules)
    assert loc.n_alarm_points > 0  # the drift gives long runs on one side of the center line

    result = assess_analysis_chart(loc.n_alarm_points, chart.k, params.alpha, mode="strict")
    assert not result.stable
    names = index_names(Stage.PRODUCTION, classify_stability(result.stable, model="C3"))
    assert names.pk == "Ppk"

    cw = within_indices(data, 9.0, 11.0)
    pp = overall_indices(data, 9.0, 11.0)
    assert cw.pk > pp.pk  # within sigma hides the drift

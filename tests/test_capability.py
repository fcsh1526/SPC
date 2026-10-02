"""Capability indices against the draft's example reports (chapter 12) and basic properties."""

import numpy as np
import pytest
from scipy.stats import norm

from spc.core.capability import (
    Stage,
    cp_confidence_interval,
    cpk_confidence_interval,
    geometric_from_quantiles,
    geometric_indices,
    index_names,
    overall_indices,
    ppm_out_of_spec,
    within_indices,
    zscore_indices,
)
from spc.core.stability import Stability


def test_draft_report_normal_example():
    # Draft Figure 12-1: LSL 129.90, USL 130.25, X50 130.0392, X99.865 - X0.135 = 0.1956, N = 875.
    sigma = 0.1956 / 6
    dist = norm(130.0392, sigma)
    g = geometric_indices(dist, 129.90, 130.25)
    assert g.p == pytest.approx(1.79, abs=0.005)
    assert g.pk == pytest.approx(1.42, abs=0.005)
    lo, hi = cp_confidence_interval(g.p, 875)
    assert (lo, hi) == pytest.approx((1.71, 1.87), abs=0.006)
    lo, hi = cpk_confidence_interval(g.pk, 875)
    assert (lo, hi) == pytest.approx((1.35, 1.49), abs=0.006)
    # Report: 0.00098 % below LSL (about 9.8 ppm)
    assert ppm_out_of_spec(dist, 129.90, 130.25) == pytest.approx(9.8, abs=1.0)


def test_draft_report_quantile_example_from_given_quantiles():
    # Draft Figure 12-3: LSL 29.860, USL 30.140 and the three quantiles of the fitted distribution.
    g = geometric_from_quantiles(29.860, 30.140, 29.92544, 30.00923, 30.08731)
    assert g.p == pytest.approx(1.73, abs=0.005)
    assert g.pk == pytest.approx(1.67, abs=0.005)
    assert g.pu == pytest.approx(1.675, abs=0.001)  # the upper side limits the capability
    assert g.pl == pytest.approx(1.78, abs=0.005)
    assert g.pu < g.pl


def test_geometric_equals_classic_formulas_for_normal():
    dist = norm(10.0, 0.5)
    g = geometric_indices(dist, 8.0, 12.5)
    assert g.p == pytest.approx((12.5 - 8.0) / (6 * 0.5), rel=1e-4)
    assert g.pk == pytest.approx(min(12.5 - 10.0, 10.0 - 8.0) / (3 * 0.5), rel=1e-4)


def test_zscore_equals_geometric_for_normal():
    dist = norm(10.0, 0.5)
    g = geometric_indices(dist, 8.0, 12.5)
    z = zscore_indices(dist, 8.0, 12.5)
    assert z.pk == pytest.approx(g.pk, rel=1e-4)
    assert z.p == pytest.approx(g.p, rel=1e-4)


def test_zscore_differs_from_geometric_for_skewed_data():
    from scipy.stats import lognorm

    dist = lognorm(s=0.5, scale=10.0)
    g = geometric_indices(dist, 2.0, 60.0)
    z = zscore_indices(dist, 2.0, 60.0)
    assert g.pk != pytest.approx(z.pk, rel=1e-3)


def test_overall_indices_use_total_standard_deviation():
    rng = np.random.default_rng(5)
    x = rng.normal(100.0, 2.0, 500)
    r = overall_indices(x, 90.0, 112.0)
    s = x.std(ddof=1)
    assert r.p == pytest.approx(22.0 / (6 * s))
    assert r.pk == pytest.approx(min(112.0 - x.mean(), x.mean() - 90.0) / (3 * s))
    assert r.n == 500


def test_one_sided_specification_gives_only_the_location_index():
    x = np.random.default_rng(2).normal(5.0, 1.0, 100)
    r = overall_indices(x, usl=10.0)
    assert r.p is None and r.pl is None
    assert r.pk == r.pu


def test_within_indices_ignore_between_subgroup_drift():
    rng = np.random.default_rng(9)
    drift = np.linspace(0, 3.0, 25)[:, None]  # location drifts between subgroups
    data = 50.0 + drift + rng.normal(0, 0.5, (25, 5))
    lsl, usl = 45.0, 58.0
    cw = within_indices(data, lsl, usl)
    pp = overall_indices(data, lsl, usl)
    # Drift inflates the total sigma, so Cwk is larger than Ppk. This is the diagnosis signal.
    assert cw.pk > pp.pk
    assert cw.p > pp.p
    assert "within" in cw.method


def test_within_estimators_agree_roughly_for_stable_data():
    rng = np.random.default_rng(13)
    data = rng.normal(0, 1, (200, 5))
    a = within_indices(data, -5, 5, "sqrt_mean_var").spread
    b = within_indices(data, -5, 5, "rbar").spread
    c = within_indices(data, -5, 5, "sbar").spread
    assert a == pytest.approx(1.0, abs=0.05)
    assert b == pytest.approx(a, rel=0.03)
    assert c == pytest.approx(a, rel=0.03)
    with pytest.raises(ValueError):
        within_indices(rng.normal(0, 1, (20, 12)), -5, 5, "rbar")


def test_spec_limits_validation():
    with pytest.raises(ValueError):
        overall_indices([1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        overall_indices([1.0, 2.0, 3.0], lsl=5, usl=1)


def test_naming_gate():
    assert index_names(Stage.MACHINE, Stability.STATISTICAL_CONTROL).p == "Pm"
    assert index_names(Stage.PRELIMINARY, Stability.STATISTICAL_CONTROL).pk == "Ppk"
    assert index_names(Stage.PRODUCTION, Stability.STATISTICAL_CONTROL).pk == "Cpk"
    assert index_names(Stage.PRODUCTION, Stability.IN_CONTROL).p == "Cp"
    for s in (Stability.UNKNOWN, Stability.UNSTABLE):
        assert index_names(Stage.PRODUCTION, s).pk == "Ppk"

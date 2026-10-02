import numpy as np
import pytest
from scipy.stats import chi2

from spc.core.charts.variable import imr, xbar_r, xbar_s
from spc.core.constants import ALPHA_3SIGMA, d2, d3, w_quantile


@pytest.fixture
def normal_subgroups():
    rng = np.random.default_rng(20260201)
    return rng.normal(10.0, 0.5, size=(25, 5))


def test_xbar_s_limits_follow_formulas(normal_subgroups):
    chart = xbar_s(normal_subgroups)
    x = normal_subgroups
    sigma = np.sqrt(np.mean(x.var(axis=1, ddof=1)))
    assert chart.sigma_hat == pytest.approx(sigma)
    assert chart.location.center == pytest.approx(x.mean())
    assert chart.location.ucl - chart.location.center == pytest.approx(3.0 * sigma / np.sqrt(5), rel=1e-6)
    f = 4
    assert chart.variation.ucl == pytest.approx(np.sqrt(chi2.ppf(1 - ALPHA_3SIGMA / 2, f) / f) * sigma)
    assert chart.variation.lcl == pytest.approx(np.sqrt(chi2.ppf(ALPHA_3SIGMA / 2, f) / f) * sigma)
    assert chart.variation.lcl > 0  # s chart limits are asymmetric and the lower limit is positive


def test_xbar_limit_is_narrower_than_individual_limit():
    # Draft: a 2.58 sigma limit for individual values becomes about 1.15 sigma for n = 5.
    rng = np.random.default_rng(1)
    chart = xbar_s(rng.normal(0, 1, size=(200, 5)), alpha=0.01)
    half = chart.location.ucl - chart.location.center
    assert half / chart.sigma_hat == pytest.approx(2.5758 / np.sqrt(5), rel=1e-3)
    assert half / chart.sigma_hat == pytest.approx(1.15, abs=0.01)


def test_xbar_r_uses_rbar_over_d2(normal_subgroups):
    chart = xbar_r(normal_subgroups)
    rbar = np.mean(normal_subgroups.max(axis=1) - normal_subgroups.min(axis=1))
    assert chart.sigma_hat == pytest.approx(rbar / d2(5))
    assert chart.variation.center == pytest.approx(rbar)
    assert chart.variation.lcl > 0
    assert chart.variation.ucl > rbar > chart.variation.lcl


def test_false_alarm_rate_matches_alpha():
    rng = np.random.default_rng(7)
    data = rng.normal(0, 1, size=(60000, 5))
    ref = xbar_s(data[:5000])
    # Use limits from a large reference, then count alarms on fresh data.
    fresh = data[5000:]
    loc = ref.location.alarms(fresh.mean(axis=1)).mean()
    s_alarm = ref.variation.alarms(fresh.std(axis=1, ddof=1)).mean()
    assert loc == pytest.approx(ALPHA_3SIGMA, abs=0.0012)
    assert s_alarm == pytest.approx(ALPHA_3SIGMA, abs=0.0015)


def test_range_chart_false_alarm_rate_matches_alpha():
    rng = np.random.default_rng(11)
    data = rng.normal(0, 1, size=(60000, 5))
    ref = xbar_r(data[:5000])
    fresh = data[5000:]
    ranges = fresh.max(axis=1) - fresh.min(axis=1)
    assert ref.variation.alarms(ranges).mean() == pytest.approx(ALPHA_3SIGMA, abs=0.0015)


def test_imr_estimators():
    x = np.array([10.1, 9.9, 10.2, 10.0, 9.8, 10.3, 10.1, 9.7, 10.0, 10.2])
    chart = imr(x)
    mrbar = np.mean(np.abs(np.diff(x)))
    assert chart.sigma_hat == pytest.approx(mrbar / 1.128, rel=1e-3)
    assert chart.location.ucl == pytest.approx(x.mean() + 3 * mrbar / 1.128, rel=1e-3)
    # The exact limit uses the w distribution: w(2; 0.99865) / d2(2) * MR-bar = 4.02 * MR-bar.
    assert chart.variation.ucl == pytest.approx(w_quantile(2, 1 - ALPHA_3SIGMA / 2) / d2(2) * mrbar)
    assert chart.variation.ucl / mrbar == pytest.approx(4.02, abs=0.01)
    # The classic constant D4(2) = 3.267 = 1 + 3 * d3 / d2 treats the skewed range distribution as normal.
    # It is lower, so it alarms more often than the stated risk. The manual asks for the exact form.
    assert 1 + 3 * d3(2) / d2(2) == pytest.approx(3.267, abs=0.002)
    assert chart.variation.ucl > 3.267 * mrbar


def test_nan_data_is_rejected():
    with pytest.raises(ValueError):
        xbar_s(np.array([[1.0, 2.0], [np.nan, 1.0]]))
    with pytest.raises(ValueError):
        imr(np.array([1.0, np.nan, 2.0]))


def test_shape_errors():
    with pytest.raises(ValueError):
        xbar_s(np.ones((5, 1)))
    with pytest.raises(ValueError):
        xbar_r(np.ones(10))

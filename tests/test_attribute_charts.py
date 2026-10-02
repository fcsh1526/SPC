import numpy as np
import pytest
from scipy.stats import binom, poisson

from spc.core.charts.attribute import c_chart, np_chart, p_chart, u_chart
from spc.core.constants import ALPHA_3SIGMA


def test_np_chart_limits_hold_the_stated_risk():
    x = np.array([3, 5, 2, 4, 6, 3, 4, 5, 2, 4])
    chart = np_chart(x, 100)
    p_bar = x.sum() / 1000
    lcl, ucl = chart.lcl[0], chart.ucl[0]
    assert binom.sf(ucl, 100, p_bar) <= ALPHA_3SIGMA / 2 + 1e-12
    assert binom.sf(ucl - 1, 100, p_bar) > ALPHA_3SIGMA / 2
    if lcl > 0:
        assert binom.cdf(lcl - 1, 100, p_bar) < ALPHA_3SIGMA / 2
        assert binom.cdf(lcl, 100, p_bar) >= ALPHA_3SIGMA / 2


def test_low_defect_rate_has_no_negative_limit_and_no_lower_alarm():
    x = np.array([1, 0, 2, 1, 0, 1, 0, 2, 1, 1])
    chart = np_chart(x, 100)
    assert np.all(chart.lcl >= 0)
    assert not chart.alarms().any()


def test_exact_limits_differ_from_normal_approximation():
    x = np.array([1, 0, 2, 1, 0, 1, 0, 2, 1, 1])
    p_bar = x.sum() / 1000
    normal_lcl = 100 * p_bar - 3 * np.sqrt(100 * p_bar * (1 - p_bar))
    assert normal_lcl < 0  # the approximation gives a negative limit
    assert np_chart(x, 100).lcl[0] == 0  # the exact limit is 0 and says so


def test_p_chart_variable_sample_size_gives_point_by_point_limits():
    x = np.array([4, 5, 3, 6, 4, 5])
    n = np.array([102, 110, 120, 104, 115, 108])
    chart = p_chart(x, n)
    assert chart.ucl.shape == (6,)
    assert len(set(np.round(chart.ucl, 6))) > 1
    for xi, ni, ucl in zip(x, n, chart.ucl):
        p_bar = x.sum() / n.sum()
        assert binom.sf(round(ucl * ni), ni, p_bar) <= ALPHA_3SIGMA / 2 + 1e-12


def test_p_chart_alarm_on_clear_shift():
    x = np.array([4, 5, 3, 6, 4, 5, 25])
    chart = p_chart(x, 100)
    assert chart.alarms()[-1]
    assert not chart.alarms()[:-1].any()


def test_np_chart_rejects_changing_sample_size():
    with pytest.raises(ValueError):
        np_chart([3, 4, 5], [100, 100, 110])


def test_small_sample_warning():
    chart = p_chart([1, 2, 1], 40)
    assert any("50" in w for w in chart.warnings)


def test_c_chart_limits():
    c = np.array([6, 4, 7, 5, 8, 3, 6, 5, 7, 4])
    chart = c_chart(c)
    mu = c.mean()
    assert chart.center == pytest.approx(mu)
    assert poisson.sf(chart.ucl[0], mu) <= ALPHA_3SIGMA / 2 + 1e-12
    assert poisson.sf(chart.ucl[0] - 1, mu) > ALPHA_3SIGMA / 2


def test_u_chart_limits_scale_with_units():
    c = np.array([12, 9, 15, 8, 20, 11])
    units = np.array([2.0, 2.0, 3.0, 1.5, 4.0, 2.0])
    chart = u_chart(c, units)
    u_bar = c.sum() / units.sum()
    assert chart.center == pytest.approx(u_bar)
    # Larger inspection amount gives narrower limits per unit.
    assert (chart.ucl - u_bar)[4] < (chart.ucl - u_bar)[3]


def test_invalid_counts():
    with pytest.raises(ValueError):
        c_chart([1, -2, 3])
    with pytest.raises(ValueError):
        p_chart([5, 2], [3, 10])

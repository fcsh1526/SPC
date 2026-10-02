"""Constants against the AIAG-VDA SPC draft tables (10.3.3.3)."""

import math

import pytest

from spc.core.constants import ALPHA_3SIGMA, c4, d2, d3, u_quantile, w_quantile

# Draft: d2 and d3 for n = 2..10
DRAFT_D2 = {2: 1.128, 3: 1.693, 4: 2.059, 5: 2.326, 6: 2.534, 7: 2.704, 8: 2.847, 9: 2.970, 10: 3.078}
DRAFT_D3 = {2: 0.8525, 3: 0.8884, 4: 0.8798, 5: 0.8641, 6: 0.8480, 7: 0.8332, 8: 0.8198, 9: 0.8078, 10: 0.7971}

# Draft: w quantiles. (alpha = 99 % lower, upper, alpha = 99.73 % lower, upper)
DRAFT_W = {
    2: (0.009, 3.970, 0.002, 4.533),
    3: (0.135, 4.424, 0.070, 4.950),
    4: (0.343, 4.694, 0.221, 5.200),
    5: (0.555, 4.886, 0.397, 5.378),
    6: (0.749, 5.033, 0.569, 5.515),
    7: (0.922, 5.154, 0.729, 5.627),
    8: (1.075, 5.255, 0.874, 5.722),
    9: (1.212, 5.341, 1.006, 5.803),
    10: (1.335, 5.418, 1.126, 5.875),
}


@pytest.mark.parametrize("n", sorted(DRAFT_D2))
def test_d2_matches_draft(n):
    assert d2(n) == pytest.approx(DRAFT_D2[n], abs=6e-4)


@pytest.mark.parametrize("n", sorted(DRAFT_D3))
def test_d3_matches_draft(n):
    assert d3(n) == pytest.approx(DRAFT_D3[n], abs=6e-4)


@pytest.mark.parametrize("n", sorted(DRAFT_W))
def test_w_quantiles_match_draft(n):
    lo99, hi99, lo73, hi73 = DRAFT_W[n]
    assert w_quantile(n, 0.005) == pytest.approx(lo99, abs=1.5e-3)
    assert w_quantile(n, 0.995) == pytest.approx(hi99, abs=1.5e-3)
    assert w_quantile(n, 0.00135) == pytest.approx(lo73, abs=1.5e-3)
    assert w_quantile(n, 0.99865) == pytest.approx(hi73, abs=1.5e-3)


def test_c4_known_values():
    assert c4(2) == pytest.approx(math.sqrt(2 / math.pi), rel=1e-12)
    assert c4(5) == pytest.approx(0.9400, abs=1e-4)
    assert c4(10) == pytest.approx(0.9727, abs=1e-4)


def test_three_sigma_alpha():
    assert ALPHA_3SIGMA == pytest.approx(0.0027, abs=1e-5)
    assert u_quantile(ALPHA_3SIGMA) == pytest.approx(3.0, abs=1e-9)


def test_invalid_subgroup_size_is_rejected():
    with pytest.raises(ValueError):
        d2(1)
    with pytest.raises(ValueError):
        w_quantile(5, 1.0)

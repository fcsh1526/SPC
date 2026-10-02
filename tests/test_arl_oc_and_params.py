import pytest

from spc.core.arl_oc import alarm_probability, arl, required_subgroup_size
from spc.core.constants import ALPHA_3SIGMA
from spc.core.rules import RuleSet
from spc.params import AnalysisParams

# Values from the second-hand article on chapter 9 (3-sigma limits). They follow ISO 7870 relations,
# and this code reproduces them.
ARL_TABLE = {
    0.4: {2: 130.9, 5: 56.6, 10: 24.2},
    0.6: {2: 63.4, 5: 20.6, 10: 7.4},
    1.0: {2: 17.7, 5: 4.5, 10: 1.8},
    2.0: {2: 2.3, 5: 1.1, 10: 1.0},
}


def test_arl0_of_a_three_sigma_chart():
    assert arl(0.0, 5) == pytest.approx(370.4, abs=0.1)


@pytest.mark.parametrize("shift", sorted(ARL_TABLE))
@pytest.mark.parametrize("n", [2, 5, 10])
def test_arl_table(shift, n):
    assert arl(shift, n) == pytest.approx(ARL_TABLE[shift][n], abs=0.06)


def test_oc_values_for_a_one_sigma_shift():
    assert alarm_probability(1.0, 2) == pytest.approx(0.056, abs=0.001)
    assert alarm_probability(1.0, 5) == pytest.approx(0.222, abs=0.001)
    assert alarm_probability(1.0, 10) == pytest.approx(0.564, abs=0.001)


def test_individual_value_chart_arl_for_one_sigma():
    assert arl(1.0, 1) == pytest.approx(43.9, abs=0.1)


def test_wider_limits_raise_arl():
    assert arl(1.0, 5, alpha=0.01) < arl(1.0, 5, alpha=0.0027)


def test_required_subgroup_size():
    n = required_subgroup_size(0.75, 10)
    assert arl(0.75, n) <= 10 < arl(0.75, n - 1)
    with pytest.raises(ValueError):
        required_subgroup_size(0.01, 1.5, n_max=20)


def test_params_are_explicit_and_hashable_for_archiving():
    a = AnalysisParams()
    b = AnalysisParams()
    assert a.fingerprint() == b.fingerprint()
    assert a.to_dict()["alpha"] == ALPHA_3SIGMA
    assert a.to_dict()["edition"] == "draft"
    c = AnalysisParams(customer="A", rules=RuleSet(run_length=7))
    assert c.fingerprint() != a.fingerprint()
    assert c.to_dict()["rules"]["run_length"] == 7

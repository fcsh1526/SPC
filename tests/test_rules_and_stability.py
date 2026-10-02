import numpy as np
import pytest

from spc.core.rules import RuleSet, evaluate
from spc.core.stability import Stability, assess_analysis_chart, classify_stability


def test_default_rule_set_checks_limits_only():
    v = np.array([0, 0, 0, 5.0, 0, 0])
    result = evaluate(v, 0.0, -3.0, 3.0)
    assert result.indices == (3,)
    assert result.by_rule("beyond_limits") == (3,)


def test_run_rule_flags_the_completing_point():
    v = np.array([-1, 1, 1, 1, 1, 1, 1, 1, -1.0])
    result = evaluate(v, 0.0, -9, 9, RuleSet(beyond_limits=False, run_length=7))
    assert result.indices == (7,)


def test_run_resets_on_a_point_on_the_center_line():
    v = np.array([1, 1, 1, 0, 1, 1, 1.0])
    result = evaluate(v, 0.0, -9, 9, RuleSet(beyond_limits=False, run_length=4))
    assert result.indices == ()


def test_trend_rule():
    v = np.array([1, 2, 3, 4, 5, 6, 7, 3.0])
    result = evaluate(v, 0.0, -99, 99, RuleSet(beyond_limits=False, trend_length=7))
    assert result.indices == (6,)
    flat = np.array([1, 2, 3, 3, 4, 5, 6.0])
    assert evaluate(flat, 0.0, -99, 99, RuleSet(beyond_limits=False, trend_length=5)).indices == ()


def test_middle_third_rule_flags_too_few_points_in_the_middle():
    # 25 points all in the outer thirds, none beyond limits
    v = np.tile([2.0, -2.0], 13)[:25]
    result = evaluate(v, 0.0, -3.0, 3.0, RuleSet(beyond_limits=False, middle_third=True))
    assert result.by_rule("middle_third") == (24,)


def test_middle_third_rule_quiet_for_random_normal_data():
    rng = np.random.default_rng(3)
    v = rng.normal(0, 1, 25)
    result = evaluate(v, 0.0, -3.0, 3.0, RuleSet(beyond_limits=False, middle_third=True))
    assert result.indices == ()


def test_sigma_rules_need_sigma():
    with pytest.raises(ValueError):
        evaluate([0.0] * 5, 0.0, -3, 3, RuleSet(two_of_three_beyond_2s=True))


def test_two_of_three_beyond_2_sigma():
    v = np.array([0, 2.5, 0.5, 2.2, 0.0])
    result = evaluate(v, 0.0, -3, 3, RuleSet(beyond_limits=False, two_of_three_beyond_2s=True), sigma=1.0)
    assert result.indices == (3,)


def test_four_of_five_beyond_1_sigma_and_fifteen_within_1_sigma():
    v = np.array([1.5, 1.2, 0.2, 1.4, 1.6])
    res = evaluate(v, 0.0, -3, 3, RuleSet(beyond_limits=False, four_of_five_beyond_1s=True), sigma=1.0)
    assert res.indices == (4,)
    calm = np.full(15, 0.2)
    res = evaluate(calm, 0.0, -3, 3, RuleSet(beyond_limits=False, fifteen_within_1s=True), sigma=1.0)
    assert res.indices == (14,)


def test_per_point_limits():
    v = np.array([1.0, 1.0, 1.0])
    result = evaluate(v, 0.0, np.array([-3, -3, -3.0]), np.array([3, 0.5, 3.0]))
    assert result.indices == (1,)


def test_strict_stability_mode():
    assert assess_analysis_chart(0, 25, 0.0027, mode="strict").stable
    assert not assess_analysis_chart(1, 25, 0.0027, mode="strict").stable


def test_default_mode_follows_the_draft_and_tolerates_chance_alarms():
    # Draft 10.3.2.3: an analysis chart must account for the expected false alarms.
    assert assess_analysis_chart(1, 250, 0.0027).stable  # expected 0.675 false alarms
    assert assess_analysis_chart(1, 250, 0.0027).mode == "random_range"
    assert not assess_analysis_chart(6, 250, 0.0027).stable


def test_a_stable_process_is_rarely_called_unstable_in_the_default_mode():
    rng = np.random.default_rng(99)
    k, runs, flagged = 250, 4000, 0
    threshold = assess_analysis_chart(0, k, 0.0027).threshold
    for _ in range(runs):
        alarms = int(rng.binomial(k, 0.0027))
        flagged += alarms > threshold
    assert flagged / runs < 0.02  # confidence 0.99 means about 1 % false "unstable"


def test_random_range_mode_tolerates_chance_alarms():
    one = assess_analysis_chart(1, 25, 0.0027, mode="random_range", confidence=0.99)
    assert one.stable
    assert one.expected_false_alarms == pytest.approx(0.0675)
    many = assess_analysis_chart(5, 25, 0.0027, mode="random_range", confidence=0.99)
    assert not many.stable


def test_classify_stability():
    assert classify_stability(None) is Stability.UNKNOWN
    assert classify_stability(False, "A1") is Stability.UNSTABLE
    assert classify_stability(True, "A1") is Stability.STATISTICAL_CONTROL
    assert classify_stability(True, "A2") is Stability.STATISTICAL_CONTROL
    # Models B..D are not in statistical control. Only controlled-stable evidence gives "in control".
    assert classify_stability(True, "C4") is Stability.UNSTABLE
    assert classify_stability(True, "C4", controlled_stable=True) is Stability.IN_CONTROL
    assert classify_stability(True, None) is Stability.UNKNOWN
    with pytest.raises(ValueError):
        classify_stability(True, "Z9")

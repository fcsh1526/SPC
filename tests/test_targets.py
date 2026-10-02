"""Sample-size adjustment of targets against the draft's Table 8-1 and Table 9-3."""

import pytest

from spc.core.capability import Stage, TargetAdjustmentNotAllowed, adjusted_target, required_targets

# Draft Table 8-1: Pm / Pmk for n = 45, 40, 35, 30 (base n = 50, confidence 99.99 %)
TABLE_8_1 = {
    "major": {45: (2.06, 1.72), 40: (2.13, 1.78), 35: (2.22, 1.86), 30: (2.35, 1.96)},
    "minor": {45: (1.72, 1.37), 40: (1.78, 1.42), 35: (1.86, 1.48), 30: (1.96, 1.56)},
    "others": {45: (1.03, 1.03), 40: (1.07, 1.07), 35: (1.11, 1.11), 30: (1.18, 1.18)},
}

# Draft Table 9-3: preliminary Pp / Ppk for n = 100, 75 (base N = 125)
TABLE_9_3 = {
    "critical": {100: (2.07, 1.73), 75: (2.18, 1.82)},
    "major": {100: (1.73, 1.38), 75: (1.82, 1.45)},
    "minor": {100: (1.38, 1.04), 75: (1.45, 1.09)},
    "others": {100: (1.38, 1.04), 75: (1.45, 1.09)},
}


@pytest.mark.parametrize("cls", sorted(TABLE_8_1))
@pytest.mark.parametrize("n", [45, 40, 35, 30])
def test_machine_targets_match_table_8_1(cls, n):
    expected_p, expected_pk = TABLE_8_1[cls][n]
    t = required_targets(Stage.MACHINE, cls, n)
    assert t.adjusted
    assert t.p == pytest.approx(expected_p, abs=0.006)
    assert t.pk == pytest.approx(expected_pk, abs=0.006)


@pytest.mark.parametrize("cls", sorted(TABLE_9_3))
@pytest.mark.parametrize("n", [100, 75])
def test_preliminary_targets_match_table_9_3(cls, n):
    expected_p, expected_pk = TABLE_9_3[cls][n]
    t = required_targets(Stage.PRELIMINARY, cls, n)
    assert t.p == pytest.approx(expected_p, abs=0.006)
    assert t.pk == pytest.approx(expected_pk, abs=0.006)


def test_full_sample_needs_no_adjustment():
    t = required_targets(Stage.PRODUCTION, "critical", 125)
    assert not t.adjusted
    assert (t.p, t.pk) == (1.67, 1.67)
    assert adjusted_target(1.67, 125, 500) == 1.67


def test_critical_machine_study_cannot_use_a_reduced_sample():
    assert required_targets(Stage.MACHINE, "critical", 50).pk == 2.00
    with pytest.raises(TargetAdjustmentNotAllowed):
        required_targets(Stage.MACHINE, "critical", 45)


def test_article_examples():
    # Secondary article: Cpk target 1.33 at N = 75 and 99.99 % becomes about 1.45.
    assert adjusted_target(1.33, 125, 75) == pytest.approx(1.45, abs=0.005)
    # Secondary article: Pm target 1.67 at n = 20 becomes about 2.36.
    assert adjusted_target(1.67, 50, 20) == pytest.approx(2.36, abs=0.005)


def test_final_edition_adds_bias_term_to_location_indices_only():
    # Secondary article for the July 2026 release: Pmk 1.67 at n = 20 becomes about 2.39.
    draft = adjusted_target(1.67, 50, 20, location_index=True, edition="draft")
    final = adjusted_target(1.67, 50, 20, location_index=True, edition="final")
    assert draft == pytest.approx(2.357, abs=0.005)
    assert final == pytest.approx(2.39, abs=0.005)
    # The potential index has no bias term in either edition.
    assert adjusted_target(1.67, 50, 20, location_index=False, edition="final") == adjusted_target(
        1.67, 50, 20, location_index=False, edition="draft"
    )


def test_lower_confidence_gives_smaller_adjustment():
    high = adjusted_target(1.33, 125, 75, 0.9999)
    low = adjusted_target(1.33, 125, 75, 0.95)
    assert 1.33 < low < high


def test_customer_table_replaces_defaults():
    table = {Stage.PRODUCTION: {"critical": (2.0, 1.8), "major": (1.5, 1.4), "minor": (1.2, 1.1), "others": (1.0, 1.0)}}
    t = required_targets(Stage.PRODUCTION, "critical", 125, table=table)
    assert (t.p, t.pk) == (2.0, 1.8)


def test_input_validation():
    with pytest.raises(ValueError):
        required_targets(Stage.PRODUCTION, "unknown", 125)
    with pytest.raises(ValueError):
        adjusted_target(1.33, 125, 75, edition="beta")
    with pytest.raises(ValueError):
        adjusted_target(1.33, 125, 75, confidence=1.0)

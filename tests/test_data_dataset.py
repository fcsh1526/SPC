import numpy as np
import pytest

from spc.core.charts.variable import xbar_s
from spc.data import Dataset, IncompleteSubgroupsError


def make(k=6, n=5, seed=1):
    rng = np.random.default_rng(seed)
    values = rng.normal(10, 0.2, k * n)
    labels = np.repeat([f"S{i + 1}" for i in range(k)], n)
    return Dataset.from_values(values, subgroup=labels)


def test_marking_returns_a_new_dataset_and_keeps_the_value():
    d = make()
    d2 = d.mark_invalid([3], "calibration part measured by mistake", "A. Chen")
    assert d.n_invalid == 0  # the original is untouched
    assert d2.n_invalid == 1 and d2.n_valid == d.n_total - 1
    assert d2.n_total == d.n_total  # nothing is deleted
    assert d2.values[3] == d.values[3]
    reason, by, at = d2.invalid_info()[3]
    assert reason.startswith("calibration") and by == "A. Chen" and at.endswith("+00:00")


def test_marking_needs_reason_and_person():
    d = make()
    with pytest.raises(ValueError):
        d.mark_invalid([0], "", "A")
    with pytest.raises(ValueError):
        d.mark_invalid([0], "   ", "A")
    with pytest.raises(ValueError):
        d.mark_invalid([0], "wrong part", "")


def test_position_checks():
    d = make()
    with pytest.raises(ValueError):
        d.mark_invalid([999], "r", "a")
    with pytest.raises(ValueError):
        d.mark_invalid([1, 1], "r", "a")
    with pytest.raises(ValueError):
        d.mark_invalid([], "r", "a")
    marked = d.mark_invalid([1], "r", "a")
    with pytest.raises(ValueError):
        marked.mark_invalid([1], "other reason", "b")  # no silent overwrite


def test_restore_keeps_the_history():
    d = make().mark_invalid([2], "mix-up", "A", at="2026-10-02T08:00:00+00:00")
    r = d.restore([2], "mix-up was a labelling error, part is fine", "B", at="2026-10-02T09:00:00+00:00")
    assert r.n_invalid == 0
    assert [e.action for e in r.log] == ["mark_invalid", "restore"]
    with pytest.raises(ValueError):
        r.restore([2], "again", "B")


def test_values_marked_invalid_are_excluded_from_calculations():
    d = make()
    d2 = d.mark_invalid([7], "sensor glitch", "A")
    vals, pos = d2.individuals()
    assert 7 not in pos and vals.size == d.n_total - 1


def test_subgroups_matrix_and_traceability():
    d = make()
    sg = d.subgroups()
    assert sg.matrix.shape == (6, 5) and sg.nominal_size == 5
    assert sg.labels[0] == "S1"
    assert np.array_equal(d.values[sg.positions], sg.matrix)


def test_an_invalid_value_makes_its_subgroup_incomplete():
    d = make().mark_invalid([7], "sensor glitch", "A")  # position 7 is in subgroup S2
    with pytest.raises(IncompleteSubgroupsError) as err:
        d.subgroups()
    assert err.value.details[0].label == "S2" and err.value.details[0].valid_count == 4
    sg = d.subgroups(incomplete="drop")
    assert sg.matrix.shape == (5, 5)
    assert "S2" not in sg.labels
    assert sg.dropped[0].label == "S2" and "invalid" in sg.dropped[0].reason


def test_chart_ignores_marked_values():
    d = make(k=25)
    spike = d.values.copy()
    spike[12] += 5.0  # a wrong measurement
    dirty = Dataset.from_values(spike, subgroup=d.subgroup)
    clean_chart = xbar_s(d.subgroups().matrix)
    dirty_chart = xbar_s(dirty.subgroups().matrix)
    assert dirty_chart.sigma_hat > clean_chart.sigma_hat * 1.5  # the spike inflates sigma
    cleaned = dirty.mark_invalid([12], "value written into the wrong cell", "A")
    after = xbar_s(cleaned.subgroups(incomplete="drop").matrix)
    # With the spiked subgroup dropped, the chart matches a chart built without that subgroup.
    expected = xbar_s(np.delete(d.subgroups().matrix, 2, axis=0))
    assert after.sigma_hat == pytest.approx(expected.sigma_hat)
    assert after.k == 24


def test_fixed_size_subgroups_without_labels():
    d = Dataset.from_values(np.arange(20.0))
    sg = d.subgroups(size=5)
    assert sg.matrix.shape == (4, 5)
    assert sg.matrix[1, 0] == 5.0
    with pytest.raises(ValueError):
        d.subgroups()  # no labels and no size
    with pytest.raises(ValueError):
        make().subgroups(size=5)  # labels exist already


def test_trailing_short_group_is_incomplete():
    d = Dataset.from_values(np.arange(23.0))
    with pytest.raises(IncompleteSubgroupsError):
        d.subgroups(size=5)
    sg = d.subgroups(size=5, incomplete="drop")
    assert sg.matrix.shape == (4, 5)
    assert "shorter" in sg.dropped[0].reason


def test_summary_counts():
    d = make().mark_invalid([0, 1], "r", "a")
    s = d.summary()
    assert (s["n_total"], s["n_effective"], s["n_invalid"], s["k_subgroups"]) == (30, 28, 2, 6)


def test_source_row_lookup():
    d = make()
    assert d.positions_for_source_rows([1, 5]) == (0, 4)
    with pytest.raises(ValueError):
        d.positions_for_source_rows([1000])


def test_construction_checks():
    with pytest.raises(ValueError):
        Dataset.from_values([1.0, float("nan")])
    with pytest.raises(ValueError):
        Dataset.from_values([1.0, 2.0], subgroup=["a"])
    with pytest.raises(ValueError):
        Dataset.from_values([])

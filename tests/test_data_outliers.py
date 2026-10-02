import numpy as np
import pytest

from spc.data import Dataset, suspects


@pytest.fixture
def clean():
    return np.random.default_rng(42).normal(10, 0.1, 100)


@pytest.mark.parametrize("method", ["mad", "tukey", "grubbs"])
def test_planted_outlier_is_found(clean, method):
    x = clean.copy()
    x[37] = 12.0
    found = suspects(x, method)
    assert [s.position for s in found] == [37]
    assert found[0].value == 12.0


@pytest.mark.parametrize("method", ["mad", "tukey", "grubbs"])
def test_clean_normal_data_gives_no_hint(clean, method):
    assert suspects(clean, method) == []


def test_hints_never_change_the_dataset(clean):
    x = clean.copy()
    x[5] = -3.0
    d = Dataset.from_values(x)
    found = suspects(d, "mad")
    assert [s.position for s in found] == [5]
    assert d.n_invalid == 0  # a hint is not a mark


def test_already_invalid_values_are_not_examined_again(clean):
    x = clean.copy()
    x[5] = -3.0
    d = Dataset.from_values(x).mark_invalid([5], "wrong part", "A")
    assert suspects(d, "mad") == []


def test_positions_refer_to_the_dataset_not_to_the_valid_values(clean):
    x = clean.copy()
    x[50] = 14.0
    d = Dataset.from_values(x).mark_invalid([0, 1, 2], "setup parts", "A")
    assert [s.position for s in suspects(d, "grubbs")] == [50]


def test_spec_limits_are_not_used(clean):
    # A value far from the others is a hint even if it would be inside a wide tolerance.
    x = clean.copy()
    x[10] = 10.9
    assert [s.position for s in suspects(x, "mad")] == [10]


def test_degenerate_scale_is_an_error():
    x = np.array([1.0] * 10 + [2.0])
    with pytest.raises(ValueError):
        suspects(x, "mad")
    with pytest.raises(ValueError):
        suspects(np.ones(10), "grubbs")


def test_thresholds_and_method_names():
    x = np.random.default_rng(1).normal(0, 1, 60)
    strict = suspects(x, "mad", threshold=1.0)
    assert len(strict) > len(suspects(x, "mad"))
    with pytest.raises(ValueError):
        suspects(x, "magic")
    with pytest.raises(ValueError):
        suspects([1.0, 2.0], "mad")

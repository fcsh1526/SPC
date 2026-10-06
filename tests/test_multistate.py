import numpy as np
import pytest
from scipy import stats

from spc.core import multistate as ms
from spc.data import Dataset
from spc.service.state_tests import state_tests_for_dataset
from tests.conftest import logged_in_client, make_app
from tests.test_api import err


def groups(seed=1):
    r = np.random.default_rng(seed)
    return [r.normal(0, 1, 20), r.normal(0.5, 2, 25), r.normal(0, 1, 15)]


def test_bartlett_and_fisher_equal_scipy():
    for seed in range(5):
        g = groups(seed)
        b, f = ms.bartlett(g), ms.fisher(g)
        ref_b, ref_f = stats.bartlett(*g), stats.f_oneway(*g)
        assert b["statistic"] == pytest.approx(ref_b.statistic, rel=1e-12) and b["p"] == pytest.approx(ref_b.pvalue, rel=1e-9)
        assert f["statistic"] == pytest.approx(ref_f.statistic, rel=1e-12) and f["p"] == pytest.approx(ref_f.pvalue, rel=1e-9)
        assert b["df"] == 2 and f["df1"] == 2 and f["df2"] == 57


def test_the_critical_value_of_grubbs_is_the_published_one():
    # NIST/SEMATECH e-Handbook table, two-sided, alpha = 0.05
    for n, value in ((10, 2.2900), (25, 2.8217)):
        assert ms.grubbs_critical(n) == pytest.approx(value, abs=1e-4)
    assert ms.grubbs_critical(10, 0.01) > ms.grubbs_critical(10, 0.05)


def test_grubbs_finds_the_most_extreme_value_only_when_it_is_too_far():
    x = list(np.random.default_rng(3).normal(10, 0.2, 30))
    assert ms.grubbs(x)["outlier"] is False
    x[11] = 12.0
    g = ms.grubbs(x)
    assert g["outlier"] and g["index"] == 11 and g["value"] == 12.0 and g["g"] > g["g_crit"]
    assert ms.grubbs([1, 1, 1, 1])["g"] is None  # no variation: nothing to test


def test_the_tests_see_what_was_built_into_the_states():
    r = np.random.default_rng(8)
    same = {"a": r.normal(10, 0.3, 40), "b": r.normal(10, 0.3, 40), "c": r.normal(10, 0.3, 40)}
    t = ms.state_tests(same)
    assert not t["variance_differs"] and not t["location_differs"] and t["outliers"] == []
    diff = {"a": r.normal(10, 0.3, 40), "b": r.normal(11, 1.0, 40), "c": r.normal(10, 0.3, 40), "tiny": [1.0, 2.0]}
    t = ms.state_tests(diff)
    assert t["variance_differs"] and t["location_differs"] and t["skipped"] == ["tiny"]


@pytest.mark.parametrize("bad", [{"a": [1, 2, 3]}, {"a": [1, 2, 3], "b": [1, 1, 1]}, {"a": [1, 2], "b": [3, 4]}])
def test_too_little_or_degenerate_data_is_refused(bad):
    with pytest.raises(ValueError):
        ms.state_tests(bad)


def test_alpha_is_checked():
    with pytest.raises(ValueError):
        ms.state_tests({"a": [1, 2, 4], "b": [2, 3, 7]}, alpha=0.7)


def test_the_states_come_from_subgroup_labels_or_a_tag_and_marked_values_do_not_count():
    r = np.random.default_rng(5)
    v = np.concatenate([r.normal(10, 0.2, 30), r.normal(10.5, 0.2, 30)])
    labels = ["m1"] * 30 + ["m2"] * 30
    ds = Dataset.from_values(v, subgroup=labels)
    out = state_tests_for_dataset(ds)
    assert set(out["states"]) == {"m1", "m2"} and out["by"] == "subgroup" and out["location_differs"]
    ds2 = Dataset.from_values(v, tags={"machine": labels}).mark_invalid([0, 1], "test", "me")
    out2 = state_tests_for_dataset(ds2, by="machine")
    assert out2["states"]["m1"]["n"] == 28 and out2["by"] == "machine"
    with pytest.raises(ValueError):
        state_tests_for_dataset(Dataset.from_values(v))  # no labels and no tag
    with pytest.raises(ValueError):
        state_tests_for_dataset(ds2, by="nothing")


def test_the_api_runs_the_tests_on_a_stored_data_set():
    app = make_app()
    c = logged_in_client(app, "eng")
    r = np.random.default_rng(6)
    v = np.concatenate([r.normal(10, 0.2, 30), r.normal(10, 0.6, 30)])
    key = app.state.store.add(Dataset.from_values(v, subgroup=["a"] * 30 + ["b"] * 30), 1, "two states")
    res = c.post(f"/api/datasets/{key}/state-tests", json={})
    assert res.status_code == 200 and res.json()["variance_differs"] is True and res.json()["bartlett"]["df"] == 1
    assert c.post(f"/api/datasets/{key}/state-tests", json={"alpha": 0.9}).status_code in (400, 422)
    plain = app.state.store.add(Dataset.from_values(v), 1, "no states")
    assert err(c.post(f"/api/datasets/{plain}/state-tests", json={}))["code"] == "invalid_input"

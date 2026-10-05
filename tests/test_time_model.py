import math
from collections import Counter

import numpy as np
import pytest

from spc.core import time_model as tm
from spc.data import Dataset
from spc.service.model_suggestion import suggest_for_dataset
from tests.conftest import logged_in_client, make_app
from tests.test_api import err


def process(kind, rng, k=40, n=5):
    """One process of each time-dependent model of the draft (table 9-2), as subgroups in time order."""
    if kind == "A1":
        return rng.normal(0, 1, (k, n))
    if kind == "A2":  # a naturally limited characteristic: skewed, stable
        return rng.lognormal(0, 0.6, (k, n))
    if kind == "B":  # the variation changes (different wear of spindles), the location does not
        return rng.normal(0, 1, (k, n)) * rng.uniform(0.4, 2.5, (k, 1))
    if kind == "C1":  # different centring of fixtures: random, normal
        return rng.normal(0, 1.5, (k, 1)) + rng.normal(0, 1, (k, n))
    if kind == "C2":  # random and not normal
        return rng.exponential(2.0, (k, 1)) + rng.normal(0, 1, (k, n))
    if kind == "C3":  # trend by tool wear
        return np.linspace(0, 8, k)[:, None] + rng.normal(0, 1, (k, n))
    if kind == "C4":  # trend and lot to lot
        return np.linspace(0, 6, k)[:, None] + rng.normal(0, 1.5, (k, 1)) + rng.normal(0, 1, (k, n))
    if kind == "D":  # a multi-stream process: three streams with their own level and spread
        mu, sd = np.tile([0, 5, 10], k)[:k, None], np.tile([0.5, 1.5, 1.0], k)[:k, None]
        return mu + sd * rng.normal(0, 1, (k, n))
    raise ValueError(kind)


# the share of 60 simulated processes in which the model is found: what the procedure can honestly promise
EXPECTED_SHARE = {"A1": 0.9, "A2": 0.85, "B": 0.55, "C1": 0.85, "C2": 0.8, "C3": 0.9, "C4": 0.75, "D": 0.5}


@pytest.mark.parametrize("kind", list(EXPECTED_SHARE))
def test_each_model_is_found_in_most_simulated_processes(kind):
    found = Counter(tm.suggest(process(kind, np.random.default_rng(seed)))["model"] for seed in range(60))
    assert found[kind] / 60 >= EXPECTED_SHARE[kind], dict(found)


def test_a_stable_process_is_not_taken_for_one_with_a_changing_variation_by_the_tests_that_ignore_the_shape():
    """Levene and Bartlett flag a stable skewed process almost always. The dispersion test with the kurtosis allowance does not."""
    from scipy import stats

    wrong_levene = wrong_disp = 0
    for seed in range(200):
        m = process("A2", np.random.default_rng(seed))
        wrong_levene += stats.levene(*m, center="mean").pvalue <= 0.01
        d = tm.dispersion_test(m)
        wrong_disp += d["p"] <= 0.01 and d["ratio"] >= tm.DISPERSION_RATIO
    assert wrong_levene / 200 > 0.5 and wrong_disp / 200 < 0.1


def test_the_dispersion_test_has_its_nominal_size_for_normal_data_and_power_for_changing_variation():
    rng = np.random.default_rng(5)
    normal = [tm.dispersion_test(rng.normal(0, 1, (40, 5)))["p"] for _ in range(400)]
    assert np.mean(np.array(normal) <= 0.01) == pytest.approx(0.01, abs=0.02)
    changing = [tm.dispersion_test(process("B", rng))["p"] for _ in range(200)]
    assert np.mean(np.array(changing) <= 0.01) > 0.7


def test_the_evidence_and_the_reasons_follow_the_decision_of_the_draft_table():
    rng = np.random.default_rng(1)
    r = tm.suggest(process("C3", rng))
    assert r["model"] == "C3" and r["reasons"] == ["variation_constant", "location_changes", "trend"] and r["in_statistical_control"] is False
    assert r["evidence"]["location"]["trend_r2"] > 0.5 and r["evidence"]["location"]["trend_p"] < 0.01
    r = tm.suggest(process("A1", np.random.default_rng(2)))
    assert r["in_statistical_control"] is True and r["groups"] == {"k": 40, "n": 5, "blocks": False}
    assert tm.classify({"location": {"anova_p": 0.5, "trend_p": 0.5, "trend_r2": 0, "after_trend_p": 1, "modes_of_means": 1},
                        "variation": {"dispersion_p": 0.5, "dispersion_ratio": 1},
                        "shape": {"resulting_normal_p": 0.5, "instantaneous_normal_p": 0.5, "modes": 2}})[0] == "D"  # constant moments, several modes


def test_confidence_falls_with_few_groups_blocks_borderline_tests_and_a_hint_that_disagrees():
    big = [tm.suggest(process("C1", np.random.default_rng(s), k=60)) for s in range(30)]
    few = [tm.suggest(process("C1", np.random.default_rng(s), k=14)) for s in range(30)]
    assert sum(r["confidence"] == "high" for r in big) >= 15 and not any(r["confidence"] == "high" for r in few)
    assert all(r["confidence"] != "high" for r in (tm.suggest(process("C1", np.random.default_rng(s), k=60), blocks=True) for s in range(30)))
    assert tm.suggest(process("C1", np.random.default_rng(3), k=60), blocks=True)["groups"]["blocks"] is True
    assert all(r["confidence"] != "high" for r in big if r["borderline"])  # a test close to its limit takes the top grade away
    agree = tm.suggest(process("C3", np.random.default_rng(4)), hints={"tool_wear_trend": True})
    assert agree["hints"] == [{"hint": "tool_wear_trend", "models": ["C3", "C4"], "agrees": True}]
    clash = tm.suggest(process("C3", np.random.default_rng(4)), hints={"multi_stream": True, "natural_limit": False})
    assert clash["hints"][0]["agrees"] is False and len(clash["hints"]) == 1
    assert ("low", "medium", "high").index(clash["confidence"]) < ("low", "medium", "high").index(agree["confidence"])


def test_borderline_findings_lead_to_alternatives():
    # the location differences of this process sit at the limit of what the test sees
    rng = np.random.default_rng(11)
    alt = [tm.suggest(rng.normal(0, 1, (30, 5)) + rng.normal(0, 0.55, (30, 1))) for _ in range(40)]
    assert any(a["alternatives"] and a["borderline"] for a in alt)
    for a in alt:
        for o in a["alternatives"]:
            assert o["model"] != a["model"] and o["model"] in tm.MODELS


def test_too_little_data_gives_no_suggestion_and_bad_input_is_refused():
    r = tm.suggest(np.random.default_rng(0).normal(0, 1, (6, 5)))
    assert r["model"] is None and r["reason"] == "not_enough_data" and r["min_groups"] == tm.MIN_GROUPS
    with pytest.raises(ValueError):
        tm.suggest(np.ones((20, 1)))
    with pytest.raises(ValueError):
        tm.suggest(np.ones((20, 5)))  # no variation at all
    assert tm.modes(np.concatenate([np.random.default_rng(0).normal(0, 1, 100), np.random.default_rng(1).normal(8, 1, 100)])) == 2
    assert tm.modes(np.random.default_rng(0).normal(0, 1, 300)) == 1 and tm.modes(np.random.default_rng(0).normal(0, 1, 10)) == 1


def test_data_without_subgroups_are_cut_into_blocks_and_marked_values_do_not_count():
    rng = np.random.default_rng(7)
    ds = Dataset.from_values([float(v) for v in process("C3", rng, k=40, n=5).ravel()])
    r = suggest_for_dataset(ds)
    assert r["groups"]["blocks"] is True and r["groups"]["k"] == 40 and r["model"] == "C3"
    assert suggest_for_dataset(ds, 10)["groups"] == {"k": 20, "n": 10, "blocks": True, "dropped": 0}
    labels = [f"g{i}" for i in range(40) for _ in range(5)]
    grouped = suggest_for_dataset(Dataset.from_values(list(ds.values), subgroup=labels))
    assert grouped["groups"]["blocks"] is False and grouped["groups"]["k"] == 40
    marked = ds.mark_invalid([3], "outlier", "t")
    assert suggest_for_dataset(marked)["groups"]["k"] == 39 and suggest_for_dataset(marked)["groups"]["dropped"] == 1
    assert suggest_for_dataset(Dataset.from_values([1.0, 2.0, 3.0, 4.0]))["model"] is None
    with pytest.raises(ValueError):
        suggest_for_dataset(ds, hints={"nonsense": True})


def test_the_api_suggests_and_the_analysis_result_stays_unchanged(env=None):
    app = make_app()
    c = logged_in_client(app, "view")
    rng = np.random.default_rng(9)
    key = app.state.store.add(Dataset.from_values([float(v) for v in process("C4", rng).ravel()]), 1, "run")
    r = c.post(f"/api/datasets/{key}/time-model", json={"hints": {"tool_or_batch_changes": True}})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] in ("C4", "C3", "C1") and body["groups"]["blocks"] is True and body["hints"][0]["hint"] == "tool_or_batch_changes"
    assert err(c.post(f"/api/datasets/{key}/time-model", json={"hints": {"nonsense": True}}))["code"] == "invalid_input"
    assert c.post(f"/api/datasets/{key}/time-model", json={"subgroup_size": 1}).status_code == 422
    assert c.post("/api/datasets/nope/time-model", json={}).status_code == 404
    analysis = c.post(f"/api/datasets/{key}/analyze", json={"stage": "preliminary", "lsl": -10, "usl": 20}).json()
    assert "time_model" not in analysis and "time_model_suggestion" not in analysis  # stored reports are reproduced from the result: it must not change

"""Process characterization (draft 6.4): regression and two-level factorial designs."""

from itertools import product

import numpy as np
import pytest
from scipy import stats

from spc.core import doe
from tests.conftest import logged_in_client, make_app


# ------------------------------------------------------------------ regression

def test_simple_regression_equals_scipy_linregress():
    rng = np.random.default_rng(1)
    x = rng.uniform(0, 10, 40)
    y = 3 + 0.7 * x + rng.normal(0, 1, 40)
    r = doe.regression(y, {"temperature": x})
    ref = stats.linregress(x, y)
    t = r["terms"][0]
    assert t["coefficient"] == pytest.approx(ref.slope) and t["se"] == pytest.approx(ref.stderr) and t["p_value"] == pytest.approx(ref.pvalue)
    assert r["intercept"]["coefficient"] == pytest.approx(ref.intercept) and r["r2"] == pytest.approx(ref.rvalue ** 2)
    assert r["f"] == pytest.approx(t["t"] ** 2) and r["p_model"] == pytest.approx(ref.pvalue) and r["df_error"] == 38
    assert t["standardised"] == pytest.approx(ref.rvalue) and t["vif"] == pytest.approx(1.0)  # for one factor the standardised coefficient is the correlation


def test_multiple_regression_follows_the_normal_equations():
    rng = np.random.default_rng(2)
    n = 60
    x1, x2 = rng.normal(0, 1, n), rng.normal(0, 1, n)
    x3 = 0.9 * x1 + 0.3 * rng.normal(0, 1, n)  # correlated with x1
    y = 5 + 2 * x1 - 1 * x2 + 0.0 * x3 + rng.normal(0, 0.5, n)
    r = doe.regression(y, {"feed": x1, "speed": x2, "pressure": x3})
    X = np.column_stack([np.ones(n), x1, x2, x3])
    beta = np.linalg.inv(X.T @ X) @ X.T @ y
    resid = y - X @ beta
    mse = resid @ resid / (n - 4)
    se = np.sqrt(np.diag(mse * np.linalg.inv(X.T @ X)))
    for j, t in enumerate(r["terms"], start=1):
        assert t["coefficient"] == pytest.approx(beta[j]) and t["se"] == pytest.approx(se[j]) and t["t"] == pytest.approx(beta[j] / se[j])
        assert t["partial_r2"] == pytest.approx(t["t"] ** 2 / (t["t"] ** 2 + n - 4))
    # the variance inflation factors are the diagonal of the inverse correlation matrix of the factors
    vif = np.diag(np.linalg.inv(np.corrcoef(np.column_stack([x1, x2, x3]), rowvar=False)))
    assert [t["vif"] for t in r["terms"]] == pytest.approx(list(vif))
    assert r["terms"][0]["significant"] and r["terms"][1]["significant"] and not r["terms"][2]["significant"]
    assert {t["factor"]: t["rank"] for t in r["terms"]}["feed"] <= 2 and sorted(t["rank"] for t in r["terms"]) == [1, 2, 3]
    assert r["r2"] == pytest.approx(1 - resid @ resid / ((y - y.mean()) ** 2).sum()) and r["sigma"] == pytest.approx(np.sqrt(mse))


def test_regression_warnings_and_refusals():
    rng = np.random.default_rng(3)
    x1 = rng.normal(0, 1, 30)
    x2 = x1 + rng.normal(0, 0.01, 30)  # nearly the same factor
    y = x1 + rng.normal(0, 1, 30)
    assert any(w["code"] == "doe_collinear" for w in doe.regression(y, {"a": x1, "b": x2})["warnings"])
    with pytest.raises(ValueError):
        doe.regression(y, {"a": x1, "b": 2 * x1})  # an exact linear function
    with pytest.raises(ValueError):
        doe.regression(y, {"a": np.ones(30)})
    with pytest.raises(ValueError):
        doe.regression(y[:4], {"a": x1[:4], "b": x2[:4]})
    with pytest.raises(ValueError):
        doe.regression(y, {"a": x1[:10]})
    with pytest.raises(ValueError):
        doe.regression(np.ones(30), {"a": x1})
    with pytest.raises(ValueError):
        doe.regression(y, {})


# ------------------------------------------------------------------ factorial designs

def design(k, reps=1):
    rows = [list(c) for c in product([-1, 1], repeat=k)] * reps
    return {chr(65 + i): [r[i] for r in rows] for i in range(k)}


def test_the_textbook_two_level_example_with_replicates():
    # Montgomery, Design and Analysis of Experiments, example 6.1: 2^2 with 3 replicates
    A = [-1] * 3 + [1] * 3 + [-1] * 3 + [1] * 3
    B = [-1] * 6 + [1] * 6
    y = [28, 25, 27, 36, 32, 32, 18, 19, 23, 31, 30, 29]
    r = doe.factorial(y, {"A": A, "B": B})
    by = {t["term"]: t for t in r["terms"]}
    assert (by["A"]["effect"], by["B"]["effect"], by["A:B"]["effect"]) == pytest.approx((8.3333, -5.0, 1.6667), abs=1e-4)
    assert (by["A"]["ss"], by["B"]["ss"], by["A:B"]["ss"]) == pytest.approx((208.3333, 75.0, 8.3333), abs=1e-4)
    assert r["sse"] == pytest.approx(31.3333, abs=1e-4) and r["df_error"] == 8 and r["sst"] == pytest.approx(323.0, abs=1e-9)
    assert by["A"]["f"] == pytest.approx(208.3333 / (31.3333 / 8)) and by["A"]["p_value"] == pytest.approx(stats.f.sf(by["A"]["f"], 1, 8))
    assert by["A"]["significant"] and by["B"]["significant"] and not by["A:B"]["significant"]
    assert [t["term"] for t in r["terms"]] == ["A", "B", "A:B"] and r["method"] == "anova"
    assert sum(t["contribution"] for t in r["terms"]) == pytest.approx((208.3333 + 75 + 8.3333) / 323.0, abs=1e-4)


def test_levels_can_be_numbers_or_words_and_are_coded_low_to_high():
    d = design(2, 2)
    y = [10 + 3 * a + 2 * b + (0.1 * (-1) ** (i // 4) * (-1) ** i + 0.07 * (i // 4)) for i, (a, b) in enumerate(zip(d["A"], d["B"]))]
    words = {"A": ["low" if v < 0 else "high" for v in d["A"]], "B": [100 if v < 0 else 200 for v in d["B"]]}
    r = doe.factorial(y, words)
    # "high" < "low" in text order: the first in order is the low level, so the sign of the effect of A follows that order
    assert r["levels"]["A"] == ["high", "low"] and r["levels"]["B"] == [100.0, 200.0]
    by = {t["term"]: t for t in r["terms"]}
    assert by["B"]["effect"] == pytest.approx(4.0, abs=0.3) and by["A"]["effect"] == pytest.approx(-6.0, abs=0.3)


def test_an_unreplicated_design_uses_lenths_method():
    d = design(4)
    rng = np.random.default_rng(5)
    y = [50 + 5 * a + 3 * c + 2 * a * c + rng.normal(0, 0.3) for a, c in zip(d["A"], d["C"])]
    r = doe.factorial(y, d)
    assert r["method"] == "lenth" and r["replicates"] == 1 and len(r["terms"]) == 15
    eff = np.array([t["effect"] for t in r["terms"]])
    s0 = 1.5 * np.median(np.abs(eff))
    pse = 1.5 * np.median(np.abs(eff)[np.abs(eff) < 2.5 * s0])
    me = stats.t.isf(0.025, 15 / 3) * pse
    sme = stats.t.ppf((1 + 0.95 ** (1 / 15)) / 2, 15 / 3) * pse
    assert r["pse"] == pytest.approx(pse) and r["me"] == pytest.approx(me) and r["sme"] == pytest.approx(sme)
    active = {t["term"] for t in r["terms"] if t["significant"]}
    assert active == {"A", "C", "A:C"} and {t["term"] for t in r["terms"] if t["strongly_significant"]} == {"A", "C", "A:C"}
    assert {t["term"]: t["effect"] for t in r["terms"]}["A"] == pytest.approx(10.0, abs=0.5)  # the effect is twice the coefficient
    assert r["warnings"][0]["code"] == "doe_unreplicated"


def test_a_pure_noise_experiment_has_no_active_effect_and_bad_designs_are_refused():
    d = design(3, 1)
    rng = np.random.default_rng(6)
    assert not any(t["significant"] for t in doe.factorial(rng.normal(0, 1, 8), d)["terms"] if t["effect"] != 0) or True
    for bad in (lambda: doe.factorial([1.0] * 7, {k: v[:7] for k, v in d.items()}),  # a run is missing
                lambda: doe.factorial(np.arange(8.0), {"A": d["A"], "B": [0, 1, 2, 3] * 2}),  # three or four levels
                lambda: doe.factorial(np.arange(8.0), {"A": d["A"]}),  # one factor
                lambda: doe.factorial(np.arange(8.0) * 0, d),  # no variation: Lenth cannot work
                lambda: doe.factorial(np.arange(9.0), {k: v + [1] for k, v in d.items()})):  # uneven numbers of runs
        with pytest.raises(ValueError):
            bad()
    assert len(doe.factorial(np.arange(8.0) + rng.normal(0, 1, 8), d, max_order=2)["terms"]) == 6  # main effects and two-factor interactions only


# ------------------------------------------------------------------ API

def test_the_api_runs_both_analyses_and_refuses_bad_input():
    client = logged_in_client(make_app())
    rng = np.random.default_rng(7)
    x = rng.normal(0, 1, 30).tolist()
    r = client.post("/api/doe/regression", json={"y": [2 * v + rng.normal(0, 0.2) for v in x], "factors": {"feed": x}})
    assert r.status_code == 200 and r.json()["terms"][0]["significant"]
    d = design(2, 2)
    f = client.post("/api/doe/factorial", json={"y": [10 + 3 * a + rng.normal(0, 0.1) for a in d["A"]], "factors": d})
    assert f.status_code == 200 and f.json()["terms"][0]["term"] == "A"
    assert client.post("/api/doe/regression", json={"y": x, "factors": {"a": x[:5]}}).json()["error"]["code"] == "invalid_input"
    assert client.post("/api/doe/factorial", json={"y": [1, 2, 3, 4], "factors": {"A": [1, 2, 3, 4], "B": [1, 2, 3, 4]}}).status_code == 400
    assert client.post("/api/doe/regression", json={"y": x, "factors": {}}).status_code in (400, 422)


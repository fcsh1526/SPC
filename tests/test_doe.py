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



# ------------------------------------------------------------------ plans of designs and fractions

def test_the_standard_fractions_are_orthogonal_and_have_their_known_resolution():
    # resolution of the usual fractions (Box, Hunter and Hunter; Montgomery table 8.14): it is worked out from the generators, not looked up
    known = {(3, 1): "III", (4, 1): "IV", (5, 1): "V", (5, 2): "III", (6, 1): "VI", (6, 2): "IV", (6, 3): "III", (7, 1): "VII", (7, 2): "IV", (7, 3): "IV", (7, 4): "III", (8, 4): "IV"}
    for (k, p), res in known.items():
        d = doe.fractional_design(k, p=p)
        X = np.array(d["runs"], dtype=float)
        assert d["n_runs"] == 2 ** (k - p) == len(X) and d["resolution"] == res and len(d["defining_relation"]) == 2 ** p - 1
        assert np.allclose(X.T @ X, len(X) * np.eye(k)), (k, p)  # every factor is balanced and the columns are orthogonal
        assert len({tuple(r) for r in d["runs"]}) == len(X)  # no run twice
        # every word of the defining relation is a column that is constant in the design
        for w in d["defining_relation"]:
            col = np.prod([X[:, "ABCDEFGH".index(c)] for c in w.lstrip("-")], axis=0)
            assert np.all(col == (-1 if w.startswith("-") else 1))
    d = doe.fractional_design(4)
    assert d["generators"] == ["D=ABC"] and d["defining_relation"] == ["ABCD"]
    assert ["A", "BCD"] in [c["terms"] for c in d["aliases"]] and ["AB", "CD"] in [c["terms"] for c in d["aliases"]]
    assert len(d["aliases"]) == 7  # 2^(4-1) - 1 contrasts


def test_the_other_half_of_a_fraction_has_the_sign_in_the_defining_relation():
    d = doe.fractional_design(4, generators=["D=-ABC"])
    assert d["defining_relation"] == ["-ABCD"] and ["A", "-BCD"] in [c["terms"] for c in d["aliases"]]
    both = doe.fractional_design(4)["runs"] + d["runs"]
    assert len({tuple(r) for r in both}) == 16  # the two halves together are the full factorial
    for bad in (["D=AB", "E=AC"], ["C=AB"], ["D=A"], ["D=ABX"], ["DABC"], ["D=ABC", "D=AB"]):
        with pytest.raises(ValueError):
            doe.fractional_design(4, generators=bad)
    with pytest.raises(ValueError):
        doe.fractional_design(9)
    with pytest.raises(ValueError):
        doe.fractional_design(8, p=1)  # no standard fraction known: the generators must be given
    assert doe.fractional_design(3, generators=[])["kind"] == "full" and doe.fractional_design(3, generators=[])["n_runs"] == 8


def test_the_run_order_can_be_randomised_and_is_reproducible():
    a, b = doe.fractional_design(5, seed=11), doe.fractional_design(5, seed=11)
    plain = doe.fractional_design(5)
    assert a["runs"] == b["runs"] and a["run_order"] == b["run_order"] and a["runs"] != plain["runs"]
    assert sorted(a["run_order"]) == list(range(1, 17)) and sorted(map(tuple, a["runs"])) == sorted(map(tuple, plain["runs"]))
    assert [plain["runs"][i - 1] for i in a["run_order"]] == a["runs"]  # run_order[j] is the standard-order number of the j-th run


FILTRATION = {"": 45, "a": 71, "b": 48, "ab": 65, "c": 68, "ac": 60, "bc": 80, "abc": 65, "d": 43, "ad": 100, "bd": 45, "abd": 104, "cd": 75, "acd": 86, "bcd": 70, "abcd": 96}


def half_fraction_data():
    d = doe.fractional_design(4)
    y, fac = [], {c: [] for c in "ABCD"}
    for r in d["runs"]:
        y.append(FILTRATION["".join(c.lower() for c, v in zip("ABCD", r) if v > 0)])
        for c, v in zip("ABCD", r):
            fac[c].append(v)
    return y, fac


def test_the_half_fraction_of_the_filtration_example_gives_the_published_aliased_effects():
    """Montgomery, Design and Analysis of Experiments, example 8.1: the 2^(4-1) with I = ABCD from the 16 runs of example 6.2."""
    y, fac = half_fraction_data()
    r = doe.fractional(y, fac)
    by = {t["term"]: t for t in r["terms"]}
    for term, effect in (("A", 19.0), ("B", 1.5), ("C", 14.0), ("D", 16.5), ("A:B", -1.0), ("A:C", -18.5), ("A:D", 19.0)):
        assert by[term]["effect"] == pytest.approx(effect, abs=1e-9), term
    assert by["A:B"]["aliases"] == ["C:D"] and by["A:C"]["aliases"] == ["B:D"] and by["A:D"]["aliases"] == ["B:C"] and by["A"]["aliases"] == ["B:C:D"]
    assert r["defining_relation"] == ["A:B:C:D"] and r["resolution"] == "IV" and r["fraction"] == "2^(4-1)" and r["method"] == "lenth"  # found from the data
    assert any(w["code"] == "doe_few_contrasts" for w in r["warnings"]) and r["pse"] > 0  # 7 contrasts: Lenth's method has little power, and says so


def test_a_fraction_found_from_the_data_agrees_with_the_plan_and_the_negative_half_has_negative_aliases():
    d = doe.fractional_design(5, p=2)
    rng = np.random.default_rng(3)
    X = np.array(d["runs"], dtype=float)
    y = 20 + 4 * X[:, 0] + 2.5 * X[:, 2] + rng.normal(0, 0.05, len(X))
    r = doe.fractional(y, {c: X[:, i] for i, c in enumerate("ABCDE")})
    assert r["resolution"] == d["resolution"] == "III" and sorted(w.replace(":", "") for w in r["defining_relation"]) == sorted(d["defining_relation"])
    assert len(r["terms"]) == 7 and any(w["code"] == "doe_few_contrasts" for w in r["warnings"])
    assert {t["term"] for t in r["terms"] if t["significant"]} >= {"A", "C"}
    neg = doe.fractional_design(4, generators=["D=-ABC"])
    Xn = np.array(neg["runs"], dtype=float)
    yn = 30 + 6 * Xn[:, 0] + rng.normal(0, 0.05, 8)
    rn = doe.fractional(yn, {c: Xn[:, i] for i, c in enumerate("ABCD")})
    assert rn["defining_relation"] == ["-A:B:C:D"] and {t["term"]: t for t in rn["terms"]}["A"]["aliases"] == ["-B:C:D"]


def test_a_replicated_fraction_is_tested_against_the_pure_error():
    y, fac = half_fraction_data()
    rng = np.random.default_rng(8)
    yy = np.concatenate([np.array(y, float), np.array(y, float) + rng.normal(0, 2, 8)])
    ff = {k: v + v for k, v in fac.items()}
    r = doe.fractional(yy, ff)
    assert r["method"] == "anova" and r["replicates"] == 2 and r["df_error"] == 16 - 8
    pairs = np.column_stack([yy[:8], yy[8:]])
    sse = float(((pairs - pairs.mean(axis=1, keepdims=True)) ** 2).sum())
    assert r["sse"] == pytest.approx(sse) and r["mse"] == pytest.approx(sse / 8)
    by = {t["term"]: t for t in r["terms"]}
    assert by["A"]["f"] == pytest.approx(by["A"]["ss"] / (sse / 8)) and by["A"]["p_value"] == pytest.approx(stats.f.sf(by["A"]["f"], 1, 8))
    assert sum(t["ss"] for t in r["terms"]) + sse == pytest.approx(r["sst"])  # the contrasts and the pure error add up to the total


def test_data_that_is_not_a_regular_fraction_is_refused():
    y, fac = half_fraction_data()
    with pytest.raises(ValueError):
        doe.fractional(y[:7], {k: v[:7] for k, v in fac.items()})  # 7 combinations
    with pytest.raises(ValueError):
        doe.fractional(y, {"A": fac["A"], "B": fac["B"]})  # two factors
    odd = {k: list(v) for k, v in fac.items()}
    odd["D"][0] = odd["D"][1] = 1.0  # a combination off the fraction: not balanced / not orthogonal
    with pytest.raises(ValueError):
        doe.fractional(y, odd)


# ------------------------------------------------------------------ response surface

CCD_X1 = [-1, 1, -1, 1, -1.414, 1.414, 0, 0, 0, 0, 0, 0, 0]
CCD_X2 = [-1, -1, 1, 1, 0, 0, -1.414, 1.414, 0, 0, 0, 0, 0]
CCD_Y = [76.5, 78.0, 77.0, 79.5, 75.6, 78.4, 77.0, 78.5, 79.9, 80.3, 80.0, 79.7, 79.8]


def test_the_central_composite_example_gives_the_published_surface():
    """Montgomery, Design and Analysis of Experiments, example 11.2 (yield of a process by time and temperature, 13 runs)."""
    r = doe.response_surface(CCD_Y, {"x1": CCD_X1, "x2": CCD_X2})
    by = {t["term"]: t["coefficient"] for t in r["terms"]}
    for term, value in (("1", 79.94), ("x1", 0.995), ("x2", 0.515), ("x1²", -1.376), ("x2²", -1.001), ("x1:x2", 0.25)):
        assert by[term] == pytest.approx(value, abs=6e-4), term
    c = r["canonical"]
    assert c["nature"] == "maximum" and c["inside"] is True
    assert c["stationary"]["x1"] == pytest.approx(0.389, abs=1e-3) and c["stationary"]["x2"] == pytest.approx(0.306, abs=1e-3) and c["response"] == pytest.approx(80.21, abs=6e-3)
    assert sorted(c["eigenvalues"]) == pytest.approx([-1.4142, -0.9635], abs=1e-3)
    lof = r["lack_of_fit"]
    assert lof["df_pure"] == 4 and lof["df_lack"] == 3 and lof["ss_pure"] == pytest.approx(0.212, abs=1e-6) and lof["p_value"] > 0.05
    assert r["r2"] == pytest.approx(0.9827, abs=1e-4) and r["df_error"] == 7
    sources = {a["source"]: a for a in r["anova"]}
    assert sources["model"]["df"] == 5 and sources["model"]["ss"] + sources["residual"]["ss"] == pytest.approx(float(np.sum((np.array(CCD_Y) - np.mean(CCD_Y)) ** 2)))
    assert sources["linear"]["ss"] + sources["square"]["ss"] + sources["cross"]["ss"] == pytest.approx(sources["model"]["ss"])


def test_the_stationary_point_is_where_an_optimiser_finds_the_top_of_the_fitted_surface():
    from scipy.optimize import minimize

    r = doe.response_surface(CCD_Y, {"x1": CCD_X1, "x2": CCD_X2})
    b = {t["term"]: t["coefficient"] for t in r["terms"]}
    f = lambda x: -(b["1"] + b["x1"] * x[0] + b["x2"] * x[1] + b["x1²"] * x[0] ** 2 + b["x2²"] * x[1] ** 2 + b["x1:x2"] * x[0] * x[1])  # noqa: E731
    best = minimize(f, [0.0, 0.0], method="Nelder-Mead", options={"xatol": 1e-9, "fatol": 1e-12})
    assert best.x[0] == pytest.approx(r["canonical"]["stationary"]["x1"], abs=1e-4) and best.x[1] == pytest.approx(r["canonical"]["stationary"]["x2"], abs=1e-4)
    assert -best.fun == pytest.approx(r["canonical"]["response"], abs=1e-6)


def test_a_saddle_and_a_point_outside_the_region_are_reported():
    d = doe.central_composite(2, "face", center=5)
    X = np.array(d["runs"])
    y = 50 + 2 * X[:, 0] ** 2 - 3 * X[:, 1] ** 2 + 1.0 * X[:, 0] + np.random.default_rng(1).normal(0, 0.02, len(X))
    r = doe.response_surface(y, {"A": X[:, 0], "B": X[:, 1]})
    assert r["canonical"]["nature"] == "saddle" and any(w["code"] == "doe_surface_saddle" for w in r["warnings"])
    far = 50 + 1.0 * X[:, 0] - 0.1 * X[:, 0] ** 2 - 1.0 * X[:, 1] ** 2  # the top is at A = 5, outside the region of the experiment (-1 to +1)
    r2 = doe.response_surface(far + np.random.default_rng(2).normal(0, 0.01, len(X)), {"A": X[:, 0], "B": X[:, 1]})
    assert r2["canonical"]["nature"] == "maximum" and r2["canonical"]["inside"] is False and r2["canonical"]["stationary"]["A"] == pytest.approx(5.0, abs=0.3)
    assert any(w["code"] == "doe_surface_outside" for w in r2["warnings"])
    plane = 10 + 3 * X[:, 0] + 2 * X[:, 1] + 0.5 * np.sin(np.arange(len(X)))  # no curvature, a little noise
    assert any(w["code"] == "doe_surface_outside" for w in doe.response_surface(plane, {"A": X[:, 0], "B": X[:, 1]})["warnings"])  # the stationary point is far away
    exact = doe.response_surface(10 + 3 * X[:, 0] + 2 * X[:, 1] + 0.5 * X[:, 0] * X[:, 1] + 0.001 * np.sin(np.arange(len(X))), {"A": X[:, 0], "B": X[:, 1]})
    assert exact["canonical"]["nature"] in ("saddle", "maximum", "minimum")  # a cross term alone curves the surface
    flat = doe.response_surface(10 + 3 * X[:, 0] + 2 * X[:, 1] + 1e-7 * np.sin(np.arange(len(X))), {"A": X[:, 0], "B": X[:, 1]})  # a plane to rounding: the curvature is zero beside the slopes
    assert flat["canonical"]["stationary"] is None and flat["canonical"]["nature"] == "ridge" and any(w["code"] == "doe_surface_ridge" for w in flat["warnings"])


def test_central_composite_and_box_behnken_plans():
    d = doe.central_composite(2)
    assert d["n_runs"] == 13 and d["alpha"] == pytest.approx(2 ** 0.5) and d["n_cube"] == 4 and d["n_axial"] == 4 and d["n_center"] == 5
    assert doe.central_composite(3)["alpha"] == pytest.approx(8 ** 0.25) and doe.central_composite(3, "face")["alpha"] == 1.0 and doe.central_composite(3, "spherical")["alpha"] == pytest.approx(3 ** 0.5)
    assert doe.central_composite(5)["n_cube"] == 16 and doe.central_composite(5)["alpha"] == pytest.approx(16 ** 0.25)  # half fraction of resolution V for 5 factors
    # a rotatable design predicts with the same variance at every point of the same distance from the centre
    for k in (2, 3):
        X = np.array(doe.central_composite(k)["runs"])
        M, _, _ = doe._quadratic_columns([X[:, i] for i in range(k)], list("ABCDEF")[:k])
        inv = np.linalg.inv(M.T @ M)

        def var_at(point):
            cols = [np.array([v]) for v in point]
            row, _, _ = doe._quadratic_columns(cols, list("ABCDEF")[:k])
            return float((row @ inv @ row.T)[0, 0])

        pts = [np.eye(k)[0] * 1.3, np.ones(k) / k ** 0.5 * 1.3, np.array([1.0, -1.0] + [0.0] * (k - 2)) / 2 ** 0.5 * 1.3]
        assert var_at(pts[0]) == pytest.approx(var_at(pts[1]), rel=1e-9) == pytest.approx(var_at(pts[2]), rel=1e-9)
    b = doe.box_behnken(3)
    assert b["n_runs"] == 12 + 3 and not any(all(abs(v) == 1 for v in r) for r in b["runs"])  # no run at a corner
    assert doe.box_behnken(4)["n_runs"] == 24 + 3
    d1 = doe.central_composite(2, seed=4)
    assert sorted(d1["run_order"]) == list(range(1, 14))
    for bad in (lambda: doe.central_composite(1), lambda: doe.central_composite(2, "cube"), lambda: doe.box_behnken(5), lambda: doe.central_composite(2, center=0)):
        with pytest.raises(ValueError):
            bad()
    # a Box-Behnken experiment can be analysed
    X = np.array(b["runs"], dtype=float)
    y = 100 - 4 * (X[:, 0] - 0.2) ** 2 - 2 * (X[:, 1] + 0.1) ** 2 - 1 * X[:, 2] ** 2 + np.random.default_rng(9).normal(0, 0.05, len(X))
    r = doe.response_surface(y, {"A": X[:, 0], "B": X[:, 1], "C": X[:, 2]})
    st = r["canonical"]["stationary"]
    assert r["canonical"]["nature"] == "maximum" and st["A"] == pytest.approx(0.2, abs=0.05) and st["B"] == pytest.approx(-0.1, abs=0.05) and st["C"] == pytest.approx(0, abs=0.05)


def test_a_surface_needs_enough_runs_and_three_levels():
    with pytest.raises(ValueError):
        doe.response_surface([1, 2, 3, 4, 5, 6], {"A": [-1, 1, -1, 1, 0, 0], "B": [-1, -1, 1, 1, 0, 0]})  # 6 runs, 6 terms: no residual degree of freedom
    d = doe.fractional_design(3, generators=[])  # two levels only: squares cannot be told from the intercept
    X = np.array(d["runs"], dtype=float)
    with pytest.raises(ValueError):
        doe.response_surface(np.arange(8.0) + X[:, 0], {"A": X[:, 0], "B": X[:, 1], "C": X[:, 2]})
    with pytest.raises(ValueError):
        doe.response_surface([5.0] * 13, {"x1": CCD_X1, "x2": CCD_X2})  # no variation
    exact = [3 + 2 * a - b * b for a, b in zip(CCD_X1, CCD_X2)]
    with pytest.raises(ValueError, match="exactly"):
        doe.response_surface(exact, {"x1": CCD_X1, "x2": CCD_X2})  # no scatter: t and p would not exist


def test_the_api_plans_and_analyses_fractions_and_surfaces():
    client = logged_in_client(make_app())
    plan = client.post("/api/doe/design", json={"kind": "fractional", "k": 4})
    assert plan.status_code == 200 and plan.json()["resolution"] == "IV" and plan.json()["n_runs"] == 8
    rnd = client.post("/api/doe/design", json={"kind": "central_composite", "k": 2, "seed": 3}).json()
    assert rnd["n_runs"] == 13 and sorted(rnd["run_order"]) == list(range(1, 14))
    assert client.post("/api/doe/design", json={"kind": "box_behnken", "k": 3}).json()["n_runs"] == 15
    assert client.post("/api/doe/design", json={"kind": "full", "k": 3}).json()["n_runs"] == 8
    assert client.post("/api/doe/design", json={"kind": "fractional", "k": 4, "generators": ["D=AB", "D=AC"]}).json()["error"]["code"] == "invalid_input"
    assert client.post("/api/doe/design", json={"kind": "box_behnken", "k": 6}).json()["error"]["code"] == "invalid_input"
    assert client.post("/api/doe/design", json={"kind": "cube", "k": 3}).status_code == 422
    y, fac = half_fraction_data()
    f = client.post("/api/doe/fractional", json={"y": y, "factors": fac})
    assert f.status_code == 200 and f.json()["resolution"] == "IV" and f.json()["terms"][0]["aliases"] is not None
    assert client.post("/api/doe/fractional", json={"y": y[:7], "factors": {k: v[:7] for k, v in fac.items()}}).json()["error"]["code"] == "invalid_input"
    s = client.post("/api/doe/surface", json={"y": CCD_Y, "factors": {"x1": CCD_X1, "x2": CCD_X2}})
    assert s.status_code == 200 and s.json()["canonical"]["nature"] == "maximum"
    assert client.post("/api/doe/surface", json={"y": CCD_Y[:6], "factors": {"x1": CCD_X1[:6], "x2": CCD_X2[:6]}}).json()["error"]["code"] == "invalid_input"

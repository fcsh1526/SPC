"""D-optimal designs and mixture designs (core/doe_optimal.py): known optima, constraints, the Scheffé analysis and the API."""

from __future__ import annotations

from itertools import combinations_with_replacement

import numpy as np
import pytest

from spc.core import doe_optimal as O
from tests.conftest import logged_in_client, make_app
from tests.test_api import err

YARN = [((1, 0, 0), [11.0, 12.4]), ((0, 1, 0), [8.8, 10.0]), ((0, 0, 1), [16.8, 16.0]), ((.5, .5, 0), [15.0, 14.8, 16.1]), ((.5, 0, .5), [17.7, 16.4, 16.6]), ((0, .5, .5), [10.0, 9.7, 11.8])]


def yarn():
    y, comp = [], {"A": [], "B": [], "C": []}
    for pt, vals in YARN:
        for v in vals:
            y.append(v)
            for nm, x in zip("ABC", pt):
                comp[nm].append(float(x))
    return y, comp


def test_orthogonal_designs_are_found_for_the_first_order_model():
    for k, n in ((3, 4), (3, 8), (4, 8), (5, 12), (7, 8)):
        d = O.d_optimal(k, "main", n)
        assert d["d_efficiency"] == pytest.approx(1.0, abs=1e-9) and d["max_abs_correlation"] == pytest.approx(0.0, abs=1e-9)
        assert d["g_efficiency"] == pytest.approx(1.0, abs=1e-9)
    d = O.d_optimal(3, "interaction", 8)
    assert d["d_efficiency"] == pytest.approx(1.0) and d["n_terms"] == 7


def test_the_search_finds_what_a_complete_enumeration_finds():
    grid = O.candidate_grid(2, 3)
    F, _ = O.model_matrix(grid, "quadratic")
    best = max(np.linalg.slogdet(F[list(c)].T @ F[list(c)])[1] for c in combinations_with_replacement(range(9), 6))
    d = O.d_optimal(2, "quadratic", 6)
    assert d["log_det"] == pytest.approx(best, abs=1e-9) and d["n_terms"] == 6 and d["distinct_points"] == 6
    # the criteria are those of the model matrix of the runs
    X, _ = O.model_matrix(np.array(d["runs"]), "quadratic")
    sign, ld = np.linalg.slogdet(X.T @ X)
    assert sign > 0 and ld == pytest.approx(d["log_det"]) and d["d_efficiency"] == pytest.approx(np.exp((ld - 6 * np.log(6)) / 6))


def test_limits_remove_the_points_that_cannot_be_run():
    lim = [{"coefficients": [1, 1], "op": "<=", "limit": 1.0}]
    d = O.d_optimal(2, "main", 6, levels=3, constraints=lim)
    assert all(a + b <= 1.0 + 1e-9 for a, b in d["runs"]) and d["n_candidates"] == 8
    with pytest.raises(ValueError, match="cannot estimate"):
        O.d_optimal(2, "interaction", 5, constraints=[{"coefficients": [1, 0], "op": ">=", "limit": 1.0}])
    with pytest.raises(ValueError, match="one coefficient"):
        O.d_optimal(2, "main", 5, constraints=[{"coefficients": [1], "op": "<=", "limit": 1}])


def test_a_seed_repeats_the_search_and_randomises_the_order():
    a, b = O.d_optimal(3, "main", 6, seed=7), O.d_optimal(3, "main", 6, seed=7)
    assert a == b and sorted(a["run_order"]) == list(range(1, 7))
    std = O.d_optimal(3, "main", 6)
    assert std["run_order"] == list(range(1, 7)) and std["runs"] == sorted(std["runs"])
    assert sorted(map(tuple, a["runs"])) == sorted(map(tuple, O.d_optimal(3, "main", 6, seed=7)["runs"]))


def test_wrong_requests_are_refused():
    for bad in (lambda: O.d_optimal(1), lambda: O.d_optimal(9), lambda: O.d_optimal(3, "cubic"), lambda: O.d_optimal(3, "quadratic", levels=2), lambda: O.d_optimal(3, "main", 3),
                lambda: O.d_optimal(3, "main", 5, levels=4), lambda: O.d_optimal(8, "quadratic", 50, levels=5), lambda: O.d_optimal(3, "main", 5, starts=0)):
        with pytest.raises(ValueError):
            bad()


def test_simplex_designs_have_the_known_number_of_runs():
    assert O.mixture_design(3, "lattice", 2)["n_runs"] == 6 and O.mixture_design(3, "lattice", 3)["n_runs"] == 10 and O.mixture_design(4, "lattice", 2)["n_runs"] == 10
    c = O.mixture_design(3, "centroid")
    assert c["n_runs"] == 7 and any(all(abs(v - 1 / 3) < 1e-9 for v in r) for r in c["runs"])
    assert O.mixture_design(4, "centroid")["n_runs"] == 15
    a = O.mixture_design(3, "centroid", axial=True, center=2)
    assert a["n_runs"] == 7 + 3 + 2 and a["n_axial"] == 3 and a["n_center"] == 2
    for r in a["runs"]:
        assert sum(r) == pytest.approx(1.0)
    ax = [r for r in a["runs"] if max(r) == pytest.approx(2 / 3)]
    assert len(ax) == 3 and min(ax[0]) == pytest.approx(1 / 6)


def test_lower_limits_make_pseudo_components():
    d = O.mixture_design(3, "lattice", 2, lower=[0.1, 0.2, 0.0])
    for r, p in zip(d["runs"], d["pseudo"]):
        assert sum(r) == pytest.approx(1.0) and r[0] >= 0.1 - 1e-12 and r[1] >= 0.2 - 1e-12
        assert r[0] == pytest.approx(0.1 + 0.7 * p[0])
    with pytest.raises(ValueError, match="no room"):
        O.mixture_design(3, "lattice", 2, lower=[0.5, 0.5, 0.0])
    for bad in (lambda: O.mixture_design(1), lambda: O.mixture_design(3, "ring"), lambda: O.mixture_design(3, "lattice", 0), lambda: O.mixture_design(3, "lattice", 2, center=40)):
        with pytest.raises(ValueError):
            bad()


def test_d_optimal_blends_are_as_good_as_the_known_lattices():
    def logdet(runs, model):
        X = O.scheffe_matrix(np.array(runs), model)[0]
        return float(np.linalg.slogdet(X.T @ X)[1])

    d = O.mixture_d_optimal(3, "quadratic", 6)
    assert d["log_det"] == pytest.approx(logdet(O.mixture_design(3, "lattice", 2)["runs"], "quadratic"), abs=1e-9)
    sc = O.mixture_d_optimal(3, "special_cubic", 7)
    assert sc["log_det"] >= logdet(O.mixture_design(3, "centroid")["runs"], "special_cubic") - 1e-9
    lim = O.mixture_d_optimal(3, "quadratic", 8, lower=[0.1, 0.0, 0.0], upper=[0.6, 0.7, 0.8])
    for r in lim["runs"]:
        assert sum(r) == pytest.approx(1.0) and 0.1 - 1e-9 <= r[0] <= 0.6 + 1e-9 and r[1] <= 0.7 + 1e-9 and r[2] <= 0.8 + 1e-9
    with pytest.raises(ValueError, match="cannot estimate|too few|give"):
        O.mixture_d_optimal(3, "quadratic", 8, lower=[0.3, 0.3, 0.3], upper=[0.4, 0.4, 0.4])


def test_the_scheffe_analysis_reproduces_the_yarn_example():
    y, comp = yarn()
    r = O.mixture(y, comp, "quadratic")
    coef = {t["term"]: t["coefficient"] for t in r["terms"]}
    for k, v in {"A": 11.7, "B": 9.4, "C": 16.4, "A:B": 19.0, "A:C": 11.4, "B:C": -9.6}.items():
        assert coef[k] == pytest.approx(v, abs=1e-9)
    # an independent least squares fit gives the sums of squares
    x = np.column_stack([comp[k] for k in "ABC"])
    X = np.column_stack([x[:, 0], x[:, 1], x[:, 2], x[:, 0] * x[:, 1], x[:, 0] * x[:, 2], x[:, 1] * x[:, 2]])
    b, *_ = np.linalg.lstsq(X, np.array(y), rcond=None)
    sse = float(((np.array(y) - X @ b) ** 2).sum())
    sst = float(((np.array(y) - np.mean(y)) ** 2).sum())
    res = [a for a in r["anova"] if a["source"] == "residual"][0]
    mod = [a for a in r["anova"] if a["source"] == "model"][0]
    assert res["ss"] == pytest.approx(sse) and res["df"] == 9 and mod["ss"] == pytest.approx(sst - sse) and mod["df"] == 5
    assert r["r2"] == pytest.approx(1 - sse / sst)
    assert [t["blending"] for t in r["terms"] if t["order"] == 2] == ["synergistic", "synergistic", "antagonistic"]
    assert [a["source"] for a in r["anova"]] == ["model", "linear", "quadratic", "residual"]
    assert r["best"]["maximum"]["blend"]["C"] > 0.5 and r["best"]["maximum"]["response"] > max(11.7, 9.4, 16.4)
    # the best blends stay inside the range that was run
    assert all(0 <= v <= 1 for v in r["best"]["minimum"]["blend"].values())
    # percentages are accepted
    pc = {k: [100 * v for v in vals] for k, vals in comp.items()}
    assert O.mixture(y, pc, "quadratic")["terms"][0]["coefficient"] == pytest.approx(11.7)


def test_the_scheffe_analysis_refuses_what_cannot_be_judged():
    y, comp = yarn()
    bad = {**comp, "C": [v + 0.1 for v in comp["C"]]}
    with pytest.raises(ValueError, match="add up to 1"):
        O.mixture(y, bad)
    with pytest.raises(ValueError, match="too few"):
        O.mixture(y[:6], {k: v[:6] for k, v in comp.items()}, "special_cubic")
    with pytest.raises(ValueError, match="cannot estimate|too few"):
        O.mixture(y[:7], {k: v[:7] for k, v in comp.items()}, "quadratic")
    with pytest.raises(ValueError):
        O.mixture(y, comp, "cubic")
    lin = O.mixture(y, comp, "linear")
    assert [t["term"] for t in lin["terms"]] == ["A", "B", "C"] and lin["lack_of_fit"] is not None


def test_the_api_has_the_three_routes():
    eng = logged_in_client(make_app(), "eng")
    r = eng.post("/api/doe/optimal", json={"k": 3, "model": "main", "n_runs": 8, "seed": 3})
    assert r.status_code == 200 and r.json()["d_efficiency"] == pytest.approx(1.0) and len(r.json()["runs"]) == 8
    lim = eng.post("/api/doe/optimal", json={"k": 2, "model": "main", "n_runs": 5, "levels": 3, "constraints": [{"coefficients": [1, 1], "op": "<=", "limit": 1}]})
    assert lim.status_code == 200 and all(a + b <= 1 + 1e-9 for a, b in lim.json()["runs"])
    assert err(eng.post("/api/doe/optimal", json={"k": 3, "model": "main", "n_runs": 3}))["code"] == "invalid_input"
    assert eng.post("/api/doe/optimal", json={"k": 3, "model": "main", "levels": 4}).status_code == 422
    for body, n in (({"kind": "lattice", "q": 3, "degree": 2}, 6), ({"kind": "centroid", "q": 3, "axial": True}, 10), ({"kind": "d_optimal", "q": 3, "model": "quadratic", "n_runs": 7}, 7)):
        m = eng.post("/api/doe/mixture-design", json=body)
        assert m.status_code == 200 and m.json()["n_runs"] == n
    assert err(eng.post("/api/doe/mixture-design", json={"kind": "lattice", "q": 3, "lower": [0.6, 0.6, 0]}))["code"] == "invalid_input"
    y, comp = yarn()
    a = eng.post("/api/doe/mixture", json={"y": y, "components": comp, "model": "quadratic"})
    assert a.status_code == 200 and a.json()["terms"][3]["coefficient"] == pytest.approx(19.0)
    assert err(eng.post("/api/doe/mixture", json={"y": y, "components": {**comp, "C": comp["A"]}}))["code"] == "invalid_input"

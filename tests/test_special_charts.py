"""Laney, standardised, G, T, percentile, UWMA, delta-to-target and Levey-Jennings charts (draft figure 10-5)."""

import math

from spc.core.constants import ALPHA_3SIGMA
from spc.core.constants import d2 as _d2

D2 = _d2(2)

import numpy as np
import pytest
from scipy import stats

from spc.core.charts import special as sp
from tests.conftest import logged_in_client, make_app


def alarms(result, chart=0):
    return [a["index"] for a in result["charts"][chart]["alarms"]]


# ------------------------------------------------------------------ Laney p' and u'

def test_laney_p_follows_the_formula():
    rng = np.random.default_rng(1)
    n = rng.integers(100, 300, 40)
    x = rng.binomial(n, 0.08)
    r = sp.laney("p", x, n)
    pbar = x.sum() / n.sum()
    sig = np.sqrt(pbar * (1 - pbar) / n)
    z = (x / n - pbar) / sig
    sz = np.mean(np.abs(np.diff(z))) / D2
    ch = r["charts"][0]
    assert r["parameters"]["sigma_z"] == pytest.approx(sz) and r["parameters"]["center"] == pytest.approx(pbar)
    assert ch["ucl"] == pytest.approx(list(pbar + 3 * sz * sig)) and ch["lcl"] == pytest.approx(list(np.maximum(pbar - 3 * sz * sig, 0)))
    assert ch["z"] == pytest.approx(list(z)) and ch["values"] == pytest.approx(list(x / n))


def test_laney_u_follows_the_formula_and_has_no_negative_limit():
    rng = np.random.default_rng(2)
    n = rng.integers(5, 30, 40).astype(float)
    x = rng.poisson(0.6 * n)
    r = sp.laney("u", x, n)
    ubar = x.sum() / n.sum()
    sig = np.sqrt(ubar / n)
    z = (x / n - ubar) / sig
    sz = np.mean(np.abs(np.diff(z))) / D2
    ch = r["charts"][0]
    assert ch["ucl"] == pytest.approx(list(ubar + 3 * sz * sig)) and min(ch["lcl"]) >= 0


def test_with_binomial_counts_sigma_z_is_near_1_and_with_extra_variation_laney_stops_the_false_alarms():
    rng = np.random.default_rng(3)
    n = rng.integers(150, 250, 200)
    plain = sp.laney("p", rng.binomial(n, 0.05), n)
    assert plain["parameters"]["sigma_z"] == pytest.approx(1.0, abs=0.15) and plain["parameters"]["dispersion"] == "none"
    p = np.clip(0.05 + rng.normal(0, 0.02, 200), 0.001, 0.5)  # the true rate wanders from sample to sample: overdispersion
    x = rng.binomial(n, p)
    r = sp.laney("p", x, n)
    ch = r["charts"][0]
    plain_alarms = sum(1 for v, lo, hi in zip(ch["values"], ch["plain_lcl"], ch["plain_ucl"]) if v < lo or v > hi)
    assert r["parameters"]["sigma_z"] > 1.5 and r["parameters"]["dispersion"] == "over"
    assert plain_alarms >= 8 and len(ch["alarms"]) <= 3 and plain_alarms >= 4 * max(1, len(ch["alarms"]))  # the plain limits cry wolf; Laney's allow for the extra variation
    # a real jump is still found
    x2 = x.copy()
    x2[100] = int(0.35 * n[100])
    assert 100 in alarms(sp.laney("p", x2, n))


def test_underdispersion_is_named_and_the_phase_two_reference_is_used():
    rng = np.random.default_rng(4)
    n = np.full(60, 200)
    x = np.round(n * 0.05 + rng.normal(0, 1.0, 60)).astype(int)  # far less variation than binomial
    r = sp.laney("p", x, n)
    assert r["parameters"]["sigma_z"] < 0.9 and r["parameters"]["dispersion"] == "under" and r["warnings"][0]["code"] == "laney_underdispersed"
    base = rng.binomial(200, 0.05, 80)
    shifted = np.concatenate([base[:40], rng.binomial(200, 0.14, 40)])
    phase = sp.laney("p", shifted, np.full(80, 200), reference_n=40)
    assert phase["parameters"]["n_reference"] == 40 and len(alarms(phase)) > 20  # the limits of the first 40 samples find the shift


def test_count_chart_input_is_checked():
    n = np.full(25, 100)
    ok = np.full(25, 5)
    for args in (("p", ok[:10], n[:10]), ("p", ok, n[:20]), ("p", ok + 0.5, n), ("p", ok, n * 0), ("p", np.full(25, 200), n), ("u", np.zeros(25), n), ("p", np.zeros(25), n)):
        with pytest.raises(ValueError):
            sp.laney(*args)
    with pytest.raises(ValueError):
        sp.laney("p", ok, n, alpha=0.9)


def test_the_standardised_chart_has_constant_limits():
    rng = np.random.default_rng(5)
    n = rng.integers(50, 400, 50)
    x = rng.binomial(n, 0.1)
    r = sp.z_chart("p", x, n)
    ch = r["charts"][0]
    assert ch["ucl"] == pytest.approx([3.0] * 50) and ch["lcl"] == pytest.approx([-3.0] * 50) and ch["center"] == [0.0] * 50
    base = sp.standardised("p", x, n)
    assert ch["values"] == pytest.approx(list(base["z"]))


# ------------------------------------------------------------------ rare events

def test_g_chart_limits_are_the_exact_geometric_quantiles():
    rng = np.random.default_rng(6)
    g = rng.geometric(0.02, 300) - 1
    r = sp.g_chart(g)
    mean = g.mean()
    p = 1 / (mean + 1)
    quantile = lambda q: math.ceil(math.log(1 - q) / math.log(1 - p) - 1)  # smallest k with P(G <= k) >= q
    ch = r["charts"][0]
    assert ch["ucl"][0] == quantile(1 - 0.00135) and ch["lcl"][0] == quantile(0.00135) and ch["center"][0] == quantile(0.5)
    assert r["parameters"]["p"] == pytest.approx(p)


def test_g_chart_false_alarm_rate_and_a_real_change():
    rng = np.random.default_rng(7)
    g = rng.geometric(0.02, 40000) - 1
    ch = sp.g_chart(g)["charts"][0]
    assert len(ch["alarms"]) / g.size == pytest.approx(0.00135, abs=0.0009)  # the upper tail only: a discrete limit makes it a little smaller
    better = np.concatenate([rng.geometric(0.02, 200) - 1, [900, 1000, 1200]])
    assert {200, 201, 202} <= set(alarms(sp.g_chart(better, reference_n=200)))  # a very long run of good parts is a signal too
    with pytest.raises(ValueError):
        sp.g_chart([0] * 30)
    with pytest.raises(ValueError):
        sp.g_chart([1, 2, 3])
    with pytest.raises(ValueError):
        sp.g_chart(np.arange(30) + 0.5)


def test_t_chart_uses_the_fitted_weibull_quantiles_and_keeps_its_false_alarm_rate():
    rng = np.random.default_rng(8)
    t = stats.weibull_min.rvs(1.4, scale=50.0, size=30000, random_state=rng)
    r = sp.t_chart(t)
    shape, _, scale = stats.weibull_min.fit(t, floc=0.0)
    d = stats.weibull_min(shape, 0, scale)
    ch = r["charts"][0]
    assert ch["ucl"][0] == pytest.approx(d.ppf(1 - 0.00135), rel=1e-3) and ch["lcl"][0] == pytest.approx(d.ppf(0.00135), rel=1e-3) and ch["center"][0] == pytest.approx(d.ppf(0.5), rel=1e-3)
    assert r["parameters"]["shape"] == pytest.approx(1.4, abs=0.05) and r["parameters"]["scale"] == pytest.approx(50.0, rel=0.03)
    assert len(ch["alarms"]) / t.size == pytest.approx(0.0027, abs=0.0012)
    for bad in ([5.0] * 10, [0.0] * 30, [-1.0] * 30):
        with pytest.raises(ValueError):
            sp.t_chart(bad)


# ------------------------------------------------------------------ percentile, UWMA, delta to target, Levey-Jennings

def test_percentile_chart_limits_are_empirical_quantiles_and_need_a_big_reference():
    rng = np.random.default_rng(9)
    x = rng.lognormal(0, 0.6, 6000)
    r = sp.percentile_chart(x)
    from spc.core.constants import ALPHA_3SIGMA
    lo, cl, hi = np.quantile(x, [ALPHA_3SIGMA / 2, 0.5, 1 - ALPHA_3SIGMA / 2])
    ch = r["charts"][0]
    assert (ch["lcl"][0], ch["center"][0], ch["ucl"][0]) == pytest.approx((lo, cl, hi)) and r["parameters"]["minimum_reference"] == 2000
    assert len(ch["alarms"]) == int(np.sum((x < lo) | (x > hi)))
    with pytest.raises(ValueError):
        sp.percentile_chart(x[:500])
    assert sp.percentile_chart(x[:500], alpha=0.05)["parameters"]["n_reference"] == 500  # 5.4 / 0.05 = 108 values are enough for the 5 % limits


def test_uwma_follows_the_moving_average_and_its_limits_narrow_with_the_span():
    rng = np.random.default_rng(10)
    x = rng.normal(10, 0.5, 80)
    r = sp.uwma_chart(x, 5)
    ch = r["charts"][0]
    assert ch["values"][:3] == pytest.approx([x[0], x[:2].mean(), x[:3].mean()]) and ch["values"][10] == pytest.approx(x[6:11].mean())
    mu, sigma = r["parameters"]["mean"], r["parameters"]["sigma"]
    assert sigma == pytest.approx(np.mean(np.abs(np.diff(x))) / D2)
    assert ch["ucl"][0] == pytest.approx(mu + 3 * sigma) and ch["ucl"][3] == pytest.approx(mu + 3 * sigma / 2) and ch["ucl"][20] == pytest.approx(mu + 3 * sigma / math.sqrt(5))
    shifted = np.concatenate([x[:50], x[50:] + 0.6])  # a shift of 1.2 sigma that a Shewhart chart on single values misses
    out = sp.uwma_chart(shifted, 5, reference_n=50)
    assert len(alarms(out)) >= 5
    for bad in (1, 80, 2.5, True):
        with pytest.raises(ValueError):
            sp.uwma_chart(x, bad)


def test_delta_to_target_chart_uses_the_differences_and_needs_every_target():
    rng = np.random.default_rng(11)
    products = np.array(["A", "B", "C"])[rng.integers(0, 3, 60)]
    targets = {"A": 10.0, "B": 25.0, "C": 3.0}
    x = np.array([targets[p] for p in products]) + rng.normal(0, 0.1, 60)
    r = sp.delta_target_chart(x, products, targets)
    d = x - np.array([targets[p] for p in products])
    sigma = np.mean(np.abs(np.diff(d))) / D2
    ch = r["charts"][0]
    assert ch["values"] == pytest.approx(list(d)) and ch["ucl"][0] == pytest.approx(3 * sigma) and set(ch["center"]) == {0.0}
    x2 = x.copy()
    x2[30] += 1.0
    assert 30 in alarms(sp.delta_target_chart(x2, products, targets))
    with pytest.raises(ValueError):
        sp.delta_target_chart(x, products, {"A": 10.0})


def test_levey_jennings_gives_one_chart_per_stream():
    rng = np.random.default_rng(12)
    streams = np.array(["S1", "S2"] * 40)
    x = np.where(streams == "S1", rng.normal(10, 0.1, 80), rng.normal(12, 0.3, 80))
    r = sp.levey_jennings(x, streams)
    assert [c["stream"] for c in r["charts"]] == ["S1", "S2"]
    s2 = x[streams == "S2"]
    c2 = r["charts"][1]
    assert c2["center"][0] == pytest.approx(s2.mean()) and c2["ucl"][0] == pytest.approx(s2.mean() + 3 * s2.std(ddof=1)) and len(c2["values"]) == 40
    with pytest.raises(ValueError):
        sp.levey_jennings(x[:30], streams[:30])  # 15 values per stream
    with pytest.raises(ValueError):
        sp.levey_jennings(x, streams[:10])


# ------------------------------------------------------------------ API

def test_the_api_draws_each_chart_and_refuses_bad_input():
    client = logged_in_client(make_app())
    rng = np.random.default_rng(13)
    n = rng.integers(100, 200, 30)
    x = rng.binomial(n, 0.06)
    r = client.post("/api/charts/special", json={"kind": "laney-p", "counts": x.tolist(), "sizes": n.tolist()})
    assert r.status_code == 200, r.text
    assert r.json()["parameters"]["sigma_z"] == pytest.approx(sp.laney("p", x, n)["parameters"]["sigma_z"])
    gaps = (rng.geometric(0.03, 60) - 1).tolist()
    assert client.post("/api/charts/special", json={"kind": "g", "values": gaps}).json()["charts"][0]["ucl"][0] > 0
    assert client.post("/api/charts/special", json={"kind": "t", "values": (rng.exponential(10, 60) + 0.01).tolist()}).status_code == 200
    assert client.post("/api/charts/special", json={"kind": "uwma", "values": rng.normal(0, 1, 60).tolist(), "span": 4}).json()["parameters"]["span"] == 4
    stream = ["a", "b"] * 30
    lj = client.post("/api/charts/special", json={"kind": "levey-jennings", "values": rng.normal(0, 1, 60).tolist(), "labels": stream}).json()
    assert [c["stream"] for c in lj["charts"]] == ["a", "b"]
    dt = client.post("/api/charts/special", json={"kind": "delta-target", "values": (rng.normal(0, 1, 30) + 5).tolist(), "labels": ["A"] * 30, "targets": {"A": 5.0}})
    assert dt.status_code == 200 and dt.json()["charts"][0]["center"][0] == 0
    assert client.post("/api/charts/special", json={"kind": "z-u", "counts": [3] * 25, "sizes": [10] * 25}).status_code == 200
    bad = client.post("/api/charts/special", json={"kind": "uwma", "values": rng.normal(0, 1, 60).tolist()})  # the span is missing
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_input"
    assert client.post("/api/charts/special", json={"kind": "percentile", "values": rng.normal(0, 1, 100).tolist()}).status_code == 400  # too short for the quantiles
    assert client.post("/api/charts/special", json={"kind": "nope"}).status_code == 422
    assert client.post("/api/charts/special", json={"kind": "g", "values": []}).status_code == 400


# ------------------------------------------------------------------ transformed values

def test_the_box_cox_chart_follows_the_transformation_and_its_limits_are_not_symmetric():
    rng = np.random.default_rng(21)
    x = rng.lognormal(1.0, 0.4, 200)
    r = sp.transformed_chart(x, "box-cox")
    ch, par = r["charts"][0], r["parameters"]
    y = (x ** par["lambda"] - 1) / par["lambda"]
    assert par["lambda"] == pytest.approx(0.0, abs=0.25) and ch["transformed"] == pytest.approx(y.tolist())
    sigma = np.abs(np.diff(y)).mean() / D2
    u = stats.norm.isf(ALPHA_3SIGMA / 2)
    inv = lambda v: (par["lambda"] * v + 1) ** (1 / par["lambda"])
    assert par["sigma_y"] == pytest.approx(sigma) and ch["ucl"][0] == pytest.approx(inv(y.mean() + u * sigma)) and ch["lcl"][0] == pytest.approx(inv(y.mean() - u * sigma))
    assert ch["ucl"][0] - ch["center"][0] > ch["center"][0] - ch["lcl"][0]  # skewed to the right
    # for a lognormal series the chart signals about as often as the stated risk on both sides, the plain chart much more on the upper side
    big = rng.lognormal(1.0, 0.4, 20000)
    plain = np.mean(np.abs(big - big.mean()) > 3 * np.abs(np.diff(big)).mean() / D2)
    transformed = len(sp.transformed_chart(big, "box-cox")["charts"][0]["alarms"]) / big.size
    assert transformed < 0.006 < 0.01 < plain


def test_the_johnson_chart_handles_any_values_and_the_limits_are_the_back_transformed_ones():
    rng = np.random.default_rng(22)
    x = stats.johnsonsu.rvs(-1.0, 1.5, loc=2.0, scale=1.0, size=400, random_state=rng)
    r = sp.transformed_chart(x, "johnson", alpha=0.01)
    ch, p = r["charts"][0], r["parameters"]
    u = stats.norm.isf(0.005)
    back = lambda y: p["j_location"] + p["j_scale"] * np.sinh((y - p["j_a"]) / p["j_b"])
    assert ch["ucl"][0] == pytest.approx(back(p["center_y"] + u * p["sigma_y"])) and ch["lcl"][0] == pytest.approx(back(p["center_y"] - u * p["sigma_y"]))
    assert len(ch["alarms"]) / x.size < 0.03
    shifted = np.concatenate([x[:200], x[200:] + 2.0 * ch["ucl"][0]])  # the upper tail of a Johnson curve is long: the shift must be large to leave the limits
    assert len(sp.transformed_chart(shifted, "johnson", reference_n=200)["charts"][0]["alarms"]) > 150  # a shift after the reference is found


def test_the_transformed_chart_refuses_what_it_cannot_do():
    rng = np.random.default_rng(23)
    x = rng.lognormal(0, 0.3, 50)
    for bad in (lambda: sp.transformed_chart(x[:10], "box-cox"), lambda: sp.transformed_chart(x - 5, "box-cox"), lambda: sp.transformed_chart(x, "log"),
                lambda: sp.transformed_chart([1.0] * 30, "johnson"), lambda: sp.transformed_chart(x, "box-cox", alpha=0.7)):
        with pytest.raises(ValueError):
            bad()


def test_the_transformed_charts_are_on_the_special_chart_route():
    client = logged_in_client(make_app())
    x = np.random.default_rng(24).lognormal(0.5, 0.3, 80).tolist()
    for kind in ("box-cox", "johnson"):
        r = client.post("/api/charts/special", json={"kind": kind, "values": x})
        assert r.status_code == 200 and r.json()["charts"][0]["name"] == "transformed" and r.json()["parameters"]["method"] == kind
    assert client.post("/api/charts/special", json={"kind": "box-cox", "values": [-1.0] * 30}).status_code == 400

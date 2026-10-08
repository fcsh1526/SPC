"""AIAG MSA manual, 4th edition: bias (independent sample and control chart methods), the range method of the gauge R&R, the signal detection approach and the analytic method."""

import numpy as np
import pytest
from scipy import stats

from spc.core import msa_aiag as AI
from spc.core.msa import MsaError
from spc.validation import aiag_msa as manual

BIAS = manual.BIAS


# ------------------------------------------------------------------ Appendix C

def test_the_d2_star_table_is_the_one_of_the_manual_and_goes_on_by_the_constant_difference():
    assert AI.d2_star(2, 1) == (1.41421, 1.0) and AI.d2_star(5, 20) == (2.33394, 72.7) and AI.d2_star(2, 5) == (1.19105, 4.6) and AI.d2_star(20, 20) == (3.73850, 263.0)
    assert AI.d2_star(10, 3) == (3.11173, 22.6)
    # d2* falls towards d2 (the value for an infinite number of subgroups) as the subgroups get more
    for m in (2, 5, 10, 20):
        series = [AI.d2_star(m, g)[0] for g in range(1, 60)]
        assert all(a > b for a, b in zip(series, series[1:])) and series[-1] > AI.D2_INF[m - 2]
    # beyond g = 20 the degrees of freedom grow by cd per subgroup: the step from 20 to 21 is cd, and d2* joins the table
    for m in (2, 5, 10):
        assert AI.d2_star(m, 21)[1] - AI.d2_star(m, 20)[1] == pytest.approx(AI.CD[m - 2])
        assert abs(AI.d2_star(m, 21)[0] - AI.d2_star(m, 20)[0]) < 1e-3  # d2 (1 + 1 / 4 nu) is close to the table, not equal
    # the constants agree with the exact ones (the table has the infinity value d2 in its last lines)
    from spc.core.constants import d2

    assert [AI.D2_INF[m - 2] for m in (2, 3, 5, 10)] == pytest.approx([d2(m) for m in (2, 3, 5, 10)], abs=1e-4)
    for bad in ((1, 5), (21, 5), (5, 0), (True, 5), (2.0, 5)):
        with pytest.raises(MsaError):
            AI.d2_star(*bad)


# ------------------------------------------------------------------ bias

def test_the_bias_example_of_the_manual_independent_sample_method():
    r = AI.bias_independent(BIAS, 6.00, process_sd=2.5)
    assert r["n"] == 15 and r["mean"] == pytest.approx(6.0067, abs=5e-5) and r["bias"] == pytest.approx(0.0067, abs=5e-5)
    assert r["repeatability"] == pytest.approx(0.2120, abs=5e-5) and r["se"] == pytest.approx(0.0547, abs=5e-5) and r["t"] == pytest.approx(0.12, abs=5e-3) and r["df"] == 14
    assert r["t_critical"] == pytest.approx(2.14479, abs=5e-6) and r["ci"] == pytest.approx([-0.1107, 0.1241], abs=5e-5)
    assert r["bias_acceptable"] and r["ev_pct"] == pytest.approx(8.5, abs=0.05) and r["verdict"] == "pass"  # "%EV = 100 (.2120 / 2.5) = 8.5 %: the repeatability is acceptable"
    assert r["p_value"] == pytest.approx(2 * stats.t.sf(abs(r["t"]), 14))
    # the tolerance divided by 6 is the total variation when the process standard deviation is not known
    t = AI.bias_independent(BIAS, 6.00, tolerance=15.0)
    assert t["tv"] == 2.5 and t["tv_basis"] == "tolerance" and t["ev_pct"] == pytest.approx(r["ev_pct"])
    assert AI.bias_independent(BIAS, 6.00)["verdict"] == "pass" and AI.bias_independent(BIAS, 6.00)["ev_pct"] is None


def test_a_large_repeatability_or_a_bias_that_is_not_zero_does_not_pass():
    rng = np.random.default_rng(1)
    x = 10.3 + rng.normal(0, 0.05, 30)
    off = AI.bias_independent(x, 10.0, process_sd=1.0)
    assert not off["bias_acceptable"] and off["verdict"] == "fail" and off["ci"][0] > 0
    noisy = AI.bias_independent(10 + rng.normal(0, 0.5, 30), 10.0, process_sd=1.0)  # %EV about 50: the statistics of the bias say little
    assert noisy["ev_verdict"] == "fail" and noisy["verdict"] == "fail"
    between = AI.bias_independent(10 + rng.normal(0, 0.2, 30), 10.0, process_sd=1.0)  # %EV about 20
    assert between["ev_verdict"] == "conditional" and between["verdict"] in ("conditional", "fail")
    for bad in (lambda: AI.bias_independent(BIAS[:9], 6.0), lambda: AI.bias_independent([5.0] * 12, 5.0), lambda: AI.bias_independent(BIAS, 6.0, alpha=0.9),
                lambda: AI.bias_independent(BIAS, float("nan")), lambda: AI.bias_independent(BIAS, 6.0, process_sd=-1)):
        with pytest.raises(MsaError):
            bad()


def test_the_bias_example_of_the_control_chart_method():
    """Table III-B 3: g = 20 subgroups of m = 5, reference 6.01, grand average 6.021, repeatability 0.2048 (= average range / d2*), 72.7 degrees of freedom."""
    d2s, _ = AI.d2_star(5, 20)
    r = AI.bias_from_chart(6.021, 0.2048 * d2s, 5, 20, 6.01, process_sd=2.5)
    assert r["bias"] == pytest.approx(0.011, abs=1e-9) and r["repeatability"] == pytest.approx(0.2048) and r["se"] == pytest.approx(0.02048, abs=1e-6)
    assert r["t"] == pytest.approx(0.5371, abs=5e-5) and r["df"] == 72.7 and r["t_critical"] == pytest.approx(1.993, abs=5e-4)
    assert r["ci"] == pytest.approx([-0.0299, 0.0519], abs=2e-4) and r["bias_acceptable"] and r["method"] == "control_chart"
    with pytest.raises(MsaError):
        AI.bias_from_chart(6.0, 0.2, 1, 20, 6.0)  # one reading per subgroup: the independent sample method


def test_the_bias_from_the_readings_of_a_stability_study_and_the_stability_check():
    rng = np.random.default_rng(7)
    a = 10.02 + rng.normal(0, 0.1, (25, 5))
    r = AI.bias_control_chart(a, 10.0, process_sd=0.5)
    ranges = a.max(axis=1) - a.min(axis=1)
    ref = AI.bias_from_chart(float(a.mean()), float(ranges.mean()), 5, 25, 10.0, process_sd=0.5)
    assert r["stable"] and r["points_outside"] == {"xbar": 0, "range": 0}
    for k in ("bias", "repeatability", "t", "df", "ci", "ev_pct"):
        assert r[k] == pytest.approx(ref[k])
    shifted = a.copy()
    shifted[12:] += 0.6  # a shift of the measurement system in the middle of the study
    u = AI.bias_control_chart(shifted, 10.0, process_sd=0.5)
    assert not u["stable"] and u["points_outside"]["xbar"] > 0 and u["verdict"] == "fail"  # the bias of an unstable system is not evaluated
    for bad in (a[:4], a[:, :1], np.full((10, 3), 5.0), [[1, 2], [3]]):
        with pytest.raises(MsaError):
            AI.bias_control_chart(bad, 10.0)


# ------------------------------------------------------------------ the range method

def test_the_range_method_example_of_the_manual():
    r = AI.range_method([0.85, 0.75, 1.00, 0.45, 0.50], [0.80, 0.70, 0.95, 0.55, 0.60], process_sd=0.0777)
    assert r["ranges"] == pytest.approx([0.05, 0.05, 0.05, 0.10, 0.10]) and r["average_range"] == pytest.approx(0.07) and r["d2_star"] == 1.19105
    assert r["grr"] == pytest.approx(0.0588, abs=5e-5) and r["pct_process"] == pytest.approx(75.7, abs=0.1) and r["verdict"] == "fail" and r["basis"] == "process"  # "in need of improvement"
    t = AI.range_method([0.85, 0.75, 1.00, 0.45, 0.50], [0.80, 0.70, 0.95, 0.55, 0.60], tolerance=10.0)
    assert t["pct_tol"] == pytest.approx(100 * 6 * t["grr"] / 10.0) and t["basis"] == "tolerance" and t["verdict"] == "pass"
    for bad in (lambda: AI.range_method([1, 2, 3, 4], [1, 2, 3, 5], process_sd=1), lambda: AI.range_method([1, 2, 3, 4, 5], [1, 2, 3, 4, 5], process_sd=1),
                lambda: AI.range_method([1, 2, 3, 4, 5], [1, 2, 3, 4, 6]), lambda: AI.range_method([1, 2, 3, 4, 5], [1, 2, 3, 4], process_sd=1)):
        with pytest.raises(MsaError):
            bad()


# ------------------------------------------------------------------ signal detection

def codes_of(ratings):
    out = []
    for i in range(50):
        votes = [ratings[x][t][i] for x in "ABC" for t in range(3)]
        out.append("+" if all(votes) else "-" if not any(votes) else "x")
    return out


def test_the_signal_detection_example_of_the_manual():
    ratings, _ = manual._study()
    codes = codes_of(ratings)
    assert (codes.count("+"), codes.count("-"), codes.count("x")) == (28, 11, 11)
    r = AI.signal_detection(manual.REF_VALUES, codes, 0.450, 0.550)
    assert r["d_lsl"] == pytest.approx(0.470832 - 0.446697, abs=1e-9) and r["d_usl"] == pytest.approx(0.566152 - 0.542704, abs=1e-9)
    assert r["d"] == pytest.approx(0.0237915, abs=1e-9) and r["pct"] == pytest.approx(23.79, abs=0.01) and r["basis"] == "tolerance"
    assert (r["last_rejected_below"], r["first_accepted_above"], r["last_accepted_below_usl"], r["first_rejected_above"]) == (0.446697, 0.470832, 0.542704, 0.566152)
    # a process that is better than the tolerance (6 sigma smaller) is the more restrictive: the system is compared with it
    p = AI.signal_detection(manual.REF_VALUES, codes, 0.450, 0.550, process_sd=0.01)
    assert p["basis"] == "process" and p["pct"] == pytest.approx(100 * r["d"] / 0.06)
    assert AI.signal_detection(manual.REF_VALUES, codes, 0.450, 0.550, process_sd=0.0777)["basis"] == "tolerance"  # Ppk 0.5: compare with the tolerance
    for bad in (lambda: AI.signal_detection(manual.REF_VALUES[:10], codes[:10], 0.45, 0.55), lambda: AI.signal_detection(manual.REF_VALUES, codes, 0.55, 0.45),
                lambda: AI.signal_detection(manual.REF_VALUES, ["?"] + codes[1:], 0.45, 0.55), lambda: AI.signal_detection(manual.REF_VALUES, codes[:-1], 0.45, 0.55),
                lambda: AI.signal_detection(manual.REF_VALUES, ["+" if v != "-" else "x" for v in codes], 0.45, 0.55)):  # nothing rejected by all
        with pytest.raises(MsaError):
            bad()


# ------------------------------------------------------------------ analytic method

XT = [-0.016, -0.015, -0.014, -0.013, -0.012, -0.011, -0.0105, -0.010, -0.008, -0.006, -0.004, -0.002]
ACCEPTS = [0, 1, 3, 5, 8, 16, 18, 20, 20, 20, 20, 20]


def test_the_analytic_method_example_of_the_manual():
    r = AI.analytic_method(XT, ACCEPTS, -0.010, "lower", tolerance=0.020)
    assert [p["pac"] for p in r["points"]][:9] == pytest.approx([0.025, 0.075, 0.175, 0.275, 0.425, 0.775, 0.875, 0.975, 1.0])  # the table of the manual
    assert r["points"][-1]["pac"] == 1.0 and r["between"] == 6
    # the manual reads the line off a plot drawn by eye (bias 0.0023, repeatability 0.00142, t 9.84); the least squares line is close to it
    assert r["x_at_0_5"] == pytest.approx(-0.0123, abs=3e-4) and r["bias"] == pytest.approx(0.0023, abs=3e-4) and r["repeatability"] == pytest.approx(0.00142, abs=2e-4)
    assert r["grr_range"] == pytest.approx(0.0085, abs=1.2e-3) and r["t"] > 9 and r["bias_significant"] and r["verdict"] == "fail" and r["t_critical"] == 2.093
    assert r["pct_tol"] == pytest.approx(100 * r["grr_range"] / 0.020)
    # the numbers follow the formulas: the repeatability is the 99 % range / 1.08 / 5.15 and t = 6.078 |bias| / repeatability
    assert r["repeatability"] == pytest.approx(r["range_99"] / 1.08 / 5.15) and r["t"] == pytest.approx(6.078 * abs(r["bias"]) / r["repeatability"])
    z = stats.norm.ppf([p["pac"] for p in r["points"] if 0 < p["pac"] < 1])
    slope, icpt = np.polyfit(z, [p["reference"] for p in r["points"] if 0 < p["pac"] < 1], 1)
    assert r["x_at_0_5"] == pytest.approx(icpt) and r["range_99"] == pytest.approx(2 * slope * stats.norm.ppf(0.995))


def test_the_upper_limit_is_the_mirror_image_and_a_gauge_without_bias_is_not_significant():
    lower = AI.analytic_method(XT, ACCEPTS, -0.010, "lower")
    upper = AI.analytic_method([-x for x in XT], ACCEPTS, 0.010, "upper")
    for k in ("bias", "repeatability", "t", "grr_range"):
        assert upper[k] == pytest.approx(lower[k])
    assert upper["x_at_0_5"] == pytest.approx(-lower["x_at_0_5"])
    # a gauge that accepts half of the parts exactly at the limit has no bias
    from scipy import stats as st

    x = np.linspace(-0.0125, -0.0075, 14)
    sd = 0.0006
    a = np.round(20 * st.norm.cdf((x + 0.010) / sd)).astype(int)
    a[0], a[-1] = 0, 20
    ok = AI.analytic_method(x, a, -0.010, "lower")
    assert abs(ok["bias"]) < 3e-4 and not ok["bias_significant"] and ok["verdict"] == "pass"


def test_the_analytic_method_wants_the_data_of_the_manual_criteria():
    bad_cases = [
        (XT, [1] + ACCEPTS[1:], "smallest"),  # the smallest part must have no accepts
        (XT, ACCEPTS[:-1] + [19], "smallest"),  # the largest part must have all
        (XT[:6], [0, 1, 3, 5, 8, 20], "at least 8"),
        (XT, [0, 0, 0, 0, 0, 0, 5, 20, 20, 20, 20, 20], "1 to 19"),  # too few parts with 1 to 19 accepts
    ]
    for x, a, word in bad_cases:
        with pytest.raises(MsaError, match=word):
            AI.analytic_method(x, a, -0.010)
    with pytest.raises(MsaError):
        AI.analytic_method(XT, ACCEPTS, -0.010, m=10)  # the constants are for 20 trials
    with pytest.raises(MsaError):
        AI.analytic_method(XT, [0, 1, 3, 5, 8, 16, 18, 20, 20, 20, 20, 21], -0.010)
    with pytest.raises(MsaError):
        AI.analytic_method(XT, ACCEPTS, -0.010, "middle")
    with pytest.raises(MsaError):
        AI.analytic_method(XT[:-1] + [XT[-2]], ACCEPTS, -0.010)  # a reference value twice


# ------------------------------------------------------------------ the system, the gate and the API

from datetime import date  # noqa: E402

from tests.conftest import logged_in_client, make_app  # noqa: E402
from tests.test_api import err  # noqa: E402


@pytest.fixture
def env():
    app = make_app()
    return app, logged_in_client(app, "eng")


def system(eng, kind="variable", tolerance=6.0, **extra):
    record = {"name": f"{kind} system", "characteristic": "bore", "unit": "mm", "kind": kind, **extra}
    if kind == "variable":
        record.update(resolution=0.01, tolerance=tolerance)
    r = eng.post("/api/msa", json={"record": record})
    assert r.status_code == 200, r.text
    return r.json()["system"]["id"]


def study(eng, sid, kind, input_):
    return eng.post(f"/api/msa/{sid}/studies", json={"kind": kind, "date": date.today().isoformat(), "input": input_})


def test_a_bias_study_is_an_optional_check_that_blocks_when_the_bias_is_not_zero(env):
    app, eng = env
    sid = system(eng, tolerance=15.0)
    assert eng.get(f"/api/msa/{sid}").json()["gate"]["checks"]["bias"]["result"] == "not_done"
    r = study(eng, sid, "bias", {"values": BIAS, "reference": 6.0, "process_sd": 2.5})
    assert r.status_code == 200, r.text
    c = r.json()["gate"]["checks"]["bias"]
    assert c["result"] == "pass" and c["kind"] == "bias" and c["bias"] == pytest.approx(0.0067, abs=5e-5) and c["ev_pct"] == pytest.approx(8.5, abs=0.05)
    assert r.json()["system"]["studies"][0]["result"]["ci"] == pytest.approx([-0.1107, 0.1241], abs=5e-5)
    # without the process standard deviation the tolerance of the system gives the total variation (tolerance / 6)
    rng = np.random.default_rng(3)
    off = study(eng, sid, "bias", {"values": (6.4 + rng.normal(0, 0.05, 20)).tolist(), "reference": 6.0})
    assert off.json()["system"]["studies"][1]["result"]["tv_basis"] == "tolerance"
    g = off.json()["gate"]
    assert g["checks"]["bias"]["result"] == "fail" and "bias" in g["blocking"] and g["status"] == "block"  # the newest bias study counts
    assert eng.put(f"/api/msa/{sid}/waivers/bias", json={"reason": "the correction is applied in the program, see deviation 4"}).json()["gate"]["checks"]["bias"]["effective"] == "waived"
    # the control chart method is a bias study as well
    chart = study(eng, sid, "bias_chart", {"subgroups": (6.0 + rng.normal(0, 0.1, (20, 5))).tolist(), "reference": 6.0})
    assert chart.status_code == 200, chart.text
    assert chart.json()["gate"]["checks"]["bias"]["kind"] == "bias_chart" and chart.json()["system"]["studies"][2]["result"]["method"] == "control_chart"


def test_the_range_method_is_kept_with_the_studies_but_does_not_prove_the_gauge_r_and_r(env):
    app, eng = env
    sid = system(eng, tolerance=2.0)
    r = study(eng, sid, "grr_range", {"a": [0.85, 0.75, 1.00, 0.45, 0.50], "b": [0.80, 0.70, 0.95, 0.55, 0.60], "process_sd": 0.0777})
    assert r.status_code == 200, r.text
    res = r.json()["system"]["studies"][0]["result"]
    assert res["grr"] == pytest.approx(0.0588, abs=5e-5) and res["pct_process"] == pytest.approx(75.7, abs=0.1) and r.json()["system"]["studies"][0]["verdict"] == "fail"
    g = r.json()["gate"]
    assert g["checks"]["grr"]["result"] == "missing" and g["checks"]["validity"]["result"] == "missing" and g["uncertainty"] is None  # a quick check is no proof
    assert err(study(eng, sid, "grr_range", {"a": [1, 2, 3], "b": [1, 2, 4], "process_sd": 1}))["code"] == "study_not_evaluable"
    assert err(study(eng, sid, "grr_range", {"a": [1, 2, 3, 4, 5]}))["code"] == "invalid_input"


def attribute_input():
    ratings, _ = manual._study()
    # the layout of the table: one list of decisions per trial for each appraiser
    return {"reference": manual.REF_VALUES, "results": ratings, "lower": 0.45, "upper": 0.55}


def test_signal_detection_and_the_analytic_method_are_optional_checks_of_an_attribute_system(env):
    app, eng = env
    sid = system(eng, "attribute")
    c = eng.get(f"/api/msa/{sid}").json()["gate"]["checks"]
    assert c["attribute_aiag"]["result"] == "not_done" and c["bias"]["result"] == "not_needed" and c["attribute"]["result"] == "missing"
    r = study(eng, sid, "signal_detection", attribute_input())
    assert r.status_code == 200, r.text
    s = r.json()["system"]["studies"][0]
    assert s["result"]["d"] == pytest.approx(0.0237915, abs=1e-8) and s["result"]["pct"] == pytest.approx(23.79, abs=0.01) and s["verdict"] == "conditional"
    assert r.json()["gate"]["checks"]["attribute_aiag"]["result"] == "warn"
    a = study(eng, sid, "analytic", {"reference": XT, "accepts": ACCEPTS, "limit": -0.010})
    assert a.status_code == 200, a.text
    g = a.json()["gate"]
    assert g["checks"]["attribute_aiag"]["result"] == "fail" and "attribute_aiag" in g["blocking"]  # the bias is significant: the worst of the two counts
    assert a.json()["system"]["studies"][1]["result"]["bias_significant"] is True
    assert err(study(eng, sid, "analytic", {"reference": XT, "accepts": [1] + ACCEPTS[1:], "limit": -0.010}))["code"] == "study_not_evaluable"
    assert err(study(eng, sid, "bias", {"values": BIAS, "reference": 6.0}))["code"] == "invalid_input"  # a variable study on an attribute system
    var = system(eng, tolerance=3.0)
    assert err(study(eng, var, "signal_detection", attribute_input()))["code"] == "invalid_input"

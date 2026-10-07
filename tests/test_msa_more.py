"""Linearity and bias, nested (destructive) gauge R&R and the uncertainty budget of a measurement system."""

import math
from datetime import date

import numpy as np
import pytest
from scipy import stats

from spc.core import msa_more as mm
from spc.core.msa import MsaError
from tests.conftest import logged_in_client, make_app
from tests.test_api import err


# ------------------------------------------------------------------ linearity and bias

REFS = [2.0, 4.0, 6.0, 8.0, 10.0]


def readings(seed=1, slope=0.0, offset=0.0, sd=0.1, m=12):
    rng = np.random.default_rng(seed)
    return [[x + offset + slope * x + rng.normal(0, sd) for _ in range(m)] for x in REFS]


def test_the_regression_of_the_bias_follows_the_textbook_formulas():
    y = np.array(readings(3, slope=-0.03, offset=0.15))
    r = mm.linearity(REFS, y, 6.0)
    x = np.repeat(REFS, 12)
    b = (y - np.array(REFS)[:, None]).ravel()
    ref = stats.linregress(x, b)
    assert r["slope"] == pytest.approx(ref.slope) and r["intercept"] == pytest.approx(ref.intercept) and r["r2"] == pytest.approx(ref.rvalue ** 2)
    assert r["slope_se"] == pytest.approx(ref.stderr) and r["slope_p"] == pytest.approx(ref.pvalue) and r["intercept_se"] == pytest.approx(ref.intercept_stderr)
    assert r["pct_linearity"] == pytest.approx(100 * abs(ref.slope)) and r["linearity"] == pytest.approx(6.0 * abs(ref.slope))
    assert r["average_bias"] == pytest.approx(b.mean()) and r["pct_bias"] == pytest.approx(100 * abs(b.mean()) / 6.0)
    # repeatability is the pooled standard deviation within the parts, the bias of a part is tested with it
    s_r = math.sqrt(sum(((row - row.mean()) ** 2).sum() for row in y) / (60 - 5))
    assert r["repeatability"] == pytest.approx(s_r) and r["df_repeatability"] == 55
    p0 = r["part_results"][0]
    assert p0["bias"] == pytest.approx(y[0].mean() - 2.0) and p0["t"] == pytest.approx(p0["bias"] / (s_r / math.sqrt(12)))
    assert p0["p_value"] == pytest.approx(2 * stats.t.sf(abs(p0["t"]), 55)) and p0["ci"][0] < p0["bias"] < p0["ci"][1]
    assert r["average_bias_t"] == pytest.approx(b.mean() / (s_r / math.sqrt(60)))


def test_the_band_decides_and_a_bias_that_grows_with_the_size_fails():
    ok = mm.linearity(REFS, readings(1))
    assert ok["verdict"] == "pass" and ok["zero_in_band"] and ok["bias_significant_parts"] == [] and ok["linearity"] is None and ok["pct_bias"] is None
    grows = mm.linearity(REFS, readings(2, slope=0.05))
    assert grows["verdict"] == "fail" and not grows["zero_in_band"] and grows["slope_p"] < 0.001 and grows["pct_linearity"] == pytest.approx(5.0, abs=1.0)
    constant = mm.linearity(REFS, readings(4, offset=0.3))
    assert constant["verdict"] == "fail" and constant["intercept_p"] < 0.001 and constant["average_bias"] == pytest.approx(0.3, abs=0.05)
    # the band is a hyperbola around the line: wider at the ends than in the middle, and the line is inside it
    b = ok["band"]
    assert all(p["lower"] < p["fit"] < p["upper"] for p in b)
    assert b[0]["upper"] - b[0]["lower"] > min(p["upper"] - p["lower"] for p in b)


def test_a_linearity_study_is_checked():
    y = readings(5)
    for bad in (lambda: mm.linearity(REFS[:4], y[:4]), lambda: mm.linearity(REFS, [r[:1] for r in y]), lambda: mm.linearity(REFS, y[:4]),
                lambda: mm.linearity([1, 1, 2, 3, 4], y), lambda: mm.linearity(REFS, [[1.0] * 12] * 5), lambda: mm.linearity(REFS, y, process_variation=-1),
                lambda: mm.linearity(REFS, y, alpha=0.7), lambda: mm.linearity(REFS, [[float("nan")] * 12] * 5)):
        with pytest.raises(MsaError):
            bad()


# ------------------------------------------------------------------ nested gauge R&R

def nested_data(seed, o=3, p=5, r=2, s_op=0.4, s_part=1.0, s_e=0.2):
    rng = np.random.default_rng(seed)
    return (10 + rng.normal(0, s_op, (o, 1, 1)) + rng.normal(0, s_part, (o, p, 1)) + rng.normal(0, s_e, (o, p, r)))


def test_the_nested_variance_components_are_the_textbook_ones():
    a = nested_data(1)
    o, p, r = a.shape
    res = mm.grr_nested(a, 6.0)
    part_m, op_m, g = a.mean(axis=2), a.mean(axis=(1, 2)), a.mean()
    ms_op = p * r * ((op_m - g) ** 2).sum() / (o - 1)
    ms_part = r * ((part_m - op_m[:, None]) ** 2).sum() / (o * (p - 1))
    ms_e = ((a - part_m[:, :, None]) ** 2).sum() / (o * p * (r - 1))
    ev2, av2, pv2 = ms_e, max(0, (ms_op - ms_part) / (p * r)), max(0, (ms_part - ms_e) / r)
    assert res["sigma"]["ev"] == pytest.approx(math.sqrt(ev2)) and res["sigma"]["av"] == pytest.approx(math.sqrt(av2)) and res["sigma"]["pv"] == pytest.approx(math.sqrt(pv2))
    assert res["sigma"]["grr"] == pytest.approx(math.sqrt(ev2 + av2)) and res["sigma"]["tv"] == pytest.approx(math.sqrt(ev2 + av2 + pv2))
    assert res["pct_tol"] == pytest.approx(100 * 6 * math.sqrt(ev2 + av2) / 6.0) and res["ndc"] == pytest.approx(1.41 * math.sqrt(pv2) / math.sqrt(ev2 + av2))
    assert res["parts"] == 15 and res["operators"] == 3 and res["trials"] == 2 and "nested" in res["notes"]
    assert [row["level"] for row in res["table"]] == ["operator", "part", "error"] and res["table"][2]["df"] == 15


def test_the_nested_study_recovers_the_components_on_average_and_has_the_same_verdict_rules():
    est = np.array([[mm.grr_nested(nested_data(100 + i, o=10, p=4), None)["sigma"][k] ** 2 for k in ("ev", "av", "pv")] for i in range(300)]).mean(axis=0)
    assert est == pytest.approx([0.04, 0.16, 1.0], rel=0.25)
    good = mm.grr_nested(nested_data(2, s_op=0.02, s_part=1.0, s_e=0.02), 12.0)
    assert good["verdict"] == "pass" and good["basis"] == "tolerance"
    bad = mm.grr_nested(nested_data(3, s_op=0.5, s_part=0.3, s_e=0.5), 3.0)
    assert bad["verdict"] == "fail"
    assert mm.grr_nested(nested_data(2), None)["basis"] == "total_variation"
    for wrong in (lambda: mm.grr_nested(np.ones((1, 5, 2)), 1.0), lambda: mm.grr_nested(np.ones((3, 5, 1)), 1.0), lambda: mm.grr_nested(np.ones((3, 5, 2)), 1.0),
                  lambda: mm.grr_nested([[1, 2], [3, 4]], 1.0)):
        with pytest.raises(MsaError):
            wrong()


# ------------------------------------------------------------------ uncertainty budget

def test_the_budget_combines_the_components_as_iso_14253_and_the_gum_say():
    comps = [{"name": "calibration", "type": "B", "value": 0.5, "distribution": "normal_k2"},
             {"name": "resolution", "type": "B", "value": 0.05, "distribution": "rectangular"},
             {"name": "repeatability", "type": "A", "value": 0.08, "distribution": "standard", "dof": 19},
             {"name": "temperature", "type": "B", "value": 0.2, "distribution": "triangular", "sensitivity": 0.5, "dof": 50}]
    r = mm.budget(comps, 4.0, k=2.0)
    u = [0.25, 0.05 / math.sqrt(3), 0.08, 0.2 / math.sqrt(6)]
    c = [0.25, 0.05 / math.sqrt(3), 0.08, 0.5 * 0.2 / math.sqrt(6)]
    uc = math.sqrt(sum(v * v for v in c))
    assert [row["u"] for row in r["components"]] == pytest.approx(u) and r["u_c"] == pytest.approx(uc) and r["U"] == pytest.approx(2 * uc)
    assert sum(row["share"] for row in r["components"]) == pytest.approx(1.0) and r["largest"] == "calibration"
    # Welch-Satterthwaite: components with infinite degrees of freedom do not enter the denominator
    assert r["nu_eff"] == pytest.approx(uc ** 4 / (0.08 ** 4 / 19 + c[3] ** 4 / 50))
    assert r["k_95"] == pytest.approx(stats.t.ppf(0.975, r["nu_eff"])) and r["q_ms"] == pytest.approx(100 * 2 * 2 * uc / 4.0)
    only_b = mm.budget(comps[:2], 4.0)
    assert only_b["nu_eff"] is None and only_b["k_95"] == pytest.approx(1.95996, abs=1e-4)


def test_the_budget_verdict_and_the_decision_zones():
    comp = [{"name": "all", "value": 0.1, "distribution": "standard"}]
    ok = mm.budget(comp, 4.0, lsl=9.0, usl=11.0)  # U = 0.2, 2U/T = 10 %
    assert ok["verdict"] == "pass" and ok["q_ms"] == pytest.approx(10.0)
    assert ok["zones"]["acceptance"] == pytest.approx([9.2, 10.8]) and ok["zones"]["rejection"] == pytest.approx([8.8, 11.2])
    assert mm.budget(comp, 1.0)["verdict"] == "fail" and mm.budget(comp, 2.0)["verdict"] == "conditional"
    assert mm.budget(comp, 1.0, lsl=9.0, usl=9.3)["zones"]["acceptance_exists"] is False
    for bad in ([], [{"name": "", "value": 1}], [{"name": "a", "value": -1}], [{"name": "a", "value": 1, "distribution": "x"}], [{"name": "a", "value": 0}],
                [{"name": "a", "value": 1, "dof": 0}], [{"name": "a", "value": 1, "extra": 2}], [{"name": "a", "value": True}], "text"):
        with pytest.raises(MsaError):
            mm.budget(bad, 4.0)
    with pytest.raises(MsaError):
        mm.budget(comp, None)
    with pytest.raises(MsaError):
        mm.budget(comp, 4.0, k=9)


# ------------------------------------------------------------------ the system, the gate and the API

@pytest.fixture
def env():
    app = make_app()
    return app, logged_in_client(app, "eng")


def system(eng, tolerance=6.0, **extra):
    r = eng.post("/api/msa", json={"record": {"name": "CMM", "characteristic": "bore", "unit": "mm", "resolution": 0.01, "tolerance": tolerance, **extra}})
    assert r.status_code == 200, r.text
    return r.json()["system"]["id"]


def post_study(eng, sid, kind, input_):
    return eng.post(f"/api/msa/{sid}/studies", json={"kind": kind, "date": date.today().isoformat(), "input": input_})


def test_a_nested_study_stands_in_for_the_crossed_one_in_the_gate(env):
    app, eng = env
    sid = system(eng)
    assert eng.get(f"/api/msa/{sid}").json()["gate"]["checks"]["grr"]["result"] == "missing"
    r = post_study(eng, sid, "grr_nested", {"data": nested_data(2, s_op=0.02, s_part=1.0, s_e=0.02).tolist()})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["gate"]["checks"]["grr"]["result"] == "pass" and v["gate"]["checks"]["validity"]["result"] == "pass" and v["gate"]["uncertainty"]["from_study"] == 1
    assert v["system"]["studies"][0]["result"]["nested"] is True
    # a later crossed study replaces it as the newest evidence
    crossed = np.random.default_rng(9).normal(10, 1, (10, 1, 1)) + np.random.default_rng(10).normal(0, 0.02, (10, 3, 3))
    r2 = post_study(eng, sid, "grr", {"data": crossed.tolist()})
    assert r2.status_code == 200 and r2.json()["gate"]["uncertainty"]["from_study"] == 2


def test_linearity_and_budget_are_optional_checks_that_block_only_when_they_fail(env):
    app, eng = env
    sid = system(eng)
    c = eng.get(f"/api/msa/{sid}").json()["gate"]["checks"]
    assert c["linearity"]["result"] == "not_done" and c["budget"]["result"] == "not_done"
    good = post_study(eng, sid, "linearity", {"reference": REFS, "values": readings(1), "process_variation": 6.0})
    assert good.status_code == 200 and good.json()["gate"]["checks"]["linearity"]["result"] == "pass"
    assert good.json()["system"]["studies"][0]["result"]["linearity"] is not None
    bad = post_study(eng, sid, "linearity", {"reference": REFS, "values": readings(2, slope=0.05)})
    g = bad.json()["gate"]
    assert g["checks"]["linearity"]["result"] == "fail" and "linearity" in g["blocking"] and g["status"] == "block"
    waived = eng.put(f"/api/msa/{sid}/waivers/linearity", json={"reason": "customer accepts the bias, see deviation 12"})
    assert waived.json()["gate"]["checks"]["linearity"]["effective"] == "waived"
    b = post_study(eng, sid, "budget", {"components": [{"name": "calibration", "value": 0.1}, {"name": "repeatability", "value": 0.05, "type": "A", "dof": 24}]})
    assert b.status_code == 200, b.text
    chk = b.json()["gate"]["checks"]["budget"]
    assert chk["result"] == "pass" and chk["U"] == pytest.approx(2 * math.sqrt(0.1 ** 2 + 0.05 ** 2)) and chk["q"] == pytest.approx(100 * 2 * chk["U"] / 6.0)
    loose = post_study(eng, sid, "budget", {"components": [{"name": "calibration", "value": 1.0}]})
    assert loose.json()["gate"]["checks"]["budget"]["result"] == "fail"  # 2U/T = 66 %


def test_the_new_studies_refuse_wrong_input_and_the_policy_has_the_budget_limits(env):
    app, eng = env
    sid = system(eng)
    assert err(post_study(eng, sid, "linearity", {"values": readings(1)}))["code"] == "invalid_input"
    assert err(post_study(eng, sid, "budget", {"components": [], "k": 2}))["code"] == "invalid_input"
    assert err(post_study(eng, sid, "budget", {"components": []}))["code"] == "study_not_evaluable"
    assert err(post_study(eng, sid, "grr_nested", {"data": [[[1, 1]] * 3] * 3}))["code"] == "study_not_evaluable"
    no_tol = eng.post("/api/msa", json={"record": {"name": "No tolerance", "resolution": 0.01}}).json()["system"]["id"]
    assert err(post_study(eng, no_tol, "budget", {"components": [{"name": "a", "value": 1}]}))["code"] == "study_not_evaluable"
    attribute = eng.post("/api/msa", json={"record": {"name": "Go/no-go", "kind": "attribute"}}).json()["system"]["id"]
    assert err(post_study(eng, attribute, "linearity", {"reference": REFS, "values": readings(1)}))["code"] == "invalid_input"
    assert eng.post("/api/msa", json={"record": {"name": "Odd policy", "policy": {"budget_pass": 40, "budget_conditional": 30}}}).status_code == 400
    ok = eng.put(f"/api/msa/{sid}", json={"record": {"name": "CMM", "characteristic": "bore", "unit": "mm", "resolution": 0.01, "tolerance": 6.0, "policy": {"budget_pass": 40, "budget_conditional": 80}}})
    assert ok.status_code == 200 and ok.json()["system"]["policy"]["budget_pass"] == 40

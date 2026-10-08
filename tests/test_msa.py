import math
from datetime import date, timedelta

import numpy as np
import pytest

from spc.core import msa
from spc.data import Dataset
from spc.msa import gate
from tests.conftest import PASSWORD, logged_in_client, make_app
from tests.test_api import err

# AIAG MSA, 4th edition: 10 parts, 3 appraisers, 3 trials. data[part][operator][trial]
A = [[.29, .41, .64], [-.56, -.68, -.58], [1.34, 1.17, 1.27], [.47, .50, .64], [-.80, -.92, -.84], [.02, -.11, -.21], [.59, .75, .66], [-.31, -.20, -.17], [2.26, 1.99, 2.01], [-1.36, -1.25, -1.31]]
B = [[.08, .25, .07], [-.47, -1.22, -.68], [1.19, .94, 1.34], [.01, 1.03, .20], [-.56, -1.20, -1.28], [-.20, .22, .06], [.47, .55, .83], [-.63, .08, -.34], [1.80, 2.12, 2.19], [-1.68, -1.62, -1.50]]
C = [[.04, -.11, -.15], [-1.38, -1.13, -.96], [.88, 1.09, .67], [.14, .20, .11], [-1.46, -1.07, -1.45], [-.29, -.67, -.49], [.02, .01, .21], [-.46, -.56, -.49], [1.77, 1.45, 1.87], [-1.49, -1.77, -2.16]]
AIAG = np.stack([A, B, C], axis=1)


def grr_data(rng, parts=10, ops=3, trials=3, s_part=1.0, s_rep=0.05, s_op=0.02):
    p = rng.normal(0, s_part, (parts, 1, 1))
    o = rng.normal(0, s_op, (1, ops, 1))
    return (10 + p + o + rng.normal(0, s_rep, (parts, ops, trials))).tolist()


# ------------------------------------------------------------------ the studies

def test_type1_follows_the_vda_5_formulas():
    rng = np.random.default_rng(1)
    x = 10.0 + rng.normal(0.001, 0.02, 50)
    r = msa.type1(x, 10.0, 1.0)
    s = x.std(ddof=1)
    assert r["cg"] == pytest.approx(0.2 * 1.0 / (4 * s)) and r["cgk"] == pytest.approx((0.1 - abs(x.mean() - 10.0)) / (2 * s)) and r["spread"] == 4  # VDA 5, 5.2.2.1
    r6 = msa.type1(x, 10.0, 1.0, spread=6)  # the variant of company guidelines: Cg 1.33 is then Q_MS 10 %
    assert r6["cg"] == pytest.approx(0.2 * 1.0 / (6 * s)) and r6["cgk"] == pytest.approx((0.1 - abs(x.mean() - 10.0)) / (3 * s))
    with pytest.raises(msa.MsaError):
        msa.type1(x, 10.0, 1.0, spread=5)
    assert r["verdict"] == "pass" and r["n"] == 50 and r["bias"] == pytest.approx(x.mean() - 10.0)
    assert msa.type1(10.0 + rng.normal(0, 0.1, 50), 10.0, 1.0)["verdict"] == "fail"  # Cg 0.5
    assert msa.type1(10.09 + rng.normal(0, 0.01, 50), 10.0, 1.0)["verdict"] == "fail"  # a good gauge with a bias: Cg is fine, Cgk is not
    for bad in (lambda: msa.type1(x[:10], 10.0, 1.0), lambda: msa.type1([5.0] * 30, 5.0, 1.0), lambda: msa.type1(x, 10.0, 0.0)):
        with pytest.raises(msa.MsaError):
            bad()


def test_the_gauge_rr_of_the_aiag_example():
    r = msa.grr(AIAG, 6.0)
    # the repeatability from the ranges (R-bar x K1) is the published 0.20188; the analysis of variance is close to it
    assert np.ptp(AIAG, axis=2).mean() * 0.5908 == pytest.approx(0.20188, abs=2e-4)
    assert r["sigma"]["ev"] == pytest.approx(0.2, abs=0.005) and r["sigma"]["av"] == pytest.approx(0.227, abs=0.01)
    assert r["interaction_p"] == pytest.approx(0.974, abs=0.003) and r["pooled_interaction"] is True  # no interaction: pooled with the error
    assert r["pct_tv"] == pytest.approx(27.9, abs=0.5) and r["ndc"] == pytest.approx(4.86, abs=0.1)
    assert r["verdict"] == "fail" and r["basis"] == "tolerance"  # 6 sigma / 6.0 = 30.2 %
    assert msa.grr(AIAG, 8.0)["verdict"] == "conditional"  # 22.7 % of a tolerance of 8; ndc below 5
    assert msa.grr(AIAG, None)["basis"] == "total_variation" and "no_tolerance" in msa.grr(AIAG, None)["notes"]


def test_the_gauge_rr_recovers_the_components_and_the_pieces_add_up():
    rng = np.random.default_rng(5)
    estimates = [msa.grr(grr_data(rng, 10, 3, 3, 1.0, 0.1, 0.1), 12.0)["sigma"] for _ in range(80)]
    assert np.mean([e["ev"] for e in estimates]) == pytest.approx(0.1, rel=0.05)
    assert np.mean([e["av"] for e in estimates]) == pytest.approx(0.1, rel=0.25)
    assert np.mean([e["pv"] for e in estimates]) == pytest.approx(1.0, rel=0.1)
    r = msa.grr(grr_data(rng), 12.0)
    s = r["sigma"]
    assert s["grr"] ** 2 == pytest.approx(s["ev"] ** 2 + s["av"] ** 2 + s["interaction"] ** 2) and s["tv"] ** 2 == pytest.approx(s["grr"] ** 2 + s["pv"] ** 2)
    assert r["verdict"] == "pass" and r["pct_tol"] < 10 and r["ndc"] > 5
    coarse = msa.grr(grr_data(rng, s_rep=0.6, s_op=0.4), 6.0)
    assert coarse["verdict"] == "fail"
    for bad in (lambda: msa.grr(np.ones((10, 3, 3)), 6.0), lambda: msa.grr(np.zeros((3, 3, 3)), 6.0), lambda: msa.grr([[1, 2], [3]], 6.0)):
        with pytest.raises(msa.MsaError):
            bad()


def test_an_interaction_between_part_and_operator_is_kept_when_it_is_there():
    rng = np.random.default_rng(6)
    d = np.array(grr_data(rng, 10, 3, 3, 1.0, 0.05, 0.0))
    d += rng.normal(0, 0.4, (10, 3, 1))  # each operator sees some parts differently
    r = msa.grr(d.tolist(), 12.0)
    assert r["interaction_p"] < 0.05 and r["pooled_interaction"] is False and r["sigma"]["interaction"] > 0.2


def test_the_stability_check_sees_a_drift_and_a_jump_but_not_noise():
    rng = np.random.default_rng(7)
    assert msa.stability(10 + rng.normal(0, 0.01, 40))["verdict"] == "pass"
    drift = msa.stability(10 + rng.normal(0, 0.01, 40) + np.linspace(0, 0.1, 40))
    assert drift["verdict"] == "fail" and drift["n_signals"] > 0
    jump = 10 + rng.normal(0, 0.01, 40)
    jump[30] += 0.2
    assert msa.stability(jump)["verdict"] == "fail"
    with pytest.raises(msa.MsaError):
        msa.stability([1.0] * 30)


def test_the_expanded_uncertainty_and_the_guard_band_follow_the_report_of_the_draft():
    u = msa.expanded_uncertainty(0.01055, k=2.0, guard_risk=0.05)
    assert u["U"] == pytest.approx(0.0211, abs=1e-4) and u["guard_band"] == pytest.approx(0.0174, abs=3e-4)  # draft 12: U 0.0211, guard band 0.0175 at 5 %
    big = msa.expanded_uncertainty(0.01, bias=0.02, resolution=0.01, u_cal=0.005)
    assert big["u"] == pytest.approx(math.sqrt(0.01 ** 2 + (0.02 / math.sqrt(3)) ** 2 + (0.01 / math.sqrt(12)) ** 2 + 0.005 ** 2))


# ------------------------------------------------------------------ the gate

def system(**over):
    base = {"policy": gate.validate_policy(None), "tolerance": 1.0, "resolution": 0.01, "studies": [], "waivers": {}}
    base.update(over)
    return base


def study(kind, day, verdict, result=None, id_=1, voided=None):
    default = {"grr": {"pct_tol": 8.0, "pct_tv": 9.0, "basis": "tolerance", "ndc": 8.0, "sigma": {"grr": 0.013}},
               "type1": {"cg": 1.6, "cgk": 1.5, "bias": 0.002}, "stability": {"n_signals": 0}}[kind]
    return {"id": id_, "kind": kind, "date": day.isoformat(), "verdict": verdict, "result": result or default, **({"voided": voided} if voided else {})}


TODAY = date(2026, 6, 1)


def full():
    return system(studies=[study("grr", TODAY - timedelta(days=60), "pass", id_=1), study("stability", TODAY - timedelta(days=30), "pass", id_=2),
                           study("type1", TODAY - timedelta(days=90), "pass", id_=3)])


def test_the_gate_is_open_with_proof_and_blocked_without_it():
    g = gate.evaluate(full(), TODAY)
    assert g["status"] == "pass" and g["blocking"] == [] and g["uncertainty"]["U"] > 0
    empty = gate.evaluate(system(), TODAY)
    assert empty["status"] == "block" and set(empty["blocking"]) == {"grr", "validity", "stability"} and empty["checks"]["type1"]["result"] == "not_done"
    assert empty["uncertainty"] is None


def test_each_check_can_stop_the_gate():
    s = full()
    assert gate.evaluate({**s, "resolution": 0.2}, TODAY)["checks"]["resolution"]["result"] == "fail"  # 20 % of the tolerance
    assert gate.evaluate({**s, "tolerance": None}, TODAY)["checks"]["resolution"]["result"] == "missing"
    old = system(studies=[study("grr", TODAY - timedelta(days=400), "pass"), study("stability", TODAY - timedelta(days=30), "pass", id_=2)])
    g = gate.evaluate(old, TODAY)
    assert g["checks"]["validity"]["result"] == "fail" and g["checks"]["validity"]["until"] == "2026-04-27"  # 400 days before TODAY is 2025-04-27, valid for 12 months
    assert g["status"] == "block" and g["blocking"] == ["validity"]
    stale = system(studies=[study("grr", TODAY - timedelta(days=60), "pass"), study("stability", TODAY - timedelta(days=250), "pass", id_=2)])
    c = gate.evaluate(stale, TODAY)["checks"]["stability"]
    assert c["result"] == "fail" and c["reason"] == "expired"
    unstable = system(studies=[study("grr", TODAY - timedelta(days=60), "pass"), study("stability", TODAY, "fail", {"n_signals": 2}, id_=2)])
    assert gate.evaluate(unstable, TODAY)["checks"]["stability"]["reason"] == "signals"
    bad_type1 = {**full(), "studies": full()["studies"][:2] + [study("type1", TODAY, "fail", id_=3)]}
    assert gate.evaluate(bad_type1, TODAY)["blocking"] == ["type1"]
    no_stab = {**full(), "policy": gate.validate_policy({"require_stability": False}), "studies": full()["studies"][:1]}
    assert gate.evaluate(no_stab, TODAY)["status"] == "pass" and gate.evaluate(no_stab, TODAY)["checks"]["stability"]["result"] == "not_needed"


def test_a_conditional_study_gives_a_conditional_gate_and_a_waiver_makes_a_failure_pass_flagged():
    cond = system(studies=[study("grr", TODAY - timedelta(days=60), "conditional"), study("stability", TODAY, "pass", id_=2)])
    g = gate.evaluate(cond, TODAY)
    assert g["status"] == "conditional" and g["remarks"] == ["grr"] and g["blocking"] == []
    waiver = {"by": "Eva", "at": "2026-05-01T08:00:00Z", "reason": "customer agreed to 25 % until the new gauge arrives"}
    waived = gate.evaluate({**cond, "waivers": {"grr": waiver}}, TODAY)
    assert waived["status"] == "pass" and waived["waived"] == ["grr"] and waived["checks"]["grr"]["effective"] == "waived"
    failing = system(studies=[study("grr", TODAY - timedelta(days=60), "fail"), study("stability", TODAY, "pass", id_=2)])
    assert gate.evaluate(failing, TODAY)["status"] == "block"
    assert gate.evaluate({**failing, "waivers": {"grr": waiver}}, TODAY)["status"] == "pass"
    assert gate.evaluate({**full(), "waivers": {"grr": waiver}}, TODAY)["waived"] == []  # nothing to waive: a waiver on a passing check is not shown


def test_the_newest_study_counts_and_a_voided_one_does_not():
    old_bad = study("grr", TODAY - timedelta(days=100), "fail", id_=1)
    new_good = study("grr", TODAY - timedelta(days=10), "pass", id_=2)
    s = system(studies=[old_bad, new_good, study("stability", TODAY, "pass", id_=3)])
    assert gate.evaluate(s, TODAY)["status"] == "pass"
    s = system(studies=[old_bad, {**new_good, "voided": {"reason": "wrong part set"}}, study("stability", TODAY, "pass", id_=3)])
    assert gate.evaluate(s, TODAY)["status"] == "block"
    assert gate.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28) and gate.add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


@pytest.mark.parametrize("change", [{"validity_months": 0}, {"grr_pass": 40, "grr_conditional": 30}, {"nonsense": 1}, {"k": 9}, {"guard_band_risk": 0.9}, {"validity_months": 1.5}])
def test_bad_policies_are_refused(change):
    with pytest.raises(ValueError):
        gate.validate_policy(change)


# ------------------------------------------------------------------ the API and where the gate stands

@pytest.fixture
def env():
    app = make_app()
    app.state.auth.create_user("oper", PASSWORD, "operator", "Olga Operator")
    return app, {u: logged_in_client(app, u) for u in ("admin", "eng", "view", "oper")}


def build_system(client, name="Bore gauge", day=None, tolerance=1.0, **studies):
    sid = client.post("/api/msa", json={"record": {"name": name, "characteristic": "bore", "unit": "mm", "resolution": 0.001, "tolerance": tolerance}}).json()["system"]["id"]
    rng = np.random.default_rng(2)
    day = (day or date.today()).isoformat()
    ok = client.post(f"/api/msa/{sid}/studies", json={"kind": "grr", "date": day, "input": {"data": grr_data(rng, s_part=0.2, s_rep=0.003, s_op=0.002)}})
    assert ok.status_code == 200, ok.text
    assert client.post(f"/api/msa/{sid}/studies", json={"kind": "stability", "date": day, "input": {"values": [float(v) for v in 10 + rng.normal(0, 0.005, 25)]}}).status_code == 200
    assert client.post(f"/api/msa/{sid}/studies", json={"kind": "type1", "date": day, "input": {"reference": 10.0, "values": [float(v) for v in 10.0 + rng.normal(0, 0.004, 50)]}}).status_code == 200
    return sid


def test_roles_and_the_life_of_a_measurement_system(env):
    app, c = env
    rec = {"name": "Bore gauge", "characteristic": "bore", "unit": "mm", "resolution": 0.001, "tolerance": 1.0}
    assert c["view"].post("/api/msa", json={"record": rec}).status_code == 403 and c["oper"].post("/api/msa", json={"record": rec}).status_code == 403
    sid = c["eng"].post("/api/msa", json={"record": rec}).json()["system"]["id"]
    assert c["eng"].post("/api/msa", json={"record": rec}).status_code == 409
    view = c["view"].get(f"/api/msa/{sid}").json()
    assert view["gate"]["status"] == "block" and view["system"]["policy"]["validity_months"] == 12
    assert c["view"].get("/api/msa").json()["systems"][0]["status"] == "block"
    sid = build_system(c["eng"], "Second gauge")
    v = c["eng"].get(f"/api/msa/{sid}").json()
    assert v["gate"]["status"] == "pass" and v["gate"]["uncertainty"]["U"] > 0 and len(v["system"]["studies"]) == 3
    assert all(s["verdict"] == "pass" for s in v["system"]["studies"])
    # a tighter tolerance changes the verdicts: the results are computed from the stored data, not stored
    r = c["eng"].put(f"/api/msa/{sid}", json={"record": {**{k: v["system"][k] for k in ("name", "characteristic", "unit", "resolution")}, "tolerance": 0.05, "policy": v["system"]["policy"]}}).json()
    assert r["gate"]["status"] == "block" and r["gate"]["checks"]["grr"]["result"] == "fail"
    assert c["eng"].delete(f"/api/msa/{sid}").status_code == 403 and c["admin"].delete(f"/api/msa/{sid}").status_code == 200
    assert err(c["eng"].get(f"/api/msa/{sid}"))["code"] == "msa_system_not_found"


def test_studies_waivers_and_voiding_with_their_reasons_and_the_audit_trail(env):
    app, c = env
    sid = build_system(c["eng"])
    assert err(c["eng"].post(f"/api/msa/{sid}/studies", json={"kind": "grr", "date": "2999-01-01", "input": {"data": grr_data(np.random.default_rng(1))}}))["code"] == "invalid_input"
    assert err(c["eng"].post(f"/api/msa/{sid}/studies", json={"kind": "grr", "date": "not a date", "input": {"data": []}}))["code"] == "invalid_input"
    assert err(c["eng"].post(f"/api/msa/{sid}/studies", json={"kind": "grr", "date": "2026-01-01", "input": {"values": [1]}}))["code"] == "invalid_input"
    assert err(c["eng"].post(f"/api/msa/{sid}/studies", json={"kind": "oracle", "date": "2026-01-01", "input": {}}))["code"] == "invalid_input"
    assert err(c["eng"].post(f"/api/msa/{sid}/studies", json={"kind": "grr", "date": "2026-01-01", "input": {"data": [[[1, 1], [1, 1]]] * 6}}))["code"] == "study_not_evaluable"
    bad = grr_data(np.random.default_rng(3), s_rep=0.5, s_op=0.4)  # a newer, bad study makes the gate block
    c["eng"].post(f"/api/msa/{sid}/studies", json={"kind": "grr", "date": date.today().isoformat(), "input": {"data": bad}})
    v = c["eng"].get(f"/api/msa/{sid}").json()
    assert v["gate"]["status"] == "block" and v["gate"]["blocking"] == ["grr"]
    assert err(c["eng"].put(f"/api/msa/{sid}/waivers/grr", json={"reason": " "}))["code"] == "reason_required"
    assert err(c["eng"].put(f"/api/msa/{sid}/waivers/nonsense", json={"reason": "agreed"}))["code"] == "invalid_input"
    w = c["eng"].put(f"/api/msa/{sid}/waivers/grr", json={"reason": "customer agreed, e-mail of 2026-05-02"}).json()
    assert w["gate"]["status"] == "pass" and w["gate"]["waived"] == ["grr"] and w["gate"]["checks"]["grr"]["waiver"]["by"].startswith("Eva")
    assert c["eng"].delete(f"/api/msa/{sid}/waivers/grr").json()["gate"]["status"] == "block"
    assert err(c["eng"].delete(f"/api/msa/{sid}/waivers/grr"))["code"] == "waiver_not_found"
    newest = max(s["id"] for s in v["system"]["studies"])
    assert err(c["eng"].post(f"/api/msa/{sid}/studies/{newest}/void", json={"reason": ""}))["code"] == "reason_required"
    assert c["eng"].post(f"/api/msa/{sid}/studies/{newest}/void", json={"reason": "operator B used a wrong part"}).json()["gate"]["status"] == "pass"
    assert err(c["eng"].post(f"/api/msa/{sid}/studies/{newest}/void", json={"reason": "again"}))["code"] == "already_voided"
    assert err(c["eng"].post(f"/api/msa/{sid}/studies/99/void", json={"reason": "no such"}))["code"] == "msa_study_not_found"
    actions = [e["action"] for e in app.state.audit.list(200)]
    assert {"msa_system_created", "msa_study_added", "msa_waiver_set", "msa_waiver_removed", "msa_study_voided"} <= set(actions) and app.state.audit.verify()["ok"]


MONITOR = {"name": "Bore", "characteristic": "bore", "kind": "xbar-s", "n": 5}
PARAMS = {"type": "parameters", "mu": 10.0, "sigma": 0.1}


def test_a_monitor_tied_to_a_blocked_system_accepts_no_sample_and_a_conditional_one_flags_it(env):
    app, c = env
    empty = c["eng"].post("/api/msa", json={"record": {"name": "No proof", "resolution": 0.001, "tolerance": 1.0}}).json()["system"]["id"]
    mid = c["eng"].post("/api/monitors", json={"config": {**MONITOR, "specs": {"msa_id": empty}}, "source": PARAMS}).json()["monitor"]["id"]
    assert c["eng"].get(f"/api/monitors/{mid}").json()["msa"]["status"] == "block"
    c["oper"].post(f"/api/monitors/{mid}/ack")
    r = c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0, 10.01, 9.99, 10.02, 10.0]})
    assert r.status_code == 409 and err(r)["code"] == "msa_gate_blocked" and err(r)["params"]["system"] == "No proof"
    assert c["eng"].get(f"/api/monitors/{mid}").json()["points"] == []
    c["eng"].put(f"/api/msa/{empty}/waivers/grr", json={"reason": "agreed"})
    for check in ("validity", "stability"):
        c["eng"].put(f"/api/msa/{empty}/waivers/{check}", json={"reason": "agreed"})
    ok = c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0, 10.01, 9.99, 10.02, 10.0]})
    assert ok.status_code == 200 and ok.json()["msa"]["waived"] == ["grr", "validity", "stability"] and ok.json()["msa"]["status"] == "pass"
    # a conditional system: accepted and flagged
    cond = build_system(c["eng"], "Conditional gauge")
    data = grr_data(np.random.default_rng(4), s_part=0.2, s_rep=0.02, s_op=0.01)  # roughly 12 to 15 % of the tolerance
    c["eng"].post(f"/api/msa/{cond}/studies", json={"kind": "grr", "date": date.today().isoformat(), "input": {"data": data}})
    assert c["eng"].get(f"/api/msa/{cond}").json()["gate"]["status"] == "conditional"
    mid2 = c["eng"].post("/api/monitors", json={"config": {**MONITOR, "name": "Bore 2", "specs": {"msa_id": cond}}, "source": PARAMS}).json()["monitor"]["id"]
    c["oper"].post(f"/api/monitors/{mid2}/ack")
    r = c["oper"].post(f"/api/monitors/{mid2}/points", json={"values": [10.0, 10.01, 9.99, 10.02, 10.0]}).json()
    assert r["msa"]["status"] == "conditional" and r["msa"]["remarks"] == ["grr"]
    # the system must exist; a deleted one blocks the monitor with its own message
    assert err(c["eng"].post("/api/monitors", json={"config": {**MONITOR, "name": "Bore 3", "specs": {"msa_id": 999}}, "source": PARAMS}))["code"] == "msa_system_not_found"
    c["admin"].delete(f"/api/msa/{cond}")
    assert err(c["oper"].post(f"/api/monitors/{mid2}/points", json={"values": [10.0, 10.01, 9.99, 10.02, 10.0]}))["code"] == "msa_system_missing"
    # a monitor without a system is not touched (existing monitors)
    mid4 = c["eng"].post("/api/monitors", json={"config": {**MONITOR, "name": "Plain"}, "source": PARAMS}).json()["monitor"]["id"]
    c["oper"].post(f"/api/monitors/{mid4}/ack")
    r = c["oper"].post(f"/api/monitors/{mid4}/points", json={"values": [10.0, 10.01, 9.99, 10.02, 10.0]}).json()
    assert r["msa"] is None and c["eng"].get(f"/api/monitors/{mid4}").json()["msa"] is None


def test_the_machine_study_item_msa_evidence_comes_from_the_gate(env):
    app, c = env
    from spc.study import checklist as cl

    record = {"name": "Grinder 4", "characteristic": "bore", "specs": {"lsl": 9.5, "usl": 10.5}}
    sid = c["eng"].post("/api/studies", json={"record": record}).json()["study"]["id"]
    row = lambda: next(r for r in c["eng"].get(f"/api/studies/{sid}").json()["evaluation"]["items"] if r["key"] == "msa_evidence")
    assert row()["effective"] == "open" and row()["result"]["reason"] == "no_system" and row()["auto"] is True
    assert err(c["eng"].put(f"/api/studies/{sid}", json={"record": {**record, "measurement_system_id": 99}}))["code"] == "msa_system_not_found"
    empty = c["eng"].post("/api/msa", json={"record": {"name": "No proof", "resolution": 0.001, "tolerance": 1.0}}).json()["system"]["id"]
    c["eng"].put(f"/api/studies/{sid}", json={"record": {**record, "measurement_system_id": empty}})
    assert row()["effective"] == "fail" and row()["blocking"] is True
    good = build_system(c["eng"], "Good gauge")
    c["eng"].put(f"/api/studies/{sid}", json={"record": {**record, "measurement_system_id": good}})
    assert row()["effective"] == "ok" and row()["result"]["system"] == "Good gauge"
    assert not any(i.key == "msa_evidence" for i in cl.ITEMS if not i.auto)
    # a person can still record that the evidence exists elsewhere, with the reason
    c["eng"].put(f"/api/studies/{sid}", json={"record": {**record, "measurement_system_id": None}})
    assert err(c["eng"].put(f"/api/studies/{sid}/items/msa_evidence", json={"status": "deviation", "note": ""}))["code"] == "note_required"
    c["eng"].put(f"/api/studies/{sid}/items/msa_evidence", json={"status": "deviation", "note": "VDA 5 report 2026-02 of the supplier"})
    assert row()["effective"] == "deviation"


def test_a_report_is_refused_for_a_blocked_system_and_carries_the_uncertainty_of_a_proven_one(env):
    app, c = env
    rng = np.random.default_rng(9)
    key = app.state.store.add(Dataset.from_values([float(v) for v in rng.normal(10, 0.05, 60)]), 1, "run")
    body = {"analysis": {"stage": "preliminary", "lsl": 9.6, "usl": 10.4}, "language": "en"}
    empty = c["eng"].post("/api/msa", json={"record": {"name": "No proof", "resolution": 0.001, "tolerance": 0.8}}).json()["system"]["id"]
    r = c["eng"].post(f"/api/datasets/{key}/reports", json={**body, "measurement_system_id": empty})
    assert r.status_code == 409 and err(r)["code"] == "msa_gate_blocked"
    good = build_system(c["eng"], "Good gauge", tolerance=0.8)
    r = c["eng"].post(f"/api/datasets/{key}/reports", json={**body, "measurement_system_id": good})
    assert r.status_code == 200, r.text
    archive = c["eng"].get(r.json()["urls"]["archive"]).json()
    u = c["eng"].get(f"/api/msa/{good}").json()["gate"]["uncertainty"]
    assert archive["report_meta"]["uncertainty"] == pytest.approx(u["U"]) and archive["report_meta"]["coverage_factor"] == u["k"]
    assert "Measurement system Good gauge" in archive["report_meta"]["technical_conditions"] and "MSA gate: pass" in archive["report_meta"]["technical_conditions"]
    html = c["eng"].get(r.json()["urls"]["html"]).text
    assert "Good gauge" in html
    own = c["eng"].post(f"/api/datasets/{key}/reports", json={**body, "measurement_system_id": good, "meta": {"uncertainty": 0.123}}).json()
    assert c["eng"].get(own["urls"]["archive"]).json()["report_meta"]["uncertainty"] == 0.123  # the author's own value stands
    plain = c["eng"].post(f"/api/datasets/{key}/reports", json=body).json()  # without a system nothing changes
    assert c["eng"].get(plain["urls"]["archive"]).json()["report_meta"]["uncertainty"] is None

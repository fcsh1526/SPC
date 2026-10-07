"""The ISO 22514-7 studies of a measurement system: the worked example through the API, the gate, the uncertainty of the report, and the attribute studies."""

import math
from datetime import date

import numpy as np
import pytest

from spc.data import Dataset
from tests.conftest import logged_in_client, make_app
from tests.test_api import err
from tests.test_iso22514_7 import A1_X, A1_Y, A4, FIG6, figure_6_results


@pytest.fixture
def env():
    app = make_app()
    return app, logged_in_client(app, "eng")


def system(eng, name="Scope", **extra):
    r = eng.post("/api/msa", json={"record": {"name": name, "characteristic": "gap", "unit": "mm", "resolution": 0.005, "tolerance": 9.0, **extra}})
    assert r.status_code == 200, r.text
    return r.json()["system"]["id"]


def post_study(eng, sid, kind, input_, day=None):
    return eng.post(f"/api/msa/{sid}/studies", json={"kind": kind, "date": day or date.today().isoformat(), "input": input_})


EXAMPLE = {"lower": 2, "upper": 11, "calibration": {"standard": 0.005}, "linearity": {"references": A1_X, "values": A1_Y}, "process": {"data": A4, "kind": "operator"}}


def test_the_worked_example_of_the_standard_through_the_api(env):
    app, eng = env
    sid = system(eng)
    r = post_study(eng, sid, "iso_study", EXAMPLE)
    assert r.status_code == 200, r.text
    v = r.json()
    study = v["system"]["studies"][0]
    c = study["result"]["combined"]
    assert (c["u_ms"], c["U_ms"], c["u_mp"], c["U_mp"]) == pytest.approx((0.0836, 0.1672, 0.2093, 0.4185), abs=2e-4)
    assert (c["q_ms"], c["q_mp"], c["c_ms"], c["c_mp"]) == pytest.approx((3.7, 9.3, 5.38, 4.30), abs=0.06)
    assert study["result"]["interval"] == 9 and study["result"]["coverage"]["k"] == 2.0
    # the repeatability and reproducibility of the study prove the `grr` check as a gauge R&R does; the gate is blocked only by the missing stability check
    checks = v["gate"]["checks"]
    assert checks["grr"]["iso"] is True and checks["grr"]["result"] in ("pass", "warn") and checks["iso22514_7"]["q_mp"] == pytest.approx(9.3, abs=0.06) and checks["validity"]["result"] == "pass"
    assert v["gate"]["blocking"] == ["stability"]
    # the uncertainty of the gate (and so of the report) is that of the measurement process
    u = v["gate"]["uncertainty"]
    assert u["source"] == "iso22514_7" and u["U"] == pytest.approx(0.4185, abs=2e-4) and u["k"] == 2.0 and u["guard_band"] == pytest.approx(1.645 * 0.2093, abs=1e-3)


def test_a_report_carries_the_iso_proof_and_its_uncertainty(env):
    app, eng = env
    sid = system(eng, policy={"require_stability": False})
    assert post_study(eng, sid, "iso_study", EXAMPLE).status_code == 200
    key = app.state.store.add(Dataset.from_values([float(v) for v in np.random.default_rng(9).normal(6, 1, 60)]), 1, "run")
    r = eng.post(f"/api/datasets/{key}/reports", json={"analysis": {"stage": "preliminary", "lsl": 2, "usl": 11}, "language": "en", "measurement_system_id": sid})
    assert r.status_code == 200, r.text
    meta = eng.get(r.json()["urls"]["archive"]).json()["report_meta"]
    assert "ISO 22514-7: Q_MP 9.3 %, C_MP 4.30" in meta["technical_conditions"] and meta["uncertainty"] == pytest.approx(0.4185, abs=2e-4)


def test_one_study_part_by_part_the_mpe_route_and_the_real_capability(env):
    app, eng = env
    sid = system(eng)
    rng = np.random.default_rng(5)
    values = [float(v) for v in rng.normal(5.003, 0.04, 40)]
    only_system = post_study(eng, sid, "iso_study", {"calibration": {"expanded": 0.01, "k": 2}, "repeatability": {"reference": 5.0, "values": values}, "linearity_a": 0.02})
    assert only_system.status_code == 200, only_system.text
    res = only_system.json()["system"]["studies"][0]["result"]
    assert res["combined"]["u_mp"] is None and res["combined"]["q_mp"] is None and "linearity_a" not in res and res["analyses"]["linearity"]["method"] == "known"
    comps = res["components"]
    assert comps["cal"] == pytest.approx(0.005) and comps["lin"] == pytest.approx(0.02 / math.sqrt(3)) and comps["bi"] == pytest.approx(abs(np.mean(values) - 5.0) / math.sqrt(3))
    # without a process study the system has no proof of repeatability and reproducibility: the `grr` check stays missing
    assert only_system.json()["gate"]["checks"]["grr"]["result"] == "missing" and only_system.json()["gate"]["uncertainty"]["source"] == "iso22514_7"
    mpe = post_study(eng, sid, "iso_study", {"mpe": [0.05], "repeatability": {"reference": 5.0, "values": values}, "observed_cp": 1.33}).json()["system"]["studies"][1]["result"]
    assert mpe["combined"]["route"] == "mpe" and mpe["combined"]["u_ms"] == pytest.approx(math.sqrt(0.05 ** 2 / 3 + np.var(values, ddof=1)), abs=1e-6)
    assert mpe["real_capability"]["basis"] == "q_ms" and mpe["real_capability"]["real"] > 1.33
    # the deviation method of 7.1.3.4 needs the repeatability study; the analysis of variance of the linearity study does not take one
    dev = {"references": [2.0, 4.0, 6.0, 8.0, 10.0], "values": [[2.5, 2.4, 2.6], [4.1, 3.9, 4.0], [6.0, 6.1, 5.9], [7.8, 7.7, 7.8], [9.4, 9.5, 9.3]], "method": "deviation"}
    assert err(post_study(eng, sid, "iso_study", {"linearity": dev}))["code"] == "study_not_evaluable"
    ok = post_study(eng, sid, "iso_study", {"linearity": dev, "repeatability": {"reference": 5.0, "values": values}})
    assert ok.status_code == 200 and ok.json()["system"]["studies"][2]["result"]["analyses"]["linearity"]["method"] == "deviation"
    assert err(post_study(eng, sid, "iso_study", {"linearity": {"references": A1_X, "values": A1_Y}, "repeatability": {"reference": 5.0, "values": values}}))["code"] == "study_not_evaluable"


def test_the_inputs_of_an_iso_study_are_checked_and_the_design_is_judged(env):
    app, eng = env
    sid = system(eng)
    assert err(post_study(eng, sid, "iso_study", {"nonsense": 1}))["code"] == "invalid_input"  # an unknown key is refused before the study is read
    for body in ({}, {"lower": 1}, {"lower": 5, "upper": 1, "repeatability": {"reference": 1, "values": [1.0] * 30}}, {"repeatability": {"reference": 5.0, "values": [1.0, 2.0]}},
                 {"calibration": {"expanded": 1}, "repeatability": {"reference": 1, "values": list(np.arange(30.0))}}, {"mpe": [0.1]}, {"mpe": []}):
        r = post_study(eng, sid, "iso_study", body)
        assert r.status_code == 400 and r.json()["error"]["code"] in ("study_not_evaluable", "invalid_input"), body
    small = post_study(eng, sid, "iso_study", {"calibration": {"standard": 0.005}, "linearity": {"references": A1_X, "values": A1_Y},
                                                "process": {"data": np.array(A4)[:2, :4, :2].tolist()}})
    res = small.json()["system"]["studies"][0]
    assert small.status_code == 200 and res["verdict"] == "conditional" and {"few_workpieces", "few_measurements"} <= {w["code"] for w in res["result"]["warnings"]}
    assert res["result"]["coverage"]["student"] is True and res["result"]["coverage"]["k"] > 2.0  # fewer than 30 measurements: Student's t
    wide = post_study(eng, sid, "iso_study", {"repeatability": {"reference": 5.0, "values": [float(v) for v in np.random.default_rng(6).normal(5, 1.5, 40)]}})
    assert wide.json()["system"]["studies"][-1]["verdict"] == "fail" and wide.json()["gate"]["checks"]["iso22514_7"]["result"] == "fail"
    assert "iso22514_7" in wide.json()["gate"]["blocking"]
    eng.put(f"/api/msa/{sid}/waivers/iso22514_7", json={"reason": "customer accepts, deviation 12"})
    assert eng.get(f"/api/msa/{sid}").json()["gate"]["checks"]["iso22514_7"]["effective"] == "waived"


def test_the_limits_of_the_policy_move_the_verdict(env):
    app, eng = env
    sid = system(eng)
    r = post_study(eng, sid, "iso_study", EXAMPLE)
    assert r.json()["system"]["studies"][0]["verdict"] == "pass"
    tight = eng.put(f"/api/msa/{sid}", json={"record": {"name": "Scope", "characteristic": "gap", "unit": "mm", "resolution": 0.005, "tolerance": 9.0, "policy": {"iso_q_mp_max": 5.0}}})
    assert tight.status_code == 200 and tight.json()["system"]["studies"][0]["verdict"] == "fail" and tight.json()["gate"]["checks"]["iso22514_7"]["result"] == "fail"
    for bad in ({"iso_q_ms_max": 0}, {"iso_q_mp_max": 200}, {"iso_c_min": 9}):
        assert eng.post("/api/msa", json={"record": {"name": "Bad policy", "policy": bad}}).status_code == 400


# ------------------------------------------------------------------ attribute systems

def attribute_system(eng, name="Visual check"):
    return eng.post("/api/msa", json={"record": {"name": name, "kind": "attribute", "characteristic": "thread"}}).json()["system"]["id"]


def range_input():
    return {"reference": FIG6[::-1], "results": {k: [row[::-1] for row in v] for k, v in figure_6_results().items()}, "lower": 0.45, "upper": 0.55}


def test_the_attribute_studies_of_the_standard_and_their_gate(env):
    app, eng = env
    sid = attribute_system(eng)
    v = eng.get(f"/api/msa/{sid}").json()
    assert v["gate"]["checks"]["attribute_iso"]["result"] == "not_done" and set(v["gate"]["blocking"]) == {"attribute", "validity"}
    r = post_study(eng, sid, "uncertainty_range", range_input())
    assert r.status_code == 200, r.text
    study = r.json()["system"]["studies"][0]
    assert study["result"]["q_attr"] == pytest.approx(23.79, abs=0.01) and study["verdict"] == "conditional"  # above the 20 % of 12.1, below the 30 % of Q_MP
    g = r.json()["gate"]
    # the uncertainty range stands for the proof as well: the `attribute` check is no longer missing
    assert g["checks"]["attribute"]["result"] == "warn" and g["checks"]["attribute"]["iso"] is True and g["status"] == "conditional" and g["checks"]["validity"]["result"] == "pass"
    assert g["checks"]["attribute_iso"]["result"] == "warn" and g["checks"]["attribute_iso"]["studies"] == {"uncertainty_range": 1}
    # Bowker's test of symmetry: 40 parts, 3 trials, 2 operators
    rng = np.random.default_rng(4)
    truth = rng.random(40) < 0.5
    ops = {name: np.array([np.where(rng.random(40) < 0.9, truth, ~truth) for _ in range(3)]).astype(int).tolist() for name in ("A", "B")}
    b = post_study(eng, sid, "bowker", {"results": ops})
    assert b.status_code == 200 and b.json()["system"]["studies"][1]["result"]["pairs"][0]["df"] in (1, 2, 3)
    # the review of 12.4: three clear workpieces, all results agree with the reference
    review = {"reference": [0.40, 0.50, 0.60], "results": [[0, 1, 0]] * 3, "lower": 0.45, "upper": 0.55, "q_mp": 30}
    ok = post_study(eng, sid, "attribute_review", review)
    assert ok.status_code == 200 and ok.json()["system"]["studies"][2]["verdict"] == "pass" and ok.json()["system"]["studies"][2]["result"]["u_mp_max"] == pytest.approx(0.015)
    bad = post_study(eng, sid, "attribute_review", {**review, "results": [[0, 1, 0], [0, 0, 0], [0, 1, 0]]})
    assert bad.json()["gate"]["checks"]["attribute_iso"]["result"] == "fail" and "attribute_iso" in bad.json()["gate"]["blocking"]
    # a variable study does not belong to an attribute system, and these do not belong to a variable system
    assert err(post_study(eng, sid, "iso_study", EXAMPLE))["code"] == "invalid_input"
    assert err(post_study(eng, system(eng, "A gauge"), "bowker", {"results": ops}))["code"] == "invalid_input"
    for kind, body in (("bowker", {}), ("uncertainty_range", {"reference": [1.0]}), ("attribute_review", {"reference": [0.4, 0.5, 0.6]})):
        assert err(post_study(eng, sid, kind, body))["code"] == "invalid_input"
    far = range_input()
    far["results"] = {k: [row[:20] for row in v] for k, v in far["results"].items()}
    far["reference"] = far["reference"][:20]
    assert err(post_study(eng, sid, "uncertainty_range", far))["code"] == "study_not_evaluable"


def test_the_linearity_of_a_system_is_monitored_with_its_regression_function(env):
    app, eng = env
    sid = system(eng)
    refs = [2.0, 6.0, 10.0]
    assert err(eng.post(f"/api/msa/{sid}/linearity-monitor", json={"references": refs, "readings": [[1.0]] * 3}))["code"] == "no_linearity_study"
    assert post_study(eng, sid, "iso_study", EXAMPLE).status_code == 200
    good = [[0.2358 + 0.987 * r + e for e in (0.02, -0.03)] for r in refs]
    r = eng.post(f"/api/msa/{sid}/linearity-monitor", json={"references": refs, "readings": good})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["valid"] and m["study"] == 1 and m["standards"] == 3 and m["ucl"] == pytest.approx(-m["lcl"]) and m["beta1"] == pytest.approx(0.987, abs=1e-3)
    drift = eng.post(f"/api/msa/{sid}/linearity-monitor", json={"references": refs, "readings": [[v + 0.5] for v in [0.2358 + 0.987 * x for x in refs]]}).json()
    assert not drift["valid"]
    assert eng.post(f"/api/msa/{sid}/linearity-monitor", json={"references": [1.0], "readings": [[1.0]]}).status_code == 422
    # a system whose studies hold no analysis of variance of a linearity study has nothing to transform with
    other = system(eng, "Other gauge")
    assert post_study(eng, other, "iso_study", {"calibration": {"standard": 0.005}, "repeatability": {"reference": 5.0, "values": [float(v) for v in np.random.default_rng(8).normal(5, 0.03, 40)]}}).status_code == 200
    assert err(eng.post(f"/api/msa/{other}/linearity-monitor", json={"references": refs, "readings": good}))["code"] == "no_linearity_study"

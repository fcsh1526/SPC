"""Attribute measurement systems: the agreement study (cross-tab method) and the gate of an attribute system."""

from datetime import date

import numpy as np
import pytest
from scipy import stats

from spc.core import msa_attribute as ma
from spc.core.msa import MsaError
from tests.conftest import PASSWORD, logged_in_client, make_app
from tests.test_api import err


# ------------------------------------------------------------------ the kappa statistics against published numbers

def test_cohens_kappa_of_the_textbook_example():
    # 50 proposals, two raters: both yes 20, A yes and B no 5, A no and B yes 10, both no 15: po = 0.7, pe = 0.5, kappa = 0.4
    a = [1] * 20 + [1] * 5 + [0] * 10 + [0] * 15
    b = [1] * 20 + [0] * 5 + [1] * 10 + [0] * 15
    assert ma.cohen_kappa(a, b) == pytest.approx(0.4)
    assert ma.cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == pytest.approx(1.0) and ma.cohen_kappa([1, 1, 1], [1, 1, 1]) is None  # nothing to agree about


def test_fleiss_kappa_of_the_published_example():
    # Fleiss (1971), 10 patients, 14 raters, 5 categories: kappa = 0.210
    table = np.array([[0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1], [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2],
                      [6, 5, 2, 1, 0], [0, 2, 2, 3, 7]])
    assert ma.fleiss_kappa(table) == pytest.approx(0.2099, abs=5e-4)


def test_the_exact_interval_equals_the_binomial_one():
    for k, n in ((0, 30), (30, 30), (7, 50), (45, 50)):
        lo, hi = ma._interval(k, n)
        ref = stats.binomtest(k, n).proportion_ci(confidence_level=0.95, method="exact")
        assert lo == pytest.approx(100 * ref.low, abs=1e-6) and hi == pytest.approx(100 * ref.high, abs=1e-6)


# ------------------------------------------------------------------ the study

def make(reference, errors, trials=3):
    """errors: {appraiser: (miss part indices, false alarm part indices)} made in every trial (in trial 1 only the first of each list for 'wobbly')."""
    ratings = {}
    for name, (miss, fa) in errors.items():
        t = np.array(reference)
        for i in miss:
            t[i] = 1
        for i in fa:
            t[i] = 0
        ratings[name] = [t.tolist() for _ in range(trials)]
    return ratings


REF = [1] * 30 + [0] * 30  # 60 parts: parts 0..29 conforming, 30..59 nonconforming


def test_perfect_appraisers_pass_with_everything_at_its_best():
    r = ma.evaluate(make(REF, {"A": ([], []), "B": ([], []), "C": ([], [])}), REF)
    assert r["verdict"] == "pass" and all(v == "pass" for v in r["checks"].values())
    for a in r["against_reference"].values():
        assert a["effectiveness"]["pct"] == 100 and a["miss"]["pct"] == 0 and a["false_alarm"]["pct"] == 0 and a["kappa"] == pytest.approx(1.0)
    assert r["between"]["agree"]["pct"] == 100 and r["system"]["right"]["pct"] == 100 and r["warnings"] == []
    assert set(r["between"]["kappa"]) == {"A×B", "A×C", "B×C"}


def test_miss_and_false_alarm_rates_count_the_decisions_on_the_right_parts():
    # A accepts 2 of the 30 nonconforming parts in every trial and rejects 1 of the 30 conforming parts in every trial
    r = ma.evaluate(make(REF, {"A": ([30, 31], [0]), "B": ([], [])}), REF)
    a = r["against_reference"]["A"]
    assert (a["miss"]["k"], a["miss"]["n"]) == (6, 90) and a["miss"]["pct"] == pytest.approx(100 * 6 / 90)  # 2 parts x 3 trials of 30 x 3 opportunities
    assert (a["false_alarm"]["k"], a["false_alarm"]["n"]) == (3, 90)
    assert (a["effectiveness"]["k"], a["effectiveness"]["n"]) == (57, 60) and a["effectiveness"]["pct"] == pytest.approx(95.0)
    assert (a["decisions_right"]["k"], a["decisions_right"]["n"]) == (171, 180)
    assert r["against_reference"]["B"]["miss"]["pct"] == 0
    assert r["system"]["right"]["k"] == 57  # B was always right: the system is as good as A
    # the kappa against the reference follows the confusion matrix
    conf_a = np.array(make(REF, {"A": ([30, 31], [0])})["A"]).ravel()
    assert a["kappa"] == pytest.approx(ma.cohen_kappa(conf_a, np.tile(REF, 3)))


def test_within_appraiser_agreement_and_kappa_see_a_wobbling_appraiser():
    t = [list(REF) for _ in range(3)]
    t[1][5] = 0
    t[2][40] = 1  # two parts on which this appraiser is not consistent
    r = ma.evaluate({"A": t, "B": [list(REF) for _ in range(3)]}, REF)
    assert r["within"]["A"]["agree"]["k"] == 58 and r["within"]["B"]["agree"]["pct"] == 100
    counts = np.stack([np.sum(np.array(t) == 1, axis=0), np.sum(np.array(t) == 0, axis=0)], axis=1)
    assert r["within"]["A"]["kappa"] == pytest.approx(ma.fleiss_kappa(counts)) and r["within"]["A"]["kappa"] < 1
    assert r["between"]["agree"]["k"] == 58


def test_the_acceptance_guidelines_decide_per_measure():
    perfect = ma.evaluate(make(REF, {"A": ([30], [0]), "B": ([], [])}), REF)
    # 1 of 30 parts missed and 1 of 30 falsely alarmed in every trial: effectiveness 96.7 %, miss 3.3 % (above 2 %, up to 5 %: conditional),
    # false alarm 3.3 % (up to 5 %: capable)
    assert perfect["checks"]["effectiveness"] == "pass" and perfect["checks"]["miss"] == "conditional" and perfect["checks"]["false_alarm"] == "pass"
    assert perfect["verdict"] == "conditional"
    assert ma.evaluate(make(REF, {"A": ([], [0]), "B": ([], [])}), REF)["verdict"] == "pass"  # false alarm 3.3 %, effectiveness 98.3 %, kappa high


def test_each_guideline_can_fail_the_study_and_the_policy_moves_the_limits():
    bad_miss = ma.evaluate(make(REF, {"A": (list(range(30, 34)), []), "B": ([], [])}), REF)  # 4 of 30 = 13 %
    assert bad_miss["checks"]["miss"] == "fail" and bad_miss["checks"]["effectiveness"] == "pass" and bad_miss["verdict"] == "fail"  # 56 of 60 parts right: 93 %
    bad_fa = ma.evaluate(make(REF, {"A": ([], list(range(5))), "B": ([], [])}), REF)  # 5 of 30 = 16.7 % false alarms
    assert bad_fa["checks"]["false_alarm"] == "fail"
    lax = ma.evaluate(make(REF, {"A": (list(range(30, 34)), []), "B": ([], [])}), REF, {"miss_pass": 20, "miss_conditional": 30, "eff_pass": 80, "eff_conditional": 70})
    assert lax["checks"]["miss"] == "pass" and lax["checks"]["effectiveness"] == "pass"
    # opposite decisions: kappa is negative and fails
    flip = {"A": [list(REF)] * 3, "B": [[1 - v for v in REF]] * 3}
    assert ma.evaluate(flip, REF)["checks"]["kappa"] == "fail"


def test_the_design_is_checked_and_warnings_say_what_is_thin():
    ok = make(REF, {"A": ([], []), "B": ([], [])})
    with pytest.raises(MsaError):
        ma.evaluate({"A": ok["A"]}, REF)  # one appraiser
    with pytest.raises(MsaError):
        ma.evaluate({"A": ok["A"][:1], "B": ok["B"][:1]}, REF)  # one trial
    with pytest.raises(MsaError):
        ma.evaluate({k: [t[:10] for t in v] for k, v in ok.items()}, REF)  # the trials are shorter than the reference
    with pytest.raises(MsaError):
        ma.evaluate(ok, [1] * 57 + [0] * 3)  # too few nonconforming parts: no opportunity for a miss
    with pytest.raises(MsaError):
        ma.evaluate({"A": [[2] * 60] * 2, "B": ok["B"][:2]}, REF)
    with pytest.raises(MsaError):
        ma.evaluate({"A": ok["A"], "B": ok["B"][:2]}, REF)  # trials differ
    few = [1] * 8 + [0] * 8
    with pytest.raises(MsaError):
        ma.evaluate(make(few, {"A": ([], []), "B": ([], [])}), few)  # fewer than 20 parts
    thin = ma.evaluate({k: v[:2] for k, v in ok.items()}, REF)  # two appraisers and two trials: the design is small, but there are 60 parts
    assert [w["code"] for w in thin["warnings"]] == ["attr_small_design"]
    thirty = [1] * 15 + [0] * 15
    assert "attr_few_parts" in [w["code"] for w in ma.evaluate(make(thirty, {"A": ([], []), "B": ([], []), "C": ([], [])}), thirty)["warnings"]]
    ref2 = [1] * 6 + [0] * 54
    unbalanced = ma.evaluate(make(ref2, {"A": ([], []), "B": ([], []), "C": ([], [])}), ref2)
    assert "attr_unbalanced_reference" in [w["code"] for w in unbalanced["warnings"]]
    for bad in ({"eff_pass": 70}, {"miss_pass": 9}, {"kappa_pass": 0.3}, {"nonsense": 1}, {"fa_pass": 120}, {"eff_pass": True}):
        with pytest.raises(ValueError):
            ma.validate_policy(bad)


# ------------------------------------------------------------------ the system, the gate and the API

@pytest.fixture
def env():
    app = make_app()
    return app, {u: logged_in_client(app, u) for u in ("admin", "eng", "view")}


def study_input(errors):
    return {"ratings": make(REF, errors), "reference": REF}


def test_an_attribute_system_has_its_own_checks_and_a_gate(env):
    app, c = env
    eng = c["eng"]
    sid = eng.post("/api/msa", json={"record": {"name": "Go/no-go gauge", "kind": "attribute", "characteristic": "thread"}}).json()["system"]["id"]
    v = eng.get(f"/api/msa/{sid}").json()
    assert v["system"]["kind"] == "attribute" and v["gate"]["status"] == "block" and set(v["gate"]["blocking"]) == {"attribute", "validity"}
    for other in ("resolution", "grr", "type1", "stability"):
        assert v["gate"]["checks"][other]["result"] == "not_needed" and v["gate"]["checks"][other]["reason"] == "other_kind"
    # a variable study does not belong to it, and an attribute study does not belong to a variable system
    r = eng.post(f"/api/msa/{sid}/studies", json={"kind": "grr", "date": date.today().isoformat(), "input": {"data": [[[1, 2]] * 3] * 5}})
    assert err(r)["code"] == "invalid_input"
    ok = eng.post(f"/api/msa/{sid}/studies", json={"kind": "attribute", "date": date.today().isoformat(), "input": study_input({"A": ([], []), "B": ([], []), "C": ([], [])})})
    assert ok.status_code == 200, ok.text
    g = ok.json()["gate"]
    assert g["status"] == "pass" and g["checks"]["attribute"]["result"] == "pass" and g["uncertainty"] is None
    study = ok.json()["system"]["studies"][0]
    assert study["verdict"] == "pass" and study["result"]["against_reference"]["A"]["effectiveness"]["pct"] == 100
    assert eng.get("/api/msa").json()["systems"][0]["kind"] == "attribute"


def test_a_failing_or_old_attribute_study_blocks_and_a_waiver_passes_it_flagged(env):
    app, c = env
    eng = c["eng"]
    sid = eng.post("/api/msa", json={"record": {"name": "Visual check", "kind": "attribute"}}).json()["system"]["id"]
    bad = eng.post(f"/api/msa/{sid}/studies", json={"kind": "attribute", "date": date.today().isoformat(),
                                                    "input": study_input({"A": (list(range(30, 36)), []), "B": ([], list(range(6)))})}).json()
    assert bad["gate"]["status"] == "block" and bad["gate"]["blocking"] == ["attribute"] and bad["system"]["studies"][0]["verdict"] == "fail"
    waived = eng.put(f"/api/msa/{sid}/waivers/attribute", json={"reason": "customer agreed to repeat the study next month"}).json()
    assert waived["gate"]["status"] == "pass" and waived["gate"]["waived"] == ["attribute"]
    old = eng.post("/api/msa", json={"record": {"name": "Old visual check", "kind": "attribute"}}).json()["system"]["id"]
    eng.post(f"/api/msa/{old}/studies", json={"kind": "attribute", "date": "2020-01-01", "input": study_input({"A": ([], []), "B": ([], [])})})
    g = eng.get(f"/api/msa/{old}").json()["gate"]
    assert g["status"] == "block" and g["blocking"] == ["validity"] and g["checks"]["validity"]["until"] == "2021-01-01"


def test_the_kind_is_kept_and_cannot_change_under_studies(env):
    app, c = env
    eng = c["eng"]
    sid = eng.post("/api/msa", json={"record": {"name": "Gauge", "kind": "attribute"}}).json()["system"]["id"]
    eng.post(f"/api/msa/{sid}/studies", json={"kind": "attribute", "date": date.today().isoformat(), "input": study_input({"A": ([], []), "B": ([], [])})})
    kept = eng.put(f"/api/msa/{sid}", json={"record": {"name": "Gauge renamed"}}).json()  # a record without the kind keeps it
    assert kept["system"]["kind"] == "attribute" and kept["system"]["name"] == "Gauge renamed" and kept["gate"]["status"] == "pass"
    assert err(eng.put(f"/api/msa/{sid}", json={"record": {"name": "Gauge", "kind": "variable"}}))["code"] == "invalid_input"
    assert err(eng.post("/api/msa", json={"record": {"name": "X", "kind": "nonsense"}}))["code"] == "invalid_input"
    # a variable system refuses an attribute study
    var = eng.post("/api/msa", json={"record": {"name": "Variable", "resolution": 0.01, "tolerance": 1}}).json()["system"]["id"]
    assert err(eng.post(f"/api/msa/{var}/studies", json={"kind": "attribute", "date": date.today().isoformat(), "input": study_input({"A": ([], []), "B": ([], [])})}))["code"] == "invalid_input"
    # study data that cannot be evaluated are refused with the reason
    r = eng.post(f"/api/msa/{sid}/studies", json={"kind": "attribute", "date": date.today().isoformat(), "input": {"ratings": {"A": [[1, 0]] * 2, "B": [[1, 0]] * 2}, "reference": [1, 0]}})
    assert err(r)["code"] == "study_not_evaluable"
    assert err(eng.post(f"/api/msa/{sid}/studies", json={"kind": "attribute", "date": date.today().isoformat(), "input": {"values": [1]}}))["code"] == "invalid_input"


def test_a_monitor_of_counts_tied_to_an_attribute_system_and_a_report_use_the_gate(env):
    app, c = env
    eng = c["eng"]
    sid = eng.post("/api/msa", json={"record": {"name": "Gate", "kind": "attribute"}}).json()["system"]["id"]
    assert app.state.msa.gate(sid)["status"] == "block"
    eng.post(f"/api/msa/{sid}/studies", json={"kind": "attribute", "date": date.today().isoformat(), "input": study_input({"A": ([], []), "B": ([], [])})})
    assert app.state.msa.gate(sid)["status"] == "pass"
    from tests.test_api import REPORT_BODY, csv_text, upload

    ds = upload(eng, csv_text(k=20, n=5)).json()
    out = eng.post(f"/api/datasets/{ds['id']}/reports", json={**REPORT_BODY, "measurement_system_id": sid})
    assert out.status_code == 200, out.text
    html = eng.get(out.json()["urls"]["html"]).text
    assert "Measurement system Gate (attribute)" in html and "effectiveness 100 %" in html

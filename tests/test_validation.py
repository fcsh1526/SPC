import numpy as np
import pytest

from spc.validation import checks as C
from spc.validation import custom as U
from spc.validation.texts import EN, ZH
from tests.conftest import logged_in_client, make_app
from tests.test_api import err


def test_every_builtin_check_passes_and_each_has_a_text():
    results = C.run_builtin()
    assert len(results) >= 50
    failed = [(c.id, c.expected, c.actual) for c in results if not c.ok]
    assert failed == [], failed
    for c in results:
        assert c.requirement in EN and ("area." + c.area) in EN and c.reference


def test_a_check_fails_when_the_program_is_wrong(monkeypatch):
    """A suite that cannot fail proves nothing: damage the constants and the limits and the checks say so."""
    from spc.core import constants

    real = constants.d2
    monkeypatch.setattr(constants, "d2", lambda n: real(n) * 1.01)
    bad = [c.id for c in C.run_builtin() if not c.ok]
    assert any(i.startswith("V01-d2") for i in bad) and not any(i.startswith("V01-c4") for i in bad)


def test_tolerances_are_checked_as_stated():
    assert C.Check("a", "x", "r", "ref", 1.0, 1.0004, 1e-3).ok and not C.Check("a", "x", "r", "ref", 1.0, 1.01, 1e-3).ok
    assert C.Check("a", "x", "r", "ref", 9.9, 9.93, None).with_abs(0.06).ok and not C.Check("a", "x", "r", "ref", 9.9, 9.97, None).with_abs(0.06).ok
    assert C.Check("a", "x", "r", "ref", [37], [37]).ok and not C.Check("a", "x", "r", "ref", [37], []).ok
    assert not C.Check("a", "x", "r", "ref", 1.0, float("nan"), 1e-3).ok and not C.Check("a", "x", "r", "ref", 1.0, None, 1e-3).ok


def test_the_texts_of_the_report_match_in_both_languages():
    import re

    assert EN.keys() == ZH.keys()
    for k in EN:
        assert sorted(re.findall(r"\{(\w+)\}", EN[k])) == sorted(re.findall(r"\{(\w+)\}", ZH[k])), k


@pytest.mark.parametrize("change", [{"name": ""}, {"source": ""}, {"values": [1, 2]}, {"values": [1, 2, 3, 4, float("nan")]}, {"subgroup_size": 4}, {"request": {"nonsense": 1}},
                                    {"expected": []}, {"expected": [{"path": "x"}]}, {"expected": [{"path": "x", "value": 1, "tol": 2}]}, {"extra": 1}])
def test_bad_cases_are_refused(change):
    good = {"name": "n", "source": "book", "values": [1.0, 2, 3, 4, 5], "expected": [{"path": "indices.mean", "value": 3}]}
    U.validate_case(good)
    with pytest.raises(ValueError):
        U.validate_case({**good, **change})


@pytest.fixture
def env():
    app = make_app()
    return app, {u: logged_in_client(app, u) for u in ("admin", "eng", "view")}


def case_record(name="Reference 1", expected=None):
    v = np.random.default_rng(77).normal(50, 2, 40)
    sd = float(v.std(ddof=1))
    exp = expected or [{"path": "indices.mean", "value": float(v.mean()), "tol": 1e-9}, {"path": "indices.sd", "value": sd, "tol": 1e-9},
                       {"path": "indices.pk", "value": min(56 - v.mean(), v.mean() - 44) / (3 * sd), "tol": 1e-9}]
    return {"name": name, "description": "customer sample", "source": "supplier calculation of 2026-02-03", "values": [float(x) for x in v],
            "request": {"stage": "production", "lsl": 44, "usl": 56}, "expected": exp}


def test_a_run_without_reference_cases_verifies_but_does_not_claim_validation(env):
    app, c = env
    assert c["view"].post("/api/validation/runs").status_code == 403
    run = c["eng"].post("/api/validation/runs").json()
    assert run["verdict"] == "verified" and run["verification"]["failed"] == 0 and run["validation"]["status"] == "none" and run["intact"]
    assert run["parameters"]["alpha"] == pytest.approx(0.0027, abs=1e-4) and run["scipy"] and run["numpy"]
    html = c["view"].get(f"/api/validation/runs/{run['id']}/report").text
    assert "NOT shown" in html and run["digest"] in html and "Verification" in html
    zh = c["view"].get(f"/api/validation/runs/{run['id']}/report?lang=zh-TW").text
    assert "未顯示" in zh and "查證" in zh and "確效" in zh
    assert c["view"].get(f"/api/validation/runs/{run['id']}/report?download=1").headers["content-disposition"].startswith("attachment")
    assert err(c["view"].get("/api/validation/runs/99"))["code"] == "validation_run_not_found"


def test_reference_cases_are_validated_with_the_settings_of_the_user(env):
    app, c = env
    r = c["eng"].post("/api/validation/cases", json={"record": case_record()})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert c["view"].post("/api/validation/cases", json={"record": case_record("x")}).status_code == 403
    assert err(c["eng"].post("/api/validation/cases", json={"record": case_record()}))["code"] == "validation_case_name_taken"
    assert err(c["eng"].post("/api/validation/cases", json={"record": {**case_record("y"), "source": ""}}))["code"] == "invalid_input"
    assert c["view"].get("/api/validation/cases").json()["cases"][0]["n_values"] == 40
    run = c["eng"].post("/api/validation/runs").json()
    assert run["verdict"] == "pass" and run["validation"]["status"] == "pass" and run["validation"]["cases"][0]["definition"]["values"][0] == case_record()["values"][0]
    # a wrong expectation, a path that does not exist: failed, with the reason
    bad = case_record(expected=[{"path": "indices.pk", "value": 9.99, "tol": 1e-6}, {"path": "indices.nothing", "value": 1, "tol": 1e-6}])
    c["eng"].put(f"/api/validation/cases/{cid}", json={"record": bad})
    run2 = c["eng"].post("/api/validation/runs").json()
    assert run2["verdict"] == "fail" and run2["validation"]["failed"] == 2 and run2["verification"]["failed"] == 0
    assert "no such number" in str(run2["validation"]["cases"][0]["checks"][1]["note"])
    assert "FAILED" in c["eng"].get(f"/api/validation/runs/{run2['id']}/report").text and "失敗" in c["eng"].get(f"/api/validation/runs/{run2['id']}/report?lang=zh-TW").text
    # the first run still says what it said, with the case as it was then
    assert c["eng"].get(f"/api/validation/runs/{run['id']}").json()["verdict"] == "pass"
    assert [x["id"] for x in c["view"].get("/api/validation/runs").json()["runs"]] == [run2["id"], run["id"]]
    assert c["eng"].delete(f"/api/validation/cases/{cid}").status_code == 403 and c["admin"].delete(f"/api/validation/cases/{cid}").status_code == 200
    actions = {e["action"] for e in app.state.audit.list(100)}
    assert {"validation_case_created", "validation_case_updated", "validation_case_deleted", "validation_run"} <= actions and app.state.audit.verify()["ok"]


def test_a_changed_run_record_is_detected(env):
    app, c = env
    run = c["eng"].post("/api/validation/runs").json()
    row = app.state.db.one("SELECT data FROM validation_runs WHERE id = ?", (run["id"],))
    app.state.db.execute("UPDATE validation_runs SET data = ? WHERE id = ?", (row["data"].replace('"verdict":"verified"', '"verdict":"pass"'), run["id"]))
    assert c["eng"].get(f"/api/validation/runs/{run['id']}").json()["intact"] is False


def test_the_case_that_cannot_be_analysed_fails_instead_of_stopping_the_run(env):
    app, c = env
    rec = {**case_record("Broken"), "request": {"stage": "production", "chart": "no-such-chart"}}
    assert c["eng"].post("/api/validation/cases", json={"record": rec}).status_code == 200
    run = c["eng"].post("/api/validation/runs").json()
    assert run["verdict"] == "fail" and all(not x["ok"] for x in run["validation"]["cases"][0]["checks"])

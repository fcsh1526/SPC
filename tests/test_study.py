import numpy as np
import pytest

from spc.data import Dataset
from spc.study import checklist as cl
from tests.conftest import PASSWORD, logged_in_client, make_app
from tests.test_api import err

RECORD = {"name": "Grinder 4 – bore", "machine": "Grinder 4", "characteristic": "bore diameter", "station": "spindle 1", "unit": "mm",
          "specs": {"lsl": 9.9, "usl": 10.1}}
MANUAL = [i.key for i in cl.ITEMS if not i.auto]


@pytest.fixture
def env():
    app = make_app()
    return app, {u: logged_in_client(app, u) for u in ("admin", "eng", "view")}


def add_dataset(app, n=50, mean=10.0, timestamps=True):
    rng = np.random.default_rng(3)
    x = [float(v) for v in rng.normal(mean, 0.02, n)]
    times = [f"2026-03-02T08:{i // 60:02d}:{i % 60:02d}" for i in range(n)] if timestamps else None
    ds = Dataset.from_values(x, timestamp=times)
    return app.state.store.add(ds, 1, "bore")


def confirm_all(client, sid):
    for key in MANUAL:
        assert client.put(f"/api/studies/{sid}/items/{key}", json={"status": "ok"}).status_code == 200


# ------------------------------------------------------------------ the record

@pytest.mark.parametrize("change", [
    {"name": " "}, {"characteristic": ""}, {"specs": {"lsl": 5, "usl": 4}}, {"specs": {"natural": "lsl"}}, {"specs": {"lsl": 0, "natural": "x", "usl": 1}},
    {"specs": {"lsl": "a"}}, {"preproduction": {"five": [1, 2, 3]}}, {"preproduction": {"nonsense": 1}}, {"sample": {"dressing_cycles": -1}},
    {"nonsense": 1}, {"dataset_id": ""}, {"name": "x" * 101},
])
def test_bad_records_are_refused(change):
    with pytest.raises(ValueError):
        cl.validate_record({**RECORD, **change})


# ------------------------------------------------------------------ the automatic items of the draft

def result(record_extra, facts=None, **kw):
    record = cl.validate_record({**RECORD, **record_extra})
    return cl.auto_results(record, facts, **kw)


FACTS = {"n_total": 50, "n_valid": 50, "n_invalid": 0, "has_timestamps": True, "mean": 10.0, "n_restarts": 0, "normality": {"test": "shapiro-wilk", "p_value": 0.4}}


def test_sample_size_needs_50_parts_or_an_approval_of_the_customer():
    assert result({}, FACTS)["sample_size"]["result"] == "pass"
    few = {**FACTS, "n_valid": 30, "n_total": 30}
    assert result({}, few)["sample_size"]["result"] == "fail"
    approved = {"sample": {"reduced_approved_by": "customer quality, J. Doe", "reduced_reason": "only 30 parts available"}}
    assert result(approved, few)["sample_size"]["result"] == "warn"
    assert result({"sample": {"reduced_approved_by": "J. Doe"}}, few)["sample_size"]["result"] == "fail"  # approval without a reason is not enough
    assert result({}, None)["sample_size"]["result"] == "unknown" and result({}, None, dataset_missing=True)["sample_size"]["reason"] == "dataset_missing"


def test_a_process_with_high_tool_wear_must_cover_one_and_a_half_dressing_cycles():
    assert result({}, FACTS)["tool_wear_cycles"]["result"] == "not_needed"
    assert result({"sample": {"tool_wear_high": True}}, FACTS)["tool_wear_cycles"]["result"] == "fail"
    assert result({"sample": {"tool_wear_high": True, "dressing_cycles": 1.4}}, FACTS)["tool_wear_cycles"]["result"] == "fail"
    assert result({"sample": {"tool_wear_high": True, "dressing_cycles": 1.5}}, FACTS)["tool_wear_cycles"]["result"] == "pass"


def test_the_machine_is_adjusted_close_to_the_middle_of_the_tolerance():
    assert result({}, FACTS)["adjusted_to_center"]["result"] == "pass"
    assert result({}, {**FACTS, "mean": 10.0125})["adjusted_to_center"]["result"] == "pass"  # 12.5 % of the tolerance 0.2 = 0.025: offset 0.0125 is 6 %
    assert result({}, {**FACTS, "mean": 10.04})["adjusted_to_center"]["result"] == "warn"
    assert result({"specs": {"lsl": 0.0, "usl": 0.1, "natural": "lsl"}}, FACTS)["adjusted_to_center"]["result"] == "not_needed"


@pytest.mark.parametrize("one,ok", [(10.0, True), (10.024, True), (10.026, False), (9.976, True), (9.974, False)])
def test_pre_production_run_of_one_part_two_sided(one, ok):
    r = result({"preproduction": {"one": one}})["preproduction_1"]  # tolerance 0.2: centre 10.0 +- 12.5 % = +-0.025
    assert r["result"] == ("pass" if ok else "fail")


def test_pre_production_run_of_five_parts_checks_the_mean_and_the_range():
    good = result({"preproduction": {"five": [10.0, 10.01, 9.99, 10.02, 10.0]}})["preproduction_5"]
    assert good["result"] == "pass" and good["range"] == pytest.approx(0.03)
    wide = result({"preproduction": {"five": [9.95, 10.05, 10.0, 10.0, 10.0]}})["preproduction_5"]  # range 0.1 >= 25 % of 0.2
    assert wide["result"] == "fail" and wide["range_ok"] is False and wide["location_ok"] is True
    off = result({"preproduction": {"five": [10.04, 10.05, 10.04, 10.05, 10.04]}})["preproduction_5"]
    assert off["result"] == "fail" and off["location_ok"] is False
    assert result({})["preproduction_5"]["result"] == "not_done" and result({})["preproduction_1"]["result"] == "not_done"


def test_pre_production_run_with_a_natural_limit_uses_62_5_percent_of_the_tolerance():
    spec = {"lsl": 0.0, "usl": 0.08, "natural": "lsl"}  # flatness: 0 is the natural limit, area 0 .. 0.05
    assert result({"specs": spec, "preproduction": {"one": 0.05}})["preproduction_1"]["result"] == "pass"
    assert result({"specs": spec, "preproduction": {"one": 0.051}})["preproduction_1"]["result"] == "fail"
    upper = {"lsl": 0.0, "usl": 0.08, "natural": "usl"}  # the natural limit on top: area 0.03 .. 0.08
    assert result({"specs": upper, "preproduction": {"one": 0.03}})["preproduction_1"]["result"] == "pass"
    assert result({"specs": upper, "preproduction": {"one": 0.029}})["preproduction_1"]["result"] == "fail"
    assert result({"specs": {"lsl": None, "usl": 0.08}, "preproduction": {"one": 0.01}})["preproduction_1"]["result"] == "unknown"


def test_traceability_and_numeric_data_and_the_distribution_come_from_the_data_set():
    r = result({}, FACTS)
    assert r["traceability"]["result"] == "pass" and r["data_numeric"]["result"] == "pass" and r["distribution"]["p_value"] == 0.4
    r = result({}, {**FACTS, "has_timestamps": False, "n_invalid": 2, "normality": None})
    assert (r["traceability"]["result"], r["data_numeric"]["result"], r["distribution"]["result"]) == ("warn", "warn", "unknown")
    assert result({"station": ""}, FACTS)["stations"]["result"] == "warn"


# ------------------------------------------------------------------ the API

def test_roles_and_the_whole_life_of_a_study(env):
    app, c = env
    assert c["view"].post("/api/studies", json={"record": RECORD}).status_code == 403
    sid = c["eng"].post("/api/studies", json={"record": {**RECORD, "dataset_id": add_dataset(app)}}).json()["study"]["id"]
    assert c["view"].get(f"/api/studies/{sid}").status_code == 200 and c["view"].get("/api/studies").json()["studies"][0]["ready"] is False
    view = c["eng"].get(f"/api/studies/{sid}").json()
    ev = view["evaluation"]
    assert ev["ready"] is False and set(ev["blockers"]) == set(MANUAL)  # nothing confirmed yet; every auto item passes
    assert {r["key"]: r["effective"] for r in ev["items"]}["sample_size"] == "ok"
    assert c["eng"].post(f"/api/studies/{sid}/close", json={"reason": "done"}).status_code == 409
    confirm_all(c["eng"], sid)
    ev = c["eng"].get(f"/api/studies/{sid}").json()["evaluation"]
    assert ev["ready"] is True and ev["blockers"] == []
    assert err(c["eng"].post(f"/api/studies/{sid}/close", json={"reason": " "}))["code"] == "reason_required"
    closed = c["eng"].post(f"/api/studies/{sid}/close", json={"reason": "all conditions met, data set checked"}).json()["study"]["closed"]
    assert closed["by"].startswith("Eva Engineer") and closed["reason"].startswith("all conditions")
    for call in (lambda: c["eng"].put(f"/api/studies/{sid}", json={"record": RECORD}), lambda: c["eng"].put(f"/api/studies/{sid}/items/human", json={"status": "open"})):
        assert err(call())["code"] == "study_closed"
    assert err(c["eng"].post(f"/api/studies/{sid}/reopen", json={"reason": ""}))["code"] == "reason_required"
    assert c["eng"].post(f"/api/studies/{sid}/reopen", json={"reason": "a tool was changed"}).json()["study"]["closed"] is None
    assert c["eng"].delete(f"/api/studies/{sid}").status_code == 403
    assert c["admin"].delete(f"/api/studies/{sid}").status_code == 200 and err(c["eng"].get(f"/api/studies/{sid}"))["code"] == "study_not_found"


def test_a_failed_automatic_item_blocks_until_a_deviation_with_a_reason_is_recorded(env):
    app, c = env
    sid = c["eng"].post("/api/studies", json={"record": {**RECORD, "dataset_id": add_dataset(app, n=30)}}).json()["study"]["id"]
    confirm_all(c["eng"], sid)
    ev = c["eng"].get(f"/api/studies/{sid}").json()["evaluation"]
    assert ev["blockers"] == ["sample_size"] and ev["ready"] is False
    assert err(c["eng"].put(f"/api/studies/{sid}/items/sample_size", json={"status": "deviation", "note": ""}))["code"] == "note_required"
    c["eng"].put(f"/api/studies/{sid}/items/sample_size", json={"status": "deviation", "note": "customer agreed to 30 parts, e-mail of 2026-03-01"})
    ev = c["eng"].get(f"/api/studies/{sid}").json()["evaluation"]
    assert ev["ready"] is True and ev["flagged"] == ["sample_size"]
    row = next(r for r in ev["items"] if r["key"] == "sample_size")
    assert row["effective"] == "deviation" and row["by"].startswith("Eva") and row["note"].startswith("customer agreed")
    closed = c["eng"].post(f"/api/studies/{sid}/close", json={"reason": "closed with a recorded deviation"}).json()["study"]["closed"]
    assert closed["flagged"] == ["sample_size"]
    # the same approval can be given in the record instead: the item becomes a remark, not a failure
    sid2 = c["eng"].post("/api/studies", json={"record": {**RECORD, "name": "second", "dataset_id": add_dataset(app, n=30),
                                                          "sample": {"reduced_approved_by": "J. Doe", "reduced_reason": "parts"}}}).json()["study"]["id"]
    row2 = next(r for r in c["eng"].get(f"/api/studies/{sid2}").json()["evaluation"]["items"] if r["key"] == "sample_size")
    assert row2["effective"] == "warn" and row2["blocking"] is False


def test_manual_items_need_a_note_where_the_draft_asks_for_an_agreement(env):
    app, c = env
    sid = c["eng"].post("/api/studies", json={"record": RECORD}).json()["study"]["id"]
    assert err(c["eng"].put(f"/api/studies/{sid}/items/human", json={"status": "not_applicable"}))["code"] == "note_required"
    assert err(c["eng"].put(f"/api/studies/{sid}/items/nonsense", json={"status": "ok"}))["code"] == "unknown_item"
    assert c["eng"].put(f"/api/studies/{sid}/items/human", json={"status": "boss"}).status_code == 400
    c["eng"].put(f"/api/studies/{sid}/items/human", json={"status": "not_applicable", "note": "automatic line, no operator"})
    row = next(r for r in c["eng"].get(f"/api/studies/{sid}").json()["evaluation"]["items"] if r["key"] == "human")
    assert row["effective"] == "not_applicable" and row["blocking"] is False
    c["eng"].put(f"/api/studies/{sid}/items/human", json={"status": "open"})  # taking a confirmation back
    row = next(r for r in c["eng"].get(f"/api/studies/{sid}").json()["evaluation"]["items"] if r["key"] == "human")
    assert row["status"] == "open" and row["blocking"] is True
    without_data = c["eng"].get(f"/api/studies/{sid}").json()["evaluation"]
    assert "sample_size" in without_data["blockers"]  # no data set linked yet: the automatic items cannot pass
    assert c["eng"].post("/api/studies", json={"record": RECORD}).status_code == 409
    assert err(c["eng"].post("/api/studies", json={"record": {**RECORD, "name": "x", "dataset_id": "nope"}}))["code"] == "dataset_not_found"
    assert c["eng"].put(f"/api/studies/{sid}", json={"record": {**RECORD, "dataset_id": "nope"}}).status_code == 404


def test_everything_is_in_the_audit_trail_and_a_deleted_data_set_is_reported(env):
    app, c = env
    ds = add_dataset(app)
    sid = c["eng"].post("/api/studies", json={"record": {**RECORD, "dataset_id": ds}}).json()["study"]["id"]
    c["eng"].put(f"/api/studies/{sid}/items/human", json={"status": "ok"})
    actions = [e["action"] for e in app.state.audit.list(100)]
    assert "study_created" in actions and "study_item" in actions and app.state.audit.verify()["ok"] is True
    app.state.db.execute("DELETE FROM datasets WHERE id = ?", (ds,))
    ev = c["eng"].get(f"/api/studies/{sid}").json()["evaluation"]
    sample = next(r for r in ev["items"] if r["key"] == "sample_size")
    assert sample["result"]["reason"] == "dataset_missing" and sample["blocking"] is True

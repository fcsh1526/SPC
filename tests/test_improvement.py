"""Improvement cycles: the PDCA of control loop 3 (draft 5.4)."""

import numpy as np
import pytest

from tests.test_api import err
from tests.test_monitor import CONFIG, ack, env, fill, make_monitor  # noqa: F401 (env is a fixture)

PLAN = {"title": "Reduce the spread of the bore", "problem": "Pk is only 1.6", "kpi": "pk", "target": 2.5, "plan": "Replace the worn spindle bearings and re-qualify the setup"}


def started(c, points=40, seed=31, name="Shaft diameter"):
    mid = make_monitor(c["eng"], {**CONFIG, "name": name, "require_ack": False})
    fill(c, mid, np.random.default_rng(seed), points)
    return mid


def create(c, mid, **kw):
    r = c["eng"].post("/api/improvements", json={"record": {**PLAN, "monitor_id": mid, **kw}})
    assert r.status_code == 200, r.text
    return r.json()


def test_an_improvement_goes_through_plan_do_check_act_and_becomes_the_standard(env):
    app, c = env
    mid = started(c)
    imp = create(c, mid, due="2026-12-31")
    assert imp["status"] == "planned" and imp["monitor_name"] == "Shaft diameter" and imp["kpi"] == "pk" and imp["target"] == 2.5
    base = imp["baseline"]
    assert base["name"] == "Cpk" and 1.2 < base["value"] < 2.2 and base["stable"] and base["points"] == 40 and imp["baseline_missing"] is None  # the ongoing report at the time of the plan
    assert [h["step"] for h in imp["history"]] == ["plan"]
    iid = imp["id"]
    # check before do, and check without enough points after it
    assert err(c["eng"].post(f"/api/improvements/{iid}/verify"))["code"] == "wrong_step"
    assert err(c["eng"].post(f"/api/improvements/{iid}/implement", json={"text": "x"}))["code"] == "invalid_input"
    done = c["eng"].post(f"/api/improvements/{iid}/implement", json={"text": "New bearings fitted, setup re-qualified on 2026-05-04"})
    assert done.status_code == 200 and done.json()["status"] == "implemented" and done.json()["implementation"]["after_seq"] == 40
    fill(c, mid, np.random.default_rng(32), 10, sigma=0.04, label="n")
    early = c["eng"].post(f"/api/improvements/{iid}/verify")
    assert early.status_code == 409 and err(early)["code"] == "verification_too_early" and early.json()["error"]["params"] == {"have": 10, "need": 25}
    fill(c, mid, np.random.default_rng(33), 20, sigma=0.04, label="m")
    v = c["eng"].post(f"/api/improvements/{iid}/verify")
    assert v.status_code == 200, v.text
    ver = v.json()["verification"]
    assert v.json()["status"] == "verified" and ver["effective"] is True and ver["value"] > 2.5 and ver["stable"] and ver["points"] == 30 and ver["from_seq"] == 41 and ver["to_seq"] == 70
    # a verification that is not effective cannot be rework, and an effective one is standardised with a note
    assert err(c["eng"].post(f"/api/improvements/{iid}/rework", json={"text": "again"}))["code"] == "not_ineffective"
    assert err(c["eng"].post(f"/api/improvements/{iid}/standardise", json={"text": ""}))["code"] == "invalid_input"
    std = c["eng"].post(f"/api/improvements/{iid}/standardise", json={"text": "Control plan 4 revision B: new bearing check every 2000 parts"})
    assert std.status_code == 200 and std.json()["status"] == "standardised" and [h["step"] for h in std.json()["history"]] == ["plan", "do", "check", "act"]
    assert err(c["eng"].post(f"/api/improvements/{iid}/verify"))["code"] == "wrong_step"
    audit = [r["action"] for r in app.state.db.all("SELECT action FROM audit WHERE action LIKE 'improvement_%' ORDER BY id")]
    assert audit == ["improvement_planned", "improvement_implemented", "improvement_verified", "improvement_standardised"]


def test_an_improvement_that_misses_its_target_is_reworked_with_a_new_plan(env):
    app, c = env
    mid = started(c)
    iid = create(c, mid)["id"]
    c["eng"].post(f"/api/improvements/{iid}/implement", json={"text": "Changed the coolant"})
    fill(c, mid, np.random.default_rng(34), 30, sigma=0.1, label="n")  # the spread did not change
    v = c["eng"].post(f"/api/improvements/{iid}/verify").json()
    assert v["status"] == "verified" and v["verification"]["effective"] is False and v["verification"]["value"] < 2.5
    assert err(c["eng"].post(f"/api/improvements/{iid}/standardise", json={"text": "make it the standard"}))["code"] == "not_effective"
    back = c["eng"].post(f"/api/improvements/{iid}/rework", json={"text": "Replace the spindle bearings instead"})
    assert back.status_code == 200 and back.json()["status"] == "planned" and back.json()["plan"] == "Replace the spindle bearings instead"
    assert back.json()["implementation"] is None and back.json()["verification"] is None and [h["step"] for h in back.json()["history"]] == ["plan", "do", "check", "act"]
    # an unstable process is not effective even when the index reaches the target: the level jumps after the implementation
    mid2 = started(c, seed=35, name="Second bore")
    j = create(c, mid2, target=0.5)["id"]
    c["eng"].post(f"/api/improvements/{j}/implement", json={"text": "Retuned"})
    fill(c, mid2, np.random.default_rng(36), 15, mu=10.0, sigma=0.04, label="s")
    fill(c, mid2, np.random.default_rng(37), 15, mu=10.3, sigma=0.04, label="t")  # the level jumps inside the window: the analysis chart is not stable
    unstable = c["eng"].post(f"/api/improvements/{j}/verify").json()["verification"]
    assert unstable["stable"] is False and unstable["effective"] is False and unstable["value"] > 0.5


def test_the_record_the_roles_and_the_missing_pieces(env):
    app, c = env
    mid = started(c)
    for bad in ({"title": ""}, {"monitor_id": 999}, {"monitor_id": True}, {"target": 0}, {"target": "high"}, {"kpi": "cpk"}, {"plan": "x"}, {"due": "soon"}, {"nonsense": 1}):
        r = c["eng"].post("/api/improvements", json={"record": {**PLAN, "monitor_id": mid, **bad}})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_input", bad
    assert c["oper"].post("/api/improvements", json={"record": {**PLAN, "monitor_id": mid}}).status_code == 403
    assert c["view"].get("/api/improvements").status_code == 200 and c["view"].get("/api/improvements/999").status_code == 404
    first = create(c, mid)
    assert [i["status"] for i in c["view"].get("/api/improvements?status=planned").json()["improvements"]] == ["planned"]
    assert c["oper"].post(f"/api/improvements/{first['id']}/implement", json={"text": "done"}).status_code == 403
    assert c["eng"].delete(f"/api/improvements/{first['id']}").status_code == 403 and c["admin"].delete(f"/api/improvements/{first['id']}").status_code == 200
    # a monitor without specification limits has no index to follow, a monitor with few points has no baseline yet
    nospec = make_monitor(c["eng"], {**CONFIG, "name": "NoSpec", "specs": {}, "require_ack": False})
    fill(c, nospec, np.random.default_rng(37), 30)
    young = c["eng"].post("/api/improvements", json={"record": {**PLAN, "monitor_id": nospec}})
    assert young.status_code == 200 and young.json()["baseline"] is None and young.json()["baseline_missing"] == "kpi_not_available"
    c["eng"].post(f"/api/improvements/{young.json()['id']}/implement", json={"text": "done"})
    fill(c, nospec, np.random.default_rng(38), 30, label="n")
    assert err(c["eng"].post(f"/api/improvements/{young.json()['id']}/verify"))["code"] == "kpi_not_available"
    fresh = make_monitor(c["eng"], {**CONFIG, "name": "Fresh", "require_ack": False})
    new = c["eng"].post("/api/improvements", json={"record": {**PLAN, "monitor_id": fresh}}).json()
    assert new["baseline"] is None and new["baseline_missing"] == "not_enough_points"

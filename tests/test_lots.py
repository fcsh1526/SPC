"""Lots and their disposition: the quality conformance gate (draft 5.4, control loop 2)."""

import time

import pytest

from tests.test_api import err
from tests.test_monitor import CONFIG, OK_SAMPLE, ack, enter, env, make_monitor, open_incident  # noqa: F401 (env is a fixture)


def lot(c, who="oper", **kw):
    body = {"lot_no": "L-001", "quantity": 500, **kw}
    return c[who].post("/api/lots", json={"record": body})


def clean_monitor(c, n=3):
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    for _ in range(n):
        enter(c["oper"], mid, OK_SAMPLE)
    return mid


def decide(c, lid, who="oper", **body):
    return c[who].post(f"/api/lots/{lid}/decision", json=body)


def test_a_lot_with_clean_evidence_can_be_released_by_an_operator(env):
    app, c = env
    mid = clean_monitor(c)
    r = lot(c, monitor_id=mid, seq_from=1, seq_to=3, product="Bracket", characteristic="bore")
    assert r.status_code == 200, r.text
    v = r.json()
    ev = v["evidence"]
    assert v["status"] == "open" and ev["clean"] and ev["valid_points"] == 3 and ev["blocking"] == [] and ev["alarm_points"] == [] and v["history"][0]["action"] == "created"
    done = decide(c, v["id"], decision="release")
    assert done.status_code == 200 and done.json()["status"] == "decided" and done.json()["decision"]["kind"] == "release" and done.json()["decision"]["role"] == "operator"
    assert done.json()["evidence"]["clean"] is True and [h["action"] for h in done.json()["history"]] == ["created", "decided"]
    assert err(decide(c, v["id"], decision="scrap", reason="no longer wanted"))["code"] == "lot_decided"  # a decision stands until it is reopened


def test_an_open_incident_in_the_range_blocks_a_release_and_sorting_is_the_way_out(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    enter(c["oper"], mid, OK_SAMPLE)
    iid = open_incident(c, mid)  # point 2
    lid = lot(c, monitor_id=mid, seq_from=1, seq_to=2).json()["id"]
    ev = c["view"].get(f"/api/lots/{lid}").json()["evidence"]
    assert not ev["clean"] and ev["blocking"][0]["code"] == "open_incident" and ev["blocking"][0]["incident"] == iid and ev["alarm_points"] == [2]
    blocked = decide(c, lid, decision="release")
    assert err(blocked)["code"] == "release_blocked" and blocked.status_code == 409 and blocked.json()["error"]["params"]["reasons"][0]["code"] == "open_incident"
    # sorting: every part is inspected, the counts add up, the rejected ones go on
    assert err(decide(c, lid, decision="sort", sorted={"inspected": 400, "good": 390, "rejected": 10, "rejected_to": "scrap"}))["code"] == "sort_incomplete"
    assert err(decide(c, lid, decision="sort", sorted={"inspected": 500, "good": 480, "rejected": 10, "rejected_to": "scrap"}))["code"] == "sort_counts"
    assert err(decide(c, lid, decision="sort", sorted={"inspected": 500, "good": 480, "rejected": 20}))["code"] == "sort_rejected_to"
    done = decide(c, lid, decision="sort", reason="100 % sorting at the line", sorted={"inspected": 500, "good": 478, "rejected": 22, "rejected_to": "rework"})
    assert done.status_code == 200 and done.json()["decision"]["sorted"] == {"inspected": 500, "good": 478, "rejected": 22, "rejected_to": "rework"}
    assert done.json()["evidence"]["blocking"][0]["code"] == "open_incident"  # the evidence is fixed at the decision


def test_a_concession_needs_an_engineer_a_reason_and_the_approval_of_the_customer(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    open_incident(c, mid)
    lid = lot(c, monitor_id=mid, seq_from=1, seq_to=1).json()["id"]
    assert err(decide(c, lid, decision="concession", reason="urgent", customer_ref="SCAR-7"))["code"] == "concession_needs_engineer"
    assert err(decide(c, lid, "eng", decision="concession", reason="urgent"))["code"] == "concession_needs_approval"
    assert err(decide(c, lid, "eng", decision="concession", customer_ref="SCAR-7"))["code"] == "concession_needs_approval"
    done = decide(c, lid, "eng", decision="concession", reason="Customer accepts the lot with a 100 % check at goods in", customer_ref="Deviation 2026-031, J. Smith")
    assert done.status_code == 200 and done.json()["decision"]["customer_ref"].startswith("Deviation") and done.json()["decision"]["role"] == "engineer"
    row = app.state.db.one("SELECT detail FROM audit WHERE action = 'lot_decided' ORDER BY id DESC LIMIT 1")
    assert '"concession"' in row["detail"] and "Deviation 2026-031" in row["detail"]  # the approval of the customer is in the audit chain


def test_a_recovered_alarm_is_a_remark_and_an_escalated_one_blocks(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    iid = open_incident(c, mid)
    url = f"/api/monitors/{mid}/incidents/{iid}"
    c["oper"].post(f"{url}/events", json={"kind": "action", "step": "adjust_parameters", "text": "Raised the feed"})
    time.sleep(1.1)
    enter(c["oper"], mid, OK_SAMPLE)
    assert c["oper"].post(f"{url}/close", json={"outcome": "recovered", "text": "Control is back"}).status_code == 200
    lid = lot(c, monitor_id=mid, seq_from=1, seq_to=2).json()["id"]
    ev = c["view"].get(f"/api/lots/{lid}").json()["evidence"]
    assert ev["clean"] and ev["remarks"][0]["code"] == "alarm_answered" and ev["remarks"][0]["outcome"] == "recovered"
    # a second monitor whose incident goes to root cause analysis
    m2 = make_monitor(c["eng"], {**CONFIG, "name": "Second"})
    ack(c["oper"], m2)
    i2 = open_incident(c, m2)
    u2 = f"/api/monitors/{m2}/incidents/{i2}"
    c["oper"].post(f"{u2}/events", json={"kind": "escalation", "step": "other", "text": "Called the engineer"})
    c["eng"].post(f"{u2}/events", json={"kind": "action", "step": "containment", "text": "Sorted"})
    c["eng"].post(f"{u2}/events", json={"kind": "action", "step": "root_cause", "text": "8D started"})
    assert c["eng"].post(f"{u2}/close", json={"outcome": "escalated", "text": "8D opened"}).status_code == 200
    l2 = lot(c, lot_no="L-002", monitor_id=m2, seq_from=1, seq_to=1).json()["id"]
    assert c["view"].get(f"/api/lots/{l2}").json()["evidence"]["blocking"][0]["code"] == "escalated_incident"


def test_points_outside_the_range_and_invalid_points_are_handled(env):
    app, c = env
    mid = clean_monitor(c, 2)
    open_incident(c, mid)  # point 3, after the range of the lot
    ok = lot(c, monitor_id=mid, seq_from=1, seq_to=2).json()
    assert ok["evidence"]["clean"]  # an incident after the end of the range does not concern the lot
    after = lot(c, lot_no="L-002", monitor_id=mid, seq_from=1, seq_to=3).json()
    assert not after["evidence"]["clean"]
    empty = lot(c, lot_no="L-003", monitor_id=mid, seq_from=50, seq_to=60).json()
    assert empty["evidence"]["blocking"][0]["code"] == "no_points" and err(decide(c, empty["id"], decision="release"))["code"] == "release_blocked"


def test_a_lot_without_a_monitor_is_released_by_an_engineer_with_a_reason(env):
    app, c = env
    lid = lot(c).json()["id"]
    assert c["oper"].get(f"/api/lots/{lid}").json()["evidence"] is None
    assert err(decide(c, lid, decision="release"))["code"] == "release_needs_evidence" and decide(c, lid, decision="release").status_code == 403
    assert err(decide(c, lid, "eng", decision="release"))["code"] == "reason_required"
    done = decide(c, lid, "eng", decision="release", reason="Supplier certificate checked, no process data")
    assert done.status_code == 200 and done.json()["evidence"] is None


def test_rework_scrap_hold_reopen_roles_and_audit(env):
    app, c = env
    ids = [lot(c, lot_no=f"L-{i}").json()["id"] for i in (1, 2, 3)]
    assert err(decide(c, ids[0], decision="scrap"))["code"] == "reason_required"
    assert decide(c, ids[0], decision="scrap", reason="Broken tool, parts out of tolerance").status_code == 200
    assert err(decide(c, ids[1], decision="rework"))["code"] == "reason_required"
    assert decide(c, ids[1], decision="rework", reason="Deburr and re-inspect").json()["decision"]["kind"] == "rework"
    held = c["oper"].post(f"/api/lots/{ids[2]}/hold", json={"reason": "waiting for the measurement"})
    assert held.status_code == 200 and held.json()["status"] == "held"
    assert err(c["oper"].post(f"/api/lots/{ids[2]}/hold", json={"reason": "again"}))["code"] == "lot_held"
    assert err(c["oper"].post(f"/api/lots/{ids[0]}/hold", json={"reason": "late"}))["code"] == "lot_decided"
    assert c["oper"].post(f"/api/lots/{ids[0]}/reopen", json={"reason": "wrong lot"}).status_code == 403  # an engineer reopens
    assert err(c["eng"].post(f"/api/lots/{ids[2]}/reopen", json={"reason": "x" * 5}))["code"] == "lot_not_decided"
    back = c["eng"].post(f"/api/lots/{ids[0]}/reopen", json={"reason": "Scrapped the wrong lot"})
    assert back.status_code == 200 and back.json()["status"] == "open" and back.json()["decision"] is None
    assert [h["action"] for h in back.json()["history"]] == ["created", "decided", "reopened"] and back.json()["history"][2]["detail"]["was"] == "scrap"
    assert [l["status"] for l in c["view"].get("/api/lots?status=decided").json()["lots"]] == ["decided"]
    assert len(c["view"].get("/api/lots").json()["lots"]) == 3
    assert c["view"].post("/api/lots", json={"record": {"lot_no": "X", "quantity": 1}}).status_code == 403
    assert c["eng"].delete(f"/api/lots/{ids[0]}").status_code == 403 and c["admin"].delete(f"/api/lots/{ids[0]}").status_code == 200


def test_the_record_is_checked(env):
    app, c = env
    mid = clean_monitor(c, 1)
    assert lot(c).status_code == 200 and err(lot(c))["code"] == "lot_number_taken"
    assert lot(c, lot_no="l-001").status_code == 409  # numbers are the same whatever the case
    for bad in ({"lot_no": ""}, {"quantity": 0}, {"quantity": 1.5}, {"monitor_id": mid}, {"monitor_id": mid, "seq_from": 3, "seq_to": 1}, {"seq_from": 1, "seq_to": 2},
                {"monitor_id": 9999, "seq_from": 1, "seq_to": 2}, {"parent_lot_id": 9999}, {"nonsense": 1}, {"note": "x" * 3000}):
        r = lot(c, **{"lot_no": "L-bad", **bad})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_input", bad
    assert c["view"].get("/api/lots/9999").status_code == 404
    parent = lot(c, lot_no="L-parent").json()["id"]
    child = lot(c, lot_no="L-child", parent_lot_id=parent, quantity=22)
    assert child.status_code == 200 and child.json()["parent_lot_id"] == parent

import numpy as np
import pytest

from spc.plan import model as M
from spc.plan import roles as R
from tests.conftest import PASSWORD, logged_in_client, make_app
from tests.test_api import err
from tests.test_msa import build_system

# Table 6-1 of the draft, read from the printed table (columns in the order of the table; None = a dash)
TABLE_COLUMNS = ("line_operator", "line_supervisor", "process_planner", "equipment_procurer", "inspection_planner", "quality_planning", "product_developer")
TABLE_ROWS = {
    "qms":                 (1, 1, 1, 1, 1, 2, 1),
    "statistics":          (1, None, 1, 1, 1, 2, 1),
    "machine_performance": (None, None, 1, 2, None, 2, None),
    "process_performance": (None, None, 2, 1, None, 2, 1),
    "critical_parameters": (None, None, 2, 2, 1, 1, None),
    "define_charts":       (None, None, 2, None, 2, 2, None),
    "use_charts":          (1, 2, 1, None, None, 1, None),
}


def test_the_role_matrix_is_table_6_1_of_the_draft():
    assert set(R.ROLES) == set(TABLE_COLUMNS) and len(R.ROLES) == 7 and set(R.COMPETENCES) == set(TABLE_ROWS)
    for comp, levels in TABLE_ROWS.items():
        for role, level in zip(TABLE_COLUMNS, levels):
            assert R.MATRIX[role][comp] == level, (role, comp)
    assert set(R.APPROVERS) == {"quality_planning", "product_developer", "process_planner", "inspection_planner"}


def test_qualification_compares_the_recorded_level_with_the_table():
    ok = {c: {"level": 2} for c in R.COMPETENCES}
    assert R.qualification(["quality_planning", "line_operator"], ok) == {"quality_planning": {"qualified": True, "gaps": []}, "line_operator": {"qualified": True, "gaps": []}}
    some = {"qms": {"level": 1}, "statistics": {"level": 1}, "use_charts": {"level": 1}}
    q = R.qualification(["line_supervisor", "line_operator"], some)
    assert q["line_operator"]["qualified"] is True
    assert q["line_supervisor"] == {"qualified": False, "gaps": [{"competence": "use_charts", "required": 2, "achieved": 1}]}
    q = R.qualification(["process_planner"], {})
    assert [g["competence"] for g in q["process_planner"]["gaps"]] == ["qms", "statistics", "machine_performance", "process_performance", "critical_parameters",
                                                                     "define_charts", "use_charts"]
    assert R.qualification([], some) == {}


# ------------------------------------------------------------------ records

LINE = {"step": "Finish turning", "kind": "product_characteristic", "characteristic": "bore", "unit": "mm", "target": 10.0, "lsl": 9.9, "usl": 10.1, "class": "major",
        "method": "air gauge", "sample_size": 5, "frequency": "every 2 hours", "control": "sampling_inspection", "reaction": "stop, sort, call the setter",
        "responsible": ["line_operator"]}


@pytest.mark.parametrize("change", [
    {"kind": "x"}, {"class": "huge"}, {"control": "magic"}, {"lsl": 5, "usl": 4}, {"sample_size": 0}, {"sample_size": 2.5}, {"responsible": ["boss"]}, {"responsible": ["line_operator"] * 2},
    {"nonsense": 1}, {"characteristic": "x" * 201}, {"monitor_id": 0}, {"msa_id": "a"}, {"lsl": "a"},
])
def test_bad_lines_are_refused(change):
    with pytest.raises(ValueError):
        M.validate_record({"name": "p", "lines": [{**LINE, **change}]})


@pytest.mark.parametrize("change", [{"name": " "}, {"phase": "someday"}, {"nonsense": 1}, {"lines": "x"}, {"lines": [1]}, {"lines": [LINE] * 301}])
def test_bad_records_are_refused(change):
    with pytest.raises(ValueError):
        M.validate_record({"name": "p", **change})


# ------------------------------------------------------------------ the checks of a line

def ctx(monitors=None, gates=None, staffing=None):
    return M.Context(monitors or {}, gates or {}, staffing or {})


def line(**over):
    return M.validate_line({**LINE, **over}, 1)


def results(checks):
    return {c["key"]: c["result"] for c in checks}


def gate(status="pass", U=0.01, guard=0.008, tolerance=0.2, blocking=()):
    return {"gate": {"status": status, "blocking": list(blocking), "uncertainty": {"U": U, "guard_band": guard} if U else None}, "name": "Air gauge", "tolerance": tolerance}


def test_a_line_needs_what_the_draft_lists():
    r = results(M.check_line(line(msa_id=1), ctx(gates={1: gate()})))
    assert r["complete"] == "pass" and r["specification"] == "pass" and r["msa"] == "pass" and r["reaction"] == "pass" and r["uncertainty"] == "pass"
    bad = M.check_line(line(characteristic="", sample_size=None, frequency="", method="", responsible=[], msa_id=1), ctx(gates={1: gate()}))
    comp = next(c for c in bad if c["key"] == "complete")
    assert comp["result"] == "fail" and set(comp["missing"]) == {"characteristic", "responsible", "sample_size", "frequency", "method"}
    full = line(control="full_inspection", sample_size=None, frequency="", msa_id=1)
    assert results(M.check_line(full, ctx(gates={1: gate()})))["complete"] == "pass"  # every part is measured: no sample size or frequency
    assert results(M.check_line(line(lsl=None, usl=None, target=None, msa_id=1), ctx(gates={1: gate()})))["specification"] == "fail"
    assert results(M.check_line(line(kind="process_parameter", lsl=None, usl=None, target=180.0, msa_id=1), ctx(gates={1: gate()})))["specification"] == "pass"
    assert results(M.check_line(line(kind="process_parameter", lsl=None, usl=None, target=None, msa_id=1), ctx(gates={1: gate()})))["specification"] == "warn"


def test_the_measurement_system_and_the_room_for_its_uncertainty():
    assert results(M.check_line(line(), ctx()))["msa"] == "fail"  # a measured line names its system
    assert results(M.check_line(line(msa_id=7), ctx()))["msa"] == "fail"  # one that no longer exists
    assert results(M.check_line(line(msa_id=1), ctx(gates={1: gate("block", blocking=["grr"])})))["msa"] == "fail"
    assert results(M.check_line(line(msa_id=1), ctx(gates={1: gate("conditional")})))["msa"] == "warn"
    # tolerance 0.2, guard band 0.08: the acceptance limits would cross: no room between tolerance and uncertainty
    tight = M.check_line(line(msa_id=1), ctx(gates={1: gate(U=0.1, guard=0.1)}))
    unc = next(c for c in tight if c["key"] == "uncertainty")
    assert unc["result"] == "fail" and unc["accept_low"] >= unc["accept_high"]
    ok = next(c for c in M.check_line(line(msa_id=1), ctx(gates={1: gate(U=0.01, guard=0.008)})) if c["key"] == "uncertainty")
    assert ok["accept_low"] == pytest.approx(9.908) and ok["accept_high"] == pytest.approx(10.092) and ok["share"] == pytest.approx(0.05)
    mismatch = results(M.check_line(line(msa_id=1), ctx(gates={1: gate(tolerance=0.5)})))
    assert mismatch["system_tolerance"] == "warn"
    assert results(M.check_line(line(control="other", msa_id=None), ctx()))["reaction"] == "pass" and "msa" not in results(M.check_line(line(control="other"), ctx()))
    assert results(M.check_line(line(control="other", msa_id=1), ctx(gates={1: gate()})))["msa"] == "warn"  # a system that nothing uses


def test_an_spc_line_names_its_monitor_and_may_take_the_reaction_from_its_action_plan():
    mon = {"name": "Bore", "kind": "xbar-s", "active": True, "ocap": {"default": {"operator_action": "Measure again, call the leader"}, "rules": {}}}
    spc = line(control="spc_chart", reaction="", monitor_id=3, msa_id=1)
    r = M.check_line(spc, ctx({3: mon}, {1: gate()}))
    assert results(r)["monitor"] == "pass" and results(r)["reaction"] == "pass" and next(c for c in r if c["key"] == "reaction")["from_monitor"] is True
    assert results(M.check_line(line(control="spc_chart", reaction="", monitor_id=3, msa_id=1), ctx({3: {**mon, "ocap": {"default": {"operator_action": ""}, "rules": {}}}}, {1: gate()})))["reaction"] == "fail"
    assert results(M.check_line(line(control="spc_chart", monitor_id=None, msa_id=1), ctx(gates={1: gate()})))["monitor"] == "fail"
    assert results(M.check_line(line(control="spc_chart", monitor_id=9, msa_id=1), ctx(gates={1: gate()})))["monitor"] == "fail"
    assert results(M.check_line(spc, ctx({3: {**mon, "active": False}}, {1: gate()})))["monitor"] == "warn"


# ------------------------------------------------------------------ the API

@pytest.fixture
def env():
    app = make_app()
    for name in ("qpe", "pde", "ppe", "ipe"):
        app.state.auth.create_user(name, PASSWORD, "engineer", name.upper())
    app.state.auth.create_user("oper", PASSWORD, "operator", "Olga Operator")
    return app, {u: logged_in_client(app, u) for u in ("admin", "eng", "view", "oper", "qpe", "pde", "ppe", "ipe")}


def uid(app, name):
    return next(u.id for u in app.state.auth.list_users() if u.username == name)


def give_roles(c, app, name, roles):
    r = c["eng"].put(f"/api/people/{uid(app, name)}/roles", json={"roles": roles})
    assert r.status_code == 200, r.text


def test_the_matrix_roles_and_competences_of_people(env):
    app, c = env
    m = c["view"].get("/api/spc-roles").json()
    assert m["matrix"]["line_supervisor"]["use_charts"] == 2 and m["approvers"] == list(R.APPROVERS) and len(m["roles"]) == 7
    assert c["view"].get("/api/people").status_code == 403 and c["oper"].put(f"/api/people/{uid(app, 'oper')}/roles", json={"roles": ["line_operator"]}).status_code == 403
    assert err(c["eng"].put(f"/api/people/{uid(app, 'oper')}/roles", json={"roles": ["boss"]}))["code"] == "invalid_input"
    give_roles(c, app, "oper", ["line_supervisor", "line_operator"])
    p = c["oper"].get(f"/api/people/{uid(app, 'oper')}").json()  # a person can read their own record
    assert p["roles"] == ["line_supervisor", "line_operator"] and p["qualification"]["line_supervisor"]["qualified"] is False
    assert c["oper"].get(f"/api/people/{uid(app, 'view')}").status_code == 403
    assert c["eng"].post(f"/api/people/{uid(app, 'oper')}/competences", json={"competence": "use_charts", "level": 2, "date": "2026-01-10", "note": ""}).status_code == 400
    assert err(c["eng"].post(f"/api/people/{uid(app, 'oper')}/competences", json={"competence": "use_charts", "level": 2, "date": "2026-01-10", "note": ""}))["code"] == "note_required"
    assert err(c["eng"].post(f"/api/people/{uid(app, 'oper')}/competences", json={"competence": "use_charts", "level": 2, "date": "2999-01-01", "note": "course"}))["code"] == "invalid_input"
    assert err(c["eng"].post(f"/api/people/{uid(app, 'oper')}/competences", json={"competence": "nonsense", "level": 1, "date": "2026-01-10", "note": "course"}))["code"] == "invalid_input"
    assert c["eng"].post(f"/api/people/{uid(app, 'oper')}/competences", json={"competence": "use_charts", "level": 3, "date": "2026-01-10", "note": "x"}).status_code == 422
    for comp, lvl in (("qms", 1), ("statistics", 1), ("use_charts", 2)):
        c["eng"].post(f"/api/people/{uid(app, 'oper')}/competences", json={"competence": comp, "level": lvl, "date": "2026-01-10", "note": "course SPC-1, test passed"})
    p = c["eng"].get(f"/api/people/{uid(app, 'oper')}").json()
    assert all(q["qualified"] for q in p["qualification"].values()) and p["competences"]["use_charts"]["by"].startswith("Eva") and len(p["history"]) == 3
    assert c["eng"].get("/api/spc-roles").json()["staffing"]["line_supervisor"] == {"assigned": 1, "qualified": 1}
    assert [x["roles"] for x in c["eng"].get("/api/people").json()["people"] if x["user"]["username"] == "oper"] == [["line_supervisor", "line_operator"]]
    actions = [e["action"] for e in app.state.audit.list(100)]
    assert "spc_roles_set" in actions and "spc_competence" in actions and app.state.audit.verify()["ok"]


def plan_record(msa_id, monitor_id=None, **over):
    return {"name": "Bore line 4", "part": "P-100", "process": "turning", "phase": "production", "lines": [
        {**LINE, "msa_id": msa_id}, {**LINE, "step": "Rough turning", "characteristic": "wall", "control": "spc_chart", "monitor_id": monitor_id, "msa_id": msa_id,
                                     "reaction": "", "responsible": ["line_supervisor", "line_operator"]}], **over}


MONITOR = {"name": "Bore chart", "characteristic": "bore", "kind": "xbar-s", "n": 5, "ocap": {"default": {"operator_action": "Measure again, call the setter"}}}


def test_the_life_of_a_control_plan(env):
    app, c = env
    msa_id = build_system(c["eng"], "Air gauge", tolerance=0.2)
    mid = c["eng"].post("/api/monitors", json={"config": MONITOR, "source": {"type": "parameters", "mu": 10.0, "sigma": 0.02}}).json()["monitor"]["id"]
    assert c["view"].post("/api/plans", json={"record": plan_record(msa_id, mid)}).status_code == 403
    assert err(c["eng"].post("/api/plans", json={"record": plan_record(999, mid)}))["code"] == "msa_system_not_found"
    assert err(c["eng"].post("/api/plans", json={"record": plan_record(msa_id, 999)}))["code"] == "monitor_not_found"
    v = c["eng"].post("/api/plans", json={"record": plan_record(msa_id, mid)}).json()
    pid = v["plan"]["id"]
    assert v["plan"]["status"] == "draft" and v["plan"]["content_revision"] == 1 and c["eng"].post("/api/plans", json={"record": plan_record(msa_id, mid)}).status_code == 409
    ev = v["evaluation"]
    assert ev["line_blockers"] == [] and ev["ready"] is False and ev["blockers"] == ["approvals"]  # the lines are fine, nobody approved
    assert any(c_["key"] == "reaction" and c_.get("from_monitor") for c_ in ev["lines"]["2"])
    assert c["view"].get(f"/api/plans/{pid}").status_code == 200 and c["view"].get("/api/plans").json()["plans"][0]["ready"] is False
    # approvals: the user must hold the role
    assert err(c["qpe"].post(f"/api/plans/{pid}/approve", json={"role": "quality_planning"}))["code"] == "role_not_held"
    assert err(c["qpe"].post(f"/api/plans/{pid}/approve", json={"role": "line_operator"}))["code"] == "invalid_input"
    for user, role in (("qpe", "quality_planning"), ("pde", "product_developer"), ("ppe", "process_planner"), ("ipe", "inspection_planner")):
        give_roles(c, app, user, [role])
    assert err(c["eng"].post(f"/api/plans/{pid}/release", json={"reason": "go"}))["code"] == "plan_not_ready"
    for user, role in (("qpe", "quality_planning"), ("pde", "product_developer"), ("ppe", "process_planner")):
        r = c[user].post(f"/api/plans/{pid}/approve", json={"role": role, "note": "tolerances, process and gauge fit"})
        assert r.status_code == 200 and r.json()["plan"]["approvals"][role]["by"].startswith(user.upper())
    assert err(c["eng"].post(f"/api/plans/{pid}/release", json={"reason": "go"}))["params"]["blockers"] == ["approvals"]
    c["ipe"].post(f"/api/plans/{pid}/approve", json={"role": "inspection_planner"})
    assert err(c["eng"].post(f"/api/plans/{pid}/release", json={"reason": " "}))["code"] == "reason_required"
    rel = c["eng"].post(f"/api/plans/{pid}/release", json={"reason": "released for the pre-series"}).json()
    assert rel["plan"]["status"] == "released" and rel["plan"]["released_revision"] == 1 and len(rel["plan"]["released"]) == 1
    assert rel["plan"]["released"][0]["snapshot"]["lines"][0]["characteristic"] == "bore" and set(rel["plan"]["released"][0]["approvals"]) == set(R.APPROVERS)
    assert "staffing" in rel["evaluation"]["remarks"]  # nobody holds the roles of the line yet: said, not blocking
    assert err(c["qpe"].post(f"/api/plans/{pid}/approve", json={"role": "quality_planning"}))["code"] == "plan_not_draft"
    assert err(c["eng"].post(f"/api/plans/{pid}/release", json={"reason": "again"}))["code"] == "plan_released"
    # a change makes it a draft again and the approvals no longer cover it
    changed = plan_record(msa_id, mid)
    changed["lines"][0]["sample_size"] = 3
    again = c["eng"].put(f"/api/plans/{pid}", json={"record": changed}).json()
    assert again["plan"]["status"] == "draft" and again["plan"]["approvals"] == {} and again["plan"]["content_revision"] == 2 and len(again["plan"]["released"]) == 1
    same = c["eng"].put(f"/api/plans/{pid}", json={"record": changed}).json()
    assert same["plan"]["content_revision"] == 2  # nothing changed: nothing happens
    # withdrawing a release
    for user, role in (("qpe", "quality_planning"), ("pde", "product_developer"), ("ppe", "process_planner"), ("ipe", "inspection_planner")):
        c[user].post(f"/api/plans/{pid}/approve", json={"role": role})
    c["eng"].post(f"/api/plans/{pid}/release", json={"reason": "second"})
    assert err(c["eng"].post(f"/api/plans/{pid}/withdraw", json={"reason": ""}))["code"] == "reason_required"
    w = c["eng"].post(f"/api/plans/{pid}/withdraw", json={"reason": "customer changed the drawing"}).json()
    assert w["plan"]["status"] == "draft" and w["plan"]["approvals"] == {} and w["plan"]["content_revision"] == 3
    assert err(c["eng"].post(f"/api/plans/{pid}/withdraw", json={"reason": "x"}))["code"] == "plan_not_released"
    assert c["eng"].delete(f"/api/plans/{pid}").status_code == 403 and c["admin"].delete(f"/api/plans/{pid}").status_code == 200
    assert err(c["eng"].get(f"/api/plans/{pid}"))["code"] == "plan_not_found"
    actions = {e["action"] for e in app.state.audit.list(500)}
    assert {"plan_created", "plan_approved", "plan_released", "plan_updated", "plan_withdrawn", "plan_deleted"} <= actions and app.state.audit.verify()["ok"]


def test_a_blocked_measurement_system_or_a_line_without_proof_stops_the_release(env):
    app, c = env
    empty = c["eng"].post("/api/msa", json={"record": {"name": "No proof", "resolution": 0.001, "tolerance": 0.2}}).json()["system"]["id"]
    record = {"name": "Blocked", "lines": [{**LINE, "msa_id": empty}]}
    v = c["eng"].post("/api/plans", json={"record": record}).json()
    assert "1:msa" in v["evaluation"]["line_blockers"]
    c["eng"].put(f"/api/msa/{empty}/waivers/grr", json={"reason": "agreed"})
    for chk in ("validity", "stability"):
        c["eng"].put(f"/api/msa/{empty}/waivers/{chk}", json={"reason": "agreed"})
    v = c["eng"].get(f"/api/plans/{v['plan']['id']}").json()
    assert "1:msa" not in v["evaluation"]["line_blockers"]  # the waiver of the gate is the gate's: it passes, flagged there
    none = c["eng"].post("/api/plans", json={"record": {"name": "No system", "lines": [LINE]}}).json()
    assert none["evaluation"]["line_blockers"] == ["1:msa"]
    empty_plan = c["eng"].post("/api/plans", json={"record": {"name": "Empty"}}).json()
    assert "has_lines" in empty_plan["evaluation"]["blockers"] and err(c["qpe"].post(f"/api/plans/{empty_plan['plan']['id']}/approve", json={"role": "quality_planning"}))["code"] in ("role_not_held", "plan_empty")
    give_roles(c, app, "qpe", ["quality_planning"])
    assert err(c["qpe"].post(f"/api/plans/{empty_plan['plan']['id']}/approve", json={"role": "quality_planning"}))["code"] == "plan_empty"


def test_the_staffing_of_the_roles_of_a_line_is_a_remark(env):
    app, c = env
    msa_id = build_system(c["eng"], "Air gauge", tolerance=0.2)
    v = c["eng"].post("/api/plans", json={"record": {"name": "Staffing", "lines": [{**LINE, "msa_id": msa_id}]}}).json()
    st = next(x for x in v["evaluation"]["plan"] if x["key"] == "staffing")
    assert st["result"] == "warn" and st["unstaffed"] == ["line_operator"]
    give_roles(c, app, "oper", ["line_operator"])
    assert next(x for x in c["eng"].get(f"/api/plans/{v['plan']['id']}").json()["evaluation"]["plan"] if x["key"] == "staffing")["result"] == "warn"  # assigned, not yet qualified
    for comp in ("qms", "statistics", "use_charts"):
        c["eng"].post(f"/api/people/{uid(app, 'oper')}/competences", json={"competence": comp, "level": 1, "date": "2026-01-10", "note": "course SPC-1"})
    st = next(x for x in c["eng"].get(f"/api/plans/{v['plan']['id']}").json()["evaluation"]["plan"] if x["key"] == "staffing")
    assert st["result"] == "pass" and st["roles"]["line_operator"] == {"assigned": 1, "qualified": 1}

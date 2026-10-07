"""Random sampling plans and the coverage of a sample (draft 9.2), the audit anchor, and the CSV exports for other systems."""

import numpy as np
import pytest

from spc.core import sampling_plan as sp
from tests.conftest import logged_in_client, make_app
from tests.test_api import err, upload


# ------------------------------------------------------------------ the random plan

def test_the_plan_deals_the_levels_out_evenly_and_the_same_seed_gives_the_same_plan():
    plan = sp.random_plan(25, 5, {"tool": ["A", "B", "C"], "shift": ["1", "2"]}, seed=7)
    assert plan["parts"] == 125 and plan["balanced"] and len(plan["plan"]) == 25
    assert plan["counts"]["tool"] == {"A": 9, "B": 8, "C": 8} and plan["counts"]["shift"] == {"1": 13, "2": 12}
    assert plan == sp.random_plan(25, 5, {"tool": ["A", "B", "C"], "shift": ["1", "2"]}, seed=7)  # repeatable
    assert plan["plan"] != sp.random_plan(25, 5, {"tool": ["A", "B", "C"], "shift": ["1", "2"]}, seed=8)["plan"]
    for i, row in enumerate(plan["plan"]):  # each subgroup is taken inside its own window of the period: spread over the whole period
        assert row["subgroup"] == i + 1 and row["window"][0] == pytest.approx(i / 25) and row["window"][0] <= row["position"] < row["window"][1]
    # the factors are dealt out independently: the combinations are not a fixed pattern
    combos = {(r["levels"]["tool"], r["levels"]["shift"]) for r in plan["plan"]}
    assert len(combos) == 6
    drawn = sp.random_plan(10, 3)
    assert isinstance(drawn["seed"], int) and drawn["counts"] == {} and drawn["balanced"] and sp.random_plan(10, 3, seed=drawn["seed"]) == drawn


def test_the_random_order_is_not_biased_towards_a_level_at_the_start():
    firsts = [sp.random_plan(6, 1, {"tool": ["A", "B", "C"]}, seed=s)["plan"][0]["levels"]["tool"] for s in range(900)]
    for level in "ABC":
        assert 240 < firsts.count(level) < 360  # one third each, within sampling noise


def test_a_bad_plan_is_refused():
    for bad in (lambda: sp.random_plan(0, 5), lambda: sp.random_plan(5, 0), lambda: sp.random_plan(2000, 5), lambda: sp.random_plan(5, 5, {"tool": []}),
                lambda: sp.random_plan(5, 5, {"tool": ["A", "A"]}), lambda: sp.random_plan(5, 5, {"": ["A"]}), lambda: sp.random_plan(5, 5, seed=-1),
                lambda: sp.random_plan(5, 5, {f"f{i}": ["A"] for i in range(7)}), lambda: sp.random_plan(True, 5)):
        with pytest.raises(ValueError):
            bad()


# ------------------------------------------------------------------ coverage

def test_coverage_finds_missing_and_thin_levels():
    tool = ["A"] * 8 + ["B", "C"]
    groups = ["s1", "s1", "s2", "s2", "s3", "s3", "s4", "s4", "s5", "s5"]
    r = sp.coverage({"tool": tool}, groups, {"tool": ["A", "B", "C", "D"]})
    f = r["factors"]["tool"]
    assert r["values"] == 10 and r["subgroups"] == 5 and not r["representative"]
    assert f["missing"] == ["D"] and f["thin"] == ["B", "C"] and [l["share"] for l in f["levels"]] == [0.8, 0.1, 0.1]
    good = sp.coverage({"tool": ["A", "B", "C"] * 10}, [f"s{i // 3}" for i in range(30)], {"tool": ["A", "B", "C"]})
    assert good["representative"] and all(not l["thin"] for l in good["factors"]["tool"]["levels"])
    one_group = sp.coverage({"tool": ["A", "A", "A", "A", "A", "B", "B"]}, ["s1", "s2", "s3", "s4", "s5", "s5", "s5"])
    assert one_group["factors"]["tool"]["thin"] == ["B"]  # a level that is in one subgroup only, among 5 subgroups
    no_groups = sp.coverage({"tool": ["A", "B"] * 5}, None)
    assert no_groups["subgroups"] is None and no_groups["representative"]
    for bad in (lambda: sp.coverage({}, None), lambda: sp.coverage({"t": ["A"]}, ["a", "b"]), lambda: sp.coverage({"t": ["A"], "u": ["B", "C"]}), lambda: sp.coverage({"t": ["A"]}, thin_share=0)):
        with pytest.raises(ValueError):
            bad()


# ------------------------------------------------------------------ the API

@pytest.fixture
def client():
    return logged_in_client(make_app(max_upload=200_000))


def test_the_plan_and_the_coverage_are_on_the_api(client):
    r = client.post("/api/sampling/random", json={"subgroups": 12, "size": 5, "factors": {"shift": ["1", "2", "3"]}, "seed": 3})
    assert r.status_code == 200 and r.json()["seed"] == 3 and r.json()["counts"]["shift"] == {"1": 4, "2": 4, "3": 4}
    assert client.post("/api/sampling/random", json={"subgroups": 0, "size": 5}).status_code == 422
    assert err(client.post("/api/sampling/random", json={"subgroups": 5, "size": 5, "factors": {"t": ["A", "A"]}}))["code"] == "invalid_input"
    lines = ["lot,value,tool"] + [f"S{i // 5},{10 + (i % 7) * 0.01:.3f},{'A' if i < 45 else 'B'}" for i in range(50)]
    ds = upload(client, "\n".join(lines).encode(), value="value", subgroup="lot", tags=["tool"]).json()
    c = client.post(f"/api/datasets/{ds['id']}/coverage", json={"expected": {"tool": ["A", "B", "C"]}})
    assert c.status_code == 200, c.text
    f = c.json()["factors"]["tool"]
    assert not c.json()["representative"] and f["missing"] == ["C"] and f["thin"] == ["B"] and c.json()["subgroups"] == 10
    assert client.post(f"/api/datasets/{ds['id']}/coverage", json={"factors": ["nope"]}).status_code == 400
    plain = upload(client, ("value\n" + "\n".join(f"{10 + (i % 7) * 0.01:.3f}" for i in range(30))).encode(), value="value", subgroup="", tags=[]).json()  # a data set without tags
    assert err(client.post(f"/api/datasets/{plain['id']}/coverage", json={}))["code"] == "invalid_input"


# ------------------------------------------------------------------ the audit anchor

def test_an_anchor_kept_outside_shows_that_the_newest_entries_were_cut():
    app = make_app()
    admin = logged_in_client(app, "admin")
    upload(logged_in_client(app, "eng"))
    kept = admin.get("/api/audit/verify").json()
    check = lambda a=kept: admin.post("/api/audit/anchor-check", json={"entries": a["entries"], "last_hash": a["last_hash"]}).json()
    assert check()["ok"] is True and check()["reason"] == "intact"
    upload(logged_in_client(app, "eng"))  # the chain grows: the kept entry is still in it
    assert check()["ok"] is True and check()["entries_now"] > kept["entries"]
    kept2 = admin.get("/api/audit/verify").json()
    app.state.db.execute("DELETE FROM audit WHERE id = (SELECT MAX(id) FROM audit)")
    assert admin.get("/api/audit/verify").json()["ok"] is True  # the shorter chain is a chain
    r = admin.post("/api/audit/anchor-check", json={"entries": kept2["entries"], "last_hash": kept2["last_hash"]}).json()
    assert r["ok"] is False and r["reason"] == "shorter"
    app.state.db.execute("UPDATE audit SET detail = '{}' WHERE id = 2")
    broken = check()
    assert broken["ok"] is False and broken["reason"] == "chain_broken" and broken["broken_at"] == 2
    assert admin.post("/api/audit/anchor-check", json={"entries": 1, "last_hash": "zz"}).status_code == 422
    assert logged_in_client(app, "eng").post("/api/audit/anchor-check", json={"entries": 1, "last_hash": "0" * 64}).status_code == 403


def test_the_anchor_of_a_rebuilt_chain_differs():
    from spc.auth.audit import Audit

    app = make_app()
    admin = logged_in_client(app, "admin")
    upload(logged_in_client(app, "eng"))
    kept = admin.get("/api/audit/verify").json()
    audit: Audit = app.state.audit
    assert audit.check_anchor(kept["entries"], "0" * 64)["reason"] == "hash_differs"
    assert audit.check_anchor(0, "0" * 64)["ok"] is True and audit.check_anchor(0, "1" * 64)["ok"] is False


# ------------------------------------------------------------------ the CSV exports for other systems

def test_the_points_of_a_monitor_and_the_lots_can_be_exported_as_csv():
    from tests.test_monitor import OK_SAMPLE, ack, alarm_values, enter, make_monitor
    from tests.conftest import PASSWORD

    app = make_app(max_upload=200_000)
    app.state.auth.create_user("oper", PASSWORD, "operator", "Olga Operator")
    c = {u: logged_in_client(app, u) for u in ("eng", "oper", "view")}
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    enter(c["oper"], mid, OK_SAMPLE, label="=cmd|calc")
    enter(c["oper"], mid, alarm_values())
    r = c["view"].get(f"/api/monitors/{mid}/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = r.content.decode("utf-8-sig").splitlines()
    assert rows[0].startswith("seq,taken_at,entered_at,entered_by,label,tags,value_1") and len(rows) == 3
    assert "'=cmd|calc" in rows[1] and "location:beyond_limits" in rows[2]  # text that Excel would run as a formula stays text
    assert c["oper"].post("/api/lots", json={"record": {"lot_no": "=L1", "quantity": 10}}).status_code == 200
    csv_text = c["view"].get("/api/lots/export.csv").content.decode("utf-8-sig")
    assert csv_text.splitlines()[0].startswith("lot_no,product,characteristic,quantity,status,decision") and "'=L1" in csv_text
    assert c["view"].get("/api/monitors/999/export.csv").status_code == 404


def test_the_plan_can_take_every_subgroup_in_the_middle_of_its_window():
    mid = sp.random_plan(8, 5, {"shift": ["1", "2"]}, seed=3, position="middle")
    assert mid["position_mode"] == "middle" and all(r["position"] == pytest.approx((r["subgroup"] - 0.5) / 8) for r in mid["plan"])
    assert mid["plan"][0]["levels"] == sp.random_plan(8, 5, {"shift": ["1", "2"]}, seed=3)["plan"][0]["levels"]  # the levels do not depend on the position
    with pytest.raises(ValueError):
        sp.random_plan(8, 5, position="sometimes")

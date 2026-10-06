import asyncio
import datetime as dt
import threading
import time

import pytest

from spc.equipment import model as M
from tests.conftest import PASSWORD, logged_in_client, make_app
from tests.test_api import err

pytest.importorskip("asyncua")

NODE = {"node_id": "ns=2;s=Bore", "monitor_id": 1}
LINK = {"name": "Grinder 4", "endpoint": "opc.tcp://127.0.0.1:4840/grinder", "nodes": [NODE]}
MONITOR = {"name": "Bore chart", "characteristic": "bore", "kind": "xbar-s", "n": 3, "ocap": {"default": {"operator_action": "Measure again"}}}


@pytest.mark.parametrize("change", [{"name": " "}, {"endpoint": "http://x"}, {"endpoint": "opc.tcp://"}, {"security": "Basic256Sha256"}, {"username": "u"},
                                    {"password_env": "lower"}, {"interval_ms": 10}, {"stale_after_s": -1}, {"nodes": []}, {"nodes": [{"node_id": "Bore", "monitor_id": 1}]},
                                    {"nodes": [{**NODE, "monitor_id": 0}]}, {"nodes": [{**NODE, "scale": 0}]}, {"nodes": [NODE, NODE]}, {"nodes": [NODE, {**NODE, "node_id": "ns=2;s=B"}]},
                                    {"nonsense": 1}])
def test_bad_links_are_refused(change):
    assert M.validate_link(LINK)["interval_ms"] == 1000
    with pytest.raises(ValueError):
        M.validate_link({**LINK, **change})


def test_security_and_credentials_are_accepted_in_their_forms():
    ok = M.validate_link({**LINK, "security": "Basic256Sha256,SignAndEncrypt,client.pem,client.key", "username": "spc", "password_env": "OPC_PASSWORD"})
    assert ok["security"].startswith("Basic256Sha256") and "password" not in ok


@pytest.fixture
def env():
    app = make_app()
    eng = logged_in_client(app, "eng")
    eng.post("/api/monitors", json={"config": MONITOR, "source": {"type": "parameters", "mu": 10.0, "sigma": 0.1}})
    eng.post("/api/monitors/1/ack")
    probes = []

    def prober(link):
        probes.append(link["name"])
        return {"ok": True, "error": None, "nodes": [{"node_id": n["node_id"], "ok": True, "value": 10.0, "type": "Double", "quality": "good", "source_time": "2026-10-01T00:00:00Z", "error": None}
                                                    for n in link["nodes"]]}

    app.state.equipment.prober = prober
    return app, {u: logged_in_client(app, u) for u in ("eng", "admin", "view")} | {"probes": probes}


def at(sec):
    return dt.datetime(2026, 10, 1, 8, 0, 0, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=sec)


def test_a_link_is_tested_before_it_is_enabled_and_a_change_starts_again(env):
    app, c = env
    assert c["view"].post("/api/equipment/links", json={"record": LINK}).status_code == 403
    assert err(c["eng"].post("/api/equipment/links", json={"record": {**LINK, "nodes": [{**NODE, "monitor_id": 99}]}}))["code"] == "monitor_not_found"
    r = c["eng"].post("/api/equipment/links", json={"record": LINK})
    assert r.status_code == 200, r.text
    lid = r.json()["link"]["id"]
    assert err(c["eng"].post("/api/equipment/links", json={"record": LINK}))["code"] == "equipment_name_taken"
    assert err(c["eng"].post(f"/api/equipment/links/{lid}/enable"))["code"] == "equipment_not_tested"
    t = c["eng"].post(f"/api/equipment/links/{lid}/test").json()
    assert t["link"]["last_test"]["ok"] and t["link"]["last_test"]["revision"] == 1 and t["link"]["last_test"]["by"].endswith("(eng)") and t["link"]["last_test"]["nodes"][0]["value"] == 10.0
    en = c["eng"].post(f"/api/equipment/links/{lid}/enable").json()
    assert en["link"]["enabled"] and en["link"]["enabled_by"] and en["runtime"]["state"] == "runner_stopped"  # the runner thread is not started in tests
    same = c["eng"].put(f"/api/equipment/links/{lid}", json={"record": LINK}).json()
    assert same["link"]["enabled"] and same["link"]["revision"] == 1  # nothing changed
    changed = c["eng"].put(f"/api/equipment/links/{lid}", json={"record": {**LINK, "interval_ms": 500}}).json()
    assert not changed["link"]["enabled"] and changed["link"]["revision"] == 2 and changed["link"]["last_test"] is None
    assert err(c["eng"].post(f"/api/equipment/links/{lid}/enable"))["code"] == "equipment_not_tested"
    c["eng"].post(f"/api/equipment/links/{lid}/test")
    c["eng"].post(f"/api/equipment/links/{lid}/enable")
    assert c["view"].get("/api/equipment/links").json()["links"][0]["enabled"] is True
    assert c["eng"].post(f"/api/equipment/links/{lid}/disable").json()["link"]["enabled"] is False
    assert c["eng"].delete(f"/api/equipment/links/{lid}").status_code == 403 and c["admin"].delete(f"/api/equipment/links/{lid}").status_code == 200
    actions = {e["action"] for e in app.state.audit.list(200)}
    assert {"equipment_created", "equipment_tested", "equipment_enabled", "equipment_updated", "equipment_disabled", "equipment_deleted"} <= actions and app.state.audit.verify()["ok"]


def test_a_failed_test_does_not_enable_and_the_person_must_know_the_action_plan(env):
    app, c = env
    lid = c["eng"].post("/api/equipment/links", json={"record": LINK}).json()["link"]["id"]
    app.state.equipment.prober = lambda link: {"ok": False, "error": None, "nodes": [{"node_id": "ns=2;s=Bore", "ok": False, "value": 1.0, "type": "Double", "quality": "Bad", "source_time": None, "error": "bad_quality"}]}
    assert c["eng"].post(f"/api/equipment/links/{lid}/test").json()["link"]["last_test"]["ok"] is False
    assert err(c["eng"].post(f"/api/equipment/links/{lid}/enable"))["code"] == "equipment_not_tested"
    app.state.equipment.prober = lambda link: {"ok": True, "error": None, "nodes": []}
    other = logged_in_client(app, "admin")
    other.post(f"/api/equipment/links/{lid}/test")
    r = other.post(f"/api/equipment/links/{lid}/enable")
    assert r.status_code == 403 and err(r)["code"] == "ocap_not_acknowledged"  # the admin has not read the action plan of the monitor


def enabled_link(c, env_app):
    lid = c["eng"].post("/api/equipment/links", json={"record": {**LINK, "stale_after_s": 3600}}).json()["link"]["id"]
    c["eng"].post(f"/api/equipment/links/{lid}/test")
    c["eng"].post(f"/api/equipment/links/{lid}/enable")
    return lid


def test_the_guards_decide_what_reaches_the_chart(env):
    app, c = env
    lid = enabled_link(c, app)
    ing = app.state.equipment.ingest
    now = at(600)
    acc = lambda v, good=True, t=None, node="ns=2;s=Bore": ing.accept(lid, node, v, good, t, now=now)
    assert acc(10.01, t=at(1))["result"] == "buffered" and acc(10.02, t=at(2))["result"] == "buffered"
    third = acc(9.99, t=at(3))
    assert third["result"] == "point" and third["point"]["values"] == [10.01, 10.02, 9.99] and third["point"]["tags"] == {"source": "opcua", "link": "Grinder 4"}
    assert third["point"]["label"] == "OPC UA" and third["point"]["taken_at"].startswith("2026-10-01T08:00:03")
    assert acc(10.0, good=False, t=at(4))["reason"] == "bad_quality"
    assert acc(float("nan"), t=at(5))["reason"] == "not_numeric" and acc("x", t=at(5))["reason"] == "not_numeric" and acc(True, t=at(5))["reason"] == "not_numeric"
    assert acc(10.0, t=None)["reason"] == "no_timestamp"
    assert acc(10.0, t=at(3))["reason"] == "old_or_repeated" and acc(10.0, t=at(2))["reason"] == "old_or_repeated"  # a repeat after a reconnect
    assert acc(10.0, t=at(600) + dt.timedelta(minutes=10))["reason"] == "future"
    assert acc(10.0, t=at(600) - dt.timedelta(hours=2))["reason"] == "stale"
    assert acc(10.0, t=at(10), node="ns=2;s=Other")["reason"] == "not_active"
    v = c["eng"].get(f"/api/equipment/links/{lid}").json()["counters"]["ns=2;s=Bore"]
    assert v["points"] == 1 and v["accepted"] == 3 and v["buffered"] == 0 and v["rejected"]["bad_quality"] == 1 and v["rejected"]["not_numeric"] == 3
    assert v["rejected"]["old_or_repeated"] == 2 and v["last_error"]["reason"] == "stale"
    assert len(c["eng"].get("/api/monitors/1/points").json()["points"]) == 1
    # the subgroup is collected over calls and survives a restart: the buffer is in the database
    acc(10.1, t=at(20)); acc(10.2, t=at(21))
    assert c["eng"].get(f"/api/equipment/links/{lid}").json()["counters"]["ns=2;s=Bore"]["buffered"] == 2


def test_scale_and_offset_convert_to_the_unit_of_the_chart(env):
    app, c = env
    lid = c["eng"].post("/api/equipment/links", json={"record": {**LINK, "nodes": [{**NODE, "scale": 0.001, "offset": 0.5}], "stale_after_s": 3600}}).json()["link"]["id"]
    c["eng"].post(f"/api/equipment/links/{lid}/test"); c["eng"].post(f"/api/equipment/links/{lid}/enable")
    ing = app.state.equipment.ingest
    for i, raw in enumerate((9500, 9510, 9490), start=1):  # micrometres from the machine, millimetres on the chart
        r = ing.accept(lid, "ns=2;s=Bore", raw, True, at(i), now=at(100))
    assert r["point"]["values"] == pytest.approx([10.0, 10.01, 9.99])


def test_a_blocked_gate_or_a_switched_off_monitor_drops_the_subgroup_and_says_why(env):
    app, c = env
    lid = enabled_link(c, app)
    ing = app.state.equipment.ingest
    app.state.db.execute("UPDATE monitors SET active = 0 WHERE id = 1")
    for i in (1, 2):
        ing.accept(lid, "ns=2;s=Bore", 10.0, True, at(i), now=at(100))
    r = ing.accept(lid, "ns=2;s=Bore", 10.0, True, at(3), now=at(100))
    assert r["result"] == "rejected" and r["reason"] == "monitor_inactive"
    v = c["eng"].get(f"/api/equipment/links/{lid}").json()["counters"]["ns=2;s=Bore"]
    assert v["buffered"] == 0 and v["rejected"]["monitor_inactive"] == 1 and v["points"] == 0


# ------------------------------------------------------------------ against a real OPC UA server

def start_server(port):
    from asyncua import Server, ua

    loop = asyncio.new_event_loop()
    state = {}
    ready = threading.Event()

    async def run():
        s = Server()
        await s.init()
        s.set_endpoint(f"opc.tcp://127.0.0.1:{port}/spc")
        ns = await s.register_namespace("http://spc.test")
        node = await s.nodes.objects.add_variable(ua.NodeId("Bore", ns), "Bore", 10.0)
        await node.set_writable()
        state.update(server=s, ns=ns, node=node, ua=ua)
        async with s:
            ready.set()
            while not state.get("stop"):
                await asyncio.sleep(0.05)

    t = threading.Thread(target=lambda: loop.run_until_complete(run()), daemon=True)
    t.start()
    assert ready.wait(10)

    def write(value, when=None, good=True):
        ua = state["ua"]
        dv = ua.DataValue(ua.Variant(value, ua.VariantType.Double), SourceTimestamp=when or dt.datetime.now(dt.timezone.utc))
        if not good:
            dv.StatusCode = ua.StatusCode(ua.StatusCodes.BadSensorFailure)
        asyncio.run_coroutine_threadsafe(state["node"].write_value(dv), loop).result(5)

    def stop():
        state["stop"] = True
        t.join(10)

    return {"write": write, "endpoint": f"opc.tcp://127.0.0.1:{port}/spc", "ns": state["ns"], "stop": stop}


@pytest.fixture
def server():
    s = start_server(48555)
    yield s
    s["stop"]()


def test_the_test_read_and_the_runner_against_a_real_server(server):
    app = make_app()
    eng = logged_in_client(app, "eng")
    eng.post("/api/monitors", json={"config": MONITOR, "source": {"type": "parameters", "mu": 10.0, "sigma": 0.1}})
    eng.post("/api/monitors/1/ack")
    bore = f"ns={server['ns']};s=Bore"
    record = {"name": "Real", "endpoint": server["endpoint"], "nodes": [{"node_id": bore, "monitor_id": 1}], "interval_ms": 100}
    lid = eng.post("/api/equipment/links", json={"record": record}).json()["link"]["id"]
    t = eng.post(f"/api/equipment/links/{lid}/test").json()["link"]["last_test"]
    assert t["ok"] and t["nodes"][0]["value"] == 10.0 and t["nodes"][0]["type"] == "Double" and t["nodes"][0]["quality"] == "good" and t["nodes"][0]["source_time"]
    # a node that does not exist, a text node and a refused connection fail the test, with the reason
    bad = eng.post("/api/equipment/links", json={"record": {**record, "name": "Missing", "nodes": [{"node_id": f"ns={server['ns']};s=Nope", "monitor_id": 1}]}}).json()["link"]["id"]
    tb = eng.post(f"/api/equipment/links/{bad}/test").json()["link"]["last_test"]
    assert not tb["ok"] and tb["nodes"][0]["error"]
    gone = eng.post("/api/equipment/links", json={"record": {**record, "name": "Gone", "endpoint": "opc.tcp://127.0.0.1:1/none"}}).json()["link"]["id"]
    tg = eng.post(f"/api/equipment/links/{gone}/test").json()["link"]["last_test"]
    assert not tg["ok"] and tg["error"]
    assert err(eng.post(f"/api/equipment/links/{gone}/enable"))["code"] == "equipment_not_tested"
    # the runner: enabled link, values written by the "machine"
    eng.post(f"/api/equipment/links/{lid}/enable")
    runner = app.state.equipment.runner
    runner.start()
    try:
        def wait(cond, what, seconds=15):
            end = time.time() + seconds
            while time.time() < end:
                if cond():
                    return
                time.sleep(0.1)
            raise AssertionError(what + f" {runner.status} {eng.get(f'/api/equipment/links/{lid}').json()['counters']}")

        wait(lambda: eng.get(f"/api/equipment/links/{lid}").json()["runtime"]["state"] == "connected", "connected")
        time.sleep(0.5)  # the subscription delivers the value that the node has now, as the first reading
        base = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)
        server["write"](10.01, base + dt.timedelta(seconds=1)); time.sleep(0.3)
        server["write"](10.02, base + dt.timedelta(seconds=2)); time.sleep(0.3)
        server["write"](10.0, base + dt.timedelta(seconds=3), good=False); time.sleep(0.3)  # bad quality: not used
        server["write"](9.99, base + dt.timedelta(seconds=4)); time.sleep(0.3)
        wait(lambda: len(eng.get("/api/monitors/1/points").json()["points"]) == 1, "one point")
        p = eng.get("/api/monitors/1/points").json()["points"][0]
        assert p["tags"]["source"] == "opcua" and p["values"] == pytest.approx([10.0, 10.01, 10.02])
        c = eng.get(f"/api/equipment/links/{lid}").json()["counters"][bore]
        assert c["rejected"].get("bad_quality") == 1 and c["points"] == 1 and c["accepted"] == 4 and c["buffered"] == 1
        # the machine writes an old value again (a repeat): rejected
        server["write"](10.5, base + dt.timedelta(seconds=1)); time.sleep(0.5)
        wait(lambda: eng.get(f"/api/equipment/links/{lid}").json()["counters"][bore]["rejected"].get("old_or_repeated", 0) >= 1, "repeat rejected")
        # disabling ends the connection
        eng.post(f"/api/equipment/links/{lid}/disable")
        wait(lambda: lid not in runner.status, "stopped", 12)
    finally:
        runner.stop()


def test_the_runner_waits_for_a_server_that_is_not_there_yet_and_connects_when_it_comes(monkeypatch):
    from spc.equipment import client as opc

    monkeypatch.setattr(opc, "BACKOFF", (1,))
    app = make_app()
    eng = logged_in_client(app, "eng")
    eng.post("/api/monitors", json={"config": MONITOR, "source": {"type": "parameters", "mu": 10.0, "sigma": 0.1}})
    eng.post("/api/monitors/1/ack")
    app.state.equipment.prober = lambda link: {"ok": True, "error": None, "nodes": [{"node_id": n["node_id"], "ok": True, "value": 1.0, "type": "Double", "quality": "good", "source_time": "x", "error": None} for n in link["nodes"]]}
    record = {"name": "Late", "endpoint": "opc.tcp://127.0.0.1:48556/spc", "nodes": [{"node_id": "ns=2;s=Bore", "monitor_id": 1}], "interval_ms": 100}
    lid = eng.post("/api/equipment/links", json={"record": record}).json()["link"]["id"]
    eng.post(f"/api/equipment/links/{lid}/test")
    eng.post(f"/api/equipment/links/{lid}/enable")
    runner = app.state.equipment.runner
    runner.start()
    srv = None
    try:
        end = time.time() + 10
        while time.time() < end and runner.status.get(lid, {}).get("state") != "error":
            time.sleep(0.1)
        assert runner.status[lid]["state"] == "error" and runner.status[lid]["message"]
        srv = start_server(48556)
        end = time.time() + 20
        while time.time() < end and runner.status.get(lid, {}).get("state") != "connected":
            time.sleep(0.1)
        assert runner.status[lid]["state"] == "connected"
    finally:
        runner.stop()
        if srv:
            srv["stop"]()

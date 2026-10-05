import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from spc.core.charts.variable import imr, xbar_r, xbar_s
from spc.core.constants import ALPHA_3SIGMA
from spc.monitor.model import check_point, compute_limits, limits_from_values, ocap_for, statistic, validate_config
from spc.monitor.notify import ListNotifier, WebhookNotifier
from tests.conftest import PASSWORD, logged_in_client, make_app
from tests.test_api import err

CONFIG = {"name": "Shaft diameter", "process": "turning", "characteristic": "diameter", "unit": "mm", "line": "L1", "kind": "xbar-s",
          "n": 5, "warn_alpha": 0.05, "specs": {"lsl": 9.5, "usl": 10.5, "target_class": "major", "model": "A1"},
          "ocap": {"default": {"operator_action": "Measure again, then call the shift leader", "responsible": "Shift leader",
                               "escalate_to": "Process engineer", "escalate_after_min": 30},
                   "rules": {"run": {"operator_action": "Check the tool wear", "responsible": "Setter", "escalate_to": "", "escalate_after_min": 0}}}}
PARAMS = {"type": "parameters", "mu": 10.0, "sigma": 0.1}
ABOVE = [9.97, 10.09, 10.03, 10.08, 10.02]  # mean 10.038: above the centre line, spread normal
OK_SAMPLE = [9.95, 10.06, 10.0, 10.08, 9.96]  # mean 10.01, s 0.05: well inside both charts (a spread far BELOW the limits would alarm too)


def good_sample(rng, n=5, mu=10.0, sigma=0.1):
    return [float(v) for v in rng.normal(mu, sigma, n)]


# ------------------------------------------------------------------ configuration

def test_a_valid_configuration_is_normalised():
    cfg = validate_config(CONFIG)
    assert cfg["alpha"] == pytest.approx(ALPHA_3SIGMA) and cfg["rules"]["beyond_limits"] is True and cfg["require_ack"] is True
    assert cfg["ocap"]["rules"]["run"]["responsible"] == "Setter" and cfg["specs"]["controlled_stable"] is False
    imr_cfg = validate_config({"name": "x", "characteristic": "y", "kind": "imr"})
    assert imr_cfg["n"] == 1 and imr_cfg["warn_alpha"] is None


@pytest.mark.parametrize("change", [
    {"kind": "median"}, {"n": 1}, {"n": 26}, {"kind": "xbar-r", "n": 10}, {"kind": "imr", "n": 5}, {"name": " "}, {"characteristic": ""},
    {"alpha": 2}, {"warn_alpha": 0.001}, {"rules": {"nonsense": 1}}, {"specs": {"lsl": 5, "usl": 4}}, {"specs": {"target_class": "huge"}},
    {"specs": {"model": "Z9"}}, {"specs": {"nonsense": 1}}, {"ocap": {"rules": {"nonsense": {}}}}, {"ocap": {"default": {"escalate_after_min": -1}}},
    {"ocap": {"default": {"nonsense": 1}}}, {"nonsense": 1}, {"name": "x" * 101},
])
def test_bad_configurations_are_refused(change):
    with pytest.raises(ValueError):
        validate_config({**CONFIG, **change})


def test_ocap_falls_back_to_the_default_entry():
    cfg = validate_config(CONFIG)
    assert ocap_for(cfg, "run")["responsible"] == "Setter"
    assert ocap_for(cfg, "trend")["responsible"] == "Shift leader"


# ------------------------------------------------------------------ the limits

@pytest.mark.parametrize("kind,n", [("xbar-s", 5), ("xbar-s", 2), ("xbar-r", 5), ("xbar-r", 3), ("imr", 1)])
def test_each_plotted_point_has_the_stated_false_alarm_rate(kind, n):
    """Fixed limits from the true mu and sigma: under control, 3-sigma risk, both charts alarm about 0.27 % of the time."""
    rng = np.random.default_rng(11)
    k = 400_000
    mu, sigma = 10.0, 0.1
    lim = compute_limits(kind, n, ALPHA_3SIGMA, None, mu, sigma)
    x = rng.normal(mu, sigma, (k, n))
    if kind == "imr":
        loc, var = x[:, 0], np.abs(np.diff(x[:, 0]))
    else:
        loc = x.mean(axis=1)
        var = x.std(axis=1, ddof=1) if kind == "xbar-s" else x.max(axis=1) - x.min(axis=1)
    loc_rate = np.mean((loc > lim["location"]["ucl"]) | (loc < lim["location"]["lcl"]))
    var_rate = np.mean((var > lim["variation"]["ucl"]) | (var < lim["variation"]["lcl"]))
    assert loc_rate == pytest.approx(ALPHA_3SIGMA, rel=0.15) and var_rate == pytest.approx(ALPHA_3SIGMA, rel=0.15)
    assert np.mean(var) == pytest.approx(lim["variation"]["cl"], rel=0.01)  # the centre is the expected value of the statistic


def test_limits_from_reference_data_equal_the_analysis_chart():
    rng = np.random.default_rng(3)
    m = rng.normal(10, 0.1, (40, 5))
    for kind, chart in (("xbar-s", xbar_s(m)), ("xbar-r", xbar_r(m))):
        lim = limits_from_values(kind, 5, ALPHA_3SIGMA, None, m)
        assert lim["location"]["ucl"] == pytest.approx(chart.location.ucl) and lim["location"]["lcl"] == pytest.approx(chart.location.lcl)
        assert lim["variation"]["ucl"] == pytest.approx(chart.variation.ucl) and lim["variation"]["cl"] == pytest.approx(chart.variation.center)
    x = rng.normal(10, 0.1, 60)
    ch = imr(x)
    lim = limits_from_values("imr", 1, ALPHA_3SIGMA, 0.05, x)
    assert lim["location"]["ucl"] == pytest.approx(ch.location.ucl) and lim["variation"]["ucl"] == pytest.approx(ch.variation.ucl)
    assert lim["location"]["wucl"] < lim["location"]["ucl"] and lim["variation"]["wlcl"] > lim["variation"]["lcl"]
    with pytest.raises(ValueError):
        limits_from_values("xbar-s", 4, ALPHA_3SIGMA, None, m)  # wrong subgroup size
    with pytest.raises(ValueError):
        compute_limits("xbar-s", 5, ALPHA_3SIGMA, None, 10.0, 0.0)


def test_statistics_of_a_sample():
    assert statistic("xbar-s", [1, 2, 3, 4, 5], None) == (3.0, pytest.approx(math.sqrt(2.5)))
    assert statistic("xbar-r", [1, 2, 3, 4, 5], None) == (3.0, 4.0)
    assert statistic("imr", [7.0], 5.0) == (7.0, 2.0) and statistic("imr", [7.0], None) == (7.0, None)
    with pytest.raises(ValueError):
        statistic("xbar-s", [1, float("nan")], None)


def check(cfg_change=None, hist=(), loc=10.0, var=None, lim=None):
    """The variation statistic is left out unless a test asks for it, so that only the location chart speaks."""
    cfg = validate_config({**CONFIG, **(cfg_change or {})})
    lim = lim or compute_limits("xbar-s", 5, ALPHA_3SIGMA, 0.05, 10.0, 0.1)
    return check_point(cfg, lim, list(hist), [], loc, var)


def test_check_point_flags_limits_warnings_and_only_what_the_new_point_completes():
    assert check() == ([], [])
    alarms, _ = check(loc=10.2)
    assert alarms == [{"chart": "location", "rule": "beyond_limits"}]
    assert check(loc=9.7)[0][0]["rule"] == "beyond_limits"
    alarms, _ = check(var=0.3)
    assert alarms == [{"chart": "variation", "rule": "beyond_limits"}]
    alarms, warnings = check(loc=10.1)  # between the warning and the control limit
    assert alarms == [] and warnings == [{"chart": "location", "rule": "warning_limits"}]
    run = {"rules": {"run_length": 7}}
    assert check(run, hist=[10.01] * 5, loc=10.01)[0] == []  # a run of 6
    assert check(run, hist=[10.01] * 6, loc=10.01)[0] == [{"chart": "location", "rule": "run"}]
    assert check(run, hist=[10.01] * 8, loc=9.99)[0] == []  # the run was completed earlier: not this point's violation
    sigma_rule = {"rules": {"two_of_three_beyond_2s": True}}
    assert check(sigma_rule, hist=[10.1, 10.0], loc=10.1)[0][0]["rule"] == "two_of_three_beyond_2s"  # 10.1 is 2.2 standard errors
    no_warn = check({"warn_alpha": None}, loc=10.1, lim=compute_limits("xbar-s", 5, ALPHA_3SIGMA, None, 10.0, 0.1))  # limits without warning band
    assert no_warn == ([], [])


# ------------------------------------------------------------------ the API

@pytest.fixture
def env():
    app = make_app(max_upload=200_000)
    app.state.auth.create_user("oper", PASSWORD, "operator", "Olga Operator")
    app.state.auth.create_user("oper2", PASSWORD, "operator", "Omar Operator")
    clients = {u: logged_in_client(app, u) for u in ("admin", "eng", "view", "oper", "oper2")}
    return app, clients


def make_monitor(eng, config=None, source=None):
    r = eng.post("/api/monitors", json={"config": config or CONFIG, "source": source or PARAMS})
    assert r.status_code == 200, r.text
    return r.json()["monitor"]["id"]


def ack(op, mid):
    assert op.post(f"/api/monitors/{mid}/ack").status_code == 200


def enter(op, mid, values, **kw):
    r = op.post(f"/api/monitors/{mid}/points", json={"values": values, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_roles_decide_who_sets_up_who_enters_and_who_only_looks(env):
    app, c = env
    for who in ("view", "oper"):
        r = c[who].post("/api/monitors", json={"config": CONFIG, "source": PARAMS})
        assert r.status_code == 403
    mid = make_monitor(c["eng"])
    assert c["view"].get(f"/api/monitors/{mid}").status_code == 200 and c["view"].get("/api/monitors").status_code == 200
    assert c["view"].post(f"/api/monitors/{mid}/ack").status_code == 403
    assert c["view"].post(f"/api/monitors/{mid}/points", json={"values": [10] * 5}).status_code == 403
    ack(c["oper"], mid)
    enter(c["oper"], mid, OK_SAMPLE)
    assert c["oper"].put(f"/api/monitors/{mid}", json={"config": CONFIG}).status_code == 403
    assert c["oper"].post(f"/api/monitors/{mid}/limits", json={"source": PARAMS, "reason": "x"}).status_code == 403
    assert c["eng"].delete(f"/api/monitors/{mid}").status_code == 403
    assert c["admin"].delete(f"/api/monitors/{mid}").status_code == 200
    assert err(c["eng"].get(f"/api/monitors/{mid}"))["code"] == "monitor_not_found"


def test_the_action_plan_must_be_acknowledged_by_each_person_and_again_after_a_change(env):
    app, c = env
    mid = make_monitor(c["eng"])
    r = c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0] * 5})
    assert r.status_code == 403 and err(r)["code"] == "ocap_not_acknowledged"
    assert c["oper"].get(f"/api/monitors/{mid}").json()["acknowledged"] is False
    ack(c["oper"], mid)
    assert c["oper"].get(f"/api/monitors/{mid}").json()["acknowledged"] is True
    assert err(c["oper2"].post(f"/api/monitors/{mid}/points", json={"values": [10.0] * 5}))["code"] == "ocap_not_acknowledged"  # per person
    enter(c["oper"], mid, OK_SAMPLE)
    changed = {**CONFIG, "ocap": {"default": {**CONFIG["ocap"]["default"], "operator_action": "A new instruction"}}}
    assert c["eng"].put(f"/api/monitors/{mid}", json={"config": changed}).json()["monitor"]["ocap_rev"] == 2
    assert err(c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0] * 5}))["code"] == "ocap_not_acknowledged"
    ack(c["oper"], mid)
    same = c["eng"].put(f"/api/monitors/{mid}", json={"config": {**changed, "line": "L2"}}).json()["monitor"]
    assert same["ocap_rev"] == 2 and same["line"] == "L2"  # a change that is not about the plan keeps the acknowledgements
    enter(c["oper"], mid, OK_SAMPLE)
    free = {**CONFIG, "name": "Free", "require_ack": False}
    m2 = make_monitor(c["eng"], free)
    enter(c["oper2"], m2, OK_SAMPLE)  # a monitor can do without it


def test_creating_a_monitor_from_data_or_parameters_and_the_checks(env):
    app, c = env
    rng = np.random.default_rng(5)
    csv = "lot,v\n" + "\n".join(f"L{i // 5},{v:.4f}" for i, v in enumerate(rng.normal(10, 0.1, 125)))
    ds = c["eng"].post("/api/datasets", params={"value": "v", "subgroup": "lot"}, content=csv.encode()).json()
    mid = make_monitor(c["eng"], {**CONFIG, "name": "From data"}, {"type": "dataset", "dataset_id": ds["id"]})
    limits = c["eng"].get(f"/api/monitors/{mid}").json()["limits"]
    assert limits["revision"] == 1 and limits["source"]["type"] == "dataset" and limits["source"]["n_values"] == 125
    assert limits["location"]["lcl"] < 10.0 < limits["location"]["ucl"] and limits["reason"] == "initial limits"
    for config, source, code in (
        ({**CONFIG, "name": "A"}, {"type": "dataset", "dataset_id": "nope"}, "dataset_not_found"),
        ({**CONFIG, "name": "B", "n": 4}, {"type": "dataset", "dataset_id": ds["id"]}, "bad_source"),
        ({**CONFIG, "name": "C"}, {"type": "parameters", "mu": 10, "sigma": 0}, "bad_source"),
        ({**CONFIG, "name": "D"}, {"type": "parameters", "mu": "x", "sigma": 1}, "bad_source"),
        ({**CONFIG, "name": "E"}, {"type": "points", "seq_from": 1, "seq_to": 2}, "bad_source"),
        ({**CONFIG, "name": "F"}, {"type": "nonsense"}, "bad_source"),
        ({**CONFIG, "name": "G", "kind": "nonsense"}, PARAMS, "invalid_input"),
        ({**CONFIG}, PARAMS, "monitor_name_taken"),
    ):
        if code == "monitor_name_taken":
            make_monitor(c["eng"])
        r = c["eng"].post("/api/monitors", json={"config": config, "source": source})
        assert err(r)["code"] == code, (code, r.text)
    names = [m["name"] for m in c["eng"].get("/api/monitors").json()["monitors"]]
    assert "A" not in names and "From data" in names  # a refused monitor leaves nothing behind


def test_entering_points_status_and_checks(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    rng = np.random.default_rng(1)
    first = enter(c["oper"], mid, good_sample(rng), label="Lot 17", tags={"shift": "early"}, taken_at="2026-10-01T06:30:00Z")
    p = first["point"]
    assert first["status"] == "ok" and p["seq"] == 1 and p["label"] == "Lot 17" and p["tags"] == {"shift": "early"}
    assert p["taken_at"] == "2026-10-01T06:30:00Z" and p["entered_by"] == "Olga Operator (oper)" and p["limits_rev"] == 1
    assert first["incident"] is None and first["instructions"] == []
    assert enter(c["oper"], mid, good_sample(rng))["point"]["seq"] == 2
    warn = enter(c["oper"], mid, [10.04, 10.12, 10.16, 10.06, 10.14])  # mean 10.104: beyond the warning limit, spread normal
    assert warn["status"] == "warning" and warn["point"]["warnings"] == [{"chart": "location", "rule": "warning_limits"}]
    assert warn["incident"] is None  # a warning is only a warning
    for body, code in (({"values": [10.0] * 4}, "wrong_value_count"), ({"values": [10.0] * 6}, "wrong_value_count"),
                       ({"values": [10.0] * 5, "taken_at": "tomorrow"}, "bad_time"),
                       ({"values": [10.0] * 5, "taken_at": "2999-01-01T00:00:00Z"}, "time_in_future")):
        r = c["oper"].post(f"/api/monitors/{mid}/points", json=body)
        assert err(r)["code"] == code
    assert c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0, "x", 10, 10, 10]}).status_code == 422
    assert c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0] * 5, "tags": {f"k{i}": "v" for i in range(7)}}).status_code == 422
    assert c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0] * 5, "bogus": 1}).status_code == 422
    assert c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0] * 5, "label": "x" * 101}).status_code == 422
    assert [p["seq"] for p in c["view"].get(f"/api/monitors/{mid}/points").json()["points"]] == [1, 2, 3]  # refused samples left no point
    off = {**CONFIG, "active": False}
    c["eng"].put(f"/api/monitors/{mid}", json={"config": off})
    assert err(c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0] * 5}))["code"] == "monitor_inactive"


def test_the_limits_stay_fixed_until_somebody_sets_new_ones_with_a_reason(env):
    app, c = env
    cfg = {**CONFIG, "rules": {"run_length": 7}}
    mid = make_monitor(c["eng"], cfg)
    ack(c["oper"], mid)
    before = c["eng"].get(f"/api/monitors/{mid}").json()["limits"]
    for _ in range(6):
        assert enter(c["oper"], mid, ABOVE)["status"] == "ok"  # a run of 6 above the centre line
    assert c["eng"].get(f"/api/monitors/{mid}").json()["limits"] == before  # points never move the limits
    r = c["eng"].post(f"/api/monitors/{mid}/limits", json={"source": {"type": "parameters", "mu": 10.02, "sigma": 0.1}, "reason": " "})
    assert err(r)["code"] == "reason_required"
    view = c["eng"].post(f"/api/monitors/{mid}/limits", json={"source": {"type": "parameters", "mu": 10.02, "sigma": 0.1},
                                                              "reason": "Tool replaced, the level moved on purpose"}).json()
    assert view["limits"]["revision"] == 2 and view["limits"]["mu"] == 10.02 and view["limits"]["created_by"] == "Eva Engineer (eng)"
    assert [h["revision"] for h in view["limits_history"]] == [2, 1] and view["limits_history"][1]["reason"] == "initial limits"
    nxt = enter(c["oper"], mid, [10.0, 10.1, 10.03, 10.08, 10.04])
    assert nxt["status"] == "ok" and nxt["point"]["limits_rev"] == 2  # the run of the old revision does not count in the new one
    by_points = c["eng"].post(f"/api/monitors/{mid}/limits", json={"source": {"type": "points", "seq_from": 1, "seq_to": 5}, "reason": "from the good run"})
    assert by_points.status_code == 200 and by_points.json()["limits"]["source"]["n_points"] == 5
    assert err(c["eng"].post(f"/api/monitors/{mid}/limits", json={"source": {"type": "points", "seq_from": 9, "seq_to": 3}, "reason": "x"}))["code"] == "bad_source"


def test_individual_values_use_the_moving_range_to_the_previous_valid_value(env):
    app, c = env
    cfg = {"name": "Torque", "characteristic": "torque", "kind": "imr", "require_ack": False}
    mid = make_monitor(c["eng"], cfg, {"type": "parameters", "mu": 50.0, "sigma": 1.0})
    a = enter(c["oper"], mid, [50.5])["point"]
    b = enter(c["oper"], mid, [51.5])["point"]
    assert a["var"] is None and b["var"] == pytest.approx(1.0)
    c["oper"].post(f"/api/monitors/{mid}/points/2/invalid", json={"reason": "typing error"})
    third = enter(c["oper"], mid, [52.0])["point"]
    assert third["var"] == pytest.approx(1.5)  # to point 1: the invalid point is not the previous one


# ------------------------------------------------------------------ the out-of-control action plan

def alarm_values():
    return [10.5, 10.6, 10.55, 10.5, 10.6]


def open_incident(c, mid):
    r = enter(c["oper"], mid, alarm_values())
    assert r["status"] == "alarm" and r["incident"]["status"] == "open"
    return r["incident"]["id"]


def test_a_violation_opens_one_incident_with_the_instructions_of_the_plan(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    r = enter(c["oper"], mid, alarm_values())
    assert r["status"] == "alarm" and r["point"]["alarms"][0] == {"chart": "location", "rule": "beyond_limits"}
    assert r["instructions"][0]["operator_action"] == "Measure again, then call the shift leader" and r["instructions"][0]["responsible"] == "Shift leader"
    iid = r["incident"]["id"]
    again = enter(c["oper"], mid, alarm_values())
    assert again["incident"]["id"] == iid and again["verification"] is False  # one incident at a time
    ok = enter(c["oper"], mid, OK_SAMPLE)
    assert ok["verification"] is True and ok["status"] == "ok" and ok["point"]["incident_id"] == iid  # a sample during an open incident
    view = c["view"].get(f"/api/monitors/{mid}").json()
    assert view["incident"]["id"] == iid and [e["kind"] for e in view["incident"]["events"]].count("alarm") == 2
    assert [a["id"] for a in c["view"].get("/api/alerts").json()["incidents"]] == [iid]
    assert c["view"].get("/api/alerts").json()["unacknowledged"] == 1


def test_the_plan_is_followed_step_by_step_and_an_incident_closes_only_on_proof(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    iid = open_incident(c, mid)
    url = f"/api/monitors/{mid}/incidents/{iid}"
    close = lambda who, outcome, text="restored": c[who].post(f"{url}/close", json={"outcome": outcome, "text": text})
    assert err(close("oper", "recovered"))["code"] == "action_missing"  # nothing was done yet
    assert err(c["oper"].post(f"{url}/events", json={"kind": "action", "step": "adjust_parameters", "text": " "}))["code"] == "text_required"
    assert err(c["oper"].post(f"{url}/events", json={"kind": "action", "step": "resample", "text": "x"}))["code"] == "invalid_input"
    assert c["view"].post(f"{url}/events", json={"kind": "ack"}).status_code == 403
    assert c["oper"].post(f"{url}/events", json={"kind": "ack"}).status_code == 200
    taken = c["oper2"].post(f"{url}/events", json={"kind": "ack"})
    assert err(taken)["code"] == "already_acknowledged" and "Olga Operator (oper)" in err(taken)["message"]
    c["oper"].post(f"{url}/events", json={"kind": "action", "step": "adjust_parameters", "text": "Raised the feed by 2 %"})
    assert err(close("oper", "recovered"))["code"] == "verification_missing"  # no new sample after the action
    enter(c["oper"], mid, alarm_values())  # the sample after the action still violates
    assert err(close("oper", "recovered"))["code"] == "verification_missing"
    time.sleep(1.1)  # the verification must come after the action, to the second
    c["oper"].post(f"{url}/events", json={"kind": "action", "step": "adjust_elements", "text": "Changed the insert"})
    time.sleep(1.1)
    enter(c["oper"], mid, OK_SAMPLE)
    assert err(close("oper", "recovered", " "))["code"] == "text_required"
    done = close("oper", "recovered", "The insert was worn. Control is back.")
    assert done.status_code == 200 and done.json()["status"] == "closed" and done.json()["outcome"] == "recovered"
    assert done.json()["metrics"]["seconds_to_ack"] is not None and done.json()["metrics"]["seconds_to_close"] >= 2
    assert [e["kind"] for e in done.json()["events"]][-1] == "closed"
    assert err(c["oper"].post(f"{url}/events", json={"kind": "observation", "step": "other", "text": "late"}))["code"] == "incident_closed"
    assert err(close("oper", "recovered"))["code"] == "incident_closed"
    assert c["view"].get("/api/alerts").json()["open"] == 0
    follow = enter(c["oper"], mid, alarm_values())  # a new violation opens a new incident
    assert follow["incident"]["id"] != iid


def test_an_invalid_sample_ends_the_incident_and_does_not_count(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    iid = open_incident(c, mid)
    url = f"/api/monitors/{mid}/incidents/{iid}"
    assert err(c["oper"].post(f"{url}/close", json={"outcome": "invalid_sample"}))["code"] == "sample_still_valid"
    assert err(c["oper"].post(f"/api/monitors/{mid}/points/1/invalid", json={"reason": " "}))["code"] == "reason_required"
    p = c["oper"].post(f"/api/monitors/{mid}/points/1/invalid", json={"reason": "The gauge was not zeroed"}).json()
    assert p["valid"] is False and p["invalid"]["reason"] == "The gauge was not zeroed" and p["invalid"]["by"] == "Olga Operator (oper)"
    closed = c["view"].get(f"/api/monitors/{mid}/incidents").json()["incidents"][0]
    assert closed["status"] == "closed" and closed["outcome"] == "invalid_sample"
    assert err(c["oper"].post(f"/api/monitors/{mid}/points/1/invalid", json={"reason": "again"}))["code"] == "already_invalid"
    assert err(c["oper"].post(f"/api/monitors/{mid}/points/99/invalid", json={"reason": "x"}))["code"] == "point_not_found"


def test_an_incident_that_cannot_be_fixed_goes_to_root_cause_and_containment(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    iid = open_incident(c, mid)
    url = f"/api/monitors/{mid}/incidents/{iid}"
    esc = lambda: c["eng"].post(f"{url}/close", json={"outcome": "escalated", "text": "8D 2026-114 opened"})
    assert err(esc())["code"] == "escalation_missing"
    c["oper"].post(f"{url}/events", json={"kind": "escalation", "step": "other", "text": "Called the process engineer"})
    c["eng"].post(f"{url}/events", json={"kind": "action", "step": "containment", "text": "Sorted the last 3 lots"})
    c["eng"].post(f"{url}/events", json={"kind": "action", "step": "root_cause", "text": "8D started"})
    done = esc()
    assert done.status_code == 200 and done.json()["outcome"] == "escalated"
    kinds = [(e["kind"], e["step"]) for e in done.json()["events"]]
    assert ("escalation", "other") in kinds and ("action", "containment") in kinds and ("action", "root_cause") in kinds


def test_an_overdue_incident_is_flagged_and_the_response_times_are_reported(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    iid = open_incident(c, mid)
    assert c["view"].get(f"/api/monitors/{mid}").json()["incident"]["overdue"] is False
    app.state.db.execute("UPDATE monitor_incidents SET opened_at = '2020-01-01T00:00:00Z' WHERE id = ?", (iid,))  # the plan says: 30 minutes
    view = c["view"].get(f"/api/monitors/{mid}").json()["incident"]
    assert view["overdue"] is True and view["escalate_after_min"] == 30
    assert c["view"].get("/api/alerts").json()["overdue"] == 1
    c["oper"].post(f"/api/monitors/{mid}/incidents/{iid}/events", json={"kind": "escalation", "step": "other", "text": "Called"})
    assert c["view"].get(f"/api/monitors/{mid}").json()["incident"]["overdue"] is False  # escalated: no longer waiting
    resp = c["view"].get(f"/api/monitors/{mid}/ongoing", params={"window": 10})
    assert resp.status_code == 409  # too few points; the response report comes with the ongoing result below


def test_notifications_go_out_once_per_incident_and_a_broken_receiver_changes_nothing():
    got = ListNotifier()

    class Broken:
        def send(self, event):
            raise RuntimeError("receiver is down")

    app = make_app(max_upload=200_000, notifiers=[Broken(), got])
    eng = logged_in_client(app, "eng")
    app.state.auth.create_user("oper", PASSWORD, "operator", "Olga Operator")
    op = logged_in_client(app, "oper")
    mid = make_monitor(eng)
    ack(op, mid)
    enter(op, mid, alarm_values())
    enter(op, mid, alarm_values())  # same incident
    assert len(got.events) == 1
    e = got.events[0]
    assert e["type"] == "incident_opened" and e["monitor"]["name"] == "Shaft diameter" and e["rules"][0]["rule"] == "beyond_limits"
    assert e["point_seq"] == 1 and e["entered_by"] == "Olga Operator (oper)"


def test_the_webhook_notifier_posts_json_and_survives_a_dead_url():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.headers["Content-Type"], json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(204); self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    WebhookNotifier(f"http://127.0.0.1:{srv.server_port}/hook").send({"type": "incident_opened", "x": 1})
    for _ in range(50):
        if received:
            break
        time.sleep(0.05)
    srv.shutdown()
    assert received == [("application/json", {"type": "incident_opened", "x": 1})]
    WebhookNotifier("http://127.0.0.1:9/dead", timeout=0.5).send({"a": 1})  # no exception
    with pytest.raises(ValueError):
        WebhookNotifier("ftp://example.com/hook")


def test_everything_that_matters_is_in_the_audit_trail(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    iid = open_incident(c, mid)
    c["oper"].post(f"/api/monitors/{mid}/points/1/invalid", json={"reason": "gauge"})
    c["eng"].post(f"/api/monitors/{mid}/limits", json={"source": PARAMS, "reason": "re-evaluated"})
    c["eng"].put(f"/api/monitors/{mid}", json={"config": {**CONFIG, "line": "L9"}})
    actions = [e["action"] for e in c["admin"].get("/api/audit").json()["entries"]]
    for a in ("monitor_created", "monitor_ocap_ack", "monitor_incident_opened", "monitor_point_invalid", "monitor_incident_closed",
              "monitor_limits", "monitor_updated"):
        assert a in actions, a
    opened = next(e for e in c["admin"].get("/api/audit").json()["entries"] if e["action"] == "monitor_incident_opened")
    assert opened["username"] == "oper" and opened["detail"]["incident"] == iid
    assert c["admin"].get("/api/audit/verify").json()["ok"]


def test_the_shape_of_a_monitor_cannot_change(env):
    app, c = env
    mid = make_monitor(c["eng"])
    for change in ({"kind": "xbar-r"}, {"n": 4}, {"alpha": 0.01}):
        assert err(c["eng"].put(f"/api/monitors/{mid}", json={"config": {**CONFIG, **change}}))["code"] == "monitor_shape_locked"
    other = make_monitor(c["eng"], {**CONFIG, "name": "Other"})
    assert err(c["eng"].put(f"/api/monitors/{other}", json={"config": CONFIG}))["code"] == "monitor_name_taken"
    assert err(c["eng"].put("/api/monitors/999", json={"config": CONFIG}))["code"] == "monitor_not_found"
    listing = c["view"].get("/api/monitors").json()["monitors"]
    assert {m["name"] for m in listing} == {"Shaft diameter", "Other"} and listing[0]["open_incidents"] == 0 and listing[0]["points"] == 0


# ------------------------------------------------------------------ ongoing performance and capability

def fill(c, mid, rng, k, mu=10.0, sigma=0.1, label="p"):
    for i in range(k):
        enter(c["oper"], mid, good_sample(rng, 5, mu, sigma), label=f"{label}{i}")


def test_ongoing_report_gives_indices_quadrant_trend_limits_review_and_response(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    rng = np.random.default_rng(21)
    fill(c, mid, rng, 6)
    assert err(c["view"].get(f"/api/monitors/{mid}/ongoing"))["code"] == "not_enough_points"
    fill(c, mid, rng, 34)
    out = c["view"].get(f"/api/monitors/{mid}/ongoing", params={"window": 30, "steps": 3}).json()
    assert out["window"]["points"] == 30 and out["window"]["to_seq"] == 40 and out["window"]["from_seq"] == 11
    r = out["result"]
    assert r["indices"]["pk"] > 1.5 and r["stability"]["class"] in ("statistical_control", "in_control")  # the model A1 was given
    assert r["names"]["pk"] == "Cpk" and r["params"]["customer"] == "Shaft diameter"
    assert out["quadrant"] == {"number": 1, "stable": True, "meets_targets": True}
    assert 1 <= len(out["trend"]) <= 3 and out["trend"][-1]["end_seq"] == 40 and out["trend"][-1]["quadrant"]["number"] == 1
    review = out["limits_review"]
    assert review["verdict"] == "ok" and review["limits_revision"] == 1 and 0.8 < review["sigma_ratio"] < 1.25
    assert out["response"]["incidents"] == 0 and out["response"]["to_ack"] is None
    assert "usl" not in json.dumps(c["view"].get(f"/api/monitors/{mid}").json()["limits"])  # the chart data hold no specification limits


def test_the_limits_review_notices_limits_that_are_too_narrow_too_wide_or_off_centre(env):
    app, c = env
    rng = np.random.default_rng(22)
    verdicts = {}
    for name, sigma_limits, mu, sigma in (("narrow", 0.05, 10.0, 0.1), ("wide", 0.3, 10.0, 0.1), ("moved", 0.1, 10.06, 0.1)):
        mid = make_monitor(c["eng"], {**CONFIG, "name": name, "require_ack": False}, {"type": "parameters", "mu": 10.0, "sigma": sigma_limits})
        for _ in range(40):
            r = c["oper"].post(f"/api/monitors/{mid}/points", json={"values": good_sample(rng, 5, mu, sigma)})
            assert r.status_code == 200
        verdicts[name] = c["view"].get(f"/api/monitors/{mid}/ongoing", params={"window": 40}).json()["limits_review"]["verdict"]
    assert verdicts == {"narrow": "too_narrow", "wide": "too_wide", "moved": "location_moved"}


def test_an_unstable_and_incapable_process_lands_in_quadrant_iv(env):
    app, c = env
    mid = make_monitor(c["eng"], {**CONFIG, "name": "Bad", "require_ack": False})
    rng = np.random.default_rng(23)
    for i in range(40):
        c["oper"].post(f"/api/monitors/{mid}/points", json={"values": good_sample(rng, 5, 10.0 + 0.02 * i, 0.25)})
    out = c["view"].get(f"/api/monitors/{mid}/ongoing", params={"window": 40}).json()
    assert out["quadrant"]["number"] == 4 and out["quadrant"]["stable"] is False and out["quadrant"]["meets_targets"] is False
    assert out["result"]["names"]["pk"] == "Ppk"  # no stability evidence: performance, not capability


def test_ongoing_report_is_a_normal_report_on_the_window_with_a_stored_dataset(env):
    app, c = env
    mid = make_monitor(c["eng"])
    ack(c["oper"], mid)
    fill(c, mid, np.random.default_rng(24), 30)
    r = c["eng"].post(f"/api/monitors/{mid}/ongoing-report", json={"window": 25, "language": "en"})
    assert r.status_code == 200, r.text
    made = r.json()
    html = c["view"].get(made["urls"]["html"]).text
    assert "Shaft diameter" in html and "turning" in html and "diameter" in html and "L1" in html
    ds = c["view"].get(f"/api/datasets/{made['dataset_id']}").json()
    assert ds["summary"]["n_total"] == 125 and ds["summary"]["k_subgroups"] == 25
    assert "#6" in json.dumps(c["view"].get(f"/api/datasets/{made['dataset_id']}/rows").json())  # subgroups carry the point numbers
    check = c["eng"].post("/api/archive/check", content=c["view"].get(made["urls"]["archive"]).content).json()
    assert check["integrity_ok"] and check["reproduced"]
    assert c["oper"].post(f"/api/monitors/{mid}/ongoing-report", json={"window": 25}).status_code == 403
    nospec = make_monitor(c["eng"], {**CONFIG, "name": "NoSpec", "specs": {}, "require_ack": False})
    fill_no = np.random.default_rng(25)
    for _ in range(12):
        c["oper"].post(f"/api/monitors/{nospec}/points", json={"values": good_sample(fill_no)})
    assert err(c["eng"].post(f"/api/monitors/{nospec}/ongoing-report", json={"window": 12}))["code"] == "report_needs_spec"
    assert c["view"].get(f"/api/monitors/{nospec}/ongoing", params={"window": 12}).json()["result"]["indices"] is None

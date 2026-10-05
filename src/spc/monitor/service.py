"""The work of an SPC monitor: set up and fix limits, enter measurements, run the out-of-control action plan,
and report the ongoing performance. All changes are written together with their audit entry."""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import numpy as np

from spc.auth.audit import Audit
from spc.data import Dataset
from spc.db.database import Database
from spc.db.stores import DatasetStore, now_iso
from spc.monitor.model import (
    ACTION_STEPS, EVENT_KINDS, OUTCOMES, STEPS, MonitorError, check_point, compute_limits, limits_from_values, ocap_for,
    statistic, validate_config,
)
from spc.monitor.notify import Notifier
from spc.monitor.store import MonitorStore
from spc.service import AnalysisRequest, analyze

HISTORY_FOR_RULES = 100
MIN_ONGOING_POINTS = 10
TAG_LIMITS = (6, 30, 100)  # number of tags, key length, value length


def _label(user) -> str:
    return user.label


def _parse_time(value: str | None) -> str:
    """The time the sample was taken, as UTC 'YYYY-MM-DDTHH:MM:SSZ'. Default: now. Not more than 5 minutes ahead."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if not value:
        return now.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MonitorError("bad_time", "the time of the sample is not a date and time") from None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(timezone.utc).replace(microsecond=0)
    if t > now + timedelta(minutes=5):
        raise MonitorError("time_in_future", "the time of the sample lies in the future")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds(a: str, b: str) -> float:
    f = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    return (f(b) - f(a)).total_seconds()


class MonitorService:
    def __init__(self, db: Database, audit: Audit, datasets: DatasetStore, notifiers: Iterable[Notifier] = ()):
        self.db, self.audit, self.datasets, self.notifiers = db, audit, datasets, list(notifiers)
        self.store = MonitorStore(db)

    def _log(self, action: str, user, target: str, detail: dict) -> None:
        self.audit.append(action, user_id=user.id, username=user.username, target=target, detail=detail)

    def _notify(self, event: dict) -> None:
        for n in self.notifiers:
            try:
                n.send(event)
            except Exception:  # a notifier must never stop the work at the line
                pass

    # ------------------------------------------------------------------ set up
    def _limits_from(self, monitor: dict, source: dict) -> tuple[dict, dict]:
        """(limits, source description) from a source: a stored data set, expected parameters, or points of this monitor."""
        kind, n, alpha, warn = monitor["kind"], monitor["n"], monitor["alpha"], monitor["warn_alpha"]
        typ = source.get("type")
        if typ == "parameters":
            mu, sigma = source.get("mu"), source.get("sigma")
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (mu, sigma)):
                raise MonitorError("bad_source", "mu and sigma must be numbers")
            try:
                return compute_limits(kind, n, alpha, warn, mu, sigma), {"type": "parameters", "mu": mu, "sigma": sigma}
            except ValueError as exc:
                raise MonitorError("bad_source", str(exc)) from None
        if typ == "dataset":
            ds = self.datasets.get(source.get("dataset_id", ""))
            try:
                if kind == "imr":
                    values, _ = ds.individuals()
                    ref = values
                else:
                    sg = ds.subgroups(size=None if ds.subgroup is not None else n, incomplete="drop")
                    ref = sg.matrix
                limits = limits_from_values(kind, n, alpha, warn, ref)
            except ValueError as exc:
                raise MonitorError("bad_source", str(exc)) from None
            name = ds.source.name if ds.source else ""
            return limits, {"type": "dataset", "dataset_id": source["dataset_id"], "name": name, "n_values": int(np.size(ref))}
        if typ == "points":
            lo, hi = source.get("seq_from"), source.get("seq_to")
            if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in (lo, hi)) or lo > hi:
                raise MonitorError("bad_source", "seq_from and seq_to must be point numbers, seq_from <= seq_to")
            pts = [p for p in self.store.points(monitor["id"], limit=100000, since_seq=lo) if p["seq"] <= hi and p["valid"]]
            try:
                ref = [p["values"][0] for p in pts] if kind == "imr" else [p["values"] for p in pts]
                limits = limits_from_values(kind, n, alpha, warn, ref)
            except ValueError as exc:
                raise MonitorError("bad_source", str(exc)) from None
            return limits, {"type": "points", "seq_from": lo, "seq_to": hi, "n_points": len(pts)}
        raise MonitorError("bad_source", "source.type must be dataset, parameters or points")

    def create(self, config_in: dict, source: dict, user) -> dict:
        try:
            config = validate_config(config_in)
        except ValueError as exc:
            raise MonitorError("invalid_input", str(exc)) from None
        if source.get("type") == "points":
            raise MonitorError("bad_source", "a new monitor has no points yet")
        with self.db.tx():
            monitor_id = self.store.create(config, user.id)
            monitor = self.store.get(monitor_id)
            limits, described = self._limits_from(monitor, source)
            self.store.add_limits(monitor_id, described, limits, "initial limits", _label(user))
            self._log("monitor_created", user, config["name"], {"id": monitor_id, "kind": config["kind"], "n": config["n"], "source": described["type"]})
        return self.store.get(monitor_id)

    def update(self, monitor_id: int, config_in: dict, user) -> dict:
        old = self.store.get(monitor_id)
        try:
            config = validate_config(config_in)
        except ValueError as exc:
            raise MonitorError("invalid_input", str(exc)) from None
        if (config["kind"], config["n"]) != (old["kind"], old["n"]):
            raise MonitorError("monitor_shape_locked", "the chart type and the subgroup size cannot change: make a new monitor", 409)
        if (config["alpha"], config["warn_alpha"]) != (old["alpha"], old["warn_alpha"]):
            raise MonitorError("monitor_shape_locked", "the risk of the limits changes with new limits: set new limits with a reason", 409)
        with self.db.tx():
            new = self.store.update(monitor_id, config)
            self._log("monitor_updated", user, config["name"], {"id": monitor_id, "revision": new["revision"],
                                                                  "ocap_revision": new["ocap_rev"]})
        return new

    def set_limits(self, monitor_id: int, source: dict, reason: str, user) -> dict:
        """New fixed limits. The criteria start again from the first point after this."""
        if not reason or not reason.strip():
            raise MonitorError("reason_required", "a reason is required to set new limits")
        monitor = self.store.get(monitor_id)
        with self.db.tx():
            limits, described = self._limits_from(monitor, source)
            rev = self.store.add_limits(monitor_id, described, limits, reason.strip(), _label(user))
            self._log("monitor_limits", user, monitor["name"], {"id": monitor_id, "revision": rev, "source": described, "reason": reason.strip()})
        return self.store.limits(monitor_id, rev)

    def acknowledge_ocap(self, monitor_id: int, user) -> None:
        monitor = self.store.get(monitor_id)
        with self.db.tx():
            self.store.add_ack(monitor_id, user.id, monitor["ocap_rev"])
            self._log("monitor_ocap_ack", user, monitor["name"], {"id": monitor_id, "ocap_revision": monitor["ocap_rev"]})

    # ------------------------------------------------------------------ measurements
    def _instructions(self, monitor: dict, alarms: list) -> list[dict]:
        seen, out = set(), []
        for a in alarms:
            if a["rule"] in seen:
                continue
            seen.add(a["rule"])
            out.append({"rule": a["rule"], **ocap_for(monitor, a["rule"])})
        return out

    def add_point(self, monitor_id: int, values, label: str, tags: dict, taken_at: str | None, user) -> dict:
        monitor = self.store.get(monitor_id)
        if not monitor["active"]:
            raise MonitorError("monitor_inactive", "this monitor is switched off", 409)
        if not isinstance(values, (list, tuple)) or len(values) != monitor["n"] or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
            raise MonitorError("wrong_value_count", f"this monitor takes {monitor['n']} measured value(s) per sample", n=monitor["n"])
        if len(label) > 100:
            raise MonitorError("invalid_input", "the label is longer than 100 characters")
        count, klen, vlen = TAG_LIMITS
        if len(tags) > count or any(not isinstance(k, str) or not isinstance(v, str) or not 0 < len(k) <= klen or len(v) > vlen for k, v in tags.items()):
            raise MonitorError("invalid_input", f"at most {count} tags, key up to {klen} and value up to {vlen} characters")
        when = _parse_time(taken_at)
        if monitor["require_ack"] and not self.store.has_ack(monitor_id, user.id, monitor["ocap_rev"]):
            raise MonitorError("ocap_not_acknowledged", "confirm that you know the action plan of this monitor first", 403)
        with self.db.tx():
            limits = self.store.limits(monitor_id)
            if limits is None:
                raise MonitorError("no_limits", "this monitor has no limits yet", 409)
            previous = self.store.previous_value(monitor_id) if monitor["kind"] == "imr" else None
            loc, var = statistic(monitor["kind"], values, previous)
            hist_loc, hist_var = self.store.valid_history(monitor_id, limits["revision"], HISTORY_FOR_RULES)
            alarms, warnings = check_point(monitor, limits, hist_loc, hist_var, loc, var)
            seq = self.store.next_seq(monitor_id)
            incident = self.store.open_incident_of(monitor_id)
            incident_id = incident["id"] if incident else None
            self.store.insert_point(monitor_id, seq, limits_rev=limits["revision"], taken_at=when, entered_by=_label(user), label=label.strip(),
                                    tags=tags, values=[float(v) for v in values], loc=loc, var=var, alarms=alarms, warnings=warnings,
                                    incident_id=incident_id)
            opened = None
            if alarms and incident is None:
                opened = self.store.open_incident(monitor_id, seq, alarms)
                incident_id = opened
                self.store.set_point_incident(monitor_id, seq, opened)
                self.store.add_event(monitor_id, opened, "system", "alarm", "", ", ".join(f"{a['chart']}:{a['rule']}" for a in alarms))
                self._log("monitor_incident_opened", user, monitor["name"], {"id": monitor_id, "incident": opened, "point": seq, "rules": alarms})
            elif alarms:  # one incident at a time: the new violation is recorded in it
                self.store.add_event(monitor_id, incident_id, "system", "alarm", "",
                                     f"point {seq}: " + ", ".join(f"{a['chart']}:{a['rule']}" for a in alarms))
            point = self.store.point(monitor_id, seq)
            incident_now = self.store.incident(incident_id) if incident_id else None
        if opened:
            self._notify({"type": "incident_opened", "at": now_iso(), "monitor": {"id": monitor_id, "name": monitor["name"], "line": monitor["line"],
                                                                                   "characteristic": monitor["characteristic"]},
                          "incident_id": opened, "point_seq": seq, "rules": alarms, "entered_by": _label(user)})
        status = "alarm" if alarms else ("warning" if warnings else "ok")
        return {"point": point, "status": status, "incident": incident_now, "instructions": self._instructions(monitor, alarms),
                "verification": bool(incident and not alarms)}

    def invalidate_point(self, monitor_id: int, seq: int, reason: str, user) -> dict:
        if not reason or not reason.strip():
            raise MonitorError("reason_required", "a reason is required to declare a sample invalid")
        monitor = self.store.get(monitor_id)
        point = self.store.point(monitor_id, seq)
        if point is None:
            raise MonitorError("point_not_found", "no such point", 404)
        if not point["valid"]:
            raise MonitorError("already_invalid", "the point is invalid already", 409)
        with self.db.tx():
            self.store.invalidate(monitor_id, seq, reason.strip(), _label(user))
            self._log("monitor_point_invalid", user, monitor["name"], {"id": monitor_id, "point": seq, "reason": reason.strip()})
            inc = self.store.incident(point["incident_id"]) if point["incident_id"] else None
            if inc and inc["status"] == "open" and inc["point_seq"] == seq:
                # draft 10.2.3: the sample is repeated to make sure it is valid. An invalid sample ends the incident.
                self.store.add_event(monitor_id, inc["id"], _label(user), "observation", "resample", f"sample declared invalid: {reason.strip()}")
                self.store.close_incident(inc["id"], _label(user), "invalid_sample", reason.strip())
                self.store.add_event(monitor_id, inc["id"], _label(user), "closed", "", "invalid_sample")
                self._log("monitor_incident_closed", user, monitor["name"], {"id": monitor_id, "incident": inc["id"], "outcome": "invalid_sample"})
        return self.store.point(monitor_id, seq)

    # ------------------------------------------------------------------ out-of-control action plan
    def _incident(self, monitor_id: int, incident_id: int) -> dict:
        inc = self.store.incident(incident_id)
        if inc["monitor_id"] != monitor_id:
            raise MonitorError("incident_not_found", "no such incident", 404)
        return inc

    def incident_view(self, monitor: dict, inc: dict) -> dict:
        events = self.store.events(incident_id=inc["id"])
        minutes = [ocap_for(monitor, r["rule"])["escalate_after_min"] for r in inc["rules"]]
        minutes = [m for m in minutes if m > 0]
        now = now_iso()
        overdue = bool(inc["status"] == "open" and minutes and _seconds(inc["opened_at"], now) > min(minutes) * 60
                       and not any(e["kind"] == "escalation" for e in events))
        first_action = next((e for e in events if e["kind"] in ("action", "escalation")), None)
        metrics = {"seconds_to_ack": _seconds(inc["opened_at"], inc["acked_at"]) if inc["acked_at"] else None,
                   "seconds_to_action": _seconds(inc["opened_at"], first_action["at"]) if first_action else None,
                   "seconds_to_close": _seconds(inc["opened_at"], inc["closed_at"]) if inc["closed_at"] else None}
        return {**inc, "events": events, "overdue": overdue, "escalate_after_min": min(minutes) if minutes else 0,
                "instructions": self._instructions(monitor, inc["rules"]), "metrics": metrics}

    def add_event(self, monitor_id: int, incident_id: int, kind: str, step: str, text: str, user) -> dict:
        monitor, inc = self.store.get(monitor_id), self._incident(monitor_id, incident_id)
        if inc["status"] != "open":
            raise MonitorError("incident_closed", "the incident is closed", 409)
        if kind not in EVENT_KINDS:
            raise MonitorError("invalid_input", f"kind must be one of {EVENT_KINDS}")
        if kind in ("action", "observation", "escalation") and step not in STEPS:
            raise MonitorError("invalid_input", f"step must be one of {STEPS}")
        if kind == "action" and step not in ACTION_STEPS:
            raise MonitorError("invalid_input", f"an action is one of {ACTION_STEPS}: a repeated sample is entered as a measurement")
        if kind in ("action", "escalation") and not text.strip():
            raise MonitorError("text_required", "describe what was done")
        if len(text) > 2000:
            raise MonitorError("invalid_input", "the text is longer than 2000 characters")
        if kind == "ack" and inc["acked_by"]:
            raise MonitorError("already_acknowledged", f"{inc['acked_by']} has taken this over already", 409)
        with self.db.tx():
            if kind in ("ack", "action", "escalation"):
                self.store.ack_incident(incident_id, _label(user))  # the first response is the acknowledgement
            event = self.store.add_event(monitor_id, incident_id, _label(user), kind, step if kind != "ack" else "", text.strip())
            self._log("monitor_incident_" + kind, user, monitor["name"], {"id": monitor_id, "incident": incident_id, "step": step, "text": text.strip()})
        return event

    def close_incident(self, monitor_id: int, incident_id: int, outcome: str, text: str, user) -> dict:
        monitor, inc = self.store.get(monitor_id), self._incident(monitor_id, incident_id)
        if inc["status"] != "open":
            raise MonitorError("incident_closed", "the incident is closed already", 409)
        if outcome not in OUTCOMES:
            raise MonitorError("invalid_input", f"outcome must be one of {OUTCOMES}")
        events = self.store.events(incident_id=incident_id)
        actions = [e for e in events if e["kind"] == "action"]
        if outcome == "invalid_sample":
            trigger = self.store.point(monitor_id, inc["point_seq"])
            if trigger and trigger["valid"]:
                raise MonitorError("sample_still_valid", "declare the sample invalid on the point, with a reason, first", 409)
        elif outcome == "recovered":
            if not actions:
                raise MonitorError("action_missing", "document the corrective action before the incident is closed", 409)
            last = max(e["at"] for e in actions)
            later = [p for p in self.store.points(monitor_id, limit=1000, since_seq=inc["point_seq"] + 1)
                     if p["valid"] and not p["alarms"] and not p["warnings"] and p["entered_at"] >= last]
            if not later:
                # draft 10.2.3: a new sample after the action must meet all criteria before monitoring goes on
                raise MonitorError("verification_missing", "enter a new sample after the last action: it must meet all criteria", 409)
        else:  # escalated: the draft sends it to a root cause analysis (8D, Ishikawa, 5-Why) and product containment
            if not (any(e["kind"] == "escalation" for e in events) or any(e["step"] == "root_cause" for e in actions)):
                raise MonitorError("escalation_missing", "log the escalation or the start of the root cause analysis first", 409)
        if outcome != "invalid_sample" and not text.strip():
            raise MonitorError("text_required", "write the result: what restored control, or where the case goes on")
        with self.db.tx():
            self.store.close_incident(incident_id, _label(user), outcome, text.strip())
            self.store.add_event(monitor_id, incident_id, _label(user), "closed", "", outcome)
            self._log("monitor_incident_closed", user, monitor["name"], {"id": monitor_id, "incident": incident_id, "outcome": outcome, "text": text.strip()})
        return self.incident_view(monitor, self.store.incident(incident_id))

    # ------------------------------------------------------------------ views
    def view(self, monitor_id: int, user, limit: int = 100) -> dict:
        monitor = self.store.get(monitor_id)
        inc = self.store.open_incident_of(monitor_id)
        return {
            "monitor": monitor,
            "limits": self.store.limits(monitor_id),
            "limits_history": [{k: v for k, v in h.items() if k in ("revision", "created_at", "created_by", "reason", "source")}
                               for h in self.store.limits_history(monitor_id)],
            "points": self.store.points(monitor_id, limit=limit),
            "incident": None if inc is None else self.incident_view(monitor, inc),
            "acknowledged": self.store.has_ack(monitor_id, user.id, monitor["ocap_rev"]),
        }

    def incidents(self, monitor_id: int | None, status: str | None) -> list[dict]:
        out = []
        for inc in self.store.incidents(monitor_id, status):
            out.append(self.incident_view(self.store.get(inc["monitor_id"]), inc))
        return out

    # ------------------------------------------------------------------ ongoing performance and capability (draft 10.4)
    def window_dataset(self, monitor: dict, window: int) -> tuple[Dataset, list[dict]]:
        pts = [p for p in self.store.points(monitor["id"], limit=window) if p["valid"]]
        if len(pts) < MIN_ONGOING_POINTS:
            raise MonitorError("not_enough_points", f"the ongoing report needs at least {MIN_ONGOING_POINTS} valid points", 409, have=len(pts))
        return self._dataset_of(monitor, pts), pts

    def analysis_request(self, monitor: dict) -> AnalysisRequest:
        sp = monitor["specs"]
        return AnalysisRequest(stage="production", chart=monitor["kind"], lsl=sp["lsl"], usl=sp["usl"], alpha=monitor["alpha"],
                               rules=dict(monitor["rules"]), model=sp["model"], controlled_stable=sp["controlled_stable"],
                               characteristic_class=sp["target_class"], edition=sp["edition"], customer=monitor["name"])

    def ongoing(self, monitor_id: int, window: int = 125, steps: int = 6) -> dict:
        """Index and stability over a rolling window, the four quadrants, the trend over earlier windows,
        a check of the limits against the data, and the response times of the action plan."""
        monitor = self.store.get(monitor_id)
        ds, pts = self.window_dataset(monitor, window)
        request = self.analysis_request(monitor)
        result = analyze(ds, request)
        quadrant = self._quadrant(result)
        trend = []
        all_pts = [p for p in self.store.points(monitor_id, limit=100000) if p["valid"]]
        step = max(1, window // 4)
        for k in range(steps):
            end = len(all_pts) - k * step
            part = all_pts[max(0, end - window):end]
            if len(part) < MIN_ONGOING_POINTS or request.lsl is None and request.usl is None:
                break
            try:
                sub = self._dataset_of(monitor, part)
                r = analyze(sub, request)
            except ValueError:
                break
            if r["indices"] is None:
                break
            trend.append({"end_seq": part[-1]["seq"], "n_points": len(part), "name_pk": r["names"]["pk"], "pk": r["indices"]["pk"],
                          "stability": r["stability"]["class"], "quadrant": self._quadrant(r)})
        trend.reverse()
        return {"window": {"points": len(pts), "from_seq": pts[0]["seq"], "to_seq": pts[-1]["seq"],
                           "from": pts[0]["taken_at"], "to": pts[-1]["taken_at"]},
                "result": result, "quadrant": quadrant, "trend": trend,
                "limits_review": self._limits_review(monitor, pts), "response": self._response(monitor_id)}

    def _dataset_of(self, monitor: dict, pts: list[dict]) -> Dataset:
        values, labels, times = [], [], []
        for p in pts:
            for v in p["values"]:
                values.append(v); labels.append(f"#{p['seq']}"); times.append(p["taken_at"].rstrip("Z"))
        return Dataset.from_values(values, subgroup=None if monitor["kind"] == "imr" else labels, timestamp=times)

    @staticmethod
    def _quadrant(result: dict) -> dict:
        """I stable and capable, II performing but not stable, III capable target missed but stable, IV neither (draft figure 10-26)."""
        stable = result["stability"]["class"] in ("statistical_control", "in_control")
        tg = result["targets"]
        verdicts = [] if not tg or tg.get("blocked") else [v for v in (tg.get("verdict_p"), tg.get("verdict_pk")) if v]
        meets = bool(verdicts) and all(v in ("meets", "meets_estimate_only", "meets_no_interval") for v in verdicts)
        known = bool(verdicts)
        number = (1 if meets else 3) if stable else (2 if meets else 4)
        return {"number": number if known else None, "stable": stable, "meets_targets": meets if known else None}

    def _limits_review(self, monitor: dict, pts: list[dict]) -> dict:
        """Are the fixed limits still right? Compare the observed spread and the share of alarms with what the limits expect
        (draft 10.4: too narrow limits cause over-regulation, too wide limits hide chances to improve)."""
        limits = self.store.limits(monitor["id"])
        k = len(pts)
        alarm_points = sum(1 for p in pts if p["alarms"])
        expected = k * monitor["alpha"]
        if monitor["kind"] == "xbar-s":
            sigma = math.sqrt(statistics.fmean([p["var"] ** 2 for p in pts]))
        elif monitor["kind"] == "xbar-r":
            from spc.core.constants import d2
            sigma = statistics.fmean([p["var"] for p in pts]) / d2(monitor["n"])
        else:
            from spc.core.constants import d2
            mrs = [p["var"] for p in pts if p["var"] is not None]
            sigma = statistics.fmean(mrs) / d2(2) if mrs else float("nan")
        ratio = sigma / limits["sigma"] if limits["sigma"] and math.isfinite(sigma) else None
        loc = [p["loc"] for p in pts]
        shift = None if not loc else (statistics.fmean(loc) - limits["mu"]) / (limits["sigma"] / math.sqrt(monitor["n"]))
        # the order matters: a shifted location also causes alarms, and lower variation limits catch good news
        if ratio is not None and ratio < 0.8:
            verdict = "too_wide"
        elif ratio is not None and ratio > 1.25:
            verdict = "too_narrow"
        elif shift is not None and abs(shift) > 1.0:
            verdict = "location_moved"
        elif alarm_points > max(3.0, 3 * expected):
            verdict = "too_narrow"
        else:
            verdict = "ok"
        return {"verdict": verdict, "points": k, "alarm_points": alarm_points, "expected_alarm_points": expected,
                "sigma_ratio": ratio, "location_shift_in_se": shift, "limits_revision": limits["revision"]}

    def _response(self, monitor_id: int) -> dict:
        """Response times of the action plan (seconds): until somebody took it over, until the first action, until it was closed."""
        monitor = self.store.get(monitor_id)
        views = [self.incident_view(monitor, i) for i in self.store.incidents(monitor_id, None, 1000)]

        def stat(key):
            v = [x["metrics"][key] for x in views if x["metrics"][key] is not None]
            return None if not v else {"n": len(v), "median": statistics.median(v), "max": max(v)}

        return {"incidents": len(views), "open": sum(1 for x in views if x["status"] == "open"),
                "overdue": sum(1 for x in views if x["overdue"]),
                "outcomes": {o: sum(1 for x in views if x["outcome"] == o) for o in OUTCOMES},
                "to_ack": stat("seconds_to_ack"), "to_action": stat("seconds_to_action"), "to_close": stat("seconds_to_close")}

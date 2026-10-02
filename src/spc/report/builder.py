"""Collect everything a report shows. No layout here, only facts, figures and sentences.

The report is made from the same analysis run that the user sees. Nothing is calculated a second
time with other settings, so numbers in the report and on the screen cannot differ.

The within-subgroup indices Cw/Cwk are left out on purpose: the handbook says they are not for reports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import chi2, norm, t as student_t

from spc import __version__
from spc.report import svg
from spc.report.meta import ReportMeta
from spc.report.texts import T
from spc.service import AnalysisRequest, analyze_detailed
from spc.data import Dataset


class ReportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Report:
    lang: str
    meta: ReportMeta
    request: AnalysisRequest
    result: dict
    facts: dict
    figures: dict
    conclusions: list
    marked: list
    trace: dict
    generated_at: str
    report_id: str
    archive_digest: str = ""


def _stamp(t) -> str:
    return str(t).replace("T", " ")


def _duration(seconds: float) -> str:
    s = int(round(seconds))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    return (f"{d} d " if d else "") + f"{h:02d}:{m:02d}:{sec:02d}"


def _interval_minutes(dataset: Dataset, groups: list[list[int]]) -> float | None:
    """Median time between the first values of consecutive subgroups (or points), in minutes."""
    if dataset.timestamp is None or len(groups) < 2:
        return None
    times = np.array([dataset.timestamp[g[0]] for g in groups], dtype="datetime64[s]")
    if np.isnat(times).any():
        return None
    steps = np.diff(times).astype("int64") / 60.0
    if (steps <= 0).any():
        return None
    return float(np.median(steps))


def _rule_texts(lang: str, rules: dict) -> list[str]:
    out = []
    if rules.get("beyond_limits"):
        out.append(T(lang, "v.rule_beyond_limits"))
    if rules.get("run_length"):
        out.append(T(lang, "v.rule_run", n=rules["run_length"]))
    if rules.get("trend_length"):
        out.append(T(lang, "v.rule_trend", n=rules["trend_length"]))
    for key in ("middle_third", "two_of_three_beyond_2s", "four_of_five_beyond_1s", "fifteen_within_1s"):
        if rules.get(key):
            out.append(T(lang, f"v.rule_{key}"))
    return out


def build_report(
    dataset: Dataset,
    request: AnalysisRequest,
    meta: ReportMeta,
    lang: str = "en",
    *,
    generated_at: str,
    report_id: str,
    archive_digest: str = "",
    outcome=None,
) -> Report:
    meta.validate()
    outcome = outcome or analyze_detailed(dataset, request)
    r = outcome.result
    if r["indices"] is None:
        raise ReportError("spec_missing", "a report needs at least one specification limit")
    x = np.asarray(outcome.values, dtype=float)
    n = int(x.size)
    ix = r["indices"]
    mean, sd = ix["mean"], ix["sd"]
    dist = outcome.dist if outcome.dist is not None else norm(mean, sd)  # fitted distribution or the normal one
    blk = r.get("distribution") if outcome.dist is not None else None
    conf = r["params"]["estimate_confidence"]
    level = f"{round(conf * 1000) / 10:g} %"
    lsl, usl = request.lsl, request.usl
    stage = r["stage"]
    names = r["names"]
    suffix = f".{request.method}" if blk else ".G"  # for a normal distribution .G and .Z give the same result
    name_p, name_pk = names["p"] + suffix, names["pk"] + suffix
    stability = r["stability"]
    chart = r["chart"]
    warn_codes = {w["code"]: w["params"] for w in r["warnings"]}

    # ---- numbers for elements 9, 15, 16
    a = 1.0 - conf
    half = float(student_t.ppf(1 - a / 2, n - 1)) * sd / math.sqrt(n)
    sd_ci = (sd * math.sqrt((n - 1) / chi2.ppf(1 - a / 2, n - 1)), sd * math.sqrt((n - 1) / chi2.ppf(a / 2, n - 1)))
    q_low, q_mid, q_high = (float(dist.ppf(p)) for p in (0.00135, 0.5, 0.99865))

    # ---- elements 4 and 10 from the time stamps when there are any
    used = set(int(p) for p in outcome.positions)
    period = None
    if dataset.timestamp is not None and used:
        stamps = np.array([dataset.timestamp[p] for p in sorted(used)], dtype="datetime64[s]")
        stamps = stamps[~np.isnat(stamps)]
        if stamps.size:
            seconds = float((stamps.max() - stamps.min()).astype("int64"))
            period = {"start": _stamp(stamps.min()), "end": _stamp(stamps.max()), "duration": _duration(seconds)}
    interval = _interval_minutes(dataset, chart["location"]["positions"])

    # ---- element 22
    guard = None
    if meta.uncertainty:
        u = meta.uncertainty / meta.coverage_factor
        z = float(norm.ppf(1 - meta.guard_band_risk))
        g = z * u
        guard = {
            "U": meta.uncertainty, "k": meta.coverage_factor, "u": u, "z": z, "g": g,
            "risk": meta.guard_band_risk,
            "accept_low": None if lsl is None else lsl + g,
            "accept_high": None if usl is None else usl - g,
        }

    # ---- share outside specification, split by side
    below = float(dist.cdf(lsl)) * 1e6 if lsl is not None else None
    above = float(dist.sf(usl)) * 1e6 if usl is not None else None

    facts = {
        "stage": stage, "n": n, "n_total": r["counts"]["n_total"], "n_marked": r["counts"]["n_invalid"],
        "n_unused": r["counts"]["n_total"] - r["counts"]["n_invalid"] - n,
        "subgroup_size": r["counts"]["subgroup_size"], "k": chart["k"], "chart_kind": chart["kind"],
        "alpha": chart["alpha"], "period": period, "interval_min": interval,
        "mean": mean, "mean_ci": (mean - half, mean + half), "sd": sd, "sd_ci": sd_ci,
        "q_low": q_low, "q_mid": q_mid, "q_high": q_high, "level": level,
        "tolerance": (usl - lsl) if (lsl is not None and usl is not None) else None,
        "name_p": name_p, "name_pk": name_pk, "ppm_below": below, "ppm_above": above,
        "ppm_total": ix["ppm"], "guard": guard, "normality": r["normality"],
        "criteria": _rule_texts(lang, r["params"]["rules"]),
        "dist": blk, "method": request.method,
        "moving_n": chart.get("moving_n"),
        "restarts": [{"source_row": int(dataset.source_rows[p]), "reason": rs, "by": by, "at": at,
                      "new_limits": p in dataset.phase_positions()}
                     for p, (rs, by, at) in dataset.restart_info().items()] if chart.get("moving_n") else [],
        "phases": [{"from_row": chart["location"]["labels"][ph["start"]], "n": ph["n_values"], "mu": ph["mu_hat"],
                    "sigma": ph["sigma_hat"]} for ph in chart.get("phase_stats", [])],
    }

    # ---- figures
    L = lambda key, **p: T(lang, key, **p)
    all_states = np.zeros(dataset.n_total, dtype=int)
    all_states[~dataset.valid_mask] = 1
    for p in range(dataset.n_total):
        if all_states[p] == 0 and p not in used:
            all_states[p] = 2
    fit_hist = L("fig.hist.fit_dist", name=L("v.dist_" + blk["name"])) if blk else L("fig.hist.fit")
    fit_prob = L("fig.prob.fit_dist", name=L("v.dist_" + blk["name"])) if blk else L("fig.prob.fit")
    figures = {
        "hist": svg.histogram(
            x, lsl, usl, meta.target, mean, sd,
            {"title": L("fig.hist.title"), "x": L("fig.hist.x"), "y": L("fig.hist.y"), "target": L("fig.target"),
             "bars": L("fig.hist.bars"), "fit": fit_hist, "spec": L("fig.hist.spec")},
            dist=outcome.dist,
        ),
        "run": svg.run_chart(
            dataset.values, all_states, lsl, usl, meta.target,
            {"title": L("fig.run.title"), "x": L("fig.run.x"), "y": L("fig.run.y"), "target": L("fig.target"),
             "used": L("fig.run.used"), "invalid": L("fig.run.invalid"), "unused": L("fig.run.unused")},
        ),
        "prob": svg.probability_plot(
            x, mean, sd, lsl, usl,
            {"title": L("fig.prob.title"), "x": L("fig.prob.x"), "y": L("fig.prob.y"), "data": L("fig.prob.data"),
             "fit": fit_prob},
            dist=outcome.dist,
        ),
    }
    if stage != "machine":
        kind = chart["kind"]
        loc_series = L("fig.series.individual" if kind == "imr" else "fig.series.mean")
        var_series = L({"xbar-s": "fig.series.s", "xbar-r": "fig.series.r", "imr": "fig.series.mr"}[kind])
        common = {"x": L("fig.x"), "y": L("fig.y"), "ucl": L("fig.ucl"), "cl": L("fig.cl"), "lcl": L("fig.lcl"),
                  "point": L("fig.point"), "alarm": L("fig.alarm")}
        figures["control_location"] = svg.control_chart(chart["location"], {**common, "title": L("fig.loc.title", series=loc_series)})
        figures["control_variation"] = svg.control_chart(chart["variation"], {**common, "title": L("fig.var.title", series=var_series)})

    # ---- element 20: conclusions as sentences
    conclusions: list[str] = []
    reason_key = {"machine": "v.reason_machine", "preliminary": "v.reason_preliminary"}.get(
        stage, "v.reason_stable" if stability["class"] in ("statistical_control", "in_control") else "v.reason_not_proven"
    )
    conclusions.append(L("c.names", p=name_p, pk=name_pk, reason=L(reason_key)))
    if blk:
        conclusions.append(L("c.fitted", name=L("v.dist_" + blk["name"]), method=request.method,
                             how=L("v.how_auto" if blk["requested"] == "auto" else "v.how_manual")))
    tg = r["targets"]
    verdicts: list[str] = []
    if tg is None:
        conclusions.append(L("c.no_target"))
    elif tg.get("blocked"):
        conclusions.append(L("c.target_blocked", base=tg["n_base"]))
    else:
        for nm, est, ci, target, verdict in (
            (name_p, ix["p"], ix["ci_p"], tg["p"], tg["verdict_p"]),
            (name_pk, ix["pk"], ix["ci_pk"], tg["pk"], tg["verdict_pk"]),
        ):
            if est is None or verdict is None:
                continue
            verdicts.append(verdict)
            key = {"meets": "c.met", "meets_estimate_only": "c.met_estimate", "meets_no_interval": "c.met_no_interval",
                   "fails": "c.fails"}[verdict]
            conclusions.append(L(key, name=nm, value=f"{est:.2f}", target=f"{target:.2f}", level=level,
                                 lower=f"{ci[0]:.2f}" if ci else "–"))
        if verdicts:
            conclusions.append(L("c.overall_met" if all(v == "meets" for v in verdicts) else "c.overall_not_met"))
    if stage == "machine":
        conclusions.append(L("c.stability_not_tested"))
    else:
        cls = stability["class"]
        conclusions.append(L(f"c.stability_{cls}", model=request.model or ""))
        if stability["n_alarm_points"]:
            conclusions.append(L("c.alarms", n=stability["n_alarm_points"]))
    if blk and ix["ci_pk"] is None:
        conclusions.append(L("c.no_interval"))
    nm_ = r["normality"]
    if not blk and nm_ and nm_["p_value"] is not None and nm_["p_value"] < 0.05:
        conclusions.append(L("c.not_normal", p=f"{nm_['p_value']:.4f}"))
    if "sample_below_base" in warn_codes:
        conclusions.append(L("c.sample_small", n=warn_codes["sample_below_base"]["n"], base=warn_codes["sample_below_base"]["base"]))
    if r["counts"]["n_invalid"]:
        conclusions.append(L("c.marked", n=r["counts"]["n_invalid"]))
    if "dropped_subgroups" in warn_codes:
        w = warn_codes["dropped_subgroups"]
        conclusions.append(L("c.dropped", count=w["count"], labels=", ".join(w["labels"])))
    if ix["p"] is None:
        conclusions.append(L("c.one_sided"))

    # ---- annexes
    marked = [
        {"source_row": int(dataset.source_rows[p]), "value": float(dataset.values[p]), "reason": rs, "by": by, "at": at}
        for p, (rs, by, at) in dataset.invalid_info().items()
    ]
    trace = {
        "engine_version": __version__, "fingerprint": r["params"]["fingerprint"], "edition": r["params"]["edition"],
        "stability_mode": r["params"]["stability_mode"], "customer": r["params"].get("customer"),
        "source": r["source"],
    }
    return Report(lang, meta, request, r, facts, figures, conclusions, marked, trace, generated_at, report_id, archive_digest)

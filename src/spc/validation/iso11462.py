"""ISO/TR 11462-3:2020: the eleven reference data sets for the validation of SPC software, run against this program.

The data sets (Annex A), the sample statistics, the control limits, the out of control situations and the capability indices of the
standard are read from the PDF of the standard by tools/extract_iso11462_3.py into data/iso_tr_11462_3.json. The numbers are kept as
printed; the number of printed decimals gives the tolerance (half a unit of the last digit). The standard computes its limits with the
tabulated factors of ISO 7870-2, so the analysis runs with limit_method="iso7870"; the exact limits of the draft are the default of the program.

What is compared, for every data set 1 to 10: the estimators of ISO 22514-2 (l = 1..4, d = 2..5) and the statistics of ISO 7870-2, the limits
of six control charts, the points that violate the limits and the runs on one side of the centre line, and the capability indices of method
M(3,5). Data set 11 (ISO 22514-8): the Grubbs, Bartlett and Fisher tests and the Type 1 capability.

A difference is never hidden. When the printed result contradicts the printed data of the standard itself, the check is marked `known` and
carries the evidence that was computed from the data (for example: the printed point 29 has R = 3,36, below the printed limit 3,7739).
Results of methods that this program does not have under the name of the standard (Pearson and Johnson transformations, extended normal,
multimodal) are listed as information and not judged.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from spc.core import estimators as E
from spc.core import multistate as ms
from spc.data import Dataset
from spc.service import AnalysisRequest, analyze
from spc.validation.checks import Check

DATA_FILE = Path(__file__).with_name("data") / "iso_tr_11462_3.json"
CASE_PREFIX = "ISO/TR 11462-3 example "
SOURCE = "ISO/TR 11462-3:2020, example {n}"
RUN_LENGTH = 7
LIMIT_REL = 1e-3  # the standard: the exact factors instead of the tabulated ones change a limit by less than 0,1 %
MEDIAN_REL = 3e-3  # the tabulated factor of the median chart has three digits: for n = 6 that is 0,27 %
STAT_CENTER = {"xbar": "l3", "individuals": "l1", "median": "l4", "s": "sbar", "R": "Rbar", "mr": "Rm"}
NAMES = {"xbar": "x̄ chart", "individuals": "individuals chart", "median": "median chart", "s": "s chart", "R": "R chart", "mr": "moving range chart"}
# which engine chart and which part of it stands for a chart of the standard
CHART_MAP = (("xbar-s", (("location", "xbar"), ("variation", "s"))), ("xbar-r", (("variation", "R"),)),
             ("median-r", (("location", "median"),)), ("imr", (("location", "individuals"), ("variation", "mr"))))
WEIBULL_SETS = ("2", "9", "10")  # the standard's d = 1 for these comes from a fitted Weibull distribution, not from the normal one


@lru_cache(maxsize=1)
def data() -> dict | None:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except OSError:
        return None


def case_name(number: int) -> str:
    return f"{CASE_PREFIX}{number}"


def catalogue() -> list[dict]:
    d = data()
    if d is None:
        return []
    out = [{"number": int(k), "model": s["model"], "distribution": s["distribution"], "n": s["n"], "subgroup_size": s["subgroup_size"],
            "lsl": float(s["lsl"]), "usl": float(s["usl"]), "decimals": s["decimals"]} for k, s in d["sets"].items()]
    s11 = d["set11"]
    out.append({"number": 11, "model": s11["model"], "distribution": s11["distribution"], "n": 30, "subgroup_size": None, "lsl": float(s11["lsl"]), "usl": float(s11["usl"]),
                "decimals": s11["decimals"]})
    return out


# ------------------------------------------------------------------------------------------------ helpers

def dp(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


def half_unit(text: str) -> float:
    return 0.5 * 10.0 ** (-dp(text))


def _check(n: int, key: str, req: str, ref: str, expected, actual, text: str | None = None, extra_tol: float = 0.0, level: str = "must", note: str = "") -> Check:
    """expected is the printed number (as text, or a float with `text` giving its printed form)."""
    e = float(expected)
    tol = (half_unit(text if text is not None else str(expected)) if (text or isinstance(expected, str)) else 0.0) + extra_tol
    c = Check(f"I{n:02d}-{key}", f"iso11462.{n}", req, f"ISO/TR 11462-3:2020, {ref}", e, actual, None, note, level=level)
    return c.with_abs(tol + 1e-12 * abs(e))


def _matrix(values: list[float], n: int) -> np.ndarray:
    return np.asarray(values, dtype=float).reshape(-1, n)


def _analysis(st: dict, chart: str, **over):
    x = [float(v) for v in st["values"]]
    n = st["subgroup_size"]
    ds = Dataset.from_values(x, subgroup=None if chart == "imr" else [str(i // n + 1) for i in range(len(x))])
    req = AnalysisRequest(stage="production", chart=chart, subgroup_size=1 if chart == "imr" else None, lsl=float(st["lsl"]), usl=float(st["usl"]),
                          limit_method="iso7870", bootstrap_n=0, rules={"beyond_limits": True, "run_length": RUN_LENGTH}, **over)
    return analyze(ds, req)


def _beyond(value: float, lcl: float, ucl: float, has_lcl: bool = True) -> bool:
    return value > ucl or (has_lcl and value < lcl)


def _extents(part: dict, flagged: list[int]) -> set[tuple[int, int]]:
    """The stretches on one side of the centre line that hold a point which the program flagged as a run."""
    v = np.asarray(part["values"], dtype=float)
    c = part["center"]
    c = np.broadcast_to(c, v.shape) if np.ndim(c) == 0 else np.asarray(c, dtype=float)
    side = np.sign(v - c)
    out, seen = set(), set()
    for i in flagged:
        if i in seen:
            continue
        a = b = i
        while a > 0 and side[a - 1] == side[i]:
            a -= 1
        while b < len(v) - 1 and side[b + 1] == side[i]:
            b += 1
        seen.update(range(a, b + 1))
        out.add((a, b))
    return out


# ------------------------------------------------------------------------------------------------ one data set

def _stats(n: int, st: dict) -> list[Check]:
    m = _matrix([float(v) for v in st["values"]], st["subgroup_size"])
    got = E.estimators(m)
    out = []
    for key, text in st["stats"].items():
        if key == "d1" and str(n) in WEIBULL_SETS:
            continue
        # d = 3 and d = 4 use c4 and d2, which the standard takes from tables with three or four digits
        out.append(_check(n, f"stat-{key}", "req.iso_stat", "sample statistics table", text, got[key], text, 5e-4 * abs(got[key]) if key in ("d3", "d4") else 0.0, note=key))
    return out


def _limits_and_signals(n: int, st: dict) -> list[Check]:
    out: list[Check] = []
    stat = {k: float(v) for k, v in st["stats"].items()}
    for engine_chart, parts in CHART_MAP:
        try:
            chart = _analysis(st, engine_chart)["chart"]
        except ValueError as exc:
            out.append(Check(f"I{n:02d}-{engine_chart}-error", f"iso11462.{n}", "req.runs", "the analysis must run", "ran", "error", None, str(exc)))
            continue
        for part, name in parts:
            ch = st["charts"].get(name)
            if not ch or not ch["limits"]:
                continue  # the standard gives no limits: for example the individuals chart of a non-normal set
            lcl_t, ucl_t = ch["limits"][0]
            e = chart[part]
            rel = MEDIAN_REL if name == "median" else LIMIT_REL
            width = abs(e["ucl"] - e["center"])
            for side, text, got in (("lcl", lcl_t, e["lcl"]), ("ucl", ucl_t, e["ucl"])):
                lvl, note = "must", NAMES[name]
                c = _check(n, f"{name}-{side}", "req.iso_limit", f"limits of the {NAMES[name]}", text, got, text, rel * width)
                if not c.ok and abs(abs(float(text)) - abs(got)) <= c.abs_tol:
                    c = _check(n, f"{name}-{side}", "req.iso_limit", f"limits of the {NAMES[name]}", text, got, text, rel * width, "known",
                               f"{NAMES[name]}: the standard prints {text}, the sign is missing: the limit is {got:.6g} (centre line {e['center']:.6g}, width {width:.6g})")
                else:
                    c.note = note
                out.append(c)
            out += _signals(n, name, ch, chart[part], stat)
    return out


def _signals(n: int, name: str, ch: dict, part: dict, stat: dict) -> list[Check]:
    labels = [int(x) for x in part["labels"]]
    values = {lab: v for lab, v in zip(labels, part["values"])}
    lcl_t, ucl_t = (float(x) for x in ch["limits"][0])
    centre = stat[STAT_CENTER[name]]
    has_lcl = not (lcl_t == 0.0 and name in ("s", "R", "mr"))
    std_lim = {i for v in ch["violations"] if "violation" in v["kind"] for i in range(v["from"], v["to"] + 1)}
    std_runs = {(v["from"], v["to"]) for v in ch["violations"] if "run" in v["kind"]}
    flagged_lim = {labels[a["index"]] for a in part["alarms"] if a["rule"] == "beyond_limits"}
    mine_runs = {(labels[a], labels[b]) for a, b in _extents(part, [a["index"] for a in part["alarms"] if a["rule"] != "beyond_limits"])}
    out = []
    # points beyond the limits
    only_std, only_eng = sorted(std_lim - flagged_lim), sorted(flagged_lim - std_lim)
    evidence = []
    for i in only_std:
        if not _beyond(values[i], lcl_t, ucl_t, has_lcl):
            evidence.append(f"printed point {i} has the value {values[i]:.6g}, inside the printed limits {lcl_t:g} to {ucl_t:g}")
    for i in only_eng:
        if _beyond(values[i], lcl_t, ucl_t, has_lcl):
            evidence.append(f"point {i} ({values[i]:.6g}) is beyond the printed limits {lcl_t:g} to {ucl_t:g} but is not listed")
    explained = len(evidence) == len(only_std) + len(only_eng)
    lvl = "must" if not (only_std or only_eng) else "known" if explained else "must"
    out.append(Check(f"I{n:02d}-{name}-limit-points", f"iso11462.{n}", "req.iso_viol_limits", f"out of control situations of the {NAMES[name]}", sorted(std_lim), sorted(flagged_lim), None,
                     "; ".join(evidence)[:600] if evidence else "", level=lvl))
    # runs on one side of the centre line. The standard lists runs of 7 and more points; for the moving range chart it omits most runs of exactly 7
    minlen = RUN_LENGTH

    def real(a, b):
        sides = {np.sign(values[i] - centre) for i in range(a, b + 1) if i in values}
        return len(sides) == 1 and 0 not in sides and all(i in values for i in range(a, b + 1))

    ev2, std_only, eng_only = [], sorted(std_runs - mine_runs), sorted(mine_runs - std_runs)
    for a, b in std_only:
        if not real(a, b) or b - a + 1 < minlen:
            ev2.append(f"the printed run {a} to {b} is not on one side of the centre line {centre:g} in the data of the standard")
    for a, b in eng_only:
        if real(a, b) and b - a + 1 >= minlen:
            ev2.append(f"the run {a} to {b} ({b - a + 1} points) is on one side of the centre line {centre:g} and is not listed")
    lvl2 = "must" if not (std_only or eng_only) else "known" if len(ev2) == len(std_only) + len(eng_only) else "must"
    out.append(Check(f"I{n:02d}-{name}-runs", f"iso11462.{n}", "req.iso_viol_runs", f"out of control situations of the {NAMES[name]}", [list(x) for x in sorted(std_runs)],
                     [list(x) for x in sorted(mine_runs)], None, "; ".join(ev2)[:600] if ev2 else "", level=lvl2))
    return out


def _capability(n: int, st: dict) -> list[Check]:
    out: list[Check] = []
    block = next((c for c in st["capability"] if c["method"] == "M3,5" and c["indices"]), None)
    if not block:
        return out
    exp = block["indices"][0]
    res = _analysis(st, "xbar-s", distribution="normal")["indices"]
    got = {"p": res["p"], "pk": res["pk"], "pl": res["pl"], "pu": res["pu"]}
    mism = {k: (exp[k], round(got[k], 2)) for k in got if exp[k] is not None and abs(float(exp[k]) - got[k]) > 0.005 + 1e-9}
    level, note = "must", ""
    if mism and n == 5:
        alt = _analysis({**st, "usl": "5"}, "xbar-s", distribution="normal")["indices"]
        if all(abs(float(exp[k]) - alt[k]) <= 0.005 + 1e-9 for k in ("p", "pk", "pu")):
            level, note = "known", ("the printed indices Pp 1,04, Ppk 0,92 and PpkU 0,92 are those of the upper limit 5, not of 7,5 that the description of the data set prints "
                                     f"(with 7,5: Pp {got['p']:.2f}, PpkU {got['pu']:.2f}); the printed PpkL 1,17 does not depend on it")
    if mism and n == 6:
        if abs(float(exp["pl"]) - got["pu"]) <= 0.005 + 1e-9 and abs(float(exp["pu"]) - got["pl"]) <= 0.005 + 1e-9:
            level, note = "known", (f"the printed PpkL {exp['pl']} and PpkU {exp['pu']} are exchanged: the mean {float(np.mean([float(v) for v in st['values']])):.4f} is below the "
                                     "centre of the limits, so the lower index must be the smaller one")
    for key in ("p", "pk", "pl", "pu"):
        if exp[key] is None:
            continue
        lv = level if key in mism else "must"
        out.append(_check(n, f"cap-{key}", "req.iso_capability", "process capability, method M(3,5)", exp[key], got[key], exp[key], level=lv, note=note if key in mism else ""))
    return out


def _information(n: int, st: dict) -> list[Check]:
    """The method M(3,1) of the standard for the distributions that this program has under that name. Not judged."""
    out: list[Check] = []
    block = next((c for c in st["capability"] if c["method"] == "M3,1" and c["indices"]), None)
    if not block:
        return out
    exp = block["indices"][0]
    fit = {"2": "weibull", "9": "weibull", "10": "weibull"}.get(str(n))
    if not fit or exp["pk"] is None:
        return out
    # the standard treats the lower limit 0 of these sets as a natural limit: one-sided capability with the upper limit only
    one_sided = str(n) in ("2", "9")
    d = Dataset.from_values([float(v) for v in st["values"]], subgroup=[str(i // st["subgroup_size"] + 1) for i in range(st["n"])])
    res = analyze(d, AnalysisRequest(stage="production", lsl=None if one_sided else float(st["lsl"]), usl=float(st["usl"]), distribution=fit, method="G", bootstrap_n=0))["indices"]
    out.append(_check(n, "cap31-pk", "req.iso_info", "process capability, method M(3,1), Weibull", exp["pk"], res["pk"], exp["pk"], level="info",
                      note="Weibull quantile method of this program; the standard's location and quantile conventions are not stated in the sources at hand"))
    return out


def example(n: int) -> list[Check]:
    d = data()
    st = d["sets"][str(n)]
    return _stats(n, st) + _limits_and_signals(n, st) + _capability(n, st) + _information(n, st)


def example11() -> list[Check]:
    d = data()["set11"]
    n = 11
    states = {k: np.array([float(v) for v in vals]) for k, vals in d["values"].items()}
    t = ms.state_tests(states)
    g = list(states.values())
    out = []
    for st in "PIC":
        out.append(_check(n, f"mean-{st}", "req.iso_state", "5.11.2.1, sample statistics", d["stats"][f"mean_{st}"], float(states[st].mean()), d["stats"][f"mean_{st}"]))
        out.append(_check(n, f"s-{st}", "req.iso_state", "5.11.2.1, sample statistics", d["stats"][f"s_{st}"], float(states[st].std(ddof=1)), d["stats"][f"s_{st}"]))
        out.append(_check(n, f"grubbs-{st}", "req.iso_state", "5.11.2.2, Grubbs test", d["grubbs"][st], t["states"][st]["grubbs"]["g"], d["grubbs"][st]))
    pooled = float(np.sqrt(np.mean([v.var(ddof=1) for v in g])))
    out.append(_check(n, "s-pooled", "req.iso_state", "5.11.2.1, sample statistics (d = 2)", d["stats"]["s_pooled"], pooled, d["stats"]["s_pooled"]))
    out.append(_check(n, "grubbs-crit-10", "req.iso_state", "5.11.2.2, Grubbs test, critical value", d["grubbs"]["critical_10"], ms.grubbs_critical(10), d["grubbs"]["critical_10"]))
    out.append(_check(n, "grubbs-crit-30", "req.iso_state", "5.11.2.2, Grubbs test, critical value", d["grubbs"]["critical_30"], ms.grubbs_critical(30), d["grubbs"]["critical_30"]))
    allv = np.concatenate(g)
    out.append(_check(n, "grubbs-30", "req.iso_state", "5.11.2.2, Grubbs test", d["grubbs"]["g_30"], ms.grubbs(allv)["g"], d["grubbs"]["g_30"]))
    out.append(_check(n, "bartlett", "req.iso_state", "5.11.2.3, Bartlett test", d["bartlett"]["statistic"], t["bartlett"]["statistic"], d["bartlett"]["statistic"]))
    out.append(_check(n, "bartlett-p", "req.iso_state", "5.11.2.3, Bartlett test", d["bartlett"]["p"], t["bartlett"]["p"], d["bartlett"]["p"]))
    from scipy import stats as sps

    out.append(_check(n, "bartlett-crit", "req.iso_state", "5.11.2.3, Bartlett test, critical value", d["bartlett"]["critical"], float(sps.chi2.ppf(0.95, 2)), d["bartlett"]["critical"]))
    out.append(_check(n, "fisher", "req.iso_state", "5.11.2.4, Fisher test", d["fisher"]["statistic"], t["fisher"]["statistic"], d["fisher"]["statistic"]))
    out.append(_check(n, "fisher-crit", "req.iso_state", "5.11.2.4, Fisher test, critical value", d["fisher"]["critical"], float(sps.f.ppf(0.95, 2, 27)), d["fisher"]["critical"]))
    out.append(Check("I11-fisher-conclusion", "iso11462.11", "req.iso_state", "ISO/TR 11462-3:2020, 5.11.2.4, Fisher test, conclusion", "no difference in locations", "difference in locations" if t["location_differs"] else "no difference in locations",
                     None, f"the standard prints F = {d['fisher']['statistic']} against the critical value {d['fisher']['critical']} and concludes that no difference was detected; "
                     f"F is far above the critical value (p = {t['fisher']['p']:.2e}), the states differ in location. The standard's conclusion contradicts its own numbers", level="known"))
    cap = ms.type1_capability(states, float(d["lsl"]), float(d["usl"]))
    c = d["capability"]
    note_pm = ""
    pm = _check(n, "pm", "req.iso_capability", "5.11.2.5, capability Type 1 (ISO 22514-8)", c["pm"], cap["pm"], c["pm"],
                level="known", note=f"printed Pm {c['pm']}; the formula (U - L - range of the state means) / (6 s pooled) gives {cap['pm']:.4f} (the text of ISO 22514-8 is not at hand: the formula is assumed, and the three Pmk values below confirm its parts)")
    out.append(pm)
    out.append(_check(n, "pmk", "req.iso_capability", "5.11.2.5, capability Type 1 (ISO 22514-8)", c["pmk"], cap["pmk"], c["pmk"]))
    out.append(_check(n, "pmk-l", "req.iso_capability", "5.11.2.5, capability Type 1 (ISO 22514-8)", c["pmk_l"], cap["pmk_l"], c["pmk_l"]))
    out.append(_check(n, "pmk-u", "req.iso_capability", "5.11.2.5, capability Type 1 (ISO 22514-8)", c["pmk_u"], cap["pmk_u"], c["pmk_u"]))
    return out


def scenarios() -> list[Check]:
    if data() is None:
        return [Check("I00-data", "iso11462.0", "req.runs", "the data file of the standard must be present", "present", "missing", None,
                      "src/spc/validation/data/iso_tr_11462_3.json is made by tools/extract_iso11462_3.py from the PDF of the standard")]
    out: list[Check] = []
    for k in range(1, 11):
        out += example(k)
    out += example11()
    return out

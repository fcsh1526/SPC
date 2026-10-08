"""VDA Volume 5, "Capability of Measurement Processes" (English edition of 2011): the worked examples and tables run against the program.

The data are typed in from the guideline (Table 8: 50 readings on a standard; Table 9: linearity of 6 standards; Table 13: 10 parts, 3 operators, 2 readings; Table 6). The
numbers are compared to the digits that the guideline prints. Two readings of the scanned tables were settled by the printed results: the 4.978 of Table 9 (standard 3, trial 1) is the only
single-digit reading that gives the printed regression function and the printed u_LIN and u_EVR, and the Table 13 data give the printed u_EVO and u_AV only with the interaction pooled
(F = 0.055 > 0.05).

The measurement process of VDA 5 and ISO 22514-7 is the same model; the Bowker test, the uncertainty range of the signal detection method and Table 6 (Table 10 there) are in the
checks of ISO 22514-7.
"""

from __future__ import annotations

import numpy as np

from spc.core import iso22514_7 as iso
from spc.core import msa
from spc.validation.checks import Check

REF = "VDA 5, "
T8 = [6.001, 6.002, 6.001, 6.001, 6.002, 6.001, 6.001, 6.000, 5.999, 6.001, 6.001, 6.000, 6.001, 6.002, 6.002, 6.002, 6.002, 6.002, 6.002, 6.000, 6.002, 6.000, 5.999, 6.002, 6.002,
      6.001, 6.001, 6.000, 5.999, 5.999, 6.000, 6.001, 6.001, 6.002, 6.001, 6.001, 6.000, 6.000, 5.999, 5.999, 6.000, 6.001, 6.002, 6.001, 6.002, 6.002, 6.001, 6.002, 6.001, 6.001]
T9_X = [0.0, 5.0, 10.0, 15.0, 20.0, 30.0]
T9_Y = [[-1.113, 1.324, -2.482, 1.673, -1.876], [1.345, 3.126, 2.123, 2.587, 0.457], [4.978, 4.083, 6.935, 5.257, 5.996], [14.746, 15.932, 17.958, 18.515, 19.359],
        [20.816, 21.869, 23.095, 24.224, 22.529], [21.843, 21.177, 24.334, 23.547, 24.420]]
# Table 13: one line per part, operator A (2 readings), B, C
T13 = [[6.029, 6.030, 6.033, 6.032, 6.031, 6.030], [6.019, 6.020, 6.020, 6.019, 6.020, 6.020], [6.004, 6.003, 6.007, 6.007, 6.010, 6.006], [5.982, 5.982, 5.985, 5.986, 5.984, 5.984],
       [6.009, 6.009, 6.014, 6.014, 6.015, 6.014], [5.971, 5.972, 5.973, 5.972, 5.975, 5.974], [5.995, 5.997, 5.997, 5.996, 5.995, 5.994], [6.014, 6.018, 6.019, 6.015, 6.016, 6.015],
       [5.985, 5.987, 5.987, 5.986, 5.987, 5.986], [6.024, 6.028, 6.029, 6.025, 6.026, 6.025]]


def _dp(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


def c(key: str, ref: str, printed: str, got: float, extra: float = 0.0, note: str = "", level: str = "must") -> Check:
    e = float(printed)
    chk = Check(f"vda5-{key}", "vda5", "req.agreement", REF + ref, e, float(got), None, note, level=level)
    return chk.with_abs(0.5 * 10 ** -_dp(printed) + extra + 1e-12 * abs(e))


def measuring_system() -> list[Check]:
    """5.2.2.1, Table 8, Figures 11 and 12: one standard of 6.002 mm, tolerance 0.06 mm, resolution 0.001 mm, U_CAL = 0.002 mm with k = 2."""
    r = iso.repeatability_standard(T8, 6.002)
    comp = {"cal": 0.002 / 2.0, "evr": r["u_evr"], "bi": r["u_bi"], "re": iso.u_resolution(0.001)}
    m = iso.combine(comp, 0.06)
    return [c("t8-s", "Figure 11, u_EVR", "0.000995", r["u_evr"]), c("t8-ubi", "Figure 11, u_BI", "0.000635", r["u_bi"]), c("t8-ure", "Figure 11, u_RE", "0.000289", comp["re"]),
            c("t8-ums", "Figure 11, u_MS", "0.00155", m["u_ms"]), c("t8-Ums", "Figure 12, U_MS", "0.00309", m["U_ms"]), c("t8-qms", "Figure 12, Q_MS", "10.31", m["q_ms"]),
            c("t8-tol", "Figure 12, minimum tolerance", "0.0413", 2.0 * m["U_ms"] / 0.15, extra=1e-4, note="the text of the guideline rounds to 0.042; the program gives 0.04125")]


def linearity() -> list[Check]:
    """5.2.2.2, Table 9, Figures 17 and 18: six standards, five readings, u_CAL = 0.05, resolution 0.001, tolerance 30."""
    lin = iso.linearity(T9_X, T9_Y)
    m = iso.combine({"cal": 0.05, "lin": lin["u_lin"], "evr": lin["u_evr"], "re": iso.u_resolution(0.001)}, 30.0)
    return [c("t9-b0", "5.2.2.2, regression function", "-0.6176", lin["beta0"]), c("t9-b1", "5.2.2.2, regression function", "0.9183", lin["beta1"]),
            c("t9-ulin", "Figure 17, u_LIN", "9.266", lin["u_lin"]), c("t9-uevr", "Figure 17, u_EVR", "1.488", lin["u_evr"]), c("t9-ums", "Figure 17, u_MS", "9.385", m["u_ms"]),
            c("t9-Ums", "Figure 18, U_MS", "18.77", m["U_ms"]), c("t9-qms", "Figure 18, Q_MS", "125.13", m["q_ms"], extra=5e-3),
            c("t9-tol", "Figure 18, minimum tolerance", "250.3", 2.0 * m["U_ms"] / 0.15, extra=0.05)]


def process() -> list[Check]:
    """5.3.1, Table 13, Figures 20 and 21: the process with u_BI and u_CAL of the measuring system above."""
    a = np.array(T13).reshape(10, 3, 2).transpose(1, 0, 2)  # operator x part x reading
    r = iso.reproducibility(a.tolist())
    t = {x["source"]: x for x in r["table"]}
    r8 = iso.repeatability_standard(T8, 6.002)
    m = iso.combine({"cal": 0.001, "evr": r8["u_evr"], "bi": r8["u_bi"], "re": iso.u_resolution(0.001), "evo": r["u_evo"], "av": r["u_av"]}, 0.06)
    return [Check("vda5-t13-pooled", "vda5", "req.agreement", REF + "5.3.1, the interaction is not significant and pooled", True, bool(r["pooled"]), None,
                  f"F = {t['interaction']['f']:.3f}, F0 = {t['interaction']['f_crit']:.3f}"),
            c("t13-uav", "Figure 20, u_AV", "0.000932", r["u_av"]), c("t13-uevo", "Figure 20, u_EVO", "0.00153", r["u_evo"]), c("t13-ump", "Figure 20, u_MP", "0.00215", m["u_mp"]),
            c("t13-Ump", "Figure 21, U_MP", "0.00430", m["U_mp"], extra=5e-6), c("t13-qmp", "Figure 21, Q_MP", "14.34", m["q_mp"], extra=0.01), c("t13-tol", "Figure 21, minimum tolerance", "0.0287", 2.0 * m["U_mp"] / 0.30, extra=1e-4)]


def relations() -> list[Check]:
    """4.7 and 5.2.2.1: Cg 1.33 is Q_MS 15 % (4 s); 4.10, Table 6; the budget limits."""
    out = []
    tol = 1.0
    s = 0.0375 * tol  # Q_MS = 4 s / T = 15 %
    x = np.random.default_rng(5).normal(0.0, 1.0, 50)
    x = (x - x.mean()) / x.std(ddof=1) * s
    r = msa.type1(x, 0.0, tol)
    out.append(Check("vda5-cg-q15", "vda5", "req.agreement", REF + "5.2.2.1, Cg of 1.33 corresponds to Q_MS of 15 %", 1.33, r["cg"], None, "s = 0.0375 T, Cg = 0.2 T / (4 s)").with_abs(0.005))
    x6 = (x / s) * 0.025 * tol
    r6 = msa.type1(x6, 0.0, tol, spread=6)
    out.append(Check("vda5-cg-q10", "vda5", "req.agreement", REF + "5.2.2.1, remark: with 6 s Cg of 1.33 corresponds to Q_MS of 10 %", 1.33, r6["cg"], None, "s = 0.025 T, Cg = 0.2 T / (6 s)").with_abs(0.005))
    table = {0.67: (0.67, 0.68, 0.70, 0.73, 0.77), 1.00: (1.01, 1.05, 1.12, 1.25, 1.51), 1.33: (1.36, 1.45, 1.66, 2.21, 18.82), 1.67: (1.72, 1.93, 2.53), 2.00: (2.10, 2.50, 4.59)}
    for c_obs, row in table.items():
        for q, printed in zip((0.1, 0.2, 0.3, 0.4, 0.5), row):
            got = iso.real_capability(c_obs, q)
            out.append(Check(f"vda5-t6-{c_obs}-{int(q * 100)}", "vda5", "req.agreement", REF + "4.10, Table 6", printed, got, None, f"observed {c_obs}, Q_MP {int(q * 100)} %").with_abs(0.0051))
    k = iso.classification(0.5, 1.0)
    out += [Check("vda5-class-limit", "vda5", "req.agreement", REF + "8.2, U_MP / KB <= 0.5 keeps a part in two adjacent classes at most", (True, 2), (k["ok"], k["adjacent_max"]), None),
            Check("vda5-class-over", "vda5", "req.agreement", REF + "8.2, 2 U_MP / KB + 1 adjacent classes", (False, 3), (iso.classification(0.8, 1.0)["ok"], iso.classification(0.8, 1.0)["adjacent_max"]), None)]
    out.append(Check("vda5-limits", "vda5", "req.agreement", REF + "4.7, recommended limits Q_MS_max 15 % and Q_MP_max 30 %", (15.0, 30.0), (iso.Q_MS_MAX, iso.Q_MP_MAX), None))
    return out


def scenarios() -> list[Check]:
    return measuring_system() + linearity() + process() + relations()

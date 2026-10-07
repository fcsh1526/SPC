"""ISO 22514-7:2012, clause 7 to 12 and annex A: the worked examples and tables of the standard run against spc.core.iso22514_7.

The data are typed in from the standard (tables 7, A.1, A.4, 12 and figure 6); the numbers are compared to the number of decimals that the standard prints. Where the printed numbers
of the standard disagree with its own formulas or data, the check is `known`, with the evidence in the note.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

from spc.core import iso22514_7 as iso
from spc.validation.checks import Check

REF = "ISO 22514-7:2012, "
T7_X = [2.0, 4.0, 6.0, 8.0, 10.0]
T7_Y = [[2.7, 2.5, 2.4, 2.5, 2.7, 2.3, 2.5, 2.5, 2.4, 2.4, 2.6, 2.4], [5.1, 3.9, 4.2, 5.0, 3.8, 3.9, 3.9, 3.9, 3.9, 4.0, 4.1, 3.8],
        [5.8, 5.7, 5.9, 5.9, 6.0, 6.1, 6.0, 6.1, 6.4, 6.3, 6.0, 6.1], [7.6, 7.7, 7.8, 7.7, 7.8, 7.8, 7.8, 7.7, 7.8, 7.5, 7.6, 7.7],
        [9.1, 9.3, 9.5, 9.3, 9.4, 9.5, 9.5, 9.5, 9.6, 9.2, 9.3, 9.4]]
A1_X = [6.19, 9.17, 1.99, 7.77, 4.00, 10.77, 4.78, 2.99, 6.98, 9.98]
A1_Y = [[6.31, 6.27, 6.31, 6.28], [9.27, 9.21, 9.34, 9.23], [2.21, 2.19, 2.22, 2.20], [8.00, 7.81, 7.95, 7.84], [4.27, 4.15, 4.15, 4.15], [10.93, 10.73, 10.92, 10.89],
        [4.95, 4.87, 5.00, 5.00], [3.24, 3.17, 3.21, 3.21], [7.14, 7.07, 7.18, 7.20], [10.23, 10.02, 10.07, 10.17]]
A4 = [[[8.120, 8.435, 8.480], [7.445, 6.815, 7.490], [9.965, 10.010, 9.560], [6.140, 5.960, 6.365], [5.690, 5.600, 5.780], [2.855, 2.450, 2.585], [10.685, 10.595, 10.775],
       [6.725, 6.275, 6.545], [4.970, 5.105, 5.510], [9.875, 10.100, 9.875]],
      [[8.200, 8.290, 8.245], [7.300, 7.120, 7.075], [9.660, 9.340, 9.250], [6.095, 6.185, 6.185], [5.080, 5.340, 5.440], [2.315, 2.585, 2.315], [10.450, 10.840, 11.050],
       [6.240, 6.120, 6.300], [5.015, 5.285, 5.150], [10.080, 9.800, 9.970]],
      [[8.525, 8.435, 8.345], [7.535, 7.355, 7.085], [9.830, 9.695, 9.515], [6.140, 6.140, 6.050], [5.780, 5.735, 5.555], [2.630, 2.360, 2.585], [10.865, 11.000, 11.180],
       [6.590, 6.500, 6.725], [5.060, 5.195, 5.105], [10.190, 9.785, 9.965]]]
FIG6 = [0.599581, 0.587893, 0.576459, 0.570360, 0.566575, 0.566152, 0.561457, 0.559918, 0.547204, 0.545804, 0.544951, 0.543077, 0.542704, 0.531939, 0.529065, 0.523754, 0.521642,
        0.520469, 0.519694, 0.517377, 0.515573, 0.514192, 0.513779, 0.509015, 0.505850, 0.503091, 0.502436, 0.502295, 0.501132, 0.496696, 0.493441, 0.488905, 0.488184, 0.487613,
        0.486379, 0.484167, 0.483803, 0.477236, 0.476901, 0.470832, 0.465454, 0.462410, 0.454510, 0.452310, 0.449696, 0.446697, 0.437810, 0.427000, 0.424530, 0.409238]


def _dp(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


def c(area: str, key: str, ref: str, printed: str, got: float, req: str, level: str = "must", note: str = "", extra: float = 0.0) -> Check:
    e = float(printed)
    chk = Check(f"{area}-{key}", f"iso22514_7.{area}", req, REF + ref, e, got, None, note, level=level)
    return chk.with_abs(0.5 * 10 ** -_dp(printed) + extra + 1e-12 * abs(e))


def linearity() -> list[Check]:
    r8 = iso.linearity_deviation(T7_X, T7_Y)
    out = [c("lin", "t8-b0", "7.1.3.4, table 8", "0.7367", r8["intercept"], "req.iso7_lin"), c("lin", "t8-b1", "7.1.3.4, table 8", "-0.1317", r8["slope"], "req.iso7_lin"),
           c("lin", "t8-lin", "7.1.3.4, table 8, linearity at the upper limit", "0.58", r8["linearity"], "req.iso7_lin")]
    a = iso.linearity(A1_X, A1_Y)
    for key, printed, got, where in (("xbar", "6.462", a["x_mean"], "A.1.2"), ("ybar", "6.614", a["y_mean"], "A.1.2"), ("b0", "0.2358", a["beta0"], "A.1.2"), ("b1", "0.9870", a["beta1"], "A.1.2"),
                                     ("sse", "0.1462", a["ss_e"], "table A.3"), ("ssevr", "0.1235", a["ss_evr"], "table A.3"), ("sslin", "0.0228", a["ss_lin"], "table A.3"),
                                     ("mslin", "0.0028", a["ms_lin"], "table A.3"), ("msevr", "0.0041", a["ms_evr"], "table A.3"), ("ulin", "0.0533", a["u_lin"], "table A.3"),
                                     ("uevr", "0.0641", a["u_evr"], "table A.3"), ("f", "0.6918", a["f"], "table A.3"), ("f0", "2.2661", a["f_crit"], "table A.3")):
        out.append(c("lin", f"a1-{key}", where, printed, got, "req.iso7_lin", extra={"ulin": 1e-4, "f0": 5e-5}.get(key, 0.0)))
    return out


def process() -> list[Check]:
    r = iso.reproducibility(A4)
    t = {x["source"]: x for x in r["table"]}
    out = []
    for key, printed, got in (("ss-op", "0.519", t["operator"]["ss"]), ("ss-pv", "526.9", t["part"]["ss"]), ("ss-ia", "0.686", t["interaction"]["ss"]), ("ss-e", "1.917", t["error"]["ss"]),
                              ("ms-op", "0.260", t["operator"]["ms"]), ("ms-pv", "58.54", t["part"]["ms"]), ("ms-ia", "0.0381", t["interaction"]["ms"]), ("ms-e", "0.0320", t["error"]["ms"]),
                              ("var-pv", "6.501", t["part"]["variance"]), ("f-ia", "1.193", t["interaction"]["f"]), ("f0-ia", "1.778", t["interaction"]["f_crit"]),
                              ("ms-pool", "0.0334", r["ms_pool"]), ("u-av", "0.08683", r["u_av"]), ("u-evo", "0.1827", r["u_evo"]), ("f-op-pooled", "7.776", t["operator"]["f"]),
                              ("f-pv-pooled", "1754", t["part"]["f"])):
        out.append(c("proc", key, "tables A.5 and A.6", printed, got, "req.iso7_anova", extra=0.5 * 10 ** -(_dp(printed)) if key in ("ss-pv", "f-pv-pooled", "ms-pv") else 1e-5 if key == "u-av" else 0.0))
    full = iso.reproducibility(A4, alpha=0.4)
    ft = {x["source"]: x for x in full["table"]}
    out += [c("proc", "var-pv-unpooled", "table A.5", "6.500", ft["part"]["variance"], "req.iso7_anova"), c("proc", "var-op", "table A.5", "0.00738", ft["operator"]["variance"], "req.iso7_anova", extra=2e-5), c("proc", "var-ia", "table A.5", "0.00205", ft["interaction"]["variance"], "req.iso7_anova", extra=2e-5),
            c("proc", "u-ia", "table A.5", "0.04528", full["u_ia"], "req.iso7_anova", extra=2e-5), c("proc", "u-av-unpooled", "table A.5", "0.08591", full["u_av"], "req.iso7_anova", extra=2e-5)]
    # the critical values of tables A.5 and A.6 use the 60 (78) degrees of freedom of the error, while B.2 says (N_A - 1)(N_P - 1) = 18 for the operator and the part
    out.append(Check("proc-f0-op", "iso22514_7.proc", "req.iso7_anova", REF + "table A.5, critical value of the operator, B.2", 3.150, ft["operator"]["f_crit"], None,
                     f"table A.5 prints F0 = 3.150, which is F(0.95; 2, 60); B.2 gives the degrees of freedom (N_A - 1, (N_A - 1)(N_P - 1)) = (2, 18), F(0.95; 2, 18) = {stats.f.ppf(0.95, 2, 18):.3f}; "
                     f"the formula of B.2 is used", level="known").with_abs(5e-4))
    out.append(Check("proc-f0-pv", "iso22514_7.proc", "req.iso7_anova", REF + "table A.5, critical value of the part, B.2", 2.040, ft["part"]["f_crit"], None,
                     f"table A.5 prints F0 = 2.040 = F(0.95; 9, 60); B.2 gives (9, 18): F = {stats.f.ppf(0.95, 9, 18):.3f}; the formula of B.2 is used", level="known").with_abs(5e-4))
    return out


def combination() -> list[Check]:
    lin, proc = iso.linearity(A1_X, A1_Y), iso.reproducibility(A4)
    comp = {"cal": 0.005, "lin": lin["u_lin"], "bi": 0.0, "evr": lin["u_evr"], "re": iso.u_resolution(5e-3), "evo": proc["u_evo"], "av": proc["u_av"]}
    r = iso.combine(comp, 11 - 2)
    out = [c("comb", "ure", "A.3", "0.00144", comp["re"], "req.iso7_comb", extra=5e-6), c("comb", "ums", "A.4", "0.0836", r["u_ms"], "req.iso7_comb"), c("comb", "Ums", "A.4", "0.1672", r["U_ms"], "req.iso7_comb"),
           c("comb", "ump", "A.4", "0.2093", r["u_mp"], "req.iso7_comb", extra=1e-4, note="printed to four decimals; the components as printed give 0.2092"), c("comb", "Ump", "A.4", "0.4185", r["U_mp"], "req.iso7_comb", extra=1e-4),
           c("comb", "qms", "A.5", "3.7", r["q_ms"], "req.iso7_comb"), c("comb", "qmp", "A.5", "9.3", r["q_mp"], "req.iso7_comb"), c("comb", "cms", "A.5", "5.38", r["c_ms"], "req.iso7_comb"),
           c("comb", "cmp", "A.5", "4.30", r["c_mp"], "req.iso7_comb")]
    cov = iso.coverage_factor(3, 2, 2, 3, n_measurements=12)
    out.append(Check("comb-t24", "iso22514_7.comb", "req.iso7_comb", REF + "8.2, example 1", 2.11, cov["k"], None,
                     f"the standard prints t(24) = 2.11 for nu = 3 x 2 x 2 x (3 - 1) = 24; Student's t(0.975; 24) is {stats.t.ppf(0.975, 24):.3f} (2.11 is the value for 17 degrees of freedom); the quantile is used", level="known").with_abs(5e-3))
    return out


def relation() -> list[Check]:
    table = {0.67: (0.67, 0.68, 0.70, 0.73, 0.77), 1.00: (1.01, 1.05, 1.12, 1.25, 1.51), 1.33: (1.36, 1.45, 1.66, 2.21, 18.82), 1.67: (1.72, 1.93, 2.53), 2.00: (2.10, 2.50, 4.59)}
    out = []
    for c_obs, row in table.items():
        for q, printed in zip((0.1, 0.2, 0.3, 0.4, 0.5), row):
            got = iso.real_capability(c_obs, q)
            out.append(Check(f"rel-t10-{c_obs}-{int(q * 100)}", "iso22514_7.rel", "req.iso7_rel", REF + "table 10 (10.1, B.4)", printed, got, None, f"observed {c_obs}, Q_MP {int(q * 100)} %").with_abs(0.0051))
    out += [Check(f"rel-na-{c_obs}-{int(q * 100)}", "iso22514_7.rel", "req.iso7_rel", REF + "table 10 (10.1, B.4), printed 'na'", None, iso.real_capability(c_obs, q), None, "no real value: the observed index is too low") for c_obs, q in ((1.67, 0.4), (1.67, 0.5), (2.0, 0.4), (2.0, 0.5))]
    # table 11: the printed values follow 1 / C^2 = 1 / C_obs^2 - (0.3 / C_MP)^2, a C_MP read with 6 u_MP, not with the 3 u_MP of 9.2
    for c_mp, printed in ((2.0, 1.36), (1.66, 1.37), (1.33, 1.39), (1.0, 1.45), (0.5, 2.21)):
        got = iso.real_capability_from_cmp(1.33, c_mp)
        out.append(Check(f"rel-t11-{c_mp}", "iso22514_7.rel", "req.iso7_rel", REF + "table 11 (10.2)", printed, got, None,
                         f"table 11 equals 1 / C_p,p^2 = 1 / C_obs^2 - (0.3 / C_MP)^2, which is C_MP = 0.3 (U - L) / (6 u_MP); 9.2 defines C_MP with 3 u_MP, which gives Q_MP = 0.4 / C_MP and the values of table 10",
                         level="known").with_abs(0.0051))
    out.append(Check("rel-example", "iso22514_7.rel", "req.iso7_rel", REF + "10.1, example (C_p,obs = 1.00, Q_MP = 30 %)", 1.1185, iso.real_capability(1.0, 0.3), None,
                     f"the standard prints 1.118 5; the formula (1 - 2.25 x 0.09)^(-1/2) is {iso.real_capability(1.0, 0.3):.4f} (and Table 10 prints 1.12)", level="known").with_abs(0.0005))
    return out


def attribute() -> list[Check]:
    b = iso.bowker([[7, 3, 1], [10, 4, 7], [2, 1, 5]])
    out = [c("attr", "bowker", "12.2, table 12", "8.603", b["chi2"], "req.iso7_attr"), c("attr", "bowker-crit", "12.2", "7.815", b["critical"], "req.iso7_attr", extra=5e-4),
           Check("attr-bowker-rejected", "iso22514_7.attr", "req.iso7_attr", REF + "12.2, the hypothesis of symmetry is rejected", False, b["symmetric"])]
    res = {}
    for o in range(3):
        rows = []
        for t in range(3):
            rows.append([0 if v >= 0.566152 or v <= 0.446697 else 1 if 0.470832 <= v <= 0.542704 else int((o == 0 and t == 0) != (v > 0.5)) for v in FIG6])
        res[f"op{o + 1}"] = rows
    r = iso.uncertainty_range(FIG6, res, 0.45, 0.55)
    out += [c("attr", "dur", "12.3.3, step 6", "0.023448", r["d_ur"], "req.iso7_attr", extra=1e-7), c("attr", "dlr", "12.3.3, step 7", "0.024135", r["d_lr"], "req.iso7_attr", extra=1e-7),
            c("attr", "d", "12.3.3, step 8", "0.0237915", r["d"], "req.iso7_attr", extra=1e-8), c("attr", "q", "12.3.3, step 9 (printed as 0.24)", "0.24", r["q_attr"] / 100.0, "req.iso7_attr")]
    return out


def scenarios() -> list[Check]:
    return linearity() + process() + combination() + relation() + attribute()

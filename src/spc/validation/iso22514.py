"""ISO 22514-8:2014, annex A and B: the worked examples of the standard run against spc.core.multistate.

The values of the examples are typed in from the standard (annex A, tables A.1, A.3, A.5, A.7, A.9, B.1, B.3 and B.4); the means and standard
deviations that the standard prints next to them check the typing. Numbers are compared to the number of decimals that the standard prints.
Where a printed number cannot be reproduced from the standard's own data or formulas, the check is `known`, with the evidence.
"""

from __future__ import annotations

import numpy as np

from spc.core import multistate as ms
from spc.validation.checks import Check

REF = "ISO 22514-8:2014, "

A1 = {"P": [26.3, 25.8, 27.3, 28.1, 26.9, 26.4, 27.4, 26.5, 27.7, 24.7], "I": [31.5, 32.3, 30.0, 32.4, 31.3, 31.1, 29.4, 29.6, 31.5, 32.5],
      "C": [35.6, 35.1, 36.3, 37.4, 36.0, 35.5, 36.6, 37.3, 35.9, 37.9]}
A3_PHASE1 = {"BL": [59, 58.2, 58.7, 58.5, 58.4, 58.9], "BM": [58.4, 58.5, 58.6, 58.5, 58.4, 58.4], "BR": [58.3, 58.6, 58.5, 59, 58.6, 58.6],
             "EL": [58.3, 58.6, 58.5, 59, 58.6, 58.6], "EM": [58.3, 58.6, 58.5, 59, 58.6, 58.6], "ER": [58.3, 58.6, 58.5, 59, 58.6, 58.6]}
A5_PHASE2 = [[58.1, 57.8, 58.2], [58.3, 57.6, 57.5], [58.0, 57.9, 58.3], [58.0, 57.8, 58.5], [57.8, 58.0, 57.4], [57.7, 57.3, 57.0], [58.2, 57.8, 58.2]]
STEADY = [58.1, 57.8, 58.2, 58.3, 57.6, 57.5, 58.0, 57.9, 58.3, 58.0, 57.8, 58.5, 57.8, 58.0, 57.4, 57.7, 57.3, 57.0, 58.2, 57.8, 58.2]
START = [59.0, 58.2, 58.7, 58.5, 58.4, 58.9, 58.4, 58.5, 58.6, 58.5, 58.4, 58.4, 58.3, 58.6, 58.5, 59.0, 58.6, 58.6]
END = [58.3, 58.6, 58.5, 59.0, 58.6, 58.6, 58.3, 58.6, 58.5, 59.0, 58.6, 58.6, 58.3, 58.6, 58.5, 59.0, 58.6, 58.6]
ADAPTERS = {"A1": [20.12, 20.11, 20.11, 20.12, 20.10], "A2": [20.11, 20.13, 20.11, 20.10, 20.10], "A3": [20.14, 20.11, 20.12, 19.95, 20.11],
            "A4": [20.12, 20.12, 20.11, 20.13, 20.12], "A5": [20.08, 20.07, 20.06, 20.09, 20.09], "A6": [20.01, 20.03, 20.01, 20.02, 20.05]}
B3 = {"A1": [143, 140, 137, 139], "A2": [143, 140, 140, 141, 145], "A3": [136, 135, 137, 137, 136]}
B4 = {"A1": [143.1] * 5, "A2": [140.2, 140.2, 140.2, 140.1], "A3": [140.2, 140.0, 140.2, 140.3, 140.6]}


def _dp(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


def c(area: str, key: str, ref: str, printed: str, got: float, level: str = "must", note: str = "", extra: float = 0.0, req: str = "req.iso_state") -> Check:
    e = float(printed)
    chk = Check(f"{area}-{key}", f"iso22514.{area[0]}", req, REF + ref, e, got, None, note, level=level)
    return chk.with_abs(0.5 * 10 ** -_dp(printed) + extra + 1e-12 * abs(e))


def example_a1() -> list[Check]:
    st = {k: np.array(v) for k, v in A1.items()}
    t = ms.state_tests(st)
    out = [c("1", "grubbs-crit", "A.1.7.1", "2.290", ms.grubbs_critical(10), extra=5e-4), c("1", "grubbs-crit-30", "A.1.7.1", "2.908", ms.grubbs_critical(30)),
           c("1", "grubbs-30", "A.1.7.1", "1.624", ms.grubbs(np.concatenate(list(st.values())))["g"])]
    for k, g in zip("PIC", ("2.016", "1.539", "1.671")):
        out.append(c("1", f"grubbs-{k}", "A.1.7.1", g, t["states"][k]["grubbs"]["g"]))
    for k, m, s in zip("PIC", ("26.710", "31.160", "36.360"), ("0.997", "1.143", "0.922")):
        out.append(c("1", f"mean-{k}", "table A.2", m, st[k].mean()))
        out.append(c("1", f"s-{k}", "table A.2", s, st[k].std(ddof=1)))
    b = ms.bartlett_forced(list(st.values()))
    out += [c("1", "bartlett", "A.1.7.2", "0.414", b["statistic"]), c("1", "bartlett-crit", "A.1.7.2", "5.991", b["critical"]), c("1", "bartlett-p", "A.1.7.2", "0.813", b["p"])]
    f = ms.fisher_standard(list(st.values()), 10)
    out += [c("1", "fisher", "A.1.7.2", "222", f["statistic"], extra=0.5), c("1", "fisher-crit", "A.1.7.2", "3.35", f["critical"])]
    a = ms.analyse(st, 25.0, 45.0)
    out.append(Check("1-type", "iso22514.1", "req.iso_state", REF + "A.1.7.3, type of global dispersion", 1, a["type"], None, "widths equal, locations different, the difference constant (decided by the analyst for this process)", level="must")
               if a["type"] == 2 else Check("1-type", "iso22514.1", "req.iso_state", REF + "A.1.7.3, type of global dispersion", 1, a["type"]))
    out.append(c("1", "delta-m", "A.1.7.2", "9.65", a["delta_m"]))
    # A.1.7.3: the standard writes Pm = (20 - 9,65) / (6 x 1,01) = 1,69 and Pmk = 0,56. 1,01 is not the pooled s (1,0248)
    out.append(c("1", "pmk-l", "A.1.7.3", "0.56", a["pmk_l"]))
    out.append(c("1", "pm", "A.1.7.3", "1.69", a["pm"], level="known",
                 note=f"the standard writes (20 - 9,65) / (6 x 1,01) = 1,69, but that is {10.35 / (6 * 1.01):.3f}; with the pooled standard deviation {a['sigma_pooled']:.4f} the result is {a['pm']:.3f}, "
                      f"with the mean of the three standard deviations (6 x {np.mean([x.std(ddof=1) for x in st.values()]):.4f}) it is {10.35 / (6 * np.mean([x.std(ddof=1) for x in st.values()])):.3f}"))
    return out


def example_a2() -> list[Check]:
    out = []
    ph1 = list(A3_PHASE1.values())
    for k, m, s in zip(A3_PHASE1, ("58.617", "58.467", "58.600", "58.600", "58.600", "58.600"), ("0.306", "0.082", "0.228", "0.228", "0.228", "0.228")):
        out.append(c("2", f"p1-mean-{k}", "table A.4", m, np.mean(A3_PHASE1[k])))
        out.append(c("2", f"p1-s-{k}", "table A.4", s, np.std(A3_PHASE1[k], ddof=1)))
    for k, g in zip(A3_PHASE1, ("1.361", "1.633", "1.754", "1.754", "1.754", "1.754")):
        out.append(c("2", f"p1-grubbs-{k}", "A.2.6.1", g, ms.grubbs(A3_PHASE1[k])["g"]))
    out += [c("2", "p1-grubbs-crit", "A.2.6.1", "1.887", ms.grubbs_critical(6)), c("2", "p1-grubbs-crit-36", "A.2.6.1", "2.991", ms.grubbs_critical(36)),
            c("2", "p1-grubbs-36", "A.2.6.1", "1.940", ms.grubbs(np.concatenate(ph1))["g"])]
    b = ms.bartlett_forced(ph1)
    out += [c("2", "p1-bartlett", "A.2.7", "6.470", b["statistic"]), c("2", "p1-bartlett-crit", "A.2.7", "11.070", b["critical"]), c("2", "p1-bartlett-p", "A.2.7", "0.263", b["p"])]
    f = ms.fisher_standard(ph1, 6)
    out += [c("2", "p1-fisher", "A.2.7", "0.369", f["statistic"]), c("2", "p1-fisher-crit", "A.2.7", "2.53", f["critical"]),
            c("2", "p1-fisher-p", "A.2.7", "0.66", f["p"], level="known", note=f"F = {f['statistic']:.3f} with {f['df1']} and {f['df2']} degrees of freedom has p = {f['p']:.3f}; the printed 0,66 does not belong to it (the conclusion, no effect, is the same)")]
    out.append(c("2", "p1-sigma", "A.2.7", "0.227", float(np.sqrt(b["pooled_variance"])), note="standard deviation of the local dispersions", extra=0.001))
    ph2 = A5_PHASE2
    for i, (m, s) in enumerate(zip(("58.033", "57.800", "58.067", "58.100", "57.733", "57.333", "58.067"), ("0.208", "0.436", "0.208", "0.361", "0.306", "0.351", "0.231")), start=1):
        out.append(c("2", f"p2-mean-{i}", "table A.6", m, np.mean(ph2[i - 1])))
        out.append(c("2", f"p2-s-{i}", "table A.6", s, np.std(ph2[i - 1], ddof=1)))
    for i, g in enumerate(("1.121", "1.147", "1.121", "1.109", "1.091", "1.044", "1.155"), start=1):
        out.append(c("2", f"p2-grubbs-{i}", "A.2.8.1", g, ms.grubbs(ph2[i - 1])["g"]))
    out += [c("2", "p2-grubbs-crit", "A.2.8.1", "1.154", ms.grubbs_critical(3), extra=1e-3), c("2", "p2-grubbs-crit-21", "A.2.8.1", "2.734", ms.grubbs_critical(21)),
            c("2", "p2-grubbs-21", "A.2.8.1", "2.327", ms.grubbs(np.concatenate(ph2))["g"], level="known",
              note="the 21 values have mean 57,876 and s 0,3714, the most extreme value is 57,0: G = 0,876 / 0,3714 = 2,359; the printed 2,327 cannot be reproduced (same conclusion: no outlier)")]
    b2 = ms.bartlett_forced(ph2)
    out += [c("2", "p2-bartlett", "A.2.8.2", "1.71", b2["statistic"]), c("2", "p2-bartlett-crit", "A.2.8.2", "12.59", b2["critical"]), c("2", "p2-bartlett-p", "A.2.8.2", "0.94", b2["p"]),
            c("2", "p2-sigma", "A.2.8.2", "0.306", float(np.sqrt(b2["pooled_variance"])), level="known",
              note=f"the pooled standard deviation of the seven samples is {np.sqrt(b2['pooled_variance']):.4f}; the printed 0,306 is the standard deviation of sample 5")]
    f2 = ms.fisher_standard(ph2, 3)
    out += [c("2", "p2-fisher", "A.2.8.2", "2.42", f2["statistic"]), c("2", "p2-fisher-crit", "A.2.8.2", "2.85", f2["critical"]),
            c("2", "p2-fisher-p", "A.2.8.2", "0.094", f2["p"], level="known", note=f"F = {f2['statistic']:.2f} with {f2['df1']} and {f2['df2']} degrees of freedom has p = {f2['p']:.3f}; the printed 0,094 does not belong to it")]
    three = [STEADY, START, END]
    b3 = ms.bartlett_forced(three)
    f3 = ms.fisher_standard(three)
    out += [c("2", "a7-bartlett", "table A.7", "7.270", b3["statistic"]), c("2", "a7-bartlett-crit", "table A.7", "5.991", b3["critical"]), c("2", "a7-bartlett-p", "table A.7", "0.026", b3["p"]),
            c("2", "a7-fisher", "table A.7", "42.91", f3["statistic"]), c("2", "a7-fisher-crit", "table A.7", "3.15", f3["critical"])]
    a = ms.analyse({"steady": STEADY, "transient": START + END}, 55.0, 60.0, widths_equal=False, locations_equal=False, delta_m_variable=True)
    s1, s2 = a["states"]["steady"], a["states"]["transient"]
    out += [c("2", "a8-mean-1", "table A.8", "57.876", s1["mean"]), c("2", "a8-s-1", "table A.8", "0.371", s1["s"]), c("2", "a8-mean-2", "table A.8", "58.581", s2["mean"]),
            c("2", "a8-s-2", "table A.8", "0.216", s2["s"]), c("2", "a8-delta-m", "table A.8", "0.705", a["delta_m"], extra=1e-3),
            c("2", "a8-x0135-1", "table A.8", "56.763", s1["x0135"], extra=1e-3), c("2", "a8-x99865-1", "table A.8", "58.989", s1["x99865"], extra=1e-3),
            c("2", "a8-x0135-2", "table A.8", "57.933", s2["x0135"], extra=1e-3), c("2", "a8-x99865-2", "table A.8", "59.229", s2["x99865"], extra=1e-3),
            c("2", "a8-dil-1", "table A.8", "1.113", s1["d_l"], extra=1e-3), c("2", "a8-dil-2", "table A.8", "0.648", s2["d_l"], extra=1e-3),
            c("2", "a8-pmkl", "table A.8", "2.58", a["pmk_l"]), c("2", "a8-pmku", "table A.8", "1.91", a["pmk_u"]), c("2", "a8-pmk", "table A.8", "1.91", a["pmk"])]
    out.append(Check("2-type", "iso22514.2", "req.iso_state", REF + "A.2.8.2, type 5", 5, a["type"], None, "widths different, differences of the locations variable"))
    out.append(c("2", "a8-pm", "table A.8", "2.25", a["pm"], level="known",
                 note=f"table 2 for type 5: Pm = T / (max D_il + max D_iu + delta_m*) = 5 / (1,113 + 1,113 + 0,705) = {a['pm']:.2f}; the printed 2,25 is 5 / (1,113 + 1,113) = {5 / (2 * 1.113):.2f}, "
                      "without delta_m*"))
    return out


def example_a3() -> list[Check]:
    st = {k: np.array(v) for k, v in ADAPTERS.items()}
    out = [c("3", "grubbs-crit", "A.3.4", "1.715", ms.grubbs_critical(5), extra=1e-3), c("3", "grubbs-a3", "A.3.4", "1.766", ms.grubbs(st["A3"])["g"]),
           c("3", "grubbs-crit-all", "A.3.4", "2.908", ms.grubbs_critical(30)), c("3", "grubbs-all", "A.3.4", "3.093", ms.grubbs(np.concatenate(list(st.values())))["g"])]
    a = ms.analyse(ADAPTERS, 19.8, 20.2, outlier_physical=True, outlier_direction="negative")
    out.append(c("3", "outlier-delta-a", "A.3.4", "-0.17", a["outliers"][0]["delta_a"]))
    out += [c("3", "bartlett", "A.3.5", "3.430", a["widths"]["statistic"], extra=1e-3), c("3", "bartlett-crit", "A.3.5", "11.070", a["widths"]["critical"]),
            c("3", "fisher", "A.3.5", "46.85", a["locations"]["statistic"]), c("3", "fisher-crit", "A.3.5", "2.62", a["locations"]["critical"])]
    raw = ms.bartlett_forced(list(st.values()))
    out.append(c("3", "bartlett-with-outlier", "A.3.5, note", "34.44", raw["statistic"], level="known",
                 note=f"formula B.2 with the 30 values gives {raw['statistic']:.2f}, also with the standard deviations rounded as in table A.11 (34,39); the printed 34,44 is 0,05 higher (same conclusion: heterogeneous)"))
    out += [c("3", "sigma", "A.3.6", "0.0123", a["sigma_pooled"], extra=1e-4), Check("3-dof", "iso22514.3", "req.iso_state", REF + "A.3.6", 23.0, float(a["dof"]), None)]
    out += [c("3", "delta-m", "A.3.6", "0.096", a["delta_m"], extra=1e-3), c("3", "dil", "A.3.6", "0.2069", a["states"]["A1"]["d_l"], extra=1e-4), c("3", "diu", "A.3.6", "0.0369", a["states"]["A1"]["d_u"], extra=1e-4),
            c("3", "pm", "A.3.6", "1.25", a["pm"]), c("3", "pmk-u", "A.3.6", "2.17", a["pmk_u"]), c("3", "pmk-l", "A.3.6", "1.08", a["pmk_l"]), c("3", "pmk", "A.3.6", "1.08", a["pmk"])]
    out.append(Check("3-type", "iso22514.3", "req.iso_state", REF + "A.3.6, type 1", 1, a["type"]))
    return out


def annex_b() -> list[Check]:
    out = []
    x = np.array([138.0, 140, 137, 180])
    g = ms.grubbs(x)
    t = float(__import__("scipy.stats", fromlist=["t"]).t.ppf(1 - 0.05 / (2 * 4), 2))
    out += [c("4", "grubbs-mean", "table B.1", "148.75", x.mean()), c("4", "grubbs-s", "table B.1", "20.87", x.std(ddof=1)), c("4", "grubbs-t", "table B.1", "8.860", t),
            c("4", "grubbs-threshold", "table B.1", "1.481", g["g_crit"]), c("4", "grubbs-g", "table B.1", "1.497", g["g"], extra=1e-3)]
    out.append(Check("4-grubbs-outlier", "iso22514.4", "req.iso_state", REF + "table B.1", 180.0, g["value"]))
    b = ms.bartlett_forced(list(B3.values()))
    out += [c("4", "bartlett-variance", "table B.3", "3.67", b["pooled_variance"], extra=5e-3), c("4", "bartlett-crit", "table B.3", "5.991", b["critical"]),
            c("4", "bartlett-c", "table B.3", "1.127", b["correction"], level="known",
               note=f"c = 1 + (sum 1/nu_j - 1/nu) / (3 (k - 1)) = 1 + (0,8333 - 0,0909) / 6 = {b['correction']:.4f}; the printed 1,127 is 0,003 higher"),
            c("4", "bartlett", "table B.3", "3.58", b["statistic"], level="known", note=f"formula B.2 with the printed data gives {b['statistic']:.3f}; the table is made with rounded logarithms and the printed c")]
    f = [ms.forced_variance(v, 0.1) for v in B4.values()]
    out += [c("4", "forced-1", "table B.4", "0.0016", f[0], extra=1e-6), c("4", "forced-2", "table B.4", "0.0074", f[1], extra=1e-6)]
    out.append(Check("4-forced-3", "iso22514.4", "req.iso_state", REF + "table B.4, range 6 scale marks: no forcing", None, f[2]))
    b4 = ms.bartlett_forced(list(B4.values()), 0.1)
    out.append(Check("4-b4-verdict", "iso22514.4", "req.iso_state", REF + "table B.4, non-homogeneous variances", True, b4["statistic"] > b4["critical"]))
    out.append(c("4", "b4-crit", "table B.4", "5.991", b4["critical"]))
    out.append(c("4", "b4-bartlett", "table B.4", "8.70", b4["statistic"], level="known",
                 note=f"the forced variances 0,0016 and 0,0074 are reproduced; the pooled variance is {b4['pooled_variance']:.5f} (printed 0,0205) and the indicator {b4['statistic']:.2f}; the printed logarithms (-2,80, -1,13, -1,32) are not natural logarithms of the variances, so the table cannot be followed"))
    return out


def scenarios() -> list[Check]:
    return example_a1() + example_a2() + example_a3() + annex_b()

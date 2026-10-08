"""The AIAG MSA Reference Manual, 4th edition: the attribute study of chapter III section C (Tables III-C 1 to 7, cross-tab method) and the bias study of chapter III section B
(Table III-B 1 and 2) run against the program. The linearity example (Table III-B 4 and 5) is run with the one of ISO 22514-7 (the data are the same: `iso22514_7.py`).

The data are typed in from the manual. Numbers are compared to the digits that the manual prints.
"""

from __future__ import annotations

import math

from scipy import stats

from spc.core import msa_attribute as MA
from spc.validation.checks import Check

REF = "AIAG MSA, 4th ed., "
# Table III-C 1: one line per part, the three trials of appraiser A, of B and of C, and the reference decision (1 = acceptable)
ROWS = """
111 111 111 1
111 111 111 1
000 000 000 0
000 000 000 0
000 000 000 0
110 110 100 1
111 111 101 1
111 111 111 1
000 000 000 0
111 111 111 1
111 111 111 1
000 000 010 0
111 111 111 1
110 111 100 1
111 111 111 1
111 111 111 1
111 111 111 1
111 111 111 1
111 111 111 1
111 111 111 1
110 101 010 1
001 010 110 0
111 111 111 1
111 111 111 1
000 000 000 0
010 000 001 0
111 111 111 1
111 111 111 1
111 111 111 1
000 001 000 0
111 111 111 1
111 111 111 1
111 111 111 1
001 001 011 0
111 111 111 1
110 111 101 1
000 000 000 0
111 111 111 1
000 000 000 0
111 111 111 1
111 111 111 1
000 000 000 0
101 111 110 1
111 111 111 1
000 000 000 0
111 111 111 1
111 111 111 1
000 000 000 0
111 111 111 1
000 000 000 0
"""
# Table III-C 1, column "Ref Value": the reference value of each part measured by a variable system (signal detection approach, Table III-C 8)
REF_VALUES = [0.476901, 0.509015, 0.576459, 0.566152, 0.570360, 0.544951, 0.465454, 0.502295, 0.437817, 0.515573, 0.488905, 0.559918, 0.542704, 0.454518, 0.517377, 0.531939, 0.519694, 0.484167, 0.520496, 0.477236, 0.452310, 0.545604, 0.529065, 0.514192, 0.599581, 0.547204, 0.502436, 0.521642, 0.523754, 0.561457, 0.503091, 0.505850, 0.487613, 0.449696, 0.498698, 0.543077, 0.409238, 0.488184, 0.427687, 0.501132, 0.513779, 0.566575, 0.462410, 0.470832, 0.412453, 0.493441, 0.486379, 0.587893, 0.483803, 0.446697]
BIAS = [5.8, 5.7, 5.9, 5.9, 6.0, 6.1, 6.0, 6.1, 6.4, 6.3, 6.0, 6.1, 6.2, 5.6, 6.0]  # Table III-B 1, reference value 6.00


def _study():
    ratings = {"A": [[], [], []], "B": [[], [], []], "C": [[], [], []]}
    reference = []
    for line in ROWS.strip().split("\n"):
        a, b, c, ref = line.split()
        for name, trials in (("A", a), ("B", b), ("C", c)):
            for t in range(3):
                ratings[name][t].append(int(trials[t]))
        reference.append(int(ref))
    return ratings, reference


def _c(key: str, area_ref: str, expected: float, got: float, tol: float, note: str = "") -> Check:
    return Check(f"aiag-{key}", "aiag_msa", "req.agreement", REF + area_ref, expected, float(got), None, note).with_abs(tol)


def scenarios() -> list[Check]:
    ratings, reference = _study()
    r = MA.evaluate(ratings, reference)
    out: list[Check] = []
    k = r["between"]["kappa"]
    for pair, printed in (("A×B", 0.86), ("A×C", 0.78), ("B×C", 0.79)):
        out.append(_c(f"kappa-{pair}", f"Table III-C 3, kappa of the appraisers {pair}", printed, k[pair], 5e-3, "printed with two decimals"))
    ref = r["against_reference"]
    for x, printed in (("A", 0.88), ("B", 0.92), ("C", 0.77)):
        out.append(_c(f"kappa-ref-{x}", f"chapter III-C, kappa of appraiser {x} to the reference decision", printed, ref[x]["kappa"], 5e-3, "printed with two decimals"))
    for x, eff, lci, uci in (("A", 84, 71, 93), ("B", 90, 78, 97), ("C", 80, 66, 90)):
        out.append(_c(f"eff-{x}", f"Table III-C 5, effectiveness of appraiser {x} (score against the reference)", eff, ref[x]["effectiveness"]["pct"], 0.5))
        out.append(_c(f"lci-{x}", f"Table III-C 5, 95 % lower bound of appraiser {x}", lci, ref[x]["effectiveness"]["ci"][0], 0.5, "exact binomial interval, printed in whole percent"))
        out.append(_c(f"uci-{x}", f"Table III-C 5, 95 % upper bound of appraiser {x}", uci, ref[x]["effectiveness"]["ci"][1], 0.5, "exact binomial interval, printed in whole percent"))
    for x, miss, fa in (("A", 6.3, 4.9), ("B", 6.3, 2.0), ("C", 12.5, 8.8)):
        out.append(_c(f"miss-{x}", f"Table III-C 7, miss rate of appraiser {x}", miss, ref[x]["miss"]["pct"], 0.06, "6.25 is printed as 6.3"))
        out.append(_c(f"fa-{x}", f"Table III-C 7, false alarm rate of appraiser {x}", fa, ref[x]["false_alarm"]["pct"], 0.06))
    s = r["system"]["right"]
    out += [_c("sys", "Table III-C 5, system effective score against the reference", 78, s["pct"], 0.5),
            _c("sys-lci", "Table III-C 5, system, 95 % lower bound", 64, s["ci"][0], 0.5, "printed in whole percent"),
            Check("aiag-sys-uci", "aiag_msa", "req.agreement", REF + "Table III-C 5, system, 95 % upper bound: 89 % printed, the exact binomial interval gives 88.5 %", 89, round(s["ci"][1]), None, "88.47 rounds to 88; the manual prints 89", level="known")]
    # chapter III-B, example - bias (independent sample method): average 6.0067, standard deviation 0.2120, standard error 0.0547, t = 0.12 (14 degrees of freedom), interval of the bias -0.1107 to 0.1241
    n = len(BIAS)
    mean = sum(BIAS) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in BIAS) / (n - 1))
    se = sd / math.sqrt(n)
    bias = mean - 6.0
    q = stats.t.ppf(0.975, n - 1)
    out += [_c("bias-mean", "Table III-B 2, average of the 15 readings", 6.0067, mean, 5e-5), _c("bias-sd", "Table III-B 2, repeatability", 0.2120, sd, 5e-5),
            _c("bias-se", "Table III-B 2, standard error of the average", 0.0547, se, 5e-5), _c("bias-t", "Table III-B 2, t statistic", 0.12, bias / se, 5e-3),
            _c("bias-q", "Table III-B 2, significant t value (2-tailed)", 2.14479, q, 5e-6),
            _c("bias-lo", "Table III-B 2, lower bound of the bias", -0.1107, bias - q * se, 5e-5), _c("bias-hi", "Table III-B 2, upper bound of the bias", 0.1241, bias + q * se, 5e-5)]
    out += _new_methods(ratings)
    return out


def _new_methods(ratings) -> list[Check]:
    from spc.core import msa_aiag as AI

    out: list[Check] = []
    # signal detection approach: the codes come from the ratings (+ all accepted in all trials, - all rejected, x they did not agree)
    codes = []
    for i in range(50):
        votes = [ratings[x][t][i] for x in "ABC" for t in range(3)]
        codes.append("+" if all(votes) else "-" if not any(votes) else "x")
    sd = AI.signal_detection(REF_VALUES, codes, 0.450, 0.550)
    out += [_c("sd-lsl", "chapter III-C, signal detection: d_LSL", 0.024135, sd["d_lsl"], 5e-7), _c("sd-usl", "chapter III-C, signal detection: d_USL", 0.023448, sd["d_usl"], 5e-7),
            _c("sd-d", "chapter III-C, signal detection: d = average of d_LSL and d_USL", 0.0237915, sd["d"], 5e-8),
            _c("sd-pct", "chapter III-C, signal detection: %GRR against the tolerance 0.100 (printed as 24 %)", 24, sd["pct"], 0.5)]
    # bias, control chart method (Table III-B 3): the example gives g = 20, m = 5, reference 6.01, grand average 6.021, repeatability 0.2048 (= average range / d2*)
    d2s, df = AI.d2_star(5, 20)
    b = AI.bias_from_chart(6.021, 0.2048 * d2s, 5, 20, 6.01, process_sd=2.5)
    out += [_c("bc-d2", "Appendix C, d2* for m = 5 and g = 20", 2.33394, d2s, 5e-6), _c("bc-df", "Appendix C, degrees of freedom for m = 5 and g = 20", 72.7, df, 0.05),
            _c("bc-t", "Table III-B 3, t statistic", 0.5371, b["t"], 5e-5), _c("bc-q", "Table III-B 3, significant t value", 1.993, b["t_critical"], 5e-4),
            _c("bc-lo", "Table III-B 3, lower bound of the bias", -0.0299, b["ci"][0], 2e-4, "the manual rounds the bias and the standard error before the interval"),
            _c("bc-hi", "Table III-B 3, upper bound of the bias", 0.0519, b["ci"][1], 2e-4, "the manual rounds the bias and the standard error before the interval")]
    # range method (Table III-B 6)
    r = AI.range_method([0.85, 0.75, 1.00, 0.45, 0.50], [0.80, 0.70, 0.95, 0.55, 0.60], process_sd=0.0777)
    out += [_c("rm-rbar", "Table III-B 6, average range", 0.07, r["average_range"], 5e-6), _c("rm-d2", "Table III-B 6 and Appendix C, d2* for m = 2 and g = 5", 1.19105, r["d2_star"], 5e-6),
            _c("rm-grr", "Table III-B 6, GRR = average range / d2*", 0.0588, r["grr"], 5e-5), _c("rm-pct", "Table III-B 6, %GRR against the process standard deviation 0.0777", 75.7, r["pct_process"], 0.1, "the manual divides the rounded GRR 0.0588")]
    # analytic method (chapter III-C, example with eight parts and the three added): the probabilities of acceptance and the results read from the manual's normal probability plot
    xs = [-0.016, -0.015, -0.014, -0.013, -0.012, -0.011, -0.0105, -0.010, -0.008, -0.006, -0.004, -0.002]
    acc = [0, 1, 3, 5, 8, 16, 18, 20, 20, 20, 20, 20]
    an = AI.analytic_method(xs, acc, -0.010, "lower")
    out += [_c(f"an-pac-{i}", f"chapter III-C, analytic method, probability of acceptance of the part {x}", p, an["points"][i]["pac"], 5e-4) for i, (x, p) in enumerate(zip(xs[:9], [0.025, 0.075, 0.175, 0.275, 0.425, 0.775, 0.875, 0.975, 1.0]))]
    plot = "read from a line drawn by eye on normal probability paper: the least squares line gives a close but not an equal value"
    out += [_c("an-x50", "chapter III-C, analytic method, reference value at Pac = 0.5 (Figure III-C 4)", -0.0123, an["x_at_0_5"], 3e-4, plot),
            _c("an-bias", "chapter III-C, analytic method, bias", 0.0023, an["bias"], 3e-4, plot),
            _c("an-sigma", "chapter III-C, analytic method, repeatability", 0.00142, an["repeatability"], 2e-4, plot),
            Check("aiag-an-sig", "aiag_msa", "req.agreement", REF + "chapter III-C, analytic method: the bias differs significantly from zero (t = 9.84 against 2.093)", True, an["bias_significant"])]
    return out

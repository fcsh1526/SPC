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
    return out

#!/usr/bin/env python3
"""Reads the reference data sets and the results of ISO/TR 11462-3:2020 from a PDF of the standard into one JSON file.

    python3 tools/extract_iso11462_3.py docs/757270310-ISO-TR-11462-3-2020.pdf src/spc/validation/data/iso_tr_11462_3.json

Needs `pdftotext` (poppler). The values of Annex A are taken as printed. Every number that is read is kept as the text that was printed
(for example "14.066349"), because the number of printed decimals gives the tolerance of a comparison. The script checks what it can:
that every data set has the printed total sample size and that every row of Annex A has the cells that the sizes of the sets require.
The data sets may be used in their original format without modification for the purposes of the document (Annex A of the standard).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

SIZES = {1: 125, 2: 600, 3: 1000, 4: 1000, 5: 1000, 6: 600, 7: 500, 8: 500, 9: 500, 10: 200}
NUM = r"(−?-?\d+(?:,\d+)?)"
VIOL = re.compile(r"(\d+)(?:to(\d+))?(violationofUCL|violationofLCL|runabovecentreline|runbelowcentreline)", re.I)  # on text without blanks: some tables are printed with spaced letters
CHARTS = {2: "xbar", 3: "individuals", 4: "median", 5: "s", 6: "R", 7: "mr"}
FORMULA = {"11": "l1", "12": "l2", "13": "l3", "14": "l4", "15": "d1", "16": "d2", "17": "d3", "18": "d4", "19": "d5"}


def text_of(path: str) -> list[str]:
    return subprocess.run(["pdftotext", "-layout", path, "-"], check=True, capture_output=True, text=True).stdout.split("\n")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def flat(s: str) -> str:
    return re.sub(r"\s+", "", s)


def dot(s: str) -> str:
    """'−0 ,0 02 5' -> '-0.0025' as text"""
    s = s.replace("−", "-").replace("–", "-").replace(" ", "").replace(",", ".")
    return s.replace("--", "-")


def table_a1(L: list[str]) -> dict[int, list[str]]:
    start = next(i for i, l in enumerate(L) if "Table A.1 — Test data input" in l)
    end = next(i for i, l in enumerate(L) if "Table A.2" in l and i > start)
    rows: dict[int, list[str]] = {}
    for l in L[start:end]:
        m = re.match(r"^\s*(\d[\d ]*?)\s{2,}(.*\S)\s*$", l)
        if not m or "©" in l:
            continue
        r = int(m.group(1).replace(" ", ""))
        cells = re.split(r"\s{2,}", m.group(2).strip())
        try:
            [float(dot(c)) for c in cells]
        except ValueError:
            continue
        rows[r] = [dot(c) for c in cells]
    data: dict[int, list[str]] = {k: [] for k in SIZES}
    for r in range(1, 1001):
        active = [k for k in SIZES if SIZES[k] >= r]
        if r not in rows or len(rows[r]) != len(active):
            raise SystemExit(f"Annex A row {r}: expected {len(active)} cells, found {rows.get(r)}")
        for k, v in zip(active, rows[r]):
            data[k].append(v)
    return data


def table_a2(L: list[str]) -> list[list[str]]:
    a = next(i for i, l in enumerate(L) if "Table A.2 — Test data set 11" in l)
    rows = []
    for l in L[a : a + 60]:
        m = re.match(r"^\s*(\d+)\s{2,}(.*\S)\s*$", l)
        if not m or "©" in l:
            continue
        cells = re.split(r"\s{2,}", m.group(2).strip())
        try:
            v = [dot(c) for c in cells]
            [float(x) for x in v]
        except ValueError:
            continue
        if len(v) == 3:
            rows.append(v)
    if len(rows) != 10:
        raise SystemExit(f"Table A.2: expected 10 cycles, found {len(rows)}")
    return rows


def sections(L, F):
    set_start = {}
    for i, f in enumerate(F):
        m = re.match(r"^5\.(\d+)Testdataset(\d+)$", f)
        if m and i > 400:
            set_start[int(m.group(1))] = i
    set_start[12] = next(i for i, f in enumerate(F) if f.startswith("Annex") and i > 4800)
    sec = {}
    for s in range(1, 11):
        idx = []
        for i in range(set_start[s], set_start[s + 1]):
            m = re.match(r"^5\.%d\.2\.(\d)" % s, F[i])
            if m:
                idx.append((int(m.group(1)), i))
        idx.sort(key=lambda t: t[1])
        for j, (k, i) in enumerate(idx):
            sec[(s, k)] = (i, idx[j + 1][1] if j + 1 < len(idx) else set_start[s + 1])
    return set_start, sec


def description(N, start, end):
    out = {}
    for i in range(start, end):
        t = N[i]
        m = re.match(r"^Distribution model (\S+) Resulting distribution (.+)$", t)
        if m:
            out["model"], out["distribution"] = m.group(1), m.group(2)
        m = re.match(r"^Data set Annex A, Table (A\.\d) decimal points (\d)$", t)
        if m:
            out["table"], out["decimals"] = m.group(1), int(m.group(2))
        m = re.match(r"^Total sample size (.+?) U ([−-]?[\d,]+)$", t)
        if m:
            out["usl"] = dot(m.group(2))
        m = re.match(r"^Size of subgroups (\d+|—) L ([−-]?[\d,]+)$", t)
        if m:
            out["lsl"] = dot(m.group(2))
            if m.group(1).isdigit():
                out["subgroup_size"] = int(m.group(1))
    return out


def capability(lines: list[str]) -> list[dict]:
    text = " ".join(lines)
    parts = re.split(r"Capability indices \(calculation method ([^)]*)\)", text)
    out = []
    for i in range(1, len(parts), 2):
        label, body = re.sub(r"\s+", "", parts[i]), parts[i + 1]
        stable = "Process is stable" in body
        entry = {"method": label, "stable": stable, "indices": []}
        for m in re.finditer(r"(?:Cp|Pp)\s+\(?(\d+,\d+)\)?[^\d]{0,30}?(?:Cpk|Ppk|Cp)\s+(\d+,\d+)(?:\s+(?:Cpk|Ppk)\s?L\s+(\d+,\d+)\s+(?:Cpk|Ppk)\s?U\s+(\d+,\d+))?", body):
            entry["indices"].append({"p": dot(m.group(1)), "pk": dot(m.group(2)), "pl": dot(m.group(3)) if m.group(3) else None,
                                     "pu": dot(m.group(4)) if m.group(4) else None})
        for m in re.finditer(r"(?:Cpk|Ppk)\s?L\s+(\d+,\d+)\s+(?:Cpk|Ppk)\s?U\s+(\d+,\d+)", body):
            if not any(e["pl"] == dot(m.group(1)) and e["pu"] == dot(m.group(2)) for e in entry["indices"]):
                if entry["indices"] and entry["indices"][-1]["pl"] is None:
                    entry["indices"][-1].update(pl=dot(m.group(1)), pu=dot(m.group(2)))
        for key in ("Pearson", "Johnson"):
            if key in body:
                entry.setdefault("variants", []).append(key.lower())
        entry["remark"] = norm(re.sub(r"Process is (?:not )?stable[^-]*- (?:Cp/Cpk|Pp/Ppk) is used", "", body))[:300]
        lu = re.search(r"(?:Cpk|Ppk)\s?L\s+(\d+,\d+)\s+(?:Cpk|Ppk)\s?U\s+(\d+,\d+)", body)
        if not entry["indices"] and lu and out and out[-1]["method"] == label and out[-1]["indices"] and out[-1]["indices"][-1]["pl"] is None:
            out[-1]["indices"][-1].update(pl=dot(lu.group(1)), pu=dot(lu.group(2)))  # the standard prints the lower and upper index of one block in two blocks
            continue
        out.append(entry)
    return out


def main(pdf: str, target: str) -> None:
    L = text_of(pdf)
    N = [norm(l) for l in L]
    F = [flat(l) for l in L]
    a1, a2 = table_a1(L), table_a2(L)
    set_start, sec = sections(L, F)
    sets = {}
    for s in range(1, 11):
        info = description(N, set_start[s], sec[(s, 1)][0])
        if len(a1[s]) != SIZES[s] or info.get("subgroup_size") is None:
            raise SystemExit(f"set {s}: sample size or subgroup size not as printed: {info}")
        r = {**info, "n": SIZES[s], "values": a1[s], "stats": {}, "charts": {}, "capability": []}
        a, b = sec[(s, 1)]
        rseen = 0
        for i in range(a, b):
            f = F[i]
            m = re.search(r"ISO22514-2:2017[,:]Formula\((1[1-9])\)", f)
            if m:
                n = re.search(NUM + r"(?:\([^)]*\))?ISO22514", f.replace("(d=", "(d=").replace("d(=", "(d="))
                num_text = f.split("ISO22514")[0]
                tok = re.findall(NUM, re.sub(r"\([^)]*\)", "", num_text.replace("(l=", "(").replace("(d=", "(")))
                r["stats"][FORMULA[m.group(1)]] = dot(tok[-1]) if tok else None
                continue
            m = re.match(r"^s" + NUM + r"ISO7870", f)
            if m:
                r["stats"]["sbar"] = dot(m.group(1)); continue
            m = re.match(r"^R" + NUM + r"ISO7870", f)
            if m:
                r["stats"]["Rtotal" if rseen == 0 else "Rbar"] = dot(m.group(1)); rseen += 1; continue
            m = re.match(r"^Rm" + NUM + r"ISO7870", f)
            if m:
                r["stats"]["Rm"] = dot(m.group(1))
        for k, name in CHARTS.items():
            if (s, k) not in sec:
                continue
            a, b = sec[(s, k)]
            pairs, lcl, viol = [], None, []
            for i in range(a, b):
                f = F[i]
                m = re.match(r"^LCL=" + NUM, f)
                if m:
                    lcl = dot(m.group(1)); continue
                m = re.match(r"^UCL=" + NUM, f)
                if m and lcl is not None:
                    pairs.append([lcl, dot(m.group(1))]); lcl = None
            # the tables of out of control situations are laid out in two columns and some are printed letter by letter and wrapped:
            # read the whole section as one text without blanks and cut it at the words that end an entry
            joined = "".join(F[i] for i in range(a, b) if "©ISO" not in F[i] and not re.match(r"^\d+$", F[i]))
            joined = re.sub(r"(?<=\d)zo(?=\d)", "to", joined)  # the text layer of the PDF has "63 zo 71" in table 28
            for mm in VIOL.finditer(joined):
                lo = int(mm.group(1)); hi = int(mm.group(2)) if mm.group(2) else lo
                kind = {"violationofucl": "violation of UCL", "violationoflcl": "violation of LCL", "runabovecentreline": "run above centreline", "runbelowcentreline": "run below centreline"}[mm.group(3).lower()]
                viol.append({"from": lo, "to": hi, "kind": kind})
            r["charts"][name] = {"limits": pairs, "violations": viol}
        a, b = sec[(s, 8)]
        r["capability"] = capability([N[i] for i in range(a, b) if N[i]])
        sets[str(s)] = r
    # set 11: the text of 5.11
    t11 = [N[i] for i in range(set_start[11], set_start[12])]
    joined = " ".join(t11)
    info11 = description(N, set_start[11], set_start[12])
    def pick(pattern, text=joined):
        m = re.search(pattern, text)
        return dot(m.group(1)) if m else None
    states = ["P", "I", "C"]
    s11 = {**info11, "n_per_state": 10, "values": {st: [row[i] for row in a2] for i, st in enumerate(states)}, "stats": {}, "grubbs": {}, "capability": {}}
    for st in states:
        s11["stats"]["mean_" + st] = pick(r"x \(State %s\) " % st + r"(\d+,\d+)")
        s11["stats"]["s_" + st] = pick(r"s \(State %s\) " % st + r"(\d+,\d+)")
        s11["grubbs"][st] = pick(r"State %s (\d,\d+)," % st) or pick(r"State %s (\d,\d+)\." % st)
    s11["stats"]["s_pooled"] = pick(r"\(d = 2\) (\d,\d+) ISO")
    s11["grubbs"]["critical_10"] = pick(r"subsamples:? [^—]*?is (\d,\d+);") or pick(r"is (2,29);")
    s11["grubbs"]["critical_30"] = pick(r"is (2,908);")
    s11["grubbs"]["g_30"] = pick(r"measurements the calculated value of Grubbs test statistic is (\d,\d+)") or pick(r"Grubbs test statistic is (1,624)")
    s11["bartlett"] = {"critical": pick(r"Bartlett test statistic at 5 % signi ?ficance level is (\d,\d+)"), "statistic": pick(r"calculated value of Bartlett test statistic is (\d,\d+)"),
                       "p": pick(r"p-value for Bartlett test statistic is (\d,\d+)")}
    s11["fisher"] = {"critical": pick(r"Fisher test statistic at 5 % signi ?ficance level is (\d,\d+)"), "statistic": pick(r"value of Fisher test statistic is (\d+)"),
                     "p": pick(r"p ?-value for Fisher test statistic is (\d,\d+)")}
    m = re.search(r"Pm (\d,\d+) Pmk (\d,\d+) Pmk ?L (\d,\d+) Pmk ?U (\d,\d+)", joined)
    if m:
        s11["capability"] = {"pm": dot(m.group(1)), "pmk": dot(m.group(2)), "pmk_l": dot(m.group(3)), "pmk_u": dot(m.group(4))}
    out = {
        "source": "ISO/TR 11462-3:2020(E), Guidelines for implementation of statistical process control (SPC), Part 3: Reference data sets for SPC software validation",
        "licence": "The data sets of Annex A may be used in their original format without modification for the purposes of the document. Taken from the copy of the standard held by the user of this program; do not pass it on.",
        "sets": sets, "set11": s11,
    }
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print("written", target, {k: len(v["values"]) for k, v in sets.items()}, s11["capability"], s11["bartlett"], s11["fisher"], s11["grubbs"])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])

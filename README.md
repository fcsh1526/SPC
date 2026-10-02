# SPC

SPC statistics engine following the AIAG-VDA SPC Manual (2026), ISO 7870 and ISO 22514.
依 AIAG-VDA SPC 手冊（2026）、ISO 7870、ISO 22514 實作的 SPC 統計核心。

Status: phase 1 core library, CSV import, web API and a bilingual (繁體中文 / English) web interface. See `docs/ARCHITECTURE.md` for the plan, sources and open questions.

## Install, test, run

```
pip install -e ".[dev]"
pytest                      # the browser tests are skipped when Playwright or Chromium is missing
python -m spc.api           # or: spc-serve   -> http://127.0.0.1:8000
```

The server listens on 127.0.0.1 only. There is no login yet, so do not expose it to a network.
Data is kept in memory and is lost when the server stops. Use the CSV export to keep marked data.

## What is in phase 1

| Module | Content |
|---|---|
| `spc.core.constants` | d2, d3, c4 and range-distribution quantiles, computed exactly |
| `spc.core.charts.variable` | X̄-s, X̄-R, I-MR with explicit α and exact χ² / w limits |
| `spc.core.charts.attribute` | p, np, c, u with exact binomial / Poisson limits |
| `spc.core.rules` | Stability criteria, each one switched on explicitly |
| `spc.core.stability` | Analysis-chart stability decision |
| `spc.core.capability` | Pm/Pmk, Pp/Ppk, Cp/Cpk, Cw/Cwk, .G / .Z, CI, PPM, naming gate, sample-size target adjustment |
| `spc.core.arl_oc` | OC curve and ARL |
| `spc.params` | All analysis parameters in one record, for archiving |
| `spc.data.csv_io` | CSV import and export: Big5 / UTF-8, delimiter and decimal comma, all errors reported with line numbers, SHA-256 of the source file |
| `spc.data.dataset` | `Dataset` with traceable invalid marks, subgroup building (`subgroups()`), counts for the report |
| `spc.data.outliers` | Hints for suspect values (MAD, Tukey, Grubbs). Hints never mark anything |
| `spc.service.analysis` | One analysis run: chart, criteria, stability, index names, indices, targets. Returns plain JSON data with message codes |
| `spc.api` | FastAPI app (`/api/...`) and the web interface (`spc/web/static`) |

## Web interface

Steps: import a CSV (Big5 / UTF-8, columns chosen on a preview) → check data and mark outliers with a reason → run the analysis → read charts, index names, confidence intervals and targets. A tools tab holds the small-sample target calculator and the ARL / OC calculator.

- Texts live in `spc/web/static/i18n/zh-TW.json` and `en.json`. `tests/test_i18n.py` fails when the two files differ, or when a message code in the backend has no text.
- The API sends message codes with parameters, never translated sentences.
- No build step and no external libraries. User text is shown with `textContent` only.
- API documentation: `/api/docs` while the server runs.

## Data flow

```python
from spc.data import ColumnMap, load_csv, suspects
from spc.core.charts.variable import xbar_s

data = load_csv("measurements.csv", ColumnMap(value="diameter", subgroup="lot", timestamp="time"))
for s in suspects(data, "mad"):          # hints only
    print(s.position, s.value)
data = data.mark_invalid([12], reason="value typed into the wrong cell", by="A. Chen")
chart = xbar_s(data.subgroups(incomplete="drop").matrix)   # marked value is left out
```

## Rules this code follows

- Every statistical parameter is explicit. No hidden defaults inside the calculations.
- Cp/Cpk and Pp/Ppk use the same formula (total variation). Only stability evidence decides the name.
- Within-subgroup indices are called Cw/Cwk and are for diagnosis only.
- Attribute chart limits come from the binomial / Poisson distribution, not from the normal approximation.
- Outliers are marked, not deleted. A mark needs a reason and a person. Tests only give hints.

## Source status

The numbers are checked against the AIAG-VDA SPC Yellow Volume (draft, February 2026). The July 2026 release version is not used yet. Differences are listed in `docs/ARCHITECTURE.md`.

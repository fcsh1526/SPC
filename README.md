# SPC

SPC statistics engine following the AIAG-VDA SPC Manual (2026), ISO 7870 and ISO 22514.
依 AIAG-VDA SPC 手冊（2026）、ISO 7870、ISO 22514 實作的 SPC 統計核心。

Status: phase 1 core library, CSV import, web API with database storage and login, and a bilingual (繁體中文 / English) web interface. See `docs/ARCHITECTURE.md` for the plan, sources and open questions.

## Install, test, run

```
pip install -e ".[dev]"
pytest                                          # the browser tests are skipped when Playwright or Chromium is missing
spc-admin --db spc.sqlite3 create-user alice --role admin     # the first user can only be made here
spc-serve --db spc.sqlite3                      # or: python -m spc.api   -> http://127.0.0.1:8000
```

Data, marks, reports and the audit trail are stored in one SQLite file (`--db`, or the environment variable `SPC_DB`; default `spc.sqlite3`, created with mode 0600). Back it up like any other record.
The server listens on 127.0.0.1 only. To serve a team, put it behind an https proxy, use `--host` and `--secure-cookies`. Without https, passwords and session cookies travel in clear text, and the server says so at start.

## Login, roles, audit

- Every `/api` route needs a login except `GET /api/meta` and `POST /api/auth/login`. A new route is protected unless it is listed as public.
- Roles: **viewer** reads and analyses, **engineer** also imports, marks, makes reports and deletes own datasets, **admin** also manages users, reads the audit trail and deletes any dataset. Datasets and reports are shared by all signed-in users.
- The person in a mark, a restore or a report is the signed-in user. The client cannot name someone else.
- Passwords: scrypt, at least 10 characters. Five wrong tries lock a name for 15 minutes. Sessions end after 2 hours idle or 12 hours. State-changing requests need the CSRF token.
- Users are managed in the Administration tab, or with `spc-admin` (`create-user`, `reset-password`, `unlock`, `list-users`, `audit-verify`). A password set by an administrator must be changed at the first sign-in.
- Audit trail: sign-ins, failed sign-ins, imports, marks, restores, deletions, reports and user changes, linked by a hash chain. The chain shows a changed or removed entry. It does not show that the newest entries were cut off, and it does not stop someone who can write the database file. Keep the last hash somewhere else from time to time (`spc-admin audit-verify` prints it).

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
| `spc.report` | The 20+2 element report as one self-contained HTML file (print it to PDF, A4) and a JSON archive with an integrity digest. `verify_archive` shows changes, `reproduce` runs the calculation again from the stored data |
| `spc.db` | SQLite database, dataset and report stores (read-change-write in one transaction) |
| `spc.auth` | Password hashing, sessions, roles, login throttling, audit hash chain |
| `spc.api` | FastAPI app (`/api/...`) and the web interface (`spc/web/static`) |

## Web interface

Sign in first. Steps: import a CSV (Big5 / UTF-8, columns chosen on a preview) → check data and mark outliers with a reason → run the analysis → read charts, index names, confidence intervals and targets. After an analysis, the report panel makes the 20+2 element report in either language. The Saved tab lists stored datasets and reports and opens them again. A tools tab holds the small-sample target calculator, the ARL / OC calculator and the archive check.

## Report

- Elements 1–10 and 20–22 take author input (process, machine, people, conditions, deviations, recommendations, measurement uncertainty). Everything else comes from the data and the same analysis run that the screen shows.
- The report is a snapshot. Later marks on the data do not change a report that exists.
- Cw/Cwk never appear in a report. The handbook says they are not for reporting.
- Elements 4 and 10 use the time stamps of the data when there are any.
- Element 22 gives the guard band `g = u(1 - risk) * U / k` and the acceptance limits `LSL + g`, `USL - g`.
- The archive keeps the data, the invalid marks with reason, person and time, all parameters, the result and the report inputs. Check it with `POST /api/archive/check` or the tools tab. A changed value breaks the digest. The digest shows a change. It does not stop one: to prove who made the record, sign the file outside this program.
- Report texts are in `spc/report/texts.py`. `tests/test_report.py` checks that both languages match.

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
data = data.mark_invalid([12], reason="value typed into the wrong cell", by="A. Chen")  # in the web app, `by` is the login
chart = xbar_s(data.subgroups(incomplete="drop").matrix)   # marked value is left out
```

## Rules this code follows

- Every statistical parameter is explicit. No hidden defaults inside the calculations.
- Cp/Cpk and Pp/Ppk use the same formula (total variation). Only stability evidence decides the name.
- Within-subgroup indices are called Cw/Cwk and are for diagnosis only.
- Attribute chart limits come from the binomial / Poisson distribution, not from the normal approximation.
- Outliers are marked, not deleted. A mark needs a reason and the signed-in person. Tests only give hints.

## Source status

The numbers are checked against the AIAG-VDA SPC Yellow Volume (draft, February 2026). The July 2026 release version is not used yet. Differences are listed in `docs/ARCHITECTURE.md`.

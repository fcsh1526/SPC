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
| `spc.core.distributions` | Fits for non-normal data (lognormal, Weibull, gamma, Johnson SU, Box-Cox, normal mixture, empirical), AIC ranking, seeded bootstrap interval |
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

## I-MR restarts and moving samples

After a tool change or an action that followed an alarm, the moving characteristics of an individuals chart must start again, or the old values cause more alarms although the process is corrected (draft 10.3.3.5).
- In the Data tab select the first value after the event, write the reason and press "Restart before selected values". Like an invalid mark it is stored with the reason, your sign-in and the time, goes into the audit trail, and can be taken away again.
- A moving range never spans a restart. After the start and after every restart the moving sample grows 1, 2, … up to the size you set in the analysis form, so there is no blind spot. The limits follow the size of the moving sample (a staircase in the chart), and the chart shows the restarts as dotted lines. Runs and trends do not continue over a restart.
- A plain restart keeps one centre line and one set of limits from all values. A tool change that moves the level then makes the location points after it fall outside the limits, because the level did move.
- If the process was changed on purpose (new fixture, improvement), tick "New limits from here" when you set the restart. It starts a new phase: the centre line and the limits are calculated again from the values of that phase alone, exactly as a chart made from those values would give them. Each phase needs one complete moving sample. The result and the report list the phases. The capability indices still use all valid values, so the level difference between phases stays in Pp/Ppk.
- Restarts apply only to the I-MR chart. The capability indices still use all valid values. The CSV export does not carry restarts. The archive does.

## Non-normal data (.G and .Z)

In the analysis form, choose a distribution (or "choose automatically") and a method. The indices are then named `Cpk.G` or `Ppk.Z` and come from the fitted distribution: `.G` from its 0.135 %, 50 % and 99.865 % quantiles, `.Z` from the shares outside the limits.
- The result lists every candidate family with AIC and the Anderson-Darling statistic. These describe the fit and are not tests. A normal distribution within 2 AIC units of the best one wins the automatic choice. Check the choice against what you know about the process.
- The interval is a percentile bootstrap (default 200 resamples, the same family fitted again). The seed is kept with the result, so the same data, settings and seed give the same interval. If the interval cannot be computed, a target is judged on the estimate alone and the result says so.
- Few values (under 50) make the tail quantiles uncertain. The result warns about it. The empirical method needs 2000 values and supports only `.G`.
- Through the API: `distribution`, `method`, `bootstrap_n` and `seed` in the analysis body. Results for a normal distribution (`distribution: "normal"`, the default) are unchanged.

## SPC at the line (control loop 1)

The tab "SPC at the line" holds SPC monitors: control charts that control a running process, as opposed to the analysis chart of a study.
- **Fixed limits.** They come from reference data, or from expected mean and standard deviation, and stay until an engineer sets new ones with a reason (a new limits revision with its history). Every sample you enter is checked at once against them and the enabled stability criteria. Warning limits are optional. Specification limits are never drawn on this chart.
- **Roles.** Operators enter samples and work through the action plan. Engineers set monitors up. Viewers only look.
- **Action plan (OCAP).** Each monitor holds what the operator does per violated criterion, who is responsible, and when to escalate. Everybody confirms that they know the plan before they can enter a sample, and again after a change of the plan.
- **A violation opens one incident.** Follow the steps of the draft: measure again to make sure the sample is valid (an invalid sample, declared with a reason, ends the incident), adjust process parameters, adjust process elements, check with a new sample, and if that does not help go to the root cause analysis and product containment. An incident is closed as "recovered" only after a documented action AND a new valid sample that meets all criteria. An incident that is not escalated in time is marked overdue. Everything is logged with person and time, and is in the audit trail.
- **Notification.** The page and the badge in the navigation show open incidents. With `spc-serve --alert-webhook URL` (or `SPC_ALERT_WEBHOOK`) the program also posts a JSON message to that URL when an incident opens.
- **Ongoing performance and capability.** Index of the latest samples (named Pp/Ppk or Cp/Cpk by the stability evidence), the four quadrants of the draft, the trend over earlier windows, a check whether the fixed limits still fit, and the response times of the action plan. One click makes a normal study report of the window.
- Monitors cover X-bar-s, X-bar-R, Median-R, I-MR and the count charts p, np, c and u. Count charts use exact binomial and Poisson limits that follow each sample's size, run only the limit, run and trend criteria (counts are not normal), have no specification limits, and give no capability report. Median charts use the c_n factors of the draft. Tolerance related monitors: the acceptance chart (draft 10.3.4; mean, median or individual values, with its variation chart) puts the limits inside the tolerance so that an accepted fraction out of tolerance p (default 1 %) is found with probability P_A (default 99 %); it needs both specification limits and warns when the variation is above T/10. The pre-control chart (draft 10.3.2.7) divides the tolerance into green (middle half), yellow and red and only monitors a start-up; the draft gives no rules for it, so the classical ones are used (marked as such in the program). Both signal only on their own limits or zones, have no warning limits, and their tolerance is fixed with the limits (change it with a new monitor). Charts with memory: the CUSUM chart (draft 10.3.5.4, tabular form with upper and lower sums, decision interval h, reference value k, optional head start) and the EWMA chart (draft 10.3.5.5, weight lambda, limits that widen from the start). h and L are solved exactly for the in-control average run length 1/alpha (370.4 at 3 sigma), and the monitor shows the run length by shift; the CUSUM table of the draft is reproduced. After a signal the chart starts again from its reference state. They need a stable variation and are meant to run next to an X-bar monitor. Short runs and several characteristics: the Z-MR chart (draft 10.3.2.9) standardises every value with the target and standard deviation of its product from a table, so one chart and one set of limits serve a job shop; Hotelling's T² and MEWMA charts (draft 10.3.2.8) watch several related characteristics together (2 to 10) and show which characteristic carries a signal. The draft names these charts but gives no formulas, so the standard ones are used (documented in ARCHITECTURE). None of the three has a capability report. Charts for processes that are not plain normal: the Shewhart chart with extended limits (draft 10.3.5.3; limits widened by the variation between subgroups, ANOVA estimate or the mean of the 3 largest and smallest subgroup means, u_out = 1.5) and the Pearson chart (draft 10.3.5.2; limits at the 0.135 % and 99.865 % quantiles of a Pearson curve of the skewness and kurtosis, types I, III, IV, V, VI and normal, or of the best fitted distribution). Both use only the limits, run and trend criteria. They are monitors; the analysis chart of a study still has no extended or Pearson variant. Not done: MCUSUM, standardised attribute charts (the p and u charts already follow the sample size), autocorrelated and multi-stream data, and the multivariate machine performance index of 8.5.2.

## Customer profiles

A profile holds what one customer agreed on and how that customer's report looks. Administrators make them in the Administration tab. Everybody can pick one in the analysis form.
- **Analysis**: risk α, confidence levels, handbook edition, stability decision, stability criteria and the target values per stage and characteristic class (p and pk). Only the settings you fill in count. When a profile is chosen the program applies them and the matching controls are locked. Through the API, fields you do not send take the profile's value, and a field you send with another value is kept and named as a deviation.
- **Report**: organisation line, title, form number and revision, footer, accent colour, logo (PNG or JPEG), default language, whether the optional elements 21 and 22 appear, extra fields of the customer (asked when the report is made) and fields that must be filled. The 20 required elements cannot be switched off.
- The report keeps a copy of the profile it used, and the archive holds the complete target table. A later change or deletion of the profile does not change a report that exists. Every change raises the revision number, and everything is in the audit trail.
- A logo must be a PNG or JPEG of at most 150 KB. An SVG is refused because it can hold script.

## Excel output

- Report: "Excel workbook" next to the HTML report (also in the Saved tab). Sheets: Summary, Report elements (the 20+2 elements as rows), Data (every value with status, marks and restarts), Control charts (plotted values and limits, with native charts), Check, Annex, Log. The language is the one of the report.
- The Check sheet recalculates n, mean, s, Cp/Cpk, their intervals and the ppm with Excel formulas from the Data sheet and compares them with the program's numbers. Change a value on the Data sheet and the check shows it. It covers the normal distribution only and says so when the report used a fitted distribution. Excel calculates the formulas when it opens the file. A viewer that does not calculate (mail preview, phone) shows the program's numbers in the Program column.
- Data: "Export Excel" in the Data tab gives the data with marks and the log. Texts typed by people are always stored as text, never as formulas. The CSV export guards them with a leading apostrophe, and the import takes it off.
- `pip install -e ".[excel]"` (or `web`, `dev`) brings openpyxl.

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

"""The report of the SPC health check: one self-contained HTML file with inline SVG (no script, no external file), and a JSON record.

Every dynamic string goes through `esc`. Print the HTML file to PDF from any browser.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape

from spc import __version__
from spc.healthcheck.texts import LANGUAGES, T

PERCENT = {"share", "share_between"}
SEV_CLASS = {"high": "bad", "medium": "warn", "low": "low", "info": "info", "ok": "ok"}

CSS = """
@page { size: A4; margin: 14mm; }
* { box-sizing: border-box; }
body { margin: 0; font: 10.5pt/1.5 "Noto Sans TC", "Microsoft JhengHei", "WenQuanYi Zen Hei", system-ui, sans-serif; color: #1c2430; background: #fff; }
main { max-width: 190mm; margin: 0 auto; padding: 8mm 4mm; }
h1 { font-size: 18pt; margin: 0; }
h2 { font-size: 13pt; margin: 18px 0 6px; border-bottom: 2px solid #1f5fbf; padding-bottom: 3px; }
h3 { font-size: 11.5pt; margin: 14px 0 4px; background: #eef2f8; border-left: 4px solid #1f5fbf; padding: 3px 8px; }
.sub { color: #59626e; margin: 2px 0 8px; }
.meta { color: #59626e; font-size: 9pt; overflow-wrap: anywhere; }
.privacy { border: 1px solid #1f7a4d; color: #1f7a4d; border-radius: 4px; padding: 4px 8px; display: inline-block; margin: 6px 0; font-size: 9.5pt; }
.summary { border: 2px solid #1f5fbf; border-radius: 6px; padding: 8px 12px; margin: 8px 0 12px; }
.summary li { margin: 3px 0; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th, td { border: 1px solid #d3d9e2; padding: 3px 7px; text-align: left; vertical-align: top; }
th { background: #f6f8fb; font-weight: 600; }
table.kv th { width: 34%; font-weight: 500; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
table.over td:nth-child(-n+4) { white-space: nowrap; }
.tag { display: inline-block; border-radius: 3px; padding: 0 6px; font-size: 9pt; font-weight: 600; white-space: nowrap; }
.bad { background: #fdecea; color: #b3261e; } .warn { background: #fff4e0; color: #8a5a00; } .low { background: #eef2f8; color: #3b4a5e; }
.info { background: #f3f4f6; color: #59626e; } .ok { background: #e7f5ee; color: #1f7a4d; }
.finding { border: 1px solid #d3d9e2; border-radius: 4px; padding: 5px 9px; margin: 5px 0; break-inside: avoid; }
.finding .t { font-weight: 600; }
.finding .do { margin-top: 2px; }
.finding .ref { color: #59626e; font-size: 8.5pt; }
.char { break-inside: avoid-page; }
.fig { border: 1px solid #d3d9e2; border-radius: 4px; padding: 4px; margin: 4px 0; }
svg { width: 100%; height: auto; display: block; }
.legend { font-size: 8.5pt; color: #59626e; }
.legend span { margin-right: 12px; white-space: nowrap; }
.sw { display: inline-block; width: 18px; height: 0; border-top: 2px solid; vertical-align: middle; margin-right: 4px; }
"""


def esc(x) -> str:
    return escape(str(x), quote=True)


def num(x, digits: int = 4) -> str:
    """A number with `digits` significant digits; whole numbers stay whole."""
    if x is None:
        return "—"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return str(x)
    return f"{float(x):.{digits}g}"


def _params(params: dict) -> dict:
    out = {}
    for key, value in params.items():
        if key in PERCENT and isinstance(value, (int, float)):
            out[key] = f"{100 * value:.0f} %"
        elif isinstance(value, float):
            out[key] = num(value, 3)
        else:
            out[key] = value
    return out


def finding_text(f: dict, L) -> tuple[str, str, str]:
    """(title, what was found, what to do) of a finding."""
    p = _params(f["params"])
    return L(f"f.{f['code']}"), L(f"f.{f['code']}.why", **p), L(f"f.{f['code']}.do", **p)


def _chart(r: dict, L, width: int = 760, height: int = 230) -> str:
    c = r["chart"]
    values = c["values"]
    if len(values) < 2:
        return ""
    lines = []  # (y, colour, dash, key)
    used, ref, spec = c["used"], c["reference"] or {}, c.get("spec") or {}
    for key in ("lcl", "ucl"):
        if used.get(key) is not None:
            lines.append((used[key], "#b3261e", "6 4"))
    if used.get("cl") is not None:
        lines.append((used["cl"], "#b3261e", "2 3"))
    for key in ("lcl", "ucl"):
        if ref.get(key) is not None:
            lines.append((ref[key], "#1f5fbf", ""))
    if ref.get("centre") is not None:
        lines.append((ref["centre"], "#1f5fbf", "2 3"))
    for key in ("lsl", "usl"):
        if spec.get(key) is not None:
            lines.append((spec[key], "#6b7280", "10 3 2 3"))
    ys = values + [y for y, _, _ in lines]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.06 or abs(hi) * 0.01 or 1.0
    lo, hi = lo - pad, hi + pad
    left, right, top, bottom = 58, 10, 8, 20
    w, h = width - left - right, height - top - bottom
    fx = lambda i: left + (w * i / (len(values) - 1))  # noqa: E731
    fy = lambda y: top + h * (hi - y) / (hi - lo)      # noqa: E731
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" xmlns="http://www.w3.org/2000/svg">',
           f'<rect x="{left}" y="{top}" width="{w}" height="{h}" fill="#fff" stroke="#d3d9e2"/>']
    for frac in (0.0, 0.5, 1.0):
        y = lo + (hi - lo) * frac
        out.append(f'<text x="{left - 5}" y="{fy(y) + 3:.1f}" font-size="10" text-anchor="end" fill="#59626e">{esc(num(y, 4))}</text>')
    for y, colour, dash in lines:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        out.append(f'<line x1="{left}" x2="{left + w}" y1="{fy(y):.1f}" y2="{fy(y):.1f}" stroke="{colour}" stroke-width="1.3"{d}/>')
    pts = " ".join(f"{fx(i):.1f},{fy(v):.1f}" for i, v in enumerate(values))
    out.append(f'<polyline points="{pts}" fill="none" stroke="#3b4a5e" stroke-width="1"/>')
    alarm = c.get("alarm") or [False] * len(values)
    r_dot = 2.2 if len(values) <= 150 else 1.4
    for i, v in enumerate(values):
        colour = "#b3261e" if alarm[i] else "#3b4a5e"
        out.append(f'<circle cx="{fx(i):.1f}" cy="{fy(v):.1f}" r="{r_dot + (1 if alarm[i] else 0)}" fill="{colour}"/>')
    out.append(f'<text x="{left}" y="{height - 5}" font-size="10" fill="#59626e">{esc(c["labels"][0])}</text>')
    out.append(f'<text x="{left + w}" y="{height - 5}" font-size="10" text-anchor="end" fill="#59626e">{esc(c["labels"][-1])}</text>')
    out.append("</svg>")
    legend = []
    if used.get("lcl") is not None or used.get("ucl") is not None:
        legend.append(f'<span><i class="sw" style="border-color:#b3261e;border-top-style:dashed"></i>{esc(L("chart.used"))}</span>')
    if ref:
        legend.append(f'<span><i class="sw" style="border-color:#1f5fbf"></i>{esc(L("chart.reference"))}</span>')
    if any(v is not None for v in spec.values()):
        legend.append(f'<span><i class="sw" style="border-color:#6b7280;border-top-style:dotted"></i>{esc(L("chart.spec"))}</span>')
    if any(alarm):
        legend.append(f'<span style="color:#b3261e">● {esc(L("chart.alarm"))}</span>')
    return f'<div class="fig">{"".join(out)}<div class="legend">{"".join(legend)}</div></div>'


def _pair(a, b, L) -> str:
    if a is None and b is None:
        return L("none")
    return f"{num(a)} {L('to')} {num(b)}"


def _facts(r: dict, L) -> str:
    rows = [(L("fact.values"), num(r["n_values"])),
            (L("fact.plotted", what=L("what.mean") if r["layout"] == "subgroups" else L("what.value")), num(r["k"])),
            (L("fact.mean"), num(r["mean"])), (L("fact.sd"), num(r["sd_total"]))]
    if r.get("period"):
        rows.append((L("fact.period"), f"{r['period'][0]} {L('to')} {r['period'][1]}"))
    lim = r["limits"]
    rows.append((L("fact.spec"), _pair(lim.get("lsl"), lim.get("usl"), L)))
    rows.append((L("fact.used"), _pair(lim.get("lcl"), lim.get("ucl"), L)))
    ref = r.get("reference") or {}
    if ref.get("lcl") is not None:
        rows.append((L("fact.reference", method=L(f"method.{ref['method']}")), _pair(ref["lcl"], ref["ucl"], L)))
    if r.get("index"):
        i = r["index"]
        ci = i.get("ci_pk") or [None, None]
        rows.append((L("fact.index"), f"{i['name']} = {num(i['pk'], 3)}" + (f"（{num(ci[0], 3)} {L('to')} {num(ci[1], 3)}）" if ci[0] is not None else "")))
    if r.get("resolution") is not None:
        rows.append((L("fact.resolution"), num(r["resolution"])))
    if "alarms" in r:
        text = num(r["alarms"])
        rows.append((L("fact.alarms", source=L(f"source.{r['alarm_source']}")), text))
    body = "".join(f"<tr><th>{esc(a)}</th><td>{esc(b)}</td></tr>" for a, b in rows)
    return f'<table class="kv">{body}</table>'


def _tag(severity: str, L) -> str:
    return f'<span class="tag {SEV_CLASS[severity]}">{esc(L("sev." + severity))}</span>'


def _summary_lines(summary: dict, L) -> list[str]:
    b = summary["by_worst"]
    lines = [L("sum.count", n=summary["characteristics"], high=b["high"], medium=b["medium"], ok=b["ok"] + b["low"])]
    if summary["blind"]:
        lines.append(L("sum.blind", n=summary["blind"]))
    if summary["noisy"]:
        lines.append(L("sum.noisy", n=summary["noisy"]))
    if summary["alarms"]:
        if summary["action_records"]:
            lines.append(L("sum.actions", alarms=summary["alarms"], without=summary["alarms_without_action"]))
        else:
            lines.append(L("sum.no_actions", alarms=summary["alarms"]))
    if summary["capability"]:
        lines.append(L("sum.capability", n=summary["capability"]))
    if summary["measurement"]:
        lines.append(L("sum.measurement", n=summary["measurement"]))
    if summary["too_few_data"]:
        lines.append(L("sum.few", n=summary["too_few_data"]))
    lines.append(L("sum.next") if b["high"] or b["medium"] else L("sum.none"))
    return lines


def summary_text(result: dict, lang: str = "zh-TW") -> str:
    """The summary as plain text (the command line prints it)."""
    return "\n".join("- " + line for line in _summary_lines(result["summary"], T(lang)))


def render_html(result: dict, info: dict, lang: str = "zh-TW", made: str | None = None) -> str:
    """The report. `info`: name, sha256 and rows of the file. `made`: the time printed in the report (default: now, UTC)."""
    if lang not in LANGUAGES:
        raise ValueError(f"lang must be one of {LANGUAGES}")
    L = T(lang)
    made = made or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    results = sorted(result["results"], key=lambda r: ("high", "medium", "low", "ok").index(r["worst"]))
    out = [f'<!doctype html><html lang="{esc(lang)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(L("title"))}</title><style>{CSS}</style></head><body><main>',
           f'<h1>{esc(L("title"))}</h1><p class="sub">{esc(L("subtitle"))}</p>',
           f'<p class="meta">{esc(L("file"))}: {esc(info.get("name", ""))} · {esc(L("rows"))}: {esc(info.get("rows", ""))} · {esc(L("made"))}: {esc(made)} · '
           f'{esc(L("program"))}: {esc(__version__)}<br>{esc(L("sha256"))}: {esc(info.get("sha256", ""))}</p>',
           f'<p class="privacy">{esc(L("privacy"))}</p>',
           f'<h2>{esc(L("summary"))}</h2><div class="summary"><ul>' + "".join(f"<li>{esc(x)}</li>" for x in _summary_lines(result["summary"], L)) + "</ul></div>",
           f'<h2>{esc(L("overview"))}</h2><table class="over"><tr><th>{esc(L("col.characteristic"))}</th><th>{esc(L("col.layout"))}</th><th>{esc(L("col.points"))}</th>'
           f'<th>{esc(L("col.result"))}</th><th>{esc(L("col.findings"))}</th></tr>']
    for r in results:
        layout = L("layout.subgroups", n=r["n"]) if r["layout"] == "subgroups" else L("layout.individuals")
        titles = "；".join(L(f"f.{f['code']}") for f in r["findings"] if f["severity"] in ("high", "medium")) or "—"
        out.append(f'<tr><td>{esc(r["name"])}</td><td>{esc(layout)}</td><td class="num">{esc(r["k"])}</td><td>{_tag(r["worst"], L)}</td><td>{esc(titles)}</td></tr>')
    out.append(f'</table><h2>{esc(L("detail"))}</h2>')
    for r in results:
        out.append(f'<div class="char"><h3>{esc(r["name"])} {_tag(r["worst"], L)}</h3>{_chart(r, L)}')
        for f in r["findings"]:
            title, why, do = finding_text(f, L)
            ref = f'<div class="ref">{esc(L("ref", ref=f["ref"]))}</div>' if f["ref"] else ""
            out.append(f'<div class="finding">{_tag(f["severity"], L)} <span class="t">{esc(title)}</span><div>{esc(why)}</div><div class="do">→ {esc(do)}</div>{ref}</div>')
        out.append(_facts(r, L) + "</div>")
    out.append(f'<h2>{esc(L("appendix"))}</h2>' + "".join(f"<p>{esc(L(k))}</p>" for k in ("app.what", "app.limits", "app.scope", "app.not")))
    rows = "".join(f'<tr><th>{esc(L("set." + key))}</th><td class="num">{esc(num(value))}</td></tr>' for key, value in result["settings"].items())
    out.append(f'<p><b>{esc(L("app.settings"))}</b></p><table class="kv">{rows}</table></main></body></html>')
    return "".join(out)


def to_json(result: dict, info: dict) -> str:
    return json.dumps({"program": "spc-check", "version": __version__, "file": info, **result}, ensure_ascii=False, indent=2, default=str)

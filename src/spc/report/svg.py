"""Figures for the report as SVG text.

Colors are fixed on purpose: a report is a print document, so it must look the same on screen,
on paper and in a PDF. Every dynamic string goes through `_esc`.
"""

from __future__ import annotations

import math
from typing import Sequence
from xml.sax.saxutils import escape

import numpy as np
from scipy.stats import norm

FONT = "Noto Sans TC, Microsoft JhengHei, WenQuanYi Zen Hei, sans-serif"
W, H = 640, 300
SERIES, ALARM, LIMIT, SPEC, TARGET, GREY, INK = "#1f5fbf", "#c62828", "#8a5a00", "#c62828", "#1f7a4d", "#8a8f98", "#26303b"
BAR_FILL = "#b9cdee"


def _esc(text) -> str:
    return escape(str(text), {'"': "&quot;"})


def _num(v: float) -> str:
    return f"{v:.2f}"


def nice_ticks(lo: float, hi: float, n: int = 5) -> tuple[list[float], int]:
    """Round tick positions and the number of decimals they need."""
    if not hi > lo:
        hi, lo = lo + 1.0, lo - 1.0
    raw = (hi - lo) / n
    mag = 10.0 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step - 1e-9)
    ticks = [round((first + i) * step, 12) for i in range(0, int((hi - lo) / step) + 2) if (first + i) * step <= hi + 1e-9]
    return ticks, max(0, -math.floor(math.log10(step) + 1e-9))


class _Frame:
    """Plot area with data-to-pixel mapping, axes, ticks and a title."""

    def __init__(self, title: str, x_lo: float, x_hi: float, y_lo: float, y_hi: float, *, ml=64, mr=24, mt=30, mb=44, width=W, height=H):
        if not x_hi > x_lo:
            x_lo, x_hi = x_lo - 1, x_hi + 1
        if not y_hi > y_lo:
            y_lo, y_hi = y_lo - 1, y_hi + 1
        self.title, self.ml, self.mr, self.mt, self.mb = title, ml, mr, mt, mb
        self.W, self.H = width, height
        self.x_lo, self.x_hi, self.y_lo, self.y_hi = x_lo, x_hi, y_lo, y_hi
        self.parts: list[str] = []

    def X(self, v: float) -> float:
        return self.ml + (v - self.x_lo) * (self.W - self.ml - self.mr) / (self.x_hi - self.x_lo)

    def Y(self, v: float) -> float:
        return self.mt + (self.y_hi - v) * (self.H - self.mt - self.mb) / (self.y_hi - self.y_lo)

    def add(self, s: str) -> None:
        self.parts.append(s)

    def line(self, x1, y1, x2, y2, color=INK, width=1.0, dash: str | None = None) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<line x1="{_num(x1)}" y1="{_num(y1)}" x2="{_num(x2)}" y2="{_num(y2)}" stroke="{color}" stroke-width="{width}"{d}/>')

    def text(self, x, y, s, size=10, anchor="start", color=INK, weight="normal") -> None:
        self.add(
            f'<text x="{_num(x)}" y="{_num(y)}" font-size="{size}" text-anchor="{anchor}" fill="{color}" '
            f'font-weight="{weight}" font-family="{_esc(FONT)}">{_esc(s)}</text>'
        )

    def axes(self, x_label: str, y_label: str, x_ticks=None, y_ticks=None, x_dec=None, y_dec=None, y_text=None) -> None:
        l, r, t, b = self.ml, self.W - self.mr, self.mt, self.H - self.mb
        self.line(l, t, l, b, "#9aa3ae")
        self.line(l, b, r, b, "#9aa3ae")
        yt, yd = nice_ticks(self.y_lo, self.y_hi) if y_ticks is None else (y_ticks, y_dec or 0)
        for v in yt:
            if self.y_lo <= v <= self.y_hi:
                self.line(l - 3, self.Y(v), l, self.Y(v), "#9aa3ae")
                self.line(l, self.Y(v), r, self.Y(v), "#e6e9ee", 0.6)
                self.text(l - 6, self.Y(v) + 3.5, y_text(v) if y_text else f"{v:.{yd}f}", 9, "end", "#59626e")
        xt, xd = nice_ticks(self.x_lo, self.x_hi) if x_ticks is None else (x_ticks, x_dec or 0)
        for v in xt:
            if self.x_lo <= v <= self.x_hi:
                self.line(self.X(v), b, self.X(v), b + 3, "#9aa3ae")
                self.text(self.X(v), b + 15, f"{v:.{xd}f}", 9, "middle", "#59626e")
        self.text((l + r) / 2, b + 28, x_label, 10, "middle", "#59626e")
        self.add(
            f'<text x="12" y="{_num((t + b) / 2)}" font-size="10" text-anchor="middle" fill="#59626e" '
            f'font-family="{_esc(FONT)}" transform="rotate(-90 12 {_num((t + b) / 2)})">{_esc(y_label)}</text>'
        )

    def vline(self, v: float, color: str, label: str, dash: str | None = None, row: int = 0) -> None:
        if not self.x_lo <= v <= self.x_hi:
            return
        self.line(self.X(v), self.mt, self.X(v), self.H - self.mb, color, 1.4, dash)
        self.text(self.X(v), self.mt - 4 - 10 * row, label, 9, "middle", color, "bold")

    def hline(self, v: float, color: str, label: str, dash: str | None = None, label_x: float | None = None) -> None:
        if not self.y_lo <= v <= self.y_hi:
            return
        self.line(self.ml, self.Y(v), self.W - self.mr, self.Y(v), color, 1.2, dash)
        self.text(label_x if label_x is not None else self.W - self.mr - 2, self.Y(v) - 3, label, 9, "end", color)

    def legend(self, items: Sequence[tuple[str, str, str]]) -> None:
        """items: (kind, color, label). kind is 'dot', 'ring', 'line' or 'dash'."""
        x = self.ml + 4
        y = self.H - 6
        for kind, color, label in items:
            if kind == "dot":
                self.add(f'<circle cx="{_num(x + 4)}" cy="{_num(y - 3)}" r="3" fill="{color}"/>')
            elif kind == "ring":
                self.add(f'<circle cx="{_num(x + 4)}" cy="{_num(y - 3)}" r="3" fill="#fff" stroke="{color}" stroke-width="1.4"/>')
            else:
                self.line(x, y - 3, x + 10, y - 3, color, 1.6, "4 3" if kind == "dash" else None)
            self.text(x + 14, y, label, 9, "start", "#59626e")
            x += 26 + 5.4 * sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(label))

    def render(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.W} {self.H}" role="img" aria-label="{_esc(self.title)}" '
            f'width="100%" font-family="{_esc(FONT)}">'
            f'<title>{_esc(self.title)}</title><rect width="{self.W}" height="{self.H}" fill="#ffffff"/>'
            + f'<text x="{self.ml}" y="14" font-size="11" font-weight="bold" fill="{INK}" font-family="{_esc(FONT)}">{_esc(self.title)}</text>'
            + "".join(self.parts)
            + "</svg>"
        )


def _pad(lo: float, hi: float, frac: float = 0.06) -> tuple[float, float]:
    span = (hi - lo) or 1.0
    return lo - span * frac, hi + span * frac


# ------------------------------------------------------------------------------------------ figures

def histogram(values, lsl, usl, target, mean, sd, labels: dict, width=430, height=300, dist=None) -> str:
    """`dist` (an object with pdf) replaces the normal curve when the indices use a fitted distribution."""
    x = np.asarray(values, dtype=float)
    n = x.size
    k = int(min(40, max(5, math.ceil(1 + 3.322 * math.log10(max(n, 2))))))
    counts, edges = np.histogram(x, bins=k)
    xs = [x.min(), x.max()] + [v for v in (lsl, usl, target) if v is not None]
    x_lo, x_hi = _pad(min(xs), max(xs))
    bin_w = edges[1] - edges[0]
    curve = None
    if dist is not None:
        grid = np.linspace(x_lo, x_hi, 160)
        with np.errstate(all="ignore"):
            dens = np.nan_to_num(np.asarray(dist.pdf(grid), dtype=float), nan=0.0, posinf=0.0) * n * bin_w
        curve = (grid, dens)
        top = float(dens.max())
    else:
        top = norm.pdf(mean, mean, sd) * n * bin_w if sd > 0 else 0
    y_hi = max(counts.max(), top) * 1.12
    f = _Frame(labels["title"], x_lo, x_hi, 0, y_hi, ml=48, mr=14, width=width, height=height)
    for c, a, b in zip(counts, edges[:-1], edges[1:]):
        if c:
            f.add(
                f'<rect x="{_num(f.X(a))}" y="{_num(f.Y(c))}" width="{_num(f.X(b) - f.X(a))}" height="{_num(f.Y(0) - f.Y(c))}" '
                f'fill="{BAR_FILL}" stroke="{SERIES}" stroke-width="0.8"/>'
            )
    if curve is not None:
        pts = " ".join(f"{_num(f.X(g))},{_num(f.Y(min(d, y_hi)))}" for g, d in zip(*curve))
        f.add(f'<polyline points="{pts}" fill="none" stroke="{LIMIT}" stroke-width="1.4"/>')
    elif sd > 0:
        grid = np.linspace(max(x_lo, mean - 4 * sd), min(x_hi, mean + 4 * sd), 120)
        pts = " ".join(f"{_num(f.X(g))},{_num(f.Y(norm.pdf(g, mean, sd) * n * bin_w))}" for g in grid)
        f.add(f'<polyline points="{pts}" fill="none" stroke="{LIMIT}" stroke-width="1.4"/>')
    f.axes(labels["x"], labels["y"], y_ticks=None)
    if lsl is not None:
        f.vline(lsl, SPEC, "LSL")
    if usl is not None:
        f.vline(usl, SPEC, "USL")
    if target is not None:
        f.vline(target, TARGET, labels["target"], "5 3", row=1)
    f.legend([("dot", BAR_FILL, labels["bars"]), ("line", LIMIT, labels["fit"]), ("line", SPEC, labels["spec"])])
    return f.render()


def run_chart(values, states, lsl, usl, target, labels: dict, width=W, height=260) -> str:
    """states: 0 used, 1 marked invalid, 2 not used (for example a subgroup left out)."""
    x = np.asarray(values, dtype=float)
    st = np.asarray(states, dtype=int)
    ys = [x.min(), x.max()] + [v for v in (lsl, usl, target) if v is not None]
    y_lo, y_hi = _pad(min(ys), max(ys))
    f = _Frame(labels["title"], 1, max(len(x), 2), y_lo, y_hi, mr=40, width=width, height=height)
    used = [(i + 1, v) for i, v in enumerate(x) if st[i] == 0]
    if len(used) > 1:
        f.add(f'<polyline points="{" ".join(f"{_num(f.X(i))},{_num(f.Y(v))}" for i, v in used)}" fill="none" stroke="{SERIES}" stroke-width="0.8" opacity="0.6"/>')
    r = 2.6 if len(x) <= 400 else 1.6
    for i, v in enumerate(x):
        cx, cy = _num(f.X(i + 1)), _num(f.Y(v))
        if st[i] == 0:
            f.add(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{SERIES}"/>')
        elif st[i] == 1:
            f.add(f'<circle cx="{cx}" cy="{cy}" r="{r + 1}" fill="#fff" stroke="{ALARM}" stroke-width="1.5"/>')
        else:
            f.add(f'<circle cx="{cx}" cy="{cy}" r="{r + 0.6}" fill="#fff" stroke="{GREY}" stroke-width="1.2"/>')
    f.axes(labels["x"], labels["y"], x_ticks=None, x_dec=0)
    if usl is not None:
        f.hline(usl, SPEC, "USL")
    if lsl is not None:
        f.hline(lsl, SPEC, "LSL")
    if target is not None:
        f.hline(target, TARGET, labels["target"], "5 3")
    legend = [("dot", SERIES, labels["used"])]
    if (st == 1).any():
        legend.append(("ring", ALARM, labels["invalid"]))
    if (st == 2).any():
        legend.append(("ring", GREY, labels["unused"]))
    f.legend(legend)
    return f.render()


def probability_plot(values, mean, sd, lsl, usl, labels: dict, width=430, height=300, dist=None) -> str:
    """With `dist` the fitted line is the curve z = Phi^-1(F(x)) of that distribution (straight only for a normal one)."""
    x = np.sort(np.asarray(values, dtype=float))
    n = x.size
    pp = (np.arange(1, n + 1) - 0.375) / (n + 0.25)  # Blom plotting positions
    z = norm.ppf(pp)
    x_lo, x_hi = _pad(x.min(), x.max())
    z_lim = max(3.2, float(np.abs(z).max()) + 0.3)
    f = _Frame(labels["title"], x_lo, x_hi, -z_lim, z_lim, ml=48, mr=14, width=width, height=height)
    ticks = [0.1, 1, 5, 25, 50, 75, 95, 99, 99.9]  # percent
    f.axes(
        labels["x"], labels["y"],
        y_ticks=[float(norm.ppf(p / 100)) for p in ticks],
        y_text=lambda zz: f"{100 * norm.cdf(zz):.3g}",
    )
    if dist is not None:
        grid = np.linspace(x_lo, x_hi, 160)
        with np.errstate(all="ignore"):
            zz = norm.ppf(np.clip(np.asarray(dist.cdf(grid), dtype=float), 1e-12, 1 - 1e-12))
        zz = np.clip(np.nan_to_num(zz, nan=0.0), -z_lim, z_lim)
        pts = " ".join(f"{_num(f.X(g))},{_num(f.Y(zv))}" for g, zv in zip(grid, zz))
        f.add(f'<polyline points="{pts}" fill="none" stroke="{LIMIT}" stroke-width="1.4"/>')
    elif sd > 0:
        f.line(f.X(mean - z_lim * sd), f.Y(-z_lim), f.X(mean + z_lim * sd), f.Y(z_lim), LIMIT, 1.4)
    r = 2.4 if n <= 400 else 1.5
    for xi, zi in zip(x, z):
        f.add(f'<circle cx="{_num(f.X(xi))}" cy="{_num(f.Y(zi))}" r="{r}" fill="{SERIES}"/>')
    if lsl is not None:
        f.vline(lsl, SPEC, "LSL")
    if usl is not None:
        f.vline(usl, SPEC, "USL")
    f.legend([("dot", SERIES, labels["data"]), ("line", LIMIT, labels["fit"])])
    return f.render()


def control_chart(part: dict, labels: dict, width=W, height=250) -> str:
    """part: values, labels, lcl, center, ucl, alarms (list of {index, rule}) from the analysis result."""
    v = np.asarray(part["values"], dtype=float)
    ys = list(v) + [t for t in (part["lcl"], part["center"], part["ucl"]) if t is not None]
    y_lo, y_hi = _pad(min(ys), max(ys), 0.08)
    f = _Frame(labels["title"], 1, max(len(v), 2), y_lo, y_hi, mr=96, width=width, height=height)
    f.add(f'<polyline points="{" ".join(f"{_num(f.X(i + 1))},{_num(f.Y(a))}" for i, a in enumerate(v))}" fill="none" stroke="{SERIES}" stroke-width="1.2"/>')
    alarms = {a["index"] for a in part["alarms"]}
    for i, a in enumerate(v):
        c = ALARM if i in alarms else SERIES
        f.add(f'<circle cx="{_num(f.X(i + 1))}" cy="{_num(f.Y(a))}" r="{3 if len(v) <= 300 else 1.7}" fill="{c}"/>')
    f.axes(labels["x"], labels["y"], x_ticks=None, x_dec=0)
    for key, color, name, dash in (("ucl", LIMIT, labels["ucl"], "6 4"), ("center", "#59626e", labels["cl"], None), ("lcl", LIMIT, labels["lcl"], "6 4")):
        t = part[key]
        if t is not None and y_lo <= t <= y_hi:
            f.line(f.ml, f.Y(t), W - f.mr, f.Y(t), color, 1.2, dash)
            f.text(W - f.mr + 4, f.Y(t) + 3.5, f"{name} {t:.5g}", 9, "start", color)
    items = [("dot", SERIES, labels["point"])]
    if alarms:
        items.append(("dot", ALARM, labels["alarm"]))
    f.legend(items)
    return f.render()

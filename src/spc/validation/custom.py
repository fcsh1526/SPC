"""Reference cases of the user: validation for the intended use (draft 11.2).

A case holds the values, the settings of the analysis and the results that the user expects, with the source of those results
(for example an example of ISO/TR 11462-3, a customer sample or an earlier system). It is run with exactly those settings.
"""

from __future__ import annotations

import math
from dataclasses import fields
from typing import Any

from spc.data import Dataset
from spc.service import AnalysisRequest, analyze
from spc.validation.checks import Check

MAX_VALUES = 5000
MAX_EXPECTED = 100
_ALLOWED_REQUEST = {f.name for f in fields(AnalysisRequest)}


def _text(d: dict, key: str, limit: int, required=0) -> str:
    v = d.get(key, "")
    if not isinstance(v, str) or len(v) > limit:
        raise ValueError(f"{key} must be text of at most {limit} characters")
    v = v.strip()
    if len(v) < required:
        raise ValueError(f"{key} is required")
    return v


def validate_case(data: dict[str, Any]) -> dict:
    allowed = {"name", "description", "source", "values", "subgroup_size", "request", "expected"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError(f"unknown setting(s): {sorted(set(data) - allowed) if isinstance(data, dict) else 'not an object'}")
    name = _text(data, "name", 100, required=1)
    values = data.get("values")
    if (not isinstance(values, list) or not 5 <= len(values) <= MAX_VALUES
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)):
        raise ValueError(f"values must be a list of 5 to {MAX_VALUES} finite numbers")
    size = data.get("subgroup_size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or not 2 <= size <= 100 or len(values) % size):
        raise ValueError("subgroup_size must be a whole number from 2 to 100 that divides the number of values")
    request = data.get("request", {})
    if not isinstance(request, dict) or set(request) - _ALLOWED_REQUEST:
        raise ValueError(f"request: unknown setting(s) {sorted(set(request) - _ALLOWED_REQUEST) if isinstance(request, dict) else ''}")
    try:
        AnalysisRequest(**request)
    except TypeError as exc:
        raise ValueError(f"request: {exc}") from None
    exp = data.get("expected")
    if not isinstance(exp, list) or not 1 <= len(exp) <= MAX_EXPECTED:
        raise ValueError(f"expected must list 1 to {MAX_EXPECTED} results")
    out = []
    for i, e in enumerate(exp, start=1):
        if not isinstance(e, dict) or set(e) - {"path", "value", "tol"}:
            raise ValueError(f"expected result {i}: use path, value and tol")
        path, value, tol = e.get("path"), e.get("value"), e.get("tol", 1e-6)
        if not isinstance(path, str) or not 1 <= len(path) <= 100:
            raise ValueError(f"expected result {i}: path must be text such as indices.pk")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"expected result {i}: value must be a number")
        if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not 0 <= tol <= 1:
            raise ValueError(f"expected result {i}: tol is a relative tolerance from 0 to 1")
        out.append({"path": path.strip(), "value": float(value), "tol": float(tol)})
    return {"name": name, "description": _text(data, "description", 2000), "source": _text(data, "source", 500, required=3),
            "values": [float(v) for v in values], "subgroup_size": size, "request": request, "expected": out}


def lookup(result: Any, path: str) -> Any:
    """indices.pk, chart.location.ucl, indices.ci_p.0 : keys of the result, numbers for list positions."""
    node = result
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def run_case(case_id: int, case: dict) -> list[Check]:
    sub = [str(i // case["subgroup_size"]) for i in range(len(case["values"]))] if case["subgroup_size"] else None
    try:
        result = analyze(Dataset.from_values(case["values"], subgroup=sub), AnalysisRequest(**case["request"]))
    except Exception as exc:  # the case could not be analysed: every expected result fails with the reason
        return [Check(f"C{case_id}-{i}", case["name"], "req.custom", case["source"], e["value"], None, e["tol"], f"{e['path']}: {type(exc).__name__}: {exc}")
                for i, e in enumerate(case["expected"], start=1)]
    out = []
    for i, e in enumerate(case["expected"], start=1):
        actual = lookup(result, e["path"])
        usable = isinstance(actual, (int, float)) and not isinstance(actual, bool)
        out.append(Check(f"C{case_id}-{i}", case["name"], "req.custom", case["source"], e["value"], float(actual) if usable else None, e["tol"],
                         e["path"] + ("" if usable else ": no such number in the result")).with_abs(1e-12))
    return out

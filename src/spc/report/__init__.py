"""Report output: the 20+2 elements of the AIAG-VDA SPC report as HTML, plus a JSON archive."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from spc.data import Dataset
from spc.report.archive import Reproduction, build_archive, reproduce, verify_archive
from spc.report.builder import Report, ReportError, build_report
from spc.report.meta import ReportMeta
from spc.report.render import render_html
from spc.report.texts import LANGUAGES
from spc.service import AnalysisRequest, analyze_detailed


@dataclass(frozen=True)
class GeneratedReport:
    report_id: str
    language: str
    html: str
    archive: dict


def generate(
    dataset: Dataset,
    request: AnalysisRequest,
    meta: ReportMeta | None = None,
    language: str = "en",
    *,
    now: str | None = None,
    report_id: str | None = None,
) -> GeneratedReport:
    """Run the analysis once, archive it, and lay the report out from the same result."""
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {LANGUAGES}")
    meta = (meta or ReportMeta()).validate()
    outcome = analyze_detailed(dataset, request)
    if outcome.result["indices"] is None:
        raise ReportError("spec_missing", "a report needs at least one specification limit")
    created = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rid = report_id or uuid.uuid4().hex[:12]
    archive = build_archive(dataset, request, meta, outcome.result, created_at=created, report_id=rid, language=language)
    report = build_report(
        dataset, request, meta, language, generated_at=created, report_id=rid,
        archive_digest=archive["integrity"]["digest"], outcome=outcome,
    )
    return GeneratedReport(rid, language, render_html(report), archive)


__all__ = [
    "GeneratedReport", "Report", "ReportError", "ReportMeta", "Reproduction", "build_archive", "build_report",
    "generate", "render_html", "reproduce", "verify_archive",
]

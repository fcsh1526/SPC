"""docs/TRACEABILITY.md must not point at anything that is not there: every file, function and test it names must exist."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "docs" / "TRACEABILITY.md").read_text(encoding="utf-8")
STATUS = {"✅": "implemented", "🔶": "interpretation", "◐": "partial", "⬜": "missing", "➖": "not software"}


def rows_of_the_chapters():
    """The rows of the chapter tables: a section number, a requirement, a status, the program, the proof and a remark."""
    out = []
    for line in TEXT.splitlines():
        if re.match(r"^\| \d", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 6:
                out.append(cells)
    return out


def references():
    for token in re.findall(r"`([^`]+)`", TEXT):
        if token.startswith(("src/", "tests/", "docs/")):
            yield token


def test_every_named_file_function_and_test_exists():
    problems = []
    for token in references():
        path, _, name = token.partition("::")
        target = ROOT / path
        if not target.exists():
            problems.append(f"missing file: {token}")
        elif name:
            if not re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b", target.read_text(encoding="utf-8"), re.M):
                problems.append(f"missing name: {token}")
    assert problems == []


def test_there_are_many_references_and_every_row_has_a_status():
    refs = list(references())
    assert len(refs) > 150
    rows = rows_of_the_chapters()
    assert len(rows) > 80
    for cells in rows:
        assert cells[2] in STATUS, cells


def test_the_summary_line_counts_the_rows():
    counts = {s: 0 for s in STATUS}
    for cells in rows_of_the_chapters():
        counts[cells[2]] += 1
    expected = "，".join(f"{s} {counts[s]}" for s in STATUS)
    assert f"統計：{expected}" in TEXT, expected

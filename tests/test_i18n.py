"""The two language files must stay in step with each other and with the code that uses them."""

import json
import re
from pathlib import Path

import pytest

from spc.core import notes
from spc.core.stability import Stability

ROOT = Path(__file__).resolve().parent.parent / "src" / "spc"
STATIC = ROOT / "web" / "static"
LANGS = ("en", "zh-TW")


def load(lang):
    return json.loads((STATIC / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def messages():
    return {lang: load(lang) for lang in LANGS}


def placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


def test_both_languages_have_the_same_keys(messages):
    en, zh = set(messages["en"]), set(messages["zh-TW"])
    assert en - zh == set(), f"missing in zh-TW: {sorted(en - zh)}"
    assert zh - en == set(), f"missing in en: {sorted(zh - en)}"


def test_no_empty_texts_and_placeholders_match(messages):
    for key, en_text in messages["en"].items():
        zh_text = messages["zh-TW"][key]
        assert en_text.strip() and zh_text.strip(), key
        assert placeholders(en_text) == placeholders(zh_text), f"placeholders differ in {key}"


def test_chinese_texts_are_not_copies_of_english(messages):
    copies = [k for k, v in messages["zh-TW"].items() if v == messages["en"][k] and re.search(r"[a-z]{4,}", v)]
    assert copies == [], copies


def test_chinese_uses_traditional_not_simplified_forms(messages):
    simplified = set("设计运执处据实验图线标为这样单变误时间数组与关键区维护统计状态规则间对应输进击还须错报档案战应")
    simplified -= set("與關鍵區維護統計狀態規則間對應輸進擊還須錯報檔案戰應")  # no overlap guard, kept explicit
    found = {k for k, v in messages["zh-TW"].items() if simplified & set(v)}
    assert found == set(), found


def page_text():
    return (STATIC / "app.js").read_text(encoding="utf-8") + (STATIC / "index.html").read_text(encoding="utf-8")


def used_in_js():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    return set(re.findall(r"""\bt\(\s*["']([\w.\-]+)["']""", js))


def used_in_html():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return set(re.findall(r'data-i18n(?:-placeholder)?="([\w.\-]+)"', html))


def test_every_key_used_by_the_page_exists(messages):
    keys = {k for k in used_in_js() | used_in_html() if not k.endswith((".", "_"))}  # skip dynamic prefixes
    assert keys, "no keys found: the scan pattern is broken"
    missing = sorted(k for k in keys if k not in messages["en"])
    assert missing == [], missing


def test_every_key_in_the_files_is_used(messages):
    """Dead texts hide drift. Dynamic prefixes are listed here on purpose."""
    dynamic = (
        "issue.", "error.", "warn.", "alarmrule.", "result.class_", "result.verdict_", "result.kind_",
        "result.series_variation_", "analysis.model_", "analysis.class_", "analysis.chart_",
        "analysis.stage_", "result.names_reason_", "role.", "dist.", "pkey.", "mon.chart_", "mon.kind_", "mon.step_", "mon.result_", "mon.quadrant_", "mon.review_", "study.item.", "study.res.", "tm.reason_", "tm.conf_", "tm.when_", "tm.hint_", "msa.res.", "msa.check_", "msa.eff_", "msa.gate_", "msa.status_", "msa.verdict_", "msa.kind_", "msa.basis_", "msa.data_", "study.eff_", "study.status_", "plan.chk.", "plan.phase_", "plan.kind_", "plan.control_", "roles.role_", "roles.comp_", "roles.level_", "roles.resp_",
    )
    text = page_text()
    unused = sorted(
        k for k in messages["en"]
        if f'"{k}"' not in text and f"'{k}'" not in text and not k.startswith(dynamic)
    )
    assert unused == [], unused


def test_every_backend_code_has_a_text(messages):
    en = messages["en"]
    sources = [*(ROOT / "api").glob("*.py"), *(ROOT / "auth").glob("*.py"), *(ROOT / "monitor").glob("*.py"), *(ROOT / "study").glob("*.py"), *(ROOT / "msa").glob("*.py"), *(ROOT / "plan").glob("*.py")]
    api = "\n".join(f.read_text(encoding="utf-8") for f in sources)
    api_codes = set(re.findall(r'(?:ApiError|AuthError)\(\s*\d+,\s*"(\w+)"', api))
    api_codes |= set(re.findall(r'MonitorError\(\s*"(\w+)"', api))
    api_codes |= set(re.findall(r'(?:StudyError|MsaProblem|PlanError)\(\s*"(\w+)"', api))
    api_codes |= set(re.findall(r'_error\(\s*\d+,\s*"(\w+)"', api))
    api_codes |= set(re.findall(r'PasswordPolicyError\(\s*"(\w+)"', api))
    api_codes |= {"not_invalid", "already_invalid"}  # chosen through a conditional expression
    api_codes -= {"", "import_failed"}  # shown with its own title
    api_codes |= {"report_needs_spec", "report_not_found", "archive_unreadable"}  # chosen through a variable
    assert {"not_authenticated", "csrf_failed", "forbidden", "invalid_credentials", "login_locked", "last_admin",
            "password_too_short", "password_change_required"} <= api_codes
    for code in api_codes | {"import_failed"}:
        assert f"error.{code}" in en, f"error.{code}"

    csv_src = (ROOT / "data" / "csv_io.py").read_text(encoding="utf-8")
    issue_codes = set(re.findall(r'ImportIssue\([^"\n]*"(\w+)",\s*[f]?"', csv_src))
    issue_codes |= set(re.findall(r'ImportIssue\([^\n]*?,\s*"([a-z_]+)",\s*f?"', csv_src))
    assert {"bad_number", "missing_value", "missing_column", "encoding"} <= issue_codes
    for code in issue_codes:
        assert f"issue.{code}" in en, f"issue.{code}"

    svc = (ROOT / "service" / "analysis.py").read_text(encoding="utf-8")
    warn_codes = set(re.findall(r'_warn\(\s*warnings,\s*"(\w+)"', svc))
    assert {"non_normal", "spec_missing", "dropped_subgroups"} <= warn_codes
    for code in warn_codes:
        assert f"warn.{code}" in en, f"warn.{code}"
    for code in notes._TEXT:
        prefix = "attribute" if code in ("small_sample", "size_varies") else "data"
        assert f"warn.{prefix}_{code}" in en, f"warn.{prefix}_{code}"

    rules_src = (ROOT / "core" / "rules.py").read_text(encoding="utf-8")
    rule_names = set(re.findall(r'Violation\([^,]+,\s*"(\w+)"\)', rules_src))
    assert {"beyond_limits", "run", "trend", "middle_third"} <= rule_names
    for name in rule_names:
        assert f"alarmrule.{name}" in en, f"alarmrule.{name}"

    for s in Stability:
        if s is not Stability.UNKNOWN:
            assert f"result.class_{s.value}" in en or s is Stability.UNKNOWN
    assert "result.class_unknown" in en and "result.class_not_tested" in en
    for v in ("meets", "meets_estimate_only", "fails"):
        assert f"result.verdict_{v}" in en
    for kind in ("xbar-s", "xbar-r", "imr"):
        assert f"result.kind_{kind}" in en and f"result.series_variation_{kind}" in en


def test_every_key_like_string_in_the_page_code_is_a_real_key(messages):
    """Keys listed in arrays or passed through variables are not found by the t("...") scan. Check them here."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    prefixes = ("mon.", "nav.", "login.", "user.", "password.", "saved.", "admin.", "data.", "import.", "result.", "report.", "tools.", "analysis.", "pkey.")
    quoted = set(re.findall(r'"((?:%s)[\w.\-]*)"' % "|".join(re.escape(p) for p in prefixes), js))
    missing = sorted(k for k in quoted if not k.endswith((".", "_")) and k not in messages["en"])
    assert missing == [], missing

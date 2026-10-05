"""Browser test of the real page. Skipped when Playwright or a Chromium binary is not available.

Set SPC_CHROMIUM to the path of a Chromium executable if it is not found automatically.
"""

import glob
import os
import socket
import threading
import time

import numpy as np
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
import uvicorn  # noqa: E402

from spc.data import Dataset  # noqa: E402
from tests.conftest import PASSWORD, make_app  # noqa: E402


def find_chromium():
    candidates = [os.environ.get("SPC_CHROMIUM", "")]
    candidates += sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return next((c for c in candidates if c and os.path.exists(c)), None)


@pytest.fixture(scope="module")
def app():
    return make_app(max_upload=20 * 1024 * 1024)


def sign_in(page, user="eng", password=PASSWORD, wait=True):
    page.fill("#login-user", user)
    page.fill("#login-pass", password)
    page.click("#login-form button[type=submit]")
    if wait:  # the next step must not run before the session exists (a person cannot be that fast either)
        page.wait_for_selector("#user-label", state="visible")


@pytest.fixture(scope="module")
def server(app):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    exe = find_chromium()
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()
        except Exception as exc:  # no usable browser on this machine
            pytest.skip(f"no Chromium available: {exc}")
        yield b
        b.close()


def sample(tmp_path):
    rng = np.random.default_rng(42)
    lines = ["批號,直徑"]
    for g in range(25):
        for j in range(5):
            v = 10 + rng.normal(0, 0.1) + (1.2 if (g, j) == (11, 3) else 0)
            lines.append(f"B{g + 1:02d},{v:.4f}")
    path = tmp_path / "sample.csv"
    path.write_bytes(("\n".join(lines) + "\n").encode("cp950"))
    return path


def test_full_flow_in_both_languages(server, browser, tmp_path):
    expect = playwright_sync.expect
    ctx = browser.new_context(viewport={"width": 1200, "height": 900}, locale="zh-TW")
    page = ctx.new_page()
    problems = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))

    page.goto(server)
    expect(page.locator("#login h2")).to_have_text("登入")  # language from the browser
    expect(page.locator("#app")).to_be_hidden()  # nothing of the program shows before the login
    sign_in(page)
    expect(page.locator("nav.tabs button[data-tab=import]")).to_have_text("1. 匯入")

    # import a Big5 file: columns are suggested from the content
    page.set_input_files("#file", str(sample(tmp_path)))
    expect(page.locator("#detected")).to_contain_text("cp950")
    expect(page.locator("#col-value")).to_have_value("直徑")
    expect(page.locator("#col-subgroup")).to_have_value("批號")
    page.click("#import-btn")
    expect(page.locator("#data-counts")).to_contain_text("共 125 筆")

    # a suspect value is only a hint. Marking needs a reason and a person.
    page.click("#suspect-btn")
    expect(page.locator("#suspect-msg")).to_contain_text("1 個可疑值")
    page.click("#suspect-select")
    page.click("#mark-btn")
    expect(page.locator("#errors")).to_contain_text("必須填寫理由")
    page.click("#errors button")
    expect(page.locator("#person-note")).to_contain_text("Eva Engineer (eng)")  # the person comes from the login
    page.fill("#reason", "typing error")
    page.click("#mark-btn")
    expect(page.locator("#data-counts")).to_contain_text("標記無效 1 筆")

    # analysis
    page.click("#to-analysis")
    expect(page.locator("#a-size-wrap")).to_be_hidden()  # the data has subgroup labels
    page.fill("#a-lsl", "9.5")
    page.fill("#a-usl", "10.5")
    page.select_option("#a-model", "A1")
    page.select_option("#a-class", "major")
    page.click("#run-btn")
    expect(page.locator("#r-names")).to_have_text("Cp 與 Cpk")
    expect(page.locator("#chart-loc svg")).to_be_visible()
    expect(page.locator("#chart-var svg")).to_be_visible()
    labels = page.locator("#chart-loc svg text.limit-label").all_text_contents()
    assert any(t.startswith("管制上限") for t in labels)

    # the language can change without losing the result
    page.select_option("#lang", "en")
    expect(page.locator("#r-names")).to_have_text("Cp and Cpk")
    expect(page.locator("#r-targets")).to_contain_text("Met, also at the lower confidence limit")

    # without a model the same data is named Pp/Ppk
    page.select_option("#a-model", "")
    page.click("#run-btn")
    expect(page.locator("#r-names")).to_have_text("Pp and Ppk")

    # the only console error allowed is the 400 that the empty-reason click caused on purpose
    assert [p for p in problems if "400" not in p] == [], problems
    ctx.close()


def test_page_does_not_overflow_on_a_phone(server, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 800}, locale="en")
    page = ctx.new_page()
    page.goto(server)
    playwright_sync.expect(page.locator("#login-form")).to_be_visible()
    assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    sign_in(page)
    playwright_sync.expect(page.locator("nav.tabs")).to_be_visible()
    assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    ctx.close()


def test_user_text_is_never_run_as_html(server, browser, tmp_path):
    """A subgroup label that looks like HTML must show as text, not run."""
    path = tmp_path / "xss.csv"
    path.write_bytes("lot,v\n<img src=x onerror=window.__pwned=1>,1.0\n<img src=x onerror=window.__pwned=1>,1.1\nb,1.2\nb,1.3\n".encode())
    ctx = browser.new_context(locale="en")
    page = ctx.new_page()
    page.goto(server)
    sign_in(page)
    page.set_input_files("#file", str(path))
    page.select_option("#col-value", "v")
    page.select_option("#col-subgroup", "lot")
    page.click("#import-btn")
    playwright_sync.expect(page.locator("#rows")).to_contain_text("<img src=x onerror")
    assert page.evaluate("() => window.__pwned === undefined")
    ctx.close()


def test_report_and_archive_check_in_the_browser(server, browser, tmp_path):
    expect = playwright_sync.expect
    ctx = browser.new_context(viewport={"width": 1200, "height": 900}, locale="en")
    page = ctx.new_page()
    problems = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))

    page.goto(server)
    sign_in(page)
    page.set_input_files("#file", str(sample(tmp_path)))
    page.select_option("#col-value", "直徑")
    page.select_option("#col-subgroup", "批號")
    page.click("#import-btn")
    expect(page.locator("#data-counts")).to_contain_text("125 values")

    # the sample holds one wrong value. Left in, the charts alarm and the indices cannot be called Cp/Cpk.
    page.click("#suspect-btn")
    expect(page.locator("#suspect-msg")).to_contain_text("1 suspect")
    page.click("#suspect-select")
    page.fill("#reason", "typing error")
    page.click("#mark-btn")
    expect(page.locator("#data-counts")).to_contain_text("1 marked invalid")

    page.click("#to-analysis")
    page.fill("#a-lsl", "9.5")
    page.fill("#a-usl", "10.5")
    page.select_option("#a-model", "A1")
    page.select_option("#a-class", "major")

    # the report panel is part of the result, so it only exists after an analysis
    expect(page.locator("#rp-create")).to_be_hidden()
    page.click("#run-btn")
    expect(page.locator("#r-names")).to_have_text("Cp and Cpk")
    page.fill("#rp-process", "turning")
    page.fill("#rp-machine", "CNC-07")
    page.fill("#rp-unit", "mm")
    page.fill("#rp-uncertainty", "0.0211")
    page.fill("#rp-recommendations", "keep sampling")
    page.select_option("#rp-language", "en")
    page.click("#rp-create")
    expect(page.locator("#rp-created")).to_contain_text("was created")
    href = page.locator("#rp-open").get_attribute("href")
    assert href.startswith("/api/reports/")

    # the report opens as its own page: it needs inline styles and SVG, and must run no script
    report = ctx.new_page()
    report_problems = []
    report.on("console", lambda m: report_problems.append(m.text) if m.type == "error" else None)
    report.goto(server.rstrip("/") + href)
    expect(report.locator("h1")).to_have_text("Process study report")
    assert report.locator("svg").count() == 5
    expect(report.locator("section.el")).not_to_have_count(0)
    assert report.evaluate("() => getComputedStyle(document.querySelector('h1')).fontSize") == "24px"  # inline CSS applied (18pt)
    assert report_problems == [], report_problems
    assert "CNC-07" in report.content() and "keep sampling" in report.content()

    # download the archive and check it in the tools tab
    archive = ctx.request.get(server.rstrip("/") + page.locator("#rp-archive").get_attribute("href"))
    path = tmp_path / "archive.json"
    path.write_bytes(archive.body())
    page.click("nav.tabs button[data-tab=tools]")
    page.set_input_files("#archive-file", str(path))
    page.click("#archive-btn")
    expect(page.locator("#archive-out")).to_contain_text("Content is unchanged")
    expect(page.locator("#archive-out")).to_contain_text("gives the stored result again")

    # a changed archive is reported as changed
    import json

    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["dataset"]["values"][0] += 0.5
    path.write_text(json.dumps(changed), encoding="utf-8")
    page.set_input_files("#archive-file", str(path))
    page.click("#archive-btn")
    expect(page.locator("#archive-out")).to_contain_text("was changed after the archive was made")

    assert problems == [], problems
    ctx.close()


def test_wrong_password_logout_and_a_reload_keep_the_data_private(server, browser):
    expect = playwright_sync.expect
    ctx = browser.new_context(locale="en")
    page = ctx.new_page()
    page.goto(server)
    sign_in(page, "eng", "not the password!!", wait=False)
    expect(page.locator("#errors")).to_contain_text("User name or password is wrong")
    assert page.locator("#app").is_hidden()
    page.click("#errors button")
    sign_in(page)
    expect(page.locator("#user-label")).to_contain_text("Eva Engineer (eng) · Engineer")

    page.reload()  # the session cookie keeps the login
    expect(page.locator("#user-label")).to_contain_text("Eva Engineer")
    cookie = next(c for c in ctx.cookies() if c["name"] == "spc_session")
    assert cookie["httpOnly"] and cookie["sameSite"] == "Strict"

    page.click("#logout-btn")
    expect(page.locator("#login-form")).to_be_visible()
    expect(page.locator("#app")).to_be_hidden()
    assert ctx.request.get(server.rstrip("/") + "/api/datasets").status == 401
    ctx.close()


def test_a_viewer_can_read_saved_data_but_sees_no_way_to_change_it(server, browser, app):
    expect = playwright_sync.expect
    owner = app.state.auth.list_users()[0].id
    demo = Dataset.from_values([10.0, 10.1, 9.9, 10.05, 10.02, 9.98] * 5)
    app.state.store.add(demo, owner, "demo-saved")
    ctx = browser.new_context(locale="en")
    page = ctx.new_page()
    page.goto(server)
    sign_in(page, "view")
    expect(page.locator("#tab-saved")).to_be_visible()  # a viewer starts at the saved data
    expect(page.locator("nav.tabs button[data-tab=import]")).to_be_hidden()
    expect(page.locator("nav.tabs button[data-tab=admin]")).to_be_hidden()
    expect(page.locator("#saved-datasets")).to_contain_text("demo-saved")
    expect(page.locator("#saved-datasets button", has_text="Delete")).to_have_count(0)
    page.click("#saved-datasets button:has-text('Open')")
    expect(page.locator("#data-counts")).to_contain_text("30 values")
    expect(page.locator("#mark-btn")).to_be_hidden()
    expect(page.locator("#reason")).to_be_hidden()
    ctx.close()


def test_a_new_user_must_change_the_password_before_anything_else(server, browser):
    expect = playwright_sync.expect
    ctx = browser.new_context(locale="en")
    admin = ctx.new_page()
    admin.goto(server)
    sign_in(admin, "admin")
    admin.click("nav.tabs button[data-tab=admin]")
    admin.fill("#nu-name", "newbie")
    admin.fill("#nu-display", "N. Ewbie")
    admin.select_option("#nu-role", "engineer")
    admin.fill("#nu-pass", "temporary-pass-1")
    admin.click("#nu-create")
    expect(admin.locator("#admin-msg")).to_contain_text("User newbie created")
    expect(admin.locator("#admin-users")).to_contain_text("N. Ewbie")
    admin.click("#audit-verify")
    expect(admin.locator("#audit-verdict")).to_contain_text("Chain intact")
    expect(admin.locator("#audit-log")).to_contain_text("user_created")
    ctx.close()

    ctx = browser.new_context(locale="en")
    page = ctx.new_page()
    page.goto(server)
    sign_in(page, "newbie", "temporary-pass-1")
    expect(page.locator("#password-forced")).to_be_visible()
    page.click("nav.tabs button[data-tab=tools]")  # no other tab opens
    expect(page.locator("#tab-password")).to_be_visible()
    page.fill("#pw-current", "temporary-pass-1")
    page.fill("#pw-new", "a much better password")
    page.fill("#pw-repeat", "something different!!")
    page.click("#password-form button[type=submit]")
    expect(page.locator("#errors")).to_contain_text("The two new passwords differ")
    page.click("#errors button")
    page.fill("#pw-repeat", "a much better password")
    page.click("#password-form button[type=submit]")
    expect(page.locator("#pw-done")).to_be_visible()
    page.click("nav.tabs button[data-tab=tools]")
    expect(page.locator("#tab-tools")).to_be_visible()
    ctx.close()


def test_fitted_distribution_in_the_browser(server, browser, tmp_path):
    expect = playwright_sync.expect
    rng = np.random.default_rng(3)
    lines = ["lot,v"] + [f"L{i // 5 + 1},{9 + rng.lognormal(0, 0.4):.4f}" for i in range(125)]
    path = tmp_path / "skew.csv"
    path.write_text("\n".join(lines) + "\n")
    ctx = browser.new_context(viewport={"width": 1200, "height": 900}, locale="en")
    page = ctx.new_page()
    problems = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(server)
    sign_in(page)
    page.set_input_files("#file", str(path))
    page.select_option("#col-value", "v")
    page.select_option("#col-subgroup", "lot")
    page.click("#import-btn")
    page.click("#to-analysis")
    page.fill("#a-lsl", "8.5")
    page.fill("#a-usl", "13")
    page.select_option("#a-class", "major")
    page.select_option("#a-model", "A2")

    expect(page.locator("#a-method")).to_be_hidden()  # only for a non-normal distribution
    expect(page.locator("#r-dist-block")).to_be_hidden()
    page.click("#run-btn")
    expect(page.locator("#r-normality")).to_contain_text("Normality test")
    expect(page.locator("#r-dist-block")).to_be_hidden()

    page.select_option("#a-dist", "lognormal")
    expect(page.locator("#a-method")).to_be_visible()
    page.fill("#a-boot", "40")
    page.click("#run-btn")
    expect(page.locator("#r-dist-block")).to_be_visible()
    expect(page.locator("#r-dist-line")).to_contain_text("Lognormal (chosen by you) · method .G")
    expect(page.locator("#r-names")).to_have_text("Cp and Cpk")
    expect(page.locator("#r-indices")).to_contain_text("Cpk.G")
    expect(page.locator("#r-targets")).to_contain_text("Cpk.G")
    expect(page.locator("#r-dist-boot")).to_contain_text("seed 20260701")
    assert page.locator("#r-dist-cands tr").count() == 8  # header and seven families

    page.select_option("#a-dist", "empirical")  # needs 2000 values: a clear message, no crash
    assert page.locator("#a-method option[value=Z]").evaluate("o => o.disabled")
    page.click("#run-btn")
    expect(page.locator("#errors")).to_contain_text("2000")
    page.click("#errors button")

    page.select_option("#a-dist", "auto")
    page.select_option("#a-method", "Z")
    page.select_option("#lang", "zh-TW")
    page.click("#run-btn")
    expect(page.locator("#r-dist-line")).to_contain_text("自動選擇")
    expect(page.locator("#r-indices")).to_contain_text(".Z")
    assert problems == [], problems
    ctx.close()


def test_restart_of_the_individuals_chart_in_the_browser(server, browser, tmp_path):
    expect = playwright_sync.expect
    rng = np.random.default_rng(2)
    values = np.r_[rng.normal(10, 0.1, 30), rng.normal(10.6, 0.1, 30)]  # a tool change moved the level
    path = tmp_path / "ind.csv"
    path.write_text("v\n" + "\n".join(f"{v:.4f}" for v in values) + "\n")
    ctx = browser.new_context(viewport={"width": 1200, "height": 900}, locale="en")
    page = ctx.new_page()
    problems = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(server)
    sign_in(page)
    page.set_input_files("#file", str(path))
    page.select_option("#col-value", "v")
    page.click("#import-btn")
    expect(page.locator("#restart-list")).to_have_text("No restarts.")

    page.click("#unrestart-btn")  # nothing selected
    expect(page.locator("#errors")).to_contain_text("Select")
    page.click("#errors button")
    page.locator("#rows tr:nth-child(32) input").check()  # No. 31: the first value after the tool change
    page.click("#restart-btn")
    expect(page.locator("#errors")).to_contain_text("reason")  # a reason is required
    page.click("#errors button")
    page.locator("#rows tr:nth-child(32) input").check()
    page.fill("#reason", "tool change T-07")
    page.click("#restart-btn")
    expect(page.locator("#restart-list")).to_contain_text("31")
    expect(page.locator("#data-counts")).to_contain_text("1 restart")
    expect(page.locator("#rows tr.restart-row")).to_have_count(1)
    expect(page.locator("#rows tr.restart-row .reason")).to_have_text("tool change T-07")

    page.click("#to-analysis")
    page.fill("#a-lsl", "9")
    page.fill("#a-usl", "12")
    page.fill("#a-moving", "3")
    page.click("#run-btn")
    expect(page.locator("#r-moving")).to_contain_text("moving sample size 3 and 1 restart")
    expect(page.locator("#chart-loc svg line.restart")).to_have_count(1)
    expect(page.locator("#chart-var svg line.restart")).to_have_count(1)
    assert page.locator("#chart-loc svg polyline.limit").count() == 2  # the limits are staircases
    expect(page.locator("#r-var-alarms li")).to_have_count(0)  # no alarm from the jump itself

    page.click("nav.tabs button[data-tab=data]")
    page.locator("#rows tr:nth-child(32) input").check()
    page.fill("#reason", "entered by mistake")
    page.click("#unrestart-btn")
    expect(page.locator("#restart-list")).to_have_text("No restarts.")
    assert problems == [], problems
    ctx.close()


def test_new_limits_for_a_phase_in_the_browser(server, browser, tmp_path):
    expect = playwright_sync.expect
    rng = np.random.default_rng(4)
    values = np.r_[rng.normal(10, 0.1, 40), rng.normal(10.6, 0.15, 40)]
    path = tmp_path / "phase.csv"
    path.write_text("v\n" + "\n".join(f"{v:.4f}" for v in values) + "\n")
    ctx = browser.new_context(viewport={"width": 1200, "height": 900}, locale="en")
    page = ctx.new_page()
    problems = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(server)
    sign_in(page)
    page.set_input_files("#file", str(path))
    page.select_option("#col-value", "v")
    page.click("#import-btn")
    page.fill("#reason", "new fixture F-2")
    page.locator("#rows tr:nth-child(42) input").check()  # No. 41: the first value of the second phase
    page.check("#restart-phase")
    page.click("#restart-btn")
    expect(page.locator("#restart-list")).to_contain_text("41 (new limits)")
    expect(page.locator("#rows tr.restart-row .status")).to_contain_text("new limits from here")

    page.click("#to-analysis")
    page.fill("#a-lsl", "9")
    page.fill("#a-usl", "12")
    page.click("#run-btn")
    expect(page.locator("#r-phases li")).to_have_count(2)
    expect(page.locator("#r-phases li").nth(1)).to_contain_text("Phase 2, from file row 42: 40 values")
    expect(page.locator("#chart-loc svg line.restart.phase")).to_have_count(1)
    expect(page.locator("#chart-loc svg polyline.center")).to_have_count(1)  # one centre line per phase, drawn as a staircase
    expect(page.locator("#r-loc-alarms li")).to_have_count(0)  # the new level raises no alarm against its own limits
    assert problems == [], problems
    ctx.close()


def test_excel_downloads_in_the_browser(server, browser, tmp_path):
    from openpyxl import load_workbook

    expect = playwright_sync.expect
    ctx = browser.new_context(viewport={"width": 1200, "height": 900}, locale="en")
    page = ctx.new_page()
    page.goto(server)
    sign_in(page)
    page.set_input_files("#file", str(sample(tmp_path)))
    page.select_option("#col-value", "直徑")
    page.select_option("#col-subgroup", "批號")
    page.click("#import-btn")

    with page.expect_download() as info:
        page.click("#export-xlsx-link")
    info.value.save_as(tmp_path / "data.xlsx")
    wb = load_workbook(tmp_path / "data.xlsx")
    assert wb.sheetnames == ["Data", "Log"] and wb["Data"].max_row == 126

    page.click("#suspect-btn")
    page.click("#suspect-select")
    page.fill("#reason", "typing error")
    page.click("#mark-btn")
    page.click("#to-analysis")
    page.fill("#a-lsl", "9.5")
    page.fill("#a-usl", "10.5")
    page.select_option("#a-model", "A1")
    page.select_option("#a-class", "major")
    page.click("#run-btn")
    page.fill("#rp-process", "turning")
    page.click("#rp-create")
    expect(page.locator("#rp-created")).to_contain_text("was created")
    with page.expect_download() as info:
        page.click("#rp-excel")
    info.value.save_as(tmp_path / "report.xlsx")
    report = load_workbook(tmp_path / "report.xlsx")
    assert report.sheetnames[0] == "Summary" and report["Summary"]["A9"].value == "Cpk.G"
    assert any(c.value == "turning" for row in report["Report elements"].iter_rows() for c in row)

    page.click("nav.tabs button[data-tab=saved]")
    expect(page.locator("#saved-reports a", has_text="Excel").first).to_be_visible()  # earlier tests made reports too
    ctx.close()


def test_customer_profile_from_editor_to_report(server, browser, tmp_path):
    expect = playwright_sync.expect
    admin_ctx = browser.new_context(viewport={"width": 1200, "height": 1000}, locale="en")
    admin = admin_ctx.new_page()
    problems = []
    admin.on("pageerror", lambda e: problems.append(str(e)))
    admin.goto(server)
    sign_in(admin, "admin")
    admin.click("nav.tabs button[data-tab=admin]")
    admin.click("#pf-new")
    admin.fill("#pf-name", "Globex")
    admin.fill("#pf-org", "Globex QA")
    admin.fill("#pf-form", "G-12")
    admin.fill("#pf-rev", "B")
    admin.fill("#pf-alpha", "0.01")
    admin.fill("#pf-t-production-major-p", "2.0")
    admin.click("#pf-save")  # a cell with only one of the two numbers is refused
    expect(admin.locator("#errors")).to_contain_text("both targets")
    admin.click("#errors button")
    admin.fill("#pf-t-production-major-pk", "1.8")
    admin.check("#pf-req-part_number")
    admin.click("#pf-extra-add")
    row = admin.locator("#pf-extra .row").first
    row.locator("input[type=text]").nth(0).fill("drawing_no")
    row.locator("input[type=text]").nth(1).fill("Drawing number")
    row.locator("input[type=text]").nth(2).fill("圖面編號")
    row.locator("input[type=checkbox]").check()
    admin.uncheck("#pf-el22")
    admin.click("#pf-save")
    expect(admin.locator("#pf-msg")).to_contain_text("saved, revision 1")
    expect(admin.locator("#profile-list")).to_contain_text("Globex")
    admin.fill("#pf-rev", "C")  # a change raises the revision
    admin.click("#pf-save")
    expect(admin.locator("#pf-msg")).to_contain_text("revision 2")
    admin_ctx.close()

    ctx = browser.new_context(viewport={"width": 1200, "height": 1000}, locale="en")
    page = ctx.new_page()
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(server)
    sign_in(page)
    page.set_input_files("#file", str(sample(tmp_path)))
    page.select_option("#col-value", "直徑")
    page.select_option("#col-subgroup", "批號")
    page.click("#import-btn")
    page.click("#suspect-btn")
    page.click("#suspect-select")
    page.fill("#reason", "typing error")
    page.click("#mark-btn")
    page.click("#to-analysis")
    page.select_option("#a-profile", label="Globex")
    expect(page.locator("#a-alpha")).to_be_disabled()  # fixed by the profile
    expect(page.locator("#a-customer")).to_have_value("Globex")
    expect(page.locator("#a-profile-line")).to_contain_text("risk α")
    expect(page.locator("#a-profile-line")).to_contain_text("target values")
    page.fill("#a-lsl", "9.5")
    page.fill("#a-usl", "10.5")
    page.select_option("#a-model", "A1")
    page.select_option("#a-class", "major")
    page.click("#run-btn")
    expect(page.locator("#r-profile")).to_contain_text("Globex, revision 2")
    # the customer's 2.0 / 1.8, raised a little for 120 values instead of 125; the draft's 1.33 would not show
    expect(page.locator("#r-targets")).to_contain_text("2.01")
    expect(page.locator("#r-targets")).to_contain_text("1.81")
    expect(page.locator("#r-targets")).not_to_contain_text("1.33")

    expect(page.locator("#rp-extra")).to_be_visible()
    expect(page.locator("label.required", has_text="Part no.")).to_have_count(1)
    expect(page.locator("label.required", has_text="Drawing number")).to_have_count(1)
    page.click("#rp-create")
    expect(page.locator("#errors")).to_contain_text("requires these fields")
    expect(page.locator("#errors")).to_contain_text("Drawing number")
    page.click("#errors button")
    page.fill("#rp-part_number", "P-7")
    page.fill("#rp-x-drawing_no", "D-100")
    page.select_option("#rp-language", "en")
    page.click("#rp-create")
    expect(page.locator("#rp-created")).to_contain_text("was created")
    report = ctx.new_page()
    report.goto(server.rstrip("/") + page.locator("#rp-open").get_attribute("href"))
    content = report.content()
    assert "Globex QA" in content and "G-12" in content and "Rev. C" in content and "D-100" in content
    expect(report.locator("h1")).to_have_text("Process study report")
    assert report.locator("section.el h2 .no", has_text="22").count() == 0  # element 22 is switched off in the profile
    assert problems == [], problems
    ctx.close()


def test_spc_monitor_at_the_line_from_set_up_to_the_action_plan(server, browser, app):
    import numpy as np

    expect = playwright_sync.expect
    users = {u.username: u for u in app.state.auth.list_users()}
    if "oper" not in users:
        app.state.auth.create_user("oper", PASSWORD, "operator", "Olga Operator")
    problems = []

    # an engineer sets the monitor up
    ctx = browser.new_context(viewport={"width": 1250, "height": 1000}, locale="en")
    eng = ctx.new_page()
    eng.on("pageerror", lambda e: problems.append(str(e)))
    eng.goto(server)
    sign_in(eng)
    eng.click("nav.tabs button[data-tab=monitor]")
    eng.click("#mon-new")
    eng.fill("#me-name", "Shaft diameter L1")
    eng.fill("#me-process", "turning")
    eng.fill("#me-characteristic", "diameter")
    eng.fill("#me-unit", "mm")
    eng.fill("#me-line", "L1")
    eng.fill("#me-mu", "10")
    eng.fill("#me-sigma", "0.1")
    eng.fill("#me-lsl", "9.5")
    eng.fill("#me-usl", "10.5")
    eng.select_option("#me-class", "major")
    eng.select_option("#me-model", "A1")
    eng.locator("#me-ocap tr[data-key=default] input").nth(0).fill("Measure again, then call the shift leader")
    eng.locator("#me-ocap tr[data-key=default] input").nth(1).fill("Shift leader")
    eng.click("#me-save")
    expect(eng.locator("#md-title")).to_have_text("Shaft diameter L1")
    expect(eng.locator("#md-sub")).to_contain_text("limits revision 1")
    eng.locator("#md-limits").locator("xpath=ancestor::details").locator("summary").click()
    expect(eng.locator("#md-limits")).to_contain_text("Limits revision 1")
    expect(eng.locator("#md-limits")).to_contain_text("parameters: mean 10, standard deviation 0.1")
    ctx.close()

    # the operator works at the line
    ctx = browser.new_context(viewport={"width": 1250, "height": 1000}, locale="en")
    op = ctx.new_page()
    op.on("pageerror", lambda e: problems.append(str(e)))
    op.goto(server)
    sign_in(op, "oper")
    expect(op.locator("#mon-new")).to_be_hidden()  # an operator does not set monitors up
    op.click("nav.tabs button[data-tab=monitor]")
    op.click("#mon-list button:has-text('Open')")
    expect(op.locator("#mon-edit")).to_be_hidden()
    expect(op.locator("#md-ack")).to_be_visible()
    expect(op.locator("#md-plan")).to_contain_text("Measure again, then call the shift leader")

    def enter(values):
        for i, v in enumerate(values):
            op.fill(f"#md-v{i}", str(v))
        op.click("#md-submit")

    enter([10.0, 10.05, 9.97, 10.04, 9.98])
    expect(op.locator("#errors")).to_contain_text("Confirm first that you know the action plan")  # not before the confirmation
    op.click("#errors button")
    op.click("#md-ack-btn")
    expect(op.locator("#md-ack")).to_be_hidden()
    enter([10.0, 10.05, 9.97, 10.04, 9.98])
    expect(op.locator("#md-result .ok-box")).to_contain_text("within the limits")
    enter([10.4, 10.5, 10.45, 10.55, 10.5])
    expect(op.locator("#md-result .alarm-box")).to_contain_text("OUT OF CONTROL")
    expect(op.locator("#md-result .alarm-box")).to_contain_text("Shift leader")
    expect(op.locator("#md-incident")).to_be_visible()
    expect(op.locator("#alert-badge")).to_have_text("1")
    expect(op.locator("#mi-line")).to_contain_text("nobody has taken it over yet")

    # the chart shows the fixed control limits and the warning limits, and never the specification
    expect(op.locator("#md-chart-loc svg line.limit")).to_have_count(2)
    expect(op.locator("#md-chart-loc svg line.warning")).to_have_count(2)
    expect(op.locator("#md-chart-loc svg circle.alarm")).to_have_count(1)
    chart_text = op.locator("#md-chart-loc").inner_text() + op.locator("#md-chart-var").inner_text()
    assert "USL" not in chart_text and "LSL" not in chart_text and "10.5" not in op.locator("#md-chart-loc svg text.limit-label").all_text_contents()

    op.click("#mi-close")  # nothing was done yet
    expect(op.locator("#errors")).to_contain_text("Log the corrective action first")
    op.click("#errors button")
    op.click("#mi-ack")
    expect(op.locator("#mi-line")).to_contain_text("taken over by Olga Operator (oper)")
    op.select_option("#mi-step", "adjust_parameters")
    op.fill("#mi-text", "Raised the feed by 2 %")
    op.click("#mi-action")
    expect(op.locator("#mi-events")).to_contain_text("Raised the feed by 2 %")
    op.fill("#mi-close-text", "Control is back")
    op.click("#mi-close")
    expect(op.locator("#errors")).to_contain_text("new sample after the last action")  # proof is needed
    op.click("#errors button")
    enter([9.96, 10.06, 10.0, 10.08, 9.97])
    expect(op.locator("#md-result")).to_contain_text("verification")
    op.click("#mi-close")
    expect(op.locator("#md-incident")).to_be_hidden()
    expect(op.locator("#alert-badge")).to_be_hidden()

    # a sample that was measured wrongly is declared invalid, with a reason
    enter([10.0, 10.05, 9.97, 10.04, 9.98])
    op.once("dialog", lambda d: d.accept("gauge not zeroed"))
    op.locator("#md-points tr").nth(1).locator("button").click()  # the newest sample is on the first row
    expect(op.locator("#md-points tr.invalid-point")).to_have_count(1)
    expect(op.locator("#md-points tr.invalid-point td").nth(6)).to_have_text("invalid")
    ctx.close()

    # the ongoing report needs some more samples
    rng = np.random.default_rng(31)
    operator = app.state.auth.get_user(next(u.id for u in app.state.auth.list_users() if u.username == "oper"))
    mid = app.state.monitors.store.list()[-1]["id"]
    for _ in range(14):
        app.state.monitors.add_point(mid, [float(v) for v in rng.normal(10, 0.1, 5)], "", {}, None, operator)
    ctx = browser.new_context(viewport={"width": 1250, "height": 1000}, locale="en")
    page = ctx.new_page()
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(server)
    sign_in(page)
    page.click("nav.tabs button[data-tab=monitor]")
    page.click("#mon-list button:has-text('Open')")
    page.locator("#md-ongoing-box summary").click()
    page.fill("#og-window", "40")
    page.click("#og-show")
    expect(page.locator("#og-out")).to_contain_text("Quadrant")
    expect(page.locator("#og-out")).to_contain_text("Response of the action plan: 1 incidents")
    page.click("#og-report")
    expect(page.locator("#og-out")).to_contain_text("was created")
    page.select_option("#lang", "zh-TW")
    expect(page.locator("#md-title")).to_have_text("Shaft diameter L1")
    expect(page.locator("nav.tabs button[data-tab=monitor]")).to_contain_text("現場 SPC")
    assert problems == [], problems
    ctx.close()

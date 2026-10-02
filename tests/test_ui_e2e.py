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


def sign_in(page, user="eng", password=PASSWORD):
    page.fill("#login-user", user)
    page.fill("#login-pass", password)
    page.click("#login-form button[type=submit]")


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
    sign_in(page, "eng", "not the password!!")
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

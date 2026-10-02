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

from spc.api import create_app  # noqa: E402


def find_chromium():
    candidates = [os.environ.get("SPC_CHROMIUM", "")]
    candidates += sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return next((c for c in candidates if c and os.path.exists(c)), None)


@pytest.fixture(scope="module")
def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
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
    expect(page.locator("nav.tabs button[data-tab=import]")).to_have_text("1. 匯入")  # language from the browser

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
    page.fill("#reason", "typing error")
    page.fill("#person", "A. Chen")
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
    page.set_input_files("#file", str(path))
    page.select_option("#col-value", "v")
    page.select_option("#col-subgroup", "lot")
    page.click("#import-btn")
    playwright_sync.expect(page.locator("#rows")).to_contain_text("<img src=x onerror")
    assert page.evaluate("() => window.__pwned === undefined")
    ctx.close()

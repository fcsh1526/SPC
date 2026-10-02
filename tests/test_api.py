import numpy as np
import pytest
from fastapi.testclient import TestClient

from spc.api import create_app
from spc.api.store import DatasetStore


def csv_text(k=25, n=5, seed=1, spike=None):
    rng = np.random.default_rng(seed)
    lines = ["lot,diameter,machine"]
    for g in range(k):
        for j in range(n):
            v = 10 + rng.normal(0, 0.1)
            if spike == (g, j):
                v += 3
            lines.append(f"L{g + 1},{v:.4f},M{1 + g % 2}")
    return "\n".join(lines).encode()


@pytest.fixture
def client():
    return TestClient(create_app(DatasetStore(max_items=3), max_upload=200_000))


def upload(client, raw=None, **params):
    q = {"value": "diameter", "subgroup": "lot", "tags": "machine", "filename": "a.csv"}
    q.update(params)
    return client.post("/api/datasets", params=q, content=raw if raw is not None else csv_text())


def err(response):
    return response.json()["error"]


# ------------------------------------------------------------------ static files and headers

def test_index_and_assets_are_served_with_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/i18n/zh-TW.json").status_code == 200
    assert client.get("/api/meta").json()["languages"] == ["zh-TW", "en"]


def test_index_has_no_inline_script_or_handlers(client):
    html = client.get("/").text
    assert "<script>" not in html and "onclick=" not in html and "style=" not in html


# ------------------------------------------------------------------ import

def test_preview(client):
    r = client.post("/api/preview", content=csv_text())
    body = r.json()
    assert r.status_code == 200
    assert body["header"] == ["lot", "diameter", "machine"] and len(body["rows"]) == 8
    assert body["delimiter"] == ","


def test_preview_big5_file(client):
    raw = "批號,直徑\n甲,10.1\n乙,10.2\n".encode("cp950")
    body = client.post("/api/preview", content=raw).json()
    assert body["encoding"] == "cp950" and body["header"] == ["批號", "直徑"]


def test_create_dataset_and_read_rows(client):
    r = upload(client)
    assert r.status_code == 200
    ds = r.json()
    assert ds["summary"]["n_total"] == 125 and ds["summary"]["k_subgroups"] == 25
    assert ds["has_subgroup"] and ds["tags"] == ["machine"] and ds["source"]["name"] == "a.csv"
    rows = client.get(f"/api/datasets/{ds['id']}/rows", params={"offset": 120, "limit": 50}).json()
    assert rows["total"] == 125 and len(rows["rows"]) == 5
    first = rows["rows"][0]
    assert first["pos"] == 120 and first["subgroup"] == "L25" and first["valid"] is True


def test_import_errors_are_reported_with_codes_and_lines(client):
    r = client.post("/api/datasets", params={"value": "diameter", "subgroup": "lot"}, content=b"lot,diameter\nL1,abc\nL1,\n")
    assert r.status_code == 400
    e = err(r)
    assert e["code"] == "import_failed"
    assert {(i["line"], i["code"]) for i in e["params"]["issues"]} == {(2, "bad_number"), (3, "missing_value")}


def test_unknown_column_and_empty_and_oversized(client):
    assert err(upload(client, value="nope"))["params"]["issues"][0]["code"] == "missing_column"
    assert err(client.post("/api/datasets", params={"value": "x"}, content=b""))["code"] == "empty_file"
    big = client.post("/api/datasets", params={"value": "x"}, content=b"x" * 300_000)
    assert big.status_code == 413 and err(big)["code"] == "file_too_large"


# ------------------------------------------------------------------ outliers

def test_suspects_are_hints_and_do_not_change_the_data(client):
    ds = upload(client, csv_text(spike=(4, 2))).json()
    r = client.post(f"/api/datasets/{ds['id']}/suspects", json={"method": "mad"}).json()
    assert r["note"] == "hints_only" and [s["position"] for s in r["suspects"]] == [22]
    assert client.get(f"/api/datasets/{ds['id']}").json()["summary"]["n_invalid"] == 0


def test_marking_needs_reason_and_person_and_cannot_be_repeated(client):
    ds = upload(client).json()
    url = f"/api/datasets/{ds['id']}/invalid"
    assert err(client.post(url, json={"positions": [3], "reason": "  ", "by": "A"}))["code"] == "reason_required"
    assert err(client.post(url, json={"positions": [3], "reason": "wrong part", "by": ""}))["code"] == "person_required"
    assert err(client.post(url, json={"positions": [999], "reason": "r", "by": "A"}))["code"] == "positions_out_of_range"
    assert err(client.post(url, json={"positions": [1, 1], "reason": "r", "by": "A"}))["code"] == "duplicate_positions"
    ok = client.post(url, json={"positions": [3], "reason": "wrong part", "by": "A"})
    assert ok.status_code == 200 and ok.json()["summary"]["n_invalid"] == 1 and ok.json()["log"][0]["reason"] == "wrong part"
    again = client.post(url, json={"positions": [3], "reason": "other", "by": "B"})
    assert again.status_code == 409 and err(again)["code"] == "already_invalid"
    rows = client.get(f"/api/datasets/{ds['id']}/rows", params={"limit": 5}).json()["rows"]
    assert rows[3]["valid"] is False and rows[3]["invalid"]["by"] == "A"


def test_restore(client):
    ds = upload(client).json()
    base = f"/api/datasets/{ds['id']}"
    body = {"positions": [3], "reason": "it was fine", "by": "B"}
    assert err(client.post(f"{base}/restore", json=body))["code"] == "not_invalid"
    client.post(f"{base}/invalid", json={"positions": [3], "reason": "wrong part", "by": "A"})
    r = client.post(f"{base}/restore", json=body)
    assert r.json()["summary"]["n_invalid"] == 0
    assert [e["action"] for e in r.json()["log"]] == ["mark_invalid", "restore"]


def test_export_keeps_marks_and_opens_in_excel(client):
    ds = upload(client).json()
    client.post(f"/api/datasets/{ds['id']}/invalid", json={"positions": [0], "reason": "量測錯誤", "by": "陳"})
    r = client.get(f"/api/datasets/{ds['id']}/export.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert "量測錯誤" in r.content.decode("utf-8-sig")


# ------------------------------------------------------------------ analysis

def test_analyze_end_to_end(client):
    ds = upload(client).json()
    body = {"lsl": 9.0, "usl": 11.0, "model": "A1", "characteristic_class": "major"}
    r = client.post(f"/api/datasets/{ds['id']}/analyze", json=body)
    assert r.status_code == 200
    res = r.json()
    assert res["stage"] == "production" and res["chart"]["kind"] == "xbar-s"
    assert (res["names"]["p"], res["names"]["pk"]) == ("Cp", "Cpk")
    assert res["targets"]["verdict_pk"] == "meets"
    assert res["source"]["name"] == "a.csv"


def test_marked_value_changes_the_analysis_and_incomplete_policy_is_respected(client):
    ds = upload(client, csv_text(spike=(4, 2))).json()
    base = f"/api/datasets/{ds['id']}"
    before = client.post(f"{base}/analyze", json={"lsl": 9, "usl": 11}).json()
    client.post(f"{base}/invalid", json={"positions": [22], "reason": "typing error", "by": "A"})
    after = client.post(f"{base}/analyze", json={"lsl": 9, "usl": 11}).json()
    assert after["chart"]["k"] == 24 and after["indices"]["pk"] > before["indices"]["pk"]
    assert any(w["code"] == "dropped_subgroups" for w in after["warnings"])
    strict = client.post(f"{base}/analyze", json={"lsl": 9, "usl": 11, "incomplete": "error"})
    assert strict.status_code == 400 and err(strict)["code"] == "incomplete_subgroups"
    assert err(strict)["params"]["labels"] == ["L5"]


def test_analyze_validation_errors(client):
    ds = upload(client).json()
    url = f"/api/datasets/{ds['id']}/analyze"
    r = client.post(url, json={"stage": "nonsense"})
    assert r.status_code == 422 and err(r)["code"] == "validation"
    assert err(r)["params"]["errors"][0]["field"] == "stage"
    assert client.post(url, json={"unknown_field": 1}).status_code == 422
    assert client.post(url, json={"alpha": 2}).status_code == 422
    bad_rule = client.post(url, json={"rules": {"nonsense": True}})
    assert bad_rule.status_code == 400 and err(bad_rule)["code"] == "invalid_input"
    swapped = client.post(url, json={"lsl": 5, "usl": 1})
    assert swapped.status_code == 400


def test_unknown_dataset(client):
    r = client.get("/api/datasets/doesnotexist")
    assert r.status_code == 404 and err(r)["code"] == "dataset_not_found"
    assert client.post("/api/datasets/nope/analyze", json={}).status_code == 404


def test_store_keeps_only_the_latest_datasets(client):
    ids = [upload(client).json()["id"] for _ in range(5)]
    assert client.get(f"/api/datasets/{ids[0]}").status_code == 404
    assert client.get(f"/api/datasets/{ids[-1]}").status_code == 200


# ------------------------------------------------------------------ tools

def test_targets_tool(client):
    r = client.post("/api/targets", json={"stage": "machine", "characteristic_class": "major", "n": 30}).json()
    assert r["blocked"] is False and r["pk"] == pytest.approx(1.96, abs=0.01) and r["adjusted"] is True
    blocked = client.post("/api/targets", json={"stage": "machine", "characteristic_class": "critical", "n": 30}).json()
    assert blocked == {"blocked": True}


def test_arl_tool(client):
    r = client.post("/api/arl", json={"shift": 0.6, "n": 5, "max_arl": 10}).json()
    assert r["arl"] == pytest.approx(20.6, abs=0.1)
    assert r["required_n"] is not None and r["required_n"] > 5
    assert len(r["curve"]) == 13 and r["curve"][0]["arl"] == pytest.approx(370.4, abs=0.1)
    assert client.post("/api/arl", json={"shift": 0, "n": 5}).status_code == 422


def test_attribute_chart_tool(client):
    r = client.post("/api/attribute-chart", json={"kind": "np", "counts": [3, 5, 2, 4, 25], "sizes": 100}).json()
    assert r["alarms"] == [4] and r["ucl"][0] < 25
    low = client.post("/api/attribute-chart", json={"kind": "p", "counts": [1, 0, 2, 1, 0, 1], "sizes": 100}).json()
    assert min(low["lcl"]) == 0  # exact limit, never negative
    assert any(w["code"] == "attribute_small_sample" for w in low["warnings"]) is False  # size 100 is above the limit
    small = client.post("/api/attribute-chart", json={"kind": "c", "counts": [4, 6, 5, 7]}).json()
    assert small["kind"] == "c"
    assert client.post("/api/attribute-chart", json={"kind": "p", "counts": [1, 2, 3]}).status_code == 400


# ------------------------------------------------------------------ reports

REPORT_BODY = {
    "analysis": {"lsl": 9.0, "usl": 11.0, "model": "A1", "characteristic_class": "major"},
    "meta": {"process": "turning", "machine": "CNC-07", "unit": "mm", "target": 10.0, "uncertainty": 0.0211},
    "language": "en",
}


def test_create_and_open_a_report(client):
    ds = upload(client).json()
    r = client.post(f"/api/datasets/{ds['id']}/reports", json=REPORT_BODY)
    assert r.status_code == 200
    out = r.json()
    assert out["language"] == "en" and len(out["digest"]) == 64
    page = client.get(out["urls"]["html"])
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script" not in csp  # the report can run no script at all
    assert "attachment" not in page.headers.get("content-disposition", "")
    assert "Process study report" in page.text and "CNC-07" in page.text and out["digest"] in page.text
    download = client.get(out["urls"]["download"])
    assert "attachment" in download.headers["content-disposition"] and download.text == page.text


def test_archive_download_and_check(client):
    ds = upload(client).json()
    out = client.post(f"/api/datasets/{ds['id']}/reports", json=REPORT_BODY).json()
    archive = client.get(out["urls"]["archive"])
    assert archive.status_code == 200 and "attachment" in archive.headers["content-disposition"]
    check = client.post("/api/archive/check", content=archive.content).json()
    assert check == {"integrity_ok": True, "reproduced": True, "same_engine_version": True, "differences": []}
    tampered = archive.json()
    tampered["dataset"]["values"][0] += 0.5
    bad = client.post("/api/archive/check", json=tampered).json()
    assert bad["integrity_ok"] is False


def test_a_report_is_a_snapshot(client):
    ds = upload(client).json()
    out = client.post(f"/api/datasets/{ds['id']}/reports", json=REPORT_BODY).json()
    before = client.get(out["urls"]["html"]).text
    client.post(f"/api/datasets/{ds['id']}/invalid", json={"positions": [3], "reason": "wrong part", "by": "A"})
    assert client.get(out["urls"]["html"]).text == before
    newer = client.post(f"/api/datasets/{ds['id']}/reports", json=REPORT_BODY).json()
    assert newer["id"] != out["id"] and "wrong part" in client.get(newer["urls"]["html"]).text


def test_report_in_chinese(client):
    ds = upload(client).json()
    body = {**REPORT_BODY, "language": "zh-TW"}
    out = client.post(f"/api/datasets/{ds['id']}/reports", json=body).json()
    assert "製程研究報告" in client.get(out["urls"]["html"]).text


def test_report_errors(client):
    ds = upload(client).json()
    url = f"/api/datasets/{ds['id']}/reports"
    no_spec = client.post(url, json={"analysis": {}})
    assert no_spec.status_code == 400 and err(no_spec)["code"] == "report_needs_spec"
    assert client.post(url, json={**REPORT_BODY, "language": "fr"}).status_code == 422
    assert client.post(url, json={**REPORT_BODY, "meta": {"uncertainty": -1}}).status_code == 422
    assert client.post(url, json={**REPORT_BODY, "meta": {"unknown": 1}}).status_code == 422
    missing = client.get("/api/reports/nope")
    assert missing.status_code == 404 and err(missing)["code"] == "report_not_found"
    assert client.get("/api/reports/nope/archive.json").status_code == 404
    assert client.post("/api/datasets/nope/reports", json=REPORT_BODY).status_code == 404
    unreadable = client.post("/api/archive/check", content=b"this is not json")
    assert unreadable.status_code == 400 and err(unreadable)["code"] == "archive_unreadable"
    assert err(client.post("/api/archive/check", content=b"{}"))["code"] == "archive_unreadable"


def test_report_html_escapes_text_sent_by_the_user(client):
    ds = upload(client).json()
    body = {**REPORT_BODY, "meta": {"process": "<script>alert(1)</script>", "recommendations": "<img src=x onerror=alert(2)>"}}
    out = client.post(f"/api/datasets/{ds['id']}/reports", json=body).json()
    html = client.get(out["urls"]["html"]).text
    assert "<script" not in html.lower() and "<img" not in html.lower()
    assert "&lt;script&gt;" in html

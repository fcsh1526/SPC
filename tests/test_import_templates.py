"""Excel import, header lines above the header, and saved import templates (the column maps of customers' files)."""

import io
from datetime import datetime

import numpy as np
import pytest
from openpyxl import Workbook

from spc.data import ColumnMap, DataImportError, load_csv
from spc.data.xlsx_io import is_xlsx, load_xlsx, preview_xlsx, sheet_names
from tests.conftest import logged_in_client, make_app
from tests.test_api import err


def workbook(rows, extra_sheet=None, title="Data") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    if extra_sheet:
        other = wb.create_sheet(extra_sheet[0])
        for r in extra_sheet[1]:
            other.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


ROWS = [["Lot", "Dia", "Time", "Machine"]] + [
    [f"L{g // 3 + 1}", 10 + 0.01 * g, datetime(2026, 3, 1, 8, g), f"M{g % 2 + 1}"] for g in range(12)
]
MAP = ColumnMap(value="Dia", subgroup="Lot", timestamp="Time", tags=("Machine",))


def test_an_excel_sheet_imports_like_a_csv_file():
    raw = workbook(ROWS)
    assert is_xlsx(raw) and not is_xlsx(b"a,b\n1,2\n")
    xl = load_xlsx(raw, MAP)
    csv_text = "\n".join(["Lot,Dia,Time,Machine"] + [f"{r[0]},{r[1]!r},{r[2]:%Y-%m-%d %H:%M:%S},{r[3]}" for r in ROWS[1:]])
    cs = load_csv(csv_text.encode(), MAP)
    assert np.array_equal(xl.values, cs.values) and list(xl.subgroup) == list(cs.subgroup)
    assert np.array_equal(xl.timestamp, cs.timestamp) and list(xl.tags["Machine"]) == list(cs.tags["Machine"])
    # full precision of the stored number, Excel row numbers, and the hash of the *.xlsx itself
    assert xl.values[3] == 10.03 and list(xl.source_rows) == list(range(2, 14))
    import hashlib

    assert xl.source.sha256 == hashlib.sha256(raw).hexdigest() and xl.source.encoding == "xlsx" and xl.source.delimiter == "[Data]"


def test_sheet_choice_and_header_line_above_the_data():
    raw = workbook([["Plant report"], [], ["Dia"], [1.5], [1.6], [1.7]], extra_sheet=("Other", [["x"], [1]]))
    assert sheet_names(raw) == ["Data", "Other"]
    p = preview_xlsx(raw, header_row=3)
    assert p["header"] == ["Dia"] and p["rows"] == [["1.5"], ["1.6"], ["1.7"]] and p["sheets"] == ["Data", "Other"] and p["sheet"] == "Data"
    ds = load_xlsx(raw, ColumnMap(value="Dia"), header_row=3)
    assert list(ds.values) == [1.5, 1.6, 1.7] and list(ds.source_rows) == [4, 5, 6]  # the row numbers of Excel
    other = load_xlsx(raw, ColumnMap(value="x"), sheet="Other")
    assert list(other.values) == [1.0]
    with pytest.raises(DataImportError) as e:
        load_xlsx(raw, ColumnMap(value="Dia"), sheet="Nope")
    assert e.value.issues[0].code == "sheet_not_found"


def test_header_line_works_for_csv_too():
    text = "Report of line 4\nprinted 2026-03-01\nDia;Lot\n1,5;A\n1,6;A\n".encode()
    ds = load_csv(text, ColumnMap(value="Dia", subgroup="Lot"), decimal=",", header_row=3)
    assert list(ds.values) == [1.5, 1.6] and list(ds.source_rows) == [4, 5] and ds.source.delimiter == ";"
    with pytest.raises(DataImportError) as e:
        load_csv(text, ColumnMap(value="Dia"), header_row=9)
    assert e.value.issues[0].code == "empty"


def test_problems_in_a_sheet_are_reported_with_excel_rows():
    raw = workbook([["Dia", "Lot"], [1.0, "A"], ["abc", "A"], [None, "A"], [2.0, "B"]])
    with pytest.raises(DataImportError) as e:
        load_xlsx(raw, ColumnMap(value="Dia"))
    assert [(i.line, i.code) for i in e.value.issues] == [(3, "bad_number"), (4, "missing_value")]
    with pytest.raises(DataImportError) as e:  # "abc" stays an error even when empty cells are skipped
        load_xlsx(raw, ColumnMap(value="Dia"), missing="skip")
    assert [i.code for i in e.value.issues] == ["bad_number"]
    ok = load_xlsx(workbook([["Dia", "Lot"], [1.0, "A"], [None, "A"], [2.0, "B"]]), ColumnMap(value="Dia"), missing="skip")
    assert list(ok.values) == [1.0, 2.0] and any(w.code == "rows_skipped" for w in ok.warnings)


def test_numbers_written_as_text_follow_the_decimal_setting():
    raw = workbook([["Dia"], ["1,25"], [1.5]])
    ds = load_xlsx(raw, ColumnMap(value="Dia"), decimal=",")
    assert list(ds.values) == [1.25, 1.5]


def test_a_file_that_is_not_a_workbook_is_refused():
    with pytest.raises(DataImportError) as e:
        load_xlsx(b"PK\x03\x04 this is not a workbook", ColumnMap(value="x"))
    assert e.value.issues[0].code == "xlsx_unreadable"


# ------------------------------------------------------------------ API

@pytest.fixture
def app():
    return make_app(max_upload=2_000_000)


@pytest.fixture
def client(app):
    return logged_in_client(app)


TEMPLATE = {
    "name": "Line 4 CMM", "format": "xlsx", "sheet": "Data", "header_row": 1, "decimal": ".", "missing": "error",
    "columns": {"value": "Dia", "subgroup": "Lot", "timestamp": "Time"}, "tags": ["Machine"],
}


def test_preview_and_import_of_an_excel_file(client):
    raw = workbook(ROWS)
    p = client.post("/api/preview", content=raw).json()
    assert p["kind"] == "xlsx" and p["header"] == ["Lot", "Dia", "Time", "Machine"] and p["sheets"] == ["Data"] and p["encoding"] == "xlsx"
    r = client.post("/api/datasets", params={"value": "Dia", "subgroup": "Lot", "filename": "cmm.xlsx"}, content=raw)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["n_total"] == 12 and body["source"]["name"] == "cmm.xlsx" and body["source"]["encoding"] == "xlsx"
    broken = client.post("/api/datasets", params={"value": "Nope"}, content=raw)
    assert broken.status_code == 400 and err(broken)["params"]["issues"][0]["code"] == "missing_column"


def test_a_saved_template_is_offered_for_a_file_that_fits_and_imports_it(app, client):
    saved = client.post("/api/import-templates", json={"record": TEMPLATE})
    assert saved.status_code == 200, saved.text
    tid = saved.json()["id"]
    assert saved.json()["columns"]["value"] == "Dia" and saved.json()["tags"] == ["Machine"]
    raw = workbook(ROWS)
    fit = client.post("/api/preview", content=raw).json()["templates"]
    assert [t["id"] for t in fit] == [tid]
    # the same data with the columns in another order still fits; a file without the columns does not
    shuffled = workbook([[r[3], r[2], r[1], r[0]] for r in ROWS])
    assert [t["id"] for t in client.post("/api/preview", content=shuffled).json()["templates"]] == [tid]
    other = workbook([["Foo", "Dia"], ["a", 1.0]])
    assert client.post("/api/preview", content=other).json()["templates"] == []
    # a CSV file does not fit a template for Excel
    assert client.post("/api/preview", content=b"Lot,Dia,Time,Machine\nL1,1.0,2026-03-01 08:00:00,M1\n").json()["templates"] == []
    r = client.post("/api/datasets", params={"template": tid, "filename": "cmm.xlsx"}, content=raw)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["summary"]["n_total"] == 12 and d["has_subgroup"] is True
    rows = app.state.db.all("SELECT action, detail FROM audit WHERE action IN ('import_template_created', 'dataset_created') ORDER BY id")
    assert [r["action"] for r in rows] == ["import_template_created", "dataset_created"] and f'"template": {tid}' in rows[1]["detail"]


def test_template_values_can_be_overridden_by_the_request(client):
    tid = client.post("/api/import-templates", json={"record": {**TEMPLATE, "format": "any"}}).json()["id"]
    raw = workbook(ROWS)
    r = client.post("/api/datasets", params={"template": tid, "subgroup": ""}, content=raw)
    assert r.status_code == 200 and r.json()["has_subgroup"] is False  # an empty value removes the role


def test_csv_template_with_decimal_comma_and_header_line(client):
    rec = {"name": "Old gauge", "format": "csv", "header_row": 3, "decimal": ",", "delimiter": ";", "encoding": "cp950",
           "columns": {"value": "直徑"}, "tags": []}
    tid = client.post("/api/import-templates", json={"record": rec}).json()["id"]
    raw = "報表\n列印日期\n直徑;批\n1,5;A\n1,6;A\n".encode("cp950")
    p = client.post("/api/preview", params={"header_row": 3, "encoding": "cp950"}, content=raw).json()
    assert p["header"] == ["直徑", "批"] and [t["id"] for t in p["templates"]] == [tid]
    r = client.post("/api/datasets", params={"template": tid}, content=raw)
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["n_total"] == 2
    wrong = client.post("/api/datasets", params={"template": tid}, content=workbook(ROWS))
    assert wrong.status_code == 400 and err(wrong)["code"] == "invalid_input"  # the template is for csv files


def test_template_rules(client):
    assert err(client.post("/api/import-templates", json={"record": {**TEMPLATE, "columns": {"subgroup": "Lot"}}}))["code"] == "invalid_input"
    assert err(client.post("/api/import-templates", json={"record": {**TEMPLATE, "columns": {"value": "Dia", "subgroup": "Dia"}}}))["code"] == "invalid_input"
    assert err(client.post("/api/import-templates", json={"record": {**TEMPLATE, "encoding": "ebcdic"}}))["code"] == "invalid_input"
    assert err(client.post("/api/import-templates", json={"record": {**TEMPLATE, "header_row": 0}}))["code"] == "invalid_input"
    assert err(client.post("/api/import-templates", json={"record": {**TEMPLATE, "colour": "red"}}))["code"] == "invalid_input"
    tid = client.post("/api/import-templates", json={"record": TEMPLATE}).json()["id"]
    twice = client.post("/api/import-templates", json={"record": {**TEMPLATE, "name": "line 4 cmm"}})
    assert twice.status_code == 409 and err(twice)["code"] == "import_template_name_taken"
    changed = client.put(f"/api/import-templates/{tid}", json={"record": {**TEMPLATE, "name": "Line 4 CMM v2"}})
    assert changed.status_code == 200 and changed.json()["name"] == "Line 4 CMM v2"
    assert [t["name"] for t in client.get("/api/import-templates").json()["templates"]] == ["Line 4 CMM v2"]
    assert client.delete(f"/api/import-templates/{tid}").status_code == 200
    gone = client.delete(f"/api/import-templates/{tid}")
    assert gone.status_code == 404 and err(gone)["code"] == "import_template_not_found"
    missing = client.post("/api/datasets", params={"template": 999}, content=workbook(ROWS))
    assert missing.status_code == 404
    nothing = client.post("/api/datasets", content=workbook(ROWS))
    assert nothing.status_code == 400 and err(nothing)["code"] == "invalid_input"


def test_an_unknown_encoding_is_an_import_problem_not_a_crash(client):
    r = client.post("/api/datasets", params={"value": "a", "encoding": "no-such-codec"}, content=b"a\n1\n")
    assert r.status_code == 400 and err(r)["params"]["issues"][0]["code"] == "encoding"

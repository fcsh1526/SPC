import hashlib

import numpy as np
import pytest

from spc.data import ColumnMap, DataImportError, Dataset, export_columns, load_csv, to_csv

BASIC = (
    "time,diameter,lot,machine,operator\n"
    "2026-10-02 08:00:00,10.01,L1,M1,Chen\n"
    "2026-10-02 08:00:10,10.03,L1,M1,Chen\n"
    "2026-10-02 08:10:00,9.98,L2,M1,Lin\n"
    "2026-10-02 08:10:10,10.00,L2,M1,Lin\n"
).encode()

COLS = ColumnMap(value="diameter", subgroup="lot", timestamp="time", tags=("machine", "operator"))


def test_basic_import_keeps_source_info():
    d = load_csv(BASIC, COLS)
    assert d.n_total == 4
    assert d.values.tolist() == [10.01, 10.03, 9.98, 10.00]
    assert d.subgroup.tolist() == ["L1", "L1", "L2", "L2"]
    assert d.tags["operator"].tolist() == ["Chen", "Chen", "Lin", "Lin"]
    assert str(d.timestamp[2]) == "2026-10-02T08:10:00"
    assert d.source.sha256 == hashlib.sha256(BASIC).hexdigest()
    assert d.source.delimiter == "," and d.source.encoding == "utf-8-sig"
    assert d.source_rows.tolist() == [2, 3, 4, 5]  # CSV line numbers, header is line 1


def test_reads_a_file_from_disk(tmp_path):
    p = tmp_path / "data.csv"
    p.write_bytes(BASIC)
    assert load_csv(p, COLS).source.name == "data.csv"


def test_excel_csv_in_big5_with_chinese_headers():
    text = "時間,直徑,批號\n2026/10/02 08:00,10.01,甲\n2026/10/02 08:05,10.02,甲\n"
    raw = text.encode("cp950")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")  # the file really is not UTF-8
    d = load_csv(raw, ColumnMap(value="直徑", subgroup="批號", timestamp="時間"))
    assert d.source.encoding == "cp950"
    assert d.subgroup.tolist() == ["甲", "甲"]
    assert str(d.timestamp[1]) == "2026-10-02T08:05:00"


def test_utf8_with_bom():
    raw = "﻿value\n1.5\n2.5\n".encode("utf-8")
    d = load_csv(raw, ColumnMap(value="value"))
    assert d.values.tolist() == [1.5, 2.5]


def test_semicolon_delimiter_and_decimal_comma():
    raw = b"value;lot\n1,5;a\n2,5;a\n"
    d = load_csv(raw, ColumnMap(value="value", subgroup="lot"), decimal=",")
    assert d.source.delimiter == ";"
    assert d.values.tolist() == [1.5, 2.5]
    with pytest.raises(DataImportError) as err:
        load_csv(raw, ColumnMap(value="value"))  # decimal comma read as a point decimal
    assert err.value.issues[0].code == "bad_number"


def test_all_problems_are_reported_together_with_line_numbers():
    raw = b"v,lot\n1.0,a\nabc,a\n3.0,\n,b\n5.0,b\n"
    with pytest.raises(DataImportError) as err:
        load_csv(raw, ColumnMap(value="v", subgroup="lot"))
    codes = {(i.line, i.code) for i in err.value.issues}
    assert codes == {(3, "bad_number"), (4, "missing_subgroup"), (5, "missing_value")}


def test_missing_column_lists_the_available_ones():
    with pytest.raises(DataImportError) as err:
        load_csv(BASIC, ColumnMap(value="diam"))
    assert err.value.issues[0].code == "missing_column"
    assert "diameter" in err.value.issues[0].message


def test_nan_and_inf_text_are_not_numbers():
    with pytest.raises(DataImportError):
        load_csv(b"v\n1\nnan\n", ColumnMap(value="v"))
    with pytest.raises(DataImportError):
        load_csv(b"v\n1\ninf\n", ColumnMap(value="v"))


def test_skip_missing_values_is_noted():
    # A fully blank line is ignored. A line with an empty value cell but other content is skipped and noted.
    raw = b"v,lot\n1,a\n\n,a\n2,a\n,b\n3,b\n"
    d = load_csv(raw, ColumnMap(value="v", subgroup="lot"), missing="skip")
    assert d.values.tolist() == [1.0, 2.0, 3.0]
    assert d.warnings == ("2 row(s) with an empty value were skipped",)
    assert d.source_rows.tolist() == [2, 5, 7]  # line numbers still point at the original file
    with pytest.raises(DataImportError) as err:
        load_csv(raw, ColumnMap(value="v", subgroup="lot"))  # the default is to stop
    assert {i.code for i in err.value.issues} == {"missing_value"}


def test_empty_and_header_only_files():
    with pytest.raises(DataImportError):
        load_csv(b"", ColumnMap(value="v"))
    with pytest.raises(DataImportError):
        load_csv(b"v\n", ColumnMap(value="v"))


def test_duplicate_header_is_an_error():
    with pytest.raises(DataImportError) as err:
        load_csv(b"v,v\n1,2\n", ColumnMap(value="v"))
    assert err.value.issues[0].code == "duplicate_header"


def test_non_contiguous_subgroup_and_unordered_time_give_warnings():
    raw = (
        "t,v,lot\n"
        "2026-10-02 09:00:00,1,a\n"
        "2026-10-02 08:00:00,2,b\n"
        "2026-10-02 10:00:00,3,a\n"
    ).encode()
    d = load_csv(raw, ColumnMap(value="v", subgroup="lot", timestamp="t"))
    assert any("not contiguous" in w for w in d.warnings)
    assert any("time order" in w for w in d.warnings)


def test_invalid_flag_creates_marks_and_needs_a_reason():
    raw = (
        "v,ok,why,who\n"
        "1.0,1,,\n"
        "99.0,0,wrong part measured,Chen\n"
        "1.1,是,,\n"
        "98.0,無效,calibration part,Chen\n"
    ).encode("utf-8")
    cols = ColumnMap(value="v", valid="ok", invalid_reason="why", invalid_by="who")
    d = load_csv(raw, cols)
    assert d.n_invalid == 2 and d.n_total == 4
    assert d.invalid_info()[1][:2] == ("wrong part measured", "Chen")
    bad = b"v,ok,why\n1.0,0,\n"
    with pytest.raises(DataImportError) as err:
        load_csv(bad, ColumnMap(value="v", valid="ok", invalid_reason="why"))
    assert err.value.issues[0].code == "invalid_without_reason"
    with pytest.raises(DataImportError):
        load_csv(b"v,ok\n1.0,maybe\n", ColumnMap(value="v", valid="ok"))


def test_round_trip_keeps_values_marks_tags_and_source_rows(tmp_path):
    d = load_csv(BASIC, COLS).mark_invalid([2], "operator logged the wrong lot", "Chen", at="2026-10-02T09:00:00+00:00")
    path = tmp_path / "out.csv"
    to_csv(d, path)
    back = load_csv(path, export_columns(d))
    assert back.values.tolist() == d.values.tolist()
    assert back.subgroup.tolist() == d.subgroup.tolist()
    assert back.tags["machine"].tolist() == d.tags["machine"].tolist()
    assert back.timestamp.tolist() == d.timestamp.tolist()
    assert back.source_rows.tolist() == d.source_rows.tolist()
    assert back.invalid_info() == d.invalid_info()
    assert back.n_valid == 3


def test_export_of_a_clean_dataset_marks_everything_valid():
    d = Dataset.from_values([1.0, 2.0, 3.0])
    text = to_csv(d)
    assert text.splitlines()[0] == "source_row,value,valid,invalid_reason,invalid_by,invalid_at"
    assert text.splitlines()[1].startswith("1,1.0,1,")


def test_invalid_rows_survive_export_and_the_file_reads_in_excel_encoding(tmp_path):
    d = Dataset.from_values([1.0, 2.0]).mark_invalid([0], "量測錯誤", "陳")
    path = tmp_path / "x.csv"
    to_csv(d, path)  # utf-8-sig, which Excel opens correctly
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    back = load_csv(path, export_columns(d))
    assert back.invalid_info()[0][:2] == ("量測錯誤", "陳")


def test_imported_data_goes_into_a_chart_without_the_marked_value():
    rng = np.random.default_rng(4)
    lines = ["v,lot,ok,why"]
    for g in range(10):
        for j in range(5):
            v = rng.normal(5, 0.1) + (3.0 if (g, j) == (4, 2) else 0.0)
            bad = (g, j) == (4, 2)
            lines.append(f"{v:.4f},G{g},{0 if bad else 1},{'typing error' if bad else ''}")
    d = load_csv("\n".join(lines).encode(), ColumnMap(value="v", subgroup="lot", valid="ok", invalid_reason="why"))
    assert d.n_invalid == 1
    sg = d.subgroups(incomplete="drop")
    assert sg.matrix.shape == (9, 5)
    assert abs(sg.matrix.max() - 5) < 1  # the marked value of about 8 is not in the matrix

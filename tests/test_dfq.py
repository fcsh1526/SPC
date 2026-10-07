"""The descriptive data of ISO/TR 11462-5 (*.DFD, *.DFQ): the rules of clauses 4.1 to 4.3 and the tables 1 and 2 of the copy of the standard that was available."""

import pytest

from spc.data import dfq
from tests.conftest import logged_in_client, make_app
from tests.test_api import err

FILE = "\r\n".join([
    "K0100 3",
    "K1001/1 4711-A",
    "K1002/1 Shaft 12 mm",
    "K1086/1 Op 20 turning",
    "K2001/1 1",
    "K2002/1 Diameter",
    "K2142/1 mm",
    "K2101/1 12.000",
    "K2110/1 11.98",
    "K2111/1 12,02",
    "K2112/1 -0.02",
    "K2113/1 0.02",
    "K2404/1 0.001",
    "K2630/1 0.002",
    "K2001/2 2",
    "K2002/2 Length",
    "K2101/2 50.0",
    "K2112/2 -0.1",
    "K2113/2 0.3",
    "K2001/3 3",
    "K2002/3 Flatness",
    "K2111/3 0.05",
    "K2022/0 3",
    "K2005/1 2",
]) + "\r\n"


def test_the_header_of_a_file_is_read_with_its_part_and_three_characteristics():
    r = dfq.parse(FILE)
    assert r["declared"] == 3 and r["part"]["part_number"] == "4711-A" and r["part"]["part_description"] == "Shaft 12 mm" and r["part"]["operation"] == "Op 20 turning"
    assert [c["name"] for c in r["characteristics"]] == ["Diameter", "Length", "Flatness"]
    d, ln, fl = r["characteristics"]
    assert (d["unit"], d["lsl"], d["usl"], d["nominal"], d["resolution"], d["calibration_uncertainty"]) == ("mm", 11.98, 12.02, 12.0, 0.001, 0.002)  # a decimal comma is read
    assert d["fields"]["class"] == 2 and d["raw"]["K2005"] == "2"  # a field of undefined content (marked "o"): kept, not interpreted
    # only the allowances: lsl = nominal - |lower allowance|, usl = nominal + |upper allowance|
    assert (ln["lsl"], ln["usl"], ln["nominal"]) == (49.9, 50.3, 50.0)
    assert fl["lsl"] is None and fl["usl"] == 0.05 and fl["nominal"] is None  # a one-sided limit
    assert [c["decimals"] for c in r["characteristics"]] == [3, 3, 3]  # K2022/0 is for all characteristics
    assert r["warnings"] == [] and r["values_present"] is False


def test_lf_alone_and_latin_1_files_are_read_too():
    text = FILE.replace("\r\n", "\n").replace("Shaft", "Wäl").encode("latin-1")
    assert dfq.parse(text)["part"]["part_description"] == "Wäl 12 mm"
    assert dfq.parse(FILE.encode("utf-8-sig"))["declared"] == 3


def test_the_rules_of_the_standard_are_checked():
    codes = lambda text: [w["code"] for w in dfq.parse(text)["warnings"]]
    base = ["K0100 1", "K1001 P", "K2001 1", "K2002 C"]
    assert codes("\n".join(base)) == []
    assert "part_field_after_characteristic" in codes("\n".join(["K0100 1", "K1001 P", "K2002 C", "K1002 late"]))  # "no more K1xxx fields may follow"
    assert "k0100_not_first" in codes("\n".join(["K1001 P", "K0100 1", "K2002 C"]))
    assert "field_too_long" in codes("\n".join(base + ["K2142 " + "m" * 21]))  # table 2: the unit is at most 20 characters
    assert "bad_number" in codes("\n".join(base + ["K2110 abc"]))
    assert "out_of_range" in codes("\n".join(base + ["K2022 40000"]))  # I5: up to 32767
    assert "index_above_k0100" in codes("\n".join(base + ["K2002/2 two"]))
    assert "no_part_field" in codes("\n".join(["K0100 1", "K2001 1", "K2002 C"]))
    assert "limits_not_ordered" in codes("\n".join(base + ["K2110 5", "K2111 4"]))
    assert "unknown_keys" in codes("\n".join(base + ["K2999 x"]))
    assert "characteristic_not_identified" in codes("\n".join(["K0100 1", "K1001 P", "K2142 mm"]))


def test_a_file_with_values_is_reported_and_the_values_are_not_read():
    r = dfq.parse(FILE + "12.001\r\n12.003\r\nK0001/1 12.0\r\n")
    assert r["values_present"] and any(w["code"] == "values_not_read" and w["lines"] == 3 for w in r["warnings"])


def test_a_file_that_is_not_of_this_format_is_refused():
    for bad in ("", "just text\nmore", "K1001 P\nK2001 1", "K0100 x\nK1001 P", "K0100 0\nK1001 P", "K0100 99999\nK1001 P"):
        with pytest.raises(dfq.DfqError):
            dfq.parse(bad)


def test_the_api_reads_a_file_and_refuses_what_is_not_one():
    client = logged_in_client(make_app(max_upload=200_000))
    r = client.post("/api/interchange/dfd", content=FILE.encode())
    assert r.status_code == 200 and r.json()["characteristics"][0]["usl"] == 12.02 and r.json()["part"]["part_number"] == "4711-A"
    assert err(client.post("/api/interchange/dfd", content=b"hello"))["code"] == "invalid_input"
    assert err(client.post("/api/interchange/dfd", content=b""))["code"] == "empty_file"
    assert logged_in_client(make_app(), "view").post("/api/interchange/dfd", content=FILE.encode()).status_code == 403
    assert client.post("/api/interchange/dfd", content=b"x" * 300_000).status_code == 413

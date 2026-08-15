"""Writing a workbook without a spreadsheet library.

The file is a zip of XML, so these read it back the way a reader would: unzip,
parse, and check the values arrived. That covers everything except whether Excel
itself is happy, which no test here can answer.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from io import BytesIO
from xml.etree import ElementTree

import pytest

from trendrelay_api import xlsx
from trendrelay_api.xlsx import column_name, escape, rows, workbook

NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
FIXED = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def sheet_rows(data: bytes) -> list[list[str]]:
    """Every cell value, as a reader would find them."""
    with zipfile.ZipFile(BytesIO(data)) as archive:
        tree = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in tree.findall(".//main:row", NS):
        cells = []
        for cell in row.findall("main:c", NS):
            inline = cell.find("main:is/main:t", NS)
            number = cell.find("main:v", NS)
            found = inline if inline is not None else number
            cells.append(found.text if found is not None else "")
        rows.append(cells)
    return rows


# --- the parts a reader needs -------------------------------------------------


def test_the_five_required_parts_are_all_present() -> None:
    with zipfile.ZipFile(BytesIO(workbook(["a"], [["b"]]))) as archive:
        assert set(archive.namelist()) == {
            "[Content_Types].xml",
            "_rels/.rels",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
            "xl/worksheets/sheet1.xml",
        }


def test_every_part_is_well_formed_xml() -> None:
    # A workbook with one malformed part does not open at all, and the reader
    # does not say which part.
    with zipfile.ZipFile(BytesIO(workbook(["a"], [["b"]]))) as archive:
        for name in archive.namelist():
            ElementTree.fromstring(archive.read(name))


# --- values arriving intact ---------------------------------------------------


def test_the_header_row_comes_first_then_the_rows() -> None:
    data = workbook(["Name", "Price"], [["Giấy ăn", 95000], ["Tẩy da chết", 183000]])

    assert sheet_rows(data) == [
        ["Name", "Price"],
        ["Giấy ăn", "95000"],
        ["Tẩy da chết", "183000"],
    ]


def test_a_workbook_can_be_read_back_for_an_import() -> None:
    data = workbook(["Item ID", "Product"], [["57860887539", "Giấy ăn"]])

    assert rows(data) == [["Item ID", "Product"], ["57860887539", "Giấy ăn"]]


def test_the_reader_preserves_sparse_cell_positions() -> None:
    data = workbook(["a", "b", "c"], [["x", None, "z"]])

    assert rows(data)[1] == ["x", "", "z"]


def test_the_reader_enforces_the_import_row_limit() -> None:
    data = workbook(["Item"], [[str(index)] for index in range(101)])

    with pytest.raises(ValueError, match="more than 100 data rows"):
        rows(data, maximum_rows=101)


def test_the_reader_rejects_a_workbook_that_expands_past_the_safety_limit(
    monkeypatch,
) -> None:
    data = workbook(["Name"], [["A perfectly ordinary product"]])
    monkeypatch.setattr(xlsx, "MAX_UNCOMPRESSED_BYTES", 20)

    with pytest.raises(ValueError, match="20 MB safety limit"):
        rows(data)


def test_vietnamese_names_survive_the_round_trip() -> None:
    """The reason this is a workbook rather than a CSV.

    Excel guesses a CSV's encoding and gets this wrong without a byte-order
    mark; a workbook declares it.
    """
    data = workbook(["Tên"], [["Giấy ăn rút Topgia thùng 40 gói"]])

    assert sheet_rows(data)[1] == ["Giấy ăn rút Topgia thùng 40 gói"]


def test_an_item_id_stays_the_digits_it_was() -> None:
    # The other reason. As a CSV, Excel reads 57860887539 as a number and shows
    # 5.78609E+10, losing the identity of the row.
    data = workbook(["Item"], [["57860887539"]])

    assert sheet_rows(data)[1] == ["57860887539"]


def test_a_number_is_written_as_a_number_and_text_as_text() -> None:
    with zipfile.ZipFile(BytesIO(workbook(["n", "s"], [[42, "42"]]))) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

    assert '<c r="A2"><v>42</v></c>' in sheet
    assert 'r="B2" t="inlineStr"' in sheet


def test_a_blank_cell_is_left_out_rather_than_written_empty() -> None:
    data = workbook(["a", "b", "c"], [["x", None, "z"]])

    # The reader sees two cells in that row, not three with a hole.
    assert sheet_rows(data)[1] == ["x", "z"]


def test_a_boolean_says_what_it_meant_rather_than_becoming_one() -> None:
    # In Python a bool is an int, so writing it as a number would put 1 in a
    # column whose question was yes or no.
    assert sheet_rows(workbook(["ok"], [[True]]))[1] == ["yes"]


# --- input that would otherwise break the file --------------------------------


def test_markup_in_a_product_name_is_escaped_rather_than_embedded() -> None:
    data = workbook(["Name"], [['Bánh <b>ngon</b> & "rẻ"']])

    assert sheet_rows(data)[1] == ['Bánh <b>ngon</b> & "rẻ"']


def test_a_control_character_is_dropped_instead_of_breaking_the_workbook() -> None:
    """XML 1.0 cannot carry one, and one scraped name would spoil the file."""
    data = workbook(["Name"], [["Before\x07After"]])

    assert sheet_rows(data)[1] == ["BeforeAfter"]


def test_a_trailing_space_in_a_name_is_preserved() -> None:
    assert sheet_rows(workbook(["n"], [["Topgia "]]))[1] == ["Topgia "]


# --- the small pieces ---------------------------------------------------------


def test_columns_count_the_way_a_spreadsheet_does() -> None:
    assert [column_name(n) for n in (1, 2, 26, 27, 28, 52, 53)] == [
        "A", "B", "Z", "AA", "AB", "AZ", "BA",
    ]


def test_escaping_leaves_ordinary_text_alone() -> None:
    assert escape("Giấy ăn") == "Giấy ăn"


def test_the_same_data_produces_the_same_bytes() -> None:
    # Only with the timestamp fixed - which is what makes two exports
    # comparable to each other rather than only to themselves.
    first = workbook(["a"], [["b"]], now=FIXED)
    second = workbook(["a"], [["b"]], now=FIXED)

    assert first == second


def test_an_empty_sheet_is_still_a_readable_workbook() -> None:
    assert sheet_rows(workbook(["Only a header"], [])) == [["Only a header"]]

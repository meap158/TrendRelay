"""Write a single-sheet workbook, using nothing but the standard library.

A `.xlsx` is a zip of XML parts, and the subset needed for one sheet of values
is small enough to write directly. That is the whole reason this exists: the
alternative was adding a spreadsheet library to the API for one export.

CSV was the other option and is deliberately not it. Excel decides a CSV's
encoding by guessing, so Vietnamese product names arrive as mojibake unless the
file carries a byte-order mark, and it reads long digit strings - which is what
a Shopee item id is - as numbers, turning 57860887539 into 5.78609E+10 and
losing the identity of the row. A workbook says what each cell is, so neither
question comes up.

Strings are written inline rather than through a shared-strings table. Product
names repeat rarely enough that the table would save little, and leaving it out
removes a whole part and its cross-references from something that has to be
exactly right to open at all.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

#: The namespace roots every part below hangs off. Named once because the
#: strings are long, fixed by the specification, and unreadable inline.
_PACKAGE = "http://schemas.openxmlformats.org/package/2006"
_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006"
_SPREADSHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOCUMENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml"

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Types xmlns="{_PACKAGE}/content-types">'
    '<Default Extension="rels"'
    f' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml"'
    f' ContentType="{_DOCUMENT_TYPE}.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml"'
    f' ContentType="{_DOCUMENT_TYPE}.worksheet+xml"/>'
    "</Types>"
)

ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{_PACKAGE}/relationships">'
    f'<Relationship Id="rId1" Type="{_OFFICE}/relationships/officeDocument"'
    ' Target="xl/workbook.xml"/>'
    "</Relationships>"
)

WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{_PACKAGE}/relationships">'
    f'<Relationship Id="rId1" Type="{_OFFICE}/relationships/worksheet"'
    ' Target="worksheets/sheet1.xml"/>'
    "</Relationships>"
)


def _workbook(sheet_name: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{_SPREADSHEET}"'
        f' xmlns:r="{_OFFICE}/relationships">'
        f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )


def escape(value: str) -> str:
    """XML-escape, and drop what XML 1.0 cannot carry at all.

    Control characters are stripped rather than escaped: they have no legal
    representation here, and a single one from a scraped product name would
    make the whole workbook unopenable.
    """
    text = (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return "".join(
        character for character in text
        if character in "\t\n\r" or ord(character) >= 0x20
    )


def column_name(index: int) -> str:
    """1 to A, 26 to Z, 27 to AA - the spreadsheet's own bijective base 26."""
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def _cell(reference: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    # Booleans first: in Python they are integers, and writing True as 1 loses
    # what the column meant.
    if isinstance(value, bool):
        return f'<c r="{reference}" t="inlineStr"><is><t>{"yes" if value else "no"}</t></is></c>'
    if isinstance(value, int | float):
        return f'<c r="{reference}"><v>{value}</v></c>'
    text = escape(str(value))
    # `xml:space` kept so a name with a trailing space survives a reader that
    # would otherwise trim it.
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<worksheet xmlns="{_SPREADSHEET}">',
        "<sheetData>",
    ]
    for number, row in enumerate([headers, *rows], start=1):
        cells = "".join(
            _cell(f"{column_name(column)}{number}", value)
            for column, value in enumerate(row, start=1)
        )
        lines.append(f'<row r="{number}">{cells}</row>')
    lines.append("</sheetData></worksheet>")
    return "".join(lines)


def workbook(
    headers: list[str],
    rows: list[list[Any]],
    *,
    sheet_name: str = "Sheet1",
    now: datetime | None = None,
) -> bytes:
    """One sheet of values, as the bytes of a `.xlsx` file.

    `now` fixes the timestamp written into the zip entries, so the same data
    produces the same bytes - which is what lets a test compare one export with
    another rather than only with itself.
    """
    # The five parts a reader needs. Anything beyond these - styles, themes,
    # calculation chains - is optional, and every one left out is a part that
    # cannot be inconsistent with the others.
    stamp = (now or datetime.now(UTC)).timetuple()[:6]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in (
            ("[Content_Types].xml", CONTENT_TYPES),
            ("_rels/.rels", ROOT_RELS),
            ("xl/workbook.xml", _workbook(sheet_name)),
            ("xl/_rels/workbook.xml.rels", WORKBOOK_RELS),
            ("xl/worksheets/sheet1.xml", _sheet(headers, rows)),
        ):
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, text)
    return buffer.getvalue()

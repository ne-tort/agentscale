"""Unit tests — tabular csv/xlsx → sqlite index."""

from __future__ import annotations

import sqlite3
import zipfile
from io import BytesIO
from xml.etree.ElementTree import Element, SubElement, tostring

from prodavan.application.content.tabular_index import index_csv_bytes, index_tabular_bytes, index_xlsx_bytes


def test_index_csv_bytes_basic() -> None:
    raw = b"name,part_number,price\nMouse,M1,10\nKeyboard,K1,20\n"
    result = index_csv_bytes(raw)
    assert result.row_count == 2
    assert "name" in result.columns
    assert "part_number" in result.columns
    conn = sqlite3.connect(":memory:")
    conn.deserialize(result.sqlite_bytes)
    rows = conn.execute("SELECT name, part_number FROM rows ORDER BY name").fetchall()
    assert rows == [("Keyboard", "K1"), ("Mouse", "M1")]
    conn.close()


def _minimal_xlsx() -> bytes:
    """Build a tiny xlsx with one sheet and shared string header."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
            <Default Extension="xml" ContentType="application/xml"/>
            <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
            <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
            <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
            </Types>""",
        )
        zf.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
            <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>
            </workbook>""",
        )
        ss = Element("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sst")
        for text in ("name", "sku", "Widget", "W-1"):
            si = SubElement(ss, "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si")
            t = SubElement(si, "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
            t.text = text
        zf.writestr("xl/sharedStrings.xml", tostring(ss))
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
                <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>
              </sheetData>
            </worksheet>""",
        )
    return buf.getvalue()


def test_index_xlsx_bytes_basic() -> None:
    result = index_xlsx_bytes(_minimal_xlsx())
    assert result.row_count == 1
    assert "name" in result.columns
    conn = sqlite3.connect(":memory:")
    conn.deserialize(result.sqlite_bytes)
    row = conn.execute("SELECT name, sku FROM rows").fetchone()
    assert row == ("Widget", "W-1")
    conn.close()


def test_index_tabular_rejects_xls_extension() -> None:
    try:
        index_tabular_bytes(b"\xd0\xcf\x11\xe0", filename="old.xls")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "xls" in str(exc).lower()


def test_catalog_sqlite_query_part_number(tmp_path) -> None:
    from prodavan.application.modules.catalog_sqlite_query import query_catalog_sqlite

    indexed = index_csv_bytes(b"title,part_number\nA,PN-1\nB,PN-2\n")
    db = tmp_path / "cat1.sqlite"
    db.write_bytes(indexed.sqlite_bytes)
    hits = query_catalog_sqlite(db, part_number="PN-2")
    assert len(hits) == 1
    assert hits[0].values["title"] == "B"

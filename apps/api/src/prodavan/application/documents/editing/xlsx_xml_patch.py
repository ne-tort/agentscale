"""XML patch primitives for template xlsx workbooks (bytes in → bytes out).

Ported from the Commerce KP exporter (``tools/kp/pack.py``): values and
formula caches are written straight into the sheet XML and the zip is
rewritten in place, so ``styles.xml`` / ``sharedStrings.xml`` / media stay
byte-identical and the template layout (merges, drawings, print setup) is
never rebuilt. No file paths here — callers pass and receive bytes.

The Commerce lookup machinery (hidden ``_Lookup`` / ``_PN`` sheets, dropdown
data validations) is deliberately NOT ported: the platform writes values
directly; template formulas keep their caches refreshed instead.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

SML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
X14AC = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
XR = "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"
XR2 = "http://schemas.microsoft.com/office/spreadsheetml/2015/revision2"
XR3 = "http://schemas.microsoft.com/office/spreadsheetml/2016/revision3"

XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
DRAWINGML = "http://schemas.openxmlformats.org/drawingml/2006/main"

# Управляющие символы, недопустимые в XML 1.0 / листах Excel (тот же набор,
# что openpyxl ILLEGAL_CHARACTERS_RE, плюс XML non-characters U+FFFE/FFFF).
# Приходят из каталожных данных (парсеры прайсов поставщиков) и роняют
# экспорт: openpyxl — IllegalCharacterError, голый ET — невалидный XML.
_ILLEGAL_SHEET_CHARS_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def sanitize_sheet_text(value: str) -> str:
    """Убирает символы, с которыми файл xlsx не откроется/не соберётся."""
    return _ILLEGAL_SHEET_CHARS_RE.sub("", value)


def register_namespaces() -> None:
    """Stable prefixes so re-serialization keeps the template's namespace map."""
    ET.register_namespace("", SML)
    ET.register_namespace("r", REL)
    ET.register_namespace("mc", MC)
    ET.register_namespace("x14ac", X14AC)
    ET.register_namespace("xr", XR)
    ET.register_namespace("xr2", XR2)
    ET.register_namespace("xr3", XR3)


def register_drawing_namespaces() -> None:
    """Prefixes for spreadsheetDrawing parts (stamps / logos anchors)."""
    ET.register_namespace("xdr", XDR)
    ET.register_namespace("a", DRAWINGML)
    ET.register_namespace("r", REL)


def dumps_sml(root: ET.Element) -> bytes:
    register_namespaces()
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def dumps_drawing(root: ET.Element) -> bytes:
    register_drawing_namespaces()
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _q(tag: str) -> str:
    return f"{{{SML}}}{tag}"


def _qdr(tag: str) -> str:
    return f"{{{XDR}}}{tag}"


def rewrite_xlsx_bytes(
    data: bytes,
    updates: dict[str, bytes],
    *,
    deletes: set[str] | None = None,
) -> bytes:
    """Rewrite zip entries in place; untouched parts keep their original bytes.

    ``updates`` maps part names to new payloads (existing entries are replaced,
    unknown names are appended). ``deletes`` removes entries. Entry order and
    per-entry compression settings of the original archive are preserved.
    """
    deletes = deletes or set()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data), "r") as zin, zipfile.ZipFile(out, "w") as zout:
        pending = dict(updates)
        for item in zin.infolist():
            if item.filename in deletes:
                continue
            payload = pending.pop(item.filename, None)
            zout.writestr(item, payload if payload is not None else zin.read(item.filename))
        for name, payload in pending.items():
            zout.writestr(name, payload, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def ensure_col_widths(root: ET.Element, widths: dict[int, float]) -> None:
    """Widen columns so ``#,##0.00`` money formats do not render as ``###``."""
    cols = root.find(_q("cols"))
    if cols is None:
        cols = ET.Element(_q("cols"))
        fmt = root.find(_q("sheetFormatPr"))
        if fmt is not None:
            root.insert(list(root).index(fmt) + 1, cols)
        else:
            root.insert(0, cols)
    by_min = {int(c.get("min") or 0): c for c in cols.findall(_q("col"))}
    for idx, width in widths.items():
        el = by_min.get(idx)
        if el is None:
            el = ET.SubElement(cols, _q("col"))
            el.set("min", str(idx))
            el.set("max", str(idx))
            el.set("customWidth", "true")
            el.set("width", f"{width:g}")
            continue
        try:
            cur = float(el.get("width") or 0)
        except ValueError:
            cur = 0.0
        if cur < width:
            el.set("width", f"{width:g}")
            el.set("customWidth", "true")


def cell_in_row(row: ET.Element, col: str) -> ET.Element:
    """Find (or append) the ``<c>`` for column ``col`` inside ``row``."""
    ref = f"{col}{row.get('r')}"
    for c in row.findall(_q("c")):
        if c.get("r") == ref:
            return c
    el = ET.SubElement(row, _q("c"))
    el.set("r", ref)
    return el


def clear_value(cell: ET.Element) -> None:
    """Drop value/formula children and the type attribute."""
    for child in list(cell):
        if child.tag in {_q("v"), _q("is"), _q("f")}:
            cell.remove(child)
    if "t" in cell.attrib:
        del cell.attrib["t"]


def set_number(cell: ET.Element, value: Any) -> None:
    """Write a plain numeric value (replaces formula/cache if any)."""
    clear_value(cell)
    v = ET.SubElement(cell, _q("v"))
    if isinstance(value, bool):
        v.text = "1" if value else "0"
    elif isinstance(value, int):
        v.text = str(value)
    else:
        v.text = str(value)


def set_text(cell: ET.Element, value: str) -> None:
    """Write an inline string value (keeps the cell style attribute)."""
    clear_value(cell)
    cell.set("t", "inlineStr")
    is_el = ET.SubElement(cell, _q("is"))
    t = ET.SubElement(is_el, _q("t"))
    value = sanitize_sheet_text(value)
    if value[:1].isspace() or value[-1:].isspace():
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = value


def num_text(value: float | int) -> str:
    """Compact cached-number text: ints without trailing ``.0``."""
    x = float(value)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.10g}"


def set_cached_number(cell: ET.Element, value: float | int) -> None:
    """Replace only the cached ``<v>`` — the ``<f>`` formula is preserved."""
    cell.set("t", "n")
    for child in list(cell):
        if child.tag == _q("v"):
            cell.remove(child)
    v = ET.SubElement(cell, _q("v"))
    v.text = num_text(value)


def set_formula(
    cell: ET.Element,
    formula: str,
    *,
    as_str: bool = False,
    cached: float | int | None = None,
) -> None:
    """Write a formula (optionally with a numeric cache / as a str formula)."""
    clear_value(cell)
    if as_str:
        cell.set("t", "str")
    f = ET.SubElement(cell, _q("f"))
    f.text = formula[1:] if formula.startswith("=") else formula
    if cached is not None and not as_str:
        set_cached_number(cell, cached)


def cell_float(cell: ET.Element | None) -> float | None:
    """Read the cached numeric value of a cell (None when absent/bad)."""
    if cell is None:
        return None
    v = cell.find(_q("v"))
    if v is None or v.text is None:
        return None
    try:
        return float(v.text)
    except ValueError:
        return None


def find_row(sheet_data: ET.Element, row_num: int) -> ET.Element | None:
    key = str(row_num)
    for row in sheet_data.findall(_q("row")):
        if row.get("r") == key:
            return row
    return None


def drawing_row_tag(tag: str) -> str:
    """Qualified spreadsheetDrawing tag name (for row shifting)."""
    return _qdr(tag)

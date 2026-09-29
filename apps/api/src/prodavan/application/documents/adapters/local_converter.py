"""Local in-proc conversion adapter (fallback, no network / no LibreOffice).

Routes (stdlib + openpyxl/python-docx only):
- xlsx → csv            (openpyxl, active sheet)
- docx → txt / md       (python-docx)
- xlsx → xlsx, docx → docx, text-family (txt/md/csv) passthrough (identity)

Everything else (legacy .doc/.rtf/.odt sources, pdf→office, cross-suite
office→office) raises ``CONVERSION_UNAVAILABLE``: convert to pdf first via
Gotenberg, then re-enter the pipeline for a local route.
"""

from __future__ import annotations

from prodavan.application.documents.editing import docx as docx_editing
from prodavan.application.documents.editing import xlsx as xlsx_editing
from prodavan.application.documents.ports.converter import ConvertedDocument
from prodavan.domain.documents.types import (
    detect_format,
    mime_for_format,
    replace_extension,
)
from prodavan.domain.errors import AppError

# Formats the local adapter can hand back unchanged (text family passthrough).
_IDENTITY_FORMATS = frozenset({"txt", "md", "csv"})


def _unsupported(filename: str, target_format: str) -> AppError:
    src = detect_format(filename)
    return AppError(
        code="CONVERSION_UNAVAILABLE",
        title="Conversion Unavailable",
        status=422,
        detail=(
            f"no local route for {src!r} -> {target_format!r}; "
            "pdf targets require the gotenberg converter"
        ),
    )


class LocalConverter:
    """Offline fallback converter — never touches the network."""

    async def ping(self) -> bool:
        return True

    async def convert(self, data: bytes, filename: str, target_format: str) -> ConvertedDocument:
        if not data:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="empty document payload",
            )
        target = (target_format or "").strip().lower()
        src = detect_format(filename)
        out_name = replace_extension(filename, target)

        # Identity / passthrough: same format or within the text family.
        if src == target or (src in _IDENTITY_FORMATS and target in _IDENTITY_FORMATS):
            return ConvertedDocument(data=data, mime=mime_for_format(target), filename=out_name)

        if src == "xlsx":
            if target == "csv":
                csv_text = xlsx_editing.sheet_to_csv(data)
                return ConvertedDocument(
                    data=csv_text.encode("utf-8"), mime="text/csv", filename=out_name
                )
            raise _unsupported(filename, target)

        if src == "docx":
            if target == "txt":
                text = docx_editing.read_text(data)
                return ConvertedDocument(
                    data=text.encode("utf-8"), mime="text/plain", filename=out_name
                )
            if target == "md":
                text = docx_editing.read_markdown(data)
                return ConvertedDocument(
                    data=text.encode("utf-8"), mime="text/markdown", filename=out_name
                )
            raise _unsupported(filename, target)

        raise _unsupported(filename, target)

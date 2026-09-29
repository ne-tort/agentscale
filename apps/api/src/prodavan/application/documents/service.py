"""DocumentsService — in-proc façade for convert / read / create / fill (DOCUM).

Routing summary (see adapters for details):
- ``→ pdf``      → Gotenberg converter when the lifespan manager provides one;
                   otherwise the local adapter (which answers 422 for office
                   sources — only pass-through identities succeed offline).
- ``→ non-pdf``  → local in-proc adapter (openpyxl / python-docx routes).

Outputs are persisted as content assets via ``UploadService`` and returned as
canonical FileRefs (the same shape ``CabinetContentUploadService`` produces).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.upload_service import UploadService
from prodavan.application.documents.adapters.local_converter import LocalConverter
from prodavan.application.documents.ports.converter import DocumentConverterPort
from prodavan.application.documents.publish import (
    EVENT_DOCUMENTS_CONVERTED,
    EVENT_DOCUMENTS_CREATED,
    EVENT_DOCUMENTS_READ,
    EVENT_DOCUMENTS_TEMPLATE_FILLED,
    emit_documents_event,
    emit_op_metric,
)
from prodavan.domain.documents.types import (
    DEFAULT_FILENAME_STEM,
    METRIC_DOCUMENTS_CONVERSIONS,
    METRIC_DOCUMENTS_CREATIONS,
    METRIC_DOCUMENTS_READS,
    METRIC_DOCUMENTS_TEMPLATE_FILLS,
    detect_format,
    mime_for_format,
    replace_extension,
    validate_source_format,
    validate_target_format,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.content import ContentBlobVersionRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

logger = logging.getLogger(__name__)

_mb = 1024 * 1024


def resolve_default_converter() -> DocumentConverterPort:
    """Gotenberg converter when the manager is live, else local fallback."""
    from prodavan.core.infra.gotenberg_manager import get_gotenberg_manager

    mgr = get_gotenberg_manager()
    if mgr is not None and mgr.enabled and mgr.converter is not None:
        return mgr.converter
    return LocalConverter()


def _file_ref(
    *,
    asset_id: str,
    version_id: str,
    storage_key: str | None,
    filename: str,
    data: bytes,
) -> dict[str, Any]:
    """Canonical FileRef shape (mirrors cabinet/module upload services)."""
    return {
        "asset_id": asset_id,
        "version_id": version_id,
        "blob_version_id": version_id,
        "storage_key": storage_key,
        "filename": filename,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class DocumentsService:
    """Façade used by the pod-facing HTTP API and module metasyntax actions."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        converter: DocumentConverterPort | None = None,
    ) -> None:
        self._session = session
        self._converter: DocumentConverterPort | None = converter

    # ------------------------------------------------------------------ helpers

    def _converter_for(self, *, target_format: str) -> DocumentConverterPort:
        if self._converter is not None:
            return self._converter
        # Lazy resolve keeps tests free of a started lifespan.
        return resolve_default_converter()

    async def _read_source(self, source: bytes | dict[str, Any]) -> bytes:
        """Accept inline bytes or a FileRef dict (storage_key blob)."""
        if isinstance(source, (bytes, bytearray, memoryview)):
            return bytes(source)
        if isinstance(source, dict):
            storage_key = str(source.get("storage_key") or "").strip()
            if not storage_key:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="file_ref.storage_key required",
                )
            return ensure_file_store().get_bytes_sync(storage_key)
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="source must be bytes or a file_ref dict",
        )

    def _check_input_size(self, data: bytes) -> None:
        from prodavan.config.settings import settings

        limit = int(settings.documents_max_input_mb) * _mb
        if limit > 0 and len(data) > limit:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"input document exceeds {settings.documents_max_input_mb} MB",
            )

    def _check_output_size(self, data: bytes) -> None:
        from prodavan.config.settings import settings

        limit = int(settings.documents_max_output_mb) * _mb
        if limit > 0 and len(data) > limit:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"output document exceeds {settings.documents_max_output_mb} MB",
            )

    async def _upload(
        self,
        *,
        data: bytes,
        filename: str,
        mime: str,
        company_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        link_id: str | None = None,
    ) -> dict[str, Any]:
        if self._session is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="session required to persist document output",
            )
        upload = UploadService(self._session)
        asset_id, version_id = await upload.upload_bytes_as_asset(
            data=data,
            owner_company_id=company_id,
            principal=principal,
            employee=employee,
            mime=mime,
            title=filename,
            # 1:1 per (link_kind, link_id): only link when an explicit target
            # id is given (module row, etc.) — plain conversions stay unlinked,
            # same as cabinet uploads.
            link_kind="documents" if link_id else None,
            link_id=link_id,
        )
        ver = await self._session.get(ContentBlobVersionRow, version_id)
        return _file_ref(
            asset_id=asset_id,
            version_id=version_id,
            storage_key=ver.storage_key if ver else None,
            filename=filename,
            data=data,
        )

    # ------------------------------------------------------------------ public API

    async def save_document(
        self,
        data: bytes,
        *,
        filename: str,
        mime: str,
        company_id: str | None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        """Persist ready-made document bytes (e.g. filled budget template)."""
        self._check_output_size(data)
        ref = await self._upload(
            data=data,
            filename=filename,
            mime=mime,
            company_id=company_id,
            principal=principal,
            employee=employee,
        )
        await emit_documents_event(
            session=self._session,
            event_type=EVENT_DOCUMENTS_CREATED,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            payload={"filename": filename, "size": len(data), "asset_id": ref["asset_id"]},
        )
        await emit_op_metric(
            session=self._session,
            metric=METRIC_DOCUMENTS_CREATIONS,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return ref

    async def convert(
        self,
        source: bytes | dict[str, Any],
        *,
        filename: str,
        target_format: str,
        company_id: str,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        principal: Principal,
        employee: EmployeeRow | None,
        session: AsyncSession | None = None,
        link_id: str | None = None,
    ) -> dict[str, Any]:
        """Convert a document (bytes or FileRef) and persist the output asset."""
        try:
            target = validate_target_format(target_format)
            src_fmt = validate_source_format(detect_format(filename))
        except ValueError as exc:
            raise AppError(
                code="VALIDATION_ERROR", title="Validation Error", status=422, detail=str(exc)
            ) from exc
        data = await self._read_source(source)
        self._check_input_size(data)
        # pdf targets need LibreOffice (Gotenberg); other targets run in-proc.
        converter = self._converter or resolve_default_converter()
        if target == "pdf" and src_fmt != "pdf":
            from prodavan.application.documents.adapters.local_converter import LocalConverter

            if isinstance(converter, LocalConverter):
                raise AppError(
                    code="CONVERSION_UNAVAILABLE",
                    title="Conversion Unavailable",
                    status=422,
                    detail=f"{src_fmt} -> pdf requires the gotenberg converter (not configured)",
                )
        converted = await converter.convert(data, filename, target)
        self._check_output_size(converted.data)
        out_name = replace_extension(filename, target)
        ref = await self._upload(
            data=converted.data,
            filename=out_name,
            mime=converted.mime or mime_for_format(target),
            company_id=company_id,
            principal=principal,
            employee=employee,
            link_id=link_id,
        )
        await emit_documents_event(
            session=session,
            event_type=EVENT_DOCUMENTS_CONVERTED,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            payload={
                "source_format": src_fmt,
                "target_format": target,
                "filename": out_name,
                "size": converted.data and len(converted.data),
                "asset_id": ref["asset_id"],
            },
        )
        await emit_op_metric(
            session=session,
            metric=METRIC_DOCUMENTS_CONVERSIONS,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return ref

    async def read_document(
        self,
        source: bytes | dict[str, Any],
        *,
        filename: str,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        session: AsyncSession | None = None,
        sheet: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Structured read: xlsx → sheets+rows; docx/pdf/text → text."""
        from prodavan.application.documents.editing import docx as docx_editing
        from prodavan.application.documents.editing import pdf_text
        from prodavan.application.documents.editing import xlsx as xlsx_editing

        try:
            fmt = validate_source_format(detect_format(filename))
        except ValueError as exc:
            raise AppError(
                code="VALIDATION_ERROR", title="Validation Error", status=422, detail=str(exc)
            ) from exc
        data = await self._read_source(source)
        self._check_input_size(data)
        if fmt == "xlsx":
            sheets = xlsx_editing.list_sheets(data)
            rows = xlsx_editing.read_rows(data, sheet=sheet, limit=limit)
            result: dict[str, Any] = {
                "kind": "xlsx",
                "format": fmt,
                "sheets": sheets,
                "sheet": sheet or (sheets[0] if sheets else None),
                "rows": rows,
            }
        elif fmt == "docx":
            result = {"kind": "docx", "format": fmt, "text": docx_editing.read_text(data)}
        elif fmt == "pdf":
            result = {"kind": "pdf", "format": fmt, "text": pdf_text.pdf_extract_text(data)}
        else:
            result = {
                "kind": "text",
                "format": fmt,
                "text": data.decode("utf-8", errors="replace").strip(),
            }
        await emit_documents_event(
            session=session,
            event_type=EVENT_DOCUMENTS_READ,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            payload={"format": fmt, "filename": filename},
        )
        await emit_op_metric(
            session=session,
            metric=METRIC_DOCUMENTS_READS,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return result

    async def create_document(
        self,
        *,
        format: str,
        spec: dict[str, Any],
        company_id: str,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        principal: Principal,
        employee: EmployeeRow | None,
        session: AsyncSession | None = None,
        filename: str | None = None,
        link_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new document from a declarative spec (xlsx / docx / text)."""
        from prodavan.application.documents.editing import docx as docx_editing
        from prodavan.application.documents.editing import xlsx as xlsx_editing

        fmt = (format or "").strip().lower().lstrip(".")
        if fmt not in {"xlsx", "docx", "txt", "csv", "md"}:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"documents.create supports xlsx/docx/txt/csv/md, got {fmt!r}",
            )
        if not isinstance(spec, dict):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="spec must be an object",
            )
        if fmt == "xlsx":
            data = xlsx_editing.create_workbook(
                xlsx_editing.SheetSpec(
                    name=str(spec.get("sheet") or "Sheet1"),
                    rows=_as_rows(spec.get("rows")),
                    header=_as_header(spec.get("header")),
                )
            )
        elif fmt == "docx":
            data = docx_editing.create_docx(
                [
                    docx_editing.ParagraphSpec(
                        text=str(p.get("text") or ""),
                        style=_opt_str(p.get("style")),
                        heading=(int(p["heading"]) if isinstance(p.get("heading"), int) else None),
                        bold=bool(p.get("bold")),
                    )
                    for p in _as_paragraphs(spec.get("paragraphs"))
                ]
            )
        else:
            raw_text = spec.get("text")
            if raw_text is None:
                rows = _as_rows(spec.get("rows"))
                if fmt == "csv":
                    import csv as _csv
                    import io as _io

                    buf = _io.StringIO()
                    writer = _csv.writer(buf, lineterminator="\n")
                    for row in rows:
                        writer.writerow([_cell(v) for v in row])
                    raw_text = buf.getvalue()
                else:
                    raw_text = "\n".join(", ".join(_cell(v) for v in row) for row in rows)
            if not isinstance(raw_text, str):
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="spec.text (or spec.rows) required for text/csv creation",
                )
            data = raw_text.encode("utf-8")
        out_name = (filename or "").strip() or f"{DEFAULT_FILENAME_STEM}.{fmt}"
        self._check_output_size(data)
        ref = await self._upload(
            data=data,
            filename=out_name,
            mime=mime_for_format(fmt),
            company_id=company_id,
            principal=principal,
            employee=employee,
            link_id=link_id,
        )
        await emit_documents_event(
            session=session,
            event_type=EVENT_DOCUMENTS_CREATED,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            payload={"format": fmt, "filename": out_name, "asset_id": ref["asset_id"]},
        )
        await emit_op_metric(
            session=session,
            metric=METRIC_DOCUMENTS_CREATIONS,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return ref

    async def fill_template(
        self,
        template: bytes | dict[str, Any],
        *,
        filename: str | None = None,
        data: dict[str, Any],
        output_format: str | None = None,
        company_id: str,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        principal: Principal,
        employee: EmployeeRow | None,
        session: AsyncSession | None = None,
        output_column: str | None = None,
        link_id: str | None = None,
    ) -> dict[str, Any]:
        """Fill a docx (docxtpl context) or xlsx (cells) template, persist output.

        ``data`` shapes: ``{"context": {...}}`` for docx Jinja templates and
        ``{"sheet": "Name", "cells": {"A1": value}}`` for xlsx. ``output_format``
        optionally converts the filled result (e.g. docx → pdf).
        """
        from prodavan.application.documents.editing import docx_template
        from prodavan.application.documents.editing import xlsx as xlsx_editing

        if not isinstance(data, dict):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="data must be an object",
            )
        tpl_bytes = await self._read_source(template)
        self._check_input_size(tpl_bytes)
        tpl_name = (filename or "").strip()
        if not tpl_name and isinstance(template, dict):
            tpl_name = str(template.get("filename") or "").strip()
        if not tpl_name:
            tpl_name = "template.docx"
        fmt = detect_format(tpl_name)
        if fmt == "docx":
            context = data.get("context") if isinstance(data.get("context"), dict) else None
            if context is None:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="docx template fill requires data.context mapping",
                )
            filled = docx_template.render_docx_template(tpl_bytes, context)
        elif fmt == "xlsx":
            cells = data.get("cells") if isinstance(data.get("cells"), dict) else None
            if not cells:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="xlsx template fill requires data.cells mapping",
                )
            filled = xlsx_editing.fill_template_cells(
                tpl_bytes, sheet=_opt_str(data.get("sheet")), cells=cells
            )
        else:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"template fill supports docx/xlsx templates, got {fmt!r}",
            )
        out_fmt = (output_format or "").strip().lower().lstrip(".") or fmt
        if out_fmt != fmt:
            return await self.convert(
                filled,
                filename=tpl_name,
                target_format=out_fmt,
                company_id=company_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
                principal=principal,
                employee=employee,
                session=session,
                link_id=link_id,
            )
        out_name = replace_extension(tpl_name, out_fmt)
        self._check_output_size(filled)
        ref = await self._upload(
            data=filled,
            filename=out_name,
            mime=mime_for_format(out_fmt),
            company_id=company_id,
            principal=principal,
            employee=employee,
            link_id=link_id,
        )
        await emit_documents_event(
            session=session,
            event_type=EVENT_DOCUMENTS_TEMPLATE_FILLED,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            payload={
                "template_format": fmt,
                "output_format": out_fmt,
                "filename": out_name,
                "asset_id": ref["asset_id"],
            },
        )
        await emit_op_metric(
            session=session,
            metric=METRIC_DOCUMENTS_TEMPLATE_FILLS,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return ref


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_rows(value: Any) -> list[list[Any]]:
    if not isinstance(value, list):
        return []
    out: list[list[Any]] = []
    for row in value:
        if isinstance(row, list):
            out.append(list(row))
        elif isinstance(row, dict):
            out.append(list(row.values()))
        else:
            out.append([row])
    return out


def _as_header(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return list(value)
    return None


def _as_paragraphs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [p for p in value if isinstance(p, dict)]


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)

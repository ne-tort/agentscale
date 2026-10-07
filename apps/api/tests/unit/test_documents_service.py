"""Unit tests — DocumentsService convert/read + module actions documents.*."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.documents.adapters.local_converter import LocalConverter
from prodavan.application.documents.ports.converter import ConvertedDocument
from prodavan.application.documents.service import DocumentsService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal


def _xlsx_bytes(rows: list[list[object]]) -> bytes:
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class _FakeUploadService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def upload_bytes_as_asset(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return ("asset_1", "ver_1")


class _FakeVersionRow:
    size = 123
    sha256 = "deadbeef"
    storage_key = "blobs/abc"


def _make_service(monkeypatch: pytest.MonkeyPatch, converter) -> tuple[DocumentsService, _FakeUploadService]:
    upload = _FakeUploadService()
    monkeypatch.setattr(
        "prodavan.application.documents.service.UploadService",
        lambda _session: upload,
    )
    session = MagicMock()
    session.get = AsyncMock(return_value=_FakeVersionRow())
    svc = DocumentsService(session, converter=converter)
    return svc, upload


class _PassthroughConverter(LocalConverter):
    """LocalConverter is already offline-safe; reuse it as the fake."""


@pytest.mark.asyncio
async def test_convert_xlsx_to_csv_persists_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict] = []
    metrics: list[str] = []

    async def _event(**kwargs):  # noqa: ANN003
        events.append(kwargs)

    async def _metric(**kwargs):  # noqa: ANN003
        metrics.append(kwargs.get("metric"))

    monkeypatch.setattr("prodavan.application.documents.service.emit_documents_event", _event)
    monkeypatch.setattr("prodavan.application.documents.service.emit_op_metric", _metric)

    svc, upload = _make_service(monkeypatch, _PassthroughConverter())
    data = _xlsx_bytes([["Name"], ["SSD"]])
    ref = await svc.convert(
        data,
        filename="prices.xlsx",
        target_format="csv",
        company_id="co1",
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
    )
    assert ref["asset_id"] == "asset_1"
    assert ref["version_id"] == "ver_1"
    assert ref["filename"] == "prices.csv"
    assert ref["storage_key"] == "blobs/abc"
    assert upload.calls[0]["mime"] == "text/csv"
    assert upload.calls[0]["title"] == "prices.csv"
    assert events and events[0]["event_type"] == "documents.converted"
    assert metrics and metrics[0] == "documents_conversions"


@pytest.mark.asyncio
async def test_convert_pdf_target_requires_gotenberg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.application.documents.service.emit_documents_event", AsyncMock()
    )
    monkeypatch.setattr(
        "prodavan.application.documents.service.emit_op_metric", AsyncMock()
    )
    svc, _ = _make_service(monkeypatch, _PassthroughConverter())
    data = _xlsx_bytes([["a"]])
    with pytest.raises(AppError) as err:
        await svc.convert(
            data,
            filename="doc.xlsx",
            target_format="pdf",
            company_id="co1",
            principal=Principal(sub="u1", roles=frozenset()),
            employee=None,
        )
    assert err.value.code == "CONVERSION_UNAVAILABLE"


@pytest.mark.asyncio
async def test_read_document_xlsx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.application.documents.service.emit_documents_event", AsyncMock()
    )
    monkeypatch.setattr(
        "prodavan.application.documents.service.emit_op_metric", AsyncMock()
    )
    svc, _ = _make_service(monkeypatch, _PassthroughConverter())
    result = await svc.read_document(_xlsx_bytes([["Name"], ["SSD"]]), filename="a.xlsx")
    assert result["kind"] == "xlsx"
    assert result["sheets"] == ["Sheet1"]
    assert result["rows"] == [{"Name": "SSD"}]


class _FakeGotenberg:
    async def ping(self) -> bool:
        return True

    async def convert(self, data: bytes, filename: str, target_format: str) -> ConvertedDocument:
        return ConvertedDocument(data=b"%PDF-fake", mime="application/pdf", filename="out.pdf")


@pytest.mark.asyncio
async def test_convert_to_pdf_with_gotenberg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.application.documents.service.emit_documents_event", AsyncMock()
    )
    monkeypatch.setattr(
        "prodavan.application.documents.service.emit_op_metric", AsyncMock()
    )
    svc, upload = _make_service(monkeypatch, _FakeGotenberg())
    ref = await svc.convert(
        _xlsx_bytes([["a"]]),
        filename="doc.xlsx",
        target_format="pdf",
        company_id="co1",
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
    )
    assert ref["filename"] == "doc.pdf"
    assert upload.calls[0]["mime"] == "application/pdf"


# ------------------------------------------------------------- module actions


@pytest.mark.asyncio
async def test_action_documents_convert_writes_output_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.application.modules.module_action_executor import ModuleActionExecutor

    executor = ModuleActionExecutor(session=MagicMock())  # type: ignore[arg-type]

    async def _rows(**kwargs):  # noqa: ANN003
        return [
            {
                "row_id": "row_1",
                "instance_id": "inst_1",
                "body": {"source": {"storage_key": "blobs/x", "filename": "in.xlsx"}},
            }
        ]

    updated: list[dict] = []

    async def _update(**kwargs):  # noqa: ANN003
        updated.append(kwargs)
        return {"row_id": kwargs.get("row_id")}

    monkeypatch.setattr(executor, "_list_rows_for_scope", _rows)
    monkeypatch.setattr(executor, "_update_row_for_scope", _update)
    monkeypatch.setattr(
        executor, "_resolve_documents_company_id", AsyncMock(return_value="co1")
    )

    ref = {"asset_id": "a", "version_id": "v", "filename": "in.pdf", "storage_key": "blobs/y"}

    async def _convert(_self, source, **kwargs):  # noqa: ANN003
        return ref

    monkeypatch.setattr(
        "prodavan.application.documents.service.DocumentsService.convert", _convert
    )

    out = await _invoke_with_action(
        executor,
        monkeypatch,
        {
            "id": "doc_convert",
            "kind": "documents.convert",
            "params": {
                "table_slug": "docs",
                "file_column": "source",
                "target_format": "pdf",
            },
        },
    )

    assert out["kind"] == "documents.convert"
    assert out["file_ref"] == ref
    assert updated, "row body must be persisted with the output FileRef"
    body = updated[0]["body"]
    assert body["source_pdf"] == ref


async def _invoke_with_action(executor, monkeypatch, action, row_id="row_1"):
    """Bypass DB action lookup: _load_action → fixed action dict."""
    async def _load(*args, **kwargs):  # noqa: ANN003
        return action

    monkeypatch.setattr(executor, "_load_action", _load)
    return await executor.invoke(
        cabinet_id="cab_1",
        module_id="mod_1",
        action_id=action["id"],
        row_id=row_id,
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
    )


@pytest.mark.asyncio
async def test_action_documents_convert_requires_row_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.application.modules.module_action_executor import ModuleActionExecutor

    executor = ModuleActionExecutor(session=MagicMock())  # type: ignore[arg-type]
    action = {
        "id": "doc_convert",
        "kind": "documents.convert",
        "params": {"table_slug": "docs", "file_column": "source", "target_format": "pdf"},
    }
    with pytest.raises(AppError) as err:
        await _invoke_with_action(executor, monkeypatch, action, row_id=None)
    assert err.value.status == 422
    assert "row_id" in (err.value.detail or "")

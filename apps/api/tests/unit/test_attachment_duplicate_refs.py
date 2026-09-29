"""Regression: sending a message with a re-attached file must not 500.

Live-dev incident: attaching the same filename 3 times created 3
project_attachments rows sharing one deterministic inbox storage_ref;
_load_raw() -> scalar_one_or_none() raised MultipleResultsFound mid-send
(HTTP 500 INTERNAL). The fix resolves the newest row via a limited ordered
query and upload re-attaches reuse the existing row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import prodavan.application.projects.attachment_delivery as delivery_mod
from prodavan.application.projects.attachment_delivery import AttachmentDeliveryService
from prodavan.infrastructure.persistence.models.projects import ProjectAttachmentRow


def _row(row_id: str, ref: str, created_at: datetime) -> ProjectAttachmentRow:
    return ProjectAttachmentRow(
        id=row_id,
        project_id="proj_1",
        filename="spec.xlsx",
        content_type="application/octet-stream",
        size_bytes=10,
        storage_ref=ref,
        content_asset_id=None,
        created_at=created_at,
    )


def _session_yielding(row: ProjectAttachmentRow | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = row
    result.scalars.return_value = scalars
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_load_raw_duplicate_storage_refs_resolves_row(monkeypatch) -> None:
    """Rows sharing one storage_ref resolve through the limited query — no raise."""
    newest = _row(
        "att_3",
        "object://p/inbox/spec.xlsx",
        datetime(2026, 9, 29, 23, 26, tzinfo=UTC),
    )
    service = AttachmentDeliveryService(_session_yielding(newest))

    store = MagicMock()
    store.get_bytes = AsyncMock(return_value=b"xlsx-bytes")
    monkeypatch.setattr(delivery_mod, "ensure_file_store", lambda: store)
    monkeypatch.setattr(delivery_mod, "parse_storage_ref", lambda ref: "projects/p/inbox/spec.xlsx")

    row, raw = await service._load_raw(
        project_id="proj_1",
        storage_ref="object://p/inbox/spec.xlsx",
        principal=MagicMock(),
        employee=None,
    )
    assert row.id == "att_3"
    assert raw == b"xlsx-bytes"

    # The executed query must be capped (limit 1) — the crash guard.
    from sqlalchemy import Select

    sent = service._session.execute.await_args.args[0]
    assert isinstance(sent, Select)
    compiled = str(sent).lower()
    assert " limit " in compiled


@pytest.mark.asyncio
async def test_load_raw_missing_ref_raises_lookup_error() -> None:
    service = AttachmentDeliveryService(_session_yielding(None))
    with pytest.raises(LookupError):
        await service._load_raw(
            project_id="proj_1",
            storage_ref="object://p/inbox/missing.bin",
            principal=MagicMock(),
            employee=None,
        )

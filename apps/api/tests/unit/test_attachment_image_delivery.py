"""Images in chat: vision content blocks + workspace copy + friendly errors."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import prodavan.application.projects.attachment_delivery as delivery_mod
from prodavan.application.agent.session_service import _rewrite_vision_error_event
from prodavan.application.projects.attachment_delivery import (
    CHAT_IMAGE_INLINE_MAX_COUNT,
    AttachmentDeliveryService,
    compose_agent_message,
    is_image_filename,
)
from prodavan.domain.agent.types import AgentEvent, AgentEventType
from prodavan.infrastructure.persistence.models.projects import ProjectAttachmentRow

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc8\x89\x00\x00\x00\nIEND\xaeB`\x82"
)


def _row(row_id: str, filename: str, *, size: int | None = None) -> ProjectAttachmentRow:
    return ProjectAttachmentRow(
        id=row_id,
        project_id="proj_1",
        filename=filename,
        content_type="image/png",
        size_bytes=size if size is not None else len(PNG_1X1),
        storage_ref=f"object://p/inbox/{filename}",
        content_asset_id=None,
        created_at=datetime(2026, 10, 7, tzinfo=UTC),
    )


def _session_yielding(rows: list[ProjectAttachmentRow]) -> MagicMock:
    """Session mock: each _load_raw query returns rows in order (limit 1 → first)."""
    results: list[MagicMock] = []
    for row in rows:
        result = MagicMock()
        scalars = MagicMock()
        scalars.first.return_value = row
        result.scalars.return_value = scalars
        results.append(result)
    session = MagicMock()
    session.execute = AsyncMock(side_effect=results)
    return session


def _patch_store(monkeypatch, raw: bytes = PNG_1X1) -> MagicMock:
    store = MagicMock()
    store.put_bytes = AsyncMock(return_value=None)
    store.get_bytes = AsyncMock(return_value=raw)
    monkeypatch.setattr(delivery_mod, "ensure_file_store", lambda: store)
    monkeypatch.setattr(delivery_mod, "parse_storage_ref", lambda ref: "projects/p/inbox/x.png")
    return store


async def _deliver(service, *, refs: list[str], text: str = "что здесь?"):
    return await service.deliver_for_send(
        project_id="proj_1",
        workspace_key="wk",
        storage_refs=refs,
        user_text=text,
        runtime_ref=None,
        principal=MagicMock(),
        employee=None,
    )


def test_is_image_filename() -> None:
    assert is_image_filename("скрин.png")
    assert is_image_filename("photo.JPG")
    assert is_image_filename("pic.webp")
    assert not is_image_filename("spec.xlsx")
    assert not is_image_filename("note.txt")


@pytest.mark.asyncio
async def test_deliver_image_inline(monkeypatch) -> None:
    service = AttachmentDeliveryService(_session_yielding([_row("a1", "скрин.png")]))
    store = _patch_store(monkeypatch)

    res = await _deliver(service, refs=["object://p/inbox/скрин.png"])
    item = res.items[0]
    assert item.kind == "image"
    assert item.image_inline is True
    assert item.mime == "image/png"
    assert item.workspace_path == "inbox/скрин.png"
    # копия в workspace записана
    store.put_bytes.assert_awaited_once()
    # картинка ушла в send body (base64)
    assert len(res.images) == 1
    assert res.images[0]["mime"] == "image/png"
    assert base64.b64decode(res.images[0]["data_base64"]).startswith(b"\x89PNG")
    # сообщение агента объявляет изображение
    assert "Изображение: скрин.png" in res.agent_message
    assert "что здесь?" in res.agent_message
    # ui_dict не содержит base64
    ui = item.ui_dict()
    assert ui["kind"] == "image"
    assert ui["image_inline"] is True
    assert "data_base64" not in ui


@pytest.mark.asyncio
async def test_deliver_image_over_inline_size(monkeypatch) -> None:
    big = PNG_1X1 + b"\x00" * (delivery_mod.CHAT_IMAGE_INLINE_MAX_BYTES + 1)
    row = _row("a1", "big.png", size=len(big))
    service = AttachmentDeliveryService(_session_yielding([row]))
    _patch_store(monkeypatch, raw=big)

    res = await _deliver(service, refs=[row.storage_ref], text="")
    item = res.items[0]
    assert item.kind == "image"
    assert item.image_inline is False
    assert res.images == ()
    assert item.workspace_path == "inbox/big.png"
    assert "превышен лимит" in item.note


@pytest.mark.asyncio
async def test_deliver_image_count_cap(monkeypatch) -> None:
    count = CHAT_IMAGE_INLINE_MAX_COUNT + 2
    rows = [_row(f"a{i}", f"pic{i}.png") for i in range(count)]
    refs = [r.storage_ref for r in rows]
    service = AttachmentDeliveryService(_session_yielding(rows))
    _patch_store(monkeypatch)

    res = await _deliver(service, refs=refs, text="")
    assert len(res.items) == count
    inline = [i for i in res.items if i.image_inline]
    assert len(inline) == CHAT_IMAGE_INLINE_MAX_COUNT
    assert len(res.images) == CHAT_IMAGE_INLINE_MAX_COUNT


def test_compose_agent_message_image_section() -> None:
    item = delivery_mod.DeliveredAttachment(
        filename="скрин.png",
        storage_ref="object://p/inbox/скрин.png",
        kind="image",
        workspace_path="inbox/скрин.png",
        row_count=None,
        records=None,
        text=None,
        note="изображение передано модели; копия в контейнере",
        mime="image/png",
        image_inline=True,
    )
    msg = compose_agent_message(user_text="что здесь?", items=[item])
    assert "Изображение: скрин.png" in msg
    assert "/workspace/inbox/скрин.png" in msg
    assert "что здесь?" in msg


def test_rewrite_vision_error_event_matches() -> None:
    ev = AgentEvent.now(
        AgentEventType.ERROR,
        {"code": "PROVIDER_ERROR", "message": "provider error 400: image content is not supported"},
    )
    out = _rewrite_vision_error_event(ev)
    assert out is not ev
    assert out.data["code"] == "MODEL_NOT_VISION"
    assert out.data["provider_code"] == "PROVIDER_ERROR"
    assert out.data["images_not_supported"] is True
    assert "Модель не поддерживает изображения" in out.data["message"]
    assert "vision-модель" in out.data["message"]


def test_rewrite_vision_error_event_passes_through_unrelated() -> None:
    ev = AgentEvent.now(
        AgentEventType.ERROR,
        {"code": "POD_NOT_RUNNING", "message": "pod is not running or not ready"},
    )
    out = _rewrite_vision_error_event(ev)
    assert out is ev

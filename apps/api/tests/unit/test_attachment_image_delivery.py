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
from prodavan.domain.projects import CHAT_INLINE_TABULAR_ROW_LIMIT
from prodavan.infrastructure.persistence.models.projects import ProjectAttachmentRow

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc8\x89\x00\x00\x00\nIEND\xaeB`\x82"
)


def _row(
    row_id: str,
    filename: str,
    *,
    size: int | None = None,
    content_type: str = "image/png",
) -> ProjectAttachmentRow:
    return ProjectAttachmentRow(
        id=row_id,
        project_id="proj_1",
        filename=filename,
        content_type=content_type,
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
async def test_inline_text_attachment_is_also_pushed_to_live_workspace(monkeypatch) -> None:
    raw = b"customer request"
    row = _row("a1", "request.txt", size=len(raw), content_type="text/plain")
    service = AttachmentDeliveryService(_session_yielding([row]))
    store = _patch_store(monkeypatch, raw=raw)
    service._write_pod_file = AsyncMock()

    res = await _deliver(service, refs=[row.storage_ref], text="обработай")
    item = res.items[0]

    assert item.kind == "inline_text"
    assert item.workspace_path == "inbox/request.txt"
    assert "/workspace/inbox/request.txt" in res.agent_message
    service._write_pod_file.assert_awaited_once_with(
        runtime_ref=None,
        relative_path="inbox/request.txt",
        data=raw,
    )
    store.put_bytes.assert_awaited_once()


@pytest.mark.asyncio
async def test_inline_tabular_attachment_is_hot_pushed_to_pod(monkeypatch) -> None:
    """Inline table previews still leave the source available to Pod tools."""
    raw = b"name,qty\nwidget,2\n"
    row = _row("a1", "rows.csv", size=len(raw), content_type="text/csv")
    service = AttachmentDeliveryService(_session_yielding([row]))
    _patch_store(monkeypatch, raw=raw)
    service._write_pod_file = AsyncMock()

    result = await service.deliver_for_send(
        project_id="proj_1",
        workspace_key="wk",
        storage_refs=[row.storage_ref],
        user_text="обработай таблицу",
        runtime_ref="pod-1",
        principal=MagicMock(),
        employee=None,
    )

    item = result.items[0]
    assert item.kind == "inline_table"
    assert item.workspace_path == "inbox/rows.csv"
    service._write_pod_file.assert_awaited_once_with(
        runtime_ref="pod-1",
        relative_path="inbox/rows.csv",
        data=raw,
    )
    assert "/workspace/inbox/rows.csv" in result.agent_message


@pytest.mark.asyncio
async def test_large_tabular_delivery_fails_if_json_hot_push_fails(monkeypatch) -> None:
    """Large tables advertise JSON workspace paths only after hot-push succeeds."""
    rows = "\n".join(f"item{i}, {i}" for i in range(CHAT_INLINE_TABULAR_ROW_LIMIT + 1))
    raw = ("name,qty\n" + rows + "\n").encode()
    row = _row("a1", "large.csv", size=len(raw), content_type="text/csv")
    service = AttachmentDeliveryService(_session_yielding([row]))
    store = _patch_store(monkeypatch, raw=raw)
    service._write_pod_file = AsyncMock(side_effect=RuntimeError("pod write failed"))

    with pytest.raises(RuntimeError, match="pod write failed"):
        await service.deliver_for_send(
            project_id="proj_1",
            workspace_key="wk",
            storage_refs=[row.storage_ref],
            user_text="обработай большую таблицу",
            runtime_ref="pod-1",
            principal=MagicMock(),
            employee=None,
        )

    # Converted JSON remains durable and retryable in the object store.
    store.put_bytes.assert_awaited_once()


@pytest.mark.asyncio
async def test_corrupt_xlsx_falls_back_to_workspace_file(monkeypatch) -> None:
    raw = b"not-a-valid-xlsx"
    row = _row("a1", "broken.xlsx", size=len(raw), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    service = AttachmentDeliveryService(_session_yielding([row]))
    store = _patch_store(monkeypatch, raw=raw)
    service._write_pod_file = AsyncMock()

    result = await service.deliver_for_send(
        project_id="proj_1",
        workspace_key="wk",
        storage_refs=[row.storage_ref],
        user_text="обработай файл",
        runtime_ref="pod-1",
        principal=MagicMock(),
        employee=None,
    )

    item = result.items[0]
    assert item.kind == "workspace_file"
    assert item.workspace_path == "inbox/broken.xlsx"
    assert "не удалось распарсить таблицу" in item.note
    assert "/workspace/inbox/broken.xlsx" in result.agent_message
    store.put_bytes.assert_awaited_once()
    service._write_pod_file.assert_awaited_once_with(
        runtime_ref="pod-1",
        relative_path="inbox/broken.xlsx",
        data=raw,
    )


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
async def test_write_pod_file_uses_workspace_port_for_sandbox(monkeypatch) -> None:
    workspace = MagicMock()
    workspace.write_bytes = AsyncMock()
    monkeypatch.setattr(delivery_mod.settings, "pod_runtime_mode", "sandbox")
    monkeypatch.setattr(
        "prodavan.application.pod_service.factory.build_pod_workspace",
        lambda: workspace,
    )

    await AttachmentDeliveryService(MagicMock())._write_pod_file(
        runtime_ref="sandbox-claim-wk",
        relative_path="inbox/spec.pdf",
        data=b"file bytes",
    )

    workspace.write_bytes.assert_awaited_once_with(
        runtime_ref="sandbox-claim-wk",
        path="inbox/spec.pdf",
        data=b"file bytes",
    )


@pytest.mark.asyncio
async def test_deliver_image_fails_when_live_workspace_copy_fails(monkeypatch) -> None:
    row = _row("a1", "скрин.png")
    service = AttachmentDeliveryService(_session_yielding([row]))
    store = _patch_store(monkeypatch)
    service._write_pod_file = AsyncMock(side_effect=RuntimeError("pod write failed"))

    with pytest.raises(RuntimeError, match="pod write failed"):
        await _deliver(service, refs=[row.storage_ref])

    # Source remains durably stored so the user can retry after the Pod recovers.
    store.put_bytes.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_attachment_names_get_distinct_workspace_paths(monkeypatch) -> None:
    rows = [_row("a1", "скрин.png"), _row("a2", "скрин.png")]
    service = AttachmentDeliveryService(_session_yielding(rows))
    store = _patch_store(monkeypatch)

    res = await _deliver(
        service,
        refs=["object://p/inbox/first.png", "object://p/inbox/second.png"],
        text="сравни изображения",
    )

    assert [item.filename for item in res.items] == ["скрин.png", "скрин.png"]
    assert [item.workspace_path for item in res.items] == [
        "inbox/скрин.png",
        "inbox/скрин (2).png",
    ]
    stored_keys = [call.args[0] for call in store.put_bytes.await_args_list]
    assert stored_keys == [
        "projects/wk/workspace/inbox/скрин.png",
        "projects/wk/workspace/inbox/скрин (2).png",
    ]
    assert "/workspace/inbox/скрин (2).png" in res.agent_message


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_large_tabular_derived_json_keeps_unique_workspace_name(monkeypatch) -> None:
    rows = [
        _row("a1", "report.csv", content_type="text/csv"),
        _row("a2", "report.json", content_type="application/json"),
    ]
    raw = ("name,qty\n" + "\n".join(
        f"item{i},{i}" for i in range(CHAT_INLINE_TABULAR_ROW_LIMIT + 1)
    ) + "\n").encode()
    service = AttachmentDeliveryService(_session_yielding(rows))
    store = _patch_store(monkeypatch, raw=raw)

    result = await _deliver(
        service,
        refs=[row.storage_ref for row in rows],
        text="обработай отчёты",
    )

    assert [item.workspace_path for item in result.items] == [
        "inbox/report.json",
        "inbox/report (2).json",
    ]
    assert [call.args[0] for call in store.put_bytes.await_args_list] == [
        "projects/wk/workspace/inbox/report.json",
        "projects/wk/workspace/inbox/report (2).json",
    ]
    assert "/workspace/inbox/report.json" in result.agent_message
    assert "/workspace/inbox/report (2).json" in result.agent_message


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

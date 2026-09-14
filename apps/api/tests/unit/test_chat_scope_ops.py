"""Chat-scoped list/write prep on project runtime path."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.modules.chat_scope import CHAT_SCOPE_ALL
from prodavan.application.modules.chat_scope_ops import (
    prepare_chat_scoped_list,
    prepare_chat_scoped_write,
)
from prodavan.domain.errors import AppError


@pytest.mark.asyncio
async def test_prepare_list_shared_no_filter() -> None:
    instances = MagicMock()
    instances.resolve_tables_body = AsyncMock(
        return_value=[{"slug": "catalogs", "scope": {"chats": "all"}}]
    )
    sid, skip = await prepare_chat_scoped_list(
        instances,
        instance_id="minst_1",
        module_id="mod_equipment",
        table_slug="catalogs",
        session_id=None,
    )
    assert sid is None
    assert skip is False


@pytest.mark.asyncio
async def test_prepare_list_current_without_session_skips() -> None:
    instances = MagicMock()
    instances.resolve_tables_body = AsyncMock(
        return_value=[{"slug": "request_lines", "scope": {"chats": "current"}}]
    )
    sid, skip = await prepare_chat_scoped_list(
        instances,
        instance_id="minst_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        session_id=None,
    )
    assert sid is None
    assert skip is True


@pytest.mark.asyncio
async def test_prepare_write_current_requires_session() -> None:
    instances = MagicMock()
    instances.resolve_tables_body = AsyncMock(
        return_value=[{"slug": "request_lines", "scope": {"chats": "current"}}]
    )
    db = AsyncMock()
    with pytest.raises(AppError) as ei:
        await prepare_chat_scoped_write(
            instances,
            instance_id="minst_1",
            module_id="mod_equipment",
            table_slug="request_lines",
            body={"title": "x"},
            session_id=None,
            db=db,
            project_id="prj_1",
        )
    assert ei.value.code == "SESSION_REQUIRED"


@pytest.mark.asyncio
async def test_prepare_write_current_stamps_and_checks_project() -> None:
    instances = MagicMock()
    instances.resolve_tables_body = AsyncMock(
        return_value=[{"slug": "request_lines", "scope": {"chats": "current"}}]
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = "ags_1"
    db.execute = AsyncMock(return_value=result)

    body, sid = await prepare_chat_scoped_write(
        instances,
        instance_id="minst_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        body={"title": "x"},
        session_id="ags_1",
        db=db,
        project_id="prj_1",
    )
    assert sid == "ags_1"
    assert body["session_id"] == "ags_1"
    assert body["title"] == "x"
    db.execute.assert_awaited()


@pytest.mark.asyncio
async def test_prepare_write_shared_strips_session() -> None:
    instances = MagicMock()
    instances.resolve_tables_body = AsyncMock(
        return_value=[{"slug": "catalogs", "scope": {"chats": CHAT_SCOPE_ALL}}]
    )
    db = AsyncMock()
    body, sid = await prepare_chat_scoped_write(
        instances,
        instance_id="minst_1",
        module_id="mod_equipment",
        table_slug="catalogs",
        body={"name": "c", "session_id": "ags_x"},
        session_id="ags_x",
        db=db,
        project_id="prj_1",
    )
    assert sid is None
    assert "session_id" not in body
    db.execute.assert_not_called()

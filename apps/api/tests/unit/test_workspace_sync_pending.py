"""WAVE16: отложенный workspace sync при активном ходе агента."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.projects.workspace_sync_policy import defer_or_schedule_project_sync
from prodavan.application.projects.workspace_outdated import (
    list_workspace_sync_pending,
    mark_workspace_sync_pending,
    take_workspace_sync_pending,
)


class _FakeSession:
    """Минимальный in-memory стенд для ProjectRow-меток (без БД)."""

    def __init__(self, row: Any) -> None:
        self.row = row

    async def get(self, model: Any, pk: str) -> Any:
        return self.row if getattr(self.row, "id", None) == pk else None


def _row(project_id: str = "proj_1") -> Any:
    # ORM-инструментированные атрибуты требуют настоящего конструктора
    from prodavan.infrastructure.persistence.models.projects import ProjectRow

    return ProjectRow(
        id=project_id,
        company_id="co_1",
        cabinet_id="cab_1",
        name="t",
        slug="t",
        status="active",
        workspace_key="wk_1",
        container_ref="c_1",
    )


@pytest.mark.asyncio
async def test_mark_and_take_pending() -> None:
    row = _row()
    session = _FakeSession(row)
    assert await mark_workspace_sync_pending(session, project_id="proj_1", source="cabinet") is True
    assert row.workspace_sync_pending_at is not None
    assert row.workspace_sync_pending_source == "cabinet"
    source = await take_workspace_sync_pending(session, project_id="proj_1")
    assert source == "cabinet"
    assert row.workspace_sync_pending_at is None
    # второй take — None (метка снята)
    assert await take_workspace_sync_pending(session, project_id="proj_1") is None


@pytest.mark.asyncio
async def test_defers_when_turn_in_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Активный ход → sync откладывается (pending), rematerialize НЕ зовётся."""
    monkeypatch.setattr(
        "prodavan.config.settings.settings.projects_auto_rematerialize_on_cabinet_change",
        True,
    )
    with (
        patch(
            "prodavan.application.projects.workspace_sync_policy._project_turn_in_progress",
            AsyncMock(return_value=True),
        ),
        patch(
            "prodavan.application.projects.workspace_sync_policy.request_rematerialize_project",
            AsyncMock(),
        ) as req,
        patch(
            "prodavan.application.projects.workspace_outdated.mark_workspace_sync_pending",
            AsyncMock(return_value=True),
        ),
    ):
        note = await defer_or_schedule_project_sync(
            AsyncMock(), project_id="proj_1", source="module_bind"
        )
    req.assert_not_awaited()
    assert note.mode == "deferred"
    assert note.reason == "turn_in_progress"


@pytest.mark.asyncio
async def test_schedules_when_no_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет активного хода → обычный rematerialize."""
    monkeypatch.setattr(
        "prodavan.config.settings.settings.projects_auto_rematerialize_on_cabinet_change",
        True,
    )
    with (
        patch(
            "prodavan.application.projects.workspace_sync_policy._project_turn_in_progress",
            AsyncMock(return_value=False),
        ),
        patch(
            "prodavan.application.projects.workspace_sync_policy.request_rematerialize_project",
            AsyncMock(return_value={"enqueued": True}),
        ) as req,
    ):
        note = await defer_or_schedule_project_sync(
            AsyncMock(), project_id="proj_1", source="module_bind"
        )
    req.assert_awaited_once()
    assert (note.mode, note.scheduled) == ("scheduled", 1)


@pytest.mark.asyncio
async def test_turn_check_failopen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой проверки хода (мок без сессий) → fail-open, sync идёт."""
    monkeypatch.setattr(
        "prodavan.config.settings.settings.projects_auto_rematerialize_on_cabinet_change",
        True,
    )
    with patch(
        "prodavan.application.projects.workspace_sync_policy.request_rematerialize_project",
        AsyncMock(return_value={"enqueued": True}),
    ) as req:
        note = await defer_or_schedule_project_sync(
            MagicMock(), project_id="proj_1", source="project_provider", force=True
        )
    req.assert_awaited_once()
    assert note.mode == "scheduled"

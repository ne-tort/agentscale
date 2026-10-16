"""Unit tests — смена провайдера/AI-ключа проекта должна доходить до пода и сессий.

Регрессия: `PATCH /projects/{id}` с новым провайдером был голой записью в БД.
`.prodavan/config.yaml` в поде оставался с прежним `base_url`/`key_ref` (bridge
кэширует их в `providerResolveOpts` при старте процесса и перечитывает только на
bind, а bind подавлен троттлингом), а ACTIVE-сессии продолжали слать lease
прежнего ключа — их снапшот пишется лишь при создании/форке.

Чат при этом показывал модели НОВОГО провайдера, потому что `/models/live`
резолвит ключ и endpoint заново на каждый запрос и передаёт их query-параметрами
в обход обоих кэшей. Отсюда симптом: список свежий, а вызов падает в
`503 PROVIDER_MODEL_ERROR · provider error 401: Invalid token` — валидный токен
нового провайдера уходит на чужой endpoint.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.agent.session_service import AgentSessionService
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.application.project_service.command import ProjectCommand
from prodavan.application.projects.workspace_sync_policy import (
    WorkspaceSyncNotification,
    defer_or_schedule_project_sync,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal

_POD_RUNTIME = "prodavan.application.pod_service.command.build_pod_runtime"
_KEYS_SVC = "prodavan.application.ai_keys.service.AiKeysService"
_SESSION_SVC = "prodavan.application.agent.session_service.AgentSessionService"
_SYNC = "prodavan.application.projects.workspace_sync_policy.defer_or_schedule_project_sync"


def _principal() -> Principal:
    return Principal(sub="emp-1", roles=frozenset({"employee"}))


def _cmd(session, row) -> ProjectCommand:
    cmd = ProjectCommand(session)
    cmd._access.require_access = AsyncMock(return_value=row)
    cmd._project_public = AsyncMock(return_value={"id": row.id, "name": "Проект"})
    return cmd


def _project_row(*, provider: str, key_id: str | None) -> MagicMock:
    row = MagicMock()
    row.id = "proj_1"
    row.cabinet_id = "cab_1"
    row.status = "active"
    row.agent_provider = provider
    row.resolved_ai_key_id = key_id
    return row


@pytest.mark.asyncio
async def test_patch_key_change_rebinds_sessions_and_forces_rematerialize() -> None:
    session = AsyncMock()
    row = _project_row(provider="codex", key_id="aik_old")

    keys = MagicMock()
    keys.agent_provider_for_project_key = AsyncMock(return_value="xai")
    rebind = AsyncMock(return_value=7)
    agent_sessions = MagicMock()
    agent_sessions.rebind_active_to_project_key = rebind
    notification = WorkspaceSyncNotification(
        mode="scheduled",
        scheduled=1,
        project_id="proj_1",
        source="project_provider",
        enqueued=("proj_1",),
    )

    with (
        patch(_POD_RUNTIME, return_value=AsyncMock()),
        patch(_KEYS_SVC, return_value=keys),
        patch(_SESSION_SVC, return_value=agent_sessions),
        patch(_SYNC, AsyncMock(return_value=notification)) as sync,
    ):
        out = await _cmd(session, row).patch(
            project_id="proj_1",
            principal=_principal(),
            employee=None,
            resolved_ai_key_id="aik_new",
            update_resolved_ai_key_id=True,
        )

    assert row.resolved_ai_key_id == "aik_new"
    assert row.agent_provider == "xai"
    # сессии переведены на новый ключ ДО sync: rematerialize джобой вызывает
    # bootstrap_project_sessions, который берёт key_id уже из строк сессий
    rebind.assert_awaited_once()
    assert rebind.await_args.kwargs["project"] is row
    sync.assert_awaited_once()
    assert sync.await_args.kwargs["source"] == "project_provider"
    # force обязателен: флаг auto-rematerialize описывает изменения кабинета
    # и по умолчанию выключен, а тут под сломан прямо сейчас
    assert sync.await_args.kwargs["force"] is True
    assert out["workspace_sync"]["scheduled"] == 1


@pytest.mark.asyncio
async def test_patch_provider_only_also_rebinds() -> None:
    session = AsyncMock()
    row = _project_row(provider="codex", key_id="aik_old")
    rebind = AsyncMock(return_value=0)
    agent_sessions = MagicMock()
    agent_sessions.rebind_active_to_project_key = rebind
    notification = WorkspaceSyncNotification(
        mode="scheduled", scheduled=1, project_id="proj_1", source="project_provider"
    )

    with (
        patch(_POD_RUNTIME, return_value=AsyncMock()),
        patch(_SESSION_SVC, return_value=agent_sessions),
        patch(_SYNC, AsyncMock(return_value=notification)),
    ):
        await _cmd(session, row).patch(
            project_id="proj_1",
            principal=_principal(),
            employee=None,
            agent_provider="xai",
            update_agent_provider=True,
        )

    assert row.agent_provider == "xai"
    rebind.assert_awaited_once()


@pytest.mark.asyncio
async def test_patch_name_only_leaves_provider_machinery_alone() -> None:
    session = AsyncMock()
    row = _project_row(provider="xai", key_id="aik_new")
    rebind = AsyncMock(return_value=0)
    agent_sessions = MagicMock()
    agent_sessions.rebind_active_to_project_key = rebind

    with (
        patch(_POD_RUNTIME, return_value=AsyncMock()),
        patch(_SESSION_SVC, return_value=agent_sessions),
        patch(_SYNC, AsyncMock()) as sync,
    ):
        out = await _cmd(session, row).patch(
            project_id="proj_1",
            principal=_principal(),
            employee=None,
            name="Новое имя",
        )

    assert row.name == "Новое имя"
    assert out == {"id": "proj_1", "name": "Проект"}
    rebind.assert_not_awaited()
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_same_key_is_noop() -> None:
    """Повторная отправка того же ключа (UI перечитал форму) не должна дёргать под."""
    session = AsyncMock()
    row = _project_row(provider="xai", key_id="aik_same")
    keys = MagicMock()
    keys.agent_provider_for_project_key = AsyncMock(return_value="xai")
    rebind = AsyncMock(return_value=0)
    agent_sessions = MagicMock()
    agent_sessions.rebind_active_to_project_key = rebind

    with (
        patch(_POD_RUNTIME, return_value=AsyncMock()),
        patch(_KEYS_SVC, return_value=keys),
        patch(_SESSION_SVC, return_value=agent_sessions),
        patch(_SYNC, AsyncMock()) as sync,
    ):
        await _cmd(session, row).patch(
            project_id="proj_1",
            principal=_principal(),
            employee=None,
            resolved_ai_key_id="aik_same",
            update_resolved_ai_key_id=True,
        )

    rebind.assert_not_awaited()
    sync.assert_not_awaited()


# --- перепривязка сессий -------------------------------------------------


def _svc_with_rows(rows, chosen=None, *, select_error=None) -> AgentSessionService:
    svc = AgentSessionService(AsyncMock())
    if select_error is not None:
        svc._keys.select_runtime_key_for_project = AsyncMock(side_effect=select_error)
    else:
        svc._keys.select_runtime_key_for_project = AsyncMock(return_value=chosen)
    res = MagicMock()
    res.scalars.return_value.all.return_value = rows
    svc._session.execute = AsyncMock(return_value=res)
    return svc


@pytest.mark.asyncio
async def test_rebind_updates_stale_sessions_and_clears_model() -> None:
    stale = SimpleNamespace(
        resolved_key_id="aik_old", provider="codex", api_kind="custom", model="gemini-3.7-flash"
    )
    current = SimpleNamespace(
        resolved_key_id="aik_new", provider="xai", api_kind="xai_oauth", model="grok-4.7"
    )
    svc = _svc_with_rows(
        [stale, current],
        chosen=SimpleNamespace(id="aik_new", provider="xai", api_kind="xai_oauth"),
    )

    changed = await svc.rebind_active_to_project_key(project=SimpleNamespace(id="proj_1"))

    assert changed == 1
    assert (stale.resolved_key_id, stale.provider, stale.api_kind) == (
        "aik_new",
        "xai",
        "xai_oauth",
    )
    # модель принадлежала старому провайдеру — следующий send резолвит дефолт нового
    assert stale.model is None
    # уже на текущем ключе: не трогаем, чтобы не сбросить выбор пользователя
    assert current.model == "grok-4.7"


@pytest.mark.asyncio
async def test_rebind_survives_missing_runtime_key() -> None:
    """Нет runtime-ключа → patch проекта не падает, сессии остаются как есть."""
    svc = _svc_with_rows(
        [],
        select_error=AppError(code="NO_AI_KEY", title="No AI key", status=404, detail="none"),
    )

    changed = await svc.rebind_active_to_project_key(project=SimpleNamespace(id="proj_1"))

    assert changed == 0
    svc._session.execute.assert_not_awaited()


# --- выбор ключа без резолва секрета -------------------------------------


def _keys_svc() -> AiKeysService:
    return AiKeysService(AsyncMock(), secrets=MagicMock())


@pytest.mark.asyncio
async def test_select_runtime_key_does_not_resolve_secret() -> None:
    """Рефакторинг не должен тянуть секрет (для xai_oauth это сеть + refresh)."""
    svc = _keys_svc()
    chosen = SimpleNamespace(id="aik_new", provider="xai", api_kind="xai_oauth")
    svc.list_available_keys_for_project = AsyncMock(return_value=[{"id": "aik_new"}])
    svc._get_row = AsyncMock(return_value=chosen)
    svc._pick_runtime_rows = MagicMock(return_value=([chosen], []))
    svc._finalize_lazy_disabled = AsyncMock()
    svc.effective_secret_for_row = AsyncMock(return_value="MUST-NOT-BE-CALLED")

    picked = await svc.select_runtime_key_for_project(
        project=SimpleNamespace(resolved_ai_key_id="aik_new", agent_provider="xai")
    )

    assert picked is chosen
    svc.effective_secret_for_row.assert_not_awaited()


@pytest.mark.asyncio
async def test_select_runtime_key_honours_project_key_over_first_runtime() -> None:
    svc = _keys_svc()
    first = SimpleNamespace(id="aik_a", provider="codex", api_kind="custom")
    pinned = SimpleNamespace(id="aik_b", provider="xai", api_kind="xai_oauth")
    svc.list_available_keys_for_project = AsyncMock(
        return_value=[{"id": "aik_a"}, {"id": "aik_b"}]
    )
    svc._get_row = AsyncMock(side_effect=lambda kid: {"aik_a": first, "aik_b": pinned}[kid])
    svc._pick_runtime_rows = MagicMock(return_value=([first, pinned], []))
    svc._finalize_lazy_disabled = AsyncMock()

    picked = await svc.select_runtime_key_for_project(
        project=SimpleNamespace(resolved_ai_key_id="aik_b", agent_provider="xai")
    )

    assert picked is pinned


@pytest.mark.asyncio
async def test_select_runtime_key_raises_when_nothing_runtime_capable() -> None:
    svc = _keys_svc()
    cli = SimpleNamespace(id="aik_cli", provider="codex", api_kind="cli_subscription")
    svc.list_available_keys_for_project = AsyncMock(return_value=[{"id": "aik_cli"}])
    svc._get_row = AsyncMock(return_value=cli)
    svc._pick_runtime_rows = MagicMock(return_value=([], []))
    svc._finalize_lazy_disabled = AsyncMock()

    with pytest.raises(AppError) as exc:
        await svc.select_runtime_key_for_project(
            project=SimpleNamespace(resolved_ai_key_id=None, agent_provider=None)
        )

    assert exc.value.code == "NO_AI_KEY"


@pytest.mark.asyncio
async def test_resolve_credentials_still_returns_secret() -> None:
    svc = _keys_svc()
    chosen = SimpleNamespace(id="aik_new", provider="xai", api_kind="xai_oauth")
    svc.select_runtime_key_for_project = AsyncMock(return_value=chosen)
    svc.effective_secret_for_row = AsyncMock(return_value="tok-123")

    cred = await svc.resolve_credentials_for_project(project=SimpleNamespace(id="proj_1"))

    assert (cred.key_id, cred.provider, cred.api_kind, cred.secret) == (
        "aik_new",
        "xai",
        "xai_oauth",
        "tok-123",
    )


# --- force в политике sync ------------------------------------------------


@pytest.mark.asyncio
async def test_force_sync_bypasses_cabinet_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.config.settings.settings.projects_auto_rematerialize_on_cabinet_change",
        False,
    )
    with patch(
        "prodavan.application.projects.workspace_sync_policy.request_rematerialize_project",
        AsyncMock(return_value={"enqueued": True}),
    ) as req:
        note = await defer_or_schedule_project_sync(
            AsyncMock(), project_id="proj_1", source="project_provider", force=True
        )

    req.assert_awaited_once()
    assert (note.mode, note.scheduled) == ("scheduled", 1)


@pytest.mark.asyncio
async def test_default_sync_still_defers_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без force поведение прежнее — иначе каждое изменение кабинета пересоздаёт под."""
    monkeypatch.setattr(
        "prodavan.config.settings.settings.projects_auto_rematerialize_on_cabinet_change",
        False,
    )
    with (
        patch(
            "prodavan.application.projects.workspace_sync_policy.request_rematerialize_project",
            AsyncMock(),
        ) as req,
        patch(
            "prodavan.application.projects.workspace_sync_policy.mark_workspace_outdated_for_project",
            AsyncMock(return_value={"marked_outdated": 1}),
        ) as mark,
    ):
        note = await defer_or_schedule_project_sync(
            AsyncMock(), project_id="proj_1", source="project_modules"
        )

    req.assert_not_awaited()
    mark.assert_awaited_once()
    assert note.mode == "deferred"

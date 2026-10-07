"""Unit tests for conversation rehydrate after pod hydrate_generation bump."""

from __future__ import annotations

import pytest

from prodavan.application.agent.conversation_rehydrate import (
    HYDRATE_GEN_KEY,
    format_rehydrate_bridge_message,
    needs_conversation_rehydrate,
    stamp_hydrate_generation,
)


def test_needs_rehydrate_when_generation_changes() -> None:
    assert needs_conversation_rehydrate({HYDRATE_GEN_KEY: 1}, current_generation=2)
    assert not needs_conversation_rehydrate({HYDRATE_GEN_KEY: 2}, current_generation=2)
    assert needs_conversation_rehydrate(None, current_generation=1)
    assert not needs_conversation_rehydrate({HYDRATE_GEN_KEY: 0}, current_generation=None)


def test_stamp_hydrate_generation_preserves_other_keys() -> None:
    out = stamp_hydrate_generation({"agentId": "a1"}, 3)
    assert out["agentId"] == "a1"
    assert out[HYDRATE_GEN_KEY] == 3


def test_format_rehydrate_includes_prior_turns() -> None:
    msg = format_rehydrate_bridge_message(
        history=[
            {"role": "user", "text": "Найди LC1D09"},
            {"role": "assistant", "text": "Ищу в каталоге"},
            {"role": "tool", "text": "equipment_catalog_search"},
        ],
        new_message="Добавь в найденные",
    )
    assert msg is not None
    assert "Найди LC1D09" in msg
    assert "Ищу в каталоге" in msg
    assert "equipment_catalog_search" not in msg
    assert "Добавь в найденные" in msg
    assert "pod runtime restarted" in msg.lower() or "Workspace was updated" in msg


def test_format_rehydrate_empty_history() -> None:
    assert format_rehydrate_bridge_message(history=[], new_message="hi") is None


@pytest.mark.asyncio
async def test_project_hydrate_generation_with_multiple_pod_rows() -> None:
    """Регрессия (500 Multiple rows): у проекта легитимно несколько pod-строк
    (terminated/failed история + живая). Поколение гидрации берём у последней
    созданной строки, а не падаем на scalar_one_or_none."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.ext.compiler import compiles

    # модели используют JSONB (postgres); для in-memory sqlite рендерим как JSON
    @compiles(JSONB, "sqlite")
    def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
        return "JSON"

    from prodavan.application.agent.session_service import AgentSessionService
    from prodavan.infrastructure.persistence.models.projects import (
        ProjectPodRow,
        ProjectRow,
    )

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync: ProjectRow.__table__.create(sync, checkfirst=True)
        )
        await conn.run_sync(
            lambda sync: ProjectPodRow.__table__.create(sync, checkfirst=True)
        )
    now = datetime.now(UTC)
    async with AsyncSession(engine) as session:
        session.add(
            ProjectRow(
                id="prj_mult",
                company_id="cmp_x",
                cabinet_id="cab_x",
                owner_employee_id="emp_x",
                name="Multi",
                slug="multi",
                status="active",
                visibility_mode="cabinet_shared",
                workspace_key="wk_multi",
                container_ref="sandbox-claim-x",
            )
        )
        base = dict(
            project_id="prj_mult", workspace_key="wk_multi", desired_state="running"
        )
        session.add(ProjectPodRow(id="pod_old", status="terminated", runtime_ref="pod-old", hydrate_generation=1, created_at=now - timedelta(hours=2), updated_at=now - timedelta(hours=2), **base))
        session.add(ProjectPodRow(id="pod_mid", status="failed", runtime_ref="pod-mid", hydrate_generation=2, created_at=now - timedelta(hours=1), updated_at=now - timedelta(hours=1), **base))
        session.add(ProjectPodRow(id="pod_new", status="running", runtime_ref="sandbox-claim-x", hydrate_generation=7, created_at=now, updated_at=now, **base))
        await session.commit()

        svc = AgentSessionService(session)
        assert await svc._project_hydrate_generation("prj_mult") == 7
        assert await svc._project_hydrate_generation("prj_missing") is None
    await engine.dispose()

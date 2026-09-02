"""Re-register resumable agent sessions in Pod bridge after runtime restart."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.agent.credential_broker import AgentCredentialBroker
from prodavan.application.agent.openclaw_bridge import (
    BridgeSessionBootstrap,
    OpenClawBridgeBootstrap,
    api_kind_to_bridge_adapter,
)
from prodavan.application.agent.session_service import AgentSessionService
from prodavan.application.pod_service.runtime_observation import RuntimeObservationService
from prodavan.config.settings import settings
from prodavan.core.infra.cache import acquire_lock, cache_key, release_lock
from prodavan.core.infra.redis_manager import get_redis_manager
from prodavan.domain.agent import AgentSessionStatus
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow

logger = logging.getLogger(__name__)

_BOOTSTRAP_WAIT_SEC = 120


def _bootstrap_payload(row: AgentSessionRow) -> BridgeSessionBootstrap:
    adapter_state = row.adapter_state
    return BridgeSessionBootstrap(
        session_id=row.id,
        prodavan_session_id=row.id,
        adapter_kind=api_kind_to_bridge_adapter(row.api_kind),
        model=row.model,
        provider_key_id=row.resolved_key_id,
        adapter_state=adapter_state if isinstance(adapter_state, dict) else None,
    )


class PodSessionBootstrap:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._sessions = AgentSessionService(session)
        self._bridge = OpenClawBridgeBootstrap(session)

    async def bootstrap_project_sessions(
        self,
        *,
        project_id: str,
        reactivate: bool = True,
        wait_for_pod: bool = True,
    ) -> dict:
        """Reactivate suspended sessions and register them in the pod bridge. Caller owns commit."""
        if not settings.pod_agent_runtime_enabled:
            return {"project_id": project_id, "registered": 0, "skipped": "runtime_disabled"}

        if wait_for_pod:
            try:
                await RuntimeObservationService(self._session).wait_for_running(
                    project_id=project_id,
                    timeout_sec=_BOOTSTRAP_WAIT_SEC,
                )
            except Exception as exc:
                logger.warning(
                    "pod session bootstrap wait failed project_id=%s: %s",
                    project_id,
                    exc,
                )
                return {"project_id": project_id, "registered": 0, "skipped": "pod_not_running"}

        pod = await self._session.execute(
            select(ProjectPodRow)
            .where(ProjectPodRow.project_id == project_id)
            .order_by(ProjectPodRow.created_at.desc())
            .limit(1)
        )
        pod_row = pod.scalar_one_or_none()
        generation = pod_row.hydrate_generation if pod_row is not None else 0
        guard_key = cache_key("session-bootstrap", project_id, str(generation))
        redis_enabled = _redis_enabled()
        lock_token = await acquire_lock(guard_key, ttl_sec=300) if redis_enabled else "local"
        if lock_token is None and redis_enabled and await self._redis_guard_exists(guard_key):
            return {"project_id": project_id, "registered": 0, "skipped": "already_bootstrapped"}

        session_ids: list[str] = []
        if reactivate:
            session_ids = await self._sessions.reactivate_resumable_for_project(project_id=project_id)

        result = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
            .where(AgentSessionRow.status == AgentSessionStatus.ACTIVE)
            .order_by(AgentSessionRow.created_at.asc())
        )
        rows = list(result.scalars().all())
        registered = 0
        keys_pushed: set[str] = set()
        broker = AgentCredentialBroker(self._session)
        for row in rows:
            if await self._bridge.register_session(project_id=project_id, payload=_bootstrap_payload(row)):
                registered += 1
            if row.resolved_key_id and row.resolved_key_id not in keys_pushed:
                await broker.push_lease_to_runtime(project_id=project_id, key_id=row.resolved_key_id)
                keys_pushed.add(row.resolved_key_id)

        if lock_token is not None and lock_token != "local":
            await release_lock(guard_key, lock_token)
        logger.info(
            "pod session bootstrap project_id=%s reactivated=%s registered=%s",
            project_id,
            len(session_ids),
            registered,
        )
        return {
            "project_id": project_id,
            "reactivated": len(session_ids),
            "registered": registered,
            "session_ids": [row.id for row in rows],
        }

    @staticmethod
    async def _redis_guard_exists(key: str) -> bool:
        from prodavan.core.infra.cache import cache_get

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return False
        return (await cache_get(key)) is not None


def _redis_enabled() -> bool:
    mgr = get_redis_manager()
    return mgr is not None and mgr.enabled

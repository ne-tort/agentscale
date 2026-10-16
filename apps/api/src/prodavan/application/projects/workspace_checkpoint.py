"""Project workspace checkpoint — dehydrate live Pod /workspace → MinIO last-good."""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.factory import build_dehydrate
from prodavan.application.pod_service.ports.dehydrate import DehydratePort, DehydrateResult
from prodavan.application.pod_service.workspace_dehydrate_rules import (
    materialized_paths_from_manifest,
)
from prodavan.domain.pods import PodStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)

MATERIALIZED_MANIFEST_NAME = "materialized.json"


def _persist_materialized_manifest(*, workspace_key: str, project: ProjectRow) -> None:
    """Положить набор материализованных путей рядом с workspace (вне ``workspace/``).

    Дегидратация читает этот манифест и НЕ выкачивает перечисленные файлы из
    пода: их источник истины — Postgres, а копия пода может быть только равной
    или УСТАРЕВШЕЙ. Без манифеста чекапоинт старого пода перезатирал свежий
    `.prodavan/config.yaml`, `AGENTS.md` и промпты модулей, и пересозданный под
    гидрировался устаревшим конфигом провайдера (чат показывал модели нового
    провайдера, а вызов падал в 401 «Invalid token»).

    Манифест обязателен именно потому, что пути модулей шаблонные
    (`{{target_path}}`) — статическим списком их не покрыть.
    """
    manifest = getattr(project, "materialize_manifest", None)
    paths = sorted(
        materialized_paths_from_manifest(manifest if isinstance(manifest, dict) else {})
    )
    try:
        from prodavan.core.infra.object_keys import workspace_meta_object_key
        from prodavan.infrastructure.files.manager import ensure_file_store

        ensure_file_store().put_bytes_sync(
            workspace_meta_object_key(
                workspace_key=workspace_key, name=MATERIALIZED_MANIFEST_NAME
            ),
            json.dumps({"paths": paths}).encode("utf-8"),
            content_type="application/json",
        )
    except Exception:  # noqa: BLE001 - best-effort: статические правила остаются
        logger.exception(
            "materialized manifest persist failed workspace_key=%s", workspace_key
        )


async def checkpoint_project_workspace(
    session: AsyncSession,
    *,
    project_id: str,
    dehydrate: DehydratePort | None = None,
    best_effort: bool = True,
) -> DehydrateResult | None:
    """Sync live Pod workspace into object store before pause/reload or after a turn.

    Returns None when there is no running pod / runtime_ref (nothing to copy).
    When ``best_effort`` is True, errors are logged and None is returned instead of raising.
    """
    project = await session.get(ProjectRow, project_id)
    if project is None:
        return None
    q = await session.execute(
        select(ProjectPodRow).where(
            ProjectPodRow.project_id == project_id,
            ProjectPodRow.status.notin_((PodStatus.TERMINATED, PodStatus.FAILED)),
        )
    )
    pod = q.scalar_one_or_none()
    if pod is None:
        return None
    runtime_ref = str(pod.runtime_ref or project.container_ref or "").strip()
    if not runtime_ref:
        return None
    workspace_key = str(pod.workspace_key or project.workspace_key or "").strip()
    if not workspace_key:
        return None

    # ДО дегидратации: upload_workspace_tar читает манифест, чтобы не выкачать
    # из пода материализованные файлы и не затереть ими свежие в хранилище
    _persist_materialized_manifest(workspace_key=workspace_key, project=project)

    try:
        adapter = dehydrate or build_dehydrate()
        result = await adapter.dehydrate(workspace_key=workspace_key, runtime_ref=runtime_ref)
        logger.info(
            "workspace checkpoint project_id=%s uploaded=%s deleted=%s",
            project_id,
            result.uploaded,
            result.deleted,
        )
        return result
    except Exception:
        if best_effort:
            logger.exception("workspace checkpoint failed project_id=%s", project_id)
            return None
        raise

"""Shared guard — project pod must be running for agent/workspace mutations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.config.settings import settings
from prodavan.domain.agent.errors import pod_not_running, project_paused
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


async def require_running_pod_runtime(
    session: AsyncSession,
    *,
    project_id: str,
    principal: Principal,
    employee: EmployeeRow | None,
    write: bool = False,
) -> dict:
    """Return pod runtime_view dict when chat/agent/workspace mutations are allowed."""
    from prodavan.application.pod_service.query import PodQuery
    from prodavan.application.project_service.access import ProjectAccessPolicy

    access = ProjectAccessPolicy(session)
    project = await access.require_access(
        project_id=project_id,
        principal=principal,
        employee=employee,
        write=write,
        allow_paused=True,
    )
    if project.status == ProjectStatus.PAUSED:
        raise project_paused()

    runtime = await PodQuery(session).runtime_view(project_id)
    if runtime is None:
        raise pod_not_running()

    if runtime.get("stub"):
        if settings.agent_inprocess_adapters_enabled:
            status = str(runtime.get("status") or runtime.get("orchestrator_status") or "")
            if status == "running":
                return runtime
        raise pod_not_running(detail="pod runtime is stub-only")

    if runtime.get("observed_state") != "running":
        raise pod_not_running(
            detail=f"observed_state={runtime.get('observed_state') or 'unknown'}",
        )
    runtime_ref = str(runtime.get("k8s_pod_name") or runtime.get("runtime_ref") or "").strip()
    if not runtime_ref or runtime_ref.startswith("object-ws:"):
        raise pod_not_running(detail="no k8s runtime ref")
    return runtime

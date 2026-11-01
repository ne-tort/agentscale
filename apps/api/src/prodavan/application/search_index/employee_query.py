"""Employee-authenticated façade for tenant Search Index queries."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.equipment_catalog_opensearch import (
    OS_NAMESPACE as EQUIPMENT_NAMESPACE,
    PLATFORM_OS_COMPANY_ID,
    catalog_os_index_name,
)
from prodavan.application.modules.module_instance_service import ModuleInstanceService
from prodavan.application.project_service import ProjectAccessPolicy
from prodavan.core.infra.opensearch_manager import get_search_index_service
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.search_index.types import PLATFORM_NAMESPACES, validate_index, validate_namespace
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

_EQUIPMENT_CATALOGS_TABLE = "catalogs"
_EQUIPMENT_MODULE_ID = "mod_equipment"


def _row_applies_to_project(body: dict[str, Any], project_id: str) -> bool:
    project_ids = body.get("project_ids")
    if not isinstance(project_ids, list) or not project_ids:
        return True
    return project_id in {str(value) for value in project_ids}


class EmployeeSearchQueryService:
    """Search tenant indexes after applying employee project visibility ACL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        namespace: str,
        index: str,
        query: dict[str, Any] | None = None,
        filter: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
        search_after: list[Any] | None = None,
        from_: int = 0,
        size: int = 20,
    ) -> dict[str, Any]:
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        project = await ProjectAccessPolicy(self._session).require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=False,
            allow_paused=True,
        )
        try:
            ns = validate_namespace(namespace)
            idx = validate_index(index)
        except ValueError as exc:
            raise AppError(
                code="VALIDATION_ERROR", title="Validation Error", status=422, detail=str(exc)
            ) from exc
        if ns in PLATFORM_NAMESPACES:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail=f"namespace not allowed for employee search: {ns}",
            )

        # Equipment catalogs are indexed as platform-owned documents even though
        # their SoT and project/cabinet visibility are tenant-bound. Admit only
        # the exact ready indexes exposed by the project's equipment SoT. Do not
        # use the broader platform namespace exception for this capability.
        platform_equipment_catalog = False
        if ns == EQUIPMENT_NAMESPACE:
            # Resolve the project-visible module SoT first. This is the binding
            # gate; reading an arbitrary equipment index must never grant access.
            instance = await ModuleInstanceService(self._session).resolve_sot_instance(
                module_id=_EQUIPMENT_MODULE_ID,
                owner_kind="project",
                owner_id=project.id,
            )
            allowed_indexes: set[str] = set()
            if instance is not None:
                rows = await ModuleInstanceService(self._session).list_data_rows(
                    instance_id=instance.id,
                    table_slug=_EQUIPMENT_CATALOGS_TABLE,
                )
                indexes_by_row: dict[str, set[str]] = {}
                for row in rows:
                    body = row.get("body") if isinstance(row.get("body"), dict) else {}
                    row_id = str(row.get("row_id") or "").strip()
                    if (
                        row_id
                        and str(body.get("status") or "").strip().lower() == "ready"
                        and not bool(body.get("paused"))
                        and _row_applies_to_project(body, project.id)
                    ):
                        indexes_by_row.setdefault(catalog_os_index_name(row_id), set()).add(row_id)
                # Normalization and truncation can alias distinct row IDs to one
                # physical index. Refuse ambiguous aliases to prevent one bound
                # catalog from exposing another catalog's documents.
                allowed_indexes = {
                    index for index, row_ids in indexes_by_row.items() if len(row_ids) == 1
                }
            if idx not in allowed_indexes:
                raise AppError(
                    code="FORBIDDEN",
                    title="Forbidden",
                    status=403,
                    detail="equipment catalog index not visible for this project",
                )
            platform_equipment_catalog = True

        if search_after and from_:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="from must be zero when search_after is supplied",
            )
        result = await get_search_index_service().search(
            namespace=ns,
            index=idx,
            query=query,
            from_=from_,
            size=size,
            company_id=PLATFORM_OS_COMPANY_ID if platform_equipment_catalog else project.company_id,
            cabinet_id=project.cabinet_id,
            project_id=project.id,
            filter=filter,
            include_unscoped_project_docs=True,
            session=self._session,
            sort=sort,
            search_after=search_after,
        )
        return {
            "hits": [
                {"id": hit.doc_id, "score": hit.score, "document": hit.source, "sort": hit.sort}
                for hit in result.hits
            ],
            "total": result.total,
            "took_ms": result.took_ms,
            "next_search_after": result.next_search_after,
        }

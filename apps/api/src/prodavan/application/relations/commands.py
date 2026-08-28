"""RelationsCommand — grant/revoke/replace + Kafka relation.* events after commit."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.grant_service import CabinetGrantService
from prodavan.application.project_service.grant_service import ProjectGrantService
from prodavan.domain.relations import (
    RELATION_GRANTED,
    RELATION_REPLACED,
    RELATION_REVOKED,
    EntityKind,
    RelationKind,
)

logger = logging.getLogger(__name__)


class RelationsCommand:
    """Write facade: mutates existing SoT tables, publishes relation events after commit."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._grants = CabinetGrantService(session)
        self._project_grants = ProjectGrantService(session)

    async def ensure_membership(
        self,
        *,
        company_id: str,
        employee_id: str,
        role: str,
    ) -> None:
        from prodavan.application.employees.service import EmployeesCommandService

        await EmployeesCommandService(self._session).ensure_membership(
            company_id=company_id,
            employee_id=employee_id,
            role=role,
        )
        await self._publish(
            event_type=RELATION_GRANTED,
            payload={
                "relation_kind": RelationKind.MEMBERSHIP,
                "subject_kind": EntityKind.EMPLOYEE,
                "subject_id": employee_id,
                "object_kind": EntityKind.COMPANY,
                "object_id": company_id,
                "role": role,
                "status": "active",
            },
            company_id=company_id,
        )

    async def assign_employee_to_cabinet(
        self,
        *,
        cabinet_id: str,
        company_id: str,
        employee_id: str,
    ) -> None:
        await self._grants.assign_employee(
            cabinet_id=cabinet_id,
            company_id=company_id,
            employee_id=employee_id,
        )
        await self._publish(
            event_type=RELATION_GRANTED,
            payload={
                "relation_kind": RelationKind.ASSIGNMENT,
                "subject_kind": EntityKind.EMPLOYEE,
                "subject_id": employee_id,
                "object_kind": EntityKind.CABINET,
                "object_id": cabinet_id,
                "via_company_id": company_id,
                "status": "active",
            },
            company_id=company_id,
            cabinet_id=cabinet_id,
        )

    async def revoke_employee_from_cabinet(
        self,
        *,
        cabinet_id: str,
        employee_id: str,
        company_id: str | None = None,
    ) -> None:
        await self._grants.revoke_employee(cabinet_id=cabinet_id, employee_id=employee_id)
        await self._publish(
            event_type=RELATION_REVOKED,
            payload={
                "relation_kind": RelationKind.ASSIGNMENT,
                "subject_kind": EntityKind.EMPLOYEE,
                "subject_id": employee_id,
                "object_kind": EntityKind.CABINET,
                "object_id": cabinet_id,
                "status": "revoked",
            },
            company_id=company_id,
            cabinet_id=cabinet_id,
        )

    async def replace_cabinet_company_grants(
        self,
        cabinet_id: str,
        company_ids: list[str],
        *,
        mode: str = "assigned_ro",
    ) -> list[str]:
        result = await self._grants.replace_company_grants(
            cabinet_id, company_ids, mode=mode
        )
        await self._publish(
            event_type=RELATION_REPLACED,
            payload={
                "relation_kind": RelationKind.GRANT,
                "subject_kind": EntityKind.COMPANY,
                "object_kind": EntityKind.CABINET,
                "object_id": cabinet_id,
                "company_ids": result,
                "mode": mode,
            },
            cabinet_id=cabinet_id,
        )
        return result

    async def assign_employee_to_project(
        self,
        *,
        project_id: str,
        cabinet_id: str,
        company_id: str,
        employee_id: str,
    ) -> None:
        await self._project_grants.assign_employee(
            project_id=project_id,
            cabinet_id=cabinet_id,
            employee_id=employee_id,
        )
        await self._publish(
            event_type=RELATION_GRANTED,
            payload={
                "relation_kind": RelationKind.ASSIGNMENT,
                "subject_kind": EntityKind.EMPLOYEE,
                "subject_id": employee_id,
                "object_kind": EntityKind.PROJECT,
                "object_id": project_id,
                "via_cabinet_id": cabinet_id,
                "via_company_id": company_id,
                "status": "active",
            },
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )

    async def revoke_employee_from_project(
        self,
        *,
        project_id: str,
        employee_id: str,
        company_id: str | None = None,
        cabinet_id: str | None = None,
    ) -> None:
        await self._project_grants.revoke_employee(project_id=project_id, employee_id=employee_id)
        await self._publish(
            event_type=RELATION_REVOKED,
            payload={
                "relation_kind": RelationKind.ASSIGNMENT,
                "subject_kind": EntityKind.EMPLOYEE,
                "subject_id": employee_id,
                "object_kind": EntityKind.PROJECT,
                "object_id": project_id,
                "status": "revoked",
            },
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )

    async def replace_project_assignments(
        self,
        *,
        project_id: str,
        cabinet_id: str,
        company_id: str,
        employee_ids: list[str],
    ) -> list[str]:
        result = await self._project_grants.replace_assignments(
            project_id=project_id,
            cabinet_id=cabinet_id,
            employee_ids=employee_ids,
        )
        await self._publish(
            event_type=RELATION_REPLACED,
            payload={
                "relation_kind": RelationKind.ASSIGNMENT,
                "subject_kind": EntityKind.EMPLOYEE,
                "object_kind": EntityKind.PROJECT,
                "object_id": project_id,
                "employee_ids": result,
                "via_cabinet_id": cabinet_id,
                "via_company_id": company_id,
            },
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        return result

    async def _publish(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        from prodavan.core.events.deferred import schedule_envelope_publish
        from prodavan.core.events.envelope import relation_event_envelope

        try:
            schedule_envelope_publish(
                self._session,
                relation_event_envelope(
                    event_id=str(uuid.uuid4()),
                    event_type=event_type,
                    company_id=company_id,
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    payload=payload,
                ),
            )
        except Exception:
            logger.exception("relations: schedule %s failed", event_type)


async def handle_relation_event_envelope(envelope: Any) -> None:
    """Kafka consumer hook for relation.* — side-effects only (no SoT mutate)."""
    from prodavan.domain.relations import RELATION_REVOKED, RelationKind

    if getattr(envelope, "bus", None) != "relation_event":
        return
    payload = getattr(envelope, "payload", None) or {}
    kind = payload.get("relation_kind")
    if envelope.event_type == RELATION_REVOKED and kind == RelationKind.BINDING:
        # Future: pause projects when AI key binding revoked — currently AiKeys still does inline.
        logger.info(
            "relation revoked binding subject=%s object=%s",
            payload.get("subject_id"),
            payload.get("object_id"),
        )
        return
    logger.debug("relation event type=%s kind=%s", envelope.event_type, kind)

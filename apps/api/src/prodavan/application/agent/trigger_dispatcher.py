"""Dispatch project triggers to agent runs (L08)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.agent.session_service import AgentSessionService
from prodavan.application.projects.access import ProjectAccessService
from prodavan.domain.agent import PLATFORM_EVENT_USER_MESSAGE, AgentSessionStatus
from prodavan.domain.identity import Principal
from prodavan.domain.projects import TriggerStatus
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectTriggerRow

_DEFAULT_DRAIN_MAX = 10
_HARD_DRAIN_MAX = 50

# Kinds that only need queue ack until a real handler exists (cabinet SPI / cron).
_ACK_ONLY_KINDS = frozenset({"project.prepare", "system.schedule", "webhook.http"})


class AgentTriggerDispatcher:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._sessions = AgentSessionService(session)
        self._projects = ProjectAccessService(session)

    async def dispatch_next(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        return await self._dispatch_one(project_id=project_id, principal=principal, employee=employee)

    async def dispatch_batch(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        max_n: int = _DEFAULT_DRAIN_MAX,
    ) -> dict:
        """Drain up to max_n queued triggers (manual worker substitute)."""
        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        limit = max_n if 1 <= max_n <= _HARD_DRAIN_MAX else _DEFAULT_DRAIN_MAX
        results: list[dict] = []
        for _ in range(limit):
            one = await self._dispatch_one(
                project_id=project_id, principal=principal, employee=employee
            )
            if not one.get("dispatched"):
                if not results and one.get("reason") == "no queued triggers":
                    return {"dispatched": False, "reason": "no queued triggers", "items": []}
                break
            results.append(one)
        return {
            "dispatched": bool(results),
            "count": len(results),
            "items": results,
        }

    async def drain_all(
        self,
        *,
        max_projects: int = 20,
        max_per_project: int = _DEFAULT_DRAIN_MAX,
    ) -> dict:
        """Drain queued triggers across active projects (background worker / admin)."""
        from prodavan.application.projects.trigger_service import ProjectTriggerService

        limit_projects = max(1, min(max_projects, 100))
        limit_each = max_per_project if 1 <= max_per_project <= _HARD_DRAIN_MAX else _DEFAULT_DRAIN_MAX
        project_ids = await ProjectTriggerService(self._session).list_active_project_ids_with_queued(
            limit=limit_projects
        )
        projects: list[dict] = []
        total = 0
        for project_id in project_ids:
            project = await self._projects.get_project(project_id)
            owner = await self._session.get(EmployeeRow, project.owner_employee_id)
            if owner is None:
                projects.append(
                    {
                        "project_id": project_id,
                        "dispatched": False,
                        "reason": "owner missing",
                        "count": 0,
                        "items": [],
                    }
                )
                continue
            principal = Principal(sub=owner.keycloak_sub or owner.id, email=owner.email)
            batch = await self.dispatch_batch(
                project_id=project_id,
                principal=principal,
                employee=owner,
                max_n=limit_each,
            )
            total += int(batch.get("count") or 0)
            projects.append({"project_id": project_id, **batch})
        return {
            "dispatched": total > 0,
            "count": total,
            "projects": projects,
        }

    async def _last_user_turn(
        self, *, project_id: str, session_id: str | None
    ) -> tuple[str | None, list[str]]:
        """Find last user_message text+refs for regenerate (session-scoped or project-latest)."""
        session_row: AgentSessionRow | None = None
        if session_id:
            session_row = await self._session.get(AgentSessionRow, session_id)
            if session_row is None or session_row.project_id != project_id:
                return None, []
        else:
            q = await self._session.execute(
                select(AgentSessionRow)
                .where(
                    AgentSessionRow.project_id == project_id,
                    AgentSessionRow.status == AgentSessionStatus.ACTIVE,
                )
                .order_by(AgentSessionRow.updated_at.desc())
                .limit(1)
            )
            session_row = q.scalar_one_or_none()
        if session_row is None:
            return None, []

        evt_q = await self._session.execute(
            select(AgentEventRow)
            .where(
                AgentEventRow.session_id == session_row.id,
                AgentEventRow.event_type == PLATFORM_EVENT_USER_MESSAGE,
            )
            .order_by(AgentEventRow.seq.desc())
            .limit(1)
        )
        event = evt_q.scalar_one_or_none()
        if event is None:
            return None, []
        payload = event.payload or {}
        text = str(payload.get("text") or "")
        refs = payload.get("attachment_refs") or []
        return text, list(refs) if isinstance(refs, list) else []

    async def _run_chat_turn(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        text: str,
        attachment_refs: list[str],
        session_id: str | None = None,
    ) -> dict:
        if session_id:
            result = await self._sessions.send_message(
                project_id=project_id,
                session_id=session_id,
                text=text,
                attachment_refs=attachment_refs,
                principal=principal,
                employee=employee,
            )
            return result
        session_row = await self._sessions.create_session(
            project_id=project_id, principal=principal, employee=employee
        )
        return await self._sessions.send_message(
            project_id=project_id,
            session_id=session_row["id"],
            text=text,
            attachment_refs=attachment_refs,
            principal=principal,
            employee=employee,
        )

    async def _dispatch_one(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        q = await self._session.execute(
            select(ProjectTriggerRow)
            .where(
                ProjectTriggerRow.project_id == project_id,
                ProjectTriggerRow.status == TriggerStatus.QUEUED,
            )
            .order_by(ProjectTriggerRow.created_at)
            .limit(1)
        )
        trigger = q.scalar_one_or_none()
        if trigger is None:
            return {"dispatched": False, "reason": "no queued triggers"}

        payload = trigger.payload or {}

        if trigger.kind == "chat.message":
            text = str(payload.get("text") or "")
            refs = payload.get("attachment_refs") or []
            result = await self._run_chat_turn(
                project_id=project_id,
                principal=principal,
                employee=employee,
                text=text,
                attachment_refs=list(refs) if isinstance(refs, list) else [],
            )
            trigger.status = TriggerStatus.DONE
            await self._session.commit()
            return {"dispatched": True, "trigger_id": trigger.id, "run": result}

        if trigger.kind == "chat.regenerate":
            session_id = payload.get("session_id")
            session_id = str(session_id) if session_id else None
            text = str(payload.get("text") or "").strip()
            refs = payload.get("attachment_refs") or []
            if not text:
                last_text, last_refs = await self._last_user_turn(
                    project_id=project_id, session_id=session_id
                )
                text = (last_text or "").strip()
                if not refs and last_refs:
                    refs = last_refs
            if not text:
                trigger.status = TriggerStatus.FAILED
                await self._session.commit()
                return {
                    "dispatched": False,
                    "trigger_id": trigger.id,
                    "reason": "nothing to regenerate",
                }
            result = await self._run_chat_turn(
                project_id=project_id,
                principal=principal,
                employee=employee,
                text=text,
                attachment_refs=list(refs) if isinstance(refs, list) else [],
                session_id=session_id,
            )
            trigger.status = TriggerStatus.DONE
            await self._session.commit()
            return {"dispatched": True, "trigger_id": trigger.id, "run": result}

        if trigger.kind in _ACK_ONLY_KINDS:
            # schedule/webhook may carry text to kick agent; otherwise ack-only.
            text = str(payload.get("text") or "").strip()
            if text:
                refs = payload.get("attachment_refs") or []
                result = await self._run_chat_turn(
                    project_id=project_id,
                    principal=principal,
                    employee=employee,
                    text=text,
                    attachment_refs=list(refs) if isinstance(refs, list) else [],
                )
                trigger.status = TriggerStatus.DONE
                await self._session.commit()
                return {
                    "dispatched": True,
                    "trigger_id": trigger.id,
                    "action": f"run_{trigger.kind}",
                    "run": result,
                }
            trigger.status = TriggerStatus.DONE
            await self._session.commit()
            return {
                "dispatched": True,
                "trigger_id": trigger.id,
                "action": f"ack_{trigger.kind}",
            }

        trigger.status = TriggerStatus.FAILED
        await self._session.commit()
        return {"dispatched": False, "trigger_id": trigger.id, "reason": f"unsupported kind {trigger.kind}"}

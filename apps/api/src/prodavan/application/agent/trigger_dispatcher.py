"""Dispatch project triggers to agent runs (L08)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.subscription_gate import CompanySubscriptionGate
from prodavan.application.agent.session_service import AgentSessionService
from prodavan.application.projects.access import ProjectAccessService
from prodavan.application.projects.trigger_service import ProjectTriggerService
from prodavan.domain.agent import PLATFORM_EVENT_USER_MESSAGE, AgentSessionStatus
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ProjectStatus
from prodavan.domain.projects.types import SUBSCRIPTION_EXEMPT_TRIGGER_KINDS
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
        self._subscription = CompanySubscriptionGate(session)
        self._triggers = ProjectTriggerService(session)

    async def dispatch_next(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        project = await self._projects.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        if project.status == ProjectStatus.PAUSED:
            return {"dispatched": False, "reason": "project_paused"}
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
        project = await self._projects.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        if project.status == ProjectStatus.PAUSED:
            return {"dispatched": False, "reason": "project_paused", "items": []}
        limit = max_n if 1 <= max_n <= _HARD_DRAIN_MAX else _DEFAULT_DRAIN_MAX
        results: list[dict] = []
        for _ in range(limit):
            one = await self._dispatch_one(
                project_id=project_id, principal=principal, employee=employee
            )
            if not one.get("dispatched"):
                if one.get("reason") == "no queued triggers":
                    if not results:
                        return {"dispatched": False, "reason": "no queued triggers", "items": []}
                    break
                results.append(one)
                # Whole project blocked — stop draining this project.
                if one.get("reason") == "company_suspended":
                    break
                continue
            results.append(one)
        return {
            "dispatched": any(r.get("dispatched") for r in results),
            "count": sum(1 for r in results if r.get("dispatched")),
            "items": results,
        }

    async def drain_all(
        self,
        *,
        max_projects: int = 20,
        max_per_project: int = _DEFAULT_DRAIN_MAX,
    ) -> dict:
        """Drain queued triggers across active projects (background worker / admin)."""
        limit_projects = max(1, min(max_projects, 100))
        limit_each = max_per_project if 1 <= max_per_project <= _HARD_DRAIN_MAX else _DEFAULT_DRAIN_MAX
        project_ids = await self._triggers.list_active_project_ids_with_queued(limit=limit_projects)
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

    async def _finish_ok(self, trigger: ProjectTriggerRow, result: dict) -> dict:
        await self._triggers.mark_done(trigger)
        await self._session.commit()
        return {"dispatched": True, "trigger_id": trigger.id, "kind": trigger.kind, **result}

    async def _finish_hard_fail(self, trigger: ProjectTriggerRow, reason: str) -> dict:
        await self._triggers.mark_failed(trigger, reason=reason)
        await self._session.commit()
        return {
            "dispatched": False,
            "trigger_id": trigger.id,
            "kind": trigger.kind,
            "reason": reason,
        }

    async def _finish_soft_or_hard(self, trigger: ProjectTriggerRow, reason: str) -> dict:
        retried = await self._triggers.release_for_retry(trigger, reason=reason)
        await self._session.commit()
        return {
            "dispatched": False,
            "trigger_id": trigger.id,
            "kind": trigger.kind,
            "reason": reason,
            "retried": retried,
            "attempts": trigger.attempts,
        }

    async def _dispatch_one(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        trigger = await self._triggers.claim_next(
            project_id=project_id,
            worker_id=f"disp_{principal.sub[:24]}",
        )
        if trigger is None:
            return {"dispatched": False, "reason": "no queued triggers"}

        if trigger.kind not in SUBSCRIPTION_EXEMPT_TRIGGER_KINDS:
            project = await self._projects.get_project(project_id)
            state = await self._subscription.subscription_state(project.company_id)
            if state.get("subscription_expired"):
                return await self._finish_hard_fail(trigger, "company_suspended")

        payload = trigger.payload or {}

        try:
            if trigger.kind in {"chat.message", "telegram.message"}:
                text = str(payload.get("text") or "")
                refs = payload.get("attachment_refs") or []
                result = await self._run_chat_turn(
                    project_id=project_id,
                    principal=principal,
                    employee=employee,
                    text=text,
                    attachment_refs=list(refs) if isinstance(refs, list) else [],
                )
                return await self._finish_ok(trigger, {"run": result})

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
                    return await self._finish_hard_fail(trigger, "nothing to regenerate")
                result = await self._run_chat_turn(
                    project_id=project_id,
                    principal=principal,
                    employee=employee,
                    text=text,
                    attachment_refs=list(refs) if isinstance(refs, list) else [],
                    session_id=session_id,
                )
                return await self._finish_ok(trigger, {"run": result})

            if trigger.kind in _ACK_ONLY_KINDS:
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
                    return await self._finish_ok(
                        trigger, {"action": f"run_{trigger.kind}", "run": result}
                    )
                return await self._finish_ok(trigger, {"action": f"ack_{trigger.kind}"})

            return await self._finish_hard_fail(trigger, f"unsupported kind {trigger.kind}")
        except Exception as exc:  # noqa: BLE001 — outbox must not leave lease stuck forever
            return await self._finish_soft_or_hard(trigger, f"dispatch_error: {exc}")

"""Agent session lifecycle + event persistence (L08)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.admin.subscription_gate import CompanySubscriptionGate
from prodavan.application.agent.adapter_registry import get_agent_adapter
from prodavan.application.agent.budget_service import AgentBudgetService
from prodavan.application.agent.openclaw_bridge import (
    BridgeSessionBootstrap,
    OpenClawBridgeBootstrap,
    api_kind_to_bridge_adapter,
)
from prodavan.application.agent.policy_service import AgentPolicyService
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.application.project_service import ProjectAccessPolicy
from prodavan.application.projects.attachment_service import ProjectAttachmentService
from prodavan.config.settings import settings
from prodavan.domain.agent import (
    FROZEN_EVENT_TYPES,
    PLATFORM_EVENT_TOOL_APPROVAL_DECISION,
    PLATFORM_EVENT_USER_MESSAGE,
    AgentEventType,
    AgentHandle,
    AgentSessionStatus,
    ChatMessage,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow, AgentUsageRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter


def _assistant_text_from_events(events: list[dict]) -> str:
    parts: list[str] = []
    for event in events:
        if event.get("type") != AgentEventType.TEXT_DELTA:
            continue
        data = event.get("data") or {}
        chunk = data.get("text")
        if chunk:
            parts.append(str(chunk))
    return "".join(parts)


def events_to_transcript(events: list[dict]) -> list[dict]:
    """Collapse platform user_message + text_delta turns into chat bubbles."""
    messages: list[dict] = []
    assistant_parts: list[str] = []

    def flush_assistant() -> None:
        if not assistant_parts:
            return
        messages.append({"role": "assistant", "text": "".join(assistant_parts)})
        assistant_parts.clear()

    for event in events:
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == PLATFORM_EVENT_USER_MESSAGE:
            flush_assistant()
            text = data.get("text")
            refs = data.get("attachment_refs") or []
            if text or refs:
                bubble: dict = {"role": "user", "text": str(text or "")}
                if isinstance(refs, list) and refs:
                    bubble["attachment_refs"] = [str(r) for r in refs]
                messages.append(bubble)
        elif etype == AgentEventType.TOOL_CALL:
            flush_assistant()
            name = data.get("name")
            if name:
                messages.append({"role": "tool", "text": str(name)})
        elif etype == AgentEventType.TOOL_APPROVAL_REQUEST:
            flush_assistant()
            name = data.get("name") or "tool"
            approval_id = data.get("id") or ""
            messages.append(
                {
                    "role": "approval",
                    "text": f"Approve {name}?",
                    "approval_id": approval_id,
                    "tool_name": name,
                    "input": data.get("input") or {},
                }
            )
        elif etype == AgentEventType.TEXT_DELTA:
            chunk = data.get("text")
            if chunk:
                assistant_parts.append(str(chunk))
        elif etype in {AgentEventType.DONE, AgentEventType.ERROR}:
            flush_assistant()

    flush_assistant()
    return messages


def _session_public(row: AgentSessionRow) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "resolved_key_id": row.resolved_key_id,
        "provider": row.provider,
        "api_kind": row.api_kind,
        "vendor_agent_id": row.vendor_agent_id,
        "model": row.model,
        "cwd": row.cwd,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _event_public(row: AgentEventRow) -> dict:
    return {
        "seq": row.seq,
        "type": row.event_type,
        "data": row.payload,
        "at": row.at.isoformat() if row.at else None,
    }


APPENDABLE_AGENT_EVENT_TYPES = frozenset(
    {
        PLATFORM_EVENT_USER_MESSAGE,
        *FROZEN_EVENT_TYPES,
    }
)

POD_AGENT_APPENDABLE_EVENT_TYPES = frozenset(FROZEN_EVENT_TYPES)


class AgentSessionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectAccessPolicy(session)
        self._policy = AgentPolicyService(session)
        self._keys = AiKeysService(session)
        self._budget = AgentBudgetService(session)
        self._subscription = CompanySubscriptionGate(session)

    async def create_session(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        model: str | None = None,
    ) -> dict:
        """Start an agent session — blocked while project is paused (runtime)."""
        project = await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        await self._subscription.require_active(project.company_id)
        company_policy = await AdminCompanyService(self._session).get_agent_policy(project.company_id)
        await self._budget.enforce_before_turn(
            company_id=project.company_id,
            session_id=None,
            policy=company_policy,
        )
        credential = await self._keys.resolve_credentials_for_project(
            project=project,
            preferred_provider=project.agent_provider or company_policy.preferred_provider,
            platform_fallback=company_policy.platform_fallback,
        )
        cwd = str(WorkspaceLayoutWriter(workspace_key=project.workspace_key).workspace_root)
        opts = await self._policy.build_create_opts(
            project=project, cwd=cwd, credential=credential, model_override=model
        )
        adapter = get_agent_adapter(api_kind=credential.api_kind)
        handle = await adapter.create(opts)
        row = AgentSessionRow(
            project_id=project_id,
            resolved_key_id=credential.key_id,
            provider=credential.provider,
            api_kind=credential.api_kind,
            vendor_agent_id=handle.id,
            model=handle.model,
            cwd=cwd,
            status=AgentSessionStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        await OpenClawBridgeBootstrap(self._session).register_session(
            project_id=project_id,
            payload=BridgeSessionBootstrap(
                session_id=row.id,
                prodavan_session_id=row.id,
                adapter_kind=api_kind_to_bridge_adapter(credential.api_kind),
                model=row.model,
                provider_key_id=credential.key_id,
            ),
        )
        return _session_public(row)

    async def get_session(self, *, session_id: str) -> AgentSessionRow:
        row = await self._session.get(AgentSessionRow, session_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        return row

    async def list_sessions(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        limit: int = 50,
    ) -> list[dict]:
        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        q = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
            .order_by(AgentSessionRow.created_at.desc())
            .limit(limit)
        )
        return [_session_public(r) for r in q.scalars().all()]

    async def send_message(
        self,
        *,
        project_id: str,
        session_id: str,
        text: str,
        attachment_refs: list[str] | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        events_out: list[dict] = []
        async for event in self._iter_send_events(
            project_id=project_id,
            session_id=session_id,
            text=text,
            attachment_refs=attachment_refs,
            principal=principal,
            employee=employee,
        ):
            events_out.append(event)
        await self._session.commit()
        return {"session_id": session_id, "events": events_out}

    async def _iter_send_events(
        self,
        *,
        project_id: str,
        session_id: str,
        text: str,
        attachment_refs: list[str] | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> AsyncIterator[dict]:
        project = await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        await self._subscription.require_active(project.company_id)
        row = await self.get_session(session_id=session_id)
        if row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        if row.status != AgentSessionStatus.ACTIVE:
            raise AppError(code="SESSION_CLOSED", title="Session closed", status=409, detail="session not active")

        company_policy = await AdminCompanyService(self._session).get_agent_policy(project.company_id)
        await self._budget.enforce_before_turn(
            company_id=project.company_id,
            session_id=session_id,
            policy=company_policy,
        )

        adapter = get_agent_adapter(api_kind=row.api_kind)
        handle = AgentHandle(id=row.vendor_agent_id, provider=row.provider, cwd=row.cwd, model=row.model)
        normalized_refs = await ProjectAttachmentService(self._session).normalize_refs(
            project_id=project_id,
            refs=list(attachment_refs or ()),
        )
        refs = tuple(normalized_refs)
        message = ChatMessage(text=text, attachment_refs=refs)

        seq_q = await self._session.execute(
            select(func.coalesce(func.max(AgentEventRow.seq), 0)).where(AgentEventRow.session_id == session_id)
        )
        seq = int(seq_q.scalar_one() or 0)

        seq += 1
        user_payload: dict = {"text": text}
        if refs:
            user_payload["attachment_refs"] = list(refs)
        self._session.add(
            AgentEventRow(
                session_id=session_id,
                seq=seq,
                event_type=PLATFORM_EVENT_USER_MESSAGE,
                payload=user_payload,
                at=None,
            )
        )
        yield {"type": PLATFORM_EVENT_USER_MESSAGE, "data": user_payload}

        used_bridge = False
        if settings.pod_agent_bridge_enabled:
            bridge = OpenClawBridgeBootstrap(self._session)
            api_key: str | None = None
            if row.resolved_key_id:
                try:
                    api_key = await self._keys.resolve_secret_for_key(row.resolved_key_id)
                except Exception:
                    api_key = None

            async for event in bridge.iter_send_events(
                project_id=project_id,
                session_id=session_id,
                message=text,
                api_key=api_key,
            ):
                used_bridge = True
                seq += 1
                self._session.add(
                    AgentEventRow(
                        session_id=session_id,
                        seq=seq,
                        event_type=event.type,
                        payload=event.data,
                        at=None,
                    )
                )
                yield event.to_dict()
                if event.type == AgentEventType.USAGE:
                    self._session.add(
                        AgentUsageRow(
                            session_id=session_id,
                            turn_id=f"turn_{seq}",
                            provider=str(event.data.get("provider") or row.provider),
                            model=event.data.get("model") or row.model,
                            input_tokens=event.data.get("input_tokens"),
                            output_tokens=event.data.get("output_tokens"),
                            cost_usd=Decimal(str(event.data["cost_usd"]))
                            if event.data.get("cost_usd") is not None
                            else None,
                        )
                    )
                if event.type in {AgentEventType.DONE, AgentEventType.ERROR}:
                    return

            if used_bridge:
                return

        async for event in adapter.send(handle, message):
            seq += 1
            self._session.add(
                AgentEventRow(
                    session_id=session_id,
                    seq=seq,
                    event_type=event.type,
                    payload=event.data,
                    at=None,
                )
            )
            yield event.to_dict()
            if event.type == AgentEventType.USAGE:
                self._session.add(
                    AgentUsageRow(
                        session_id=session_id,
                        turn_id=f"turn_{seq}",
                        provider=str(event.data.get("provider") or row.provider),
                        model=event.data.get("model") or row.model,
                        input_tokens=event.data.get("input_tokens"),
                        output_tokens=event.data.get("output_tokens"),
                        cost_usd=Decimal(str(event.data["cost_usd"]))
                        if event.data.get("cost_usd") is not None
                        else None,
                    )
                )

    async def chat_turn(
        self,
        *,
        project_id: str,
        text: str,
        session_id: str | None,
        attachment_refs: list[str] | None,
        principal: Principal,
        employee: EmployeeRow | None,
        model: str | None = None,
    ) -> dict:
        """One-shot chat: reuse active session or create, then send (L05/L09)."""
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        sid = await self._resolve_sendable_session_id(
            project_id=project_id,
            session_id=session_id,
            principal=principal,
            employee=employee,
            model=model,
        )
        result = await self.send_message(
            project_id=project_id,
            session_id=sid,
            text=text,
            attachment_refs=attachment_refs,
            principal=principal,
            employee=employee,
        )
        result["assistant_text"] = _assistant_text_from_events(result.get("events") or [])
        result["pending_approvals"] = _pending_approvals_from_events(result.get("events") or [])
        return result

    async def iter_chat_turn_sse(
        self,
        *,
        project_id: str,
        text: str,
        session_id: str | None,
        attachment_refs: list[str] | None,
        principal: Principal,
        employee: EmployeeRow | None,
        model: str | None = None,
    ) -> AsyncIterator[dict]:
        """SSE event stream for L05 workspace — persists like send_message."""
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        sid = await self._resolve_sendable_session_id(
            project_id=project_id,
            session_id=session_id,
            principal=principal,
            employee=employee,
            model=model,
        )
        yield {"type": "_session", "data": {"session_id": sid}}
        events: list[dict] = []
        try:
            async for event in self._iter_send_events(
                project_id=project_id,
                session_id=sid,
                text=text,
                attachment_refs=attachment_refs,
                principal=principal,
                employee=employee,
            ):
                events.append(event)
                yield event
            await self._session.commit()
        except AppError:
            await self._session.rollback()
            raise
        assistant_text = _assistant_text_from_events(events)
        yield {
            "type": "_turn_complete",
            "data": {
                "session_id": sid,
                "assistant_text": assistant_text,
                "pending_approvals": _pending_approvals_from_events(events),
            },
        }

    async def _resolve_sendable_session_id(
        self,
        *,
        project_id: str,
        session_id: str | None,
        principal: Principal,
        employee: EmployeeRow,
        model: str | None,
    ) -> str:
        """Use ACTIVE session_id if valid; ignore cancelled/closed leftovers from UI after pause."""
        if session_id:
            row = await self.get_session(session_id=session_id)
            if row.project_id != project_id:
                raise AppError(
                    code="NOT_FOUND",
                    title="Not Found",
                    status=404,
                    detail="Agent session not found",
                )
            if row.status == AgentSessionStatus.ACTIVE:
                return row.id
        return await self._resolve_active_session_id(
            project_id=project_id,
            principal=principal,
            employee=employee,
            model=model,
        )

    async def _resolve_active_session_id(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow,
        model: str | None,
    ) -> str:
        q = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
            .where(AgentSessionRow.status == AgentSessionStatus.ACTIVE)
            .order_by(AgentSessionRow.created_at.desc())
            .limit(1)
        )
        row = q.scalar_one_or_none()
        if row is not None:
            return row.id
        created = await self.create_session(
            project_id=project_id,
            principal=principal,
            employee=employee,
            model=model,
        )
        return str(created["id"])

    async def list_events(
        self,
        *,
        session_id: str,
        project_id: str,
        principal: Principal | None,
        employee: EmployeeRow | None,
        limit: int = 200,
        pod_agent: bool = False,
    ) -> list[dict]:
        if pod_agent:
            row = await self.get_session(session_id=session_id)
            if row.project_id != project_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        else:
            await self._projects.require_access(
                project_id=project_id, principal=principal, employee=employee, write=False
            )
            row = await self.get_session(session_id=session_id)
            if row.project_id != project_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        q = await self._session.execute(
            select(AgentEventRow)
            .where(AgentEventRow.session_id == session_id)
            .order_by(AgentEventRow.seq)
            .limit(limit)
        )
        return [_event_public(r) for r in q.scalars().all()]

    async def append_event(
        self,
        *,
        project_id: str,
        session_id: str,
        event_type: str,
        data: dict,
        at: str | None,
        principal: Principal | None,
        employee: EmployeeRow | None,
        pod_agent: bool = False,
    ) -> dict:
        """Append a single agent event (OpenClaw hybrid transcript write)."""
        if not pod_agent and employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        allowed_types = POD_AGENT_APPENDABLE_EVENT_TYPES if pod_agent else APPENDABLE_AGENT_EVENT_TYPES
        if event_type not in allowed_types:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"unsupported event type: {event_type}",
            )
        if not isinstance(data, dict):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="data must be an object",
            )

        if pod_agent:
            row = await self.get_session(session_id=session_id)
            if row.project_id != project_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        else:
            await self._projects.require_access(
                project_id=project_id, principal=principal, employee=employee, write=True
            )
            row = await self.get_session(session_id=session_id)
            if row.project_id != project_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        if row.status != AgentSessionStatus.ACTIVE:
            raise AppError(code="SESSION_CLOSED", title="Session closed", status=409, detail="session not active")

        parsed_at: datetime | None = None
        if at:
            try:
                parsed_at = datetime.fromisoformat(at.replace("Z", "+00:00"))
                if parsed_at.tzinfo is None:
                    parsed_at = parsed_at.replace(tzinfo=UTC)
            except ValueError:
                parsed_at = None

        seq_q = await self._session.execute(
            select(func.coalesce(func.max(AgentEventRow.seq), 0)).where(AgentEventRow.session_id == session_id)
        )
        seq = int(seq_q.scalar_one() or 0) + 1

        ev_row = AgentEventRow(
            session_id=session_id,
            seq=seq,
            event_type=event_type,
            payload=data,
            at=parsed_at,
        )
        self._session.add(ev_row)

        if event_type == AgentEventType.USAGE:
            self._session.add(
                AgentUsageRow(
                    session_id=session_id,
                    turn_id=f"turn_{seq}",
                    provider=str(data.get("provider") or row.provider),
                    model=data.get("model") or row.model,
                    input_tokens=data.get("input_tokens"),
                    output_tokens=data.get("output_tokens"),
                    cost_usd=Decimal(str(data["cost_usd"])) if data.get("cost_usd") is not None else None,
                )
            )

        await self._session.commit()
        return _event_public(ev_row)

    async def get_transcript(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        session_id: str | None = None,
        limit: int = 500,
    ) -> dict:
        """Chat bubbles for active (or given) session — L05 workspace reload."""
        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        sid = session_id
        if sid is None:
            q = await self._session.execute(
                select(AgentSessionRow)
                .where(AgentSessionRow.project_id == project_id)
                .where(AgentSessionRow.status == AgentSessionStatus.ACTIVE)
                .order_by(AgentSessionRow.created_at.desc())
                .limit(1)
            )
            active = q.scalar_one_or_none()
            if active is not None:
                sid = active.id
            else:
                # After pause auto-cancel, fall back to latest session so history remains.
                latest_q = await self._session.execute(
                    select(AgentSessionRow)
                    .where(AgentSessionRow.project_id == project_id)
                    .order_by(AgentSessionRow.created_at.desc())
                    .limit(1)
                )
                latest = latest_q.scalar_one_or_none()
                if latest is None:
                    return {"session_id": None, "messages": []}
                sid = latest.id
        else:
            row = await self.get_session(session_id=sid)
            if row.project_id != project_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")

        events = await self.list_events(
            session_id=sid,
            project_id=project_id,
            principal=principal,
            employee=employee,
            limit=limit,
        )
        row = await self.get_session(session_id=sid)
        return {
            "session_id": sid,
            "session_status": row.status,
            "messages": events_to_transcript(events),
        }

    async def cancel_session(
        self,
        *,
        project_id: str,
        session_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        """Cancel an agent session — allowed while paused (cleanup / stop runtime)."""
        await self._projects.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_paused=True,
        )
        row = await self.get_session(session_id=session_id)
        if row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        if row.status == AgentSessionStatus.CANCELLED:
            return _session_public(row)
        adapter = get_agent_adapter(api_kind=row.api_kind)
        handle = AgentHandle(id=row.vendor_agent_id, provider=row.provider, cwd=row.cwd, model=row.model)
        await adapter.cancel(handle)
        row.status = AgentSessionStatus.CANCELLED
        await self._session.commit()
        await self._session.refresh(row)
        return _session_public(row)

    async def cancel_active_for_project(self, *, project_id: str) -> int:
        """Best-effort cancel of ACTIVE sessions (project pause). Caller already authorized."""
        result = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
            .where(AgentSessionRow.status == AgentSessionStatus.ACTIVE)
        )
        return await self._cancel_session_rows(list(result.scalars().all()))

    async def reset_for_project(self, *, project_id: str) -> dict:
        """Stop agent sessions and purge chat history for a launched project."""
        from sqlalchemy import delete

        cancelled = await self.cancel_active_for_project(project_id=project_id)
        result = await self._session.execute(
            select(AgentSessionRow).where(AgentSessionRow.project_id == project_id)
        )
        rows = list(result.scalars().all())
        session_ids = [row.id for row in rows]
        for row in rows:
            if row.status == AgentSessionStatus.ACTIVE:
                continue
            try:
                adapter = get_agent_adapter(api_kind=row.api_kind)
                handle = AgentHandle(
                    id=row.vendor_agent_id,
                    provider=row.provider,
                    cwd=row.cwd,
                    model=row.model,
                )
                await adapter.close(handle)
            except Exception:
                pass
        if session_ids:
            await self._session.execute(
                delete(AgentEventRow).where(AgentEventRow.session_id.in_(session_ids))
            )
            await self._session.execute(
                delete(AgentUsageRow).where(AgentUsageRow.session_id.in_(session_ids))
            )
            await self._session.execute(
                delete(AgentSessionRow).where(AgentSessionRow.id.in_(session_ids))
            )
        return {"project_id": project_id, "cancelled": cancelled, "sessions_cleared": len(session_ids)}

    async def cancel_active_for_company(self, *, company_id: str) -> int:
        """Best-effort cancel of ACTIVE sessions across company projects (subscription suspend)."""
        from prodavan.infrastructure.persistence.models.projects import ProjectRow

        result = await self._session.execute(
            select(AgentSessionRow)
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(ProjectRow.company_id == company_id)
            .where(AgentSessionRow.status == AgentSessionStatus.ACTIVE)
        )
        return await self._cancel_session_rows(list(result.scalars().all()))

    async def cancel_active_for_key(self, *, key_id: str) -> int:
        """Cancel ACTIVE sessions that resolved to this AI key (disable/delete cascade)."""
        result = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.resolved_key_id == key_id)
            .where(AgentSessionRow.status == AgentSessionStatus.ACTIVE)
        )
        return await self._cancel_session_rows(list(result.scalars().all()))

    async def _cancel_session_rows(self, rows: list[AgentSessionRow]) -> int:
        for row in rows:
            try:
                adapter = get_agent_adapter(api_kind=row.api_kind)
                handle = AgentHandle(
                    id=row.vendor_agent_id,
                    provider=row.provider,
                    cwd=row.cwd,
                    model=row.model,
                )
                await adapter.cancel(handle)
            except Exception:
                # Lifecycle stop must succeed even if vendor cancel fails.
                pass
            row.status = AgentSessionStatus.CANCELLED
        return len(rows)

    async def list_pending_approvals(
        self,
        *,
        project_id: str,
        session_id: str,
        principal: Principal | None,
        employee: EmployeeRow | None,
        pod_agent: bool = False,
    ) -> list[dict]:
        events = await self.list_events(
            session_id=session_id,
            project_id=project_id,
            principal=principal,
            employee=employee,
            limit=500,
            pod_agent=pod_agent,
        )
        return _pending_approvals_from_events(events)

    async def resolve_tool_approval(
        self,
        *,
        project_id: str,
        session_id: str,
        approval_id: str,
        decision: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        if decision not in {"approve", "deny"}:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="decision must be approve or deny",
            )
        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        row = await self.get_session(session_id=session_id)
        if row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        if row.status != AgentSessionStatus.ACTIVE:
            raise AppError(code="SESSION_CLOSED", title="Session closed", status=409, detail="session not active")

        events = await self.list_events(
            session_id=session_id,
            project_id=project_id,
            principal=principal,
            employee=employee,
            limit=500,
        )
        pending = _pending_approvals_from_events(events)
        match = next((p for p in pending if p["id"] == approval_id), None)
        if match is None:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail="pending tool approval not found",
            )

        seq_q = await self._session.execute(
            select(func.coalesce(func.max(AgentEventRow.seq), 0)).where(AgentEventRow.session_id == session_id)
        )
        seq = int(seq_q.scalar_one() or 0)
        out_events: list[dict] = []

        def _append(event_type: str, payload: dict) -> None:
            nonlocal seq
            seq += 1
            self._session.add(
                AgentEventRow(
                    session_id=session_id,
                    seq=seq,
                    event_type=event_type,
                    payload=payload,
                    at=None,
                )
            )
            out_events.append({"type": event_type, "data": payload})

        _append(
            PLATFORM_EVENT_TOOL_APPROVAL_DECISION,
            {"id": approval_id, "decision": decision, "name": match["name"]},
        )

        if decision == "approve":
            _append(
                AgentEventType.TOOL_RESULT,
                {
                    "id": approval_id,
                    "name": match["name"],
                    "output": {"ok": True, "approved": True},
                    "is_error": False,
                },
            )
            _append(
                AgentEventType.TEXT_DELTA,
                {"text": f"Approved {match['name']} and continued."},
            )
        else:
            _append(
                AgentEventType.TOOL_RESULT,
                {
                    "id": approval_id,
                    "name": match["name"],
                    "output": {"ok": False, "approved": False},
                    "is_error": True,
                },
            )
            _append(
                AgentEventType.TEXT_DELTA,
                {"text": f"Denied {match['name']}."},
            )

        _append(AgentEventType.DONE, {"reason": f"approval_{decision}"})
        await self._session.commit()
        return {
            "session_id": session_id,
            "approval_id": approval_id,
            "decision": decision,
            "events": out_events,
            "assistant_text": _assistant_text_from_events(out_events),
        }


def _pending_approvals_from_events(events: list[dict]) -> list[dict]:
    """tool_approval_request without later tool_result / decision for the same id."""
    resolved: set[str] = set()
    for event in events:
        etype = event.get("type")
        payload = event.get("payload") if "payload" in event else event.get("data") or {}
        if not isinstance(payload, dict):
            continue
        eid = str(payload.get("id") or "")
        if not eid:
            continue
        if etype in {AgentEventType.TOOL_RESULT, PLATFORM_EVENT_TOOL_APPROVAL_DECISION}:
            resolved.add(eid)

    pending: list[dict] = []
    for event in events:
        etype = event.get("type")
        if etype != AgentEventType.TOOL_APPROVAL_REQUEST:
            continue
        payload = event.get("payload") if "payload" in event else event.get("data") or {}
        if not isinstance(payload, dict):
            continue
        eid = str(payload.get("id") or "")
        if not eid or eid in resolved:
            continue
        pending.append(
            {
                "id": eid,
                "name": str(payload.get("name") or "tool"),
                "input": payload.get("input") or {},
            }
        )
    return pending

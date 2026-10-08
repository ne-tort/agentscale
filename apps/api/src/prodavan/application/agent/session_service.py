"""Agent session lifecycle + event persistence (L08)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.admin.subscription_gate import CompanySubscriptionGate
from prodavan.application.agent.adapter_registry import get_agent_adapter
from prodavan.application.agent.budget_service import AgentBudgetService
from prodavan.application.agent.chat_projection import (
    assistant_text_from_events,
    events_to_chat_blocks,
    events_to_transcript,
)
from prodavan.application.agent.conversation_rehydrate import (
    format_rehydrate_bridge_message,
    needs_conversation_rehydrate,
    stamp_hydrate_generation,
)
from prodavan.application.agent.credential_broker import AgentCredentialBroker
from prodavan.application.agent.detached_turn import DetachedTurn, detached_turns
from prodavan.application.agent.openclaw_bridge import (
    BridgeSessionBootstrap,
    OpenClawBridgeBootstrap,
    api_kind_to_bridge_adapter,
)
from prodavan.application.agent.policy_service import AgentPolicyService
from prodavan.application.agent.runtime_guard import require_running_pod_runtime
from prodavan.application.agent.runtime_model import sanitize_runtime_model, sdk_fallback_model
from prodavan.application.agent.runtime_transport import resolve_runtime_endpoint
from prodavan.application.agent.stream_normalizer import TurnStreamNormalizer
from prodavan.application.agent.token_normalizer import (
    TokenNormalizer,
    extract_usage,
    normalize_usage_event,
)
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.application.ai_models.resolution import AiModelResolutionService
from prodavan.application.project_service import ProjectAccessPolicy
from prodavan.application.projects.attachment_delivery import AttachmentDeliveryService
from prodavan.application.projects.attachment_service import ProjectAttachmentService
from prodavan.application.projects.workspace_checkpoint import checkpoint_project_workspace
from prodavan.config.settings import settings
from prodavan.domain.agent import (
    FROZEN_EVENT_TYPES,
    PLATFORM_EVENT_TOOL_APPROVAL_DECISION,
    PLATFORM_EVENT_USER_MESSAGE,
    PLATFORM_STREAM_EVENT_TYPES,
    AgentEvent,
    AgentEventType,
    AgentHandle,
    AgentSessionStatus,
    ChatMessage,
)
from prodavan.domain.agent.errors import agent_runtime_unavailable, app_error_from_bridge_event
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import CHAT_MAX_ATTACHMENTS_PER_MESSAGE, CHAT_MAX_MESSAGE_CHARS
from prodavan.domain.projects.chat_error_policy import policy_to_send_fields
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow, AgentUsageRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import ModuleInstanceDataRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter


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
        "title": row.title,
        "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "agent_tokens_used": 0,
        "agent_requests": 0,
        "agent_messages": 0,
    }


async def _session_usage_maps(
    session,
    session_ids: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    """SQL baseline: tokens from agent_usage, requests from user_message events."""
    tokens_by: dict[str, int] = {sid: 0 for sid in session_ids}
    req_by: dict[str, int] = {sid: 0 for sid in session_ids}
    if not session_ids:
        return tokens_by, req_by
    usage_q = await session.execute(
        select(
            AgentUsageRow.session_id,
            func.coalesce(func.sum(AgentUsageRow.input_tokens), 0),
            func.coalesce(func.sum(AgentUsageRow.output_tokens), 0),
        )
        .where(AgentUsageRow.session_id.in_(session_ids))
        .group_by(AgentUsageRow.session_id)
    )
    for sid, inp, out in usage_q.all():
        tokens_by[str(sid)] = int(inp or 0) + int(out or 0)
    msg_q = await session.execute(
        select(AgentEventRow.session_id, func.count())
        .where(
            AgentEventRow.session_id.in_(session_ids),
            AgentEventRow.event_type == "user_message",
        )
        .group_by(AgentEventRow.session_id)
    )
    for sid, cnt in msg_q.all():
        req_by[str(sid)] = int(cnt or 0)
    return tokens_by, req_by


async def _overlay_session_metrics(item: dict, *, session_id: str) -> dict:
    from prodavan.application.metrics.overview_merge import overlay_store_counters
    from prodavan.domain.metrics.types import ENTITY_SESSION

    return await overlay_store_counters(
        item, entity_type=ENTITY_SESSION, entity_id=session_id
    )


def _default_chat_title(text: str) -> str:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return "Диалог"
    if len(cleaned) <= 60:
        return cleaned
    return cleaned[:57].rstrip() + "…"


def _dialog_title_for_ordinal(ordinal: int) -> str:
    """First chat on a project is «Диалог»; later ones «Диалог {n}» (1-based ordinal)."""
    if ordinal <= 1:
        return "Диалог"
    return f"Диалог {ordinal}"


def _touch_session_activity(row: AgentSessionRow, *, text: str | None = None) -> None:
    row.last_message_at = datetime.now(tz=UTC)
    # Do not overwrite explicit create titles («Диалог» / «Диалог N»).
    if text and not row.title:
        row.title = _default_chat_title(text)


_VISION_ERROR_MARKERS = ("image", "vision", "multimodal", "modalit", "content part")


def _rewrite_vision_error_event(event: AgentEvent) -> AgentEvent:
    """Provider rejected image content blocks (non-vision model) → friendly note.

    The original provider text is kept truncated: the file copy lives in the
    workspace, so the user can re-send without images or switch to a
    vision-capable model.
    """
    data = event.data if isinstance(event.data, dict) else {}
    message = str(data.get("message") or data.get("detail") or "")
    lowered = message.lower()
    if not any(marker in lowered for marker in _VISION_ERROR_MARKERS):
        return event
    detail = message.strip()
    if len(detail) > 240:
        detail = f"{detail[:240]}…"
    hint = (
        "Модель не поддерживает изображения. "
        "Картинки сохранены в контейнере проекта (inbox); "
        "отправьте сообщение без картинок или выберите vision-модель."
    )
    new_data = dict(data)
    # Our own classification (never a reconnect-retryable code); the
    # original provider code is preserved for diagnostics.
    if data.get("code"):
        new_data["provider_code"] = str(data["code"])
    new_data["code"] = "MODEL_NOT_VISION"
    new_data["message"] = f"{hint} Причина: {detail}" if detail else hint
    new_data["images_not_supported"] = True
    return AgentEvent.now(AgentEventType.ERROR, new_data)


def _event_public(row: AgentEventRow) -> dict:
    return {
        "seq": row.seq,
        "type": row.event_type,
        "data": row.payload,
        "at": row.at.isoformat() if row.at else None,
        # Row creation time — feeds chat block timestamps (created_at / turn_ms).
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


APPENDABLE_AGENT_EVENT_TYPES = frozenset(
    {
        PLATFORM_EVENT_USER_MESSAGE,
        *FROZEN_EVENT_TYPES,
        *PLATFORM_STREAM_EVENT_TYPES,
    }
)

POD_AGENT_APPENDABLE_EVENT_TYPES = frozenset({*FROZEN_EVENT_TYPES, *PLATFORM_STREAM_EVENT_TYPES})

# Mid-turn durability: commit user_message immediately; flush stream every N events
# or on block boundaries so API/pod crash does not lose the whole turn.
_STREAM_FLUSH_EVERY = 8
_STREAM_FLUSH_TYPES = frozenset(
    {
        AgentEventType.TOOL_RESULT,
        AgentEventType.TOOL_APPROVAL_REQUEST,
        AgentEventType.USAGE,
        AgentEventType.DONE,
        AgentEventType.ERROR,
        AgentEventType.SUBAGENT_STOP,
    }
)

# A turn is "done" once one of these lands in the transcript.
TERMINAL_EVENT_TYPES = frozenset({str(AgentEventType.DONE), str(AgentEventType.ERROR)})

# Detached-turn tailer poll interval (the run writes via its own session).
_TAIL_POLL_INTERVAL_SEC = 0.25

# How long a non-terminal tail still counts as "the agent is working". The
# stall watchdog (run_stall.STALL_AFTER = 7 min) writes a terminal marker, so
# staying generous avoids flapping between "working" and "idle" while it
# decides; beyond this window we treat a silent tail as a dead run.
_TURN_IN_PROGRESS_MAX_AGE = timedelta(minutes=15)


def _ensure_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _raise_if_agent_error_events(events: list[dict]) -> None:
    for event in reversed(events):
        if event.get("type") != AgentEventType.ERROR:
            continue
        data = event.get("data")
        if isinstance(data, dict):
            raise app_error_from_bridge_event(data)
        raise agent_runtime_unavailable()


class AgentSessionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectAccessPolicy(session)
        self._policy = AgentPolicyService(session)
        self._keys = AiKeysService(session)
        self._budget = AgentBudgetService(session)
        self._subscription = CompanySubscriptionGate(session)

    async def _flush_events(self) -> None:
        """Commit pending agent_events so mid-turn crashes keep transcript durable."""
        await self._session.commit()

    async def _maybe_flush_stream(self, *, since_flush: int, event_type: str) -> int:
        if since_flush >= _STREAM_FLUSH_EVERY or event_type in _STREAM_FLUSH_TYPES:
            await self._flush_events()
            return 0
        return since_flush

    def _persist_usage_event(
        self,
        *,
        data: dict,
        session_id: str,
        seq: int,
        employee_id: str | None,
        fallback_provider: str,
        fallback_model: str | None,
        token_normalizer: TokenNormalizer,
        project_id: str,
        company_id: str,
        cabinet_id: str,
    ) -> None:
        """Persist a normalized USAGE event to ``agent_usage`` + metrics.

        Single source of truth for the bridge and in-process adapter send
        paths and the bridge ``append_event`` path (audit CLAW-P1a
        copy-paste). Applies ``TokenNormalizer`` (CLAW-P0b): dedupe by
        ``message_id``, drop fully-zero trailing usage, persist cache
        tokens + ``token_source``.
        """
        normalized = token_normalizer.normalize_usage(data)
        if normalized is None:
            # Duplicate message_id or fully-zero trailing usage — skip.
            return
        self._session.add(
            AgentUsageRow(
                session_id=session_id,
                employee_id=employee_id,
                turn_id=f"turn_{seq}",
                provider=normalized.provider or fallback_provider,
                model=normalized.model or fallback_model,
                input_tokens=normalized.input_tokens,
                output_tokens=normalized.output_tokens,
                cache_creation_tokens=normalized.cache_creation_tokens,
                cache_read_tokens=normalized.cache_read_tokens,
                message_id=normalized.message_id,
                token_source=normalized.token_source,
                cost_usd=Decimal(str(normalized.cost_usd))
                if normalized.cost_usd is not None
                else None,
            )
        )
        from prodavan.application.metrics.publish import schedule_usage_turn

        schedule_usage_turn(
            self._session,
            project_id=project_id,
            company_id=company_id,
            cabinet_id=cabinet_id,
            employee_id=employee_id,
            session_id=session_id,
            input_tokens=normalized.input_tokens,
            output_tokens=normalized.output_tokens,
            provider=normalized.provider or fallback_provider,
            model=normalized.model or fallback_model,
        )

    def _append_stream_event(
        self,
        *,
        event: AgentEvent,
        session_id: str,
        seq: int,
        employee_id: str | None,
        fallback_provider: str,
        fallback_model: str | None,
        stream_normalizer: TurnStreamNormalizer,
        token_normalizer: TokenNormalizer,
        project_id: str,
        company_id: str,
        cabinet_id: str,
    ) -> tuple[AgentEvent, int] | None:
        """Persist one stream event + (for USAGE) usage row, return wire event.

        Single send-path helper (audit CLAW-P1a copy-paste) shared by the
        bridge and in-process adapter ``_iter_send_events`` branches:

        * ``TurnStreamNormalizer`` projects cumulative text/thinking deltas
          to incremental wire chunks;
        * ``normalize_usage_event`` projects USAGE payload to canonical
          fields (cache_* tokens, ``token_source``) before the event is
          persisted to ``agent_events`` and yielded on the wire, so the
          transcript never carries vendor aliases like
          ``cache_creation_input_tokens`` (CLAW-P0b);
        * ``_persist_usage_event`` dedupes by ``message_id`` and persists
          the canonical usage row + metrics.

        Returns the ``(wire_event, next_seq)`` tuple, or ``None`` when the
        event produced no wire output (normalized away). ``seq`` is mutated
        by the caller; the helper receives the current ``seq`` and returns
        the incremented value to keep both branches identical.
        """
        normalized = stream_normalizer.normalize_event(event)
        if normalized is None:
            return None
        if normalized.type == AgentEventType.USAGE:
            projected = normalize_usage_event(normalized)
            if projected is not None:
                normalized = projected
            else:
                # Fully-zero usage: normalize_usage_event dropped it, but we
                # still persist the event to the transcript — canonicalize the
                # payload so agent_events never carries vendor aliases like
                # cache_creation_input_tokens (CLAW-P0b consistency).
                canonical_payload = extract_usage(normalized.data).to_payload()
                normalized = AgentEvent(
                    type=normalized.type, data=canonical_payload, at=normalized.at
                )
        next_seq = seq + 1
        self._session.add(
            AgentEventRow(
                session_id=session_id,
                seq=next_seq,
                event_type=normalized.type,
                payload=normalized.data,
                at=None,
            )
        )
        if normalized.type == AgentEventType.USAGE:
            self._persist_usage_event(
                data=normalized.data,
                session_id=session_id,
                seq=next_seq,
                employee_id=employee_id,
                fallback_provider=fallback_provider,
                fallback_model=fallback_model,
                token_normalizer=token_normalizer,
                project_id=project_id,
                company_id=company_id,
                cabinet_id=cabinet_id,
            )
        return normalized, next_seq

    async def _checkpoint_workspace_after_turn(self, *, project_id: str) -> None:
        # Throttle (B9): a checkpoint streams + uploads the whole workspace,
        # so one per project per interval is enough for the last-good tree.
        # The Redis NX guard IS the throttle window (left to expire, never
        # released); without Redis the checkpoint runs unthrottled (the
        # checkpoint itself is best-effort anyway).
        interval = int(settings.pod_workspace_checkpoint_interval_sec or 0)
        if interval > 0:
            from prodavan.core.infra.cache import acquire_lock, cache_key
            from prodavan.core.infra.redis_manager import get_redis_manager

            mgr = get_redis_manager()
            if mgr is not None and mgr.enabled:
                guard = await acquire_lock(
                    cache_key("ws-checkpoint", project_id),
                    ttl_sec=interval,
                )
                if guard is None:
                    return
        await checkpoint_project_workspace(self._session, project_id=project_id, best_effort=True)

    async def _project_hydrate_generation(self, project_id: str) -> int | None:
        from prodavan.infrastructure.persistence.models.projects import ProjectPodRow

        # Projects legitimately carry several pod rows (terminated/failed
        # history + the live one, e.g. after a failed launch relaunch).
        # The generation the bridge compares against belongs to the LATEST
        # pod — a bare scalar_one_or_none() 500s on any history.
        q = await self._session.execute(
            select(ProjectPodRow.hydrate_generation)
            .where(ProjectPodRow.project_id == project_id)
            .order_by(ProjectPodRow.created_at.desc(), ProjectPodRow.id.desc())
            .limit(1)
        )
        value = None
        if hasattr(q, "scalar_one_or_none"):
            value = q.scalar_one_or_none()
        elif hasattr(q, "scalar_one"):
            # Unit mocks often stub only scalar_one.
            value = q.scalar_one()
        if value is None:
            return None
        return int(value)

    async def _session_transcript_bubbles(self, session_id: str) -> list[dict]:
        q = await self._session.execute(
            select(AgentEventRow)
            .where(AgentEventRow.session_id == session_id)
            .order_by(AgentEventRow.seq.asc())
        )
        rows: list[Any] = []
        if hasattr(q, "scalars"):
            rows = list(q.scalars().all())
        events = [
            {
                "type": getattr(row, "event_type", None),
                "data": row.payload if isinstance(getattr(row, "payload", None), dict) else {},
                "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
            }
            for row in rows
        ]
        return events_to_transcript(events)

    async def create_session(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        model: str | None = None,
        title: str | None = None,
    ) -> dict:
        """Start an agent session — blocked while project is paused (runtime)."""
        project = await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        await require_running_pod_runtime(
            self._session,
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
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
        existing_q = await self._session.execute(
            select(func.count())
            .select_from(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
        )
        existing_count = int(existing_q.scalar_one() or 0)
        cleaned_title = (title or "").strip()
        default_title = (
            cleaned_title[:200] if cleaned_title else _dialog_title_for_ordinal(existing_count + 1)
        )
        vendor_agent_id: str
        model_name = sanitize_runtime_model(opts.model)
        if settings.pod_agent_runtime_enabled:
            row = AgentSessionRow(
                project_id=project_id,
                resolved_key_id=credential.key_id,
                provider=credential.provider,
                api_kind=credential.api_kind,
                vendor_agent_id="pending",
                model=model_name,
                cwd=cwd,
                status=AgentSessionStatus.ACTIVE,
                title=default_title,
            )
            self._session.add(row)
            await self._session.flush()
            row.vendor_agent_id = row.id
            vendor_agent_id = row.id
        else:
            adapter = get_agent_adapter(api_kind=credential.api_kind)
            handle = await adapter.create(opts)
            vendor_agent_id = handle.id
            model_name = handle.model
            row = AgentSessionRow(
                project_id=project_id,
                resolved_key_id=credential.key_id,
                provider=credential.provider,
                api_kind=credential.api_kind,
                vendor_agent_id=vendor_agent_id,
                model=model_name,
                cwd=cwd,
                status=AgentSessionStatus.ACTIVE,
                title=default_title,
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
        await AgentCredentialBroker(self._session).push_lease_to_runtime(
            project_id=project_id,
            key_id=credential.key_id,
            principal=principal,
        )
        return _session_public(row)

    async def get_session(self, *, session_id: str) -> AgentSessionRow:
        row = await self._session.get(AgentSessionRow, session_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        return row

    async def _resolve_send_model(
        self,
        *,
        project: ProjectRow,
        row: AgentSessionRow,
        model: str | None,
    ) -> str | None:
        explicit = sanitize_runtime_model(model)
        if explicit is not None:
            validated = await self._policy.validate_send_model(
                project=project,
                credential_api_kind=row.api_kind,
                model=explicit,
            )
            row.model = validated
            return validated

        stored = sanitize_runtime_model(row.model)
        if stored is not None:
            row.model = stored
            return stored

        fallback = sdk_fallback_model(row.api_kind)
        if fallback is None:
            live_default = await self._resolve_live_default_model(project=project, row=row)
            if live_default is not None:
                validated = await self._policy.validate_send_model(
                    project=project,
                    credential_api_kind=row.api_kind,
                    model=live_default,
                )
                row.model = validated
                return validated
            return None
        validated = await self._policy.validate_send_model(
            project=project,
            credential_api_kind=row.api_kind,
            model=fallback,
        )
        row.model = validated
        return validated

    async def _resolve_live_default_model(
        self,
        *,
        project: ProjectRow,
        row: AgentSessionRow,
    ) -> str | None:
        key_id = row.resolved_key_id or getattr(project, "resolved_ai_key_id", None)
        if not key_id:
            return None
        return await AiModelResolutionService(self._session).resolve_send_default(
            company_id=project.company_id,
            key_id=str(key_id),
            project_id=project.id,
        )

    async def list_sessions(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        limit: int = 50,
    ) -> list[dict]:
        from prodavan.infrastructure.persistence.models.agent import EmployeeChatPinRow

        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        q = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
            .order_by(AgentSessionRow.created_at.desc())
            .limit(limit)
        )
        rows = list(q.scalars().all())
        pinned_ids: set[str] = set()
        if employee is not None and rows:
            pin_q = await self._session.execute(
                select(EmployeeChatPinRow.session_id).where(
                    EmployeeChatPinRow.employee_id == employee.id,
                    EmployeeChatPinRow.session_id.in_([r.id for r in rows]),
                )
            )
            pinned_ids = {sid for sid in pin_q.scalars().all()}
        tokens_by, req_by = await _session_usage_maps(self._session, [r.id for r in rows])
        out: list[dict] = []
        for r in rows:
            item = _session_public(r)
            item["pinned"] = r.id in pinned_ids
            item["agent_tokens_used"] = tokens_by.get(r.id, 0)
            item["agent_requests"] = req_by.get(r.id, 0)
            item["agent_messages"] = item["agent_requests"]
            item = await _overlay_session_metrics(item, session_id=r.id)
            out.append(item)
        return out

    async def get_session_public(
        self,
        *,
        project_id: str,
        session_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        from prodavan.infrastructure.persistence.models.agent import EmployeeChatPinRow

        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        row = await self.get_session(session_id=session_id)
        if row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        tokens_by, req_by = await _session_usage_maps(self._session, [row.id])
        item = _session_public(row)
        item["agent_tokens_used"] = tokens_by.get(row.id, 0)
        item["agent_requests"] = req_by.get(row.id, 0)
        item["agent_messages"] = item["agent_requests"]
        if employee is not None:
            pin_q = await self._session.execute(
                select(EmployeeChatPinRow.session_id).where(
                    EmployeeChatPinRow.employee_id == employee.id,
                    EmployeeChatPinRow.session_id == row.id,
                )
            )
            item["pinned"] = pin_q.scalar_one_or_none() is not None
        else:
            item["pinned"] = False
        return await _overlay_session_metrics(item, session_id=row.id)

    async def patch_session(
        self,
        *,
        project_id: str,
        session_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        title: str | None = None,
        pin: bool | None = None,
    ) -> dict:
        from prodavan.application.agent.chat_sidebar_service import ChatSidebarService

        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        row = await self.get_session(session_id=session_id)
        if row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        if title is not None:
            cleaned = title.strip()
            row.title = cleaned[:200] if cleaned else None
            await self._session.commit()
            await self._session.refresh(row)
        pinned = None
        if pin is not None:
            if employee is None:
                raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
            result = await ChatSidebarService(self._session).set_pin(
                session_id=session_id,
                pinned=pin,
                principal=principal,
                employee=employee,
            )
            pinned = result["pinned"]
        out = _session_public(row)
        if pinned is not None:
            out["pinned"] = pinned
        return out

    async def send_message(
        self,
        *,
        project_id: str,
        session_id: str,
        text: str,
        attachment_refs: list[str] | None,
        principal: Principal,
        employee: EmployeeRow | None,
        model: str | None = None,
        stall_retry: bool = False,
    ) -> dict:
        events_out: list[dict] = []
        async for event in self._iter_send_events(
            project_id=project_id,
            session_id=session_id,
            text=text,
            attachment_refs=attachment_refs,
            principal=principal,
            employee=employee,
            model=model,
            stall_retry=stall_retry,
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
        model: str | None = None,
        stall_retry: bool = False,
    ) -> AsyncIterator[dict]:
        project = await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        await self._subscription.require_active(project.company_id)
        runtime = await require_running_pod_runtime(
            self._session,
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=True,
        )
        runtime_ref = str(runtime.get("k8s_pod_name") or runtime.get("runtime_ref") or "").strip() or None
        row = await self.get_session(session_id=session_id)
        if row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        if row.status != AgentSessionStatus.ACTIVE:
            raise AppError(code="SESSION_CLOSED", title="Session closed", status=409, detail="session not active")

        send_model = await self._resolve_send_model(project=project, row=row, model=model)

        company_policy = await AdminCompanyService(self._session).get_agent_policy(project.company_id)
        await self._budget.enforce_before_turn(
            company_id=project.company_id,
            session_id=session_id,
            policy=company_policy,
        )

        handle = AgentHandle(id=row.vendor_agent_id, provider=row.provider, cwd=row.cwd, model=send_model)
        adapter = None
        if not settings.pod_agent_runtime_enabled:
            adapter = get_agent_adapter(api_kind=row.api_kind)

        user_text = (text or "").strip()
        if len(user_text) > CHAT_MAX_MESSAGE_CHARS:
            raise AppError(
                code="MESSAGE_TOO_LONG",
                title="Message too long",
                status=413,
                detail=f"max {CHAT_MAX_MESSAGE_CHARS} characters",
            )
        raw_refs = list(attachment_refs or ())
        if len(raw_refs) > CHAT_MAX_ATTACHMENTS_PER_MESSAGE:
            raise AppError(
                code="TOO_MANY_ATTACHMENTS",
                title="Too many attachments",
                status=422,
                detail=f"max {CHAT_MAX_ATTACHMENTS_PER_MESSAGE} attachments per message",
            )
        normalized_refs = await ProjectAttachmentService(self._session).normalize_refs(
            project_id=project_id,
            refs=raw_refs,
        )
        refs = tuple(normalized_refs)

        delivery = None
        bridge_images: list[dict] = []
        if refs:
            delivery = await AttachmentDeliveryService(self._session).deliver_for_send(
                project_id=project_id,
                workspace_key=project.workspace_key,
                storage_refs=list(refs),
                user_text=user_text,
                runtime_ref=runtime_ref,
                principal=principal,
                employee=employee,
            )
            agent_text = delivery.agent_message
            display_text = delivery.display_text
            # Vision images ride with the send body (bridge schema caps
            # them); non-vision providers reject the request — the error
            # mapping below turns that into a friendly chat message.
            bridge_images = [dict(img) for img in delivery.images]
        else:
            agent_text = user_text
            display_text = user_text

        message = ChatMessage(text=agent_text, attachment_refs=refs)

        hydrate_gen = await self._project_hydrate_generation(project_id)
        bridge_message = agent_text
        if settings.pod_agent_runtime_enabled and needs_conversation_rehydrate(
            row.adapter_state if isinstance(row.adapter_state, dict) else None,
            current_generation=hydrate_gen,
        ):
            prior = await self._session_transcript_bubbles(session_id)
            rebuilt = format_rehydrate_bridge_message(history=prior, new_message=agent_text)
            if rebuilt:
                bridge_message = rebuilt

        seq_q = await self._session.execute(
            select(func.coalesce(func.max(AgentEventRow.seq), 0)).where(AgentEventRow.session_id == session_id)
        )
        seq = int(seq_q.scalar_one() or 0)

        seq += 1
        user_payload: dict = {"text": display_text}
        if stall_retry:
            # повтор run-stall сторожа: UI не дублирует пузырь пользователя,
            # а сторож считает такие сообщения частью цепочки ретраев
            user_payload["stall_retry"] = True
        if refs:
            user_payload["attachment_refs"] = list(refs)
        if delivery is not None:
            user_payload["attachments"] = delivery.ui_attachments()
        if employee is not None:
            user_payload["employee_id"] = employee.id
        self._session.add(
            AgentEventRow(
                session_id=session_id,
                seq=seq,
                event_type=PLATFORM_EVENT_USER_MESSAGE,
                payload=user_payload,
                at=None,
            )
        )
        _touch_session_activity(row, text=display_text)
        if employee is not None:
            from prodavan.application.agent.composer_draft_service import ComposerDraftService

            await ComposerDraftService(self._session).clear_session_draft_silent(
                employee_id=employee.id,
                session_id=session_id,
            )
        yield {"type": PLATFORM_EVENT_USER_MESSAGE, "data": user_payload}

        from prodavan.application.metrics.publish import schedule_agent_request

        schedule_agent_request(
            self._session,
            project_id=project_id,
            company_id=project.company_id,
            cabinet_id=project.cabinet_id,
            employee_id=employee.id if employee else None,
            session_id=session_id,
        )
        # Durable user turn before vendor stream (crash mid-turn keeps the prompt).
        await self._flush_events()
        since_flush = 0

        stream_normalizer = TurnStreamNormalizer()
        token_normalizer = TokenNormalizer()
        used_bridge = False
        turn_ok = False
        if settings.pod_agent_runtime_enabled:
            bridge = OpenClawBridgeBootstrap(self._session)
            # Resolve the runtime endpoint ONCE per message (B4): the guard
            # above already produced a fresh runtime_view, so reuse it — the
            # lease push and the send share the endpoint instead of paying
            # for two more runtime_view + claim-status round-trips. The
            # bridge still re-resolves on its own after a recoverable
            # 404/410 (sandbox re-adoption).
            runtime_endpoint = await resolve_runtime_endpoint(
                self._session,
                project_id,
                view=runtime,
            )
            if row.resolved_key_id:
                pushed = await AgentCredentialBroker(self._session).push_lease_to_runtime(
                    project_id=project_id,
                    key_id=row.resolved_key_id,
                    principal=principal,
                    endpoint=runtime_endpoint,
                )
                if not pushed:
                    raise agent_runtime_unavailable(
                        detail="failed to push AI key lease to pod agent-runtime",
                    )

            async for event in bridge.iter_send_events(
                project_id=project_id,
                session_id=session_id,
                message=bridge_message,
                images=bridge_images or None,
                model=send_model,
                endpoint=runtime_endpoint,
                retry=policy_to_send_fields(project.chat_error_policy),
                bootstrap=BridgeSessionBootstrap(
                    session_id=row.id,
                    prodavan_session_id=row.id,
                    adapter_kind=api_kind_to_bridge_adapter(row.api_kind),
                    model=send_model,
                    provider_key_id=row.resolved_key_id,
                    adapter_state=row.adapter_state if isinstance(row.adapter_state, dict) else None,
                ),
            ):
                used_bridge = True
                if event.type == AgentEventType.ERROR and bridge_images:
                    event = _rewrite_vision_error_event(event)
                appended = self._append_stream_event(
                    event=event,
                    session_id=session_id,
                    seq=seq,
                    employee_id=employee.id if employee else None,
                    fallback_provider=row.provider,
                    fallback_model=row.model,
                    stream_normalizer=stream_normalizer,
                    token_normalizer=token_normalizer,
                    project_id=project_id,
                    company_id=project.company_id,
                    cabinet_id=project.cabinet_id,
                )
                if appended is None:
                    continue
                normalized, seq = appended
                yield normalized.to_dict()
                since_flush += 1
                if normalized.type in {AgentEventType.DONE, AgentEventType.ERROR}:
                    if normalized.type == AgentEventType.DONE:
                        state = await bridge.sync_adapter_state_for_session(
                            project_id=project_id,
                            session_id=session_id,
                        )
                        if isinstance(state, dict):
                            row.adapter_state = state
                        if hydrate_gen is not None:
                            row.adapter_state = stamp_hydrate_generation(
                                row.adapter_state if isinstance(row.adapter_state, dict) else None,
                                hydrate_gen,
                            )
                        turn_ok = True
                    await self._flush_events()
                    if turn_ok:
                        await self._checkpoint_workspace_after_turn(project_id=project_id)
                    return
                since_flush = await self._maybe_flush_stream(
                    since_flush=since_flush, event_type=normalized.type
                )

            if used_bridge:
                state = await bridge.sync_adapter_state_for_session(
                    project_id=project_id,
                    session_id=session_id,
                )
                if isinstance(state, dict):
                    row.adapter_state = state
                if hydrate_gen is not None:
                    row.adapter_state = stamp_hydrate_generation(
                        row.adapter_state if isinstance(row.adapter_state, dict) else None,
                        hydrate_gen,
                    )
                await self._flush_events()
                await self._checkpoint_workspace_after_turn(project_id=project_id)
                return
            raise agent_runtime_unavailable(detail="pod agent-runtime did not respond")

        if adapter is None:
            raise agent_runtime_unavailable()
        async for event in adapter.send(handle, message):
            appended = self._append_stream_event(
                event=event,
                session_id=session_id,
                seq=seq,
                employee_id=employee.id if employee else None,
                fallback_provider=row.provider,
                fallback_model=row.model,
                stream_normalizer=stream_normalizer,
                token_normalizer=token_normalizer,
                project_id=project_id,
                company_id=project.company_id,
                cabinet_id=project.cabinet_id,
            )
            if appended is None:
                continue
            normalized, seq = appended
            yield normalized.to_dict()
            since_flush += 1
            if normalized.type == AgentEventType.DONE:
                turn_ok = True
            if normalized.type in {AgentEventType.DONE, AgentEventType.ERROR}:
                await self._flush_events()
                if turn_ok:
                    await self._checkpoint_workspace_after_turn(project_id=project_id)
                return
            since_flush = await self._maybe_flush_stream(
                since_flush=since_flush, event_type=normalized.type
            )
        await self._flush_events()
        await self._checkpoint_workspace_after_turn(project_id=project_id)

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
            model=model,
        )
        _raise_if_agent_error_events(result.get("events") or [])
        result["assistant_text"] = assistant_text_from_events(result.get("events") or [])
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
        turn_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """SSE for a chat turn: START a detached run, then TAIL its events.

        The turn runs autonomously (see `detached_turn`); this generator only
        forwards persisted events. If the client disconnects, the run keeps
        going — a reloaded page re-attaches via `after_seq` polling and sees
        the same events.
        """
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        sid = await self._resolve_sendable_session_id(
            project_id=project_id,
            session_id=session_id,
            principal=principal,
            employee=employee,
            model=model,
        )
        # Commit the resolved/created session NOW: the detached run opens its
        # own session and must see this row (and any reactivation).
        await self._session.commit()
        turn = await self.start_detached_turn(
            project_id=project_id,
            session_id=sid,
            text=text,
            attachment_refs=attachment_refs,
            principal=principal,
            employee=employee,
            model=model,
            turn_id=turn_id,
        )
        yield {"type": "_session", "data": {"session_id": sid, "turn_id": turn.turn_id}}
        async for event in self.iter_turn_tail(turn):
            yield event

    async def start_detached_turn(
        self,
        *,
        project_id: str,
        session_id: str,
        text: str,
        attachment_refs: list[str] | None,
        principal: Principal,
        employee: EmployeeRow | None,
        model: str | None,
        turn_id: str | None = None,
    ) -> DetachedTurn:
        """Start (or reuse) an autonomous turn for `session_id`."""
        seq_q = await self._session.execute(
            select(func.coalesce(func.max(AgentEventRow.seq), 0)).where(
                AgentEventRow.session_id == session_id
            )
        )
        start_seq = int(seq_q.scalar_one() or 0)
        return await detached_turns.start(
            turn_id=turn_id or uuid4().hex,
            session_id=session_id,
            project_id=project_id,
            start_seq=start_seq,
            employee_id=employee.id if employee else None,
            principal=principal,
            runner=lambda turn: self._detached_events(
                turn,
                text=text,
                attachment_refs=attachment_refs,
                model=model,
            ),
        )

    async def _detached_events(
        self,
        turn: DetachedTurn,
        *,
        text: str,
        attachment_refs: list[str] | None,
        model: str | None,
    ) -> None:
        """Run one turn to completion on its OWN DB session, then persist.

        Owns a fresh `AsyncSession` so the run is independent of any request
        (the request that started it, and its connection, may be gone). Every
        event is persisted (+ flushed) by `_iter_send_events`, so a client that
        re-attaches mid-turn reads the transcript and catches up.
        """
        from prodavan.infrastructure.persistence.database import get_session_factory

        factory = get_session_factory()
        collected: list[dict] = []
        async with factory() as run_session:
            run_service = AgentSessionService(run_session)
            employee = (
                await run_session.get(EmployeeRow, turn.employee_id)
                if turn.employee_id
                else None
            )
            try:
                async for event in run_service._iter_send_events(
                    project_id=turn.project_id,
                    session_id=turn.session_id,
                    text=text,
                    attachment_refs=attachment_refs,
                    principal=turn.principal,  # type: ignore[arg-type]
                    employee=employee,
                    model=model,
                ):
                    collected.append(event)
                await run_session.commit()
            except asyncio.CancelledError:
                await run_session.rollback()
                turn.error_code = "CANCELLED"
                turn.error_detail = "turn cancelled"
                raise
            except AppError as err:
                await run_session.rollback()
                turn.error_code = err.code
                turn.error_detail = err.detail
            except Exception:  # noqa: BLE001 — surface to the tailer, never crash
                await run_session.rollback()
                turn.error_code = turn.error_code or "INTERNAL"
                turn.error_detail = turn.error_detail or "agent turn failed"
        turn.drained_events = collected
        turn.assistant_text = assistant_text_from_events(collected)

    async def iter_turn_tail(self, turn: DetachedTurn) -> AsyncIterator[dict]:
        """Yield a detached turn's persisted events until it finishes.

        Polls `agent_events` past a moving cursor, so it works identically for
        the original SSE sender and a page that re-attached after a reload.
        """
        cursor = turn.start_seq
        while True:
            events, _meta = await self._list_events_page(
                session_id=turn.session_id,
                project_id=turn.project_id,
                principal=None,
                employee=None,
                limit=200,
                after_seq=cursor,
                skip_access=True,
                include_total_count=False,
            )
            for event in events:
                seq = event.get("seq")
                if isinstance(seq, int):
                    cursor = seq
                yield event
            if turn.finished and not events:
                break
            if not events:
                await asyncio.sleep(_TAIL_POLL_INTERVAL_SEC)
        # The run died before writing anything (authz/budget/hard failure):
        # surface a structured error so the client is not left guessing.
        if turn.error_code and not turn.drained_events:
            yield {
                "type": "error",
                "data": {"code": turn.error_code, "message": turn.error_detail or ""},
            }
        yield {
            "type": "_turn_complete",
            "data": {
                "session_id": turn.session_id,
                "turn_id": turn.turn_id,
                "assistant_text": turn.assistant_text,
                "pending_approvals": _pending_approvals_from_events(turn.drained_events),
                "cancelled": turn.cancel_requested,
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
        """Reuse ACTIVE session; reactivate SUSPENDED session from UI after pause/resume."""
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
            if row.status == AgentSessionStatus.SUSPENDED:
                self._reactivate_session_row(row)
                await self._session.flush()
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
        suspended_q = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
            .where(AgentSessionRow.status == AgentSessionStatus.SUSPENDED)
            .order_by(AgentSessionRow.created_at.desc())
            .limit(1)
        )
        suspended = suspended_q.scalar_one_or_none()
        if suspended is not None:
            self._reactivate_session_row(suspended)
            await self._session.flush()
            return suspended.id
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
        before_seq: int | None = None,
        tail: bool = False,
        pod_agent: bool = False,
    ) -> list[dict]:
        events, _meta = await self._list_events_page(
            session_id=session_id,
            project_id=project_id,
            principal=principal,
            employee=employee,
            limit=limit,
            before_seq=before_seq,
            tail=tail,
            pod_agent=pod_agent,
        )
        return events

    async def _list_events_page(
        self,
        *,
        session_id: str,
        project_id: str,
        principal: Principal | None,
        employee: EmployeeRow | None,
        limit: int = 500,
        before_seq: int | None = None,
        after_seq: int | None = None,
        tail: bool = False,
        pod_agent: bool = False,
        session_row: AgentSessionRow | None = None,
        skip_access: bool = False,
        include_total_count: bool = True,
    ) -> tuple[list[dict], dict]:
        if session_row is not None:
            row = session_row
            if row.project_id != project_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        elif pod_agent:
            row = await self.get_session(session_id=session_id)
            if row.project_id != project_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        else:
            if not skip_access:
                await self._projects.require_access(
                    project_id=project_id, principal=principal, employee=employee, write=False
                )
            row = await self.get_session(session_id=session_id)
            if row.project_id != project_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")

        base = select(AgentEventRow).where(AgentEventRow.session_id == session_id)
        fetch_limit = limit + 1

        if before_seq is not None:
            q = await self._session.execute(
                base.where(AgentEventRow.seq < before_seq)
                .order_by(AgentEventRow.seq.desc())
                .limit(fetch_limit)
            )
            rows = list(q.scalars().all())
            has_more = len(rows) > limit
            rows = rows[:limit]
            rows.reverse()
        elif tail:
            q = await self._session.execute(base.order_by(AgentEventRow.seq.desc()).limit(fetch_limit))
            rows = list(q.scalars().all())
            has_more = len(rows) > limit
            rows = rows[:limit]
            rows.reverse()
        elif after_seq is not None:
            # Incremental tail: only events strictly newer than the cursor,
            # ascending, so a client can poll for live updates after a reload
            # without re-fetching the whole transcript.
            q = await self._session.execute(
                base.where(AgentEventRow.seq > after_seq)
                .order_by(AgentEventRow.seq.asc())
                .limit(fetch_limit)
            )
            rows = list(q.scalars().all())
            has_more = len(rows) > limit
            rows = rows[:limit]
        else:
            q = await self._session.execute(base.order_by(AgentEventRow.seq.asc()).limit(fetch_limit))
            rows = list(q.scalars().all())
            has_more = len(rows) > limit
            rows = rows[:limit]

        events = [_event_public(r) for r in rows]
        meta: dict = {
            "oldest_seq": rows[0].seq if rows else None,
            "newest_seq": rows[-1].seq if rows else None,
            "has_more": has_more,
        }
        if include_total_count:
            count_q = await self._session.execute(
                select(func.count()).select_from(AgentEventRow).where(AgentEventRow.session_id == session_id)
            )
            meta["total_events"] = int(count_q.scalar_one() or 0)
        return events, meta

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

        if event_type == PLATFORM_EVENT_USER_MESSAGE:
            _touch_session_activity(row, text=str(data.get("text") or ""))
        elif event_type in {AgentEventType.TEXT_DELTA, AgentEventType.DONE, AgentEventType.TOOL_CALL}:
            _touch_session_activity(row)

        if event_type == AgentEventType.USAGE:
            emp_id = employee.id if employee else None
            if emp_id is None:
                raw_emp = data.get("employee_id")
                if isinstance(raw_emp, str) and raw_emp.strip():
                    emp_id = raw_emp.strip()
            project = await self._session.get(ProjectRow, row.project_id)
            if project is not None:
                self._persist_usage_event(
                    data=data,
                    session_id=session_id,
                    seq=seq,
                    employee_id=emp_id,
                    fallback_provider=row.provider,
                    fallback_model=row.model,
                    token_normalizer=TokenNormalizer(),
                    project_id=project.id,
                    company_id=project.company_id,
                    cabinet_id=project.cabinet_id,
                )

        await self._session.commit()
        return _event_public(ev_row)

    async def get_transcript(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        session_id: str,
        limit: int = 500,
        before_seq: int | None = None,
        after_seq: int | None = None,
    ) -> dict:
        """Chat bubbles for an explicit session — UI must pass session_id (multi-chat).

        ``after_seq`` — incremental mode: return only events newer than the
        cursor (for live tailing after a reload). ``turn_in_progress`` tells
        the client the agent is still working so it can show the indicator and
        keep polling instead of appearing idle.
        """
        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        if not session_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=400,
                detail="session_id is required",
            )
        sid = session_id
        session_row = await self.get_session(session_id=sid)
        if session_row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")

        turn_in_progress = await self.turn_in_progress(sid)

        if after_seq is not None:
            # Incremental poll: new blocks only, no transcript-wide scans.
            events, meta = await self._list_events_page(
                session_id=sid,
                project_id=project_id,
                principal=principal,
                employee=employee,
                limit=limit,
                after_seq=after_seq,
                tail=False,
                session_row=session_row,
                skip_access=True,
                include_total_count=False,
            )
            return {
                "session_id": sid,
                "session_status": session_row.status,
                "blocks": events_to_chat_blocks(events),
                "pending_approvals": [],
                "turn_in_progress": turn_in_progress,
                **meta,
            }

        events, meta = await self._list_events_page(
            session_id=sid,
            project_id=project_id,
            principal=principal,
            employee=employee,
            limit=limit,
            before_seq=before_seq,
            tail=before_seq is None,
            session_row=session_row,
            skip_access=True,
            include_total_count=before_seq is None,
        )

        pending_approvals: list[dict] = []
        if before_seq is None:
            pending_approvals = _pending_approvals_from_events(events)
            if meta.get("has_more"):
                scan_events, _ = await self._list_events_page(
                    session_id=sid,
                    project_id=project_id,
                    principal=principal,
                    employee=employee,
                    limit=500,
                    tail=True,
                    session_row=session_row,
                    skip_access=True,
                    include_total_count=False,
                )
                pending_approvals = _pending_approvals_from_events(scan_events)

        return {
            "session_id": sid,
            "session_status": session_row.status,
            "blocks": events_to_chat_blocks(events),
            "pending_approvals": pending_approvals,
            "turn_in_progress": turn_in_progress,
            **meta,
        }

    async def turn_in_progress(self, session_id: str) -> bool:
        """Whether a turn is still running for this session.

        Authoritative source is the in-process detached-turn registry (there is
        exactly one API replica). After an API restart the registry is empty,
        so we fall back to the event tail: newest event is not terminal
        (``done``/``error``) and not older than the recency window.
        """
        if detached_turns.is_working(session_id):
            return True
        q = await self._session.execute(
            select(AgentEventRow.event_type, AgentEventRow.created_at)
            .where(AgentEventRow.session_id == session_id)
            .order_by(AgentEventRow.seq.desc())
            .limit(1)
        )
        row = q.first()
        if row is None:
            return False
        event_type, created_at = row
        if event_type in TERMINAL_EVENT_TYPES:
            return False
        if created_at is not None:
            age = datetime.now(UTC) - _ensure_utc(created_at)
            if age > _TURN_IN_PROGRESS_MAX_AGE:
                return False
        return True

    async def delete_session(
        self,
        *,
        project_id: str,
        session_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        """Cancel runtime (best-effort) and hard-delete session + cascade history/pins."""
        from sqlalchemy import delete

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

        if row.status == AgentSessionStatus.ACTIVE:
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
                pass
        else:
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

        # Chat-scoped module rows (request_lines / found_offers / budget_lines …,
        # scope.chats=current) must die with their chat — otherwise the deleted
        # chat's data outlives it and can leak into other sessions' views.
        # Rows with NULL session_id (chats=all tables: catalogs, sellers …)
        # are never matched and stay intact.
        # Указатели «активный чат» сотрудников на удаляемую сессию — обнуляем,
        # иначе клиенты восстановят мёртвый чат при следующем входе.
        from prodavan.application.agent.chat_sidebar_service import (
            clear_chat_selection_refs,
        )

        await clear_chat_selection_refs(self._session, session_id)
        module_rows = await self._session.execute(
            delete(ModuleInstanceDataRow).where(ModuleInstanceDataRow.session_id == session_id)
        )
        await self._session.execute(delete(AgentEventRow).where(AgentEventRow.session_id == session_id))
        await self._session.execute(delete(AgentUsageRow).where(AgentUsageRow.session_id == session_id))
        await self._session.execute(delete(AgentSessionRow).where(AgentSessionRow.id == session_id))
        await self._session.commit()
        return {
            "ok": True,
            "session_id": session_id,
            "project_id": project_id,
            "module_rows_deleted": int(module_rows.rowcount or 0),
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
        # Stop any detached run first: cancelling its task closes the runtime
        # stream (the runtime aborts the run) and marks it cancelled.
        detached_turns.request_cancel(session_id)
        if row.status == AgentSessionStatus.CANCELLED:
            return _session_public(row)
        if settings.pod_agent_runtime_enabled and not settings.agent_inprocess_adapters_enabled:
            # Pod-runtime sessions have no in-process adapter to cancel - the
            # running turn streams into a cancelled session and stops on its
            # own. Marking CANCELLED is the user-visible "stop" semantics.
            row.status = AgentSessionStatus.CANCELLED
        else:
            adapter = get_agent_adapter(api_kind=row.api_kind)
            handle = AgentHandle(id=row.vendor_agent_id, provider=row.provider, cwd=row.cwd, model=row.model)
            await adapter.cancel(handle)
            row.status = AgentSessionStatus.CANCELLED
        await self._session.commit()
        await self._session.refresh(row)
        return _session_public(row)

    async def cancel_active_for_project(self, *, project_id: str) -> int:
        """Best-effort cancel of ACTIVE sessions (reset prelude / hard stop). Caller owns commit."""
        result = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
            .where(AgentSessionRow.status == AgentSessionStatus.ACTIVE)
        )
        return await self._cancel_session_rows(list(result.scalars().all()))

    async def cancel_resumable_for_project(self, *, project_id: str) -> int:
        """Cancel ACTIVE and SUSPENDED sessions (delete with wipe / purge). Caller owns commit."""
        result = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
            .where(
                AgentSessionRow.status.in_(
                    (AgentSessionStatus.ACTIVE, AgentSessionStatus.SUSPENDED)
                )
            )
        )
        return await self._cancel_session_rows(list(result.scalars().all()))

    async def suspend_active_for_project(self, *, project_id: str) -> int:
        """Suspend ACTIVE sessions before project pod teardown (recoverable on resume)."""
        result = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
            .where(AgentSessionRow.status == AgentSessionStatus.ACTIVE)
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = AgentSessionStatus.SUSPENDED
        return len(rows)

    async def reactivate_resumable_for_project(self, *, project_id: str) -> list[str]:
        """Reactivate SUSPENDED sessions after project pod is back."""
        result = await self._session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.project_id == project_id)
            .where(AgentSessionRow.status == AgentSessionStatus.SUSPENDED)
            .order_by(AgentSessionRow.created_at.asc())
        )
        rows = list(result.scalars().all())
        for row in rows:
            self._reactivate_session_row(row)
        return [row.id for row in rows]

    @staticmethod
    def _reactivate_session_row(row: AgentSessionRow) -> None:
        row.status = AgentSessionStatus.ACTIVE

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
            tail=True,
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
            tail=True,
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

        forwarded = False
        if settings.pod_agent_runtime_enabled:
            forwarded = await OpenClawBridgeBootstrap(self._session).resolve_approval(
                project_id=project_id,
                approval_id=approval_id,
                decision=decision,
            )

        if not forwarded:
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
            "assistant_text": assistant_text_from_events(out_events),
        }

    async def fork_session(
        self,
        *,
        project_id: str,
        session_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        source = await self.get_session(session_id=session_id)
        if source.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        if source.status != AgentSessionStatus.ACTIVE:
            raise AppError(code="SESSION_CLOSED", title="Session closed", status=409, detail="session not active")

        row = AgentSessionRow(
            project_id=project_id,
            resolved_key_id=source.resolved_key_id,
            provider=source.provider,
            api_kind=source.api_kind,
            vendor_agent_id=source.vendor_agent_id,
            model=source.model,
            cwd=source.cwd,
            status=AgentSessionStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)

        await OpenClawBridgeBootstrap(self._session).fork_session(
            project_id=project_id,
            source_session_id=session_id,
            new_session_id=row.id,
        )
        await OpenClawBridgeBootstrap(self._session).register_session(
            project_id=project_id,
            payload=BridgeSessionBootstrap(
                session_id=row.id,
                prodavan_session_id=row.id,
                adapter_kind=api_kind_to_bridge_adapter(source.api_kind),
                model=row.model,
                provider_key_id=source.resolved_key_id,
            ),
        )
        if source.resolved_key_id:
            await AgentCredentialBroker(self._session).push_lease_to_runtime(
                project_id=project_id,
                key_id=source.resolved_key_id,
                principal=principal,
            )
        return _session_public(row)

    async def get_sidechain_transcript(
        self,
        *,
        project_id: str,
        session_id: str,
        tool_use_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await self._projects.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        row = await self.get_session(session_id=session_id)
        if row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        body = await OpenClawBridgeBootstrap(self._session).get_sidechain_transcript(
            project_id=project_id,
            session_id=session_id,
            tool_use_id=tool_use_id,
        )
        if body is None:
            raise AppError(
                code="RUNTIME_UNAVAILABLE",
                title="Service Unavailable",
                status=503,
                detail="sidechain transcript unavailable",
            )
        return body


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

"""Tenant Infra Events — publish/poll log with backlog + retention (no broker DSN to Pod)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_identity.bridge import SCOPE_INFRA_EVENTS, PodBridgeClaims
from prodavan.application.tenant_infra.publish import emit_tenant_infra_event
from prodavan.application.tenant_infra.quota import TenantInfraQuotaService, enforce_ops_rate, quota_exceeded
from prodavan.core.events.envelope import EventEnvelope
from prodavan.core.infra.cache import cache_get, cache_set
from prodavan.domain.errors import AppError

_MEMORY_LOGS: dict[str, list[dict[str, Any]]] = {}


def _log_key(company_id: str, project_id: str) -> str:
    return f"tenant:{company_id}:proj:{project_id}:__events"


def _offset_key(company_id: str, project_id: str, consumer: str) -> str:
    return f"tenant:{company_id}:proj:{project_id}:__events_off:{consumer}"


class TenantEventsService:
    def _require(self, bridge: PodBridgeClaims, project_id: str) -> None:
        bridge.require_project(project_id)
        bridge.require_scope(SCOPE_INFRA_EVENTS)

    async def _quota(self, bridge: PodBridgeClaims, session: AsyncSession | None):
        return await TenantInfraQuotaService(session).get_quota(bridge.company_id)

    async def _load_log(self, key: str) -> list[dict[str, Any]]:
        raw = await cache_get(key)
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return list(_MEMORY_LOGS.get(key) or [])

    async def _save_log(self, key: str, items: list[dict[str, Any]], retention_sec: int) -> None:
        _MEMORY_LOGS[key] = items
        await cache_set(key, json.dumps(items, ensure_ascii=False, default=str), ttl_sec=max(60, retention_sec))

    async def publish(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        typ: str,
        payload: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="kafka",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.kafka_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        body = payload or {}
        raw = json.dumps(body, ensure_ascii=False, default=str)
        if len(raw.encode("utf-8")) > quota.kafka_max_payload_bytes:
            raise quota_exceeded(f"event payload exceeds {quota.kafka_max_payload_bytes} bytes")
        key = _log_key(bridge.company_id, bridge.project_id)
        items = await self._load_log(key)
        now = time.time()
        cutoff = now - float(quota.kafka_retention_sec)
        items = [i for i in items if float(i.get("ts") or 0) >= cutoff]
        if len(items) >= quota.kafka_max_backlog:
            raise quota_exceeded(f"event backlog max {quota.kafka_max_backlog} exceeded")
        event = {
            "event_id": f"tev_{uuid.uuid4().hex[:16]}",
            "type": (typ or "message").strip()[:128] or "message",
            "ts": now,
            "company_id": bridge.company_id,
            "project_id": bridge.project_id,
            "pod_id": bridge.pod_id,
            "payload": body,
        }
        items.append(event)
        await self._save_log(key, items, quota.kafka_retention_sec)
        # Best-effort mirror onto platform Kafka bus when enabled.
        try:
            from prodavan.core.infra.kafka_manager import get_kafka_manager_optional

            mgr = get_kafka_manager_optional()
            if mgr is not None:
                await mgr.publish(
                    EventEnvelope(
                        bus="tenant",
                        event_id=event["event_id"],
                        event_type=str(event["type"]),
                        occurred_at=EventEnvelope.now_iso(),
                        company_id=bridge.company_id,
                        project_id=bridge.project_id,
                        cabinet_id=bridge.cabinet_id,
                        payload=body,
                    )
                )
        except Exception:
            pass
        await emit_tenant_infra_event(
            session=session,
            event_type="tenant_infra.op",
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            payload={"op": "events.publish", "type": event["type"]},
        )
        return {"event_id": event["event_id"], "ok": True}

    async def poll(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        consumer: str = "default",
        limit: int = 50,
        commit: bool = True,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="kafka",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.kafka_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        key = _log_key(bridge.company_id, bridge.project_id)
        off_key = _offset_key(bridge.company_id, bridge.project_id, (consumer or "default")[:64])
        items = await self._load_log(key)
        now = time.time()
        cutoff = now - float(quota.kafka_retention_sec)
        items = [i for i in items if float(i.get("ts") or 0) >= cutoff]
        await self._save_log(key, items, quota.kafka_retention_sec)
        raw_off = await cache_get(off_key)
        try:
            offset = int(raw_off or "0")
        except ValueError:
            offset = 0
        batch = items[offset : offset + min(max(1, limit), 200)]
        next_offset = offset + len(batch)
        if commit and batch:
            await cache_set(off_key, str(next_offset), ttl_sec=max(60, quota.kafka_retention_sec))
        return {
            "consumer": consumer or "default",
            "offset": offset,
            "next_offset": next_offset,
            "items": batch,
            "backlog": max(0, len(items) - next_offset),
        }

    async def purge_project(self, *, company_id: str, project_id: str) -> int:
        key = _log_key(company_id, project_id)
        _MEMORY_LOGS.pop(key, None)
        from prodavan.core.infra.cache import cache_delete

        await cache_delete(key)
        # Best-effort wipe common consumer offsets
        await cache_delete(_offset_key(company_id, project_id, "default"))
        return 1

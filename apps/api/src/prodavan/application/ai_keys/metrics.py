"""AI keys metrics events — Kafka-first facts for the metrics module (PROBE-P1).

Publishes events to the `metrics` bus so a future metrics read-model can
aggregate key lifecycle facts. Events are published even if no consumer is
listening right now (dual-write / PG outbox pattern).

Event types:
- `ai_key.probe`        — a probe was performed (status ok/error/unavailable, latency, models)
- `ai_key.created`       — key created (provider, api_kind, owner_scope, company_id, subscription meta)
- `ai_key.updated`       — key fields updated
- `ai_key.disabled`      — key disabled (by user or lazy expiry)
- `ai_key.renewed`       — key subscription renewed (months, next_renewal_at)
- `ai_key.rotated`       — key secret rotated
- `ai_key.deleted`       — key deleted
- `ai_key.bound`         — key bound to a company (relation grant)
- `ai_key.unbound`       — key unbound from a company (relation revoke)
- `ai_key.scope_bound`   — key bound to employee/cabinet/project (relation grant)
- `ai_key.scope_unbound` — key unbound from employee/cabinet/project (relation revoke)
- `ai_key.provider_meta` — provider/type metadata changed (provider, api_kind, subscription dates)

This module is the only place that knows ai_key.* metric event shapes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.ai_keys import ProbeResult, ProbeStatus

logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _publish(
    session: AsyncSession,
    *,
    event_type: str,
    key_id: str | None,
    company_id: str | None = None,
    payload: dict[str, Any],
) -> None:
    """Schedule a metrics event for Kafka dual-write after commit."""
    try:
        from prodavan.core.events.deferred import schedule_metrics_event_publish

        schedule_metrics_event_publish(
            session,
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            company_id=company_id,
            payload={
                "key_id": key_id,
                "occurred_at": _ts(),
                **payload,
            },
        )
    except Exception:
        logger.exception("ai_keys metrics: schedule %s failed key=%s", event_type, key_id)


def schedule_key_probe_event(
    session: AsyncSession,
    *,
    key_id: str,
    provider: str | None,
    api_kind: str | None,
    result: ProbeResult,
    company_id: str | None = None,
) -> None:
    """ai_key.probe — one verification attempt."""
    status = result.status.value if isinstance(result.status, ProbeStatus) else str(result.status)
    kind = result.kind.value if result.kind else None
    _publish(
        session,
        event_type="ai_key.probe",
        key_id=key_id,
        company_id=company_id,
        payload={
            "probe_status": status,
            "probe_kind": kind,
            "latency_ms": result.latency_ms,
            "http_status": result.http_status,
            "error_code": result.error_code,
            "models_count": len(result.models),
            "default_model": result.default_model,
            "provider": provider,
            "api_kind": api_kind,
        },
    )


def schedule_key_created_event(
    session: AsyncSession,
    *,
    key_id: str,
    name: str,
    provider: str,
    api_kind: str,
    owner_scope: str,
    owner_company_id: str | None = None,
    company_ids: list[str] | None = None,
    next_renewal_at: str | None = None,
    renewal_price: str | None = None,
    currency: str | None = None,
) -> None:
    """ai_key.created — key created."""
    _publish(
        session,
        event_type="ai_key.created",
        key_id=key_id,
        company_id=owner_company_id,
        payload={
            "name": name,
            "provider": provider,
            "api_kind": api_kind,
            "owner_scope": owner_scope,
            "owner_company_id": owner_company_id,
            "company_ids": list(company_ids or []),
            "next_renewal_at": next_renewal_at,
            "renewal_price": renewal_price,
            "currency": currency,
            "subscription_expires_at": next_renewal_at,
        },
    )


def schedule_key_updated_event(
    session: AsyncSession,
    *,
    key_id: str,
    fields: list[str],
    status: str | None = None,
    company_id: str | None = None,
    next_renewal_at: str | None = None,
    renewal_price: str | None = None,
    currency: str | None = None,
) -> None:
    """ai_key.updated — key fields patched."""
    _publish(
        session,
        event_type="ai_key.updated",
        key_id=key_id,
        company_id=company_id,
        payload={
            "fields": sorted(fields),
            "status": status,
            "next_renewal_at": next_renewal_at,
            "renewal_price": renewal_price,
            "currency": currency,
            "subscription_expires_at": next_renewal_at,
        },
    )


def schedule_key_disabled_event(
    session: AsyncSession,
    *,
    key_id: str,
    reason: str | None = None,
    company_id: str | None = None,
) -> None:
    """ai_key.disabled — key disabled (user or lazy expiry)."""
    _publish(
        session,
        event_type="ai_key.disabled",
        key_id=key_id,
        company_id=company_id,
        payload={"reason": reason or "manual"},
    )


def schedule_key_renewed_event(
    session: AsyncSession,
    *,
    key_id: str,
    months: int,
    next_renewal_at: str | None,
    company_id: str | None = None,
) -> None:
    """ai_key.renewed — subscription extended."""
    _publish(
        session,
        event_type="ai_key.renewed",
        key_id=key_id,
        company_id=company_id,
        payload={
            "months": months,
            "next_renewal_at": next_renewal_at,
            "subscription_expires_at": next_renewal_at,
        },
    )


def schedule_key_rotated_event(
    session: AsyncSession,
    *,
    key_id: str,
    company_id: str | None = None,
) -> None:
    """ai_key.rotated — secret rotated."""
    _publish(
        session,
        event_type="ai_key.rotated",
        key_id=key_id,
        company_id=company_id,
        payload={},
    )


def schedule_key_deleted_event(
    session: AsyncSession,
    *,
    key_id: str,
    name: str | None = None,
    company_id: str | None = None,
) -> None:
    """ai_key.deleted — key removed."""
    _publish(
        session,
        event_type="ai_key.deleted",
        key_id=key_id,
        company_id=company_id,
        payload={"name": name},
    )


def schedule_key_bound_event(
    session: AsyncSession,
    *,
    key_id: str,
    company_id: str,
) -> None:
    """ai_key.bound — key bound to a company (relation grant)."""
    _publish(
        session,
        event_type="ai_key.bound",
        key_id=key_id,
        company_id=company_id,
        payload={"relation": "company", "company_id": company_id},
    )


def schedule_key_unbound_event(
    session: AsyncSession,
    *,
    key_id: str,
    company_id: str,
) -> None:
    """ai_key.unbound — key unbound from a company (relation revoke)."""
    _publish(
        session,
        event_type="ai_key.unbound",
        key_id=key_id,
        company_id=company_id,
        payload={"relation": "company", "company_id": company_id},
    )


def schedule_key_scope_bound_event(
    session: AsyncSession,
    *,
    key_id: str,
    scope: str,
    scope_id: str,
    company_id: str | None = None,
) -> None:
    """ai_key.scope_bound — key bound to employee/cabinet/project."""
    _publish(
        session,
        event_type="ai_key.scope_bound",
        key_id=key_id,
        company_id=company_id,
        payload={"scope": scope, "scope_id": scope_id},
    )


def schedule_key_scope_unbound_event(
    session: AsyncSession,
    *,
    key_id: str,
    scope: str,
    scope_id: str,
    company_id: str | None = None,
) -> None:
    """ai_key.scope_unbound — key unbound from employee/cabinet/project."""
    _publish(
        session,
        event_type="ai_key.scope_unbound",
        key_id=key_id,
        company_id=company_id,
        payload={"scope": scope, "scope_id": scope_id},
    )


def schedule_key_provider_meta_event(
    session: AsyncSession,
    *,
    key_id: str,
    provider: str,
    api_kind: str,
    company_id: str | None = None,
    next_renewal_at: str | None = None,
    renewal_price: str | None = None,
    currency: str | None = None,
) -> None:
    """ai_key.provider_meta — provider/type metadata + subscription dates.

    Emitted on create/provider-change/renew/subscription-edit so the metrics
    module can aggregate subscription durations and provider distribution.
    """
    _publish(
        session,
        event_type="ai_key.provider_meta",
        key_id=key_id,
        company_id=company_id,
        payload={
            "provider": provider,
            "api_kind": api_kind,
            "next_renewal_at": next_renewal_at,
            "renewal_price": renewal_price,
            "currency": currency,
            "subscription_expires_at": next_renewal_at,
        },
    )

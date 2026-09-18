"""AI models metrics events — Kafka-first facts for the metrics module (MODELS-L1).

Published to the `metrics` bus so a future metrics read-model can aggregate
model lifecycle facts. Events fire even if no consumer listens right now
(dual-write / PG outbox pattern).

Event types:
- `ai_model.created` — model created (name, provider, key_aliases, owner_scope)
- `ai_model.updated` — model fields updated
- `ai_model.deleted` — model removed
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _publish(
    session: AsyncSession,
    *,
    event_type: str,
    model_id: str | None,
    payload: dict[str, Any],
    company_id: str | None = None,
) -> None:
    try:
        from prodavan.core.events.deferred import schedule_metrics_event_publish

        schedule_metrics_event_publish(
            session,
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            company_id=company_id,
            payload={
                "model_id": model_id,
                "occurred_at": _ts(),
                **payload,
            },
        )
    except Exception:
        logger.exception("ai_models metrics: schedule %s failed model=%s", event_type, model_id)


def schedule_model_created_event(
    session: AsyncSession,
    *,
    model_id: str,
    name: str,
    provider: str | None = None,
    key_aliases: list[str] | None = None,
    owner_scope: str = "platform",
    owner_company_id: str | None = None,
) -> None:
    _publish(
        session,
        event_type="ai_model.created",
        model_id=model_id,
        company_id=owner_company_id if owner_scope == "company" else None,
        payload={
            "name": name,
            "provider": provider,
            "key_aliases": list(key_aliases or []),
            "owner_scope": owner_scope,
            "owner_company_id": owner_company_id,
        },
    )


def schedule_model_updated_event(
    session: AsyncSession,
    *,
    model_id: str,
    name: str,
    provider: str | None = None,
    owner_scope: str = "platform",
    owner_company_id: str | None = None,
) -> None:
    _publish(
        session,
        event_type="ai_model.updated",
        model_id=model_id,
        company_id=owner_company_id if owner_scope == "company" else None,
        payload={
            "name": name,
            "provider": provider,
            "owner_scope": owner_scope,
            "owner_company_id": owner_company_id,
        },
    )


def schedule_model_deleted_event(
    session: AsyncSession,
    *,
    model_id: str,
    name: str | None = None,
    company_id: str | None = None,
) -> None:
    _publish(
        session,
        event_type="ai_model.deleted",
        model_id=model_id,
        company_id=company_id,
        payload={"name": name},
    )

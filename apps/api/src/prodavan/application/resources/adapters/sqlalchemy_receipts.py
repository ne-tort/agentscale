"""Durable materialization apply receipts.

A receipt is the only memory of a completed apply: it lets the router answer a
repeated ``Idempotency-Key`` with the original outcome instead of planning and
applying a second time, and it makes the same key with another request a
conflict.

Storage is ``platform_events``: the table already exists, its
``uq_platform_events_idempotency_key`` constraint gives a race-proof identity, and
the dynamic-app audit port uses the same mechanism. The receipt row carries
**only** identity, generations, outcome and digests — never a resource payload,
provider output or secret material.

The generation fence itself is *not* implemented here: it belongs to
``AtomicMaterializationApplyPort`` (durable attempt ledger, revision
``2026110306``). This adapter records what that boundary reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.resources.api.errors import StoreConflict
from prodavan.application.resources.api.ports import ApplyReceipt
from prodavan.infrastructure.persistence.models.platform_events import PlatformEventRow

EVENT_TYPE = "resource.materialization.applied"
_KEY_PREFIX = "resource-apply"
_KEY_LIMIT = 200


def receipt_key(*, tenant_id: str, project_id: str, idempotency_key: str) -> str:
    """Tenant-qualified durable identity of one apply attempt."""
    value = f"{_KEY_PREFIX}:{tenant_id}:{project_id}:{idempotency_key}"
    if len(value) > _KEY_LIMIT:
        raise StoreConflict("apply_receipt_key_too_long")
    return value


@dataclass(slots=True)
class SqlAlchemyApplyReceiptStore:
    """``ResourceApplyReceiptPort`` over ``platform_events``."""

    session: AsyncSession

    async def load_apply_receipt(
        self, *, tenant_id: str, project_id: str, idempotency_key: str
    ) -> ApplyReceipt | None:
        key = receipt_key(
            tenant_id=tenant_id, project_id=project_id, idempotency_key=idempotency_key
        )
        row = await self.session.scalar(
            select(PlatformEventRow).where(
                PlatformEventRow.idempotency_key == key,
                PlatformEventRow.event_type == EVENT_TYPE,
            )
        )
        if row is None:
            return None
        payload = dict(row.payload or {})
        if str(payload.get("tenant_id")) != tenant_id:
            # A global unique key collision must never leak another tenant's
            # receipt; the row is treated as absent and the insert will conflict.
            return None
        try:
            return ApplyReceipt(
                tenant_id=tenant_id,
                project_id=str(payload.get("project_id")),
                idempotency_key=str(payload.get("idempotency_key")),
                request_digest=str(payload.get("request_digest")),
                plan_id=str(payload.get("plan_id")),
                outcome=str(payload.get("outcome")),
                expected_generation=int(payload.get("expected_generation")),
                desired_generation=int(payload.get("desired_generation")),
                applied_generation=(
                    int(payload["applied_generation"])
                    if payload.get("applied_generation") is not None
                    else None
                ),
                blockers=tuple(payload.get("blockers") or ()),
            )
        except (TypeError, ValueError) as exc:
            raise StoreConflict("apply_receipt_corrupt") from exc

    async def save_apply_receipt(self, receipt: ApplyReceipt) -> None:
        key = receipt_key(
            tenant_id=receipt.tenant_id,
            project_id=receipt.project_id,
            idempotency_key=receipt.idempotency_key,
        )
        payload: dict[str, Any] = {
            "tenant_id": receipt.tenant_id,
            "project_id": receipt.project_id,
            "idempotency_key": receipt.idempotency_key,
            "request_digest": receipt.request_digest,
            "plan_id": receipt.plan_id,
            "outcome": receipt.outcome,
            "expected_generation": receipt.expected_generation,
            "desired_generation": receipt.desired_generation,
            "applied_generation": receipt.applied_generation,
            "blockers": list(receipt.blockers),
        }
        try:
            async with self.session.begin_nested():
                self.session.add(
                    PlatformEventRow(
                        event_type=EVENT_TYPE,
                        company_id=receipt.tenant_id,
                        project_id=receipt.project_id,
                        idempotency_key=key,
                        payload=payload,
                    )
                )
                await self.session.flush()
        except IntegrityError as exc:
            # A concurrent writer won the key. Only an identical body may be
            # accepted; anything else is a conflict and never an overwrite.
            stored = await self.load_apply_receipt(
                tenant_id=receipt.tenant_id,
                project_id=receipt.project_id,
                idempotency_key=receipt.idempotency_key,
            )
            if stored is not None and stored.request_digest == receipt.request_digest:
                return
            raise StoreConflict("apply_receipt_conflict") from exc
        await self.session.commit()


__all__ = ["EVENT_TYPE", "SqlAlchemyApplyReceiptStore", "receipt_key"]

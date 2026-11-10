"""Regression tests for the SQL slotted-grant egress mapping.

Written during the migration verification freeze; intentionally not executed.
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressMode,
    EgressPolicy,
    GrantStatus,
    QuotaSet,
)
from prodavan.application.plugins.capabilities.adapters.build import _SqlEgressPolicy
from prodavan.application.plugins.egress.errors import EgressPolicyError
from prodavan.application.plugins.egress.policy import EgressMode as ResolvedEgressMode
from prodavan.application.plugins.egress.service import EgressDecisionCode

NOW = datetime(2026, 1, 1, tzinfo=UTC)
GRANT_ID = "grant_slotted"
HOST = "hooks.example.com"
PATH = "/v1/hooks/orders"
DIGEST = "sha256:" + "a" * 64


def _grant(**changes: Any) -> CapabilityGrantRecord:
    values: dict[str, Any] = {
        "grant_id": GRANT_ID,
        "company_id": "company_1",
        "plugin_id": "plugin_1",
        "plugin_instance_id": "instance_1",
        "manifest_version": "1.2.3",
        "artifact_digest": DIGEST,
        "capabilities": ("network",),
        "egress_policy": EgressPolicy(
            mode=EgressMode.WEBHOOKS_ONLY,
            allowed_hosts=(HOST,),
            allowed_paths=("/v1/hooks/",),
        ),
        "quotas": QuotaSet(
            ops_per_minute=11,
            max_rows=12,
            max_bytes=13,
            max_keys=14,
            max_object_bytes=15,
            max_egress_bytes=16,
        ),
        "status": GrantStatus.ACTIVE,
        "created_at": NOW,
    }
    values.update(changes)
    return CapabilityGrantRecord(**values)


class _Store:
    def __init__(self, record: Any) -> None:
        self.record = record

    async def get_by_grant_id(self, *, grant_id: str) -> Any:
        return self.record


@pytest.mark.asyncio
async def test_slotted_grant_preserves_exact_egress_quotas_and_digest() -> None:
    record = _grant()
    assert not hasattr(record, "__dict__")

    mapped = _SqlEgressPolicy._as_mapping(record)
    assert mapped["artifact_digest"] == DIGEST
    assert mapped["quotas"] == dataclasses.asdict(record.quotas)
    assert mapped["egress_policy"] == dataclasses.asdict(record.egress_policy)
    assert mapped["egress"] == {
        "mode": "webhooks-only",
        "allowed_hosts": (HOST,),
        "allowed_paths": ("/v1/hooks/",),
        "approved": False,
        "max_egress_bytes": 16,
    }

    adapter = _SqlEgressPolicy(_Store(record))
    lookup = await adapter._lookup(GRANT_ID)
    resolved = adapter._resolver(lookup).resolve(GRANT_ID)
    assert resolved.mode is ResolvedEgressMode.WEBHOOKS_ONLY
    assert resolved.allowed_hosts == (HOST,)
    assert resolved.allowed_paths == ("/v1/hooks/",)
    assert resolved.max_egress_bytes == 16
    assert await adapter.allows(grant_id=GRANT_ID, host=HOST, port=443, path=PATH)

    claims = {"company_id": "company_1", "sub": "subject_1", "grant_id": GRANT_ID}
    allowed = await adapter.check(
        claims,
        HOST,
        PATH,
        "POST",
        16,
        resolved_host=HOST,
        hops=(),
        redirect_checked=True,
        tls_verified=True,
    )
    denied = await adapter.check(
        claims,
        HOST,
        PATH,
        "POST",
        17,
        resolved_host=HOST,
        hops=(),
        redirect_checked=True,
        tls_verified=True,
    )
    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.code is EgressDecisionCode.QUOTA_EXCEEDED


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", ("egress_policy", "quotas"))
async def test_missing_policy_or_quotas_fails_closed_without_defaults(field_name: str) -> None:
    record = _grant()
    object.__setattr__(record, field_name, None)
    adapter = _SqlEgressPolicy(_Store(record))

    with pytest.raises(EgressPolicyError) as caught:
        await adapter.allows(grant_id=GRANT_ID, host=HOST, port=443, path=PATH)
    assert caught.value.reason == "policy_invalid"


@pytest.mark.asyncio
async def test_foreign_object_with_dict_is_not_accepted_as_grant() -> None:
    valid = _grant()
    foreign = SimpleNamespace(
        grant_id=GRANT_ID,
        egress_policy=valid.egress_policy,
        quotas=valid.quotas,
        is_active=True,
        created_at=NOW,
    )
    adapter = _SqlEgressPolicy(_Store(foreign))

    with pytest.raises(EgressPolicyError) as caught:
        await adapter.allows(grant_id=GRANT_ID, host=HOST, port=443, path=PATH)
    assert caught.value.reason == "grant_unreadable"


@pytest.mark.asyncio
async def test_foreign_grant_id_and_missing_record_fail_closed() -> None:
    foreign = _SqlEgressPolicy(_Store(_grant(grant_id="grant_other")))
    with pytest.raises(LookupError, match="binding mismatch"):
        await foreign.allows(grant_id=GRANT_ID, host=HOST, port=443, path=PATH)

    missing = _SqlEgressPolicy(_Store(None))
    with pytest.raises(LookupError, match="not found"):
        await missing.allows(grant_id=GRANT_ID, host=HOST, port=443, path=PATH)

"""Async authoritative revocation regression tests (verification freeze)."""
from __future__ import annotations

from datetime import datetime

import pytest

from prodavan.application.plugins.identity.revocation import RevocationPort
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    SqlCapabilityGrantRepository,
)
from prodavan.infrastructure.persistence.repositories.plugins.revocation import (
    DbGrantRevocation,
)

from .conftest import NOW, FakeSession, grant_row

pytestmark = pytest.mark.asyncio


def _repo(*, rows: list | None = None) -> SqlCapabilityGrantRepository:
    return SqlCapabilityGrantRepository(session=FakeSession(rows=rows), now=lambda: NOW)


async def test_db_revocation_satisfies_async_identity_port() -> None:
    rev = DbGrantRevocation(_repo(), clock=lambda: NOW)
    assert isinstance(rev, RevocationPort)


async def test_every_activity_check_reads_db_and_cold_revoke_denies() -> None:
    row = grant_row(id="pcg_01", status="active")
    session = FakeSession(rows=[row])
    repo = SqlCapabilityGrantRepository(session=session, now=lambda: NOW)
    rev = DbGrantRevocation(repo, clock=lambda: NOW)

    assert await rev.is_active("pcg_01", now=NOW) is True
    assert await rev.is_active("pcg_01", now=NOW) is True
    assert len(session.statements) == 2

    row.status = "revoked"
    row.revoked_at = NOW
    assert await rev.is_active("pcg_01", now=NOW) is False
    assert len(session.statements) == 3
    assert rev.stats["reads"] == 3


async def test_revoke_async_performs_one_durable_action_without_sync_revoke() -> None:
    repo = _repo()
    durable_calls: list[tuple[str, str]] = []
    sync_calls: list[str] = []

    async def durable(grant_id: str, *, reason: str = "") -> bool:
        durable_calls.append((grant_id, reason))
        return True

    def forbidden_sync(grant_id: str) -> None:
        sync_calls.append(grant_id)
        raise AssertionError("wrapper must not issue a second sync revoke")

    repo.revoke_async = durable  # type: ignore[method-assign]
    repo.revoke = forbidden_sync  # type: ignore[method-assign]
    rev = DbGrantRevocation(repo, clock=lambda: NOW)

    assert await rev.revoke_async(grant_id="pcg_01", reason="test") is True
    assert durable_calls == [("pcg_01", "test")]
    assert sync_calls == []


async def test_check_uses_canonical_async_read() -> None:
    from prodavan.application.plugins.identity.types import PluginClaims, PluginTokenError

    row = grant_row(id="pcg_01", status="active")
    rev = DbGrantRevocation(_repo(rows=[row]), clock=lambda: NOW)
    claims = PluginClaims(grant_id="pcg_01")
    assert await rev.check(claims) is claims

    row.status = "revoked"
    row.revoked_at = NOW
    with pytest.raises(PluginTokenError):
        await rev.check(claims)


async def test_naive_datetime_and_unknown_grant_fail_closed() -> None:
    rev = DbGrantRevocation(_repo(), clock=lambda: NOW)
    assert await rev.is_active("pcg_01", now=datetime(2026, 1, 1)) is False
    assert await rev.is_active("unknown", now=NOW) is False
    assert await rev.is_revoked("unknown") is True

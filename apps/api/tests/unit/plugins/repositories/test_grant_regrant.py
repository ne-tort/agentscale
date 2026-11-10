"""Атомарный regrant revoked CapabilityGrantRow. Не запускать (freeze)."""

from __future__ import annotations

import pytest

from prodavan.infrastructure.persistence.models.plugin_platform import CapabilityGrantRow
from prodavan.infrastructure.persistence.repositories.plugins._schema import (
    PluginRepositoryUnavailable,
)
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    PluginRegrantConflict,
    SqlCapabilityGrantRepository,
)

from .conftest import (
    NOW,
    FakeResult,
    FakeSession,
    FakeSessionFactory,
    grant_record,
    grant_row,
)


async def test_regrant_replaces_only_revoked_row_with_new_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "prodavan.infrastructure.persistence.repositories.plugins.grants.load_row",
        lambda _name: CapabilityGrantRow,
    )
    session = FakeSession(rows=[grant_row(status="revoked", revoked_at=NOW)])
    original_execute = session.execute

    async def select_then_returning(statement, *args, **kwargs):
        result = await original_execute(statement, *args, **kwargs)
        if len(session.statements) == 2:
            return FakeResult(
                [grant_row(id="pcg_new", status="active", revoked_at=None)]
            )
        return result

    session.execute = select_then_returning  # type: ignore[method-assign]
    repository = SqlCapabilityGrantRepository(
        session_factory=FakeSessionFactory(session), now=lambda: NOW
    )
    candidate = grant_record(grant_id="pcg_new", status="active")

    stored = await repository.regrant(previous_grant_id="pcg_01", grant=candidate)

    assert stored.grant_id == "pcg_new"
    assert session.commits == 1
    assert repository.get_by_id("pcg_01") is None
    assert repository.get_by_id("pcg_new") == candidate


async def test_regrant_same_id_is_conflict_without_sql_or_resurrection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "prodavan.infrastructure.persistence.repositories.plugins.grants.load_row",
        lambda _name: CapabilityGrantRow,
    )
    revoked_row = grant_row(status="revoked", revoked_at=NOW)
    session = FakeSession(rows=[revoked_row])
    factory = FakeSessionFactory(session)
    repository = SqlCapabilityGrantRepository(
        session_factory=factory, now=lambda: NOW
    )

    with pytest.raises(PluginRegrantConflict) as caught:
        await repository.regrant(
            previous_grant_id="pcg_01",
            grant=grant_record(grant_id="pcg_01", status="active"),
        )

    assert caught.value.code == "REGRANT_ID_CONFLICT"
    assert factory.calls == 0, "same-id conflict обязан precede session/SQL boundary"
    assert session.statements == []
    assert session.added == []
    assert session.commits == 0
    assert session.rollbacks == 0
    assert revoked_row.status == "revoked"
    assert revoked_row.revoked_at == NOW
    assert repository.get_by_id("pcg_01") is None
    assert repository.is_active("pcg_01") is False


async def test_regrant_rolls_back_returned_old_pk_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "prodavan.infrastructure.persistence.repositories.plugins.grants.load_row",
        lambda _name: CapabilityGrantRow,
    )
    revoked_row = grant_row(status="revoked", revoked_at=NOW)
    session = FakeSession(rows=[revoked_row])
    original_execute = session.execute

    async def select_then_return_old_pk(statement, *args, **kwargs):
        result = await original_execute(statement, *args, **kwargs)
        if len(session.statements) == 2:
            return FakeResult(
                [grant_row(id="pcg_01", status="active", revoked_at=None)]
            )
        return result

    session.execute = select_then_return_old_pk  # type: ignore[method-assign]
    repository = SqlCapabilityGrantRepository(session=session, now=lambda: NOW)

    with pytest.raises(PluginRepositoryUnavailable, match="binding mismatch"):
        await repository.regrant(
            previous_grant_id="pcg_01",
            grant=grant_record(grant_id="pcg_new", status="active"),
        )

    assert session.commits == 0
    assert session.rollbacks == 1
    assert revoked_row.status == "revoked"
    assert revoked_row.revoked_at == NOW
    assert repository.get_by_id("pcg_01") is None
    assert repository.get_by_id("pcg_new") is None
    assert repository.is_active("pcg_01") is False


async def test_regrant_refuses_to_resurrect_active_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "prodavan.infrastructure.persistence.repositories.plugins.grants.load_row",
        lambda _name: CapabilityGrantRow,
    )
    repository = SqlCapabilityGrantRepository(
        session=FakeSession(rows=[grant_row(status="active")]), now=lambda: NOW
    )
    with pytest.raises(PluginRepositoryUnavailable, match="не REVOKED"):
        await repository.regrant(
            previous_grant_id="pcg_01",
            grant=grant_record(grant_id="pcg_new", status="active"),
        )

"""Unit contract tests for SQL reservation; intentionally not run in freeze."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from prodavan.application.plugins.sources.repository import SqlModuleSourceRepository
from prodavan.application.plugins.sources.types import (
    ModuleSourceProvisioningReservation,
    ModuleSourceReservationStatus,
)
from prodavan.infrastructure.persistence.models.plugin_platform import (
    PluginSourceReservationRow,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
WORKFLOW = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40


def _reservation() -> ModuleSourceProvisioningReservation:
    return ModuleSourceProvisioningReservation(
        id="psr_one",
        company_id="co_123",
        plugin_id="mod_equipment",
        full_name="managed-plugins/plugin-one",
        private=True,
        default_branch="main",
        installation_credential_ref="vault://github_app/co_123/installation",
        trusted_workflow_ref=WORKFLOW,
        attempt_id="attempt-one",
        created_at=NOW,
        updated_at=NOW,
    )


def _row() -> PluginSourceReservationRow:
    return PluginSourceReservationRow(
        id="psr_one",
        company_id="co_123",
        plugin_id="mod_equipment",
        provider="github_app",
        expected_full_name="managed-plugins/plugin-one",
        expected_private=True,
        expected_default_branch="main",
        installation_credential_ref="vault://github_app/co_123/installation",
        trusted_workflow_ref=WORKFLOW,
        attempt_id="attempt-one",
        create_in_progress=True,
        status=ModuleSourceReservationStatus.PROVISIONING.value,
        created_at=NOW,
        updated_at=NOW,
    )


class _Result:
    def __init__(self, *, scalar: str | None = None, rows: list[object] | None = None):
        self._scalar = scalar
        self._rows = rows

    def scalar_one_or_none(self) -> str | None:
        return self._scalar

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[object]:
        return [] if self._rows is None else self._rows


class _Session:
    def __init__(self, *, inserted: bool) -> None:
        self.statements: list[object] = []
        self.inserted = inserted
        self.row = _row()

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        if len(self.statements) == 1:
            return _Result(scalar=self.row.id if self.inserted else None)
        return _Result(rows=[self.row])

    async def get(self, model: object, source_id: str) -> object:
        assert model is PluginSourceReservationRow
        assert source_id == self.row.id
        return self.row


def _repository_with_session(
    monkeypatch: pytest.MonkeyPatch, session: _Session
) -> SqlModuleSourceRepository:
    repository = SqlModuleSourceRepository(session_factory=cast(Any, object()))

    @asynccontextmanager
    async def context():
        yield session

    monkeypatch.setattr(repository, "_reservation_session_ctx", context)
    return repository


async def test_reserve_uses_atomic_insert_and_returns_acquired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(inserted=True)
    repository = _repository_with_session(monkeypatch, session)

    result = await repository.reserve_provisioning(_reservation())

    assert result.acquired is True
    assert result.reservation.full_name == "managed-plugins/plugin-one"
    statement = str(session.statements[0])
    assert "ON CONFLICT DO NOTHING" in statement
    assert "RETURNING" in statement
    assert len(session.statements) == 1


async def test_reserve_conflict_returns_existing_without_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(inserted=False)
    repository = _repository_with_session(monkeypatch, session)

    result = await repository.reserve_provisioning(_reservation())

    assert result.acquired is False
    assert result.reservation.id == "psr_one"
    assert result.reservation.attempt_id == "attempt-one"
    assert len(session.statements) == 2
    assert "ON CONFLICT DO NOTHING" in str(session.statements[0])
    assert re.search(r"\bUPDATE\b", str(session.statements[0]).upper()) is None


class _OwnedSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _OwnedFactory:
    def __init__(self, session: _OwnedSession) -> None:
        self.session = session

    def __call__(self) -> "_OwnedFactory":
        return self

    async def __aenter__(self) -> _OwnedSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


async def test_owned_reservation_context_commits_and_rolls_back() -> None:
    session = _OwnedSession()
    repository = SqlModuleSourceRepository(
        session_factory=cast(Any, _OwnedFactory(session))
    )

    async with repository._reservation_session_ctx():
        pass
    assert session.commits == 1
    assert session.rollbacks == 0

    with pytest.raises(RuntimeError):
        async with repository._reservation_session_ctx():
            raise RuntimeError("abort before provider HTTP")
    assert session.commits == 1
    assert session.rollbacks == 1

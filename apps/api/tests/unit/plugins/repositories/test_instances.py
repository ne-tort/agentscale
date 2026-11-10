"""Async SQL PluginInstance repository regressions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.plugins.admin.contracts import (
    PluginInstanceRecord,
    PluginInstanceStatus,
)
from prodavan.infrastructure.persistence.models.plugin_platform import PluginInstanceRow
from prodavan.infrastructure.persistence.repositories.plugins.instances import (
    SqlPluginInstanceRepository,
)
from prodavan.infrastructure.persistence.repositories.plugins._schema import (
    PluginRepositoryUnavailable,
)

from .conftest import FakeSession, FakeSessionFactory

NOW = datetime(2026, 11, 9, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def record(**overrides) -> PluginInstanceRecord:
    values = dict(
        plugin_instance_id="pinst_01",
        company_id="co_a",
        plugin_id="mod_equipment",
        manifest_version="1.2.0",
        artifact_digest=DIGEST,
        status=PluginInstanceStatus.INSTALLED,
        tenant_db_name="tenant_co_a",
        bucket_name="tenant-co_a",
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(overrides)
    return PluginInstanceRecord(**values)


def row(**overrides) -> PluginInstanceRow:
    values = dict(
        id="pinst_01",
        company_id="co_a",
        plugin_id="mod_equipment",
        manifest_version="1.2.0",
        artifact_digest=DIGEST,
        status="installed",
        desired_replicas=0,
        last_active_at=None,
        active_job_ids=[],
        wake_in_flight=False,
        tenant_db_name="tenant_co_a",
        bucket_name="tenant-co_a",
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(overrides)
    return PluginInstanceRow(**values)


def repo(session: FakeSession, monkeypatch: pytest.MonkeyPatch, *, owned: bool = False):
    monkeypatch.setattr(
        "prodavan.infrastructure.persistence.repositories.plugins.instances.load_row",
        lambda _name: PluginInstanceRow,
    )
    if owned:
        return SqlPluginInstanceRepository(
            session_factory=FakeSessionFactory(session), now=lambda: NOW
        )
    return SqlPluginInstanceRepository(session=session, now=lambda: NOW)


async def test_create_uses_real_insert_on_conflict_and_commits_owned_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    repository = repo(session, monkeypatch, owned=True)
    created = await repository.create(instance=record())

    assert created.plugin_instance_id == "pinst_01"
    assert session.commits == 1
    assert any("ON CONFLICT DO NOTHING" in str(stmt) for stmt in session.statements)


async def test_create_idempotently_returns_existing_company_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(rows=[row()])
    repository = repo(session, monkeypatch)
    created = await repository.create(instance=record(plugin_instance_id="pinst_retry"))

    assert created.plugin_instance_id == "pinst_01"
    assert len(session.statements) == 1
    assert session.commits == 0


async def test_create_rejects_immutable_pin_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = repo(FakeSession(rows=[row()]), monkeypatch)
    with pytest.raises(PluginRepositoryUnavailable, match="immutable"):
        await repository.create(instance=record(manifest_version="2.0.0"))


async def test_get_by_company_and_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = repo(FakeSession(rows=[row()]), monkeypatch)
    by_pair = await repository.get(company_id="co_a", plugin_id="mod_equipment")
    by_id = await repository.get_by_id(plugin_instance_id="pinst_01")
    assert by_pair == by_id
    assert by_id is not None and by_id.tenant_db_name == "tenant_co_a"
    assert by_id.bucket_name == "tenant-co_a"
    assert await repository.resolve(company_id="co_a", plugin_id="mod_equipment") == "pinst_01"


async def test_update_status_requires_exact_sql_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(rows=[row()])
    repository = repo(session, monkeypatch)
    updated = await repository.update_status(
        plugin_instance_id="pinst_01", status=PluginInstanceStatus.ACTIVE
    )
    assert updated.status is PluginInstanceStatus.ACTIVE

    session._rowcount = 0
    with pytest.raises(PluginRepositoryUnavailable, match="ровно одну"):
        await repository.update_status(
            plugin_instance_id="pinst_01", status=PluginInstanceStatus.REVOKED
        )


async def test_delete_missing_is_idempotent_after_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    repository = repo(session, monkeypatch, owned=True)
    assert await repository.delete(company_id="co_a", plugin_id="mod_equipment") is True
    assert session.commits == 1

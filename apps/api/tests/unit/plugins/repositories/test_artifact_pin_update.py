"""Fenced artifact update repository tests."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from prodavan.application.plugins.admin.contracts import PluginInstanceStatus
from prodavan.infrastructure.persistence.models.plugin_platform import (
    CapabilityGrantRow,
    PluginInstanceRow,
)
from prodavan.infrastructure.persistence.repositories.plugins._schema import (
    GRANT_ROW,
    PluginRepositoryUnavailable,
)
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    SqlCapabilityGrantRepository,
)
from prodavan.infrastructure.persistence.repositories.plugins.instances import (
    SqlPluginInstanceRepository,
)

from .conftest import (
    DIGEST,
    NOW,
    OTHER_DIGEST,
    FakeResult,
    FakeSession,
    FakeSessionFactory,
    grant_row,
)

OLD_VERSION = "1.2.0"
NEW_VERSION = "1.3.0"


def instance_row(*, status: str = "active") -> PluginInstanceRow:
    return PluginInstanceRow(
        id="pinst_01",
        company_id="co_123",
        plugin_id="mod_equipment",
        manifest_version=OLD_VERSION,
        artifact_digest=DIGEST,
        status=status,
        desired_replicas=1,
        last_active_at=None,
        active_job_ids=[],
        wake_in_flight=False,
        tenant_db_name="tenant_co_123",
        bucket_name="tenant-co_123",
        created_at=NOW,
        updated_at=NOW,
    )


class InstanceCasSession(FakeSession):
    def __init__(self, current: PluginInstanceRow) -> None:
        super().__init__()
        self.current = current
        self.selects = 0
        self.updates = 0

    async def execute(self, statement, *args, **kwargs) -> FakeResult:
        self.statements.append(statement)
        if self.updates == 0 and self.selects == 0:
            self.selects += 1
            return FakeResult([self.current])
        self.updates += 1
        self.current.manifest_version = NEW_VERSION
        self.current.artifact_digest = OTHER_DIGEST
        self.current.status = "active"
        self.current.updated_at = NOW + timedelta(seconds=1)
        return FakeResult([self.current], rowcount=self._rowcount)


class AtomicCasSession(FakeSession):
    def __init__(self, grant, instance: PluginInstanceRow) -> None:
        super().__init__()
        self.grant = grant
        self.instance = instance
        self.calls = 0
        self.updates = 0
        self.fail_instance_return = False

    async def execute(self, statement, *args, **kwargs) -> FakeResult:
        self.statements.append(statement)
        self.calls += 1
        if self.calls == 1:
            return FakeResult([self.grant])
        if self.calls == 2:
            return FakeResult([self.instance])
        if self.calls == 3:
            self.updates += 1
            self.grant.manifest_version = NEW_VERSION
            self.grant.artifact_digest = OTHER_DIGEST
            return FakeResult([self.grant], rowcount=self._rowcount)
        if self.calls == 4:
            self.updates += 1
            if self.fail_instance_return:
                return FakeResult([], rowcount=0)
            self.instance.manifest_version = NEW_VERSION
            self.instance.artifact_digest = OTHER_DIGEST
            self.instance.status = "active"
            self.instance.updated_at = NOW + timedelta(seconds=1)
            return FakeResult([self.instance], rowcount=self._rowcount)
        raise AssertionError("unexpected SQL execute")


def atomic_repo(session: AtomicCasSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "prodavan.infrastructure.persistence.repositories.plugins.grants.load_row",
        lambda name: CapabilityGrantRow if name == GRANT_ROW else PluginInstanceRow,
    )
    return SqlCapabilityGrantRepository(
        session_factory=FakeSessionFactory(session), now=lambda: NOW
    )


def atomic_kwargs(**overrides):
    values = dict(
        grant_id="pcg_01",
        company_id="co_123",
        plugin_id="mod_equipment",
        plugin_instance_id="pinst_01",
        expected_manifest_version=OLD_VERSION,
        expected_artifact_digest=DIGEST,
        manifest_version=NEW_VERSION,
        artifact_digest=OTHER_DIGEST,
        expected_instance_status=PluginInstanceStatus.QUARANTINED,
        instance_status=PluginInstanceStatus.ACTIVE,
    )
    values.update(overrides)
    return values


async def test_instance_pin_cas_checks_old_fence_and_returning_post_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = instance_row()
    session = InstanceCasSession(current)
    monkeypatch.setattr(
        "prodavan.infrastructure.persistence.repositories.plugins.instances.load_row",
        lambda _name: PluginInstanceRow,
    )
    repository = SqlPluginInstanceRepository(session=session, now=lambda: NOW)

    updated = await repository.update_artifact_pin(
        company_id="co_123",
        plugin_id="mod_equipment",
        plugin_instance_id="pinst_01",
        expected_manifest_version=OLD_VERSION,
        expected_artifact_digest=DIGEST,
        manifest_version=NEW_VERSION,
        artifact_digest=OTHER_DIGEST,
        expected_status=PluginInstanceStatus.ACTIVE,
        status=PluginInstanceStatus.ACTIVE,
    )

    assert updated.manifest_version == NEW_VERSION
    assert updated.artifact_digest == OTHER_DIGEST
    assert current.desired_replicas == 1
    assert session.updates == 1


async def test_atomic_pin_cas_recovers_quarantined_instance_without_regrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AtomicCasSession(grant_row(), instance_row(status="quarantined"))
    repository = atomic_repo(session, monkeypatch)

    result = await repository.update_artifact_pin(**atomic_kwargs())

    assert result.grant.grant_id == "pcg_01"
    assert result.grant.manifest_version == NEW_VERSION
    assert result.grant.artifact_digest == OTHER_DIGEST
    assert result.grant.created_at == NOW
    assert result.grant.revoked_at is None
    assert result.instance.status is PluginInstanceStatus.ACTIVE
    assert result.instance.artifact_digest == OTHER_DIGEST
    assert session.updates == 2
    assert session.commits == 1
    assert session.rollbacks == 0
    assert sum(
        re.match(r"\s*UPDATE\b", str(statement).upper()) is not None
        for statement in session.statements
    ) == 2
    assert all("FOR UPDATE" in str(session.statements[index]) for index in (0, 1))


async def test_atomic_pin_cas_rejects_wrong_company_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AtomicCasSession(grant_row(), instance_row(status="quarantined"))
    repository = atomic_repo(session, monkeypatch)

    with pytest.raises(PluginRepositoryUnavailable, match="binding mismatch"):
        await repository.update_artifact_pin(**atomic_kwargs(company_id="co_other"))

    assert session.updates == 0
    assert session.commits == 0
    assert session.rollbacks == 1


async def test_atomic_pin_cas_rolls_back_when_second_returning_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AtomicCasSession(grant_row(), instance_row(status="quarantined"))
    session.fail_instance_return = True
    repository = atomic_repo(session, monkeypatch)

    with pytest.raises(PluginRepositoryUnavailable, match="instance UPDATE RETURNING"):
        await repository.update_artifact_pin(**atomic_kwargs())

    assert session.updates == 2
    assert session.commits == 0
    assert session.rollbacks == 1


async def test_atomic_pin_cas_new_post_state_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grant = grant_row(manifest_version=NEW_VERSION, artifact_digest=OTHER_DIGEST)
    instance = instance_row(status="active")
    instance.manifest_version = NEW_VERSION
    instance.artifact_digest = OTHER_DIGEST
    session = AtomicCasSession(grant, instance)
    repository = atomic_repo(session, monkeypatch)

    result = await repository.update_artifact_pin(
        **atomic_kwargs(expected_instance_status=PluginInstanceStatus.ACTIVE)
    )

    assert result.grant.artifact_digest == OTHER_DIGEST
    assert result.instance.artifact_digest == OTHER_DIGEST
    assert session.updates == 0
    assert session.commits == 0
    assert session.rollbacks == 0

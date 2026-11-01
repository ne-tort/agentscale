"""Source acceptance checks for the M05 trusted plugin-instance adapter."""

from datetime import datetime, timedelta, timezone

from prodavan.application.plugin_instances import (
    FencingToken,
    LeaseCasDecision,
    LeaseCasRequest,
    OwnerCasDecision,
    PluginInstanceDecisionReason,
    PluginInstanceIdentity,
    PluginInstanceWriteRequest,
    WriterCasRequest,
)
from prodavan.application.plugin_instances_adapter import TrustedPluginInstanceRepository, TrustedPluginInstanceService

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
DIGEST = "sha256:" + "a" * 64


def identity(**changes):
    values = {
        "plugin_instance_id": "instance-1",
        "definition_id": "definition-1",
        "version_id": "version-1",
        "tenant_id": "tenant-1",
        "scope_kind": "project",
        "scope_id": "project-1",
        "artifact_digest": DIGEST,
    }
    values.update(changes)
    return PluginInstanceIdentity(**values)


def request(**changes):
    values = {
        "identity": identity(),
        "subject_id": "subject-1",
        "session_id": "session-1",
        "fencing": FencingToken(3, 7),
        "lease_owner": "worker-1",
        "lease_id": "lease-1",
        "now": NOW,
    }
    values.update(changes)
    return PluginInstanceWriteRequest(**values)


class RuntimeSource:
    def __init__(self, row=None, lease_row=None, writer_row=None):
        self.row = row
        self.lease_row = lease_row
        self.writer_row = writer_row
        self.lookups = []
        self.lease_requests = []
        self.writer_requests = []

    async def read_exact(self, value):
        self.lookups.append(value)
        return self.row

    async def acquire_lease(self, value):
        self.lease_requests.append(value)
        return self.row if self.lease_row is None else self.lease_row

    async def advance_writer(self, value):
        self.writer_requests.append(value)
        return self.row if self.writer_row is None else self.writer_row


class Quarantine:
    def __init__(self):
        self.events = []

    async def quarantine(self, row, *, reason):
        self.events.append((row, reason))


def row(**changes):
    value = {
        "plugin_instance_id": "instance-1",
        "definition_id": "definition-1",
        "version_id": "version-1",
        "tenant_id": "tenant-1",
        "scope_kind": "project",
        "scope_id": "project-1",
        "artifact_digest": DIGEST,
        "subject_id": "subject-1",
        "session_id": "session-1",
        "state": "active",
        "runtime_generation": 3,
        "writer_generation": 7,
        "lease_owner": "worker-1",
        "lease_id": "lease-1",
        "lease_until": NOW + timedelta(minutes=5),
    }
    value.update(changes)
    return value


async def test_repository_maps_complete_mapping_row_and_exactly_checks_identity():
    source = RuntimeSource(row())
    repository = TrustedPluginInstanceRepository(source, source)
    result = await repository.get_exact(identity())
    assert result is not None
    assert result.identity == identity()
    assert result.fencing == FencingToken(3, 7)


async def test_incomplete_row_is_deny_only_and_quarantined():
    source = RuntimeSource(row(session_id=None))
    quarantine = Quarantine()
    repository = TrustedPluginInstanceRepository(source, source, quarantine)
    result = await repository.get_exact(identity())
    assert result is None
    assert quarantine.events and quarantine.events[0][0] == source.row
    assert quarantine.events[0][1].startswith("incomplete_runtime_instance:")


async def test_matching_aliases_are_accepted_but_disagreement_is_quarantined():
    accepted = row(app_definition_id="definition-1", app_version_id="version-1")
    source = RuntimeSource(accepted)
    repository = TrustedPluginInstanceRepository(source, source)
    assert (await repository.get_exact(identity())).identity == identity()

    quarantine = Quarantine()
    source = RuntimeSource(row(app_definition_id="definition-other"))
    repository = TrustedPluginInstanceRepository(source, source, quarantine)
    assert await repository.get_exact(identity()) is None
    assert quarantine.events[0][1].startswith("incomplete_runtime_instance:")


async def test_mismatched_row_is_quarantined_without_fallback():
    source = RuntimeSource(row(tenant_id="tenant-other"))
    quarantine = Quarantine()
    repository = TrustedPluginInstanceRepository(source, source, quarantine)
    assert await repository.get_exact(identity()) is None
    assert quarantine.events[-1][1] == "runtime_instance_identity_mismatch"


async def test_cas_success_requires_complete_same_instance_snapshot():
    source = RuntimeSource(row(), lease_row=row(lease_id="lease-2"), writer_row=row(writer_generation=8))
    repository = TrustedPluginInstanceRepository(source, source)
    lease = await repository.acquire_lease(
        LeaseCasRequest("instance-1", "worker-1", "lease-2", FencingToken(3, 7), NOW + timedelta(minutes=5))
    )
    assert lease.acquired
    writer = await repository.advance_writer(WriterCasRequest("instance-1", "worker-1", FencingToken(3, 7)))
    assert writer.changed


async def test_writer_cas_requires_same_runtime_and_exactly_next_writer_snapshot():
    quarantine = Quarantine()
    request_value = WriterCasRequest("instance-1", "worker-1", FencingToken(3, 7))
    for returned in (row(runtime_generation=4, writer_generation=8), row(writer_generation=9)):
        source = RuntimeSource(row(), writer_row=returned)
        repository = TrustedPluginInstanceRepository(source, source, quarantine)
        result = await repository.advance_writer(request_value)
        assert result == OwnerCasDecision(False, PluginInstanceDecisionReason.WRITER_CAS_CONFLICT)
    source = RuntimeSource(row(), writer_row=row(writer_generation=8))
    repository = TrustedPluginInstanceRepository(source, source, quarantine)
    result = await repository.advance_writer(request_value)
    assert result.changed
    assert result.record is not None
    assert result.record.fencing == FencingToken(3, 8)


async def test_cas_missing_result_is_conflict_not_inferred_success():
    source = RuntimeSource(None)
    repository = TrustedPluginInstanceRepository(source, source)
    lease = await repository.acquire_lease(
        LeaseCasRequest("instance-1", "worker-1", "lease-2", FencingToken(3, 7), NOW + timedelta(minutes=5))
    )
    assert lease == LeaseCasDecision(False, PluginInstanceDecisionReason.LEASE_CAS_CONFLICT)
    writer = await repository.advance_writer(WriterCasRequest("instance-1", "worker-1", FencingToken(3, 7)))
    assert writer == OwnerCasDecision(False, PluginInstanceDecisionReason.WRITER_CAS_CONFLICT)


async def test_service_preserves_fail_closed_not_found_for_quarantined_mismatch():
    source = RuntimeSource(row(artifact_digest="sha256:" + "b" * 64))
    quarantine = Quarantine()
    service = TrustedPluginInstanceService(source, source, quarantine)
    result = await service.authorize_positive_write(request())
    assert not result.allowed
    assert result.reason is PluginInstanceDecisionReason.NOT_FOUND
    assert quarantine.events[-1][1] == "runtime_instance_identity_mismatch"

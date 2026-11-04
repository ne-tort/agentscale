"""Source-level acceptance checks for M05 plugin instance/runtime fencing."""

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.plugin_instances import (
    FencingToken,
    LeaseCasDecision,
    LeaseCasRequest,
    OwnerCasDecision,
    PluginInstanceDecisionReason,
    PluginInstanceFencingService,
    PluginInstanceIdentity,
    PluginInstanceRecord,
    PluginInstanceState,
    PluginInstanceWriteRequest,
    WriterCasRequest,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
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


def record(**changes):
    values = {
        "identity": identity(),
        "subject_id": "subject-1",
        "session_id": "session-1",
        "state": PluginInstanceState.ACTIVE,
        "fencing": FencingToken(3, 7),
        "lease_owner": "worker-1",
        "lease_id": "lease-1",
        "lease_until": NOW + timedelta(minutes=5),
    }
    values.update(changes)
    return PluginInstanceRecord(**values)


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


class FakeRepository:
    """Exactly the ``PluginInstanceRepository`` port; no read fallback and no inference.

    The read/CAS members used to sit on ``FakeQuarantine`` below, which left the
    service calling ``get_exact`` on a repository that could not answer: every
    fencing assertion in this file failed with ``AttributeError`` instead of
    exercising the decision table.  Quarantine is a separate, append-only sink.
    """

    def __init__(self, row=None):
        self.row = row
        self.lookups = []
        self.lease_requests = []
        self.writer_requests = []

    async def get_exact(self, identity):
        self.lookups.append(identity)
        return self.row

    async def acquire_lease(self, request):
        self.lease_requests.append(request)
        return LeaseCasDecision(False, PluginInstanceDecisionReason.LEASE_CAS_CONFLICT)

    async def advance_writer(self, request):
        self.writer_requests.append(request)
        return OwnerCasDecision(False, PluginInstanceDecisionReason.WRITER_CAS_CONFLICT)


class FakeQuarantine:
    def __init__(self):
        self.records = []

    async def record_quarantine(self, record, *, reason):
        self.records.append((record, reason))


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("plugin_instance_id", PluginInstanceDecisionReason.IDENTITY_MISMATCH),
        ("definition_id", PluginInstanceDecisionReason.DEFINITION_MISMATCH),
        ("version_id", PluginInstanceDecisionReason.VERSION_MISMATCH),
        ("tenant_id", PluginInstanceDecisionReason.TENANT_MISMATCH),
        ("scope_id", PluginInstanceDecisionReason.SCOPE_MISMATCH),
        ("artifact_digest", PluginInstanceDecisionReason.DIGEST_MISMATCH),
    ],
)
async def test_composite_identity_and_digest_mismatch_have_no_write(field, reason):
    repository = FakeRepository(record())
    query = request(identity=identity(**{field: "other" if field != "artifact_digest" else "sha256:" + "b" * 64}))
    result = await PluginInstanceFencingService(repository).authorize_positive_write(query)
    assert not result.allowed
    assert result.reason is reason
    assert result.record is None


async def test_exact_instance_binding_is_used_for_lookup():
    repository = FakeRepository(None)
    await PluginInstanceFencingService(repository).authorize_positive_write(request())
    assert repository.lookups == [identity()]


async def test_binding_mismatch_is_quarantined_before_deny():
    repository = FakeRepository(record())
    quarantine = FakeQuarantine()
    result = await PluginInstanceFencingService(repository, quarantine).authorize_positive_write(
        request(identity=identity(artifact_digest="sha256:" + "b" * 64))
    )
    assert not result.allowed
    assert result.reason is PluginInstanceDecisionReason.DIGEST_MISMATCH
    assert quarantine.records == [(repository.row, PluginInstanceDecisionReason.DIGEST_MISMATCH.value)]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"subject_id": "other"}, PluginInstanceDecisionReason.SUBJECT_MISMATCH),
        ({"session_id": "other"}, PluginInstanceDecisionReason.SESSION_MISMATCH),
        ({"fencing": FencingToken(2, 7)}, PluginInstanceDecisionReason.RUNTIME_GENERATION_STALE),
        ({"fencing": FencingToken(3, 6)}, PluginInstanceDecisionReason.WRITER_GENERATION_STALE),
        ({"lease_owner": "other"}, PluginInstanceDecisionReason.LEASE_OWNER_MISMATCH),
        ({"lease_id": "other"}, PluginInstanceDecisionReason.LEASE_ID_MISMATCH),
    ],
)
async def test_runtime_write_context_is_fail_closed(changes, reason):
    service = PluginInstanceFencingService(FakeRepository(record(**changes)))
    result = await service.authorize_positive_write(request())
    assert (result.allowed, result.reason) == (False, reason)


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (PluginInstanceState.ARCHIVED, PluginInstanceDecisionReason.ARCHIVED),
        (PluginInstanceState.QUARANTINED, PluginInstanceDecisionReason.QUARANTINED),
    ],
)
async def test_non_active_states_never_authorize(state, reason):
    result = await PluginInstanceFencingService(FakeRepository(record(state=state))).authorize_positive_write(request())
    assert not result.allowed
    assert result.reason is reason


async def test_expired_or_missing_lease_never_authorizes():
    expired = record(lease_until=NOW)
    missing = record(lease_owner=None, lease_id=None, lease_until=None)
    for row, reason in ((expired, PluginInstanceDecisionReason.LEASE_EXPIRED), (missing, PluginInstanceDecisionReason.LEASE_MISSING)):
        result = await PluginInstanceFencingService(FakeRepository(row)).authorize_positive_write(request())
        assert not result.allowed
        assert result.reason is reason


async def test_positive_result_contains_the_fencing_snapshot():
    result = await PluginInstanceFencingService(FakeRepository(record())).authorize_positive_write(request())
    assert result.allowed
    assert result.record is not None
    assert result.record.fencing == FencingToken(3, 7)


async def test_lease_and_writer_paths_delegate_to_cas_without_inference():
    repository = FakeRepository(record())
    service = PluginInstanceFencingService(repository)
    lease = await service.acquire_lease(
        LeaseCasRequest(
            identity=identity(),
            subject_id="subject-1",
            session_id="session-1",
            expected=FencingToken(3, 7),
            lease_owner="worker-1",
            lease_id="lease-2",
            lease_until=NOW + timedelta(minutes=5),
        )
    )
    assert not lease.acquired
    assert repository.lease_requests
    writer = await service.advance_writer(
        WriterCasRequest(
            identity=identity(),
            subject_id="subject-1",
            session_id="session-1",
            expected=FencingToken(3, 7),
            lease_owner="worker-1",
            lease_id="lease-1",
            lease_until=NOW + timedelta(minutes=5),
        )
    )
    assert not writer.allowed
    assert writer.reason is PluginInstanceDecisionReason.WRITER_CAS_CONFLICT
    assert repository.writer_requests

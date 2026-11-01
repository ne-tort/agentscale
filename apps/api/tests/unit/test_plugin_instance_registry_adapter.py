"""Source acceptance checks for the T02 plugin-instance registry adapter."""

import pytest

from prodavan.application.plugin_instance_registry import (
    PluginInstanceRegistryIdentity,
    PluginInstanceRegistryOutcome,
    PluginInstanceRegistryQuery,
    PluginInstanceRegistryReason,
    PluginInstanceRegistryService,
)

DIGEST = "sha256:" + "a" * 64


def identity(**changes):
    value = {
        "plugin_instance_id": "instance-1",
        "definition_id": "definition-1",
        "version_id": "version-1",
        "tenant_id": "tenant-1",
        "scope_kind": "project",
        "scope_id": "project-1",
        "artifact_digest": DIGEST,
    }
    value.update(changes)
    return PluginInstanceRegistryIdentity(**value)


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
        "runtime_generation": 4,
        "writer_generation": 9,
    }
    value.update(changes)
    return value


class Source:
    def __init__(self, value):
        self.value = value
        self.queries = []

    async def read_exact(self, value):
        self.queries.append(value)
        return self.value


class Quarantine:
    def __init__(self):
        self.events = []

    async def quarantine(self, value, *, reason):
        self.events.append((value, reason))


async def test_active_resolution_preserves_complete_identity_and_runtime_evidence():
    source = Source(row())
    result = await PluginInstanceRegistryService(source).resolve(
        PluginInstanceRegistryQuery(identity(), subject_id="subject-1", session_id="session-1", runtime_generation=4)
    )
    assert result.outcome is PluginInstanceRegistryOutcome.RESOLVED
    assert result.record is not None
    assert result.record.identity == identity()
    assert result.record.subject_id == "subject-1"
    assert result.record.session_id == "session-1"
    assert (result.record.runtime_generation, result.record.writer_generation) == (4, 9)
    assert source.queries == [identity()]


async def test_missing_is_negative_without_record():
    result = await PluginInstanceRegistryService(Source(None)).resolve(PluginInstanceRegistryQuery(identity()))
    assert (result.outcome, result.reason, result.record) == (
        PluginInstanceRegistryOutcome.MISSING, PluginInstanceRegistryReason.NOT_FOUND, None
    )


async def test_archived_and_quarantined_states_never_resolve():
    for state, outcome, reason in (
        ("archived", PluginInstanceRegistryOutcome.ARCHIVED, PluginInstanceRegistryReason.ARCHIVED),
        ("quarantined", PluginInstanceRegistryOutcome.QUARANTINED, PluginInstanceRegistryReason.QUARANTINED),
    ):
        result = await PluginInstanceRegistryService(Source(row(state=state))).resolve(PluginInstanceRegistryQuery(identity()))
        assert (result.outcome, result.reason, result.record) == (outcome, reason, None)


async def test_identity_mismatch_is_quarantined_and_fail_closed():
    quarantine = Quarantine()
    result = await PluginInstanceRegistryService(Source(row(tenant_id="other")), quarantine).resolve(PluginInstanceRegistryQuery(identity()))
    assert (result.outcome, result.reason, result.record) == (
        PluginInstanceRegistryOutcome.QUARANTINED, PluginInstanceRegistryReason.IDENTITY_MISMATCH, None
    )
    assert quarantine.events[-1][1] == PluginInstanceRegistryReason.IDENTITY_MISMATCH.value


async def test_subject_session_and_generations_are_exact_context_gates():
    for query, reason in (
        (PluginInstanceRegistryQuery(identity(), subject_id="other"), PluginInstanceRegistryReason.SUBJECT_MISMATCH),
        (PluginInstanceRegistryQuery(identity(), session_id="other"), PluginInstanceRegistryReason.SESSION_MISMATCH),
        (PluginInstanceRegistryQuery(identity(), runtime_generation=3), PluginInstanceRegistryReason.RUNTIME_GENERATION_MISMATCH),
        (PluginInstanceRegistryQuery(identity(), writer_generation=8), PluginInstanceRegistryReason.WRITER_GENERATION_MISMATCH),
    ):
        result = await PluginInstanceRegistryService(Source(row())).resolve(query)
        assert result.reason is reason
        assert result.record is None


async def test_malformed_source_is_quarantined_without_positive_fallback():
    quarantine = Quarantine()
    result = await PluginInstanceRegistryService(Source(row(session_id=None)), quarantine).resolve(PluginInstanceRegistryQuery(identity()))
    assert result.outcome is PluginInstanceRegistryOutcome.QUARANTINED
    assert result.reason is PluginInstanceRegistryReason.INVALID_SNAPSHOT
    assert result.record is None
    assert quarantine.events


def test_identity_rejects_untyped_or_misaligned_plugin_scope():
    with pytest.raises(ValueError):
        identity(scope_kind="unknown")
    with pytest.raises(ValueError):
        identity(scope_kind="plugin_instance", scope_id="different-instance")


def test_identity_rejects_non_hex_artifact_digest():
    with pytest.raises(ValueError):
        identity(artifact_digest="sha256:" + "z" * 64)


def test_adapter_has_no_positive_write_or_cas_surface():
    assert not any(name in dir(PluginInstanceRegistryService) for name in ("create", "update", "delete", "acquire_lease", "advance_writer"))

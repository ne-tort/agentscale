"""Acceptance checks for gates, transitions, immutable snapshots and ports; not run."""
from __future__ import annotations

import asyncio

from .acceptance_fixtures import DEFINITION_ID, MODULE_ID, definition, gate, version, version_digest
from prodavan.application.migration.module_capabilities.lifecycle import (
    ModuleCapabilityLifecycleService,
    PublicationCommit,
    PublicationOutcome,
    TransitionCommit,
    TransitionOutcome,
)
from prodavan.application.migration.module_capabilities.contracts import (
    CapabilityDefinition,
    DefinitionLifecycle,
    VersionLifecycle,
)


class Repository:
    def __init__(self, commit: PublicationCommit = PublicationCommit(True)) -> None:
        self.commit = commit
        self.snapshots = []
        self.transitions = []

    async def append_published_snapshot(self, *, snapshot, transitions):
        if self.commit.committed:
            self.snapshots.append(snapshot)
            self.transitions.extend(transitions)
        return self.commit

    async def append_transition(self, *, transition):
        self.transitions.append(transition)
        return TransitionCommit(True)


class GateVerifier:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls = []

    async def verify_publication_gate(self, evidence):
        self.calls.append(evidence)
        return self.accepted


def run(coro):
    return asyncio.run(coro)


def test_publish_is_blocked_without_durable_scope_gate() -> None:
    repository = Repository()
    verifier = GateVerifier()
    service = ModuleCapabilityLifecycleService(repository=repository, gate_verifier=verifier)
    result = run(service.publish(definition=definition(), version=version(), evidence=gate(ready=False)))
    assert result.outcome is PublicationOutcome.BLOCKED
    assert result.reason == "durable_publication_gate_missing"
    assert not repository.snapshots
    assert not verifier.calls


def test_publish_requires_owner_scope_even_when_evidence_is_present() -> None:
    repository = Repository()
    verifier = GateVerifier()
    service = ModuleCapabilityLifecycleService(repository=repository, gate_verifier=verifier)
    result = run(service.publish(definition=definition(owner=None), version=version(), evidence=gate()))
    assert result.outcome is PublicationOutcome.BLOCKED
    assert result.reason == "durable_owner_scope_missing"
    assert not repository.snapshots


def test_publish_appends_immutable_snapshot_and_two_lifecycle_transitions() -> None:
    repository = Repository()
    service = ModuleCapabilityLifecycleService(repository=repository, gate_verifier=GateVerifier())
    result = run(service.publish(definition=definition(), version=version(), evidence=gate()))
    assert result.outcome is PublicationOutcome.PUBLISHED
    assert result.snapshot is not None
    assert result.snapshot.definition.lifecycle is DefinitionLifecycle.PUBLISHED
    assert result.snapshot.version.lifecycle is VersionLifecycle.PUBLISHED
    assert result.snapshot.artifact_digest == version_digest()
    assert len(repository.snapshots) == 1
    assert {item.to_state for item in repository.transitions} == {"published"}
    assert repository.transitions[0].from_state == "draft"
    assert repository.transitions[0].to_state == "1.0.0"


def test_changed_digest_replay_is_conflict_and_does_not_authorize_rewrite() -> None:
    repository = Repository(PublicationCommit(False, reason="source_digest_changed"))
    service = ModuleCapabilityLifecycleService(repository=repository, gate_verifier=GateVerifier())
    result = run(service.publish(definition=definition(), version=version(), evidence=gate()))
    assert result.outcome is PublicationOutcome.CONFLICT
    assert result.reason == "source_digest_changed"


def test_published_version_cannot_be_published_again_as_mutable_input() -> None:
    repository = Repository()
    service = ModuleCapabilityLifecycleService(repository=repository, gate_verifier=GateVerifier())
    result = run(service.publish(definition=definition(), version=version(lifecycle=VersionLifecycle.PUBLISHED), evidence=gate()))
    assert result.outcome.value == "invalid_transition"
    assert not repository.snapshots


def test_deprecated_definition_requires_explicit_forward_transition_evidence() -> None:
    repository = Repository()
    service = ModuleCapabilityLifecycleService(repository=repository, gate_verifier=GateVerifier())
    result = run(service.deprecate_definition(
        definition=definition(lifecycle=DefinitionLifecycle.PUBLISHED, current_version="0.9.0"),
        actor_id="operator", reason="replacement version", evidence_id="transition-proof", correlation_id="corr",
    ))
    assert result.outcome is TransitionOutcome.DEPRECATED
    assert result.transition is not None
    assert result.transition.from_state == "published"
    assert result.transition.to_state == "deprecated"


def test_definition_version_identity_mismatch_is_quarantined_before_repository() -> None:
    repository = Repository()
    service = ModuleCapabilityLifecycleService(repository=repository, gate_verifier=GateVerifier())
    foreign = version(module_id="different_module")
    result = run(service.publish(definition=definition(), version=foreign, evidence=gate()))
    assert result.outcome.value == "quarantined"
    assert result.reason == "definition_version_identity_mismatch"
    assert not repository.snapshots

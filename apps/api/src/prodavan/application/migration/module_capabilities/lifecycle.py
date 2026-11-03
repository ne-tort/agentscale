"""Lifecycle and publication gates for normalized module capabilities.

Only application ports are defined here. A repository implementation must make
publication plus its transition evidence one durable append-only transaction.
This service never writes legacy module rows or invokes module runtime code.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, Sequence

from .contracts import (
    CapabilityDefinition,
    CapabilityVersion,
    DefinitionLifecycle,
    LifecycleTransition,
    PublishedCapabilitySnapshot,
    PublicationGateEvidence,
    VersionLifecycle,
)


class PublicationOutcome(StrEnum):
    PUBLISHED = "published"
    REPLAY = "replay"
    BLOCKED = "blocked"
    CONFLICT = "conflict"
    INVALID_TRANSITION = "invalid_transition"
    QUARANTINED = "quarantined"


class TransitionOutcome(StrEnum):
    DEPRECATED = "deprecated"
    BLOCKED = "blocked"
    CONFLICT = "conflict"
    INVALID_TRANSITION = "invalid_transition"


@dataclass(frozen=True, slots=True)
class PublicationCommit:
    committed: bool
    replay: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TransitionCommit:
    committed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationResult:
    outcome: PublicationOutcome
    snapshot: PublishedCapabilitySnapshot | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TransitionResult:
    outcome: TransitionOutcome
    transition: LifecycleTransition | None = None
    reason: str | None = None


class PublicationGateVerifierPort(Protocol):
    """Server-side verifier for durable owner/version/scope evidence."""

    async def verify_publication_gate(self, evidence: PublicationGateEvidence) -> bool:
        ...


class CapabilityPublicationRepositoryPort(Protocol):
    """Trusted persistence boundary; no SQL or legacy adapter belongs here."""

    async def append_published_snapshot(
        self,
        *,
        snapshot: PublishedCapabilitySnapshot,
        transitions: Sequence[LifecycleTransition],
    ) -> PublicationCommit:
        """Atomically append immutable snapshot and transition evidence.

        Implementations must reject changed-digest reuse of a published identity,
        preserve the first snapshot on replay, and CAS writer_generation.
        """
        ...

    async def append_transition(self, *, transition: LifecycleTransition) -> TransitionCommit:
        """Append a forward-only lifecycle transition without rewriting snapshots."""
        ...


class ModuleCapabilityLifecycleService:
    """Fail-closed draft/published/deprecated transition application service."""

    def __init__(
        self,
        *,
        repository: CapabilityPublicationRepositoryPort,
        gate_verifier: PublicationGateVerifierPort,
    ) -> None:
        self._repository = repository
        self._gate_verifier = gate_verifier

    async def publish(
        self,
        *,
        definition: CapabilityDefinition,
        version: CapabilityVersion,
        evidence: PublicationGateEvidence,
    ) -> PublicationResult:
        if definition.owner_scope is None:
            return PublicationResult(PublicationOutcome.BLOCKED, reason="durable_owner_scope_missing")
        if definition.lifecycle not in {DefinitionLifecycle.DRAFT, DefinitionLifecycle.PUBLISHED}:
            return PublicationResult(PublicationOutcome.INVALID_TRANSITION, reason="definition_not_publishable")
        if version.lifecycle is not VersionLifecycle.DRAFT:
            return PublicationResult(PublicationOutcome.INVALID_TRANSITION, reason="version_not_draft")
        if version.definition_id != definition.definition_id or version.module_id != definition.module_id:
            return PublicationResult(PublicationOutcome.QUARANTINED, reason="definition_version_identity_mismatch")
        if not evidence.matches(definition, version):
            return PublicationResult(PublicationOutcome.QUARANTINED, reason="publication_evidence_identity_mismatch")
        if not evidence.ready:
            return PublicationResult(PublicationOutcome.BLOCKED, reason="durable_publication_gate_missing")
        if not await self._gate_verifier.verify_publication_gate(evidence):
            return PublicationResult(PublicationOutcome.BLOCKED, reason="publication_gate_rejected")

        published_definition = replace(
            definition,
            lifecycle=DefinitionLifecycle.PUBLISHED,
            current_version=version.version,
        )
        published_version = replace(version, lifecycle=VersionLifecycle.PUBLISHED)
        snapshot = PublishedCapabilitySnapshot(
            definition=published_definition,
            version=published_version,
            artifact_digest=version.content_digest,
            owner_evidence_id=evidence.owner_evidence_id,
            version_evidence_id=evidence.version_evidence_id,
            scope_evidence_id=evidence.scope_evidence_id,
            dependency_graph_evidence_id=evidence.dependency_graph_evidence_id,
            dependency_graph_digest=evidence.dependency_graph_digest,
            transaction_id=evidence.transaction_id,
            actor_id=evidence.actor_id,
            decision_owner=evidence.decision_owner,
            writer_generation=evidence.writer_generation,
            correlation_id=evidence.correlation_id,
        )
        transitions = (
            LifecycleTransition(
                definition_id=definition.definition_id,
                version=None,
                from_state=definition.current_version or definition.lifecycle.value,
                to_state=version.version,
                actor_id=evidence.actor_id,
                reason="publish capability definition",
                evidence_id=evidence.transaction_id,
                correlation_id=evidence.correlation_id,
            ),
            LifecycleTransition(
                definition_id=definition.definition_id,
                version=version.version,
                from_state=version.lifecycle.value,
                to_state=VersionLifecycle.PUBLISHED.value,
                actor_id=evidence.actor_id,
                reason="publish immutable capability version",
                evidence_id=evidence.transaction_id,
                correlation_id=evidence.correlation_id,
            ),
        )
        commit = await self._repository.append_published_snapshot(snapshot=snapshot, transitions=transitions)
        if not commit.committed:
            return PublicationResult(PublicationOutcome.CONFLICT, reason=commit.reason or "publication_commit_rejected")
        return PublicationResult(
            PublicationOutcome.REPLAY if commit.replay else PublicationOutcome.PUBLISHED,
            snapshot=snapshot,
            reason=commit.reason,
        )

    async def deprecate_definition(
        self,
        *,
        definition: CapabilityDefinition,
        actor_id: str,
        reason: str,
        evidence_id: str,
        correlation_id: str,
    ) -> TransitionResult:
        if definition.lifecycle is not DefinitionLifecycle.PUBLISHED:
            return TransitionResult(TransitionOutcome.INVALID_TRANSITION, reason="definition_must_be_published")
        transition = LifecycleTransition(
            definition_id=definition.definition_id,
            version=None,
            from_state=DefinitionLifecycle.PUBLISHED.value,
            to_state=DefinitionLifecycle.DEPRECATED.value,
            actor_id=actor_id,
            reason=reason,
            evidence_id=evidence_id,
            correlation_id=correlation_id,
        )
        commit = await self._repository.append_transition(transition=transition)
        if not commit.committed:
            return TransitionResult(TransitionOutcome.CONFLICT, transition=transition, reason=commit.reason or "transition_commit_rejected")
        return TransitionResult(TransitionOutcome.DEPRECATED, transition=transition)

    async def deprecate_version(
        self,
        *,
        definition_id: str,
        version: CapabilityVersion,
        actor_id: str,
        reason: str,
        evidence_id: str,
        correlation_id: str,
    ) -> TransitionResult:
        if version.lifecycle is not VersionLifecycle.PUBLISHED:
            return TransitionResult(TransitionOutcome.INVALID_TRANSITION, reason="version_must_be_published")
        transition = LifecycleTransition(
            definition_id=definition_id,
            version=version.version,
            from_state=VersionLifecycle.PUBLISHED.value,
            to_state=VersionLifecycle.DEPRECATED.value,
            actor_id=actor_id,
            reason=reason,
            evidence_id=evidence_id,
            correlation_id=correlation_id,
        )
        commit = await self._repository.append_transition(transition=transition)
        if not commit.committed:
            return TransitionResult(TransitionOutcome.CONFLICT, transition=transition, reason=commit.reason or "transition_commit_rejected")
        return TransitionResult(TransitionOutcome.DEPRECATED, transition=transition)


__all__ = [
    "CapabilityPublicationRepositoryPort", "ModuleCapabilityLifecycleService",
    "PublicationCommit", "PublicationGateVerifierPort", "PublicationOutcome", "PublicationResult",
    "TransitionCommit", "TransitionOutcome", "TransitionResult",
]

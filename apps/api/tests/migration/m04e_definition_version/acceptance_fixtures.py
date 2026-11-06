"""Fixture-only ports for M04E contract behavior; never bound in production."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Sequence

from prodavan.application.dynamic_apps_definition import (
    DefinitionResolution,
    DefinitionResolutionOutcome,
    DefinitionVersionQuery,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillBatch,
    BackfillCheckpoint,
    BackfillContext,
    BackfillOutcome,
    BackfillRecord,
    CandidateDecision,
    CandidateProof,
    EvidenceCandidate,
    EvidenceStatus,
    SourceRowState,
    UpstreamGateEvidence,
)
from prodavan.application.migration.m04e_definition_version.repository import (
    ActiveTypedScopeProof,
    DefinitionVersionProof,
    MigrationEvidenceContext,
)
from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppLifecycleStatus,
    AppScope,
    AppScopeKind,
    AppVersion,
)

TENANT = "tenant-m04e"
DEFINITION = "definition-m04e"
APP_ID = "demo-app"
VERSION_ID = "version-1"
MANIFEST_VERSION = "1.0.0"
ARTIFACT_URI = "content://tenant-m04e/definition-m04e/version-1"
ARTIFACT_BYTES = b"immutable-app-bundle-v1"
ARTIFACT_DIGEST = "sha256:" + hashlib.sha256(ARTIFACT_BYTES).hexdigest()
SNAPSHOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
SCOPE_DIGEST = "sha256:" + "c" * 64
SAME_DEFINITION_DIGEST = "sha256:" + "d" * 64
IMMUTABLE_DIGEST = "sha256:" + "e" * 64
AUDIT_DIGEST = "sha256:" + "f" * 64
NOW_TEXT = "2026-11-03T00:00:00+00:00"


def digest(byte: str = "1") -> str:
    return "sha256:" + byte * 64


def backfill_context() -> BackfillContext:
    return BackfillContext(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        source_snapshot_id="snapshot-m04e-1",
        source_snapshot_digest=SNAPSHOT_DIGEST,
        source_schema_version="legacy-definition-version.v1",
        canonical_schema_version="m04e.v1",
        transaction_id="tx-m04e-1",
        correlation_id="corr-m04e-1",
        actor="migration-fixture",
        decision_owner="coordinator-fixture",
        writer_generation=1,
    )


def checkpoint(*, sequence: int = 0, after_source_id: str | None = None) -> BackfillCheckpoint:
    return BackfillCheckpoint(
        partition=(TENANT, DEFINITION),
        phase="version",
        after_source_id=after_source_id,
        sequence=sequence,
        source_snapshot_id="snapshot-m04e-1",
        source_schema_version="legacy-definition-version.v1",
        source_snapshot_digest=SNAPSHOT_DIGEST,
        canonical_schema_version="m04e.v1",
        writer_generation=1,
    )


def version_candidate(
    *,
    source_id: str = VERSION_ID,
    definition_current_version_id: str | None = VERSION_ID,
    source_state: SourceRowState = SourceRowState.ACTIVE,
    payload_overrides: dict[str, object] | None = None,
) -> EvidenceCandidate:
    payload: dict[str, object] = {
        "status": "published",
        "manifest_version": MANIFEST_VERSION,
        "artifact_digest": ARTIFACT_DIGEST,
        "artifact_uri": ARTIFACT_URI,
        "definition_current_version_id": definition_current_version_id,
        "manifest": {"appId": APP_ID, "version": MANIFEST_VERSION},
    }
    if payload_overrides:
        payload.update(payload_overrides)
    return EvidenceCandidate(
        source_kind="legacy_app_version",
        source_id=source_id,
        schema_version="legacy-definition-version.v1",
        payload=payload,
        phase="version",
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        source_state=source_state,
        canonical_schema_version="m04e.v1",
    )


def definition_candidate(*, current_version_id: str = VERSION_ID) -> EvidenceCandidate:
    return EvidenceCandidate(
        source_kind="legacy_app_definition",
        source_id=DEFINITION,
        schema_version="legacy-definition-version.v1",
        payload={
            "app_id": APP_ID,
            "status": "published",
            "current_version_id": current_version_id,
        },
        phase="definition",
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        source_state=SourceRowState.ACTIVE,
        canonical_schema_version="m04e.v1",
    )


def gates_for(context: BackfillContext) -> UpstreamGateEvidence:
    return UpstreamGateEvidence(
        tenant_id=context.tenant_id,
        app_definition_id=context.app_definition_id,
        source_snapshot_digest=context.source_snapshot_digest,
        transaction_id=context.transaction_id,
        correlation_id=context.correlation_id,
        m04_gate_digest=digest("2"),
        m04e_gate_digest=digest("3"),
        active_scope_digest=SCOPE_DIGEST,
    )


def proof_for(
    row: EvidenceCandidate,
    context: BackfillContext,
    *,
    current_version_id: str | None = None,
    current_version_lifecycle: str | None = None,
) -> CandidateProof:
    manifest = row.payload.get("manifest")
    app_id = row.payload.get("app_id") if row.phase == "definition" else (
        manifest.get("appId") if isinstance(manifest, dict) else ""
    )
    linked_version_value = (
        row.payload.get("current_version_id") if row.phase == "definition"
        else row.payload.get("definition_current_version_id")
    )
    linked_version_id = linked_version_value if isinstance(linked_version_value, str) else None
    current_id = current_version_id if current_version_id is not None else linked_version_id
    version_digest = row.payload.get("artifact_digest") or ARTIFACT_DIGEST
    uri = row.payload.get("artifact_uri") or ARTIFACT_URI
    lifecycle = current_version_lifecycle or str(row.payload.get("status", "published"))
    return CandidateProof(
        tenant_id=context.tenant_id,
        app_definition_id=context.app_definition_id,
        source_kind=row.source_kind,
        source_id=row.source_id,
        source_digest=row.source_digest,
        transaction_id=context.transaction_id,
        correlation_id=context.correlation_id,
        owner_proof_digest=OWNER_DIGEST,
        active_scope_proof_digest=SCOPE_DIGEST,
        canonical_definition_id=context.app_definition_id,
        app_id=str(app_id),
        app_version_id=row.source_id if row.phase == "version" else None,
        manifest_version=str(row.payload.get("manifest_version", MANIFEST_VERSION)) if row.phase == "version" else None,
        artifact_uri=str(uri) if row.phase == "version" else None,
        same_definition_proof_digest=SAME_DEFINITION_DIGEST if row.phase == "version" else None,
        artifact_digest=str(version_digest) if row.phase == "version" else None,
        artifact_immutability_proof_digest=IMMUTABLE_DIGEST if row.phase == "version" else None,
        current_version_id=(
            row.source_id if row.phase == "version" and current_id == row.source_id
            else current_id if isinstance(current_id, str) else None
        ),
        current_version_definition_id=context.app_definition_id if current_id else None,
        current_version_manifest_version=MANIFEST_VERSION if current_id else None,
        current_version_artifact_digest=ARTIFACT_DIGEST if current_id else None,
        current_version_artifact_uri=ARTIFACT_URI if current_id else None,
        current_version_lifecycle=lifecycle if current_id else None,
        current_version_match_count=1 if current_id else 0,
        current_version_proof_digest=AUDIT_DIGEST if current_id else None,
        current_version_same_definition_digest=SAME_DEFINITION_DIGEST if current_id else None,
        current_version_immutable_artifact_digest=IMMUTABLE_DIGEST if current_id else None,
    )


class FixtureCandidateReader:
    def __init__(self, rows: Sequence[EvidenceCandidate]):
        self.rows = tuple(rows)
        self.calls: list[tuple[str, str | None, int]] = []

    async def read_page(self, *, context, phase, after_source_id, limit):
        self.calls.append((phase, after_source_id, limit))
        eligible = [
            row for row in self.rows
            if row.phase == phase and (after_source_id is None or row.source_id > after_source_id)
        ]
        return tuple(sorted(eligible, key=lambda item: item.source_id)[:limit])


class FixtureGatePort:
    async def prove_partition(self, *, context):
        return gates_for(context)


class FixtureCandidateProofPort:
    def __init__(self, overrides: dict[str, CandidateProof] | None = None):
        self.overrides = overrides or {}

    async def prove_candidate(self, *, context, gates, candidate):
        return self.overrides.get(candidate.source_id) or proof_for(candidate, context)


class FixtureArtifactContentPort:
    def __init__(self, content: bytes | None = ARTIFACT_BYTES, uri: str = ARTIFACT_URI):
        self.content = content
        self.uri = uri
        self.calls: list[tuple[str, str, str, str, int]] = []

    async def resolve_artifact_uri(self, *, tenant_id, app_definition_id, app_version_id):
        self.calls.append(("uri", tenant_id, app_definition_id, app_version_id, 0))
        return self.uri

    async def read_artifact(self, *, tenant_id, app_definition_id, app_version_id, artifact_uri, max_bytes):
        self.calls.append(("read", tenant_id, app_definition_id, app_version_id, max_bytes))
        if artifact_uri != self.uri:
            return None
        return self.content


class FixtureEvidenceStore:
    """Exercise application-port semantics; not a durable or production adapter."""

    def __init__(self):
        self.first: dict[tuple[str, str, str], BackfillRecord] = {}
        self.observations: dict[tuple[str, str, str, str | None, str | None], BackfillRecord] = {}
        self.durable_checkpoint: BackfillCheckpoint | None = None
        self.blockers: list[str] = []

    async def find_existing(self, *, context, source_kind, source_id, source_schema_version):
        return self.first.get((source_kind, source_id, source_schema_version))

    async def find_observation(
        self, *, context, source_kind, source_id, source_schema_version,
        observed_source_digest, observed_artifact_digest,
    ):
        record = self.observations.get((
            source_kind, source_id, source_schema_version,
            observed_source_digest, observed_artifact_digest,
        ))
        return replace(record, outcome=BackfillOutcome.REPLAY) if record else None

    async def record_blocked(self, *, context, checkpoint, reason_code, blocker_evidence=()):
        self.blockers.append(reason_code)
        return BackfillRecord(
            source_kind="m04e_backfill",
            source_id="blocked",
            schema_version=context.source_schema_version,
            source_digest=context.source_snapshot_digest,
            canonical_digest=None,
            canonical_identity=(),
            checkpoint=checkpoint,
            status=EvidenceStatus.BLOCKED,
            reason_code=reason_code,
            correlation_id=context.correlation_id,
            tenant_id=context.tenant_id,
            app_definition_id=context.app_definition_id,
            blocker_evidence=tuple(blocker_evidence),
        )

    async def classify_and_commit_page(
        self, *, context, gates, expected_checkpoint, decisions, page_exhausted,
    ):
        records: list[BackfillRecord] = []
        consumed: list[EvidenceCandidate] = []
        for decision in decisions:
            row = decision.candidate
            key = (row.source_kind, row.source_id, row.schema_version)
            if decision.status is EvidenceStatus.REPLAY and decision.reason_code in {
                "source_digest_changed", "artifact_digest_changed",
            }:
                observation_key = (
                    row.source_kind,
                    row.source_id,
                    row.schema_version,
                    row.source_digest if decision.reason_code == "source_digest_changed" else None,
                    decision.verified_artifact_digest if decision.reason_code == "artifact_digest_changed" else None,
                )
                observed = self.observations.get(observation_key)
                if observed is not None:
                    records.append(replace(observed, outcome=BackfillOutcome.REPLAY))
                    continue
            previous = self.first.get(key)
            if (
                previous is not None
                and previous.source_digest == row.source_digest
                and previous.verified_artifact_digest == decision.verified_artifact_digest
                and previous.status is decision.status
                and previous.reason_code == decision.reason_code
            ):
                records.append(replace(previous, outcome=BackfillOutcome.REPLAY))
                continue
            if previous is not None:
                source_changed = previous.source_digest != row.source_digest
                observation = replace(
                    previous,
                    status=EvidenceStatus.CONFLICT,
                    canonical_digest=None,
                    verified_artifact_digest=(
                        previous.verified_artifact_digest if source_changed
                        else decision.verified_artifact_digest
                    ),
                    reason_code="source_digest_changed" if source_changed else "artifact_digest_changed",
                    outcome=BackfillOutcome.CONFLICT,
                    observed_source_digest=row.source_digest if source_changed else None,
                    observed_artifact_digest=(
                        decision.verified_artifact_digest if not source_changed else None
                    ),
                )
                observation_key = (
                    row.source_kind, row.source_id, row.schema_version,
                    observation.observed_source_digest, observation.observed_artifact_digest,
                )
                self.observations.setdefault(observation_key, observation)
                self.observations.setdefault(
                    observation_key, replace(observation, outcome=BackfillOutcome.REPLAY)
                )
                records.append(observation)
                consumed.append(row)
                continue
            if decision.status is EvidenceStatus.CONFLICT:
                record = BackfillRecord(
                    source_kind=row.source_kind,
                    source_id=row.source_id,
                    schema_version=row.schema_version,
                    source_digest=row.source_digest,
                    canonical_digest=None,
                    verified_artifact_digest=decision.verified_artifact_digest,
                    canonical_identity=row.canonical_identity,
                    checkpoint=expected_checkpoint,
                    status=EvidenceStatus.CONFLICT,
                    reason_code=decision.reason_code,
                    correlation_id=context.correlation_id,
                    tenant_id=context.tenant_id,
                    app_definition_id=context.app_definition_id,
                    phase=row.phase,
                    source_snapshot_digest=context.source_snapshot_digest,
                    transaction_id=context.transaction_id,
                    writer_generation=context.writer_generation,
                    outcome=BackfillOutcome.CONFLICT,
                )
                if decision.reason_code in {"source_digest_changed", "artifact_digest_changed"}:
                    observation_key = (
                        row.source_kind, row.source_id, row.schema_version,
                        row.source_digest if decision.reason_code == "source_digest_changed" else None,
                        decision.verified_artifact_digest if decision.reason_code == "artifact_digest_changed" else None,
                    )
                    replay_record = replace(record, outcome=BackfillOutcome.REPLAY)
                    self.observations.setdefault(observation_key, replay_record)
                self.first.setdefault(key, record)
                records.append(record)
                consumed.append(row)
                continue
            proof = decision.proof
            proof_evidence = ()
            if proof is not None:
                proof_evidence = (
                    ("owner", proof.owner_proof_digest),
                    ("active_scope", proof.active_scope_proof_digest),
                )
                if row.phase == "version":
                    proof_evidence += (
                        ("same_definition", proof.same_definition_proof_digest or ""),
                        ("immutable_artifact", proof.artifact_immutability_proof_digest or ""),
                        ("artifact_content", decision.verified_artifact_digest or ""),
                    )
                if proof.current_version_id:
                    proof_evidence += (
                        ("current_version", proof.current_version_proof_digest or ""),
                        ("current_same_definition", proof.current_version_same_definition_digest or ""),
                        ("current_artifact_immutability", proof.current_version_immutable_artifact_digest or ""),
                        ("current_artifact_content", decision.verified_current_artifact_digest or ""),
                    )
            outcome = {
                EvidenceStatus.ACCEPTED: BackfillOutcome.ACCEPTED,
                EvidenceStatus.QUARANTINED: BackfillOutcome.QUARANTINED,
                EvidenceStatus.CONFLICT: BackfillOutcome.CONFLICT,
            }[decision.status]
            record = BackfillRecord(
                source_kind=row.source_kind,
                source_id=row.source_id,
                schema_version=row.schema_version,
                source_digest=row.source_digest,
                canonical_digest=row.canonical_digest if decision.status is EvidenceStatus.ACCEPTED else None,
                canonical_identity=row.canonical_identity,
                checkpoint=expected_checkpoint,
                status=decision.status,
                reason_code=decision.reason_code,
                correlation_id=context.correlation_id,
                tenant_id=context.tenant_id,
                app_definition_id=context.app_definition_id,
                phase=row.phase,
                source_snapshot_digest=context.source_snapshot_digest,
                proof_evidence=proof_evidence,
                artifact_uri=proof.artifact_uri if row.phase == "version" and proof else None,
                artifact_digest=str(row.payload.get("artifact_digest")) if row.phase == "version" else None,
                verified_artifact_digest=decision.verified_artifact_digest,
                current_artifact_digest=decision.verified_current_artifact_digest,
                transaction_id=context.transaction_id,
                writer_generation=context.writer_generation,
                outcome=outcome,
                blocker_evidence=decision.blocker_evidence,
            )
            self.first[key] = record
            records.append(record)
            consumed.append(row)
        current = self.durable_checkpoint or expected_checkpoint
        if consumed:
            last = consumed[-1]
            complete = page_exhausted and expected_checkpoint.phase == "version"
            current = replace(
                expected_checkpoint,
                phase=("version" if page_exhausted and expected_checkpoint.phase == "definition" else expected_checkpoint.phase),
                after_source_id=(None if page_exhausted and expected_checkpoint.phase == "definition" else last.source_id),
                sequence=expected_checkpoint.sequence + len(consumed) + (1 if page_exhausted and expected_checkpoint.phase == "definition" else 0),
                phase_complete=complete,
            )
            self.durable_checkpoint = current
        elif records:
            # A replay-only page consumes no cursor position, so a durable store
            # returns its stored checkpoint unchanged. Rebuilding it from the
            # caller's older ``expected_checkpoint`` would model a store that
            # silently regresses (and could replay) an already-consumed cursor,
            # which the real adapter never does.
            current = self.durable_checkpoint or expected_checkpoint
        elif page_exhausted and expected_checkpoint.phase == "definition":
            current = replace(
                expected_checkpoint,
                phase="version",
                after_source_id=None,
                sequence=expected_checkpoint.sequence + 1,
            )
            self.durable_checkpoint = current
        elif page_exhausted and expected_checkpoint.phase == "version":
            current = replace(expected_checkpoint, phase_complete=True)
            self.durable_checkpoint = current
        return BackfillBatch(tuple(records), current, current.phase_complete)


class FixtureDefinitionResolver:
    def __init__(self, result: DefinitionResolution):
        self.result = result
        self.calls: list[DefinitionVersionQuery] = []

    async def resolve(self, query):
        self.calls.append(query)
        return self.result


class FixtureScopePort:
    def __init__(self, *, scope_id: str = TENANT, active: bool = True):
        self.proof = ActiveTypedScopeProof(
            tenant_id=TENANT,
            app_id=APP_ID,
            app_definition_id=DEFINITION,
            scope_kind="tenant",
            scope_id=scope_id,
            transaction_id="tx-resolve",
            correlation_id="corr-resolve",
            proof_digest=OWNER_DIGEST,
            m04_gate_digest=digest("7"),
            m04e_gate_digest=digest("8"),
            active=active,
        )

    async def prove_active_scope(self, *, context, tenant_id, app_id):
        return self.proof


class FixtureDefinitionEvidencePort:
    def __init__(self, proof: DefinitionVersionProof):
        self.proof = proof
        self.denials = []
        self.proofs = 0

    async def prove_and_record(
        self, *, context, query, resolution, scope, artifact_uri, artifact_content_digest,
    ):
        self.proofs += 1
        return self.proof

    async def record_denial(self, *, context, query, reason):
        self.denials.append((query, reason))
        return AUDIT_DIGEST


class FixtureM05HandoffPort:
    def __init__(self):
        self.markers = []

    async def append_marker(self, marker):
        self.markers.append(marker)
        return marker


def resolved_pair(*, current_version_id: str | None = VERSION_ID):
    from datetime import datetime, timezone

    from prodavan.application.dynamic_apps_definition import DefinitionVersionRef

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    definition = AppDefinition(
        id=DEFINITION,
        tenant_id=TENANT,
        app_id=APP_ID,
        display_name="Demo",
        scope=AppScope(kind=AppScopeKind.TENANT, id=TENANT),
        status=AppLifecycleStatus.PUBLISHED if current_version_id else AppLifecycleStatus.DRAFT,
        current_version_id=current_version_id,
        created_at=now,
        updated_at=now,
    )
    version = AppVersion(
        id=VERSION_ID,
        app_definition_id=DEFINITION,
        manifest_version=MANIFEST_VERSION,
        artifact_digest=ARTIFACT_DIGEST,
        manifest={
            "protocol": "1.0",
            "appId": APP_ID,
            "version": MANIFEST_VERSION,
            "displayName": "Demo",
            "entrypoints": {"ui": {"type": "text"}},
            "requestedCapabilities": [],
        },
        status=AppLifecycleStatus.PUBLISHED if current_version_id else AppLifecycleStatus.DRAFT,
        created_at=now,
        published_at=now if current_version_id else None,
    )
    reference = DefinitionVersionRef(
        tenant_id=TENANT,
        app_id=APP_ID,
        app_definition_id=DEFINITION,
        app_version_id=VERSION_ID,
        manifest_version=MANIFEST_VERSION,
        artifact_digest=ARTIFACT_DIGEST,
    )
    result = DefinitionResolution(
        outcome=DefinitionResolutionOutcome.RESOLVED,
        definition=definition,
        version=version,
        reference=reference,
    )
    return result


def resolver_context(*, tenant_id: str | None = TENANT, authenticated: bool = True) -> MigrationEvidenceContext:
    return MigrationEvidenceContext(
        resolved_tenant_id=tenant_id,
        authenticated=authenticated,
        transaction_id="tx-resolve",
        correlation_id="corr-resolve",
    )


def definition_version_proof(*, current_version_id: str | None = VERSION_ID) -> DefinitionVersionProof:
    return DefinitionVersionProof(
        tenant_id=TENANT,
        app_id=APP_ID,
        app_definition_id=DEFINITION,
        app_version_id=VERSION_ID,
        manifest_version=MANIFEST_VERSION,
        transaction_id="tx-resolve",
        correlation_id="corr-resolve",
        definition_owner_digest=OWNER_DIGEST,
        same_definition_digest=SAME_DEFINITION_DIGEST,
        immutable_artifact_digest=IMMUTABLE_DIGEST,
        artifact_digest=ARTIFACT_DIGEST,
        artifact_uri=ARTIFACT_URI,
        artifact_content_digest=ARTIFACT_DIGEST,
        current_version_id=current_version_id,
        current_version_definition_id=DEFINITION if current_version_id else None,
        current_version_manifest_version=MANIFEST_VERSION if current_version_id else None,
        current_version_artifact_digest=ARTIFACT_DIGEST if current_version_id else None,
        current_version_artifact_uri=ARTIFACT_URI if current_version_id else None,
        current_version_lifecycle="published" if current_version_id else None,
        current_version_match_count=1 if current_version_id else 0,
        current_version_same_definition_digest=SAME_DEFINITION_DIGEST if current_version_id else None,
        current_version_immutable_artifact_digest=IMMUTABLE_DIGEST if current_version_id else None,
        current_version_proof_digest=AUDIT_DIGEST if current_version_id else None,
        definition_active=True,
        version_active=True,
        audit_digest=AUDIT_DIGEST,
    )

"""Server-resolved M06 dependency handoff gates.

The adapter must never accept a caller-supplied readiness object. This module turns
accepted, partition-bound upstream evidence (M04 scope/RLS, M04E definition/version,
M05 exact instance/runtime) plus the accepted bounded source inventory into
:class:`DependencyGates`. Every field is re-derived from server state and compared;
anything missing, stale, ambiguous or out of partition yields ``None`` so the adapter
records no shadow evidence and the legacy gateway stays the only decision owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

from .adapter import (
    AuthenticatedGrantContext,
    DependencyGates,
    DependencyHandoffPort,
    LegacyGrantSource,
    digest,
    is_sha256,
    request_identity_digest_from_mapping,
    schema_identity_digest,
)


@dataclass(frozen=True, slots=True)
class AcceptedSourceKey:
    """One accepted legacy source row inside the bounded partition inventory."""

    source_kind: str
    source_id: str
    schema_version: str
    source_digest: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip()
                   for value in (self.source_kind, self.source_id, self.schema_version)):
            raise ValueError("accepted source identity is required")
        if not is_sha256(self.source_digest):
            raise ValueError("accepted source digest must be sha256")


@dataclass(frozen=True, slots=True)
class AcceptedPartitionEvidence:
    """Immutable upstream acceptance record for exactly one runtime partition.

    Produced by trusted migration/security tooling, never by a request. The
    ``accepted_sources`` inventory is the only proof that a source row belongs to the
    partition; a source outside it is not migratable evidence.
    """

    tenant_id: str
    app_definition_id: str
    app_version_id: str
    plugin_instance_id: str
    writer_epoch: int
    m04_scope_digest: str
    m04_rls_digest: str
    m04e_definition_version_digest: str
    m05_instance_digest: str
    m06_schema_identity_digest: str
    m04_scope_evidence_id: str
    m04_rls_evidence_id: str
    m04e_definition_version_evidence_id: str
    m05_instance_runtime_evidence_id: str
    atomic_evidence_id: str
    m06_schema_evidence_id: str
    final_delta_checkpoint: str
    inventory_digest: str
    accepted_sources: tuple[AcceptedSourceKey, ...]

    def __post_init__(self) -> None:
        for name in ("tenant_id", "app_definition_id", "app_version_id", "plugin_instance_id",
                     "m04_scope_evidence_id", "m04_rls_evidence_id",
                     "m04e_definition_version_evidence_id", "m05_instance_runtime_evidence_id",
                     "atomic_evidence_id", "m06_schema_evidence_id", "final_delta_checkpoint"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"accepted partition evidence requires {name}")
        for name in ("m04_scope_digest", "m04_rls_digest", "m04e_definition_version_digest",
                     "m05_instance_digest", "m06_schema_identity_digest", "inventory_digest"):
            if not is_sha256(getattr(self, name)):
                raise ValueError(f"accepted partition evidence {name} must be sha256")
        if not isinstance(self.writer_epoch, int) or isinstance(self.writer_epoch, bool) or self.writer_epoch <= 0:
            raise ValueError("accepted writer epoch must be positive")
        if not isinstance(self.accepted_sources, tuple) or any(
            not isinstance(item, AcceptedSourceKey) for item in self.accepted_sources
        ):
            raise ValueError("accepted source inventory must be a tuple of accepted source keys")
        keys = [(item.source_kind, item.source_id, item.schema_version)
                for item in self.accepted_sources]
        if len(set(keys)) != len(keys):
            raise ValueError("accepted source inventory must not repeat a source identity")
        if not self.accepted_sources:
            raise ValueError("accepted partition evidence requires a non-empty source inventory")
        ordered = tuple(sorted(self.accepted_sources, key=lambda item: (
            item.source_kind, item.source_id, item.schema_version, item.source_digest)))
        if ordered != self.accepted_sources:
            raise ValueError("accepted source inventory must be canonically ordered")
        if self.inventory_digest != inventory_digest(self.accepted_sources):
            raise ValueError("accepted source inventory digest does not match its contents")

    def admits(self, source: LegacyGrantSource, source_digest: str) -> bool:
        return any(
            item.source_kind == source.source_kind and item.source_id == source.source_id
            and item.schema_version == source.schema_version
            and item.source_digest == source_digest
            for item in self.accepted_sources
        )


def inventory_digest(sources: Sequence[AcceptedSourceKey]) -> str:
    return digest(tuple((item.source_kind, item.source_id, item.schema_version, item.source_digest)
                        for item in sources))


class PartitionHandoffReadPort(Protocol):
    """Read accepted immutable upstream evidence for one exact runtime partition."""

    async def load_partition(self, *, tenant_id: str, app_definition_id: str,
                             app_version_id: str, plugin_instance_id: str,
                             writer_generation: int) -> AcceptedPartitionEvidence | None: ...


class ServerResolvedHandoffResolver(DependencyHandoffPort):
    """Build gates only from accepted partition evidence matching the live request."""

    def __init__(self, *, partitions: PartitionHandoffReadPort,
                 expected_schema_identity_digest: str | None = None) -> None:
        self._partitions = partitions
        self._expected_schema = expected_schema_identity_digest or schema_identity_digest()

    async def resolve_gates(self, *, context: AuthenticatedGrantContext,
                            source: LegacyGrantSource, source_digest: str,
                            now: datetime) -> DependencyGates | None:
        if not self._context_usable(context):
            return None
        if not is_sha256(source_digest):
            return None
        if (source.app_definition_id is None or source.app_version_id is None
                or source.asserted_scope_kind is None or source.asserted_scope_id is None
                or (source.asserted_company_id is not None
                    and source.asserted_company_id != context.company_id)):
            return None
        try:
            evidence = await self._partitions.load_partition(
                tenant_id=context.tenant_id or "",
                app_definition_id=source.app_definition_id,
                app_version_id=source.app_version_id,
                plugin_instance_id=context.plugin_instance_id or "",
                writer_generation=context.writer_generation or 0,
            )
        except Exception:
            return None
        if not isinstance(evidence, AcceptedPartitionEvidence):
            return None
        if not self._evidence_matches(evidence, context, source):
            return None
        if not evidence.admits(source, source_digest):
            return None
        attested = request_identity_digest_from_mapping({
            "tenant_id": context.tenant_id, "company_id": context.company_id,
            "app_definition_id": source.app_definition_id, "app_id": source.app_id,
            "app_version_id": source.app_version_id,
            "manifest_version": source.manifest_version,
            "scope_kind": "plugin_instance", "scope_id": context.plugin_instance_id,
            "installation_scope_kind": source.asserted_scope_kind,
            "installation_scope_id": source.asserted_scope_id,
            "cabinet_id": context.cabinet_id, "project_id": context.project_id,
            "subject_kind": context.subject_kind, "subject_id": context.subject_id,
            "session_id": context.session_id,
            "plugin_instance_id": context.plugin_instance_id,
            "runtime_generation": context.runtime_generation,
            "writer_generation": context.writer_generation,
        })
        if attested is None:
            return None
        return DependencyGates(
            m04_scope_digest=evidence.m04_scope_digest,
            m04_rls_digest=evidence.m04_rls_digest,
            m04e_definition_version_digest=evidence.m04e_definition_version_digest,
            m05_instance_digest=evidence.m05_instance_digest,
            m06_schema_digest=evidence.m06_schema_identity_digest,
            trusted_writer_epoch=context.writer_generation,
            m04_scope_accepted=True,
            m04_rls_accepted=True,
            m04e_definition_version_accepted=True,
            m05_instance_runtime_accepted=True,
            atomic_evidence_accepted=True,
            m04_scope_evidence_id=evidence.m04_scope_evidence_id,
            m04_rls_evidence_id=evidence.m04_rls_evidence_id,
            m04e_definition_version_evidence_id=evidence.m04e_definition_version_evidence_id,
            m05_instance_runtime_evidence_id=evidence.m05_instance_runtime_evidence_id,
            atomic_evidence_id=evidence.atomic_evidence_id,
            m06_schema_accepted=True,
            m06_schema_evidence_id=evidence.m06_schema_evidence_id,
            m06_schema_evidence_digest=evidence.m06_schema_identity_digest,
            tenant_id=context.tenant_id,
            app_definition_id=source.app_definition_id,
            app_version_id=source.app_version_id,
            scope_kind="plugin_instance",
            scope_id=context.plugin_instance_id,
            plugin_instance_id=context.plugin_instance_id,
            subject_id=context.subject_id,
            subject_kind=context.subject_kind,
            session_id=context.session_id,
            runtime_generation=context.runtime_generation,
            writer_generation=context.writer_generation,
            transaction_id=context.transaction_id,
            correlation_id=context.correlation_id,
            source_digest=source_digest,
            final_delta_checkpoint=evidence.final_delta_checkpoint,
            installation_scope_kind=source.asserted_scope_kind,
            installation_scope_id=source.asserted_scope_id,
            company_id=context.company_id,
            app_id=source.app_id,
            manifest_version=source.manifest_version,
            cabinet_id=context.cabinet_id,
            project_id=context.project_id,
            request_identity_digest=attested,
        )

    @staticmethod
    def _context_usable(context: AuthenticatedGrantContext) -> bool:
        rls = context.rls_context
        return bool(
            context.authenticated is True
            and context.tenant_id and context.company_id and context.subject_kind
            and context.subject_id and context.session_id and context.transaction_id
            and context.correlation_id.strip() and context.plugin_instance_id
            and context.runtime_generation and context.writer_generation
            and rls is not None and rls.tenant_id == context.tenant_id
            and rls.user_id == context.subject_id
            and rls.cabinet_id == context.cabinet_id
            and rls.project_id == context.project_id
        )

    @staticmethod
    def _evidence_matches(evidence: AcceptedPartitionEvidence,
                          context: AuthenticatedGrantContext,
                          source: LegacyGrantSource) -> bool:
        return bool(
            evidence.tenant_id == context.tenant_id
            and evidence.app_definition_id == source.app_definition_id
            and evidence.app_version_id == source.app_version_id
            and evidence.plugin_instance_id == context.plugin_instance_id
            and evidence.writer_epoch == context.writer_generation
            and evidence.m06_schema_identity_digest == schema_identity_digest()
        )


__all__ = [
    "AcceptedPartitionEvidence",
    "AcceptedSourceKey",
    "PartitionHandoffReadPort",
    "ServerResolvedHandoffResolver",
    "inventory_digest",
]

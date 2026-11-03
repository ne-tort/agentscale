"""Transaction-bound, read-only M04E ORM adapters.

This module reads only registered M04E and M04 ORM models. It does not enable
writes, emit evidence digests, or promote a partition when durable gate proof is
not available.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.dynamic_apps_definition.contracts import (
    DefinitionResolution,
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
    DefinitionVersionQuery,
    DefinitionVersionRef,
)
from prodavan.application.migration.m04e_definition_version.artifact import ArtifactContentPort
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
    RequestedCapability,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    DynamicAppDefinitionFoundationRow,
    DynamicAppDefinitionVersionFoundationRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.owner_registry import (
    CanonicalTenantAnchorRow,
    ScopeOwnerCabinetAnchorRow,
    ScopeOwnerMappingRow,
    ScopeOwnerProjectAnchorRow,
    ScopeRegistryRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.persistence.migration_scope.adapter import ReviewedScopeRlsManifest

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_TENANT_POLICY = re.compile(
    r"(?:\"?[a-z_][a-z0-9_]*\"?\.)?\"?(?:tenant_id|company_id|id)\"?"
    r"\s*=\s*current_setting\(\s*'app\.tenant_id'"
    r"(?:\s*::\s*text)?\s*,\s*true\s*\)"
)
_ACTIVE_POLICY = re.compile(
    r"owner_state\s*=\s*'active'|mapping_state\s*=\s*'mapped'|"
    r"status\s*=\s*'active'|project_status\s*=\s*'active'"
)
_M04_OWNER_TABLES = (
    "scope_registry",
    "scope_owner_tenant_anchors",
    "scope_owner_cabinet_anchors",
    "scope_owner_project_anchors",
    "scope_owner_mappings",
    "scope_owner_quarantine",
    "scope_owner_audit",
)
_CONTEXT_SQL = text(
    "SELECT current_setting('app.tenant_id', true) AS tenant_id, "
    "current_setting('app.authenticated_subject_id', true) AS subject_id, "
    "current_setting('app.authenticated', true) AS authenticated, "
    "current_setting('app.correlation_id', true) AS correlation_id, "
    "current_setting('transaction_isolation') AS transaction_isolation, "
    "txid_current()::text AS transaction_id"
)
_RLS_SQL = text(
    "SELECT c.relrowsecurity AS enabled, c.relforcerowsecurity AS forced, "
    "p.policyname AS policyname, p.cmd AS command, p.qual AS qualifier, p.with_check AS with_check, "
    "p.roles AS roles, p.permissive AS permissive, "
    "r.rolsuper AS is_superuser, r.rolbypassrls AS bypass_rls, current_user AS current_role "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_roles r ON r.rolname = current_user "
    "LEFT JOIN pg_policies p ON p.schemaname = n.nspname AND p.tablename = c.relname "
    "WHERE c.oid = to_regclass(:table_name)"
)

M04E_PERSISTENCE_BLOCKERS = (
    "m04_scope_promotion_evidence_missing: no registered m04_scope_promotion_evidence table or M04-02 immutable partition gate row exists; scope_registry has no M04/M04E gate-digest fields",
    "m04_scope_mapping_lineage_incomplete: scope_owner_mappings has no canonical_schema_version and there is no durable source-snapshot checkpoint/CAS record",
    "m04_scope_registry_rls_unreviewed: M04 ENABLE/FORCE RLS and restrictive tenant/active USING/WITH CHECK policy manifest/digests have not been independently reviewed",
    "m04e_definition_version_rls_unreviewed: M04E definition/version ENABLE/FORCE RLS and restrictive tenant USING/WITH CHECK policy digests have not been independently reviewed",
    "m04e_definition_version_resolution_audit_missing: no registered table has tenant_id, app_id, definition_id, version_id, manifest_version, artifact_digest, artifact_uri, content_digest, current_version_id, current_definition_id, current_manifest_version, current_artifact_digest, current_artifact_uri, current_lifecycle, current_match_count, transaction_id, correlation_id, event_kind, outcome, reason_code, and recorded_at columns",
    "m04e_audit_append_only_rls_missing: no registered resolver audit has trusted append-only privileges, UPDATE/DELETE denial, ENABLE/FORCE RLS, and tenant-bound USING/WITH CHECK policies",
    "m04e_artifact_content_adapter_missing: no production content adapter is bound for byte-level artifact verification",
    "m04e_authenticated_transaction_context_unbound: no application persistence setup binds app.tenant_id, app.authenticated_subject_id, app.authenticated=true, app.correlation_id, and SERIALIZABLE isolation before this adapter runs",
    "m04_actor_membership_unproven: no trusted app-user mapping to active tenant MembershipRow is present in the resolver context; MembershipRow has no active/deleted lifecycle columns",
)

_M04_SCOPE_PROMOTION_BLOCKER = M04E_PERSISTENCE_BLOCKERS[0]
_M04_SCOPE_MAPPING_LINEAGE_BLOCKER = M04E_PERSISTENCE_BLOCKERS[1]
_M04_SCOPE_RLS_BLOCKER = M04E_PERSISTENCE_BLOCKERS[2]
_M04E_RLS_BLOCKER = M04E_PERSISTENCE_BLOCKERS[3]
_M04E_AUDIT_BLOCKER = M04E_PERSISTENCE_BLOCKERS[4]
_M04E_AUDIT_RLS_BLOCKER = M04E_PERSISTENCE_BLOCKERS[5]
_ARTIFACT_CONTENT_BLOCKER = M04E_PERSISTENCE_BLOCKERS[6]
_M04_ACTOR_MEMBERSHIP_BLOCKER = M04E_PERSISTENCE_BLOCKERS[7]


@dataclass(frozen=True, slots=True)
class ReviewedM04EDefinitionRlsManifest:
    """Independent review digest for the two M04E read-owner tables."""

    review_id: str
    database_role: str
    policy_digests: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        expected = {"m04e_dynamic_app_definitions", "m04e_dynamic_app_versions"}
        names = tuple(name for name, _digest in self.policy_digests)
        if not self.review_id.strip() or not self.database_role.strip():
            raise ValueError("M04E RLS review ID and database role are required")
        if set(names) != expected or len(names) != len(expected):
            raise ValueError("M04E RLS review must bind both definition/version tables exactly once")
        if any(_SHA256.fullmatch(digest) is None for _name, digest in self.policy_digests):
            raise ValueError("M04E RLS policy digests must use sha256 format")

    def digest_for(self, table_name: str) -> str | None:
        return dict(self.policy_digests).get(table_name)


class M04EDefinitionVersionPersistenceAdapter:
    """Read exact M04E keys inside a caller-owned authenticated DB transaction.

    A single AsyncSession is supplied for the resolver, scope and evidence ports
    so every read is transaction-bound. The adapter never starts or commits a
    transaction itself.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        artifacts: ArtifactContentPort | None = None,
        scope_rls_manifest: ReviewedScopeRlsManifest | None = None,
        definition_rls_manifest: ReviewedM04EDefinitionRlsManifest | None = None,
    ) -> None:
        self._session = session
        self._artifacts = artifacts
        self._scope_rls_manifest = scope_rls_manifest
        self._definition_rls_manifest = definition_rls_manifest
        self._blockers: list[str] = []

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*M04E_PERSISTENCE_BLOCKERS, *self._blockers)))

    async def server_context(self) -> MigrationEvidenceContext | None:
        """Read identity and correlation only from trusted DB transaction state."""
        if not self._session.in_transaction():
            self._note("transaction_required: caller must supply an active database transaction")
            return None
        if not self._session.get_transaction().is_active:
            self._note("transaction_inactive: adapter requires a live caller-owned transaction")
            return None
        result = await self._session.execute(_CONTEXT_SQL)
        row = result.mappings().one_or_none()
        if row is None:
            self._note("server_context_missing: authenticated tenant and correlation GUCs are required")
            return None
        tenant_id = row.get("tenant_id")
        subject_id = row.get("subject_id")
        correlation_id = row.get("correlation_id")
        transaction_id = row.get("transaction_id")
        transaction_isolation = row.get("transaction_isolation")
        authenticated = row.get("authenticated")
        if not all(isinstance(value, str) and value.strip() for value in (
            tenant_id, subject_id, correlation_id, transaction_id,
        )) or authenticated != "true" or not transaction_id.isdigit():
            self._note("server_context_invalid: tenant, authenticated subject, correlation, and DB transaction ID are required")
            return None
        if transaction_isolation != "serializable":
            self._note("transaction_isolation_insufficient: exact proof reads require SERIALIZABLE transaction isolation")
            return None
        return MigrationEvidenceContext(
            resolved_tenant_id=tenant_id,
            authenticated=True,
            transaction_id=f"pg:{transaction_id}",
            correlation_id=correlation_id,
        )

    async def resolve(self, query: DefinitionVersionQuery) -> DefinitionResolution:
        """Implement the read-only resolver port; the facade owns M04/M04E admission."""
        context = await self.server_context()
        if context is None:
            return self._negative(DefinitionResolutionReason.INVALID_DEFINITION_HISTORY)
        return await self._resolve_exact(query, context)

    async def _resolve_exact(
        self,
        query: DefinitionVersionQuery,
        context: MigrationEvidenceContext,
    ) -> DefinitionResolution:
        """Read and compare the exact target snapshot in this DB transaction."""
        if query.tenant_id != context.resolved_tenant_id:
            return self._negative(DefinitionResolutionReason.CROSS_TENANT_DEFINITION)
        if query.expected_artifact_digest is not None and not _SHA256.fullmatch(
            query.expected_artifact_digest
        ):
            return self._negative(DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT)
        if not self._table_rls_ready("m04e_dynamic_app_definitions") or not self._table_rls_ready(
            "m04e_dynamic_app_versions"
        ):
            self._note(_M04E_RLS_BLOCKER)
            return self._negative(DefinitionResolutionReason.INVALID_DEFINITION_HISTORY)

        definitions = await self._all(
            select(DynamicAppDefinitionFoundationRow).where(
                DynamicAppDefinitionFoundationRow.tenant_id == context.resolved_tenant_id,
                DynamicAppDefinitionFoundationRow.app_id == query.app_id,
            )
        )
        if not definitions:
            return self._negative(DefinitionResolutionReason.MISSING_DEFINITION, missing=True)
        if len(definitions) != 1:
            return self._negative(DefinitionResolutionReason.INVALID_DEFINITION_HISTORY)
        definition_row = definitions[0]

        versions = await self._all(
            select(DynamicAppDefinitionVersionFoundationRow).where(
                DynamicAppDefinitionVersionFoundationRow.tenant_id == context.resolved_tenant_id,
                DynamicAppDefinitionVersionFoundationRow.app_id == query.app_id,
                DynamicAppDefinitionVersionFoundationRow.definition_id == definition_row.id,
                DynamicAppDefinitionVersionFoundationRow.manifest_version == query.manifest_version,
            )
        )
        if not versions:
            return self._negative(DefinitionResolutionReason.MISSING_VERSION, missing=True)
        if len(versions) != 1:
            return self._negative(DefinitionResolutionReason.AMBIGUOUS_VERSION)
        selected = versions[0]

        history = await self._all(
            select(DynamicAppDefinitionVersionFoundationRow).where(
                DynamicAppDefinitionVersionFoundationRow.tenant_id == context.resolved_tenant_id,
                DynamicAppDefinitionVersionFoundationRow.app_id == query.app_id,
                DynamicAppDefinitionVersionFoundationRow.definition_id == definition_row.id,
            )
        )
        if not history or any(
            row.definition_id != definition_row.id
            or row.tenant_id != context.resolved_tenant_id
            or row.app_id != query.app_id
            for row in history
        ):
            return self._negative(DefinitionResolutionReason.CROSS_DEFINITION_VERSION)
        if len({row.manifest_version for row in history}) != len(history):
            return self._negative(DefinitionResolutionReason.AMBIGUOUS_VERSION)
        if len({row.artifact_digest for row in history}) != len(history):
            return self._negative(DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        matching = [row for row in history if row.id == selected.id]
        if len(matching) != 1 or not self._version_snapshot_equal(matching[0], selected):
            return self._negative(DefinitionResolutionReason.INVALID_VERSION_HISTORY)

        if (
            definition_row.tenant_id != query.tenant_id
            or definition_row.app_id != query.app_id
            or selected.tenant_id != query.tenant_id
            or selected.app_id != query.app_id
            or selected.definition_id != definition_row.id
            or selected.manifest_version != query.manifest_version
        ):
            return self._negative(DefinitionResolutionReason.CROSS_DEFINITION_VERSION)
        if query.expected_artifact_digest is not None and (
            selected.artifact_digest != query.expected_artifact_digest
        ):
            return self._negative(DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT)
        if not _SHA256.fullmatch(selected.artifact_digest) or not selected.artifact_uri:
            return self._negative(DefinitionResolutionReason.INVALID_VERSION_HISTORY)

        if definition_row.lifecycle not in {"draft", "published"} or selected.lifecycle not in {
            "draft", "published",
        }:
            return self._negative(DefinitionResolutionReason.DISALLOWED_VERSION_STATUS)
        if definition_row.lifecycle == "published" and selected.lifecycle != "published":
            return self._negative(DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH)
        if selected.lifecycle == "published" and (
            definition_row.lifecycle != "published"
            or definition_row.current_version_id != selected.id
            or selected.published_at is None
        ):
            return self._negative(DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH)
        if definition_row.lifecycle != "published" and selected.published_at is not None:
            return self._negative(DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        if definition_row.current_version_id not in {None, selected.id}:
            return self._negative(DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH)
        if definition_row.lifecycle == "published" and definition_row.current_version_id is None:
            return self._negative(DefinitionResolutionReason.INVALID_DEFINITION_HISTORY)

        current_row = None
        if definition_row.current_version_id is not None:
            current_rows = await self._all(
                select(DynamicAppDefinitionVersionFoundationRow).where(
                    DynamicAppDefinitionVersionFoundationRow.id == definition_row.current_version_id,
                    DynamicAppDefinitionVersionFoundationRow.definition_id == definition_row.id,
                    DynamicAppDefinitionVersionFoundationRow.tenant_id == query.tenant_id,
                    DynamicAppDefinitionVersionFoundationRow.app_id == query.app_id,
                )
            )
            if len(current_rows) != 1:
                return self._negative(DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH)
            current_row = current_rows[0]
            if not self._current_equals_selected(current_row, selected):
                return self._negative(DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH)

        try:
            definition = self._definition(definition_row)
            version = self._version(selected)
        except (AttributeError, TypeError, ValueError, KeyError):
            return self._negative(DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        if current_row is not None and current_row.lifecycle != "published":
            return self._negative(DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH)
        if self._artifacts is None:
            self._note("m04e_artifact_content_adapter_missing: no production content adapter is bound for byte-level artifact verification")
            return self._negative(DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        resolved_uri = await self._artifacts.resolve_artifact_uri(
            tenant_id=query.tenant_id,
            app_definition_id=definition_row.id,
            app_version_id=selected.id,
        )
        if resolved_uri is None or resolved_uri != selected.artifact_uri:
            self._note("artifact_uri_source_mismatch: persisted and content-store identity must agree")
            return self._negative(DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        artifact_bytes = await self._artifacts.read_artifact(
            tenant_id=query.tenant_id,
            app_definition_id=definition_row.id,
            app_version_id=selected.id,
            artifact_uri=resolved_uri,
            max_bytes=64 * 1024 * 1024,
        )
        if not isinstance(artifact_bytes, bytes) or len(artifact_bytes) > 64 * 1024 * 1024:
            self._note("artifact_content_unavailable: exact version bytes were not read")
            return self._negative(DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        if "sha256:" + hashlib.sha256(artifact_bytes).hexdigest() != selected.artifact_digest:
            self._note("artifact_content_digest_mismatch: stored digest differs from exact version bytes")
            return self._negative(DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT)

        return DefinitionResolution(
            outcome=DefinitionResolutionOutcome.RESOLVED,
            definition=definition,
            version=version,
            reference=DefinitionVersionRef(
                tenant_id=query.tenant_id,
                app_id=query.app_id,
                app_definition_id=definition_row.id,
                app_version_id=selected.id,
                manifest_version=selected.manifest_version,
                artifact_digest=selected.artifact_digest,
            ),
            evidence={
                "database_transaction_id": context.transaction_id or "",
                "correlation_id": context.correlation_id or "",
            },
        )

    async def prove_active_scope(
        self,
        *,
        context: MigrationEvidenceContext,
        tenant_id: str,
        app_id: str,
    ) -> ActiveTypedScopeProof | None:
        """Check live typed registry/parent rows, but require missing M04 gate proof."""
        actual = await self.server_context()
        if not self._same_context(actual, context) or tenant_id != context.resolved_tenant_id:
            self._note("scope_context_mismatch: scope proof must share the exact trusted transaction")
            return None
        if not await self._table_rls_ready("m04e_dynamic_app_definitions"):
            self._note("m04e_definition_version_rls_missing: definition ownership must be read under forced tenant RLS")
            return None
        definitions = await self._all(
            select(DynamicAppDefinitionFoundationRow).where(
                DynamicAppDefinitionFoundationRow.tenant_id == tenant_id,
                DynamicAppDefinitionFoundationRow.app_id == app_id,
            )
        )
        if len(definitions) != 1:
            self._note("scope_definition_ambiguous_or_missing: exact tenant/app definition is required")
            return None
        definition = definitions[0]
        if definition.scope_kind not in {"tenant", "cabinet", "project"}:
            self._note("scope_kind_not_positive: plugin_instance and unknown scope kinds are denied")
            return None
        scope_tables = [
            "scope_registry", "scope_owner_tenant_anchors", "companies",
            "scope_owner_mappings", "cabinet_company_grants",
        ]
        if definition.scope_kind in {"cabinet", "project"}:
            scope_tables.extend(("cabinet_instances", "scope_owner_cabinet_anchors"))
        if definition.scope_kind == "project":
            scope_tables.extend(("scope_owner_project_anchors", "projects"))
        rls_results = [await self._table_rls_ready(table, active=True) for table in scope_tables]
        if not all(rls_results):
            self._note(_M04_SCOPE_RLS_BLOCKER)
            return None
        registry_rows = await self._all(
            select(ScopeRegistryRow).where(
                ScopeRegistryRow.tenant_id == tenant_id,
                ScopeRegistryRow.scope_kind == definition.scope_kind,
                ScopeRegistryRow.scope_id == definition.scope_id,
                ScopeRegistryRow.owner_state == "active",
            )
        )
        if len(registry_rows) != 1:
            self._note("active_scope_registry_missing_or_ambiguous: exactly one active typed M04 registry row is required")
            return None
        registry = registry_rows[0]
        if not self._has_provenance(registry, require_checkpoint=True):
            self._note("active_scope_registry_provenance_invalid: source identity, checkpoint, and SHA-256 digest are required")
            return None
        mappings = await self._all(
            select(ScopeOwnerMappingRow).where(
                ScopeOwnerMappingRow.scope_registry_id == registry.id,
                ScopeOwnerMappingRow.mapping_state == "mapped",
            )
        )
        if (
            len(mappings) != 1
            or mappings[0].source_kind != registry.source_kind
            or mappings[0].source_id != registry.source_id
            or mappings[0].schema_version != registry.schema_version
            or mappings[0].source_payload_digest != registry.payload_digest
            or not _SHA256.fullmatch(mappings[0].canonical_payload_digest or "")
            or not _SHA256.fullmatch(mappings[0].source_payload_digest)
            or not mappings[0].checkpoint
        ):
            self._note("active_scope_source_mapping_missing_or_inconsistent: one checkpointed mapped source digest must match the active registry row")
            return None
        if not await self._active_anchor_evidence(registry, definition):
            self._note("active_scope_parent_evidence_missing_or_inconsistent: live tenant/cabinet/project parent rows must match")
            return None

        # Source mapping is schema-versioned, but its ORM row omits canonical
        # schema identity and M04 has no cursor CAS or reviewed policy manifest.
        self._note(_M04_SCOPE_MAPPING_LINEAGE_BLOCKER)
        self._note(_M04_SCOPE_RLS_BLOCKER)
        self._note(_M04_ACTOR_MEMBERSHIP_BLOCKER)
        # M04-02 has no registered immutable partition promotion row containing
        # the required gate digest. A live registry row alone is not that gate.
        self._note(_M04_SCOPE_PROMOTION_BLOCKER)
        return None

    async def prove_and_record(
        self,
        *,
        context: MigrationEvidenceContext,
        query: DefinitionVersionQuery,
        resolution: DefinitionResolution,
        scope: ActiveTypedScopeProof,
        artifact_uri: str,
        artifact_content_digest: str,
    ) -> DefinitionVersionProof | None:
        """Re-read persisted identity/content and refuse proof without durable audit."""
        actual = await self.server_context()
        if (
            not self._same_context(actual, context)
            or query.tenant_id != context.resolved_tenant_id
            or resolution.outcome is not DefinitionResolutionOutcome.RESOLVED
            or resolution.definition is None
            or resolution.version is None
            or resolution.reference is None
            or not self._scope_identity_matches(scope, context, query)
            or scope.app_definition_id != resolution.reference.app_definition_id
            or scope.scope_kind != resolution.definition.scope.kind.value
            or scope.scope_id != resolution.definition.scope.id
        ):
            self._note("proof_context_or_identity_mismatch: caller structures are not persistence evidence")
            return None

        persisted = await self._resolve_exact(query, context)
        if (
            persisted.outcome is not DefinitionResolutionOutcome.RESOLVED
            or persisted.reference != resolution.reference
            or persisted.definition != resolution.definition
            or persisted.version != resolution.version
        ):
            self._note("proof_recheck_mismatch: exact target rows changed or no longer resolve")
            return None
        version_row = await self._one_version_row(query, resolution.reference.app_definition_id)
        if version_row is None or (
            version_row.artifact_uri != artifact_uri
            or version_row.artifact_digest != artifact_content_digest
            or not _SHA256.fullmatch(artifact_content_digest)
        ):
            self._note("artifact_equality_unproven: persisted URI/digest must equal bytes read from the trusted content store")
            return None
        if self._artifacts is None:
            self._note(_ARTIFACT_CONTENT_BLOCKER)
            return None
        resolved_uri = await self._artifacts.resolve_artifact_uri(
            tenant_id=query.tenant_id,
            app_definition_id=resolution.reference.app_definition_id,
            app_version_id=resolution.reference.app_version_id,
        )
        if resolved_uri is None or resolved_uri != version_row.artifact_uri:
            self._note("artifact_uri_source_mismatch: content adapter URI must equal the exact persisted version URI")
            return None
        content = await self._artifacts.read_artifact(
            tenant_id=query.tenant_id,
            app_definition_id=resolution.reference.app_definition_id,
            app_version_id=resolution.reference.app_version_id,
            artifact_uri=resolved_uri,
            max_bytes=64 * 1024 * 1024,
        )
        if not isinstance(content, bytes) or len(content) > 64 * 1024 * 1024:
            self._note("artifact_content_unavailable: exact version bytes were not read")
            return None
        actual_digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if actual_digest != version_row.artifact_digest or actual_digest != artifact_content_digest:
            self._note("artifact_content_digest_mismatch: stored digest differs from exact artifact bytes")
            return None

        # The registered M04E promotion row only accepts blocked/quarantined
        # gate outcomes; CanonicalGrantAuditRow is unregistered and lacks the
        # resolver event's transaction/current-link/artifact semantics. No proof
        # is returned or digest synthesized until an approved durable audit gate.
        self._note(_M04E_AUDIT_BLOCKER)
        self._note(_M04E_AUDIT_RLS_BLOCKER)
        return None

    async def record_denial(
        self,
        *,
        context: MigrationEvidenceContext,
        query: DefinitionVersionQuery | None,
        reason: DefinitionResolutionReason,
    ) -> str | None:
        """Do not claim a durable denial digest without an approved audit schema."""
        actual = await self.server_context()
        if not self._same_context(actual, context):
            self._note("denial_context_mismatch: transaction and correlation must come from DB state")
        self._note(_M04E_AUDIT_BLOCKER)
        self._note(_M04E_AUDIT_RLS_BLOCKER)
        return None

    async def _active_anchor_evidence(
        self,
        registry: ScopeRegistryRow,
        definition: DynamicAppDefinitionFoundationRow,
    ) -> bool:
        tenant_rows = await self._all(
            select(CanonicalTenantAnchorRow).where(
                CanonicalTenantAnchorRow.tenant_id == registry.tenant_id,
                CanonicalTenantAnchorRow.tenant_type == "company",
                CanonicalTenantAnchorRow.owner_state == "active",
            )
        )
        companies = await self._all(
            select(CompanyRow).where(
                CompanyRow.id == registry.tenant_id,
                CompanyRow.status == "active",
                CompanyRow.deleted_at.is_(None),
            )
        )
        if len(tenant_rows) != 1 or len(companies) != 1:
            return False
        if registry.scope_kind == "tenant":
            return (
                registry.scope_id == registry.tenant_id
                and definition.scope_id == registry.tenant_id
                and registry.cabinet_id is None
                and registry.project_id is None
                and self._has_provenance(tenant_rows[0])
            )
        if registry.scope_kind == "cabinet":
            anchors = await self._all(
                select(ScopeOwnerCabinetAnchorRow).where(
                    ScopeOwnerCabinetAnchorRow.cabinet_id == registry.cabinet_id,
                    ScopeOwnerCabinetAnchorRow.tenant_id == registry.tenant_id,
                    ScopeOwnerCabinetAnchorRow.tenant_type == "company",
                    ScopeOwnerCabinetAnchorRow.owner_state == "active",
                )
            )
            grants = await self._all(
                select(CabinetCompanyGrantRow).where(
                    CabinetCompanyGrantRow.cabinet_id == registry.cabinet_id,
                    CabinetCompanyGrantRow.company_id == registry.tenant_id,
                    CabinetCompanyGrantRow.status == "active",
                )
            )
            cabinet_rows = await self._all(
                select(CabinetInstanceRow).where(
                    CabinetInstanceRow.id == registry.cabinet_id,
                    CabinetInstanceRow.status == "active",
                    CabinetInstanceRow.company_id == registry.tenant_id,
                )
            )
            return bool(
                registry.cabinet_id == registry.scope_id == definition.scope_id
                and registry.project_id is None
                and len(anchors) == 1 and len(grants) == 1 and len(cabinet_rows) == 1
                and self._has_provenance(tenant_rows[0])
                and self._has_provenance(anchors[0])
            )
        if registry.scope_kind == "project":
            anchors = await self._all(
                select(ScopeOwnerProjectAnchorRow).where(
                    ScopeOwnerProjectAnchorRow.project_id == registry.project_id,
                    ScopeOwnerProjectAnchorRow.tenant_id == registry.tenant_id,
                    ScopeOwnerProjectAnchorRow.tenant_type == "company",
                    ScopeOwnerProjectAnchorRow.cabinet_id == registry.cabinet_id,
                    ScopeOwnerProjectAnchorRow.project_status == "active",
                    ScopeOwnerProjectAnchorRow.owner_state == "active",
                )
            )
            projects = await self._all(
                select(ProjectRow).where(
                    ProjectRow.id == registry.project_id,
                    ProjectRow.company_id == registry.tenant_id,
                    ProjectRow.cabinet_id == registry.cabinet_id,
                    ProjectRow.status == "active",
                )
            )
            cabinets = await self._all(
                select(ScopeOwnerCabinetAnchorRow).where(
                    ScopeOwnerCabinetAnchorRow.cabinet_id == registry.cabinet_id,
                    ScopeOwnerCabinetAnchorRow.tenant_id == registry.tenant_id,
                    ScopeOwnerCabinetAnchorRow.tenant_type == "company",
                    ScopeOwnerCabinetAnchorRow.owner_state == "active",
                )
            )
            grants = await self._all(
                select(CabinetCompanyGrantRow).where(
                    CabinetCompanyGrantRow.cabinet_id == registry.cabinet_id,
                    CabinetCompanyGrantRow.company_id == registry.tenant_id,
                    CabinetCompanyGrantRow.status == "active",
                )
            )
            cabinet_rows = await self._all(
                select(CabinetInstanceRow).where(
                    CabinetInstanceRow.id == registry.cabinet_id,
                    CabinetInstanceRow.status == "active",
                    CabinetInstanceRow.company_id == registry.tenant_id,
                )
            )
            return bool(
                registry.project_id == registry.scope_id == definition.scope_id
                and registry.cabinet_id
                and len(anchors) == 1 and len(projects) == 1
                and len(cabinets) == 1 and len(grants) == 1 and len(cabinet_rows) == 1
                and self._has_provenance(tenant_rows[0])
                and self._has_provenance(anchors[0])
                and self._has_provenance(cabinets[0])
            )
        return False

    async def _one_version_row(
        self,
        query: DefinitionVersionQuery,
        definition_id: str,
    ) -> DynamicAppDefinitionVersionFoundationRow | None:
        rows = await self._all(
            select(DynamicAppDefinitionVersionFoundationRow).where(
                DynamicAppDefinitionVersionFoundationRow.tenant_id == query.tenant_id,
                DynamicAppDefinitionVersionFoundationRow.app_id == query.app_id,
                DynamicAppDefinitionVersionFoundationRow.definition_id == definition_id,
                DynamicAppDefinitionVersionFoundationRow.manifest_version == query.manifest_version,
            )
        )
        return rows[0] if len(rows) == 1 else None

    async def _all(self, statement) -> Sequence[Any]:
        result = await self._session.execute(statement)
        return tuple(result.scalars().all())

    async def _table_rls_ready(self, table_name: str, *, active: bool = False) -> bool:
        """Require forced, non-bypass RLS with exclusively scoped read policies."""
        result = await self._session.execute(_RLS_SQL, {"table_name": table_name})
        rows = result.mappings().all()
        if not rows:
            return False
        first = rows[0]
        if not first.get("enabled") or not first.get("forced"):
            return False
        if first.get("is_superuser") or first.get("bypass_rls"):
            return False
        policies = [row for row in rows if row.get("command") in {"SELECT", "ALL"}]
        if not policies:
            return False
        usable = []
        for policy in policies:
            roles = policy.get("roles")
            current_role = policy.get("current_role")
            if not isinstance(roles, (tuple, list)) or not isinstance(current_role, str):
                return False
            if not roles or any(str(role).lower() not in {"public", current_role.lower()} for role in roles):
                return False
            usable.append(policy)
        if not usable:
            return False
        for policy in usable:
            if str(policy.get("permissive") or "").upper() != "RESTRICTIVE":
                return False
            if policy.get("command") not in {"SELECT", "ALL"}:
                return False
            qualifier = policy.get("qualifier")
            if not isinstance(qualifier, str):
                return False
            normalized = " ".join(qualifier.lower().split())
            if " or " in normalized or not _TENANT_POLICY.search(normalized):
                return False
            if policy.get("command") in {"ALL", "UPDATE"}:
                with_check = policy.get("with_check")
                if not isinstance(with_check, str) or not _TENANT_POLICY.search(with_check.lower()):
                    return False
                if active and not _ACTIVE_POLICY.search(with_check.lower()):
                    return False
            if active and not _ACTIVE_POLICY.search(normalized):
                return False
        if table_name.startswith("m04e_"):
            manifest = self._definition_rls_manifest
        elif table_name in _M04_OWNER_TABLES:
            manifest = self._scope_rls_manifest
        else:
            return True
        if manifest is None or manifest.database_role != str(first.get("current_role")):
            return False
        reviewed_digest = manifest.digest_for(table_name)
        if reviewed_digest is None or self._policy_digest(rows) != reviewed_digest:
            return False
        return True

    @staticmethod
    def _policy_digest(rows: Sequence[Mapping[str, Any]]) -> str:
        material = [
            {
                "policyname": str(row["policyname"]),
                "permissive": str(row["permissive"]),
                "roles": sorted(str(role) for role in (row["roles"] or ())),
                "cmd": str(row["command"]),
                "qual": row["qualifier"],
                "with_check": row["with_check"],
            }
            for row in rows
        ]
        material.sort(key=lambda item: item["policyname"])
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _has_provenance(row: Any, *, require_checkpoint: bool = False) -> bool:
        return bool(
            row.source_kind and row.source_id and row.schema_version
            and (not require_checkpoint or bool(getattr(row, "checkpoint", None)))
            and isinstance(row.payload_digest, str)
            and _SHA256.fullmatch(row.payload_digest)
        )

    @staticmethod
    def _same_context(
        actual: MigrationEvidenceContext | None,
        supplied: MigrationEvidenceContext,
    ) -> bool:
        return bool(
            actual is not None
            and actual.authenticated
            and supplied.usable
            and actual.resolved_tenant_id == supplied.resolved_tenant_id
            and actual.transaction_id == supplied.transaction_id
            and actual.correlation_id == supplied.correlation_id
        )

    @staticmethod
    def _scope_identity_matches(
        scope: ActiveTypedScopeProof,
        context: MigrationEvidenceContext,
        query: DefinitionVersionQuery,
    ) -> bool:
        return bool(
            scope.active
            and scope.tenant_id == query.tenant_id == context.resolved_tenant_id
            and scope.app_id == query.app_id
            and scope.scope_kind in {"tenant", "cabinet", "project"}
            and scope.scope_id.strip()
            and scope.transaction_id == context.transaction_id
            and scope.correlation_id == context.correlation_id
            and scope.app_definition_id.strip()
        )

    @staticmethod
    def _version_snapshot_equal(left, right) -> bool:
        return all(getattr(left, name) == getattr(right, name) for name in (
            "id", "definition_id", "tenant_id", "app_id", "manifest_version",
            "artifact_digest", "artifact_uri", "manifest", "requested_capabilities",
            "lifecycle", "published_at", "source_snapshot_id", "source_digest",
        ))

    @staticmethod
    def _current_equals_selected(current, selected) -> bool:
        return all(getattr(current, name) == getattr(selected, name) for name in (
            "id", "definition_id", "tenant_id", "app_id", "manifest_version",
            "artifact_digest", "artifact_uri", "lifecycle", "published_at",
            "source_snapshot_id", "source_digest",
        ))

    @staticmethod
    def _definition(row: DynamicAppDefinitionFoundationRow) -> AppDefinition:
        return AppDefinition(
            id=row.id,
            tenant_id=row.tenant_id,
            app_id=row.app_id,
            display_name=row.display_name,
            description=row.description,
            scope=AppScope(kind=AppScopeKind(row.scope_kind), id=row.scope_id),
            status=AppLifecycleStatus(row.lifecycle),
            current_version_id=row.current_version_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _version(row: DynamicAppDefinitionVersionFoundationRow) -> AppVersion:
        if not isinstance(row.manifest, Mapping):
            raise ValueError("persisted manifest must be an object")
        requested = row.requested_capabilities
        manifest_requested = row.manifest.get("requestedCapabilities")
        if not isinstance(requested, list) or not isinstance(manifest_requested, list):
            raise ValueError("requested capabilities must be arrays")
        if requested != manifest_requested:
            raise ValueError("persisted capabilities disagree with manifest")
        return AppVersion(
            id=row.id,
            app_definition_id=row.definition_id,
            manifest_version=row.manifest_version,
            artifact_digest=row.artifact_digest,
            manifest=dict(row.manifest),
            requested_capabilities=tuple(RequestedCapability(**item) for item in requested),
            status=AppLifecycleStatus(row.lifecycle),
            created_at=row.created_at,
            published_at=row.published_at,
        )

    @staticmethod
    def _negative(
        reason: DefinitionResolutionReason,
        *,
        missing: bool = False,
    ) -> DefinitionResolution:
        return DefinitionResolution(
            outcome=(DefinitionResolutionOutcome.MISSING if missing else DefinitionResolutionOutcome.QUARANTINED),
            reason=reason,
        )

    def _note(self, blocker: str) -> None:
        self._blockers.append(blocker)


__all__ = [
    "M04E_PERSISTENCE_BLOCKERS",
    "M04EDefinitionVersionPersistenceAdapter",
    "ReviewedM04EDefinitionRlsManifest",
]

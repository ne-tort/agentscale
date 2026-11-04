"""Behavioral repository tests for the M04E persistence adapter; do not run during freeze."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.sql.elements import BinaryExpression, TextClause

from prodavan.application.dynamic_apps_definition.contracts import (
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
    DefinitionVersionQuery,
)
from prodavan.application.migration.m04e_definition_version.repository import (
    ActiveTypedScopeProof,
    MigrationEvidenceContext,
)
from prodavan.infrastructure.persistence.models.cabinets import CabinetCompanyGrantRow, CabinetInstanceRow
from prodavan.infrastructure.persistence.migration_definition_version.adapter import (
    M04EDefinitionVersionPersistenceAdapter,
    ReviewedM04EDefinitionRlsManifest,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    DynamicAppDefinitionFoundationRow,
    DynamicAppDefinitionVersionFoundationRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.persistence.models.owner_registry import (
    CanonicalTenantAnchorRow,
    ScopeOwnerMappingRow,
    ScopeOwnerCabinetAnchorRow,
    ScopeOwnerProjectAnchorRow,
    ScopeRegistryRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.persistence.migration_scope.adapter import (
    ReviewedScopeRlsManifest,
)

TENANT = "tenant-m04e"
APP_ID = "demo-app"
DEFINITION_ID = "definition-m04e"
VERSION_ID = "version-m04e-1"
MANIFEST_VERSION = "1.0.0"
ARTIFACT_URI = "content://tenant-m04e/definition-m04e/version-m04e-1"
ARTIFACT_BYTES = b"registered-m04e-artifact"
ARTIFACT_DIGEST = "sha256:" + hashlib.sha256(ARTIFACT_BYTES).hexdigest()
NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def _definition(**overrides):
    values = {
        "id": DEFINITION_ID,
        "tenant_id": TENANT,
        "app_id": APP_ID,
        "display_name": "Demo app",
        "description": None,
        "scope_kind": "tenant",
        "scope_id": TENANT,
        "lifecycle": "published",
        "current_version_id": VERSION_ID,
        "source_snapshot_id": "snapshot-1",
        "source_digest": "sha256:" + "a" * 64,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _version(**overrides):
    values = {
        "id": VERSION_ID,
        "definition_id": DEFINITION_ID,
        "tenant_id": TENANT,
        "app_id": APP_ID,
        "manifest_version": MANIFEST_VERSION,
        "artifact_digest": ARTIFACT_DIGEST,
        "artifact_uri": ARTIFACT_URI,
        "manifest": {
            "protocol": "1.0",
            "appId": APP_ID,
            "version": MANIFEST_VERSION,
            "displayName": "Demo app",
            "entrypoints": {"ui": {"type": "text"}},
            "requestedCapabilities": [],
        },
        "requested_capabilities": [],
        "lifecycle": "published",
        "source_snapshot_id": "snapshot-1",
        "source_digest": "sha256:" + "b" * 64,
        "created_at": NOW,
        "published_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _MappingView:
    """Mirror ``sqlalchemy.engine.Result.mappings()``: yield mapping rows only."""

    def __init__(self, rows):
        self._rows = tuple(rows)

    def all(self):
        return list(self._rows)

    def one_or_none(self):
        if len(self._rows) > 1:
            raise AssertionError("context query must be unique")
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows=(), mapping_rows=()):
        self._rows = tuple(rows)
        self._mapping_rows = tuple(mapping_rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def mappings(self):
        # Returning ``self`` here made ``mappings().all()`` yield the ORM-row list,
        # so the adapter's ``pg_policies`` read always saw zero rows and every RLS
        # gate silently returned "not ready" regardless of the fixture state.
        return _MappingView(self._mapping_rows or self._rows)


class _FakeSession:
    """Interpret SQLAlchemy WHERE equality/IS predicates for repository queries."""

    def __init__(self, rows, *, context=None, rls: tuple[Any, ...] | None = None):
        self.rows = rows
        self.context = context or {
            "tenant_id": TENANT,
            "subject_id": "authenticated-subject",
            "authenticated": "true",
            "correlation_id": "corr-db-1",
            "transaction_isolation": "serializable",
            "transaction_id": "234567",
        }
        self.rls = rls if rls is not None else (True, True, "(tenant_id = current_setting('app.tenant_id', true))")
        self.statements = []
        self._active = True

    def in_transaction(self):
        return self._active

    def get_transaction(self):
        return SimpleNamespace(is_active=self._active)

    async def execute(self, statement, params=None):
        self.statements.append((statement, params))
        if isinstance(statement, TextClause):
            sql = str(statement)
            if "txid_current()" in sql:
                return _Result(mapping_rows=[self.context] if self.context else [])
            if "pg_class" in sql:
                enabled, forced, qualifier = self.rls[:3]
                table_name = params["table_name"]
                if table_name in {
                    "scope_registry", "scope_owner_tenant_anchors",
                    "scope_owner_cabinet_anchors", "scope_owner_project_anchors",
                }:
                    qualifier = "(tenant_id = current_setting('app.tenant_id', true) AND owner_state = 'active')"
                elif table_name == "scope_owner_mappings":
                    qualifier = "(scope_registry_id IN (SELECT id FROM scope_registry WHERE tenant_id = current_setting('app.tenant_id', true) AND mapping_state = 'mapped'))"
                elif table_name == "companies":
                    qualifier = "(id = current_setting('app.tenant_id', true) AND status = 'active')"
                elif table_name in {"cabinet_company_grants", "cabinet_instances", "projects"}:
                    qualifier = "(company_id = current_setting('app.tenant_id', true) AND status = 'active')"
                if len(self.rls) > 5 and isinstance(self.rls[5], dict) and table_name in self.rls[5]:
                    enabled, forced, qualifier = self.rls[5][table_name]
                return _Result(mapping_rows=[{
                    "enabled": enabled,
                    "forced": forced,
                    "command": "SELECT",
                    "qualifier": qualifier,
                    "with_check": None,
                    "policyname": f"tenant_policy_{table_name}",
                    "roles": ["public"],
                    "permissive": (
                        self.rls[7].get(table_name, "RESTRICTIVE")
                        if len(self.rls) > 7 and isinstance(self.rls[7], dict)
                        else self.rls[6] if len(self.rls) > 6 else "RESTRICTIVE"
                    ),
                    "current_role": "prodavan_api",
                    "is_superuser": self.rls[3] if len(self.rls) > 3 else False,
                    "bypass_rls": self.rls[4] if len(self.rls) > 4 else False,
                }] if enabled else [])
            raise AssertionError(f"unexpected text query: {sql}")

        description = statement.column_descriptions[0]
        model = description["entity"]
        if model is DynamicAppDefinitionFoundationRow:
            rows = self.rows.get("definitions", ())
        elif model is DynamicAppDefinitionVersionFoundationRow:
            rows = self.rows.get("versions", ())
        elif model is ScopeRegistryRow:
            rows = self.rows.get("scopes", ())
        elif model is ScopeOwnerMappingRow:
            rows = self.rows.get("mappings", ())
        elif model is CanonicalTenantAnchorRow:
            rows = self.rows.get("tenant_anchors", ())
        elif model is ScopeOwnerCabinetAnchorRow:
            rows = self.rows.get("cabinet_anchors", ())
        elif model is ScopeOwnerProjectAnchorRow:
            rows = self.rows.get("project_anchors", ())
        elif model is CompanyRow:
            rows = self.rows.get("companies", ())
        elif model is CabinetCompanyGrantRow:
            rows = self.rows.get("cabinet_grants", ())
        elif model is CabinetInstanceRow:
            rows = self.rows.get("cabinets", ())
        elif model is ProjectRow:
            rows = self.rows.get("projects", ())
        else:
            rows = ()

        for criterion in statement.whereclause.clauses:
            if not isinstance(criterion, BinaryExpression):
                continue
            key = criterion.left.key
            value = getattr(criterion.right, "value", None)
            operator = criterion.operator.__name__
            if operator in {"eq", "is_"}:
                rows = tuple(row for row in rows if getattr(row, key) == value)
        return _Result(rows)


class _ArtifactStore:
    def __init__(self, *, uri=ARTIFACT_URI, content=ARTIFACT_BYTES):
        self.uri = uri
        self.content = content
        self.calls = []

    async def resolve_artifact_uri(self, *, tenant_id, app_definition_id, app_version_id):
        self.calls.append(("uri", tenant_id, app_definition_id, app_version_id))
        return self.uri

    async def read_artifact(self, *, tenant_id, app_definition_id, app_version_id, artifact_uri, max_bytes):
        self.calls.append(("read", tenant_id, app_definition_id, app_version_id, artifact_uri, max_bytes))
        if artifact_uri != self.uri:
            return None
        return self.content


def _tenant_scope_fixture():
    return {
        "tenant_anchors": (SimpleNamespace(
            tenant_id=TENANT,
            tenant_type="company",
            owner_state="active",
            source_kind="tenant_source",
            source_id=TENANT,
            schema_version="owner.v1",
            payload_digest="sha256:" + "d" * 64,
        ),),
        "companies": (SimpleNamespace(id=TENANT, status="active", deleted_at=None),),
    }


def _rls_policy():
    return "(tenant_id = current_setting('app.tenant_id', true))"


def _scope_rls_manifest():
    tables = (
        "scope_registry", "scope_owner_tenant_anchors", "scope_owner_cabinet_anchors",
        "scope_owner_project_anchors", "scope_owner_mappings", "scope_owner_quarantine",
        "scope_owner_audit",
    )
    digests = []
    for table in tables:
        qualifier = _rls_policy()
        if table in {
            "scope_registry", "scope_owner_tenant_anchors",
            "scope_owner_cabinet_anchors", "scope_owner_project_anchors",
        }:
            qualifier = "(tenant_id = current_setting('app.tenant_id', true) AND owner_state = 'active')"
        elif table == "scope_owner_mappings":
            qualifier = "(scope_registry_id IN (SELECT id FROM scope_registry WHERE tenant_id = current_setting('app.tenant_id', true) AND mapping_state = 'mapped'))"
        material = [{
            "policyname": f"tenant_policy_{table}",
            "permissive": "RESTRICTIVE",
            "roles": ["public"],
            "cmd": "SELECT",
            "qual": qualifier,
            "with_check": None,
        }]
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digests.append((table, "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()))
    return ReviewedScopeRlsManifest(
        review_id="fixture-review",
        database_role="prodavan_api",
        policy_digests=tuple(digests),
    )


def _definition_rls_manifest():
    digests = []
    for table in ("m04e_dynamic_app_definitions", "m04e_dynamic_app_versions"):
        material = [{
            "policyname": f"tenant_policy_{table}",
            "permissive": "RESTRICTIVE",
            "roles": ["public"],
            "cmd": "SELECT",
            "qual": _rls_policy(),
            "with_check": None,
        }]
        material.sort(key=lambda item: item["policyname"])
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digests.append((table, "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()))
    return ReviewedM04EDefinitionRlsManifest(
        review_id="fixture-m04e-review",
        database_role="prodavan_api",
        policy_digests=tuple(digests),
    )


def _adapter(
    rows=None,
    *,
    context=None,
    artifacts=None,
    rls: bool | tuple[Any, ...] = True,
    scope_rls_manifest=None,
    definition_rls_manifest=None,
):
    rls_state = (
        (True, True, _rls_policy()) if rls is True
        else (False, False, None) if rls is False
        else rls
    )
    session = _FakeSession(rows or {}, context=context, rls=rls_state)
    return M04EDefinitionVersionPersistenceAdapter(
        session, artifacts=artifacts, scope_rls_manifest=scope_rls_manifest,
        definition_rls_manifest=definition_rls_manifest or _definition_rls_manifest(),
    ), session


def _query(*, tenant=TENANT, app=APP_ID, manifest=MANIFEST_VERSION, digest=None):
    return DefinitionVersionQuery(tenant, app, manifest, digest)


@pytest.mark.asyncio
async def test_exact_hit_uses_registered_rows_and_database_context_but_proof_stays_blocked():
    registry = SimpleNamespace(
        id="scope-tenant-1",
        tenant_id=TENANT,
        scope_kind="tenant",
        scope_id=TENANT,
        owner_state="active",
        cabinet_id=None,
        project_id=None,
        source_kind="tenant_source",
        source_id=TENANT,
        schema_version="owner.v1",
        payload_digest="sha256:" + "e" * 64,
        checkpoint="source-1",
    )
    mapping = SimpleNamespace(
        scope_registry_id="scope-tenant-1",
        mapping_state="mapped",
        source_kind="tenant_source",
        source_id=TENANT,
        schema_version="owner.v1",
        source_payload_digest=registry.payload_digest,
        canonical_payload_digest=registry.payload_digest,
        checkpoint="source-1",
    )
    rows = {
        "definitions": (_definition(),),
        "versions": (_version(),),
        "scopes": (registry,),
        "mappings": (mapping,),
        **_tenant_scope_fixture(),
    }
    artifacts = _ArtifactStore()
    adapter, session = _adapter(
        rows, artifacts=artifacts, scope_rls_manifest=_scope_rls_manifest(),
        definition_rls_manifest=_definition_rls_manifest(),
    )

    resolved = await adapter.resolve(_query())

    assert resolved.outcome is DefinitionResolutionOutcome.RESOLVED
    assert resolved.reference is not None
    assert resolved.reference.tenant_id == TENANT
    assert resolved.reference.app_definition_id == DEFINITION_ID
    assert resolved.reference.app_version_id == VERSION_ID
    assert resolved.reference.manifest_version == MANIFEST_VERSION
    assert resolved.reference.artifact_digest == ARTIFACT_DIGEST
    assert resolved.evidence == {
        "database_transaction_id": "pg:234567",
        "correlation_id": "corr-db-1",
    }
    assert artifacts.calls[0] == ("uri", TENANT, DEFINITION_ID, VERSION_ID)
    assert any(isinstance(statement, TextClause) and "pg_policies" in str(statement)
               for statement, _ in session.statements)
    proof = await adapter.prove_active_scope(
        context=MigrationEvidenceContext(TENANT, True, "pg:234567", "corr-db-1"),
        tenant_id=TENANT,
        app_id=APP_ID,
    )
    assert proof is None
    assert any("m04_scope_promotion_evidence_missing" in blocker for blocker in adapter.blockers)
    assert any("m04_scope_mapping_lineage_incomplete" in blocker for blocker in adapter.blockers)


@pytest.mark.asyncio
async def test_missing_definition_and_manifest_version_are_reported_without_positive_reference():
    adapter, _ = _adapter({"definitions": (), "versions": ()}, artifacts=_ArtifactStore())
    missing_definition = await adapter.resolve(_query())
    assert missing_definition.outcome is DefinitionResolutionOutcome.MISSING
    assert missing_definition.reason is DefinitionResolutionReason.MISSING_DEFINITION

    adapter, _ = _adapter({"definitions": (_definition(),), "versions": ()}, artifacts=_ArtifactStore())
    missing_version = await adapter.resolve(_query())
    assert missing_version.outcome is DefinitionResolutionOutcome.MISSING
    assert missing_version.reason is DefinitionResolutionReason.MISSING_VERSION


@pytest.mark.asyncio
async def test_duplicate_definition_or_manifest_key_is_quarantined_as_ambiguous():
    adapter, _ = _adapter(
        {"definitions": (_definition(), _definition(id="definition-duplicate")), "versions": (_version(),)},
        artifacts=_ArtifactStore(),
    )
    duplicate_definition = await adapter.resolve(_query())
    assert duplicate_definition.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert duplicate_definition.reason is DefinitionResolutionReason.INVALID_DEFINITION_HISTORY

    adapter, _ = _adapter(
        {"definitions": (_definition(),), "versions": (_version(), _version(id="version-duplicate"))},
        artifacts=_ArtifactStore(),
    )
    duplicate_version = await adapter.resolve(_query())
    assert duplicate_version.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert duplicate_version.reason is DefinitionResolutionReason.AMBIGUOUS_VERSION


@pytest.mark.asyncio
async def test_cross_tenant_query_and_cross_tenant_version_are_denied():
    adapter, session = _adapter(
        {"definitions": (_definition(),), "versions": (_version(tenant_id="other-tenant"),)},
        artifacts=_ArtifactStore(),
    )
    cross_tenant_query = await adapter.resolve(_query(tenant="other-tenant"))
    assert cross_tenant_query.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert cross_tenant_query.reason is DefinitionResolutionReason.CROSS_TENANT_DEFINITION
    assert not any("m04e_dynamic_app_definitions" in str(statement) for statement, _ in session.statements)

    adapter, _ = _adapter(
        {"definitions": (_definition(),), "versions": (_version(tenant_id="other-tenant"),)},
        artifacts=_ArtifactStore(),
    )
    mismatch = await adapter.resolve(_query())
    assert mismatch.outcome is DefinitionResolutionOutcome.MISSING
    assert mismatch.reference is None


@pytest.mark.asyncio
async def test_current_version_mismatch_and_cross_definition_link_are_denied():
    adapter, _ = _adapter(
        {"definitions": (_definition(current_version_id="another-version"),), "versions": (_version(),)},
        artifacts=_ArtifactStore(),
    )
    mismatch = await adapter.resolve(_query())
    assert mismatch.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert mismatch.reason is DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH

    adapter, _ = _adapter(
        {"definitions": (_definition(),), "versions": (_version(definition_id="other-definition"),)},
        artifacts=_ArtifactStore(),
    )
    cross_definition = await adapter.resolve(_query())
    assert cross_definition.outcome is DefinitionResolutionOutcome.MISSING
    assert cross_definition.reference is None


@pytest.mark.asyncio
async def test_artifact_digest_mismatch_and_missing_artifact_store_fail_closed():
    adapter, _ = _adapter(
        {"definitions": (_definition(),), "versions": (_version(),)},
        artifacts=_ArtifactStore(content=b"changed artifact"),
    )
    mismatch = await adapter.resolve(_query())
    assert mismatch.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert mismatch.reason is DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT

    adapter, _ = _adapter(
        {"definitions": (_definition(),), "versions": (_version(),)},
        artifacts=None,
    )
    missing_store = await adapter.resolve(_query())
    assert missing_store.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert missing_store.reason is DefinitionResolutionReason.INVALID_VERSION_HISTORY
    assert any("artifact_content_adapter_missing" in blocker for blocker in adapter.blockers)


@pytest.mark.asyncio
async def test_artifact_uri_mismatch_and_absent_m04e_rls_manifest_fail_closed():
    adapter, _ = _adapter(
        {"definitions": (_definition(),), "versions": (_version(),)},
        artifacts=_ArtifactStore(uri="content://other/uri"),
    )
    uri_mismatch = await adapter.resolve(_query())
    assert uri_mismatch.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert uri_mismatch.reason is DefinitionResolutionReason.INVALID_VERSION_HISTORY

    session = _FakeSession({"definitions": (_definition(),), "versions": (_version(),)})
    adapter = M04EDefinitionVersionPersistenceAdapter(session, artifacts=_ArtifactStore())
    unreviewed = await adapter.resolve(_query())
    assert unreviewed.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert unreviewed.reason is DefinitionResolutionReason.INVALID_DEFINITION_HISTORY
    assert any("m04e_definition_version_rls_unreviewed" in blocker for blocker in adapter.blockers)


@pytest.mark.asyncio
async def test_absent_rls_and_missing_audit_gate_block_resolution_and_never_make_proof():
    rows = {
        "definitions": (_definition(),),
        "versions": (_version(),),
        **_tenant_scope_fixture(),
    }
    adapter, _ = _adapter(rows, artifacts=_ArtifactStore(), rls=False)
    denied = await adapter.resolve(_query())
    assert denied.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert denied.reference is None
    assert any("m04e_definition_version_rls_unreviewed" in blocker for blocker in adapter.blockers)

    adapter, _ = _adapter(
        rows, artifacts=_ArtifactStore(), scope_rls_manifest=_scope_rls_manifest(),
        definition_rls_manifest=_definition_rls_manifest(),
    )
    context = MigrationEvidenceContext(TENANT, True, "pg:234567", "corr-db-1")
    scope = ActiveTypedScopeProof(
        tenant_id=TENANT,
        app_id=APP_ID,
        app_definition_id=DEFINITION_ID,
        scope_kind="tenant",
        scope_id=TENANT,
        transaction_id="pg:234567",
        correlation_id="corr-db-1",
        proof_digest="sha256:" + "1" * 64,
        m04_gate_digest="sha256:" + "2" * 64,
        m04e_gate_digest="sha256:" + "3" * 64,
        active=True,
    )
    result = await adapter.prove_and_record(
        context=context,
        query=_query(),
        resolution=await adapter._resolve_exact(_query(), context),
        scope=scope,
        artifact_uri=ARTIFACT_URI,
        artifact_content_digest=ARTIFACT_DIGEST,
    )
    denial_digest = await adapter.record_denial(
        context=context,
        query=_query(),
        reason=DefinitionResolutionReason.INVALID_DEFINITION_HISTORY,
    )
    assert result is None
    assert denial_digest is None
    assert any("m04e_definition_version_resolution_audit_missing" in blocker for blocker in adapter.blockers)
    assert any("m04e_audit_append_only_rls_missing" in blocker for blocker in adapter.blockers)


@pytest.mark.asyncio
async def test_context_is_database_derived_and_inactive_transaction_is_denied():
    fake_context = {
        "tenant_id": TENANT,
        "subject_id": "subject",
        "authenticated": "true",
        "correlation_id": "corr-from-db",
        "transaction_isolation": "serializable",
        "transaction_id": "99",
    }
    adapter, session = _adapter({}, context=fake_context)
    context = await adapter.server_context()
    assert context == MigrationEvidenceContext(TENANT, True, "pg:99", "corr-from-db")
    assert isinstance(session.statements[0][0], TextClause)

    session._active = False
    denied = await adapter.resolve(_query())
    assert denied.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert denied.reason is DefinitionResolutionReason.INVALID_DEFINITION_HISTORY


@pytest.mark.asyncio
async def test_nonserializable_or_rls_bypass_transaction_context_fails_closed():
    nonserializable = {
        "tenant_id": TENANT,
        "subject_id": "subject",
        "authenticated": "true",
        "correlation_id": "corr-db-1",
        "transaction_isolation": "read committed",
        "transaction_id": "100",
    }
    adapter, _ = _adapter({}, context=nonserializable)
    denied = await adapter.resolve(_query())
    assert denied.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert any("transaction_isolation_insufficient" in blocker for blocker in adapter.blockers)

    adapter, _ = _adapter(
        {"definitions": (_definition(),), "versions": (_version(),)},
        artifacts=_ArtifactStore(),
        rls=(True, True, _rls_policy(), True, False),
    )
    bypass = await adapter.resolve(_query())
    assert bypass.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert bypass.reason is DefinitionResolutionReason.INVALID_DEFINITION_HISTORY


@pytest.mark.asyncio
async def test_missing_active_scope_policy_and_permissive_policy_do_not_prove_scope():
    registry = SimpleNamespace(
        id="scope-tenant-1", tenant_id=TENANT, scope_kind="tenant", scope_id=TENANT,
        owner_state="active", cabinet_id=None, project_id=None,
        source_kind="tenant_source", source_id=TENANT, schema_version="owner.v1",
        payload_digest="sha256:" + "e" * 64, checkpoint="source-1",
    )
    mapping = SimpleNamespace(
        scope_registry_id="scope-tenant-1", mapping_state="mapped",
        source_kind="tenant_source", source_id=TENANT, schema_version="owner.v1",
        source_payload_digest="sha256:" + "d" * 64,
        canonical_payload_digest=registry.payload_digest, checkpoint="source-1",
    )
    rows = {
        "definitions": (_definition(),), "scopes": (registry,), "mappings": (mapping,),
        **_tenant_scope_fixture(),
    }
    context = MigrationEvidenceContext(TENANT, True, "pg:234567", "corr-db-1")
    no_active = (True, True, _rls_policy(), False, False, {
        "scope_registry": (True, True, "(tenant_id = current_setting('app.tenant_id', true))"),
    })
    adapter, _ = _adapter(rows, rls=no_active, scope_rls_manifest=_scope_rls_manifest())
    assert await adapter.prove_active_scope(
        context=context, tenant_id=TENANT, app_id=APP_ID,
    ) is None
    assert any("m04_scope_registry_rls_unreviewed" in blocker for blocker in adapter.blockers)

    permissive = (True, True, _rls_policy(), False, False, {}, None, {"scope_registry": "PERMISSIVE"})
    adapter, _ = _adapter(rows, rls=permissive, scope_rls_manifest=_scope_rls_manifest())
    assert await adapter.prove_active_scope(
        context=context, tenant_id=TENANT, app_id=APP_ID,
    ) is None
    assert any("m04_scope_registry_rls_unreviewed" in blocker for blocker in adapter.blockers)

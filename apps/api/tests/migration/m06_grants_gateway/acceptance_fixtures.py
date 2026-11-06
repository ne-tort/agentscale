"""Deterministic, side-effect-free fixtures for MIG-M06-02 behavior tests."""
from datetime import UTC, datetime, timedelta

from prodavan.application.capability_method_mapping.contracts import (
    CapabilityMethodMapping,
    MethodIdentity,
    ResourceDomainConstraint,
)
from prodavan.application.migration.m06_grants_gateway.adapter import (
    AuthenticatedGrantContext, DependencyGates, LegacyGrantSource, ResolvedGrantIdentity,
    SCHEMA_VERSION, OPERATION_MAP_VERSION, binding_digest, digest, request_identity_digest,
    schema_identity_digest, source_payload_digest,
)
from prodavan.application.migration.m06_grants_gateway.handoff import (
    AcceptedPartitionEvidence, AcceptedSourceKey, inventory_digest,
)
from prodavan.application.migration.m06_grants_gateway.method_mapping import (
    ApprovedMethodOperationAdapter, DeclaredMappingReadPort,
)
from prodavan.application.migration.m06_grants_gateway.parity_runner import (
    ParityPartition, ParityRunRequest,
)
from prodavan.application.scope_registry.contracts import RlsContext

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
M04_SCOPE_DIGEST = "sha256:" + "1" * 64
M04_RLS_DIGEST = "sha256:" + "5" * 64
M04E_DIGEST = "sha256:" + "2" * 64
M05_DIGEST = "sha256:" + "3" * 64
M06_SCHEMA_DIGEST = schema_identity_digest()
RLS = RlsContext(tenant_id="tenant-a", user_id="subject-a", role="member",
                 cabinet_id="cabinet-a", project_id="project-a")
CONTEXT = AuthenticatedGrantContext(
    tenant_id="tenant-a", company_id="company-a", subject_kind="user", subject_id="subject-a",
    session_id="session-a", cabinet_id="cabinet-a", project_id="project-a", authenticated=True,
    correlation_id="corr-a", transaction_id="tx-a", rls_context=RLS,
    plugin_instance_id="instance-a", runtime_generation=3, writer_generation=7,
    app_definition_id="definition-a",
)
IDENTITY = ResolvedGrantIdentity(
    tenant_id="tenant-a", company_id="company-a", app_definition_id="definition-a",
    app_id="inventory", app_version_id="version-a", manifest_version="1.2.0",
    artifact_digest="sha256:" + "a" * 64, scope_kind="plugin_instance", scope_id="instance-a",
    installation_scope_kind="project", installation_scope_id="project-a",
    cabinet_id="cabinet-a", project_id="project-a", subject_kind="user", subject_id="subject-a",
    session_id="session-a", plugin_instance_id="instance-a", runtime_generation=3,
    writer_generation=7, transaction_id="tx-a", correlation_id="corr-a",
    rls_evidence_digest=M04_RLS_DIGEST,
    owner_evidence_digest=M04_SCOPE_DIGEST, definition_evidence_digest=M04E_DIGEST,
    instance_evidence_digest=M05_DIGEST, requested_capabilities=frozenset({"storage.files"}),
)
GRANTED = {
    "name": "storage.files", "decision": "granted", "granted_by": "administrator",
    "constraints": {"operations": ["read"], "resources": ["workspace"], "domains": ["files"]},
}
DENIED = {**GRANTED, "decision": "denied"}
SOURCE = LegacyGrantSource(
    source_kind="legacy_app_grant", source_id="grant-a", schema_version="grantset-v1",
    app_id="inventory", manifest_version="1.2.0", grants=(GRANTED,), issued_at=NOW,
    expires_at=NOW + timedelta(days=1), asserted_company_id=None,
    asserted_scope_kind="project", asserted_scope_id="project-a",
    app_definition_id="definition-a", app_version_id="version-a",
)
REQUEST_IDENTITY = request_identity_digest(IDENTITY)
GOOD_GATES = DependencyGates(
    m04_scope_digest=M04_SCOPE_DIGEST, m04_rls_digest=M04_RLS_DIGEST,
    m04e_definition_version_digest=M04E_DIGEST, m05_instance_digest=M05_DIGEST,
    m06_schema_digest=M06_SCHEMA_DIGEST, trusted_writer_epoch=7,
    m04_scope_accepted=True, m04_rls_accepted=True,
    m04e_definition_version_accepted=True, m05_instance_runtime_accepted=True,
    atomic_evidence_accepted=True, m04_scope_evidence_id="m04-scope-proof",
    m04_rls_evidence_id="m04-rls-proof", m04e_definition_version_evidence_id="m04e-proof",
    m05_instance_runtime_evidence_id="m05-proof", atomic_evidence_id="txn-proof",
    m06_schema_accepted=True, request_identity_digest=REQUEST_IDENTITY,
    m06_schema_evidence_id="m06-schema-proof", m06_schema_evidence_digest=M06_SCHEMA_DIGEST,
    tenant_id="tenant-a", app_definition_id="definition-a", app_version_id="version-a",
    scope_kind="plugin_instance", scope_id="instance-a", plugin_instance_id="instance-a",
    subject_id="subject-a", session_id="session-a", runtime_generation=3, writer_generation=7,
    transaction_id="tx-a", correlation_id="corr-a", source_digest=source_payload_digest(SOURCE),
    final_delta_checkpoint="final-delta-1", installation_scope_kind="project",
    installation_scope_id="project-a", company_id="company-a", app_id="inventory",
    manifest_version="1.2.0", subject_kind="user", cabinet_id="cabinet-a",
    project_id="project-a",
)
ACCEPTED_SOURCES = (
    AcceptedSourceKey("legacy_app_grant", "grant-a", "grantset-v1", source_payload_digest(SOURCE)),
)
PARTITION = AcceptedPartitionEvidence(
    tenant_id="tenant-a", app_definition_id="definition-a", app_version_id="version-a",
    plugin_instance_id="instance-a", writer_epoch=7, m04_scope_digest=M04_SCOPE_DIGEST,
    m04_rls_digest=M04_RLS_DIGEST, m04e_definition_version_digest=M04E_DIGEST,
    m05_instance_digest=M05_DIGEST, m06_schema_identity_digest=M06_SCHEMA_DIGEST,
    m04_scope_evidence_id="m04-scope-proof", m04_rls_evidence_id="m04-rls-proof",
    m04e_definition_version_evidence_id="m04e-proof",
    m05_instance_runtime_evidence_id="m05-proof", atomic_evidence_id="txn-proof",
    m06_schema_evidence_id="m06-schema-proof", final_delta_checkpoint="final-delta-1",
    inventory_digest=inventory_digest(ACCEPTED_SOURCES), accepted_sources=ACCEPTED_SOURCES,
)
APPROVED_MAPPINGS = (
    CapabilityMethodMapping(
        identity=MethodIdentity(server="files", tool="read", version="1.0.0"),
        capability_key="storage.files", operation="read",
        constraints=ResourceDomainConstraint(resources=("workspace",), domains=("files",)),
        supported_protocol_version="mcp-1", tool_version_compatibility=("1.0.0",),
    ),
    CapabilityMethodMapping(
        identity=MethodIdentity(server="files", tool="delete", version="1.0.0"),
        capability_key="storage.files", operation="delete",
        constraints=ResourceDomainConstraint(resources=("workspace",), domains=("files",)),
        supported_protocol_version="mcp-1", tool_version_compatibility=("1.0.0",),
    ),
)
METHOD_SERVERS = {"read": "files", "delete": "files"}
METHOD_VERSIONS = {"read": "1.0.0", "delete": "1.0.0"}
MAPPING_READ_PORT = DeclaredMappingReadPort(APPROVED_MAPPINGS)


def method_mapping(*, servers=None, versions=None, protocol_version="mcp-1"):
    return ApprovedMethodOperationAdapter(
        mappings=MAPPING_READ_PORT,
        method_servers=servers or METHOD_SERVERS,
        method_versions=versions or METHOD_VERSIONS,
        protocol_version=protocol_version,
        now=NOW,
    )


PARITY_PARTITION = ParityPartition(tenant_id="tenant-a", app_definition_id="definition-a")
PARITY_REQUEST = ParityRunRequest(
    partition=PARITY_PARTITION, window_start=NOW, window_end=NOW + timedelta(hours=1),
    max_observations=100, page_size=25,
)
CASES = (
    "unknown capability is quarantined without shadow decision",
    "unknown and unsupported methods are quarantined by the approved mapping registry",
    "missing or ambiguous instance blocks comparison and evidence writes",
    "stale runtime or writer generation blocks comparison and evidence writes",
    "legacy allow versus canonical deny is a deterministic mismatch",
    "changed source digest conflicts while preserving the first mapping",
    "cross-tenant replay is detected using globally unique source identity",
    "expired and revoked canonical grants produce explicit deterministic denial",
    "exact source schema and digest replay does not duplicate mapping identity",
    "incomplete partition is reported as incomplete, never as ready_for_review",
    "caller-supplied capabilities and ownership are never authorization",
)

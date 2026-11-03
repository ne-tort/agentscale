"""Unregistered persistence schema for MIG-AUTO-01/02 automation migration.

These candidate rows deliberately stay out of the global model registry until
an Alembic migration, tenant security policy, and explicit host wiring are
reviewed. Existing legacy tables remain read-only inputs.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")
_DIGEST_CHECK = "length({column}) = 71 AND {column} LIKE 'sha256:%'"


class AutomationProjectScopeEvidenceRow(Base):
    """Server-resolved M04 tenant/project parent proof; not caller authority."""

    __tablename__ = "mig_auto_project_scope_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        CheckConstraint("tenant_id <> '' AND project_id <> ''", name="ck_mig_auto_scope_ids"),
        Index("ix_mig_auto_scope_partition", "tenant_id", "project_id", "active"),
    )

    scope_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_proof_id: Mapped[str] = mapped_column(String(256), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    gate_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    parent_compatible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    evidence_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationGateEvidenceRow(Base):
    """Immutable-by-policy M04/M04.e evidence snapshot consumed by ingress."""

    __tablename__ = "mig_auto_gate_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "kind", "record_id", name="uq_mig_auto_gate_identity"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        CheckConstraint("kind IN ('M04', 'M04.e')", name="ck_mig_auto_gate_kind"),
        CheckConstraint("verified = false OR revoked = false", name="ck_mig_auto_gate_not_revoked"),
        Index("ix_mig_auto_gate_partition", "tenant_id", "project_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    partition_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    dependencies: Mapped[list] = mapped_column(_JSON, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    signature_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    decision_owner: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationDefinitionRow(Base):
    """Target definition identity; legacy automation/config rows are not copied here."""

    __tablename__ = "mig_auto_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "automation_id", name="uq_mig_auto_definition_partition"),
        UniqueConstraint("id", "tenant_id", "project_id", "automation_id", name="uq_mig_auto_definition_composite"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        CheckConstraint("lifecycle IN ('draft', 'active', 'paused', 'retired', 'quarantined')", name="ck_mig_auto_definition_lifecycle"),
        CheckConstraint("revision >= 0", name="ck_mig_auto_definition_revision"),
        CheckConstraint("lifecycle <> 'active' OR current_version_id IS NOT NULL", name="ck_mig_auto_active_has_version"),
        ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["mig_auto_definition_versions.version_id", "mig_auto_definition_versions.definition_id"],
            name="fk_mig_auto_definition_current_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_mig_auto_definition_owner", "tenant_id", "project_id", "lifecycle"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(24), nullable=False, default="quarantined", server_default="quarantined")
    current_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationDefinitionVersionRow(Base):
    """Immutable artifact snapshot and ingress command/event allowlist."""

    __tablename__ = "mig_auto_definition_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "automation_id", "version_id", name="uq_mig_auto_version_identity"),
        UniqueConstraint("automation_id", "definition_id", "version_id", name="uq_mig_auto_version_definition"),
        UniqueConstraint("version_id", "definition_id", name="uq_mig_auto_version_definition_link"),
        ForeignKeyConstraint(
            ["definition_id", "tenant_id", "project_id", "automation_id"],
            ["mig_auto_definitions.id", "mig_auto_definitions.tenant_id", "mig_auto_definitions.project_id", "mig_auto_definitions.automation_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(_DIGEST_CHECK.format(column="artifact_digest"), name="ck_mig_auto_version_artifact_digest"),
        CheckConstraint("lifecycle IN ('draft', 'published', 'disabled', 'revoked', 'archived', 'quarantined')", name="ck_mig_auto_version_lifecycle"),
        Index("ix_mig_auto_version_owner", "tenant_id", "project_id", "automation_id", "lifecycle"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version_id: Mapped[str] = mapped_column(String(80), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    task_name: Mapped[str] = mapped_column(String(256), nullable=False)
    command: Mapped[str] = mapped_column(String(128), nullable=False)
    events: Mapped[list] = mapped_column(_JSON, nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(24), nullable=False, default="quarantined", server_default="quarantined")
    evidence_id: Mapped[str] = mapped_column(String(256), nullable=False)
    gate_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationIngressMappingRow(Base):
    """Tenant/project-owned webhook target mapping; secrets stay as secret refs."""

    __tablename__ = "mig_auto_ingress_mappings"
    __table_args__ = (
        UniqueConstraint("source_id", "subscription_id", "mapping_id", name="uq_mig_auto_mapping_subscription"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "automation_id"],
            ["mig_auto_definitions.tenant_id", "mig_auto_definitions.project_id", "mig_auto_definitions.automation_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("secret_ref LIKE 'secret://%'", name="ck_mig_auto_mapping_secret_ref"),
        Index("ix_mig_auto_mapping_subscription", "source_id", "subscription_id", "enabled", "inbound_enabled"),
    )

    mapping_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subscription_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version_id: Mapped[str] = mapped_column(String(80), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    routes: Mapped[list] = mapped_column(_JSON, nullable=False)
    event_types: Mapped[list] = mapped_column(_JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    inbound_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationProjectTriggerEvidenceRow(Base):
    """Target evidence for a legacy project_triggers row; never owns or mutates it."""

    __tablename__ = "mig_auto_project_trigger_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["trigger_id"], ["project_triggers.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        CheckConstraint("legacy_fence_generation >= 1", name="ck_mig_auto_trigger_fence_generation"),
        Index("ix_mig_auto_trigger_project", "tenant_id", "project_id", "trigger_id"),
    )

    trigger_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    mapping_id: Mapped[str] = mapped_column(String(128), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version_id: Mapped[str] = mapped_column(String(80), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    source_evidence_id: Mapped[str] = mapped_column(String(256), nullable=False)
    eligibility_evidence_id: Mapped[str] = mapped_column(String(256), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    legacy_fence_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    legacy_fence_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    target_claim_evidence_id: Mapped[str | None] = mapped_column(String(256), nullable=True)


class AutomationIngressApprovalLinkRow(Base):
    """Durable link to an already-created AgentRun approval request."""

    __tablename__ = "mig_auto_ingress_approval_links"
    __table_args__ = (
        ForeignKeyConstraint(["approval_id"], ["approval_requests.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        UniqueConstraint("approval_id", "run_id", name="uq_mig_auto_approval_run"),
        Index("ix_mig_auto_approval_partition", "tenant_id", "project_id", "run_id"),
    )

    link_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    approval_id: Mapped[str] = mapped_column(String(40), nullable=False)
    run_id: Mapped[str] = mapped_column(String(40), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationIngressClaimRow(Base):
    """Durable idempotency receipt for one exact tenant/project/source identity."""

    __tablename__ = "mig_auto_ingress_claims"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_mig_auto_ingress_idempotency"),
        UniqueConstraint(
            "tenant_id", "project_id", "source_kind", "source_id", "source_event_id", "subscription_key",
            name="uq_mig_auto_ingress_source_identity",
        ),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["task_run_id"], ["mig_auto_task_intents.task_run_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["task_run_id", "agent_run_id", "tenant_id", "project_id"],
            ["mig_auto_task_intents.task_run_id", "mig_auto_task_intents.agent_run_id", "mig_auto_task_intents.tenant_id", "mig_auto_task_intents.project_id"],
            name="fk_mig_auto_ingress_claim_task_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["audit_id"], ["mig_auto_ingress_audit.evidence_id"], ondelete="RESTRICT"),
        CheckConstraint(_DIGEST_CHECK.format(column="request_digest"), name="ck_mig_auto_ingress_request_digest"),
        CheckConstraint("status = 'accepted'", name="ck_mig_auto_ingress_status"),
        Index("ix_mig_auto_ingress_source", "tenant_id", "project_id", "source_kind", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    subscription_key: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(71), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    claim_json: Mapped[dict] = mapped_column(_JSON, nullable=False)
    provenance_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    source_claim_marker: Mapped[str | None] = mapped_column(String(256), nullable=True)
    task_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_run_id: Mapped[str] = mapped_column(String(40), nullable=False)
    audit_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="accepted", server_default="accepted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationTaskIntentRow(Base):
    """Task identity separate from the AgentRun/conversation tables."""

    __tablename__ = "mig_auto_task_intents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "idempotency_key", name="uq_mig_auto_task_intent"),
        UniqueConstraint("agent_run_id", name="uq_mig_auto_task_agent_run"),
        UniqueConstraint("task_run_id", "agent_run_id", "tenant_id", "project_id", name="uq_mig_auto_task_receipt_link"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        CheckConstraint("state IN ('queued', 'leased', 'running', 'waiting_retry', 'succeeded', 'failed', 'cancelled')", name="ck_mig_auto_task_state"),
        CheckConstraint("attempt >= 0 AND lease_generation >= 0 AND revision >= 0", name="ck_mig_auto_task_counters"),
        CheckConstraint(
            "(state IN ('leased', 'running') AND attempt >= 1 AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR (state NOT IN ('leased', 'running') AND lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL)",
            name="ck_mig_auto_task_lease_shape",
        ),
        CheckConstraint(
            "(state IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NOT NULL) OR (state NOT IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NULL)",
            name="ck_mig_auto_task_terminal_shape",
        ),
        Index("ix_mig_auto_task_due", "tenant_id", "project_id", "state", "available_at"),
    )

    task_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_run_id: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(71), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    input_payload: Mapped[dict] = mapped_column(_JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", server_default="queued")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationIngressAuditRow(Base):
    """Append-shaped ingress/quarantine/conflict evidence; no payload secrets."""

    __tablename__ = "mig_auto_ingress_audit"
    __table_args__ = (
        UniqueConstraint("evidence_id", name="uq_mig_auto_ingress_audit_evidence"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        Index("ix_mig_auto_ingress_audit_partition", "tenant_id", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    request_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence: Mapped[dict] = mapped_column(_JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationIngressEventRow(Base):
    """One ordered domain event linked to an accepted automation run."""

    __tablename__ = "mig_auto_ingress_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_mig_auto_ingress_event_sequence"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        CheckConstraint("sequence >= 1", name="ck_mig_auto_ingress_event_sequence"),
        CheckConstraint(_DIGEST_CHECK.format(column="request_digest"), name="ck_mig_auto_ingress_event_request_digest"),
        Index("ix_mig_auto_ingress_event_source", "tenant_id", "project_id", "source_event_id"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    run_id: Mapped[str] = mapped_column(String(40), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(_JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationCommandReceiptRow(Base):
    """Scoped command idempotency fence for atomic business/event/enqueue writes."""

    __tablename__ = "mig_auto_command_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "automation_id", "idempotency_key", name="uq_mig_auto_command_key"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        CheckConstraint(_DIGEST_CHECK.format(column="request_digest"), name="ck_mig_auto_command_digest"),
    )

    receipt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    adapter_evidence_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationBusinessMutationIntentRow(Base):
    """Immutable durable mutation intent; not a substitute for domain projection writes."""

    __tablename__ = "mig_auto_business_mutation_intents"
    __table_args__ = (
        UniqueConstraint("receipt_id", name="uq_mig_auto_business_mutation_receipt"),
        ForeignKeyConstraint(["receipt_id"], ["mig_auto_command_receipts.receipt_id"], ondelete="RESTRICT"),
        CheckConstraint("expected_revision >= 0", name="ck_mig_auto_business_mutation_revision"),
    )

    receipt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    payload: Mapped[dict] = mapped_column(_JSON, nullable=False)


class AutomationOutboxEventRow(Base):
    """Immutable target event, distinct from its delivery/retry state."""

    __tablename__ = "mig_auto_outbox_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_mig_auto_outbox_event_key"),
        UniqueConstraint("tenant_id", "aggregate_type", "aggregate_id", "aggregate_version", name="uq_mig_auto_outbox_aggregate_version"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        CheckConstraint("aggregate_version >= 1", name="ck_mig_auto_outbox_version"),
        CheckConstraint(_DIGEST_CHECK.format(column="payload_digest"), name="ck_mig_auto_outbox_payload_digest"),
        Index("ix_mig_auto_outbox_partition_source", "tenant_id", "project_id", "automation_id", "source_event_id"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    source_run_id: Mapped[str] = mapped_column(String(40), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(_JSON, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationOutboxStateRow(Base):
    """CAS-managed dispatcher state for one immutable outbox event."""

    __tablename__ = "mig_auto_outbox_state"
    __table_args__ = (
        ForeignKeyConstraint(["event_id"], ["mig_auto_outbox_events.event_id"], ondelete="RESTRICT"),
        CheckConstraint("state IN ('pending', 'claimed', 'retry_wait', 'delivered', 'dead_letter')", name="ck_mig_auto_outbox_state"),
        CheckConstraint("attempts >= 0 AND lease_generation >= 0 AND revision >= 0", name="ck_mig_auto_outbox_counters"),
        CheckConstraint("(state = 'claimed' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR (state <> 'claimed' AND lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL)", name="ck_mig_auto_outbox_lease_shape"),
        Index("ix_mig_auto_outbox_due", "state", "available_at", "lease_until"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AutomationOutboxEnqueueIntentRow(Base):
    """Durable handoff written in the same transaction as its source event."""

    __tablename__ = "mig_auto_outbox_enqueue_intents"
    __table_args__ = (
        UniqueConstraint("queue_name", "deduplication_key", name="uq_mig_auto_enqueue_dedup"),
        ForeignKeyConstraint(["event_id"], ["mig_auto_outbox_events.event_id"], ondelete="RESTRICT"),
        CheckConstraint("state IN ('pending', 'claimed', 'dispatched', 'dead_letter')", name="ck_mig_auto_enqueue_state"),
        CheckConstraint("attempts >= 0 AND lease_generation >= 0 AND revision >= 0", name="ck_mig_auto_enqueue_counters"),
        Index("ix_mig_auto_enqueue_due", "queue_name", "state", "available_at", "lease_until"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    queue_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AutomationDeliveryIntentRow(Base):
    """One outbound intent per exact partition/subscription/source-event identity."""

    __tablename__ = "mig_auto_delivery_intents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "automation_id", "subscription_id", "source_event_id", name="uq_mig_auto_delivery_identity"),
        UniqueConstraint("idempotency_key", name="uq_mig_auto_delivery_key"),
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["task_run_id"], ["mig_auto_task_intents.task_run_id"], ondelete="RESTRICT"),
        CheckConstraint(_DIGEST_CHECK.format(column="payload_digest"), name="ck_mig_auto_delivery_payload_digest"),
        CheckConstraint(_DIGEST_CHECK.format(column="request_digest"), name="ck_mig_auto_delivery_request_digest"),
        Index("ix_mig_auto_delivery_due", "tenant_id", "project_id", "automation_id", "state", "available_at"),
    )

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_run_id: Mapped[str] = mapped_column(String(40), nullable=False)
    subscription_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    destination_ref: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    payload: Mapped[dict] = mapped_column(_JSON, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8, server_default="8")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationDeliveryStateRow(Base):
    """Independent delivery retry state with opaque token and generation fencing."""

    __tablename__ = "mig_auto_delivery_state"
    __table_args__ = (
        ForeignKeyConstraint(["delivery_id"], ["mig_auto_delivery_intents.delivery_id"], ondelete="RESTRICT"),
        CheckConstraint("state IN ('pending', 'claimed', 'retry_wait', 'delivered', 'dead_letter')", name="ck_mig_auto_delivery_state"),
        CheckConstraint("attempts >= 0 AND lease_generation >= 0 AND revision >= 0", name="ck_mig_auto_delivery_counters"),
        CheckConstraint("(state = 'claimed' AND claim_token IS NOT NULL AND lease_owner IS NOT NULL AND lease_until IS NOT NULL) OR (state <> 'claimed' AND claim_token IS NULL AND lease_owner IS NULL AND lease_until IS NULL)", name="ck_mig_auto_delivery_lease_shape"),
        Index("ix_mig_auto_delivery_claim", "state", "available_at", "lease_until"),
    )

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AutomationPartitionOwnershipRow(Base):
    """Exclusive per-partition migration owner/lease state; defaults are legacy."""

    __tablename__ = "mig_auto_partition_ownership"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        CheckConstraint("state IN ('legacy_active', 'shadow', 'target_read_legacy_write', 'final_delta', 'target_active', 'rolled_back', 'target_frozen', 'blocked')", name="ck_mig_auto_ownership_state"),
        CheckConstraint("NOT (legacy_claims_enabled AND target_claims_enabled)", name="ck_mig_auto_no_dual_claims"),
        CheckConstraint("epoch >= 0 AND revision >= 0 AND active_legacy_leases >= 0 AND active_target_leases >= 0", name="ck_mig_auto_ownership_counters"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    automation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="legacy_active", server_default="legacy_active")
    read_owner: Mapped[str] = mapped_column(String(16), nullable=False, default="legacy", server_default="legacy")
    write_owner: Mapped[str] = mapped_column(String(16), nullable=False, default="legacy", server_default="legacy")
    legacy_claims_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    target_claims_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    projection_read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    shadow_reads_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    fencing_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    legacy_marker: Mapped[dict] = mapped_column(_JSON, nullable=False)
    legacy_marker_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    evidence_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    active_legacy_leases: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    active_target_leases: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AutomationOwnershipEvidenceRow(Base):
    """Append-only-by-policy record of each owner transition and gate digest."""

    __tablename__ = "mig_auto_ownership_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "automation_id", "epoch", name="uq_mig_auto_owner_epoch"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "automation_id"],
            ["mig_auto_partition_ownership.tenant_id", "mig_auto_partition_ownership.project_id", "mig_auto_partition_ownership.automation_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(_DIGEST_CHECK.format(column="evidence_digest"), name="ck_mig_auto_owner_evidence_digest"),
        Index("ix_mig_auto_owner_evidence_partition", "tenant_id", "project_id", "automation_id", "created_at"),
    )

    transition_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    automation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(256), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence: Mapped[dict] = mapped_column(_JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ = [
    "AutomationDefinitionRow", "AutomationDefinitionVersionRow", "AutomationDeliveryIntentRow",
    "AutomationDeliveryStateRow", "AutomationGateEvidenceRow", "AutomationIngressAuditRow",
    "AutomationIngressClaimRow", "AutomationIngressMappingRow", "AutomationOutboxEnqueueIntentRow",
    "AutomationOutboxEventRow", "AutomationOutboxStateRow", "AutomationOwnershipEvidenceRow",
    "AutomationPartitionOwnershipRow", "AutomationProjectScopeEvidenceRow", "AutomationProjectTriggerEvidenceRow",
    "AutomationTaskIntentRow",
]

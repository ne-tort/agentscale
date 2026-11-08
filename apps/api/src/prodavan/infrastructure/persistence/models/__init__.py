"""Persistence models package."""

from prodavan.infrastructure.persistence.models.admin import (
    CompanyAgentRuntimePolicyRow,
    CompanyCabinetQuotaRow,
    CompanyTenantInfraQuotaRow,
)
from prodavan.infrastructure.persistence.models.agent import (
    AgentEventRow,
    AgentSessionRow,
    AgentTurnRow,
    AgentUsageRow,
    EmployeeChatPinRow,
    EmployeeProjectSelectionRow,
)
from prodavan.infrastructure.persistence.models.agent_control_plane import (
    AgentRunRow,
    ApprovalRequestRow,
)
from prodavan.infrastructure.persistence.agent_control_plane.models import (
    AgentControlPlaneAggregateRow,
    AgentControlPlaneCommandRow,
    AgentControlPlaneEnqueueIntentRow,
    AgentControlPlaneEventRow,
    AgentControlPlaneOutboxEventRow,
    AgentControlPlaneRunScopeRow,
)
from prodavan.infrastructure.persistence.agent_control_plane.lease_registry import (
    AgentRunLeaseRow,
)
from prodavan.infrastructure.persistence.agent_control_plane.attempt_registry import (
    AgentRunProviderAttemptRow,
)
from prodavan.infrastructure.persistence.models.ai_keys import (
    AiKeyAuditEventRow,
    AiKeyCheckResultRow,
    AiProviderKeyRow,
    CompanyAiKeyBindingRow,
)
from prodavan.infrastructure.persistence.models.ai_models import (
    AiKeyModelBindingRow,
    AiKeyModelGrantRow,
    AiModelRow,
    AiModelSdkBindingRow,
)
# MIG-AUTHSESS-01: the durable correlation_id -> verified subject store the auth-boundary
# principal resolver was missing. A platform table (like companies/memberships): it is read
# before a tenant exists, so it is deliberately absent from the canonical RLS registry and
# recorded in policies.EXCLUDED_TABLES instead.
from prodavan.infrastructure.persistence.models.auth_sessions import AuthSessionRow
from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.catalog import ReferenceCatalogEntryRow
from prodavan.infrastructure.persistence.models.equipment_v2 import (
    EquipmentBudgetAllocationRow,
    EquipmentCandidateGroupOfferRow,
    EquipmentCandidateGroupRow,
    EquipmentCatalogOfferRow,
    EquipmentCatalogSnapshotRow,
    EquipmentIngestionRunRow,
    EquipmentRequestLineRow,
    EquipmentRequestRow,
    EquipmentTenderCandidateGroupRow,
    EquipmentTenderRow,
)
from prodavan.infrastructure.persistence.models.composer_draft import EmployeeComposerDraftRow
from prodavan.infrastructure.persistence.models.content import (
    ContentAclEntryRow,
    ContentAliasBindingRow,
    ContentAliasRow,
    ContentAssetLinkRow,
    ContentAssetRow,
    ContentBlobVersionRow,
)
from prodavan.infrastructure.persistence.models.identity import (
    CompanyRow,
    EmployeeRow,
    IdentityLinkRow,
    MembershipRow,
)
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleCompanyGrantRow,
    ModuleInstanceDataRow,
    ModuleInstanceMetaDocumentRow,
    ModuleInstanceRow,
    ModuleMetaDocumentRow,
    ModuleProjectBindingRow,
    ModuleRow,
)
from prodavan.infrastructure.persistence.models.platform import PlatformBootstrapRow
from prodavan.infrastructure.persistence.models.platform_events import PlatformEventRow
# Plugin-платформа (дизайн v4, ревизия 2026110801): 6 таблиц СОСТОЯНИЯ
# платформы. Без этого импорта Base.metadata их не увидит и `alembic check`
# на деплое упадёт по drift. Данные самих плагинов лежат НЕ здесь, а в
# tenant_<company> (прямой DSN, ADR-007).
from prodavan.infrastructure.persistence.models.plugin_platform import (
    AUDIT_FORBIDDEN_RAW_COLUMNS,
    CapabilityGrantRow,
    PluginArtifactRow,
    PluginAuditEventRow,
    PluginEventDeliveryRow,
    PluginFeatureFlagRow,
    PluginInstanceRow,
    build_app_version_projection,
)
from prodavan.infrastructure.persistence.models.external_api import (
    ApiAppKeyRow,
    WebhookDeliveryRow,
    WebhookSubscriptionRow,
)
# MIG-DDL-M06-01: canonical grants own their tables; see
# docs/08-migration/canonical-grants-ownership-decision.md. The retired
# dynamic_app_grants.DynamicAppCapabilityGrantRow duplicate is deliberately not
# imported — a second __tablename__ claim would fork Base.metadata.
from prodavan.infrastructure.persistence.models.canonical_grants import (
    CanonicalGrantAuditRow,
    CanonicalGrantMappingRow,
    CanonicalGrantQuarantineRow,
    CanonicalGrantRow,
    CanonicalGrantWriterEpochRow,
    M06CanonicalGrantPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.owner_registry import (
    CanonicalTenantAnchorRow,
    ScopeOwnerAuditRow,
    ScopeOwnerCabinetAnchorRow,
    ScopeOwnerMappingRow,
    ScopeOwnerProjectAnchorRow,
    ScopeOwnerQuarantineRow,
    ScopeRegistryRow,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    DynamicAppDefinitionFoundationRow,
    DynamicAppDefinitionVersionFoundationRow,
    M04EDefinitionVersionPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.plugin_instance_registry import (
    M05PluginInstancePromotionEvidenceRow,
    PluginInstanceRegistryLinkRow,
    PluginInstanceRegistryRow,
)
# MIG-DDL-M04EVID-01: the accepted M04-02 scope/RLS evidence row that the evidence
# ports refused to synthesise. Registered here so it is part of Base.metadata and of
# the RLS coverage report; no positive canonical writer is enabled by importing it.
from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
    M04ScopePromotionEvidenceRow,
)
# MIG-DDL-M04E-LEDGER-01: the four ledger tables the M04E EvidenceStore port needs
# (per-source first mapping, changed-digest observations, durable cursor, redacted
# audit). Registered so they are part of Base.metadata and of the RLS coverage
# report; importing them enables no writer and no positive canonical write.
from prodavan.infrastructure.persistence.models.m04e_ledger import (
    M04EDefinitionVersionAuditRow,
    M04EDefinitionVersionCheckpointRow,
    M04EDefinitionVersionMappingRow,
    M04EDefinitionVersionQuarantineRow,
)
# MIG-DDL-CUTOVER-01: the two tables SqlCutoverRepository fails closed without —
# the append-only partition cutover ledger (state CAS + command idempotency) and the
# live legacy source tail. Registered so they are part of Base.metadata and of the RLS
# coverage report; importing them enables no writer and no positive canonical write.
from prodavan.infrastructure.persistence.models.m06_cutover_evidence import (
    M06PartitionCutoverEvidenceRow,
    M06PartitionCutoverSourceTailRow,
)
from prodavan.infrastructure.persistence.models.dynamic_apps import (
    AppDefinitionRow,
    AppGrantRow,
    AppInstanceRow,
    AppVersionRow,
)
from prodavan.infrastructure.persistence.models.projects import (
    ProjectAttachmentRow,
    ProjectRow,
    ProjectScheduleRow,
    ProjectTriggerRow,
)
from prodavan.infrastructure.persistence.models.resources import (
    ResourceBindingRow,
    ResourceRow,
    ResourceVersionRow,
)
from prodavan.infrastructure.persistence.models.runtime_revisions import (
    CapabilityBundleRow,
    MaterializationEntryRow,
    ProjectRuntimeRevisionRow,
)

__all__ = [
    "AUDIT_FORBIDDEN_RAW_COLUMNS",
    "AgentControlPlaneAggregateRow",
    "AgentControlPlaneCommandRow",
    "AgentControlPlaneEnqueueIntentRow",
    "AgentControlPlaneEventRow",
    "AgentControlPlaneOutboxEventRow",
    "AgentControlPlaneRunScopeRow",
    "AgentEventRow",
    "AgentRunLeaseRow",
    "AgentRunProviderAttemptRow",
    "AgentRunRow",
    "AgentSessionRow",
    "AgentTurnRow",
    "ApprovalRequestRow",
    "ApiAppKeyRow",
    "WebhookDeliveryRow",
    "WebhookSubscriptionRow",
    "AgentUsageRow",
    "AuthSessionRow",
    "CanonicalGrantAuditRow",
    "CanonicalGrantMappingRow",
    "CanonicalGrantQuarantineRow",
    "CanonicalGrantRow",
    "CanonicalGrantWriterEpochRow",
    "M06CanonicalGrantPromotionEvidenceRow",
    "AppDefinitionRow",
    "AppGrantRow",
    "AppInstanceRow",
    "AppVersionRow",
    "EmployeeChatPinRow",
    "EmployeeComposerDraftRow",
    "EmployeeProjectSelectionRow",
    "AiKeyAuditEventRow",
    "AiKeyCheckResultRow",
    "AiKeyModelBindingRow",
    "AiKeyModelGrantRow",
    "AiModelRow",
    "AiModelSdkBindingRow",
    "AiProviderKeyRow",
    "Base",
    "CabinetCompanyGrantRow",
    "CabinetEmployeeAssignmentRow",
    "CabinetInstanceRow",
    "CompanyAgentRuntimePolicyRow",
    "CompanyAiKeyBindingRow",
    "CompanyCabinetQuotaRow",
    "CompanyTenantInfraQuotaRow",
    "CompanyRow",
    "CanonicalTenantAnchorRow",
    "ScopeOwnerAuditRow",
    "ScopeOwnerCabinetAnchorRow",
    "ScopeOwnerMappingRow",
    "ScopeOwnerProjectAnchorRow",
    "ScopeOwnerQuarantineRow",
    "ScopeRegistryRow",
    "DynamicAppDefinitionFoundationRow",
    "DynamicAppDefinitionVersionFoundationRow",
    "M04EDefinitionVersionPromotionEvidenceRow",
    "M04ScopePromotionEvidenceRow",
    "M04EDefinitionVersionAuditRow",
    "M04EDefinitionVersionCheckpointRow",
    "M04EDefinitionVersionMappingRow",
    "M04EDefinitionVersionQuarantineRow",
    "M06PartitionCutoverEvidenceRow",
    "M06PartitionCutoverSourceTailRow",
    "M05PluginInstancePromotionEvidenceRow",
    "PluginInstanceRegistryLinkRow",
    "PluginInstanceRegistryRow",
    "ContentAclEntryRow",
    "ContentAliasBindingRow",
    "ContentAliasRow",
    "ContentAssetLinkRow",
    "ContentAssetRow",
    "ContentBlobVersionRow",
    "EquipmentBudgetAllocationRow",
    "EquipmentCandidateGroupOfferRow",
    "EquipmentCandidateGroupRow",
    "EquipmentCatalogOfferRow",
    "EquipmentCatalogSnapshotRow",
    "EquipmentIngestionRunRow",
    "EquipmentRequestLineRow",
    "EquipmentRequestRow",
    "EquipmentTenderCandidateGroupRow",
    "EquipmentTenderRow",
    "EmployeeRow",
    "IdentityLinkRow",
    "MembershipRow",
    "ModuleCabinetBindingRow",
    "ModuleCompanyGrantRow",
    "ModuleInstanceDataRow",
    "ModuleInstanceMetaDocumentRow",
    "ModuleInstanceRow",
    "ModuleMetaDocumentRow",
    "ModuleProjectBindingRow",
    "ModuleRow",
    "PlatformBootstrapRow",
    "PlatformEventRow",
    "CapabilityGrantRow",
    "PluginArtifactRow",
    "PluginAuditEventRow",
    "PluginEventDeliveryRow",
    "PluginFeatureFlagRow",
    "PluginInstanceRow",
    "build_app_version_projection",
    "ProjectAttachmentRow",
    "ProjectRow",
    "ProjectScheduleRow",
    "ProjectTriggerRow",
    "CapabilityBundleRow",
    "MaterializationEntryRow",
    "ProjectRuntimeRevisionRow",
    "ReferenceCatalogEntryRow",
    "ResourceBindingRow",
    "ResourceRow",
    "ResourceVersionRow",
]


def __getattr__(name: str):
    """Fail closed for the retired duplicate grant model (MIG-DDL-M06-01).

    ``DynamicAppCapabilityGrantRow`` described the same physical table as
    ``CanonicalGrantRow`` with a narrower, non-canonical shape. Importing it
    would create a second ``Table`` object for ``dynamic_app_capability_grants``
    and fork ``Base.metadata``, so the name is not exported any more.
    """

    if name == "DynamicAppCapabilityGrantRow":
        raise AttributeError(
            "DynamicAppCapabilityGrantRow is retired: dynamic_app_capability_grants "
            "is owned by models.canonical_grants.CanonicalGrantRow. Use "
            "CanonicalGrantRow. The retired declaration file has been deleted; "
            "see docs/08-migration/canonical-grants-ownership-decision.md."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.catalog import ReferenceCatalogEntryRow
from prodavan.infrastructure.persistence.models.equipment_v2 import (
    EquipmentBudgetAllocationRow,
    EquipmentCandidateGroupRow,
    EquipmentCatalogOfferRow,
    EquipmentCatalogSnapshotRow,
    EquipmentIngestionRunRow,
    EquipmentRequestLineRow,
    EquipmentRequestRow,
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
from prodavan.infrastructure.persistence.models.external_api import (
    ApiAppKeyRow,
    WebhookDeliveryRow,
    WebhookSubscriptionRow,
)
from prodavan.infrastructure.persistence.models.dynamic_app_grants import DynamicAppCapabilityGrantRow
from prodavan.infrastructure.persistence.models.owner_registry import (
    ScopeOwnerAuditRow,
    ScopeOwnerMappingRow,
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
# T00 blocker: canonical_grants.CanonicalGrantRow claims the already-registered
# dynamic_app_capability_grants table owned by DynamicAppCapabilityGrantRow.
# Do not import canonical_grants until its table-ownership conflict is resolved.
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
    "AgentEventRow",
    "AgentRunRow",
    "AgentSessionRow",
    "AgentTurnRow",
    "ApprovalRequestRow",
    "ApiAppKeyRow",
    "WebhookDeliveryRow",
    "WebhookSubscriptionRow",
    "AgentUsageRow",
    "DynamicAppCapabilityGrantRow",
    "ScopeOwnerAuditRow",
    "ScopeOwnerMappingRow",
    "ScopeOwnerQuarantineRow",
    "ScopeRegistryRow",
    "DynamicAppDefinitionFoundationRow",
    "DynamicAppDefinitionVersionFoundationRow",
    "M04EDefinitionVersionPromotionEvidenceRow",
    "PluginInstanceRegistryRow",
    "PluginInstanceRegistryLinkRow",
    "M05PluginInstancePromotionEvidenceRow",
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
    "ContentAclEntryRow",
    "ContentAliasBindingRow",
    "ContentAliasRow",
    "ContentAssetLinkRow",
    "ContentAssetRow",
    "ContentBlobVersionRow",
    "EquipmentBudgetAllocationRow",
    "EquipmentCandidateGroupRow",
    "EquipmentCatalogOfferRow",
    "EquipmentCatalogSnapshotRow",
    "EquipmentIngestionRunRow",
    "EquipmentRequestLineRow",
    "EquipmentRequestRow",
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

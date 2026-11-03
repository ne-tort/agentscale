"""Runtime metadata acceptance for migration model registration."""

from __future__ import annotations

import prodavan.infrastructure.persistence.models as models
from prodavan.infrastructure.persistence.models.base import Base


READY_MODELS = {
    "CanonicalTenantAnchorRow": "scope_owner_tenant_anchors",
    "ScopeOwnerCabinetAnchorRow": "scope_owner_cabinet_anchors",
    "ScopeOwnerProjectAnchorRow": "scope_owner_project_anchors",
    "ScopeRegistryRow": "scope_registry",
    "ScopeOwnerMappingRow": "scope_owner_mappings",
    "ScopeOwnerQuarantineRow": "scope_owner_quarantine",
    "ScopeOwnerAuditRow": "scope_owner_audit",
    "DynamicAppDefinitionFoundationRow": "m04e_dynamic_app_definitions",
    "DynamicAppDefinitionVersionFoundationRow": "m04e_dynamic_app_versions",
    "M04EDefinitionVersionPromotionEvidenceRow": "m04e_definition_version_promotion_evidence",
    "PluginInstanceRegistryRow": "m05_plugin_instances",
    "PluginInstanceRegistryLinkRow": "m05_plugin_instance_registry_links",
    "M05PluginInstancePromotionEvidenceRow": "m05_plugin_instance_promotion_evidence",
}


def _mapped_owners(table) -> list[type]:
    return [
        mapper.class_
        for mapper in Base.registry.mappers
        if mapper.local_table is table
    ]


def test_ready_migration_models_are_registered_once_in_metadata() -> None:
    for class_name, table_name in READY_MODELS.items():
        model = getattr(models, class_name)
        assert model.__tablename__ == table_name
        assert Base.metadata.tables[table_name] is model.__table__
        assert _mapped_owners(model.__table__) == [model]


def test_legacy_grant_owns_table_and_canonical_grant_is_excluded() -> None:
    legacy_model = models.DynamicAppCapabilityGrantRow
    legacy_table = legacy_model.__table__

    assert legacy_table.name == "dynamic_app_capability_grants"
    assert Base.metadata.tables[legacy_table.name] is legacy_table
    assert _mapped_owners(legacy_table) == [legacy_model]
    assert "CanonicalGrantRow" not in models.__all__
    assert not hasattr(models, "CanonicalGrantRow")
    assert all(mapper.class_.__name__ != "CanonicalGrantRow" for mapper in Base.registry.mappers)

"""Runtime metadata acceptance for migration model registration.

Ownership after MIG-OWNERSHIP-CLEANUP-01: every physical table has exactly one
declaring model. In particular ``dynamic_app_capability_grants`` is owned by
``models/canonical_grants.py::CanonicalGrantRow``; the retired
``models/dynamic_app_grants.py::DynamicAppCapabilityGrantRow`` duplicate is
deleted and the name fails closed through the models package ``__getattr__``.
The legacy ``dynamic_app_grants`` / ``AppGrantRow`` projection is a different
physical table and is not covered here.

Authored under the migration verification freeze: not executed in this task.
"""

from __future__ import annotations

import pytest

import prodavan.infrastructure.persistence.models as models
from prodavan.infrastructure.persistence.models import canonical_grants
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
    # All six canonical grant classes are registered; the first one owns the
    # table the retired duplicate used to claim.
    "CanonicalGrantRow": "dynamic_app_capability_grants",
    "CanonicalGrantMappingRow": "dynamic_app_capability_grant_mappings",
    "CanonicalGrantQuarantineRow": "dynamic_app_capability_grant_quarantine",
    "CanonicalGrantAuditRow": "dynamic_app_capability_audit",
    "CanonicalGrantWriterEpochRow": "dynamic_app_capability_writer_epochs",
    "M06CanonicalGrantPromotionEvidenceRow": "m06_canonical_grant_promotion_evidence",
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


def test_canonical_grant_owns_the_capability_grant_table() -> None:
    owner = models.CanonicalGrantRow
    table = owner.__table__

    assert owner is canonical_grants.CanonicalGrantRow
    assert table.name == "dynamic_app_capability_grants"
    assert Base.metadata.tables[table.name] is table
    assert _mapped_owners(table) == [owner]
    assert "CanonicalGrantRow" in models.__all__


def test_declared_canonical_owner_map_matches_metadata() -> None:
    assert set(canonical_grants.M06_TABLE_OWNERS) <= set(Base.metadata.tables)

    for table_name, class_name in sorted(canonical_grants.M06_TABLE_OWNERS.items()):
        model = getattr(models, class_name)
        assert model.__tablename__ == table_name
        assert Base.metadata.tables[table_name] is model.__table__
        assert _mapped_owners(model.__table__) == [model]
        assert class_name in models.__all__


def test_retired_duplicate_grant_model_is_absent_and_fails_closed() -> None:
    assert "DynamicAppCapabilityGrantRow" not in models.__all__
    assert not hasattr(models, "DynamicAppCapabilityGrantRow")
    assert all(
        mapper.class_.__name__ != "DynamicAppCapabilityGrantRow"
        for mapper in Base.registry.mappers
    )
    with pytest.raises(AttributeError, match="retired"):
        models.DynamicAppCapabilityGrantRow  # noqa: B018 — attribute access is the assertion

"""Trusted tenant context and the canonical RLS policy registry.

Two responsibilities, one package:

* :mod:`~prodavan.infrastructure.persistence.rls.context` - the only supported
  way to set transaction-local ``app.tenant_id`` / ``app.cabinet_id`` /
  ``app.project_id`` from a server-resolved principal;
* :mod:`~prodavan.infrastructure.persistence.rls.policies` - the declarative
  registry of canonical tables and policies installed by Alembic revision
  ``2026110307``.  The revision and the acceptance tests both import it, so a
  table cannot be policied twice or left out silently.

Neither module authorizes a positive canonical write.  Registry rows, parent
``EXISTS`` proofs, definition/version/instance/artifact gates, the trusted
writer epoch and role privileges remain separate requirements.
"""

from prodavan.infrastructure.persistence.rls.context import (
    ROLE_SETTING,
    SERVER_RESOLUTION_SOURCES,
    SERVICE_GUCS,
    TENANT_GUCS,
    USER_SETTING,
    CallerAssertion,
    RlsContextError,
    ServerResolution,
    TenantContext,
    TrustedPrincipal,
    issue_server_resolution,
    trusted_service_transaction,
    trusted_tenant_context,
    trusted_tenant_context_sync,
    trusted_tenant_transaction,
    trusted_tenant_transaction_sync,
)
from prodavan.infrastructure.persistence.rls.policies import (
    CABINET_SETTING,
    CANONICAL_TABLE_POLICIES,
    CANONICAL_TABLE_PREFIXES,
    EXCLUDED_TABLES,
    PROJECT_SETTING,
    REGISTRY_VERSION,
    TENANT_POLICY_SUFFIX,
    TENANT_SETTING,
    TRUSTED_POLICY_SUFFIX,
    TRUSTED_SERVICE_ROLE,
    TRUSTED_SERVICE_SETTING,
    ParentHop,
    RlsRegistryError,
    ScopedColumn,
    TablePolicySpec,
    create_policy_statements,
    downgrade_guard_sql,
    downgrade_statements,
    drop_policy_statements,
    enable_statements,
    excluded_table_names,
    policy_inventory,
    registry_tables,
    relation_exists,
    scoped_column_predicate,
    tenant_predicate,
    trusted_service_predicate,
    uncovered_relations,
    upgrade_guard_sql,
    upgrade_statements,
    validate_registry,
)

__all__ = [
    "CABINET_SETTING",
    "CANONICAL_TABLE_POLICIES",
    "CANONICAL_TABLE_PREFIXES",
    "EXCLUDED_TABLES",
    "PROJECT_SETTING",
    "REGISTRY_VERSION",
    "ROLE_SETTING",
    "SERVER_RESOLUTION_SOURCES",
    "SERVICE_GUCS",
    "TENANT_GUCS",
    "TENANT_POLICY_SUFFIX",
    "TENANT_SETTING",
    "TRUSTED_POLICY_SUFFIX",
    "TRUSTED_SERVICE_ROLE",
    "TRUSTED_SERVICE_SETTING",
    "USER_SETTING",
    "CallerAssertion",
    "ParentHop",
    "RlsContextError",
    "RlsRegistryError",
    "ScopedColumn",
    "ServerResolution",
    "TablePolicySpec",
    "TenantContext",
    "TrustedPrincipal",
    "create_policy_statements",
    "downgrade_guard_sql",
    "downgrade_statements",
    "drop_policy_statements",
    "enable_statements",
    "excluded_table_names",
    "issue_server_resolution",
    "policy_inventory",
    "registry_tables",
    "relation_exists",
    "scoped_column_predicate",
    "tenant_predicate",
    "trusted_service_predicate",
    "trusted_service_transaction",
    "trusted_tenant_context",
    "trusted_tenant_context_sync",
    "trusted_tenant_transaction",
    "trusted_tenant_transaction_sync",
    "uncovered_relations",
    "upgrade_guard_sql",
    "upgrade_statements",
    "validate_registry",
]

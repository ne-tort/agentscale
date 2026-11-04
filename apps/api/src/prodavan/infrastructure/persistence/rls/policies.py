"""Canonical PostgreSQL RLS policy registry for the migration target schema.

This module is the single source of truth for:

* which canonical tables carry tenant/cabinet/project scope;
* the exact policy name, command and predicate installed for each table;
* the transaction-local GUC names the policies read;
* the guarded, idempotent DDL text emitted by Alembic revision ``2026110307``.

It is imported by the revision (so the revision and the acceptance tests can
never drift apart) and by tests.  Installing a policy is *not* a positive-write
gate: upstream M04/M04E/M05/M06 promotion evidence, registry rows, role
privileges and trusted writers remain separate requirements.

Invariants enforced by :func:`validate_registry` at import time:

* every canonical table has exactly one tenant policy and exactly one trusted
  service policy, with unique names;
* a table either exposes a tenant anchor itself or inherits it through an
  explicit typed parent chain - there is no implicit "platform-wide" default;
* every generated predicate compares against ``current_setting(..., true)``
  (unset context is NULL, therefore deny) and never interpolates a value;
* policy DDL is idempotent (``DROP POLICY IF EXISTS`` before ``CREATE POLICY``)
  and guarded by ``to_regclass(...)`` so a partial schema cannot raise;
* every rendered policy name fits PostgreSQL's 63-byte identifier limit, so the
  name stored in ``pg_policy`` is byte-for-byte the name this registry declares
  (the rule is spelled out below).

Policy names and the 63-byte limit
----------------------------------
PostgreSQL truncates an identifier longer than ``NAMEDATALEN - 1`` (63 bytes)
*silently*: ``CREATE POLICY m04e_definition_version_promotion_evidence_trusted_service_access``
(65 characters) is stored as ``..._trusted_service_acce``.  Anything comparing
the stored name with the declared one then never matches, and two distinct
declared names could truncate to the same stored name - a ``CREATE POLICY``
collision or, worse, one table's policy silently replaced by another's.

The rule here is deterministic and applies to every table without exception:

1. the name is ``<table>_<full suffix>`` (``tenant_isolation`` /
   ``trusted_service_access``) when that fits in :data:`POLICY_NAME_MAX_LENGTH`;
2. otherwise the **compact suffix for the same kind** is used (``tenant`` /
   ``trusted_access``), which is how ``m04e_definition_version_promotion_evidence``
   gets ``m04e_definition_version_promotion_evidence_trusted_access`` (57);
3. the table part is never shortened - a policy name always begins with the
   exact table it belongs to, so names stay unambiguous;
4. a table whose name is so long that even the compact form does not fit stops
   the registry at import time (:class:`RlsRegistryError`).  Truncation is never
   a fallback: fail closed, never degrade silently.

:func:`installed_policy_name` is the single way to map a rendered name to the
name PostgreSQL stores; catalog comparisons go through it so the "declared vs
stored" mismatch cannot come back by hand.

Review notes / deliberate exclusions live in :data:`EXCLUDED_TABLES`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import text

REGISTRY_VERSION: Final = "2026110307.1"

TENANT_SETTING: Final = "app.tenant_id"
CABINET_SETTING: Final = "app.cabinet_id"
PROJECT_SETTING: Final = "app.project_id"
TRUSTED_SERVICE_SETTING: Final = "app.trusted_service"

#: Database role that is allowed to read across tenant boundaries.  The policy
#: additionally requires ``app.trusted_service='true'`` so that merely reusing
#: the role name does not silently widen access.
TRUSTED_SERVICE_ROLE: Final = "prodavan_service"

TENANT_POLICY_SUFFIX: Final = "tenant_isolation"
TRUSTED_POLICY_SUFFIX: Final = "trusted_service_access"

PolicyCommand = Literal["SELECT", "ALL"]

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}")


class RlsRegistryError(RuntimeError):
    """The static policy registry is internally inconsistent."""


def _require_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise RlsRegistryError(f"{field} must be a plain lowercase SQL identifier, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class ParentHop:
    """One typed parent step used to inherit a scope column.

    ``on_column`` is the column on the *previous* relation (the child table for
    the first hop, the previous parent alias afterwards).  ``key`` is the
    referenced column on ``table``.  ``value_column`` supplies the scoped value
    and is required only on the **last** hop of a chain.
    """

    table: str
    on_column: str
    key: str = "id"
    value_column: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.table, "parent hop table")
        _require_identifier(self.on_column, "parent hop on_column")
        _require_identifier(self.key, "parent hop key")
        if self.value_column is not None:
            _require_identifier(self.value_column, "parent hop value_column")


@dataclass(frozen=True, slots=True)
class ScopedColumn:
    """Where one canonical scope value lives for a table.

    Exactly one of ``column`` (direct column on the table) or ``chain``
    (inherited through typed parents) is set.  ``allows_null`` mirrors models
    where the scope anchor is optional (for example a tenant-scoped row that has
    no cabinet); it never allows a NULL to match another tenant.
    """

    column: str | None = None
    chain: tuple[ParentHop, ...] = ()
    allows_null: bool = False

    def __post_init__(self) -> None:
        if (self.column is None) == (not self.chain):
            raise RlsRegistryError("scoped column needs exactly one of a direct column or a parent chain")
        if self.column is not None:
            _require_identifier(self.column, "scoped column")
        if any(not isinstance(hop, ParentHop) for hop in self.chain):
            raise RlsRegistryError("parent chain entries must be ParentHop values")
        if self.chain and self.chain[-1].value_column is None:
            raise RlsRegistryError("the last parent hop must name the scope value column")
        if any(hop.value_column is not None for hop in self.chain[:-1]):
            raise RlsRegistryError("only the last parent hop may name a scope value column")


@dataclass(frozen=True, slots=True)
class TablePolicySpec:
    """One canonical table and the policies that isolate it."""

    table: str
    owner_revision: str
    tenant: ScopedColumn | None
    cabinet: ScopedColumn | None = None
    project: ScopedColumn | None = None
    tenant_restriction: str | None = None
    trusted_command: PolicyCommand = "SELECT"
    notes: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.table, "table")
        if not self.owner_revision.strip():
            raise RlsRegistryError(f"{self.table}: owner revision is required")
        if self.tenant is None and (self.cabinet is not None or self.project is not None):
            raise RlsRegistryError(f"{self.table}: cabinet/project scope without a tenant anchor")
        if self.cabinet is None and self.project is not None:
            raise RlsRegistryError(f"{self.table}: project scope without a cabinet anchor")
        if self.trusted_command not in ("SELECT", "ALL"):
            raise RlsRegistryError(f"{self.table}: unsupported trusted policy command")
        if self.tenant_restriction is not None:
            restriction = self.tenant_restriction
            if ";" in restriction or "--" in restriction or "/*" in restriction:
                raise RlsRegistryError(f"{self.table}: restriction must be a single predicate")
            if self.table not in restriction:
                raise RlsRegistryError(f"{self.table}: restriction must be table-qualified")

    # -- naming -----------------------------------------------------------
    @property
    def tenant_policy(self) -> str:
        return f"{self.table}_{TENANT_POLICY_SUFFIX}"

    @property
    def trusted_policy(self) -> str:
        return f"{self.table}_{TRUSTED_POLICY_SUFFIX}"

    @property
    def policy_names(self) -> tuple[str, str]:
        return (self.tenant_policy, self.trusted_policy)


# ---------------------------------------------------------------------------
# Predicate rendering
# ---------------------------------------------------------------------------


def _guc(setting: str) -> str:
    """Render a GUC read; empty string is treated exactly like unset context."""

    return f"nullif(current_setting('{setting}', true), '')"


def _comparison(value: str, setting: str, *, allows_null: bool) -> str:
    if allows_null:
        return f"({value} IS NULL OR {value} = {_guc(setting)})"
    return f"{value} = {_guc(setting)}"


def _chain_predicate(parent_ref: str, chain: tuple[ParentHop, ...], depth: int, setting: str, allows_null: bool) -> str:
    hop = chain[0]
    alias = f"rls_{depth}"
    join = f"{alias}.{hop.key} = {parent_ref}.{hop.on_column}"
    if len(chain) == 1:
        # ``value_column`` is guaranteed by ScopedColumn validation.
        leaf = _comparison(f"{alias}.{hop.value_column}", setting, allows_null=allows_null)
        return f"EXISTS (SELECT 1 FROM {hop.table} AS {alias} WHERE {join} AND {leaf})"
    inner = _chain_predicate(alias, chain[1:], depth + 1, setting, allows_null)
    return f"EXISTS (SELECT 1 FROM {hop.table} AS {alias} WHERE {join} AND {inner})"


def scoped_column_predicate(spec: TablePolicySpec, scoped: ScopedColumn, setting: str) -> str:
    """Render the predicate that binds ``scoped`` to the transaction-local GUC."""

    if scoped.column is not None:
        return _comparison(f"{spec.table}.{scoped.column}", setting, allows_null=scoped.allows_null)
    return _chain_predicate(spec.table, scoped.chain, 0, setting, scoped.allows_null)


def tenant_predicate(spec: TablePolicySpec) -> str:
    """The full tenant/cabinet/project predicate shared by USING and WITH CHECK.

    A table without a tenant anchor (global migration evidence) renders ``false``:
    tenant sessions are denied and only the trusted service policy may reach it.
    """

    if spec.tenant is None:
        return "false"
    parts = [scoped_column_predicate(spec, spec.tenant, TENANT_SETTING)]
    if spec.cabinet is not None:
        parts.append(scoped_column_predicate(spec, spec.cabinet, CABINET_SETTING))
    if spec.project is not None:
        parts.append(scoped_column_predicate(spec, spec.project, PROJECT_SETTING))
    if spec.tenant_restriction is not None:
        parts.append(f"({spec.tenant_restriction})")
    return " AND ".join(parts)


def trusted_service_predicate() -> str:
    """Predicate for the cross-tenant service/trusted role."""

    return (
        f"current_user = '{TRUSTED_SERVICE_ROLE}' "
        f"AND {_guc(TRUSTED_SERVICE_SETTING)} = 'true'"
    )


# ---------------------------------------------------------------------------
# DDL statements
# ---------------------------------------------------------------------------


def enable_statements(spec: TablePolicySpec) -> tuple[str, str]:
    return (
        f"ALTER TABLE {spec.table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {spec.table} FORCE ROW LEVEL SECURITY",
    )


def create_policy_statements(spec: TablePolicySpec) -> tuple[str, str]:
    trusted = trusted_service_predicate()
    if spec.trusted_command == "SELECT":
        trusted_statement = (
            f"CREATE POLICY {spec.trusted_policy} ON {spec.table} FOR SELECT USING ({trusted})"
        )
    else:
        trusted_statement = (
            f"CREATE POLICY {spec.trusted_policy} ON {spec.table} FOR ALL "
            f"USING ({trusted}) WITH CHECK ({trusted})"
        )
    predicate = tenant_predicate(spec)
    tenant_statement = (
        f"CREATE POLICY {spec.tenant_policy} ON {spec.table} FOR ALL "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )
    return (tenant_statement, trusted_statement)


def drop_policy_statements(spec: TablePolicySpec) -> tuple[str, str]:
    return (
        f"DROP POLICY IF EXISTS {spec.tenant_policy} ON {spec.table}",
        f"DROP POLICY IF EXISTS {spec.trusted_policy} ON {spec.table}",
    )


def upgrade_statements(spec: TablePolicySpec) -> tuple[str, ...]:
    """Idempotent enable+create statements (policies are dropped first)."""

    return (*enable_statements(spec), *drop_policy_statements(spec), *create_policy_statements(spec))


def downgrade_statements(spec: TablePolicySpec) -> tuple[str, ...]:
    """Idempotent policy removal; data is never touched."""

    return (
        *drop_policy_statements(spec),
        f"ALTER TABLE {spec.table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {spec.table} DISABLE ROW LEVEL SECURITY",
    )


def _guarded_do_block(spec: TablePolicySpec, statements: tuple[str, ...], direction: str) -> str:
    body = "\n".join(f"        {statement};" for statement in statements)
    # No ``%`` placeholders: the block is executed as literal SQL through
    # Alembic and must not depend on the DBAPI paramstyle.
    notice = f"rls 2026110307 {direction} skipped: relation {spec.table} is absent"
    return (
        "DO $rls$\n"
        "BEGIN\n"
        f"    IF to_regclass('{spec.table}') IS NULL THEN\n"
        f"        RAISE NOTICE '{notice}';\n"
        "        RETURN;\n"
        "    END IF;\n"
        f"{body}\n"
        "END\n"
        "$rls$"
    )


def upgrade_guard_sql(spec: TablePolicySpec) -> str:
    """One atomic, idempotent, existence-guarded upgrade block for ``spec``.

    A missing relation is *not* an error: sibling revisions create these tables
    and the revision may run against a partial schema.  The skip is reported as
    a PostgreSQL NOTICE and the revision logs it separately, so a silently
    unpolicied table cannot pass unnoticed.
    """

    return _guarded_do_block(spec, upgrade_statements(spec), "upgrade")


def downgrade_guard_sql(spec: TablePolicySpec) -> str:
    return _guarded_do_block(spec, downgrade_statements(spec), "downgrade")


# ---------------------------------------------------------------------------
# Catalog helpers (read-only; used by the revision log and by tests)
# ---------------------------------------------------------------------------


def relation_exists(connection, table: str) -> bool:
    """Resolve ``table`` with the connection search_path, without raising."""

    result = connection.execute(
        text("SELECT to_regclass(:relation) AS relation"), {"relation": _require_identifier(table, "table")}
    )
    return result.scalar_one() is not None


# ---------------------------------------------------------------------------
# Canonical registry
# ---------------------------------------------------------------------------

_CANONICAL: tuple[TablePolicySpec, ...] = (
    # --- M04 owner registry / canonical tenant-scope evidence (2026110301) ---
    TablePolicySpec(
        table="scope_owner_tenant_anchors",
        owner_revision="2026110301",
        tenant=ScopedColumn(column="tenant_id"),
        notes="Typed retained tenant anchor; one row per canonical companies.id.",
    ),
    TablePolicySpec(
        table="scope_owner_cabinet_anchors",
        owner_revision="2026110301",
        tenant=ScopedColumn(column="tenant_id"),
        cabinet=ScopedColumn(column="cabinet_id"),
        notes="Cabinet anchor; cabinet membership proof stays a trusted-transaction check.",
    ),
    TablePolicySpec(
        table="scope_owner_project_anchors",
        owner_revision="2026110301",
        tenant=ScopedColumn(column="tenant_id"),
        cabinet=ScopedColumn(column="cabinet_id"),
        project=ScopedColumn(column="project_id"),
        notes="Project anchor carries the tenant/cabinet/project compatibility tuple.",
    ),
    TablePolicySpec(
        table="scope_registry",
        owner_revision="2026110301",
        tenant=ScopedColumn(column="tenant_id"),
        cabinet=ScopedColumn(column="cabinet_id", allows_null=True),
        project=ScopedColumn(column="project_id", allows_null=True),
        tenant_restriction="scope_registry.owner_state = 'active'",
        notes=(
            "Typed scope identity. Tenant sessions see only active rows; archived and "
            "quarantined evidence stays reachable through the trusted service policy."
        ),
    ),
    TablePolicySpec(
        table="scope_owner_mappings",
        owner_revision="2026110301",
        tenant=ScopedColumn(
            chain=(ParentHop(table="scope_registry", on_column="scope_registry_id", value_column="tenant_id"),),
        ),
        trusted_command="ALL",
        notes=(
            "Source-identity mapping has no tenant column; ownership is inherited through "
            "scope_registry. An unresolved (NULL) registry link is invisible to tenants."
        ),
    ),
    TablePolicySpec(
        table="scope_owner_quarantine",
        owner_revision="2026110301",
        tenant=ScopedColumn(column="resolved_tenant_id"),
        trusted_command="ALL",
        notes=(
            "Redacted quarantine evidence. Rows without a resolved tenant are tenant-invisible "
            "and remain available to migration/security review through the trusted policy."
        ),
    ),
    TablePolicySpec(
        table="scope_owner_audit",
        owner_revision="2026110301",
        tenant=None,
        trusted_command="ALL",
        notes="Append-shaped migration audit has no tenant column; tenant sessions are denied.",
    ),
    # --- M04.e definition/version foundation (2026110302) ---
    TablePolicySpec(
        table="m04e_dynamic_app_definitions",
        owner_revision="2026110302",
        tenant=ScopedColumn(column="tenant_id"),
        notes="Definition owner tenant; scope proof remains the registry/anchor relation.",
    ),
    TablePolicySpec(
        table="m04e_dynamic_app_versions",
        owner_revision="2026110302",
        tenant=ScopedColumn(column="tenant_id"),
        notes="Version snapshot inherits the definition tenant through the composite FK.",
    ),
    TablePolicySpec(
        table="m04e_definition_version_promotion_evidence",
        owner_revision="2026110302",
        tenant=ScopedColumn(column="tenant_id"),
        notes="Immutable review evidence; readable in-tenant, writable only by trusted writers.",
    ),
    # --- M04E backfill ledger (2026110315, task MIG-DDL-M04E-LEDGER-01) ---
    # The four tables the M04E EvidenceStore port needs: per-source first mapping,
    # changed-digest observations, durable cursor and redacted audit. Every one
    # carries its own ``tenant_id`` (the ledger is keyed by
    # ``(tenant_id, definition_id)`` and has no FK to the definition row, so a
    # blocked page can still record evidence for an unresolvable definition), hence
    # a direct tenant anchor instead of a parent chain. The mapping/observations are
    # written by the trusted migration writer only, so they need an ALL command for
    # the trusted policy; the checkpoints row is mutated by that writer as well.
    TablePolicySpec(
        table="m04e_definition_version_mappings",
        owner_revision="2026110315",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes=(
            "First mapping ever recorded for one exact source identity; append-only, "
            "written by the trusted migration writer. Readable in-tenant."
        ),
    ),
    TablePolicySpec(
        table="m04e_definition_version_quarantine",
        owner_revision="2026110315",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes=(
            "Redacted changed-digest observation. Append-only; only reason codes, "
            "digests and actionable guidance, never payload."
        ),
    ),
    TablePolicySpec(
        table="m04e_definition_version_checkpoints",
        owner_revision="2026110315",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes=(
            "Durable per-partition cursor read FOR UPDATE and advanced by CAS. The "
            "trusted migration writer needs both SELECT and UPDATE, so the trusted "
            "policy is FOR ALL."
        ),
    ),
    TablePolicySpec(
        table="m04e_definition_version_audit",
        owner_revision="2026110315",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes=(
            "Append-only redacted page audit: event kind, reason code, correlation, "
            "checkpoint, actor and numeric counters. UPDATE/DELETE are denied by the "
            "revision's append-only trigger, not by the policy."
        ),
    ),
    # --- M04 accepted scope/RLS evidence (2026110313) ---
    # MIG-DDL-M04EVID-01 creates this table; the policy itself is installed by the next
    # registry re-apply revision, because a table-adding revision cannot install its own
    # policy after the fact (see 2026110312). Membership here keeps the coverage report
    # honest in the meantime: an unregistered sibling would be reported, not skipped.
    # REGISTRY_VERSION is deliberately not bumped here: it also names the reviewed RLS
    # manifest through application/migration/tenant_context.py::SECURITY_REVIEW_ID, and
    # re-approving that identity belongs to the RLS follow-up task.
    TablePolicySpec(
        table="m04_scope_promotion_evidence",
        owner_revision="2026110313",
        tenant=ScopedColumn(column="tenant_id"),
        notes=(
            "Accepted M04-02 scope/RLS evidence row; the only M04 promotion-evidence "
            "table whose outcome may read 'ready'. Readable in-tenant, append-only for "
            "the trusted migration writer."
        ),
    ),
    # --- M05 plugin instance registry (2026110303) ---
    TablePolicySpec(
        table="m05_plugin_instances",
        owner_revision="2026110303",
        tenant=ScopedColumn(column="tenant_id"),
        cabinet=ScopedColumn(column="cabinet_id", allows_null=True),
        project=ScopedColumn(column="project_id", allows_null=True),
        notes="Exact plugin instance identity at its installation scope.",
    ),
    TablePolicySpec(
        table="m05_plugin_instance_registry_links",
        owner_revision="2026110303",
        tenant=ScopedColumn(column="tenant_id"),
        cabinet=ScopedColumn(column="cabinet_id", allows_null=True),
        project=ScopedColumn(column="project_id", allows_null=True),
        notes="Staged reciprocal registry link evidence.",
    ),
    TablePolicySpec(
        table="m05_plugin_instance_promotion_evidence",
        owner_revision="2026110303",
        tenant=ScopedColumn(column="tenant_id"),
        notes="Instance promotion record; canonical_positive_writes stays false.",
    ),
    # --- M06 canonical grants (2026110304) ---
    TablePolicySpec(
        table="dynamic_app_capability_grants",
        owner_revision="2026110304",
        tenant=ScopedColumn(column="tenant_id"),
        cabinet=ScopedColumn(column="cabinet_id", allows_null=True),
        project=ScopedColumn(column="project_id", allows_null=True),
        notes=(
            "Canonical grant. Definition/version/instance/scope-registry proof and the trusted "
            "writer epoch remain application/database gates; RLS only bounds tenant visibility."
        ),
    ),
    TablePolicySpec(
        table="dynamic_app_capability_grant_mappings",
        owner_revision="2026110304",
        tenant=ScopedColumn(
            chain=(
                ParentHop(
                    table="dynamic_app_capability_grants",
                    on_column="target_grant_id",
                    value_column="tenant_id",
                ),
            )
        ),
        trusted_command="ALL",
        notes="Mapping evidence inherits the target grant tenant; unresolved mappings are tenant-invisible.",
    ),
    TablePolicySpec(
        table="dynamic_app_capability_grant_quarantine",
        owner_revision="2026110304",
        tenant=ScopedColumn(column="resolved_tenant_id"),
        trusted_command="ALL",
        notes="Quarantine evidence inherits the resolved tenant; unresolved rows stay with trusted review.",
    ),
    TablePolicySpec(
        table="dynamic_app_capability_audit",
        owner_revision="2026110304",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes="Audit history: nullable tenant rows are platform-level and tenant-invisible.",
    ),
    TablePolicySpec(
        table="dynamic_app_capability_writer_epochs",
        owner_revision="2026110304",
        tenant=ScopedColumn(column="tenant_id"),
        notes="Trusted writer fence state per bounded partition.",
    ),
    TablePolicySpec(
        table="m06_canonical_grant_promotion_evidence",
        owner_revision="2026110304",
        tenant=ScopedColumn(column="tenant_id"),
        notes="Immutable promotion record; never implies a positive writer.",
    ),
    # --- M06 partition cutover ledger + live source tail (2026110316) ---
    # MIG-DDL-CUTOVER-01 creates these two tables and re-applies this registry in the
    # same revision, so no table here is left for "the next pass" (the defect
    # 2026110309/2026110311 shipped twice, see test_policy_install_order).
    TablePolicySpec(
        table="m06_partition_cutover_evidence",
        owner_revision="2026110316",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes=(
            "Append-only cutover ledger: one row per partition transition carrying the "
            "PartitionCutover snapshot and its PromotionEvidence record. The trusted "
            "migration writer needs SELECT and INSERT, so the trusted policy is FOR ALL; "
            "UPDATE/DELETE are denied by the revision's append-only trigger, not by the "
            "policy. Tenant identity is the only RLS anchor - the partition is "
            "(tenant_id, app_definition_id) and the state CAS is a unique key, not a "
            "policy predicate."
        ),
    ),
    TablePolicySpec(
        table="m06_partition_cutover_source_tail",
        owner_revision="2026110316",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes=(
            "Live legacy source tail per partition, read by the cutover CAS and written "
            "by the legacy writer, which advances it in place: the trusted writer needs "
            "UPDATE as well, hence FOR ALL. A missing tail row is what makes a "
            "final-delta transition refuse, so no policy ever synthesises one."
        ),
    ),
    # --- Runtime revisions / materialization (2026110306) ---
    TablePolicySpec(
        table="project_runtime_revisions",
        owner_revision="2026110306",
        tenant=ScopedColumn(
            chain=(ParentHop(table="projects", on_column="project_id", value_column="company_id"),),
        ),
        cabinet=ScopedColumn(
            chain=(ParentHop(table="projects", on_column="project_id", value_column="cabinet_id"),),
        ),
        project=ScopedColumn(column="project_id"),
        notes=(
            "Runtime revision has no tenant column: tenant/cabinet are proven through "
            "projects(company_id, cabinet_id) and the project GUC must match project_id."
        ),
    ),
    TablePolicySpec(
        table="materialization_entries",
        owner_revision="2026110306",
        tenant=ScopedColumn(
            chain=(
                ParentHop(table="project_runtime_revisions", on_column="revision_id"),
                ParentHop(table="projects", on_column="project_id", value_column="company_id"),
            ),
        ),
        cabinet=ScopedColumn(
            chain=(
                ParentHop(table="project_runtime_revisions", on_column="revision_id"),
                ParentHop(table="projects", on_column="project_id", value_column="cabinet_id"),
            ),
        ),
        project=ScopedColumn(
            chain=(
                ParentHop(table="project_runtime_revisions", on_column="revision_id"),
                ParentHop(table="projects", on_column="project_id", value_column="id"),
            ),
        ),
        notes=(
            "Materialization entry inherits the whole scope from "
            "project_runtime_revisions -> projects; it has no copied tenant column."
        ),
    ),
    # --- Equipment v2 (2026110305) ---
    # The catalog side is tenant-owned through the ingestion run; only the run
    # carries a tenant column, so every catalog child inherits it hop by hop
    # instead of duplicating a tenant anchor that could drift.
    TablePolicySpec(
        table="equipment_v2_ingestion_runs",
        owner_revision="2026110305",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes="Root of the catalog ingestion chain; the only catalog table with a tenant anchor.",
    ),
    TablePolicySpec(
        table="equipment_v2_catalog_snapshots",
        owner_revision="2026110305",
        tenant=ScopedColumn(
            chain=(ParentHop(table="equipment_v2_ingestion_runs", on_column="ingestion_run_id", value_column="tenant_id"),),
        ),
        trusted_command="ALL",
        notes="Snapshot inherits the run tenant; a snapshot never crosses its ingestion run.",
    ),
    TablePolicySpec(
        table="equipment_v2_catalog_offers",
        owner_revision="2026110305",
        tenant=ScopedColumn(
            chain=(
                ParentHop(table="equipment_v2_catalog_snapshots", on_column="snapshot_id"),
                ParentHop(table="equipment_v2_ingestion_runs", on_column="ingestion_run_id", value_column="tenant_id"),
            ),
        ),
        trusted_command="ALL",
        notes="Offer inherits the tenant through snapshot -> ingestion run; it has no tenant column of its own.",
    ),
    TablePolicySpec(
        table="equipment_v2_requests",
        owner_revision="2026110305",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes="Root of the procurement request chain.",
    ),
    TablePolicySpec(
        table="equipment_v2_request_lines",
        owner_revision="2026110305",
        tenant=ScopedColumn(
            chain=(ParentHop(table="equipment_v2_requests", on_column="request_id", value_column="tenant_id"),),
        ),
        trusted_command="ALL",
        notes="Request line inherits the request tenant.",
    ),
    TablePolicySpec(
        table="equipment_v2_candidate_groups",
        owner_revision="2026110305",
        tenant=ScopedColumn(
            chain=(
                ParentHop(table="equipment_v2_request_lines", on_column="request_line_id"),
                ParentHop(table="equipment_v2_requests", on_column="request_id", value_column="tenant_id"),
            ),
        ),
        trusted_command="ALL",
        notes=(
            "Candidate group inherits the tenant through line -> request. "
            "selected_offer_id is a cross-aggregate link and is not an authorization anchor."
        ),
    ),
    TablePolicySpec(
        table="equipment_v2_budget_allocations",
        owner_revision="2026110305",
        tenant=ScopedColumn(
            chain=(ParentHop(table="equipment_v2_requests", on_column="request_id", value_column="tenant_id"),),
        ),
        trusted_command="ALL",
        notes="Budget allocation inherits the request tenant.",
    ),
    TablePolicySpec(
        table="equipment_v2_tenders",
        owner_revision="2026110305",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes="Tender carries its own tenant anchor and references the request by identity.",
    ),
    # --- Agent Run execution fence + attempt journal (2026110309, 2026110311) ---
    TablePolicySpec(
        table="agent_run_leases",
        owner_revision="2026110309",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes=(
            "Lease fence row. tenant_id is denormalised from the run so the claim predicate "
            "stays index-only; it is not an authorization anchor. No project/cabinet scope is "
            "declared because the table carries no cabinet anchor and project narrowing is the "
            "claim predicate's job, not RLS policy state."
        ),
    ),
    TablePolicySpec(
        table="agent_run_provider_attempts",
        owner_revision="2026110311",
        tenant=ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes=(
            "Redacted attempt journal: digests, state and counters only. Reconciliation must "
            "read across the tenant boundary, hence a trusted service policy alongside the "
            "tenant policy."
        ),
    ),
    # --- Runtime revision head / attempt / audit (2026110306) ---
    # These carry ``project_id`` only; tenant and cabinet are proven through
    # ``projects``, exactly like ``project_runtime_revisions``.
    TablePolicySpec(
        table="runtime_revision_heads",
        owner_revision="2026110306",
        tenant=ScopedColumn(
            chain=(ParentHop(table="projects", on_column="project_id", value_column="company_id"),),
        ),
        cabinet=ScopedColumn(
            chain=(ParentHop(table="projects", on_column="project_id", value_column="cabinet_id"),),
        ),
        project=ScopedColumn(column="project_id"),
        trusted_command="ALL",
        notes=(
            "Resource-wide CAS head. Has no tenant column: tenant/cabinet are inherited from "
            "projects and the project GUC must match project_id."
        ),
    ),
    TablePolicySpec(
        table="runtime_materialization_attempts",
        owner_revision="2026110306",
        tenant=ScopedColumn(
            chain=(ParentHop(table="projects", on_column="project_id", value_column="company_id"),),
        ),
        cabinet=ScopedColumn(
            chain=(ParentHop(table="projects", on_column="project_id", value_column="cabinet_id"),),
        ),
        project=ScopedColumn(column="project_id"),
        trusted_command="ALL",
        notes="Durable attempt history; scope inherited from projects.",
    ),
    TablePolicySpec(
        table="runtime_materialization_audit",
        owner_revision="2026110306",
        tenant=ScopedColumn(
            chain=(ParentHop(table="projects", on_column="project_id", value_column="company_id"),),
        ),
        cabinet=ScopedColumn(
            chain=(ParentHop(table="projects", on_column="project_id", value_column="cabinet_id"),),
        ),
        project=ScopedColumn(column="project_id"),
        trusted_command="ALL",
        notes="Append-only materialization audit; scope inherited from projects.",
    ),
)

CANONICAL_TABLE_POLICIES: Final[tuple[TablePolicySpec, ...]] = _CANONICAL

#: Tables deliberately left without an RLS policy by this revision, with the
#: reviewed reason.  Anything else in a canonical prefix (see
#: :data:`CANONICAL_TABLE_PREFIXES`) is reported by :func:`uncovered_relations`
#: so an unregistered sibling table cannot pass unnoticed.
EXCLUDED_TABLES: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (
        ("capability_bundles",),
        "Global content-addressed manifest referenced by digest and shared across tenants; "
        "it carries no tenant anchor, so a tenant-scoped policy would deny legitimate "
        "inserts. Ownership is proven through project_runtime_revisions.bundle_id.",
    ),
    (
        ("resources", "resource_versions", "resource_bindings"),
        "Polymorphic (scope_kind, scope_id) resources created by 2026110306 need an EXISTS "
        "binding to scope_registry before policies are safe; deferred to a dedicated RLS "
        "review (blocker recorded, not silently skipped).",
    ),
    (
        ("companies", "cabinet_instances", "cabinet_company_grants", "projects", "memberships"),
        "Legacy platform anchors stay outside the canonical target RLS set; they are read "
        "by the policy predicates above and need a separate platform-level RLS decision.",
    ),
    (
        ("auth_sessions",),
        "MIG-AUTHSESS-01: durable correlation_id -> verified subject store. A platform "
        "anchor like companies/memberships, read *before* a tenant exists (that is how a "
        "correlation id learns which tenant it may bind), so the reader runs with no "
        "app.tenant_id and a tenant-scoped policy would deny the only legitimate read. "
        "Ownership is enforced at write time: only the auth boundary writes it, after "
        "signature/expiry verification (application/auth/auth_sessions.py).",
    ),
)

#: Name prefixes that mark a table as part of the canonical tenant-owned target
#: schema.  Used only for the coverage report, never to install a policy.
CANONICAL_TABLE_PREFIXES: Final[tuple[str, ...]] = (
    "scope_owner_",
    "scope_registry",
    "m04e_",
    # ``m04_scope_promotion_evidence`` matched no prefix before MIG-DDL-M04EVID-01:
    # it is neither ``m04e_`` nor ``scope_``, so a missing RLS decision for it would
    # have been invisible to the coverage report instead of being named.
    "m04_scope_",
    "m05_",
    "m06_",
    "dynamic_app_capability_",
    "project_runtime_revision",
    "materialization_",
    # ``runtime_revision_heads``, ``runtime_materialization_attempts`` and
    # ``runtime_materialization_audit`` were a blind spot: none of the prefixes
    # above matched their names, so the coverage report could not name them and
    # the "nothing uncovered" invariant held vacuously. A prefix that matches a
    # table set nobody registered is exactly what the report exists to catch.
    "runtime_",
    "equipment_v2_",
    "agent_run_",
)


def validate_registry(specs: tuple[TablePolicySpec, ...] = CANONICAL_TABLE_POLICIES) -> None:
    """Reject a registry that would install ambiguous or duplicated policies."""

    tables = [spec.table for spec in specs]
    if len(set(tables)) != len(tables):
        raise RlsRegistryError("each canonical table must be registered exactly once")
    policy_names = [name for spec in specs for name in spec.policy_names]
    if len(set(policy_names)) != len(policy_names):
        raise RlsRegistryError("policy names must be unique across the registry")
    for spec in specs:
        if spec.tenant is not None and not scoped_column_predicate(spec, spec.tenant, TENANT_SETTING):
            raise RlsRegistryError(f"{spec.table}: empty tenant predicate")
        for scoped in (spec.cabinet, spec.project):
            if scoped is not None and not scoped_column_predicate(spec, scoped, TENANT_SETTING):
                raise RlsRegistryError(f"{spec.table}: empty scope predicate")


validate_registry()


def policy_inventory() -> tuple[dict[str, str], ...]:
    """Stable review inventory: one entry per installed policy."""

    inventory: list[dict[str, str]] = []
    for spec in CANONICAL_TABLE_POLICIES:
        tenant_statement, trusted_statement = create_policy_statements(spec)
        inventory.append(
            {
                "table": spec.table,
                "policy": spec.tenant_policy,
                "command": "ALL",
                "owner_revision": spec.owner_revision,
                "definition": tenant_statement,
            }
        )
        inventory.append(
            {
                "table": spec.table,
                "policy": spec.trusted_policy,
                "command": spec.trusted_command,
                "owner_revision": spec.owner_revision,
                "definition": trusted_statement,
            }
        )
    return tuple(inventory)


def registry_tables() -> tuple[str, ...]:
    return tuple(spec.table for spec in CANONICAL_TABLE_POLICIES)


def excluded_table_names() -> frozenset[str]:
    return frozenset(name for names, _ in EXCLUDED_TABLES for name in names)


def uncovered_relations(relation_names: Iterable[str]) -> tuple[str, ...]:
    """Canonical-prefixed relations with neither a policy nor a reviewed exclusion.

    Called by the revision after upgrade with the actual catalog relations (and by
    the acceptance tests with the model metadata), so a sibling revision adding a
    canonical table cannot silently ship without an RLS decision.
    """

    covered = set(registry_tables()) | excluded_table_names()
    return tuple(
        sorted(
            name
            for name in relation_names
            if name.startswith(CANONICAL_TABLE_PREFIXES) and name not in covered
        )
    )


__all__ = [
    "CABINET_SETTING",
    "CANONICAL_TABLE_POLICIES",
    "CANONICAL_TABLE_PREFIXES",
    "EXCLUDED_TABLES",
    "PROJECT_SETTING",
    "ParentHop",
    "PolicyCommand",
    "REGISTRY_VERSION",
    "RlsRegistryError",
    "ScopedColumn",
    "TENANT_POLICY_SUFFIX",
    "TENANT_SETTING",
    "TRUSTED_POLICY_SUFFIX",
    "TRUSTED_SERVICE_ROLE",
    "TRUSTED_SERVICE_SETTING",
    "TablePolicySpec",
    "create_policy_statements",
    "downgrade_guard_sql",
    "downgrade_statements",
    "drop_policy_statements",
    "enable_statements",
    "excluded_table_names",
    "policy_inventory",
    "registry_tables",
    "relation_exists",
    "scoped_column_predicate",
    "tenant_predicate",
    "trusted_service_predicate",
    "uncovered_relations",
    "upgrade_guard_sql",
    "upgrade_statements",
    "validate_registry",
]

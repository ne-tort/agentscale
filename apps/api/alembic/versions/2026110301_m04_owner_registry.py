"""M04 owner registry — canonical tenant anchors, typed scope registry, evidence tables.

Authored by hand against ``models/owner_registry.py`` (source of truth), not by
autogenerate, because every constraint in this revision carries an explicit name
that reviewers and the runtime schema gate match literally.

Object → table map (create order is the dependency order, not alphabetical):

1. ``projects.uq_projects_scope_owner_parent_tuple`` — compatibility key
   ``(id, company_id, cabinet_id)`` installed on the retained ``projects`` table.
   ``ProjectRow`` declares it through
   ``owner_registry._install_parent_compatibility_keys()``; PostgreSQL cannot
   create the composite project FK below without a matching unique constraint on
   the referenced tuple, and the persistence gate looks for it in ``pg_constraint``
   (``contype IN ('u','p')``), so it must be a real unique *constraint* rather
   than a unique index. It is created first and dropped last.
2. ``CanonicalTenantAnchorRow`` → ``scope_owner_tenant_anchors`` — the typed
   alias of the legacy ``companies.id`` tenant key. Everything else references
   it, so it is created first among the new tables.
3. ``ScopeOwnerCabinetAnchorRow`` → ``scope_owner_cabinet_anchors`` — cabinet +
   tenant evidence. Its composite FKs point at ``companies``,
   ``cabinet_instances``, ``cabinet_company_grants`` and
   ``scope_owner_tenant_anchors`` (2), so it must follow (2).
4. ``ScopeOwnerProjectAnchorRow`` → ``scope_owner_project_anchors`` — project /
   tenant / cabinet evidence. References ``projects`` (via 1),
   ``cabinet_company_grants``, ``scope_owner_tenant_anchors`` (2) and
   ``scope_owner_cabinet_anchors`` (3), so it must follow (3).
5. ``ScopeRegistryRow`` → ``scope_registry`` — typed scope identity. Its three
   parent FKs target the *state-bearing* unique keys of (2), (3) and (4), so the
   registry can only exist after all three anchors.
6. ``ScopeOwnerMappingRow`` → ``scope_owner_mappings`` — source identity mapping
   with ``fk_scope_owner_mapping_registry`` onto ``scope_registry.id`` (5).
7. ``ScopeOwnerQuarantineRow`` → ``scope_owner_quarantine`` — redacted
   unresolved evidence; no FK, placed after the registry for readability only.
8. ``ScopeOwnerAuditRow`` → ``scope_owner_audit`` — append-shaped redacted
   evidence; no FK, last so the drop order mirrors it.

Deliberate non-goals of this revision:

* No RLS, no ``FORCE ROW LEVEL SECURITY``, no policies, no privilege grid and no
  append-only trigger on ``scope_owner_audit`` — that is revision ``2026110307``.
  Until it lands, ``scope_owner_audit`` is append-shaped by contract only.
* No M05 plugin-instance objects. ``plugin_instance_registry.py`` appends
  ``uq_scope_registry_m05_instance_*`` and the deferred
  ``fk_m05_registry_instance_*`` constraints to ``scope_registry``; they belong
  to revision ``2026110303``, which creates ``m05_plugin_instances`` first.
* No legacy ``dynamic_app_*`` table is read, altered, backfilled or dropped.
  This revision is pure additive DDL.
* No Python-side ``default=`` (``scope_<uuid>`` id generation) is expressed as a
  server default; identifiers stay application-generated.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026110301"
down_revision = "2026110202"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Retained-parent compatibility key required by the composite project FK.
    op.create_unique_constraint(
        "uq_projects_scope_owner_parent_tuple",
        "projects",
        ["id", "company_id", "cabinet_id"],
    )

    # 2. Canonical tenant anchor — typed alias of companies.id.
    op.create_table(
        "scope_owner_tenant_anchors",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("tenant_type", sa.String(16), nullable=False, server_default="company"),
        sa.Column("owner_state", sa.String(16), nullable=False, server_default="quarantined"),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("payload_digest", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["companies.id"],
            name="fk_scope_owner_tenant_company",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "tenant_type", name="uq_scope_owner_tenant_identity"),
        sa.UniqueConstraint(
            "tenant_id",
            "tenant_type",
            "owner_state",
            name="uq_scope_owner_tenant_identity_state",
        ),
        sa.CheckConstraint("tenant_type = 'company'", name="ck_scope_owner_tenant_type"),
        sa.CheckConstraint(
            "owner_state IN ('active', 'archived', 'quarantined')",
            name="ck_scope_owner_tenant_state",
        ),
        sa.CheckConstraint(
            "tenant_id <> '' AND tenant_id = btrim(tenant_id) "
            "AND tenant_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_scope_owner_tenant_id_well_formed",
        ),
        sa.CheckConstraint("source_kind <> ''", name="ck_scope_owner_tenant_source_kind"),
        sa.CheckConstraint("source_id <> ''", name="ck_scope_owner_tenant_source_id"),
        sa.CheckConstraint("schema_version <> ''", name="ck_scope_owner_tenant_schema"),
        sa.CheckConstraint("payload_digest <> ''", name="ck_scope_owner_tenant_digest"),
    )
    op.create_index(
        "ix_scope_owner_tenant_state",
        "scope_owner_tenant_anchors",
        ["tenant_id", "owner_state"],
    )

    # 3. Cabinet anchor — cabinet/tenant tuple retained as non-destructive evidence.
    op.create_table(
        "scope_owner_cabinet_anchors",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("cabinet_id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("tenant_type", sa.String(16), nullable=False, server_default="company"),
        sa.Column("owner_state", sa.String(16), nullable=False, server_default="quarantined"),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("payload_digest", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["cabinet_id", "tenant_id"],
            ["cabinet_company_grants.cabinet_id", "cabinet_company_grants.company_id"],
            name="fk_scope_owner_cabinet_anchor_company_grant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tenant_type"],
            ["scope_owner_tenant_anchors.tenant_id", "scope_owner_tenant_anchors.tenant_type"],
            name="fk_scope_owner_cabinet_anchor_typed_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["companies.id"],
            name="fk_scope_owner_cabinet_anchor_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cabinet_id"],
            ["cabinet_instances.id"],
            name="fk_scope_owner_cabinet_anchor_cabinet",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cabinet_id",
            "tenant_id",
            "tenant_type",
            name="uq_scope_owner_cabinet_anchor_typed_tuple",
        ),
        sa.UniqueConstraint(
            "cabinet_id",
            "tenant_id",
            "tenant_type",
            "owner_state",
            name="uq_scope_owner_cabinet_anchor_typed_tuple_state",
        ),
        sa.CheckConstraint(
            "tenant_type = 'company'",
            name="ck_scope_owner_cabinet_anchor_tenant_type",
        ),
        sa.CheckConstraint(
            "owner_state IN ('active', 'archived', 'quarantined')",
            name="ck_scope_owner_cabinet_anchor_state",
        ),
        sa.CheckConstraint(
            "cabinet_id <> '' AND tenant_id <> ''",
            name="ck_scope_owner_cabinet_anchor_ids_nonempty",
        ),
        sa.CheckConstraint("source_kind <> ''", name="ck_scope_owner_cabinet_anchor_source_kind"),
        sa.CheckConstraint("source_id <> ''", name="ck_scope_owner_cabinet_anchor_source_id"),
        sa.CheckConstraint("schema_version <> ''", name="ck_scope_owner_cabinet_anchor_schema"),
        sa.CheckConstraint("payload_digest <> ''", name="ck_scope_owner_cabinet_anchor_digest"),
    )
    op.create_index(
        "ix_scope_owner_cabinet_anchor_tenant_state",
        "scope_owner_cabinet_anchors",
        ["tenant_id", "owner_state"],
    )

    # 4. Project anchor — explicit project/tenant/cabinet compatibility evidence.
    op.create_table(
        "scope_owner_project_anchors",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("tenant_type", sa.String(16), nullable=False, server_default="company"),
        sa.Column("cabinet_id", sa.String(40), nullable=False),
        sa.Column("project_status", sa.String(32), nullable=False),
        sa.Column("owner_state", sa.String(16), nullable=False, server_default="quarantined"),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("payload_digest", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "tenant_id", "cabinet_id"],
            ["projects.id", "projects.company_id", "projects.cabinet_id"],
            name="fk_scope_owner_project_anchor_project_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tenant_type"],
            ["scope_owner_tenant_anchors.tenant_id", "scope_owner_tenant_anchors.tenant_type"],
            name="fk_scope_owner_project_anchor_typed_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["companies.id"],
            name="fk_scope_owner_project_anchor_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cabinet_id"],
            ["cabinet_instances.id"],
            name="fk_scope_owner_project_anchor_cabinet",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cabinet_id", "tenant_id", "tenant_type"],
            [
                "scope_owner_cabinet_anchors.cabinet_id",
                "scope_owner_cabinet_anchors.tenant_id",
                "scope_owner_cabinet_anchors.tenant_type",
            ],
            name="fk_scope_owner_project_anchor_cabinet_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cabinet_id", "tenant_id"],
            ["cabinet_company_grants.cabinet_id", "cabinet_company_grants.company_id"],
            name="fk_scope_owner_project_anchor_cabinet_company_grant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "tenant_id",
            "tenant_type",
            "cabinet_id",
            "project_status",
            "owner_state",
            name="uq_scope_owner_project_anchor_typed_tuple_state",
        ),
        sa.CheckConstraint(
            "tenant_type = 'company'",
            name="ck_scope_owner_project_anchor_tenant_type",
        ),
        sa.CheckConstraint(
            "project_status IN ('draft', 'active', 'paused', 'error', 'completed', 'deleted')",
            name="ck_scope_owner_project_anchor_project_status",
        ),
        sa.CheckConstraint(
            "owner_state IN ('active', 'archived', 'quarantined')",
            name="ck_scope_owner_project_anchor_state",
        ),
        sa.CheckConstraint(
            "project_id <> '' AND tenant_id <> '' AND cabinet_id <> ''",
            name="ck_scope_owner_project_anchor_ids_nonempty",
        ),
        sa.CheckConstraint("source_kind <> ''", name="ck_scope_owner_project_anchor_source_kind"),
        sa.CheckConstraint("source_id <> ''", name="ck_scope_owner_project_anchor_source_id"),
        sa.CheckConstraint("schema_version <> ''", name="ck_scope_owner_project_anchor_schema"),
        sa.CheckConstraint("payload_digest <> ''", name="ck_scope_owner_project_anchor_digest"),
    )
    op.create_index(
        "ix_scope_owner_project_anchor_tenant_cabinet",
        "scope_owner_project_anchors",
        ["tenant_id", "cabinet_id"],
    )

    # 5. Typed scope identity — parents are the state-bearing anchor keys above.
    op.create_table(
        "scope_registry",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("tenant_type", sa.String(16), nullable=False, server_default="company"),
        sa.Column("cabinet_id", sa.String(40), nullable=True),
        sa.Column("project_id", sa.String(40), nullable=True),
        sa.Column("project_status", sa.String(32), nullable=True),
        # No FK by design: the reciprocal exact-instance key is owned by M05
        # (revision 2026110303) and plugin rows stay quarantined until then.
        sa.Column("plugin_instance_id", sa.String(40), nullable=True),
        sa.Column("owner_state", sa.String(16), nullable=False, server_default="quarantined"),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("payload_digest", sa.String(128), nullable=False),
        sa.Column("checkpoint", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tenant_type", "owner_state"],
            [
                "scope_owner_tenant_anchors.tenant_id",
                "scope_owner_tenant_anchors.tenant_type",
                "scope_owner_tenant_anchors.owner_state",
            ],
            name="fk_scope_registry_tenant_owner_state",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cabinet_id", "tenant_id", "tenant_type", "owner_state"],
            [
                "scope_owner_cabinet_anchors.cabinet_id",
                "scope_owner_cabinet_anchors.tenant_id",
                "scope_owner_cabinet_anchors.tenant_type",
                "scope_owner_cabinet_anchors.owner_state",
            ],
            name="fk_scope_registry_cabinet_typed_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "project_id",
                "tenant_id",
                "tenant_type",
                "cabinet_id",
                "project_status",
                "owner_state",
            ],
            [
                "scope_owner_project_anchors.project_id",
                "scope_owner_project_anchors.tenant_id",
                "scope_owner_project_anchors.tenant_type",
                "scope_owner_project_anchors.cabinet_id",
                "scope_owner_project_anchors.project_status",
                "scope_owner_project_anchors.owner_state",
            ],
            name="fk_scope_registry_project_typed_parent",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_kind", "scope_id", name="uq_scope_registry_kind_id"),
        sa.UniqueConstraint(
            "scope_kind",
            "scope_id",
            "tenant_id",
            "tenant_type",
            "cabinet_id",
            "project_id",
            "project_status",
            "plugin_instance_id",
            name="uq_scope_registry_typed_anchor",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('tenant', 'cabinet', 'project', 'plugin_instance')",
            name="ck_scope_registry_kind",
        ),
        sa.CheckConstraint("tenant_type IN ('company')", name="ck_scope_registry_tenant_type"),
        sa.CheckConstraint(
            "owner_state IN ('active', 'archived', 'quarantined')",
            name="ck_scope_registry_owner_state",
        ),
        sa.CheckConstraint(
            "scope_id <> '' AND scope_id = btrim(scope_id) "
            "AND scope_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_scope_registry_scope_id_well_formed",
        ),
        sa.CheckConstraint(
            "tenant_id <> '' AND tenant_id = btrim(tenant_id) "
            "AND tenant_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_scope_registry_tenant_id_well_formed",
        ),
        sa.CheckConstraint("source_kind <> ''", name="ck_scope_registry_source_kind_nonempty"),
        sa.CheckConstraint("source_id <> ''", name="ck_scope_registry_source_id_nonempty"),
        sa.CheckConstraint(
            "schema_version <> ''",
            name="ck_scope_registry_schema_version_nonempty",
        ),
        sa.CheckConstraint(
            "payload_digest <> ''",
            name="ck_scope_registry_payload_digest_nonempty",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'tenant' AND scope_id = tenant_id "
            "AND cabinet_id IS NULL AND project_id IS NULL AND project_status IS NULL "
            "AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'cabinet' AND cabinet_id IS NOT NULL AND scope_id = cabinet_id "
            "AND project_id IS NULL AND project_status IS NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'project' AND project_id IS NOT NULL AND scope_id = project_id "
            "AND cabinet_id IS NOT NULL "
            "AND project_status IN ('draft', 'active', 'paused', 'error', 'completed', 'deleted') "
            "AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'plugin_instance' AND plugin_instance_id IS NOT NULL "
            "AND scope_id = plugin_instance_id AND tenant_id IS NOT NULL "
            "AND (project_id IS NULL OR (cabinet_id IS NOT NULL AND project_status IS NOT NULL)))",
            name="ck_scope_registry_typed_shape",
        ),
        sa.CheckConstraint(
            "scope_kind <> 'plugin_instance' OR owner_state = 'quarantined'",
            name="ck_scope_registry_plugin_deferred",
        ),
    )
    op.create_index(
        "ix_scope_registry_tenant_state",
        "scope_registry",
        ["tenant_id", "owner_state"],
    )
    op.create_index(
        "ix_scope_registry_cabinet_project",
        "scope_registry",
        ["cabinet_id", "project_id"],
    )

    # 6. Source-identity mapping — immutable evidence, no writer is enabled here.
    op.create_table(
        "scope_owner_mappings",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("source_payload_digest", sa.String(128), nullable=False),
        sa.Column("canonical_payload_digest", sa.String(128), nullable=True),
        sa.Column("scope_registry_id", sa.String(40), nullable=True),
        sa.Column("mapping_state", sa.String(16), nullable=False, server_default="quarantined"),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("checkpoint", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["scope_registry_id"],
            ["scope_registry.id"],
            name="fk_scope_owner_mapping_registry",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_kind",
            "source_id",
            "schema_version",
            name="uq_scope_owner_mapping_source",
        ),
        sa.CheckConstraint(
            "mapping_state IN ('mapped', 'archived', 'quarantined')",
            name="ck_scope_owner_mapping_state",
        ),
        sa.CheckConstraint(
            "source_kind <> ''",
            name="ck_scope_owner_mapping_source_kind_nonempty",
        ),
        sa.CheckConstraint(
            "source_id <> ''",
            name="ck_scope_owner_mapping_source_id_nonempty",
        ),
        sa.CheckConstraint(
            "schema_version <> ''",
            name="ck_scope_owner_mapping_schema_version_nonempty",
        ),
        sa.CheckConstraint(
            "source_payload_digest <> ''",
            name="ck_scope_owner_mapping_digest_nonempty",
        ),
    )
    op.create_index(
        "ix_scope_owner_mapping_checkpoint",
        "scope_owner_mappings",
        ["checkpoint"],
    )

    # 7. Quarantine evidence — redacted, no FK, raw payloads never land here.
    op.create_table(
        "scope_owner_quarantine",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("source_payload_digest", sa.String(128), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("resolved_tenant_id", sa.String(40), nullable=True),
        sa.Column("resolved_cabinet_id", sa.String(40), nullable=True),
        sa.Column("resolved_project_id", sa.String(40), nullable=True),
        sa.Column("checkpoint", sa.String(128), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_kind",
            "source_id",
            "schema_version",
            "source_payload_digest",
            name="uq_scope_owner_quarantine_observation",
        ),
        sa.CheckConstraint(
            "source_kind <> ''",
            name="ck_scope_owner_quarantine_source_kind_nonempty",
        ),
        sa.CheckConstraint(
            "source_id <> ''",
            name="ck_scope_owner_quarantine_source_id_nonempty",
        ),
        sa.CheckConstraint(
            "schema_version <> ''",
            name="ck_scope_owner_quarantine_schema_version_nonempty",
        ),
        sa.CheckConstraint(
            "source_payload_digest <> ''",
            name="ck_scope_owner_quarantine_digest_nonempty",
        ),
        sa.CheckConstraint(
            "reason_code <> ''",
            name="ck_scope_owner_quarantine_reason_nonempty",
        ),
    )
    op.create_index(
        "ix_scope_owner_quarantine_checkpoint",
        "scope_owner_quarantine",
        ["checkpoint"],
    )

    # 8. Audit evidence — append-shaped by contract; privileges are 2026110307.
    op.create_table(
        "scope_owner_audit",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("source_payload_digest", sa.String(128), nullable=False),
        sa.Column("event_kind", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("checkpoint", sa.String(128), nullable=True),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "source_kind <> ''",
            name="ck_scope_owner_audit_source_kind_nonempty",
        ),
        sa.CheckConstraint(
            "source_id <> ''",
            name="ck_scope_owner_audit_source_id_nonempty",
        ),
        sa.CheckConstraint(
            "schema_version <> ''",
            name="ck_scope_owner_audit_schema_version_nonempty",
        ),
        sa.CheckConstraint(
            "source_payload_digest <> ''",
            name="ck_scope_owner_audit_digest_nonempty",
        ),
        sa.CheckConstraint("event_kind <> ''", name="ck_scope_owner_audit_event_nonempty"),
        sa.CheckConstraint("reason_code <> ''", name="ck_scope_owner_audit_reason_nonempty"),
        sa.CheckConstraint(
            "correlation_id <> ''",
            name="ck_scope_owner_audit_correlation_nonempty",
        ),
    )
    op.create_index(
        "ix_scope_owner_audit_source",
        "scope_owner_audit",
        ["source_kind", "source_id"],
    )


def downgrade() -> None:
    # Strictly reverse: children before parents, indexes before their tables.
    op.drop_index("ix_scope_owner_audit_source", table_name="scope_owner_audit")
    op.drop_table("scope_owner_audit")

    op.drop_index("ix_scope_owner_quarantine_checkpoint", table_name="scope_owner_quarantine")
    op.drop_table("scope_owner_quarantine")

    op.drop_index("ix_scope_owner_mapping_checkpoint", table_name="scope_owner_mappings")
    op.drop_table("scope_owner_mappings")

    op.drop_index("ix_scope_registry_cabinet_project", table_name="scope_registry")
    op.drop_index("ix_scope_registry_tenant_state", table_name="scope_registry")
    op.drop_table("scope_registry")

    op.drop_index(
        "ix_scope_owner_project_anchor_tenant_cabinet",
        table_name="scope_owner_project_anchors",
    )
    op.drop_table("scope_owner_project_anchors")

    op.drop_index(
        "ix_scope_owner_cabinet_anchor_tenant_state",
        table_name="scope_owner_cabinet_anchors",
    )
    op.drop_table("scope_owner_cabinet_anchors")

    op.drop_index("ix_scope_owner_tenant_state", table_name="scope_owner_tenant_anchors")
    op.drop_table("scope_owner_tenant_anchors")

    # Last: no surviving FK depends on the retained-parent key any more.
    op.drop_constraint("uq_projects_scope_owner_parent_tuple", "projects", type_="unique")

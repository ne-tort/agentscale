"""Add bind_kind + child_may_edit; backfill global/local MP; drop project_module_bindings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026090711"
down_revision = "2026090710"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in (
        "module_company_grants",
        "module_cabinet_bindings",
        "module_project_bindings",
    ):
        op.add_column(
            table,
            sa.Column(
                "bind_kind",
                sa.String(length=16),
                nullable=False,
                server_default="local",
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "child_may_edit",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )

    conn = op.get_bind()

    # Platform-owned modules granted to companies → global, locked.
    conn.execute(
        sa.text(
            """
            UPDATE module_company_grants g
            SET bind_kind = 'global', child_may_edit = false
            FROM modules m
            WHERE g.module_id = m.id
              AND m.owner_scope = 'platform'
            """
        )
    )

    # Management modules on cabinets → global MC (share company/platform SoT).
    conn.execute(
        sa.text(
            """
            UPDATE module_cabinet_bindings
            SET bind_kind = 'global', child_may_edit = false
            WHERE module_id IN ('mod_prompts', 'mod_mcp', 'mod_files')
            """
        )
    )

    # Backfill MP from legacy PMB (enabled modules).
    if _has_table(conn, "project_module_bindings"):
        conn.execute(
            sa.text(
                """
                INSERT INTO module_project_bindings
                    (id, module_id, project_id, bind_kind, child_may_edit, created_at)
                SELECT
                    'mpb_' || substr(md5(random()::text || pmb.module_id || pmb.project_id), 1, 16),
                    pmb.module_id,
                    pmb.project_id,
                    CASE
                        WHEN pmb.module_id IN ('mod_prompts', 'mod_mcp', 'mod_files')
                            THEN 'global'
                        ELSE 'local'
                    END,
                    CASE
                        WHEN pmb.module_id IN ('mod_prompts', 'mod_mcp', 'mod_files')
                            THEN false
                        ELSE true
                    END,
                    now()
                FROM project_module_bindings pmb
                ON CONFLICT (module_id, project_id) DO NOTHING
                """
            )
        )

    # For every MC binding: ensure MP for all cabinet projects (management = global).
    # Only promote local→global for management modules; never force equipment overwrite.
    conn.execute(
        sa.text(
            """
            INSERT INTO module_project_bindings
                (id, module_id, project_id, bind_kind, child_may_edit, created_at)
            SELECT
                'mpb_' || substr(md5(random()::text || mcb.module_id || p.id), 1, 16),
                mcb.module_id,
                p.id,
                CASE
                    WHEN mcb.module_id IN ('mod_prompts', 'mod_mcp', 'mod_files')
                        THEN 'global'
                    ELSE 'local'
                END,
                CASE
                    WHEN mcb.module_id IN ('mod_prompts', 'mod_mcp', 'mod_files')
                        THEN false
                    ELSE true
                END,
                now()
            FROM module_cabinet_bindings mcb
            JOIN projects p ON p.cabinet_id = mcb.cabinet_id
            ON CONFLICT (module_id, project_id) DO UPDATE
            SET bind_kind = EXCLUDED.bind_kind,
                child_may_edit = EXCLUDED.child_may_edit
            WHERE module_project_bindings.bind_kind = 'local'
              AND EXCLUDED.bind_kind = 'global'
            """
        )
    )

    # Ensure management modules are global on existing MP rows (idempotent).
    conn.execute(
        sa.text(
            """
            UPDATE module_project_bindings
            SET bind_kind = 'global', child_may_edit = false
            WHERE module_id IN ('mod_prompts', 'mod_mcp', 'mod_files')
            """
        )
    )

    # Drop unused project leaf instances for globally bound management modules.
    conn.execute(
        sa.text(
            """
            DELETE FROM module_instances
            WHERE owner_kind = 'project'
              AND module_id IN ('mod_prompts', 'mod_mcp', 'mod_files')
            """
        )
    )
    # Drop unused cabinet leaf instances for globally bound management modules.
    conn.execute(
        sa.text(
            """
            DELETE FROM module_instances mi
            USING module_cabinet_bindings mcb
            WHERE mi.owner_kind = 'cabinet'
              AND mi.owner_id = mcb.cabinet_id
              AND mi.module_id = mcb.module_id
              AND mcb.bind_kind = 'global'
              AND mcb.module_id IN ('mod_prompts', 'mod_mcp', 'mod_files')
            """
        )
    )

    if _has_table(conn, "project_module_bindings"):
        op.drop_table("project_module_bindings")

    from prodavan.application.platform.product_module_upsert import upsert_product_modules

    upsert_product_modules(conn)


def downgrade() -> None:
    # Recreate PMB shell only — data loss acceptable for downgrade.
    op.create_table(
        "project_module_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=40),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "module_id",
            sa.String(length=40),
            sa.ForeignKey("modules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "module_id", name="uq_project_module_binding"),
    )
    for table in (
        "module_project_bindings",
        "module_cabinet_bindings",
        "module_company_grants",
    ):
        op.drop_column(table, "child_may_edit")
        op.drop_column(table, "bind_kind")


def _has_table(conn: sa.Connection, name: str) -> bool:
    row = conn.execute(
        sa.text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = :n
            """
        ),
        {"n": name},
    ).fetchone()
    return row is not None

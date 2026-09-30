"""Move templates module into mod_equipment; drop mod_templates; seed built-ins."""

import json

import sqlalchemy as sa

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026093008"
down_revision = "2026093007"
branch_labels = None
depends_on = None

_BUILTIN_SEEDS = [
    (
        "tpl_budget_builtin",
        "budget",
        "Бюджетирование (встроенный)",
        "builtin/kp-template.xlsx",
        "budget.xlsx",
    ),
    (
        "tpl_kp_builtin",
        "commercial_proposal",
        "Коммерческое предложение (встроенный)",
        "builtin/commercial-proposal-template.xlsx",
        "commercial-proposal.xlsx",
    ),
    (
        "tpl_spec_builtin",
        "specification",
        "Спецификация (встроенный)",
        "builtin/specification-template.xlsx",
        "specification.xlsx",
    ),
]


def _seed_row_body(ttype: str, title: str, key: str, fname: str) -> str:
    return json.dumps(
        {
            "template_type": ttype,
            "title": title,
            "file": {"storage_key": key, "filename": fname},
            "active": True,
        },
        ensure_ascii=False,
    )


def upgrade() -> None:
    conn = op.get_bind()

    # 1) migrate user-uploaded template rows from mod_templates instances to
    #    the same-owner mod_equipment instance (when it exists).
    eq_instances = {
        (r.owner_kind, r.owner_id): r.id
        for r in conn.execute(
            sa.text(
                "SELECT id, owner_kind, owner_id FROM module_instances WHERE module_id = 'mod_equipment'"
            )
        ).mappings()
    }
    tpl_instances = conn.execute(
        sa.text(
            "SELECT id, owner_kind, owner_id FROM module_instances WHERE module_id = 'mod_templates'"
        )
    ).mappings()
    for inst in tpl_instances:
        target = eq_instances.get((inst.owner_kind, inst.owner_id))
        if target is None:
            continue
        rows = conn.execute(
            sa.text(
                "SELECT row_id, body, session_id, created_by FROM module_instance_data_rows "
                "WHERE instance_id = :src AND table_slug = 'templates'"
            ),
            {"src": inst.id},
        ).mappings()
        for row in rows:
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM module_instance_data_rows "
                    "WHERE instance_id = :dst AND table_slug = 'templates' AND row_id = :rid"
                ),
                {"dst": target, "rid": row.row_id},
            ).scalar()
            if exists:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO module_instance_data_rows "
                    "(id, instance_id, table_slug, row_id, body, session_id, created_by, created_at, updated_at) "
                    "VALUES (gen_random_uuid()::text, :dst, 'templates', :rid, :body, :sess, "
                    "COALESCE(:cb, 'module_seed'), now(), now())"
                ),
                {
                    "dst": target,
                    "rid": row.row_id,
                    "body": row.body,
                    "sess": row.session_id,
                    "cb": row.created_by,
                },
            )

    # 2) drop the mod_templates module: instances, bindings, meta, catalog row.
    conn.execute(
        sa.text(
            "DELETE FROM module_instance_data_rows WHERE instance_id IN "
            "(SELECT id FROM module_instances WHERE module_id = 'mod_templates')"
        )
    )
    conn.execute(
        sa.text(
            "DELETE FROM module_instance_meta_documents WHERE instance_id IN "
            "(SELECT id FROM module_instances WHERE module_id = 'mod_templates')"
        )
    )
    conn.execute(sa.text("DELETE FROM module_instances WHERE module_id = 'mod_templates'"))
    conn.execute(sa.text("DELETE FROM module_meta_documents WHERE module_id = 'mod_templates'"))
    conn.execute(sa.text("DELETE FROM module_cabinet_bindings WHERE module_id = 'mod_templates'"))
    conn.execute(sa.text("DELETE FROM module_project_bindings WHERE module_id = 'mod_templates'"))
    conn.execute(sa.text("DELETE FROM module_company_grants WHERE module_id = 'mod_templates'"))
    conn.execute(sa.text("DELETE FROM modules WHERE id = 'mod_templates'"))

    # 3) refresh product meta (mod_equipment now owns templates).
    upsert_product_modules(conn)

    # 4) seed built-in template rows into every existing mod_equipment
    #    instance (module_seed provenance; meta docs were refreshed above).
    for row_id, ttype, title, key, fname in _BUILTIN_SEEDS:
        body = _seed_row_body(ttype, title, key, fname)
        conn.execute(
            sa.text(
                "INSERT INTO module_instance_data_rows "
                "(id, instance_id, table_slug, row_id, body, session_id, created_by, created_at, updated_at) "
                "SELECT gen_random_uuid()::text, i.id, 'templates', :rid, :body::jsonb, NULL, "
                "'module_seed', now(), now() "
                "FROM module_instances i "
                "WHERE i.module_id = 'mod_equipment' "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM module_instance_data_rows d "
                "  WHERE d.instance_id = i.id AND d.table_slug = 'templates' AND d.row_id = :rid)"
            ),
            {"rid": row_id, "body": body},
        )


def downgrade() -> None:
    pass

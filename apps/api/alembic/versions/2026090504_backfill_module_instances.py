"""Backfill module instances from template meta + cabinet module_data_rows."""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "2026090504"
down_revision = "2026090503"
branch_labels = None
depends_on = None


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _row_applies(body: object, project_id: str) -> bool:
    if not isinstance(body, dict):
        return True
    pids = body.get("project_ids")
    if pids is None or not isinstance(pids, list) or len(pids) == 0:
        return True
    return project_id in [str(p) for p in pids]


def _ensure_instance(
    conn: Connection,
    *,
    module_id: str,
    owner_kind: str,
    owner_id: str,
    parent_id: str | None,
) -> str:
    row = conn.execute(
        sa.text(
            """
            SELECT id FROM module_instances
            WHERE owner_kind = :ok AND owner_id = :oid AND module_id = :mid
            """
        ),
        {"ok": owner_kind, "oid": owner_id, "mid": module_id},
    ).fetchone()
    if row:
        return str(row[0])
    iid = _id("minst")
    conn.execute(
        sa.text(
            """
            INSERT INTO module_instances (id, module_id, owner_kind, owner_id, parent_instance_id)
            VALUES (:id, :mid, :ok, :oid, :pid)
            """
        ),
        {"id": iid, "mid": module_id, "ok": owner_kind, "oid": owner_id, "pid": parent_id},
    )
    return iid


def _copy_template_meta(conn: Connection, *, module_id: str, instance_id: str) -> None:
    docs = conn.execute(
        sa.text("SELECT slug, body FROM module_meta_documents WHERE module_id = :mid"),
        {"mid": module_id},
    ).fetchall()
    for slug, body in docs:
        conn.execute(
            sa.text(
                """
                INSERT INTO module_instance_meta_documents (id, instance_id, slug, body)
                VALUES (:id, :iid, :slug, CAST(:body AS jsonb))
                ON CONFLICT (instance_id, slug) DO NOTHING
                """
            ),
            {
                "id": _id("mimd"),
                "iid": instance_id,
                "slug": slug,
                "body": json.dumps(body if body is not None else {}),
            },
        )


def _copy_instance_meta(conn: Connection, *, src_id: str, dst_id: str) -> None:
    docs = conn.execute(
        sa.text("SELECT slug, body FROM module_instance_meta_documents WHERE instance_id = :iid"),
        {"iid": src_id},
    ).fetchall()
    for slug, body in docs:
        conn.execute(
            sa.text(
                """
                INSERT INTO module_instance_meta_documents (id, instance_id, slug, body)
                VALUES (:id, :iid, :slug, CAST(:body AS jsonb))
                ON CONFLICT (instance_id, slug) DO NOTHING
                """
            ),
            {
                "id": _id("mimd"),
                "iid": dst_id,
                "slug": slug,
                "body": json.dumps(body if body is not None else {}),
            },
        )


def _upsert_data_row(
    conn: Connection,
    *,
    instance_id: str,
    table_slug: str,
    row_id: str,
    body: object,
    created_by: str | None,
) -> None:
    if not isinstance(body, dict):
        body = {}
    conn.execute(
        sa.text(
            """
            INSERT INTO module_instance_data_rows
                (id, instance_id, table_slug, row_id, body, created_by)
            VALUES
                (:id, :iid, :ts, :rid, CAST(:body AS jsonb), :cb)
            ON CONFLICT (instance_id, table_slug, row_id) DO UPDATE
            SET body = EXCLUDED.body
            """
        ),
        {
            "id": _id("midr"),
            "iid": instance_id,
            "ts": table_slug,
            "rid": row_id,
            "body": json.dumps(body),
            "cb": created_by,
        },
    )


def _seed_from_template(conn: Connection, *, module_id: str, instance_id: str) -> None:
    row = conn.execute(
        sa.text(
            """
            SELECT body FROM module_meta_documents
            WHERE module_id = :mid AND slug = 'seed_rows'
            """
        ),
        {"mid": module_id},
    ).fetchone()
    if not row or not isinstance(row[0], dict):
        return
    items = row[0].get("items")
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        table_slug = item.get("table_slug")
        row_id = item.get("row_id")
        body = item.get("body", {})
        if not isinstance(table_slug, str) or not isinstance(row_id, str):
            continue
        if not isinstance(body, dict):
            body = {}
        _upsert_data_row(
            conn,
            instance_id=instance_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            created_by="module_seed",
        )


def _copy_instance_data(
    conn: Connection,
    *,
    src_id: str,
    dst_id: str,
    project_id_filter: str | None = None,
) -> None:
    rows = conn.execute(
        sa.text(
            """
            SELECT table_slug, row_id, body, created_by
            FROM module_instance_data_rows WHERE instance_id = :iid
            """
        ),
        {"iid": src_id},
    ).fetchall()
    for table_slug, row_id, body, created_by in rows:
        if not isinstance(body, dict):
            body = {}
        if project_id_filter and not _row_applies(body, project_id_filter):
            continue
        if project_id_filter and "project_ids" in body:
            body = {**body, "project_ids": []}
        _upsert_data_row(
            conn,
            instance_id=dst_id,
            table_slug=str(table_slug),
            row_id=str(row_id),
            body=body,
            created_by=created_by,
        )


def _import_cabinet_rows(
    conn: Connection,
    *,
    schema_name: str,
    module_id: str,
    instance_id: str,
) -> None:
    qschema = '"' + schema_name.replace('"', "") + '"'
    exists = conn.execute(
        sa.text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :n"),
        {"n": schema_name},
    ).fetchone()
    if not exists:
        return
    table_exists = conn.execute(
        sa.text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = :n AND table_name = 'module_data_rows'
            """
        ),
        {"n": schema_name},
    ).fetchone()
    if not table_exists:
        return
    rows = conn.execute(
        sa.text(
            f"""
            SELECT table_slug, row_id, body, created_by
            FROM {qschema}.module_data_rows
            WHERE module_id = :mid
            """
        ),
        {"mid": module_id},
    ).fetchall()
    for table_slug, row_id, body, created_by in rows:
        if not isinstance(body, dict):
            body = {}
        _upsert_data_row(
            conn,
            instance_id=instance_id,
            table_slug=str(table_slug),
            row_id=str(row_id),
            body=body,
            created_by=created_by,
        )


def upgrade() -> None:
    conn = op.get_bind()

    # 1) Platform instances for every module
    modules = conn.execute(sa.text("SELECT id FROM modules")).fetchall()
    platform_ids: dict[str, str] = {}
    for (module_id,) in modules:
        iid = _ensure_instance(
            conn,
            module_id=module_id,
            owner_kind="platform",
            owner_id="platform",
            parent_id=None,
        )
        platform_ids[module_id] = iid
        _copy_template_meta(conn, module_id=module_id, instance_id=iid)
        _seed_from_template(conn, module_id=module_id, instance_id=iid)

    # 2) Company instances from grants
    grants = conn.execute(
        sa.text("SELECT module_id, company_id FROM module_company_grants WHERE status = 'active'")
    ).fetchall()
    company_ids: dict[tuple[str, str], str] = {}
    for module_id, company_id in grants:
        parent = platform_ids.get(module_id)
        if parent is None:
            parent = _ensure_instance(
                conn,
                module_id=module_id,
                owner_kind="platform",
                owner_id="platform",
                parent_id=None,
            )
            platform_ids[module_id] = parent
            _copy_template_meta(conn, module_id=module_id, instance_id=parent)
        iid = _ensure_instance(
            conn,
            module_id=module_id,
            owner_kind="company",
            owner_id=company_id,
            parent_id=parent,
        )
        company_ids[(module_id, company_id)] = iid
        _copy_instance_meta(conn, src_id=parent, dst_id=iid)
        _copy_instance_data(conn, src_id=parent, dst_id=iid)

    # 3) Cabinet instances from MC bindings + legacy rows
    bindings = conn.execute(
        sa.text(
            """
            SELECT mcb.module_id, mcb.cabinet_id, ci.schema_name,
                   COALESCE(ci.owner_company_id, ci.company_id) AS company_id
            FROM module_cabinet_bindings mcb
            JOIN cabinet_instances ci ON ci.id = mcb.cabinet_id
            """
        )
    ).fetchall()
    cabinet_ids: dict[tuple[str, str], str] = {}
    for module_id, cabinet_id, schema_name, company_id in bindings:
        parent = None
        if company_id:
            parent = company_ids.get((module_id, company_id))
            if parent is None:
                plat = platform_ids.get(module_id) or _ensure_instance(
                    conn,
                    module_id=module_id,
                    owner_kind="platform",
                    owner_id="platform",
                    parent_id=None,
                )
                parent = _ensure_instance(
                    conn,
                    module_id=module_id,
                    owner_kind="company",
                    owner_id=company_id,
                    parent_id=plat,
                )
                company_ids[(module_id, company_id)] = parent
                _copy_instance_meta(conn, src_id=plat, dst_id=parent)
                _copy_instance_data(conn, src_id=plat, dst_id=parent)
        else:
            parent = platform_ids.get(module_id)
        if parent is None:
            continue
        iid = _ensure_instance(
            conn,
            module_id=module_id,
            owner_kind="cabinet",
            owner_id=cabinet_id,
            parent_id=parent,
        )
        cabinet_ids[(module_id, cabinet_id)] = iid
        _copy_instance_meta(conn, src_id=parent, dst_id=iid)
        _copy_instance_data(conn, src_id=parent, dst_id=iid)
        _import_cabinet_rows(
            conn, schema_name=schema_name, module_id=module_id, instance_id=iid
        )

    # 4) Project instances forked from cabinet (filter by project_ids)
    projects = conn.execute(
        sa.text(
            """
            SELECT id, cabinet_id FROM projects
            WHERE COALESCE(status, '') NOT IN ('deleted', 'soft_deleted', 'purged')
            """
        )
    ).fetchall()
    for project_id, cabinet_id in projects:
        for (module_id, cab_id), cab_inst in list(cabinet_ids.items()):
            if cab_id != cabinet_id:
                continue
            iid = _ensure_instance(
                conn,
                module_id=module_id,
                owner_kind="project",
                owner_id=project_id,
                parent_id=cab_inst,
            )
            _copy_instance_meta(conn, src_id=cab_inst, dst_id=iid)
            _copy_instance_data(
                conn, src_id=cab_inst, dst_id=iid, project_id_filter=project_id
            )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM module_instance_data_rows"))
    conn.execute(sa.text("DELETE FROM module_instance_meta_documents"))
    conn.execute(sa.text("DELETE FROM module_instances"))

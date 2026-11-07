"""One-time, idempotent fold of mod_prompts/mod_mcp/mod_files into «Управление».

«Управление» replaced three independent product modules. A deployment that ran
with the old catalog still holds their grants, bindings, per-owner instances and
data rows; this module moves that material into the merged module and archives
the old catalog rows. It never deletes anything, so the recycle bin and the
audit trail keep working, and re-running it is a no-op.

The work is split in three:

* :func:`load_state` — reads the catalog pieces the merge needs (instances,
  bindings, grants, data rows, instance meta) into plain dicts;
* :func:`plan_merge` — a **pure** function over that state that decides which
  instances to create, which rows/bindings to copy and which modules to archive;
* :func:`apply_plan` — executes the plan with ``ON CONFLICT DO NOTHING``.

Splitting it this way is what makes the migration testable without Postgres:
every decision (including idempotency, see the ``plan`` tests) is made from
data, not from the dialect.

Two rules shape the plan:

1. **One instance per owner scope, holding the union of the three areas.** A
   scope that had a local instance for *any* of the three modules gets a
   ``mod_management`` instance; the rows are read from the instance each area
   actually resolved to for that scope (its own copy when it had one, its
   inherited parent otherwise). Copying only the local copies would leave, say,
   MCP rows invisible on a cabinet that kept a local prompts copy.
2. **The merged meta wins.** Only instance meta slugs the destination does not
   have yet are copied — copying a single area's ``tables``/``views`` document
   would drop the other two areas declared by the merged seed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from prodavan.application.platform.product_module_seeds import (
    LEGACY_MERGED_MODULE_IDS,
    MANAGEMENT_MODULE_ID,
    PRODUCT_MODULE_COMPANY_GRANT_SCOPE,
)

OWNER_PLATFORM = "platform"
OWNER_COMPANY = "company"
OWNER_CABINET = "cabinet"
OWNER_PROJECT = "project"
PLATFORM_OWNER_ID = "platform"

#: Owner scopes in cascade order — the parent of a scope is always earlier.
_OWNER_ORDER: dict[str, int] = {
    OWNER_PLATFORM: 0,
    OWNER_COMPANY: 1,
    OWNER_CABINET: 2,
    OWNER_PROJECT: 3,
}

#: Instance-meta slug that lists workspace roots a sync may prune. The merged
#: module declares its own union, so the per-area documents are merged into it
#: rather than dropped.
_MATERIALIZE_ROOTS_SLUG = "materialize_roots"

# Every list parameter uses an ``expanding`` bind parameter rather than
# ``= ANY(:list)``: Alembic runs through asyncpg, which needs an explicit array
# type for the latter and would make this revision dialect-dependent.
_SQL_LEGACY_INSTANCES = """
SELECT id, module_id, owner_kind, owner_id, parent_instance_id
FROM module_instances
WHERE module_id IN :module_ids
"""

_SQL_CABINET_BINDINGS = """
SELECT module_id, cabinet_id, bind_kind, child_may_edit
FROM module_cabinet_bindings
WHERE module_id IN :module_ids
"""

_SQL_PROJECT_BINDINGS = """
SELECT module_id, project_id, bind_kind, child_may_edit
FROM module_project_bindings
WHERE module_id IN :module_ids
"""

_SQL_COMPANY_GRANTS = """
SELECT module_id, company_id, bind_kind, child_may_edit, status
FROM module_company_grants
WHERE module_id IN :module_ids
"""

_SQL_DATA_ROWS = """
SELECT instance_id, table_slug, row_id, body, created_by, session_id
FROM module_instance_data_rows
WHERE instance_id IN :instance_ids
ORDER BY instance_id, table_slug, row_id
"""

_SQL_INSTANCE_META = """
SELECT instance_id, slug, body
FROM module_instance_meta_documents
WHERE instance_id IN :instance_ids
"""

_SQL_MODULES = "SELECT id, status, company_grant_scope FROM modules WHERE id IN :module_ids"

_SQL_CABINET_COMPANY = """
SELECT id, COALESCE(owner_company_id, company_id) AS company_id
FROM cabinet_instances
WHERE id IN :cabinet_ids
"""

_SQL_PROJECT_CABINET = "SELECT id, cabinet_id FROM projects WHERE id IN :project_ids"

_SQL_INSERT_INSTANCE = """
INSERT INTO module_instances (id, module_id, owner_kind, owner_id, parent_instance_id)
VALUES (:id, :module_id, :owner_kind, :owner_id, :parent_instance_id)
ON CONFLICT (owner_kind, owner_id, module_id) DO NOTHING
"""

_SQL_COPY_TEMPLATE_META = """
INSERT INTO module_instance_meta_documents (id, instance_id, slug, body)
SELECT 'mimd_' || substr(md5(random()::text || i.id || d.slug), 1, 16), i.id, d.slug, d.body
FROM module_instances i
JOIN module_meta_documents d ON d.module_id = i.module_id
WHERE i.module_id = :module_id
ON CONFLICT (instance_id, slug) DO NOTHING
"""

_SQL_INSERT_DATA_ROW = """
INSERT INTO module_instance_data_rows
    (id, instance_id, table_slug, row_id, body, session_id, created_by)
VALUES
    (:id, :instance_id, :table_slug, :row_id, CAST(:body AS jsonb), :session_id, :created_by)
ON CONFLICT (instance_id, table_slug, row_id) DO NOTHING
"""

#: Template seed rows into one instance. A from-base install has no legacy rows
#: to copy (its own module instances were seeded back in 2026090504) so the new
#: merged instances have to be seeded from the merged `seed_rows` document —
#: otherwise «Управление» opens empty on a fresh database.
_SQL_SEED_ROWS_FROM_TEMPLATE = """
INSERT INTO module_instance_data_rows
    (id, instance_id, table_slug, row_id, body, created_by)
SELECT
    -- Every operand is parenthesised on purpose: `->>` binds looser than `||`,
    -- so `s.ord || s.item->>'table_slug'` parses as `(s.ord || s.item) ->> '…'`
    -- and Postgres answers "operator does not exist: text ->> unknown". An empty
    -- database never evaluates this list, which is why it only failed on a
    -- database that already had the merged seed document.
    'midr_' || substr(
        md5(
            random()::text
            || (:instance_id)::text
            || (s.ord)::text
            || (s.item->>'table_slug')
            || (s.item->>'row_id')
        ),
        1,
        16
    ),
    :instance_id,
    s.item->>'table_slug',
    s.item->>'row_id',
    -- A seed body is an object; anything else (missing or JSON null) seeds {}.
    CASE
        WHEN jsonb_typeof(s.item->'body') = 'object' THEN s.item->'body'
        ELSE '{}'::jsonb
    END,
    'module_seed'
FROM (
    SELECT ordinality AS ord, item
    FROM module_meta_documents d,
         LATERAL jsonb_array_elements(COALESCE(d.body->'items', '[]'::jsonb)) WITH ORDINALITY AS t(item, ordinality)
    WHERE d.module_id = :module_id AND d.slug = 'seed_rows'
) AS s
WHERE s.item->>'table_slug' IS NOT NULL AND s.item->>'row_id' IS NOT NULL
ON CONFLICT (instance_id, table_slug, row_id) DO NOTHING
"""

_SQL_INSERT_INSTANCE_META = """
INSERT INTO module_instance_meta_documents (id, instance_id, slug, body)
VALUES (:id, :instance_id, :slug, CAST(:body AS jsonb))
ON CONFLICT (instance_id, slug) DO NOTHING
"""

_SQL_INSERT_CABINET_BINDING = """
INSERT INTO module_cabinet_bindings (id, module_id, cabinet_id, bind_kind, child_may_edit)
VALUES (:id, :module_id, :cabinet_id, :bind_kind, :child_may_edit)
ON CONFLICT (module_id, cabinet_id) DO NOTHING
"""

_SQL_INSERT_PROJECT_BINDING = """
INSERT INTO module_project_bindings (id, module_id, project_id, bind_kind, child_may_edit)
VALUES (:id, :module_id, :project_id, :bind_kind, :child_may_edit)
ON CONFLICT (module_id, project_id) DO NOTHING
"""

_SQL_INSERT_COMPANY_GRANT = """
INSERT INTO module_company_grants
    (id, module_id, company_id, status, bind_kind, child_may_edit)
VALUES (:id, :module_id, :company_id, :status, :bind_kind, :child_may_edit)
ON CONFLICT (module_id, company_id) DO NOTHING
"""

_SQL_ARCHIVE_MODULE = """
UPDATE modules SET status = 'archived' WHERE id = :id AND status <> 'archived'
"""

#: Give the merged module the reach its predecessors had. This is the one moment
#: it can be applied: the row already exists (created by revision 2026082711,
#: which predates the column, or by the upsert whose ON CONFLICT branch leaves an
#: existing scope alone), so neither of those paths sets it.
#:
#: Guarded on the schema default, which is "nobody has decided yet". An
#: administrator's choice is never the default value, so a re-run — or a database
#: where someone has already picked a scope for «Управление» — is left alone.
_SQL_INHERIT_GRANT_SCOPE = """
UPDATE modules SET company_grant_scope = :scope
WHERE id = :module_id
  AND company_grant_scope = 'selected'
"""

_SQL_DELETE_CABINET_BINDING = """
DELETE FROM module_cabinet_bindings WHERE module_id = :module_id AND cabinet_id = :owner_id
"""

_SQL_DELETE_PROJECT_BINDING = """
DELETE FROM module_project_bindings WHERE module_id = :module_id AND project_id = :owner_id
"""

_SQL_DELETE_COMPANY_GRANT = """
DELETE FROM module_company_grants WHERE module_id = :module_id AND company_id = :owner_id
"""


@dataclass(frozen=True)
class Instance:
    id: str
    module_id: str
    owner_kind: str
    owner_id: str
    parent_instance_id: str | None = None


@dataclass(frozen=True)
class DataRow:
    instance_id: str
    table_slug: str
    row_id: str
    body: dict[str, Any] = field(default_factory=dict)
    created_by: str | None = None
    session_id: str | None = None


@dataclass
class MergeState:
    """Everything the merge needs, loaded from the catalog."""

    modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    instances: list[Instance] = field(default_factory=list)
    cabinet_bindings: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    project_bindings: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    company_grants: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    data_rows: dict[str, list[DataRow]] = field(default_factory=dict)
    instance_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    cabinet_company: dict[str, str | None] = field(default_factory=dict)
    project_cabinet: dict[str, str | None] = field(default_factory=dict)

    def legacy_module_ids(self) -> list[str]:
        return [mid for mid in LEGACY_MERGED_MODULE_IDS if mid in self.modules]


@dataclass
class NewInstance:
    id: str
    module_id: str
    owner_kind: str
    owner_id: str
    parent_instance_id: str | None


@dataclass
class MergePlan:
    create_instances: list[NewInstance] = field(default_factory=list)
    copy_rows: list[DataRow] = field(default_factory=list)
    copy_instance_meta: list[dict[str, Any]] = field(default_factory=list)
    copy_cabinet_bindings: list[dict[str, Any]] = field(default_factory=list)
    copy_project_bindings: list[dict[str, Any]] = field(default_factory=list)
    copy_company_grants: list[dict[str, Any]] = field(default_factory=list)
    #: Legacy bindings/grants to revoke **after** they were copied to the merged
    #: module. Revoking is not cosmetic: an archived module that kept its
    #: cabinet binding would still be materialized into the same workspace
    #: paths and would overwrite «Управление»'s freshly written prompts on
    #: every sync. Moving the binding is what makes the merge one module.
    revoke_cabinet_bindings: list[dict[str, str]] = field(default_factory=list)
    revoke_project_bindings: list[dict[str, str]] = field(default_factory=list)
    revoke_company_grants: list[dict[str, str]] = field(default_factory=list)
    archive_modules: list[str] = field(default_factory=list)
    #: The scope the product seed declares for the merged module; `None` to leave
    #: the stored value untouched.
    inherit_grant_scope: str | None = None

    @property
    def creates_instances(self) -> bool:
        return bool(self.create_instances)

    def counts(self) -> dict[str, int]:
        return {
            "instances": len(self.create_instances),
            "rows": len(self.copy_rows),
            "instance_meta": len(self.copy_instance_meta),
            "cabinet_bindings": len(self.copy_cabinet_bindings),
            "project_bindings": len(self.copy_project_bindings),
            "company_grants": len(self.copy_company_grants),
            "revoked_bindings": (
                len(self.revoke_cabinet_bindings)
                + len(self.revoke_project_bindings)
                + len(self.revoke_company_grants)
            ),
            "archived_modules": len(self.archive_modules),
        }


# --- state loading ----------------------------------------------------------


def _fetchall(
    conn: sa.Connection,
    sql: str,
    params: dict[str, Any],
    *,
    expanding: tuple[str, ...] = (),
) -> list[tuple]:
    """Run a read query, expanding list parameters (see the SQL constants)."""
    stmt = sa.text(sql)
    values = dict(params)
    for name in expanding:
        values[name] = list(values.get(name) or [])
        stmt = stmt.bindparams(sa.bindparam(name, expanding=True))
    result = conn.execute(stmt, values)
    return list(result.fetchall())


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


def load_state(conn: sa.Connection) -> MergeState:
    module_ids = [*LEGACY_MERGED_MODULE_IDS, MANAGEMENT_MODULE_ID]
    state = MergeState()

    for module_id, status, scope in _fetchall(
        conn, _SQL_MODULES, {"module_ids": module_ids}, expanding=("module_ids",)
    ):
        state.modules[str(module_id)] = {
            "status": str(status or ""),
            "company_grant_scope": str(scope or "selected"),
        }

    state.instances = [
        Instance(
            id=str(instance_id),
            module_id=str(module_id),
            owner_kind=str(owner_kind),
            owner_id=str(owner_id),
            parent_instance_id=str(parent) if parent else None,
        )
        for instance_id, module_id, owner_kind, owner_id, parent in _fetchall(
            conn, _SQL_LEGACY_INSTANCES, {"module_ids": module_ids}, expanding=("module_ids",)
        )
    ]

    for module_id, cabinet_id, bind_kind, child_may_edit in _fetchall(
        conn, _SQL_CABINET_BINDINGS, {"module_ids": module_ids}, expanding=("module_ids",)
    ):
        state.cabinet_bindings[(str(module_id), str(cabinet_id))] = {
            "bind_kind": str(bind_kind or "local"),
            "child_may_edit": bool(child_may_edit),
        }

    for module_id, project_id, bind_kind, child_may_edit in _fetchall(
        conn, _SQL_PROJECT_BINDINGS, {"module_ids": module_ids}, expanding=("module_ids",)
    ):
        state.project_bindings[(str(module_id), str(project_id))] = {
            "bind_kind": str(bind_kind or "local"),
            "child_may_edit": bool(child_may_edit),
        }

    for module_id, company_id, bind_kind, child_may_edit, status in _fetchall(
        conn, _SQL_COMPANY_GRANTS, {"module_ids": module_ids}, expanding=("module_ids",)
    ):
        state.company_grants[(str(module_id), str(company_id))] = {
            "bind_kind": str(bind_kind or "local"),
            "child_may_edit": bool(child_may_edit),
            "status": str(status or "active"),
        }

    instance_ids = [inst.id for inst in state.instances]
    if instance_ids:
        for instance_id, table_slug, row_id, body, created_by, session_id in _fetchall(
            conn, _SQL_DATA_ROWS, {"instance_ids": instance_ids}, expanding=("instance_ids",)
        ):
            state.data_rows.setdefault(str(instance_id), []).append(
                DataRow(
                    instance_id=str(instance_id),
                    table_slug=str(table_slug),
                    row_id=str(row_id),
                    body=body if isinstance(body, dict) else {},
                    created_by=created_by,
                    session_id=session_id,
                )
            )

        for instance_id, slug, body in _fetchall(
            conn, _SQL_INSTANCE_META, {"instance_ids": instance_ids}, expanding=("instance_ids",)
        ):
            state.instance_meta.setdefault(str(instance_id), {})[str(slug)] = body

    # Only the scopes the plan may need to walk through: resolution goes
    # cabinet → company and project → cabinet, never the other way.
    cabinet_ids: set[str] = {row for _, row in state.cabinet_bindings}
    cabinet_ids.update(
        inst.owner_id for inst in state.instances if inst.owner_kind == OWNER_CABINET
    )
    project_ids: set[str] = {row for _, row in state.project_bindings}

    if cabinet_ids:
        for cabinet_id, company_id in _fetchall(
            conn,
            _SQL_CABINET_COMPANY,
            {"cabinet_ids": sorted(cabinet_ids)},
            expanding=("cabinet_ids",),
        ):
            state.cabinet_company[str(cabinet_id)] = str(company_id) if company_id else None

    if project_ids:
        for project_id, cabinet_id in _fetchall(
            conn,
            _SQL_PROJECT_CABINET,
            {"project_ids": sorted(project_ids)},
            expanding=("project_ids",),
        ):
            state.project_cabinet[str(project_id)] = str(cabinet_id) if cabinet_id else None

    return state


# --- planning (pure) --------------------------------------------------------


def _local_instance_id(state: MergeState, *, module_id: str, owner_kind: str, owner_id: str) -> str | None:
    for inst in state.instances:
        if (
            inst.module_id == module_id
            and inst.owner_kind == owner_kind
            and inst.owner_id == owner_id
        ):
            return inst.id
    return None


def _platform_instance_id(state: MergeState, *, module_id: str) -> str | None:
    return _local_instance_id(
        state, module_id=module_id, owner_kind=OWNER_PLATFORM, owner_id=PLATFORM_OWNER_ID
    )


def effective_instance_id(
    state: MergeState, *, module_id: str, owner_kind: str, owner_id: str
) -> str | None:
    """Instance this owner actually reads for ``module_id``.

    Mirrors :meth:`ModuleInstanceService.resolve_sot_instance`: an owner-local
    instance wins; otherwise a global bind walks up one scope; a company grant
    falls through to the platform instance when the grant is global or when the
    module is granted to all companies; anything else resolves to nothing.
    """
    local = _local_instance_id(
        state, module_id=module_id, owner_kind=owner_kind, owner_id=owner_id
    )
    if local is not None:
        return local

    if owner_kind == OWNER_PROJECT:
        binding = state.project_bindings.get((module_id, owner_id))
        if binding is None or binding["bind_kind"] != "global":
            return None
        cabinet_id = state.project_cabinet.get(owner_id)
        if not cabinet_id:
            return None
        return effective_instance_id(
            state, module_id=module_id, owner_kind=OWNER_CABINET, owner_id=cabinet_id
        )

    if owner_kind == OWNER_CABINET:
        binding = state.cabinet_bindings.get((module_id, owner_id))
        if binding is None or binding["bind_kind"] != "global":
            return None
        company_id = state.cabinet_company.get(owner_id)
        if company_id:
            return effective_instance_id(
                state, module_id=module_id, owner_kind=OWNER_COMPANY, owner_id=company_id
            )
        return _platform_instance_id(state, module_id=module_id)

    if owner_kind == OWNER_COMPANY:
        grant = state.company_grants.get((module_id, owner_id))
        if grant is not None:
            if grant["bind_kind"] != "global" or grant["status"] != "active":
                return None
            return _platform_instance_id(state, module_id=module_id)
        module = state.modules.get(module_id) or {}
        if module.get("company_grant_scope") == "all":
            return _platform_instance_id(state, module_id=module_id)
        return None

    return _platform_instance_id(state, module_id=module_id)


def _dest_parent_id(
    state: MergeState,
    *,
    owner_kind: str,
    owner_id: str,
    planned: dict[tuple[str, str], str],
) -> str | None:
    """Parent instance for a planned «Управление» instance, or None."""
    if owner_kind == OWNER_PLATFORM:
        return None
    if owner_kind == OWNER_COMPANY:
        return planned.get((OWNER_PLATFORM, PLATFORM_OWNER_ID)) or _local_instance_id(
            state, module_id=MANAGEMENT_MODULE_ID, owner_kind=OWNER_PLATFORM, owner_id=PLATFORM_OWNER_ID
        )
    if owner_kind == OWNER_CABINET:
        company_id = state.cabinet_company.get(owner_id)
        if company_id:
            return planned.get((OWNER_COMPANY, company_id)) or _local_instance_id(
                state, module_id=MANAGEMENT_MODULE_ID, owner_kind=OWNER_COMPANY, owner_id=company_id
            )
        return planned.get((OWNER_PLATFORM, PLATFORM_OWNER_ID)) or _local_instance_id(
            state, module_id=MANAGEMENT_MODULE_ID, owner_kind=OWNER_PLATFORM, owner_id=PLATFORM_OWNER_ID
        )
    cabinet_id = state.project_cabinet.get(owner_id)
    if cabinet_id:
        return planned.get((OWNER_CABINET, cabinet_id)) or _local_instance_id(
            state, module_id=MANAGEMENT_MODULE_ID, owner_kind=OWNER_CABINET, owner_id=cabinet_id
        )
    return planned.get((OWNER_PLATFORM, PLATFORM_OWNER_ID)) or _local_instance_id(
        state, module_id=MANAGEMENT_MODULE_ID, owner_kind=OWNER_PLATFORM, owner_id=PLATFORM_OWNER_ID
    )


def _new_instance_id() -> str:
    return f"minst_{uuid.uuid4().hex[:16]}"


def plan_merge(state: MergeState) -> MergePlan:
    """Decide the whole merge from ``state`` — no queries, no side effects."""
    plan = MergePlan()
    legacy_ids = state.legacy_module_ids()
    if not legacy_ids:
        return plan

    existing_dest = {
        (inst.owner_kind, inst.owner_id): inst.id
        for inst in state.instances
        if inst.module_id == MANAGEMENT_MODULE_ID
    }
    planned: dict[tuple[str, str], str] = dict(existing_dest)

    # Owner scopes that need a merged instance: the platform scope always (it is
    # the root every global bind walks to) plus every scope that held an
    # instance of any of the three modules.
    owners: set[tuple[str, str]] = {(OWNER_PLATFORM, PLATFORM_OWNER_ID)}
    for inst in state.instances:
        if inst.module_id in legacy_ids:
            owners.add((inst.owner_kind, inst.owner_id))

    ordered = sorted(
        owners,
        key=lambda item: (_OWNER_ORDER.get(item[0], 99), item[1]),
    )

    for owner_kind, owner_id in ordered:
        if (owner_kind, owner_id) in existing_dest:
            continue
        instance_id = _new_instance_id()
        plan.create_instances.append(
            NewInstance(
                id=instance_id,
                module_id=MANAGEMENT_MODULE_ID,
                owner_kind=owner_kind,
                owner_id=owner_id,
                parent_instance_id=_dest_parent_id(
                    state, owner_kind=owner_kind, owner_id=owner_id, planned=planned
                ),
            )
        )
        planned[(owner_kind, owner_id)] = instance_id

    # Rows: union of the three areas, each read from the instance that owner
    # actually resolved to. Existing destination rows are never overwritten
    # (idempotency: a second run plans nothing).
    for owner_kind, owner_id in ordered:
        dest_id = planned[(owner_kind, owner_id)]
        seen: set[tuple[str, str]] = {
            (row.table_slug, row.row_id) for row in state.data_rows.get(dest_id, [])
        }
        for module_id in legacy_ids:
            source_id = effective_instance_id(
                state, module_id=module_id, owner_kind=owner_kind, owner_id=owner_id
            )
            if source_id is None or source_id == dest_id:
                continue
            for row in state.data_rows.get(source_id, []):
                key = (row.table_slug, row.row_id)
                if key in seen:
                    continue
                seen.add(key)
                plan.copy_rows.append(
                    DataRow(
                        instance_id=dest_id,
                        table_slug=row.table_slug,
                        row_id=row.row_id,
                        body=dict(row.body),
                        created_by=row.created_by,
                        session_id=row.session_id,
                    )
                )

        # Instance meta: only slugs the destination cannot already have from
        # the merged seed (the template copy). `materialize_roots` is special:
        # the merged seed declares the union itself, so a destination that
        # already carries it needs nothing copied.
        dest_meta = state.instance_meta.get(dest_id, {})
        merged_roots = _merged_workspace_roots(state, owner_kind=owner_kind, owner_id=owner_id, legacy_ids=legacy_ids)
        if merged_roots and _MATERIALIZE_ROOTS_SLUG not in dest_meta:
            plan.copy_instance_meta.append(
                {
                    "id": f"_meta_{dest_id}",
                    "instance_id": dest_id,
                    "slug": _MATERIALIZE_ROOTS_SLUG,
                    "body": {"workspace_roots": merged_roots},
                    "merge_roots": True,
                }
            )
        for module_id in legacy_ids:
            source_id = effective_instance_id(
                state, module_id=module_id, owner_kind=owner_kind, owner_id=owner_id
            )
            if source_id is None or source_id == dest_id:
                continue
            for slug, body in (state.instance_meta.get(source_id) or {}).items():
                if slug == _MATERIALIZE_ROOTS_SLUG:
                    continue
                if slug in dest_meta:
                    continue  # merged seed doc wins
                plan.copy_instance_meta.append(
                    {
                        "id": f"_meta_{dest_id}_{slug}",
                        "instance_id": dest_id,
                        "slug": slug,
                        "body": body,
                        "merge_roots": False,
                    }
                )

    # Bindings: move every legacy cabinet/project binding and company grant onto
    # «Управление». Two legacy modules can be bound to the same owner with
    # different flags, so the merged binding takes the most permissive reading —
    # a global bind wins over a local one (sharing the parent SoT) and
    # ``child_may_edit`` is the OR — otherwise folding A+B into one module could
    # silently turn an editable area read-only. A binding «Управление» already
    # has is kept exactly as stored: an admin may have edited it after an
    # earlier partial run.
    grouped_cabinets = _group_bindings(
        state.cabinet_bindings, legacy_ids=legacy_ids, merged_id=MANAGEMENT_MODULE_ID,
    )
    for cabinet_id, binding in sorted(grouped_cabinets.items()):
        if binding is not None:
            plan.copy_cabinet_bindings.append(
                {
                    "id": f"_mcb_{cabinet_id}",
                    "module_id": MANAGEMENT_MODULE_ID,
                    "cabinet_id": cabinet_id,
                    **binding,
                }
            )
        for module_id in legacy_ids:
            if (module_id, cabinet_id) in state.cabinet_bindings:
                plan.revoke_cabinet_bindings.append(
                    {"module_id": module_id, "owner_id": cabinet_id}
                )

    grouped_projects = _group_bindings(
        state.project_bindings, legacy_ids=legacy_ids, merged_id=MANAGEMENT_MODULE_ID,
    )
    for project_id, binding in sorted(grouped_projects.items()):
        if binding is not None:
            plan.copy_project_bindings.append(
                {
                    "id": f"_mpb_{project_id}",
                    "module_id": MANAGEMENT_MODULE_ID,
                    "project_id": project_id,
                    **binding,
                }
            )
        for module_id in legacy_ids:
            if (module_id, project_id) in state.project_bindings:
                plan.revoke_project_bindings.append(
                    {"module_id": module_id, "owner_id": project_id}
                )

    grouped_companies = _group_bindings(
        state.company_grants, legacy_ids=legacy_ids, merged_id=MANAGEMENT_MODULE_ID,
    )
    for company_id, grant in sorted(grouped_companies.items()):
        if grant is not None:
            plan.copy_company_grants.append(
                {
                    "id": f"_mcg_{company_id}",
                    "module_id": MANAGEMENT_MODULE_ID,
                    "company_id": company_id,
                    **grant,
                }
            )
        for module_id in legacy_ids:
            if (module_id, company_id) in state.company_grants:
                plan.revoke_company_grants.append(
                    {"module_id": module_id, "owner_id": company_id}
                )

    for module_id in legacy_ids:
        if (state.modules.get(module_id) or {}).get("status") != "archived":
            plan.archive_modules.append(module_id)

    # Apply the reach the seed declares for the merged module, while its scope is
    # still the schema default (see _SQL_INHERIT_GRANT_SCOPE).
    plan.inherit_grant_scope = declared_grant_scope(MANAGEMENT_MODULE_ID)

    return plan


def declared_grant_scope(module_id: str) -> str | None:
    """The scope the product seed declares for a module, or `None` if it declares none."""
    return PRODUCT_MODULE_COMPANY_GRANT_SCOPE.get(module_id)


def _group_bindings(
    bindings: dict[tuple[str, str], dict[str, Any]],
    *,
    legacy_ids: list[str],
    merged_id: str,
) -> dict[str, dict[str, Any] | None]:
    """Union legacy bindings per owner: ``{owner_id: binding or None}``.

    ``None`` means "revoke only" — the merged module already holds a binding for
    that owner and it must not be overwritten by a stale legacy copy.
    """
    per_owner: dict[str, dict[str, Any]] = {}
    for (module_id, owner_id), binding in bindings.items():
        if module_id not in legacy_ids:
            continue
        current = per_owner.get(owner_id)
        if current is None:
            per_owner[owner_id] = {
                "bind_kind": binding.get("bind_kind", "local"),
                "child_may_edit": bool(binding.get("child_may_edit")),
            }
            if "status" in binding:
                per_owner[owner_id]["status"] = binding["status"]
            continue
        if binding.get("bind_kind") == "global":
            current["bind_kind"] = "global"
        current["child_may_edit"] = bool(current["child_may_edit"]) or bool(
            binding.get("child_may_edit")
        )
        if "status" in binding and binding.get("status") == "active":
            current["status"] = "active"

    out: dict[str, dict[str, Any] | None] = {}
    for owner_id, binding in per_owner.items():
        if (merged_id, owner_id) in bindings:
            out[owner_id] = None  # already bound: keep the stored row
            continue
        out[owner_id] = dict(binding)
    return out


def _merged_workspace_roots(
    state: MergeState,
    *,
    owner_kind: str,
    owner_id: str,
    legacy_ids: list[str],
) -> list[str]:
    roots: list[str] = []
    for module_id in legacy_ids:
        source_id = effective_instance_id(
            state, module_id=module_id, owner_kind=owner_kind, owner_id=owner_id
        )
        if source_id is None:
            continue
        doc = (state.instance_meta.get(source_id) or {}).get(_MATERIALIZE_ROOTS_SLUG)
        body = doc.get("workspace_roots") if isinstance(doc, dict) else None
        if not isinstance(body, list):
            continue
        for root in body:
            if isinstance(root, str) and root not in roots:
                roots.append(root)
    return roots


# --- apply ------------------------------------------------------------------


def apply_plan(conn: sa.Connection, plan: MergePlan) -> None:
    for instance in plan.create_instances:
        conn.execute(
            sa.text(_SQL_INSERT_INSTANCE),
            {
                "id": instance.id,
                "module_id": instance.module_id,
                "owner_kind": instance.owner_kind,
                "owner_id": instance.owner_id,
                "parent_instance_id": instance.parent_instance_id,
            },
        )
    if plan.create_instances:
        # New instances need the merged meta documents; the seed documents are
        # the single source of truth for them.
        conn.execute(sa.text(_SQL_COPY_TEMPLATE_META), {"module_id": MANAGEMENT_MODULE_ID})
    for row in plan.copy_rows:
        conn.execute(
            sa.text(_SQL_INSERT_DATA_ROW),
            {
                "id": f"midr_{_new_id()}",
                "instance_id": row.instance_id,
                "table_slug": row.table_slug,
                "row_id": row.row_id,
                "body": json.dumps(row.body or {}),
                "session_id": row.session_id,
                "created_by": row.created_by,
            },
        )
    if plan.create_instances:
        # Template rows go in **after** the copied ones: both statements are
        # ``ON CONFLICT DO NOTHING``, so this order is what keeps a real,
        # edited row (say a renamed ``profile_default``) from being shadowed by
        # the same-id template row. The seed only fills the gaps — a from-base
        # install, where there was nothing to copy.
        for instance in plan.create_instances:
            conn.execute(
                sa.text(_SQL_SEED_ROWS_FROM_TEMPLATE),
                {"module_id": MANAGEMENT_MODULE_ID, "instance_id": instance.id},
            )
    for meta in plan.copy_instance_meta:
        conn.execute(
            sa.text(_SQL_INSERT_INSTANCE_META),
            {
                "id": f"mimd_{_new_id()}",
                "instance_id": meta["instance_id"],
                "slug": meta["slug"],
                "body": json.dumps(meta.get("body") or {}),
            },
        )
    for binding in plan.copy_cabinet_bindings:
        conn.execute(
            sa.text(_SQL_INSERT_CABINET_BINDING),
            {"id": f"mcb_{_new_id()}", **_sql_params(binding)},
        )
    for binding in plan.copy_project_bindings:
        conn.execute(
            sa.text(_SQL_INSERT_PROJECT_BINDING),
            {"id": f"mpb_{_new_id()}", **_sql_params(binding)},
        )
    # Before any grant is copied and before archiving: the guard compares against
    # the untouched schema default (see _SQL_INHERIT_GRANT_SCOPE).
    if plan.inherit_grant_scope:
        conn.execute(
            sa.text(_SQL_INHERIT_GRANT_SCOPE),
            {"scope": plan.inherit_grant_scope, "module_id": MANAGEMENT_MODULE_ID},
        )
    for grant in plan.copy_company_grants:
        conn.execute(
            sa.text(_SQL_INSERT_COMPANY_GRANT),
            {"id": f"mcg_{_new_id()}", **_sql_params(grant)},
        )
    # Revoke only after the copy: the bindings are *moved* to the merged module,
    # never dropped into nothing.
    for entry in plan.revoke_cabinet_bindings:
        conn.execute(sa.text(_SQL_DELETE_CABINET_BINDING), entry)
    for entry in plan.revoke_project_bindings:
        conn.execute(sa.text(_SQL_DELETE_PROJECT_BINDING), entry)
    for entry in plan.revoke_company_grants:
        conn.execute(sa.text(_SQL_DELETE_COMPANY_GRANT), entry)
    for module_id in plan.archive_modules:
        conn.execute(sa.text(_SQL_ARCHIVE_MODULE), {"id": module_id})


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _sql_params(entry: dict[str, Any]) -> dict[str, Any]:
    """Drop the plan-only ``id`` placeholder; the SQL supplies its own id."""
    return {key: value for key, value in entry.items() if key != "id"}


def merge_management_modules(conn: sa.Connection) -> dict[str, int]:
    """Run the one-time merge. Safe to call twice; no-op without legacy modules.

    ``upsert_product_modules`` must have run first (it creates the
    ``mod_management`` catalog row and its meta documents).
    """
    if not _has_table(conn, "module_instances"):
        return {"skipped": 1}
    state = load_state(conn)
    if not state.legacy_module_ids():
        return {"skipped": 1}
    plan = plan_merge(state)
    apply_plan(conn, plan)
    counts = plan.counts()
    counts["skipped"] = 0
    return counts

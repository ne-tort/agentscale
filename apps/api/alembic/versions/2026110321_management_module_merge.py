"""Merge mod_prompts / mod_mcp / mod_files into one module «Управление».

What changed in the product
===========================

Three product modules used to be independent: «Промпты» (``mod_prompts``),
«MCP» (``mod_mcp``) and «Файлы» (``mod_files``). Prompts, MCP packages and
workspace files are one administrative area, so they are now one declarative
app — ``mod_management`` («Управление») — with a hub view that leads into the
three areas, and the old ids are gone from ``PRODUCT_MODULES``.

What this revision has to do
============================

``upsert_product_modules`` (called at the top) creates the new definition and
its meta documents, and keeps the three old catalog rows as **archived shells**
— revision 2026082711 iterates the seed list and 2026082906 writes
``materialize_roots`` meta documents for those ids, so a from-base upgrade needs
the rows to exist at that point in the chain. Creating the definition is not
enough for a deployment that has been running, though: it still holds grants,
cabinet/project bindings, per-owner ``module_instances`` and the data rows those
instances carry. Losing them would lose real prompts, MCP packages and files.

So :func:`merge_management_modules` runs once here and:

1. creates a ``mod_management`` instance for the platform scope and for every
   owner scope that had an instance of any of the three modules, copying the
   merged meta documents into each;
2. copies the data rows — ``table_slug`` and ``body`` untouched, so nothing has
   to be reshaped — from the instance each area *actually resolved to* for that
   scope into the merged instance of the same scope;
3. copies the union of each scope's ``materialize_roots`` so a project sync
   still prunes ``rules/``, ``prompts/``, ``AGENTS.md`` and ``packages/``;
4. moves cabinet bindings, project bindings and company grants onto
   ``mod_management`` (``bind_kind``/``child_may_edit`` preserved; a global bind
   wins over a local one when two areas disagreed) and revokes the old ones —
   a binding left behind would keep materializing the archived copy over the
   merged module's output on every sync;
5. sets ``status = 'archived'`` on the three old modules.

Nothing is deleted: the old catalog rows, their meta documents, their instances
and their data rows all stay, so the recycle bin and the audit trail keep
working and an operator can still inspect or export what was there.

Idempotency and from-base upgrades
==================================

Every decision is made by a pure planner (:func:`plan_merge`) over the state
read from the catalog, and every statement is ``ON CONFLICT DO NOTHING`` or
scoped by ``status <> 'archived'``. A second run therefore plans nothing, and a
database that never had the old modules — a fresh install, or
``alembic downgrade base && alembic upgrade head`` in CI — sees archived shells
with no instances and no bindings, so the merge is a no-op.

The merged meta itself comes from the seeds (``upsert_product_modules``), not
from this revision: a later seed change updates «Управление» the same way it
updates every other product module.

Revision ID: 2026110321
Revises: 2026110320
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from prodavan.application.platform.management_module_merge import (
    merge_management_modules,
)
from prodavan.application.platform.product_module_seeds import LEGACY_MODULE_ROWS
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision: str = "2026110321"
down_revision: str | Sequence[str] | None = "2026110320"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    # 1) Catalog: «Управление» definition + meta, legacy ids archived.
    upsert_product_modules(conn)
    # 2) Data: instances, rows, bindings and grants moved onto it.
    merge_management_modules(conn)


def downgrade() -> None:
    """Un-archive the three modules. The copy into «Управление» is kept.

    Downgrading cannot un-fold data: rows copied into ``mod_management`` may
    have been edited since, and the archived modules' own instances were never
    touched. So the honest reverse step is small — the old ids become product
    modules again (they still hold every row they held before the merge) and
    «Управление» keeps its copy as an ordinary module an operator can delete
    deliberately. Bindings are not restored; the operator re-binds whatever is
    needed, because guessing them back from a merged module would be a guess.
    """
    conn = op.get_bind()
    for module_id, _ in LEGACY_MODULE_ROWS:
        conn.execute(
            sa.text("UPDATE modules SET status = 'active' WHERE id = :id"),
            {"id": module_id},
        )

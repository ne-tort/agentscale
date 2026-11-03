"""Install tenant/cabinet/project RLS policies on the canonical target tables.

Revision ``2026110307`` is the last link of the wave-1 DDL chain.  It owns no
table: it only enables row level security, forces it for the table owner, and
creates two policies per canonical table:

* ``<table>_tenant_isolation`` (``FOR ALL``, ``USING`` **and** ``WITH CHECK``)
  reading ``current_setting('app.tenant_id'|'app.cabinet_id'|'app.project_id',
  true)``.  An unset or empty setting yields ``NULL``; every comparison with
  ``NULL`` is unknown, so the predicate denies.  There is no platform-wide
  fallback;
* ``<table>_trusted_service_access`` for the reviewed service role
  (``prodavan_service``) which additionally requires
  ``app.trusted_service = 'true'``, so reusing the role name in a tenant session
  does not widen access by itself.

The exact table list, predicates and parent chains live in
:mod:`prodavan.infrastructure.persistence.rls.policies` and are shared with the
acceptance tests; this module only walks that registry.

Idempotence and partial schemas
-------------------------------
The sibling revisions ``2026110301``-``2026110306`` create these tables, possibly
after this file was written.  Each table is therefore upgraded inside one
``DO`` block that:

1. checks ``to_regclass('<table>')`` and returns with a ``RAISE NOTICE`` when the
   relation is absent (the skip is *reported*, never swallowed silently - the
   revision logs the missing relations afterwards as warnings);
2. re-applies ``ENABLE``/``FORCE ROW LEVEL SECURITY`` (both are safe to repeat);
3. runs ``DROP POLICY IF EXISTS`` before every ``CREATE POLICY`` so a re-run, a
   retried transaction or a manually repaired policy converges instead of
   raising ``duplicate_object``.

Deployment ordering caveat
--------------------------
``FORCE ROW LEVEL SECURITY`` applies the policies to the table owner as well.  A
role that is neither the trusted service role nor a session with a valid
transaction-local context will read zero rows from every listed table.  This
revision must therefore be applied together with the runtime wiring from
:mod:`prodavan.infrastructure.persistence.rls.context`; applying it ahead of
that wiring intentionally fail-closes and is not a data-loss event (no data is
touched) but it is an availability event.

Downgrade removes the policies, drops ``FORCE`` and disables RLS.  It never
touches rows, grants or evidence.
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

from prodavan.infrastructure.persistence.rls import policies

revision = "2026110307"
down_revision = "2026110306"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_CANONICAL_RELATIONS_SQL = (
    "SELECT c.relname AS relation FROM pg_class AS c "
    "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p') AND n.nspname = current_schema()"
)


def _offline() -> bool:
    return bool(getattr(op.get_context(), "as_sql", False))


def _report_missing_relations() -> tuple[str, ...]:
    """Log canonical tables whose relation is absent, so a skip is visible."""

    if _offline():
        logger.info(
            "rls %s: offline mode, relation existence is checked by the DO blocks only",
            revision,
        )
        return ()
    bind = op.get_bind()
    absent = tuple(
        table
        for table in policies.registry_tables()
        if not policies.relation_exists(bind, table)
    )
    for table in absent:
        logger.warning(
            "rls %s: relation %s is absent, its policies were skipped and must be "
            "replayed after the owning revision creates the table",
            revision,
            table,
        )
    return absent


def _report_uncovered_relations() -> tuple[str, ...]:
    """Canonical-prefixed relations with neither a policy nor a reviewed exclusion."""

    if _offline():
        logger.info("rls %s: offline mode, coverage report skipped", revision)
        return ()
    relations = [str(row[0]) for row in op.get_bind().execute(text(_CANONICAL_RELATIONS_SQL))]
    uncovered = policies.uncovered_relations(relations)
    for relation in uncovered:
        logger.warning(
            "rls %s: canonical relation %s has no policy and no reviewed exclusion; "
            "register it in the rls registry or record the exclusion",
            revision,
            relation,
        )
    return uncovered


def upgrade() -> None:
    logger.info(
        "rls %s: installing %d canonical policies from registry %s",
        revision,
        len(policies.CANONICAL_TABLE_POLICIES) * 2,
        policies.REGISTRY_VERSION,
    )
    for spec in policies.CANONICAL_TABLE_POLICIES:
        op.execute(policies.upgrade_guard_sql(spec))
    absent = _report_missing_relations()
    _report_uncovered_relations()
    logger.info(
        "rls %s: %d of %d canonical tables policied",
        revision,
        len(policies.CANONICAL_TABLE_POLICIES) - len(absent),
        len(policies.CANONICAL_TABLE_POLICIES),
    )


def downgrade() -> None:
    logger.info("rls %s: removing canonical policies", revision)
    for spec in policies.CANONICAL_TABLE_POLICIES:
        op.execute(policies.downgrade_guard_sql(spec))
    _report_missing_relations()

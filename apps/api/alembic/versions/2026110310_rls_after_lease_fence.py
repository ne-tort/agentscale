"""Re-install the canonical RLS registry after the late wave-1 tables.

Revision ``2026110310`` follows ``2026110309`` and owns no table of its own.  It
exists because ``2026110307`` installs policies by walking the shared registry in
:mod:`prodavan.infrastructure.persistence.rls.policies`, and any table created
*after* that revision is skipped by its ``to_regclass`` guard.

Why this is not a no-op
-----------------------
``2026110307`` installs policies for the tables that existed when it ran.  The
execution fence table ``agent_run_leases`` is created by ``2026110309``, so the
earlier revision correctly skipped it and reported the missing relation.  Without
this revision the table would carry a tenant column but no policy — the worst of
both worlds, because readers would assume isolation that was never installed.

Behaviour
---------
* Every registry spec is re-applied with its idempotent guard SQL
  (``DROP POLICY IF EXISTS`` before ``CREATE POLICY``), so the revision is safe
  to run on a database where the earlier policies already exist.
* Tables that still do not exist are reported, never silently swallowed: the
  guard returns a notice and the revision logs the count.
* ``downgrade`` deliberately does nothing.  Dropping policies here would also
  drop the ones installed by ``2026110307``, which this revision does not own;
  the correct rollback for a policy set is a reviewed forward change.
"""

from __future__ import annotations

import logging

from alembic import op

from prodavan.infrastructure.persistence.rls import policies

revision = "2026110310"
down_revision = "2026110309"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    installed = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        op.execute(policies.upgrade_guard_sql(spec))
        installed += 1
    logger.info("rls %s: re-applied %d canonical table policies", revision, installed)


def downgrade() -> None:
    # Intentionally empty: this revision re-applies an idempotent policy set that
    # is owned by 2026110307.  Removing it would silently drop policies this
    # revision does not own.  Roll back policies only with a reviewed forward
    # revision against a database whose actual policy state is known.
    logger.info(
        "rls %s: downgrade is a no-op; canonical policies remain owned by 2026110307",
        revision,
    )

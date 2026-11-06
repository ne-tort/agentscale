"""Re-apply the canonical RLS registry after the M04 scope-evidence table.

Revision ``2026110314`` follows ``2026110313`` and owns no table. It exists
because ``2026110313`` creates ``m04_scope_promotion_evidence`` *after* the last
policy installer (``2026110312``), so on a fresh ``upgrade head`` that table
would carry a tenant column and no policy.

This is the third occurrence of the same pattern in the chain (``agent_run_leases``,
``agent_run_provider_attempts``, now the M04 scope-evidence table), which is why
``tests/migration/rls/test_policy_install_order.py`` exists: the rule "any table
adding revision must be followed by a registry re-apply" is otherwise only
enforced by review, and review already missed it twice.

Behaviour
---------
* Every registry spec is re-applied with its idempotent guard SQL, so running
  against a database where earlier policies exist is a no-op for those tables.
* Tables that still do not exist are skipped by the guard and reported, never
  silently swallowed.
* ``downgrade`` withdraws only the policies this revision is accountable for —
  the ones whose ``owner_revision`` is ``2026110313``. Policies owned by earlier
  installers stay untouched.
"""

from __future__ import annotations

import logging

from alembic import op

from prodavan.infrastructure.persistence.rls import policies

revision = "2026110314"
down_revision = "2026110313"
branch_labels = None
depends_on = None

#: Owner revisions whose policies this revision withdraws on downgrade.
_POLICIES_ADDED_HERE = frozenset({"2026110313"})

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    installed = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        op.execute(policies.upgrade_guard_sql(spec))
        installed += 1
    logger.info("rls %s: re-applied %d canonical table policies", revision, installed)


def downgrade() -> None:
    removed = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        if spec.owner_revision not in _POLICIES_ADDED_HERE:
            continue
        op.execute(policies.downgrade_guard_sql(spec))
        removed += 1
    logger.info("rls %s: withdrew %d canonical table policies", revision, removed)

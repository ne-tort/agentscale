"""Re-apply the canonical RLS registry after every late table-adding revision.

Revision ``2026110312`` follows ``2026110311`` and owns no table.

Why another pass
----------------
Policies are installed by walking the shared registry.  A revision that creates a
tenant-scoped table *after* the last installing revision leaves that table without
policies, because the installer's ``to_regclass`` guard correctly skips a relation
that does not exist yet.  This happened twice in this chain:

* ``2026110309`` created ``agent_run_leases`` after ``2026110307``;
* ``2026110311`` created ``agent_run_provider_attempts`` after the ``2026110310``
  re-apply, and ``2026110306``'s ``runtime_revision_heads`` /
  ``runtime_materialization_attempts`` / ``runtime_materialization_audit`` were
  never registered at all, so no earlier pass could cover them.

Registry membership alone is **not** proof that a policy exists, which is why the
independent DDL cross-review caught this as a blocker.

Behaviour
---------
* Every registry spec is re-applied with its idempotent guard SQL.
* ``downgrade`` drops only the policies introduced *after* the previous re-apply
  (``owner_revision == "2026110311"``).  It deliberately does not touch policies
  owned by earlier revisions: removing those would silently unprotect tables this
  revision never installed anything for.

Known structural weakness (recorded, not hidden)
------------------------------------------------
"Any table-adding revision must be followed by a registry re-apply" is enforced
only by review. ``owner_revision`` is a free string, so neither the registry nor
its validators can prove that an installing revision exists later than the owner.
A test asserts the current chain satisfies the rule; making it structural would
need a registry change outside this revision.
"""

from __future__ import annotations

import logging

from alembic import op

from prodavan.infrastructure.persistence.rls import policies

revision = "2026110312"
down_revision = "2026110311"
branch_labels = None
depends_on = None

#: Owner revisions whose policies this revision is responsible for withdrawing.
#: Everything owned by an earlier re-apply stays in place.
_POLICIES_ADDED_HERE = frozenset({"2026110311"})

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

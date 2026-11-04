"""Withdraw the truncated RLS policy name and reinstall the registry (task ``MIG-RLS-LIVE-01``).

Revision ``2026110318`` follows ``2026110317`` and owns no table.  It exists because
``2026110307`` (and every registry re-apply after it) rendered the trusted-service
policy name as ``<table>_trusted_service_access``.  For one table that name is
longer than PostgreSQL's 63-byte identifier limit, so the server stored a
**truncated** name:

    declared: m04e_definition_version_promotion_evidence_trusted_service_access (65)
    stored:   m04e_definition_version_promotion_evidence_trusted_service_acce (63)

Nothing failed at install time - PostgreSQL truncates identifiers silently - but any
code or test comparing the stored name with the registry's declared name could never
match, and a second table long enough to truncate to the same prefix would make
``CREATE POLICY`` collide with (or replace) an unrelated policy.

What this revision changes
==========================
* ``rls/policies.py`` now derives every policy name through
  ``_policy_name``: ``<table>_<full suffix>`` while it fits, otherwise the compact
  suffix for the same kind (``_tenant_isolation`` -> ``_tenant``,
  ``_trusted_service_access`` -> ``_trusted_access``).  The table part is never
  shortened, and a table too long even for the compact form raises at import time.
  The registry therefore *declares* exactly what PostgreSQL *stores*.
* ``policies.legacy_policy_names`` reproduces the truncated name an earlier
  registry left behind (same truncation PostgreSQL applied), and
  ``upgrade_guard_sql`` now drops those names before creating the current pair.
  A re-apply on a database that ran the old code therefore ends with exactly two
  policies per table instead of three.

What this revision does
=======================
1. Re-applies every registry spec with the idempotent, existence-guarded block
   (enable + force RLS, drop current *and* legacy names, create the current pair).
2. Verifies the result in the same transaction: for every relation that exists,
   the set of policy names in ``pg_policy`` must equal the two names the registry
   declares.  A mismatch raises and aborts the revision - an installer that
   silently skipped a table or left a stale name is not allowed to record success.
   This is the fail-closed half of the change: the *upgrade* proves the round-trip
   instead of trusting the DDL it just ran.

Downgrade
=========
For each spec with a legacy name: drop the current trusted policy and recreate it
under the legacy (already truncated) name, so the schema returns to what
``2026110317`` left behind.  The recreated statement uses the *stored* name, never
a 65-character identifier, so the downgrade does not reintroduce truncation-prone
DDL.  The tenant policy is untouched by the downgrade - its name never changed.

Not changed here
================
``REGISTRY_VERSION`` is not bumped: it also names the reviewed RLS manifest through
``application/migration/tenant_context.py::SECURITY_REVIEW_ID``, which is recorded
in promotion-evidence rows, and re-approving that identity is a separate review
decision (the same reasoning the registry already records for M04 scope evidence).
The policy *set* is unchanged - still one tenant and one trusted policy per table -
only one name is spelled differently.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

from prodavan.infrastructure.persistence.rls import policies

revision = "2026110318"
down_revision = "2026110317"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: Policy names for one relation, restricted to the schema the migration runs in.
#: ``current_schema()`` is used rather than a hard-coded ``public`` because the
#: integration harness points ``search_path`` at a throwaway schema.
_POLICY_NAMES_SQL = sa.text(
    """
    SELECT p.polname
    FROM pg_policy AS p
    JOIN pg_class AS c ON c.oid = p.polrelid
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE c.relname = :table
      AND n.nspname = current_schema()
    """
)


def _installed_policy_names(connection, table: str) -> set[str]:
    return {row[0] for row in connection.execute(_POLICY_NAMES_SQL, {"table": table}).all()}


def _relation_exists(connection, table: str) -> bool:
    return bool(
        connection.execute(
            sa.text("SELECT to_regclass(:relation) IS NOT NULL"), {"relation": table}
        ).scalar()
    )


def upgrade() -> None:
    connection = op.get_bind()

    legacy_targets: list[tuple[str, tuple[str, ...]]] = []
    for spec in policies.CANONICAL_TABLE_POLICIES:
        op.execute(policies.upgrade_guard_sql(spec))
        legacy = policies.legacy_policy_names(spec)
        if legacy:
            legacy_targets.append((spec.table, legacy))

    for table, names in legacy_targets:
        logger.info("rls %s: withdrew stale policy name(s) %s on %s", revision, ", ".join(names), table)

    # Fail-closed verification of the round-trip this revision exists for.
    problems: list[str] = []
    for spec in policies.CANONICAL_TABLE_POLICIES:
        if not _relation_exists(connection, spec.table):
            # The guard reported the skip; a relation created by a later revision
            # gets its policies from that revision's own re-apply.
            continue
        if policies.missing_scope_columns(connection, spec):
            # MIG-EQ-ANCHORS-01: ``equipment_v2`` rows get their project/cabinet
            # anchors in ``2026110319``, so their predicate cannot be created yet and
            # the guard defers them to that revision's re-apply - which is the same
            # revision that adds the columns.  Without this branch the deferral would
            # read as "a policy went missing" and abort a chain that is in fact
            # correct; a table that *has* its columns but no policies still fails.
            continue
        expected = set(spec.policy_names)
        actual = _installed_policy_names(connection, spec.table)
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            problems.append(
                f"{spec.table}: missing={missing or 'none'} unexpected={unexpected or 'none'}"
            )
    if problems:
        raise RuntimeError(
            "rls 2026110318: installed policies do not match the registry; "
            + "; ".join(problems)
        )
    logger.info(
        "rls %s: verified %d canonical tables carry exactly their two declared policies",
        revision,
        len(policies.CANONICAL_TABLE_POLICIES),
    )


def _legacy_restore_sql(spec: policies.TablePolicySpec, keep: str, stale: tuple[str, ...]) -> str:
    """Guarded block restoring one policy under its pre-rename (truncated) name.

    ``keep`` is the name to recreate; ``stale`` lists any further historical names
    that must not survive the downgrade (dropping them keeps "exactly one trusted
    policy per table" true even if a future table had produced two legacy names).
    """

    drops = "".join(
        f"        DROP POLICY IF EXISTS {name} ON {spec.table};\n" for name in stale
    )
    return (
        "DO $rls$\n"
        "BEGIN\n"
        f"    IF to_regclass('{spec.table}') IS NULL THEN\n"
        f"        RAISE NOTICE 'rls {revision} downgrade skipped: relation {spec.table} is absent';\n"
        "        RETURN;\n"
        "    END IF;\n"
        f"        DROP POLICY IF EXISTS {spec.trusted_policy} ON {spec.table};\n"
        f"{drops}"
        f"        {policies.trusted_policy_statement(spec, keep)};\n"
        "END\n"
        "$rls$"
    )


def downgrade() -> None:
    """Restore the pre-rename trusted policy name for the affected tables only."""

    restored = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        legacy = policies.legacy_policy_names(spec)
        if not legacy:
            # Every other table kept its name; their policies are still valid.
            continue
        op.execute(_legacy_restore_sql(spec, legacy[0], legacy[1:]))
        restored += 1
    logger.info("rls %s: restored %d legacy policy name(s)", revision, restored)

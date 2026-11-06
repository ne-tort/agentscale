"""Late validation gate for every staged constraint of the DDL wave (MIG-DDL-VALIDATE-01).

Revision ``2026110308`` closes the wave ``2026110301``-``2026110307`` with the one
step those revisions deliberately left out: an explicit ``VALIDATE CONSTRAINT``
pass over every constraint that was staged instead of proven at creation time.

Why validation is its own revision
----------------------------------
1. Schema creation must not be gated on a table scan. The wave creates empty
   tables and keeps every positive path closed (``canonical_positive_writes =
   false``, ``lifecycle = 'quarantined'``, blocked promotion outcomes), so adding
   the constraints first and proving them second keeps the initial DDL cheap,
   restartable and independent from the data that lands later.
2. A staged constraint is not a passed gate. ``DEFERRABLE INITIALLY DEFERRED``
   changes *when* a constraint is enforced inside a transaction; it says nothing
   about rows that already exist. Until ``pg_constraint.convalidated`` is true for
   every staged constraint, no revision, adapter, promotion record or cutover
   decision in this wave may claim "0 violations".
3. Validation is the only operation of the wave that can fail because of
   historical data. Isolating it keeps such a failure attributable to one
   revision and lets it be replayed after repair without touching the schema.

What was staged (static read of 2026110301-2026110307)
-----------------------------------------------------
None of the seven revisions emits ``NOT VALID``; the staged set is the set of
late/cyclic foreign keys plus the exact-instance binding that the wave shipped
without validation evidence:

* ``2026110302`` -- ``fk_m04e_definition_current_version_same_definition``, the
  cycle-breaking definition -> current version key;
* ``2026110303`` -- the seven reciprocal registry/instance keys:
  ``fk_m05_registry_instance_link_evidence``,
  ``fk_m05_instance_registry_{tenant,cabinet,project}_link`` and
  ``fk_m05_registry_instance_{tenant,cabinet,project}_link``;
* ``2026110304`` -- ``fk_cap_grant_instance_exact``, the grant -> instance
  binding whose instance partition is still quarantined.

PostgreSQL marks a foreign key added with ``ALTER TABLE ... ADD CONSTRAINT`` as
validated unless ``NOT VALID`` is spelled out, and every table above was empty
when the constraint landed. Most calls below are therefore expected to be
no-ops on a database that followed the chain exactly. That is precisely why the
``convalidated`` check exists: the revision reports the no-op instead of
pretending to have scanned rows, and it performs the real scan wherever a
constraint was staged, rebuilt or added after rows already existed. The pass is
also the recorded evidence gate, so it runs unconditionally.

Lock profile
------------
``VALIDATE CONSTRAINT`` takes ``SHARE UPDATE EXCLUSIVE`` on the altered table and
``ROW SHARE`` on the referenced tables -- not ``ACCESS EXCLUSIVE``. Ordinary reads
and writes continue while the scan runs and no table is rewritten. The scan is
still proportional to table size, so run the revision while the affected
partitions are quiet; this revision contains no other DDL and never blocks a
table exclusively.

Idempotence and fail-closed behaviour
-------------------------------------
Each constraint is handled by one ``DO`` block that
1. resolves the table with ``to_regclass`` and returns with a ``RAISE NOTICE``
   when the relation is absent;
2. looks the constraint up in ``pg_constraint`` by table + name and returns with
   a ``RAISE NOTICE`` when it is absent or is not a foreign key;
3. returns with a ``RAISE NOTICE`` when ``convalidated`` is already true;
4. otherwise runs ``ALTER TABLE ... VALIDATE CONSTRAINT`` and logs success.
No ``EXCEPTION`` handler is installed: a genuine validation failure (orphan rows,
cross-tenant evidence, a dropped target key) aborts the migration instead of
being swallowed. A terminal gate then re-reads ``pg_constraint`` and raises
``MIG-DDL-VALIDATE-01 blocker`` if any listed constraint is still missing or not
validated, so a skip can never be mistaken for a passed gate.

Downgrade
---------
``VALIDATE CONSTRAINT`` is unidirectional: it proves a property of the data and
has no inverse operation. ``downgrade()`` is therefore a documented no-op -- it
does not drop constraints, does not rewrite rows and does not attempt to
"unvalidate" anything. Reopening the gate requires a separate revision and is
only safe while the referencing tables are empty; once rows exist, dropping a
proven constraint returns the wave to an unproven state, not to a previous one.

Static test: ``apps/api/tests/migration/ddl_validation/``.
"""

from __future__ import annotations

import logging
import re

from alembic import op

revision = "2026110308"
down_revision = "2026110307"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

NOTICE_PREFIX = "MIG-DDL-VALIDATE-01"
BLOCKER_PREFIX = "MIG-DDL-VALIDATE-01 blocker"

#: Every name below is a plain lower-case PostgreSQL identifier. The rendering
#: helpers refuse anything else, so a future edit cannot inject DDL through the
#: spec table -- and this revision never depends on Python-side introspection.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

#: Constraints staged by the wave, in the order the revision validates them.
#: ``staging`` records *why* the constraint was not proven at creation time:
#: ``deferred_cycle`` -- added after both cycle sides existed, as
#: ``DEFERRABLE INITIALLY DEFERRED`` under ``use_alter``;
#: ``closed_partition_binding`` -- an exact binding whose owning partition is
#: still quarantined, so the wave shipped no validation evidence for it.
#: The acceptance test derives this set independently from revisions
#: ``2026110301``-``2026110307`` and fails on any missing or extra entry.
VALIDATION_SPECS = (
    {
        "table": "m04e_dynamic_app_definitions",
        "constraint": "fk_m04e_definition_current_version_same_definition",
        "owner_revision": "2026110302",
        "staging": "deferred_cycle",
    },
    {
        "table": "m05_plugin_instance_registry_links",
        "constraint": "fk_m05_registry_instance_link_evidence",
        "owner_revision": "2026110303",
        "staging": "deferred_cycle",
    },
    {
        "table": "m05_plugin_instances",
        "constraint": "fk_m05_instance_registry_tenant_link",
        "owner_revision": "2026110303",
        "staging": "deferred_cycle",
    },
    {
        "table": "m05_plugin_instances",
        "constraint": "fk_m05_instance_registry_cabinet_link",
        "owner_revision": "2026110303",
        "staging": "deferred_cycle",
    },
    {
        "table": "m05_plugin_instances",
        "constraint": "fk_m05_instance_registry_project_link",
        "owner_revision": "2026110303",
        "staging": "deferred_cycle",
    },
    {
        "table": "scope_registry",
        "constraint": "fk_m05_registry_instance_tenant_link",
        "owner_revision": "2026110303",
        "staging": "deferred_cycle",
    },
    {
        "table": "scope_registry",
        "constraint": "fk_m05_registry_instance_cabinet_link",
        "owner_revision": "2026110303",
        "staging": "deferred_cycle",
    },
    {
        "table": "scope_registry",
        "constraint": "fk_m05_registry_instance_project_link",
        "owner_revision": "2026110303",
        "staging": "deferred_cycle",
    },
    {
        "table": "dynamic_app_capability_grants",
        "constraint": "fk_cap_grant_instance_exact",
        "owner_revision": "2026110304",
        "staging": "closed_partition_binding",
    },
)


def _identifier(name: str) -> str:
    """Render a quoted identifier, failing closed on anything unexpected."""
    if not isinstance(name, str) or _IDENTIFIER.match(name) is None:
        raise ValueError(f"{NOTICE_PREFIX}: refusing to render identifier {name!r}")
    return f'"{name}"'


def _literal(value: str) -> str:
    """Render a single-quoted literal for an already validated identifier."""
    if not isinstance(value, str) or _IDENTIFIER.match(value) is None:
        raise ValueError(f"{NOTICE_PREFIX}: refusing to render literal {value!r}")
    return f"'{value}'"


def _assert_unique_specs() -> None:
    """Fail closed on a duplicated entry: one constraint, one validation."""
    pairs = [(spec["table"], spec["constraint"]) for spec in VALIDATION_SPECS]
    if len(pairs) != len(set(pairs)):
        raise RuntimeError(f"{NOTICE_PREFIX}: duplicate constraint in VALIDATION_SPECS")
    names = [spec["constraint"] for spec in VALIDATION_SPECS]
    if len(names) != len(set(names)):
        raise RuntimeError(f"{NOTICE_PREFIX}: a constraint name appears twice")


def validation_statement(spec: dict) -> str:
    """One idempotent ``DO`` block that validates a single staged constraint.

    The statement is pure SQL: no Python-side introspection and no client-side
    placeholders, so it behaves identically in online and ``--sql`` modes. Every
    skip path reports itself with ``RAISE NOTICE``; nothing is swallowed.
    """
    table = spec["table"]
    constraint = spec["constraint"]
    owner = spec["owner_revision"]

    notice_table_absent = (
        f"{NOTICE_PREFIX}: table {table} is absent, "
        f"constraint {constraint} from revision {owner} was left unvalidated"
    )
    notice_constraint_absent = (
        f"{NOTICE_PREFIX}: constraint {constraint} is absent on {table}, "
        "nothing to validate"
    )
    notice_not_foreign_key = (
        f"{NOTICE_PREFIX}: object {constraint} on {table} is not a foreign key, "
        "validation skipped"
    )
    notice_already_valid = (
        f"{NOTICE_PREFIX}: constraint {constraint} on {table} is already validated, "
        "no-op"
    )
    notice_validated = f"{NOTICE_PREFIX}: constraint {constraint} on {table} validated"

    # The only statement that consumes a lock wider than ACCESS SHARE:
    # SHARE UPDATE EXCLUSIVE on the altered table, ROW SHARE on the referent.
    ddl = (
        f"ALTER TABLE {_identifier(table)} "
        f"VALIDATE CONSTRAINT {_identifier(constraint)};"
    )
    return f"""
DO $validate_constraint$
DECLARE
    v_relation regclass;
    v_validated boolean;
    v_kind "char";
BEGIN
    v_relation := to_regclass({_literal(table)});
    IF v_relation IS NULL THEN
        RAISE NOTICE '{notice_table_absent}';
        RETURN;
    END IF;

    SELECT c.convalidated, c.contype
      INTO v_validated, v_kind
      FROM pg_constraint AS c
     WHERE c.conrelid = v_relation
       AND c.conname = {_literal(constraint)};

    IF NOT FOUND THEN
        RAISE NOTICE '{notice_constraint_absent}';
        RETURN;
    END IF;

    IF v_kind <> 'f' THEN
        RAISE NOTICE '{notice_not_foreign_key}';
        RETURN;
    END IF;

    IF v_validated THEN
        RAISE NOTICE '{notice_already_valid}';
        RETURN;
    END IF;

    {ddl}

    RAISE NOTICE '{notice_validated}';
END
$validate_constraint$;
"""


def validation_gate_statement() -> str:
    """Terminal postcondition: every listed constraint really is validated.

    The per-constraint blocks report skips instead of aborting, so the gate is
    what keeps the revision fail-closed: a missing relation, a dropped
    constraint or a type collision leaves an entry unvalidated and the gate
    raises ``MIG-DDL-VALIDATE-01 blocker`` naming every offender.
    """
    values = ",\n                ".join(
        f"({_literal(spec['table'])}, {_literal(spec['constraint'])})"
        for spec in VALIDATION_SPECS
    )
    blocker_message = (
        f"{BLOCKER_PREFIX}: constraints are still missing or not validated "
        f"after revision {revision}: "
    )
    blocker_hint = (
        "Repair the referenced rows or restore the owning revision, replay "
        f"revision {revision}, then record a zero-violation claim."
    )
    return f"""
DO $validate_constraint_gate$
DECLARE
    v_not_validated text;
BEGIN
    SELECT string_agg(v.constraint_name, ', ' ORDER BY v.constraint_name)
      INTO v_not_validated
      FROM (VALUES
                {values}
           ) AS v(table_name, constraint_name)
     WHERE NOT EXISTS (
               SELECT 1
                 FROM pg_constraint AS c
                WHERE c.conrelid = to_regclass(v.table_name)
                  AND c.conname = v.constraint_name
                  AND c.contype = 'f'
                  AND c.convalidated
           );

    IF v_not_validated IS NOT NULL THEN
        RAISE EXCEPTION USING
            MESSAGE = '{blocker_message}' || v_not_validated,
            HINT = '{blocker_hint}';
    END IF;
END
$validate_constraint_gate$;
"""


def upgrade() -> None:
    """Validate every staged constraint once, then assert the postcondition."""
    _assert_unique_specs()
    logger.info(
        "%s: validating %d staged constraints from revisions 2026110302-2026110304",
        NOTICE_PREFIX,
        len(VALIDATION_SPECS),
    )
    for spec in VALIDATION_SPECS:
        op.execute(validation_statement(spec))
    op.execute(validation_gate_statement())
    logger.info(
        "%s: validation pass finished, every listed constraint is verified valid",
        NOTICE_PREFIX,
    )


def downgrade() -> None:
    """Documented no-op: ``VALIDATE CONSTRAINT`` has no inverse operation.

    Validation is unidirectional. It proves that the existing rows satisfy the
    constraint and records that fact in ``pg_constraint.convalidated``; it never
    rewrites a row, so there is nothing to undo on the way back. This function
    therefore neither drops the constraints nor touches data, and it does not
    pretend to "unvalidate" them: replaying the parent revision would leave the
    wave unproven while the proof still holds, which is worse than an honest
    no-op. Reopening the gate requires a separate revision and is only safe
    while the referencing tables are empty.
    """
    # Intentionally empty: no DDL, no DML, no constraint drop. A future revision
    # is the only place that may change this decision.
    return None

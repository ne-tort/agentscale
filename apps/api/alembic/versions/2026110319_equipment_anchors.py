"""Tenant/project anchors for the ``equipment_v2`` tables (task MIG-EQ-ANCHORS-01).

Revision ``2026110319`` (``down_revision`` ``2026110318``) does three things and
then re-applies the canonical RLS registry, so no table it touches is left for a
later installing pass.

(The task was drafted against id ``2026110318`` with parent ``2026110317``.  That
id is already taken by ``2026110318_rls_policy_rename``, which lands on the same
parent; re-using it would have forked the chain into two heads, so this revision
takes the next free id and chains after it.  Nothing else in the design depends on
the number.)

What was wrong
==============
``equipment_v2_*`` carried ``tenant_id`` on exactly three tables (``ingestion_runs``,
``requests``, ``tenders``) and **no** ``project_id``/``cabinet_id`` anywhere:

* a procurement request could not be tied to the project or cabinet it was raised
  for, so the partition boundary the design asks for (``equipment-persistence-
  adapter-contract.md`` section 4.1: "``project_id`` is the partition boundary for
  requests, request lines, candidate groups, tenders, selections") did not exist;
* an RLS policy had to inherit the tenant through two or three hops
  (``offer -> snapshot -> run``, ``candidate_group -> request_line -> request``),
  which is both expensive per row and fragile: a NULL or dangling parent makes the
  ``EXISTS`` false, so the failure mode is "row silently invisible";
* the two JSONB reference arrays had no relational counterpart at all.

What this revision changes
==========================
1. ``ADD COLUMN project_id, cabinet_id`` on ``equipment_v2_ingestion_runs``,
   ``equipment_v2_requests`` and ``equipment_v2_tenders``.  Both columns are
   **nullable** and both are ``ON DELETE RESTRICT`` foreign keys to
   ``projects``/``cabinet_instances``: a project or cabinet that still owns
   procurement evidence cannot be deleted out from under it, and no existing row
   is invalidated (they all keep ``NULL`` until the backfill runs).
2. Two indexes per anchored table, one for the project feed (``project_id`` with
   the status/created ordering the screens actually use) and one for the cabinet
   sweep.
3. ``CREATE TABLE equipment_v2_candidate_group_offers`` and
   ``equipment_v2_tender_candidate_groups`` - the association tables of
   ``equipment-association-tables.md`` 3.1/3.2: pair primary key, positive
   ``ordinal`` unique **per parent and deferred**, ``tenant_id`` mandatory,
   nullable ``project_id``/``cabinet_id``, and the parent references with the
   design's delete semantics (group membership ``CASCADE`` from the group,
   ``RESTRICT`` on the offer; tender membership ``CASCADE`` from the tender,
   ``RESTRICT`` on the group).
4. Re-apply of ``policies.CANONICAL_TABLE_POLICIES`` via ``upgrade_guard_sql``.
   The two new tables are registered with ``owner_revision="2026110318"``, and the
   three anchored tables switched their predicates from inherited chains to the
   direct nullable columns.

Decisions recorded here (and why the "missing" pieces are missing)
=================================================================
* **Nullable, not ``NOT NULL``.** The design (2.1) is explicit: a nullable anchor
  plus a backfill first, and a *separate* revision that forbids ``NULL`` second.
  Making them ``NOT NULL`` in the creating revision would fail on every existing
  row, and a server default would invent a project - the exact "guessing" the
  document forbids.  ``downgrade`` therefore only drops columns it added; there is
  no NOT NULL transition to reverse.
* **No scoped composite FK yet.** ``fk_ecg_offer_group_scope`` /
  ``fk_etender_group_tender_scope`` of sections 3.1/3.2 need
  ``UNIQUE (id, tenant_id, project_id)`` on ``candidate_groups``/``tenders`` and
  ``project_id NOT NULL`` on both sides.  None of that exists yet, so adding the
  composite key here would target a key PostgreSQL does not have and would reject
  every association row written before the backfill.  The single-column parent
  references installed here are the part a plain FK can prove today; the scoped
  keys belong to the post-backfill revision together with ``NOT NULL``.
* **Shared catalog stays shared.** Snapshots and offers keep inheriting the
  tenant through ``ingestion_run``; ``ingestion_runs.project_id`` records *which
  project asked for the import* and is not propagated to the catalog rows.
* **The JSONB arrays stay authoritative.** ``candidate_groups.offer_ids`` and
  ``tenders.candidate_group_ids`` are untouched: this revision is the "expand"
  step of section 6 (no backfill, no dual-write, no read cutover), and the arrays
  remain the rollback source until the cutover window closes.
* **Deferred ordinal uniqueness.** A reorder exchanges two ``ordinal`` values in
  one statement; with an immediate unique key it would transiently collide.
  ``DEFERRABLE INITIALLY DEFERRED`` is what section 3.1/3.2 specifies.
* **No data statement, no trigger, no grant.** Nothing here writes a row, and the
  same-request / selected-offer-membership invariants stay service/trigger work
  (a plain constraint cannot express them).
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op
from prodavan.infrastructure.persistence.rls import policies

revision = "2026110319"
down_revision = "2026110318"
branch_labels = None
depends_on = None

#: Tables that receive ``project_id``/``cabinet_id``, in the order the columns are
#: added.  ``downgrade`` walks the same list backwards.
ANCHORED_TABLES: tuple[str, ...] = (
    "equipment_v2_ingestion_runs",
    "equipment_v2_requests",
    "equipment_v2_tenders",
)

#: The two anchor columns, in creation order (``downgrade`` reverses it).
ANCHOR_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (column, target table, target column)
    ("project_id", "projects", "id"),
    ("cabinet_id", "cabinet_instances", "id"),
)

#: Indexes on the new anchor columns: ``(name, table, expressions)``.  The
#: expressions are the model's, including the ``DESC`` ordering the screens use, so
#: ``alembic revision --autogenerate`` stays empty.
ANCHOR_INDEXES: tuple[tuple[str, str, tuple[object, ...]], ...] = (
    (
        "ix_equipment_v2_ingestion_project_created",
        "equipment_v2_ingestion_runs",
        ("project_id", sa.text("created_at DESC")),
    ),
    ("ix_equipment_v2_ingestion_cabinet_id", "equipment_v2_ingestion_runs", ("cabinet_id",)),
    (
        "ix_equipment_v2_requests_project_status_created",
        "equipment_v2_requests",
        ("project_id", "status", sa.text("created_at DESC")),
    ),
    ("ix_equipment_v2_requests_cabinet_id", "equipment_v2_requests", ("cabinet_id",)),
    (
        "ix_equipment_v2_tenders_project_status",
        "equipment_v2_tenders",
        ("project_id", "status"),
    ),
    ("ix_equipment_v2_tenders_cabinet_id", "equipment_v2_tenders", ("cabinet_id",)),
)

#: Association tables, in creation order (``downgrade`` reverses it).
ASSOCIATION_TABLES: tuple[str, ...] = (
    "equipment_v2_candidate_group_offers",
    "equipment_v2_tender_candidate_groups",
)

#: Indexes on the association tables: ``(name, table, columns)``.
ASSOCIATION_INDEXES: tuple[tuple[str, str, tuple[object, ...]], ...] = (
    ("ix_ecg_offer_by_offer", "equipment_v2_candidate_group_offers", ("tenant_id", "offer_id")),
    ("ix_ecg_offer_project", "equipment_v2_candidate_group_offers", ("project_id",)),
    (
        "ix_etender_group_by_group",
        "equipment_v2_tender_candidate_groups",
        ("tenant_id", "candidate_group_id"),
    ),
    ("ix_etender_group_project", "equipment_v2_tender_candidate_groups", ("project_id",)),
)

#: Policies this revision is accountable for withdrawing on downgrade: exactly the
#: tables it creates.  Withdrawing an older table's policy would unprotect a table
#: this revision installed nothing for (see ``2026110316``).
_POLICIES_ADDED_HERE = frozenset({revision})

#: What the three anchored tables were policied with *before* this revision, i.e.
#: from ``2026110305`` until ``2026110318``: the same tenant anchor and the same
#: trusted policy, without a cabinet/project scope, because neither column existed.
#: ``downgrade`` re-installs exactly these specs after dropping the columns, so a
#: downgrade ends with the tables protected by the predicate their schema supports
#: instead of unprotected.  Written as specs (not raw SQL) so the names and the
#: trusted policy are still derived from the registry code.
PRE_ANCHOR_POLICIES: tuple[policies.TablePolicySpec, ...] = tuple(
    policies.TablePolicySpec(
        table=table_name,
        owner_revision="2026110305",
        tenant=policies.ScopedColumn(column="tenant_id"),
        trusted_command="ALL",
        notes="Pre-anchor predicate restored by the downgrade of 2026110319.",
    )
    for table_name in (
        "equipment_v2_ingestion_runs",
        "equipment_v2_requests",
        "equipment_v2_tenders",
    )
)

logger = logging.getLogger("alembic.runtime.migration")

#: Policy names of one relation, restricted to the schema the migration runs in.
#: ``current_schema()`` rather than a hard-coded ``public`` because the harness
#: points ``search_path`` at a throwaway schema.
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


def _verify_policies(connection) -> int:
    """Fail closed unless every existing canonical relation carries its two policies.

    The upgrade guard defers a table whose scope columns do not exist yet (they may
    be added by a later revision).  This revision *is* the one that adds those
    columns for ``equipment_v2``, so from here on every registered relation must
    already exist and be policied: a deferral that survives to this point, or a
    block that silently did nothing, aborts the migration instead of recording
    success.  Returning the count keeps the caller's log honest.
    """

    problems: list[str] = []
    verified = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        if not policies.relation_exists(connection, spec.table):
            # Created by a revision that lands later in the chain; its own re-apply
            # installs the policies, and that revision verifies them.
            continue
        expected = set(spec.policy_names)
        actual = {
            row[0] for row in connection.execute(_POLICY_NAMES_SQL, {"table": spec.table}).all()
        }
        if actual != expected:
            problems.append(
                f"{spec.table}: missing={sorted(expected - actual) or 'none'} "
                f"unexpected={sorted(actual - expected) or 'none'}"
            )
        verified += 1
    if problems:
        raise RuntimeError(
            f"rls {revision}: installed policies do not match the registry; " + "; ".join(problems)
        )
    return verified


def _add_anchor_columns() -> None:
    """Add the two nullable anchors, each with its ``RESTRICT`` foreign key."""

    for table_name in ANCHORED_TABLES:
        for column_name, target_table, target_column in ANCHOR_COLUMNS:
            op.add_column(
                table_name,
                sa.Column(column_name, sa.String(40), nullable=True),
            )
            op.create_foreign_key(
                f"fk_{table_name}_{column_name}",
                table_name,
                target_table,
                [column_name],
                [target_column],
                ondelete="RESTRICT",
            )


def _create_association_tables() -> None:
    """Create the two ordered association tables of the design (3.1 and 3.2)."""

    op.create_table(
        "equipment_v2_candidate_group_offers",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=True),
        sa.Column("cabinet_id", sa.String(40), nullable=True),
        sa.Column("candidate_group_id", sa.String(40), nullable=False),
        sa.Column("offer_id", sa.String(40), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("candidate_group_id", "offer_id"),
        # Reorders exchange ordinals inside one statement, so the key must be
        # deferred rather than immediate (design 3.1).
        sa.UniqueConstraint(
            "candidate_group_id",
            "ordinal",
            name="uq_ecg_offer_ordinal",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("ordinal > 0", name="ck_ecg_offer_ordinal_positive"),
        sa.ForeignKeyConstraint(
            ["candidate_group_id"],
            ["equipment_v2_candidate_groups.id"],
            name="fk_ecg_offer_candidate_group",
            ondelete="CASCADE",
        ),
        # An offer that is still referenced cannot be deleted; a snapshot delete
        # then aborts atomically instead of silently losing candidates.
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["equipment_v2_catalog_offers.id"],
            name="fk_ecg_offer_offer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_equipment_v2_candidate_group_offers_project_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cabinet_id"],
            ["cabinet_instances.id"],
            name="fk_equipment_v2_candidate_group_offers_cabinet_id",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "equipment_v2_tender_candidate_groups",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=True),
        sa.Column("cabinet_id", sa.String(40), nullable=True),
        sa.Column("tender_id", sa.String(40), nullable=False),
        sa.Column("candidate_group_id", sa.String(40), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("tender_id", "candidate_group_id"),
        sa.UniqueConstraint(
            "tender_id",
            "ordinal",
            name="uq_etender_group_ordinal",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("ordinal > 0", name="ck_etender_group_ordinal_positive"),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["equipment_v2_tenders.id"],
            name="fk_etender_group_tender",
            ondelete="CASCADE",
        ),
        # A group that is part of a tender cannot vanish; the tender's own delete
        # removes only its membership rows.
        sa.ForeignKeyConstraint(
            ["candidate_group_id"],
            ["equipment_v2_candidate_groups.id"],
            name="fk_etender_group_candidate_group",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_equipment_v2_tender_candidate_groups_project_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cabinet_id"],
            ["cabinet_instances.id"],
            name="fk_equipment_v2_tender_candidate_groups_cabinet_id",
            ondelete="RESTRICT",
        ),
    )


def upgrade() -> None:
    # 1. The anchors. Nullable first (design 2.1); a later post-backfill revision
    #    forbids NULL once the values are proven, and this one is the schema they
    #    are proven against.
    _add_anchor_columns()

    # 2. Their lookup indexes.
    for index_name, table_name, columns in ANCHOR_INDEXES:
        op.create_index(index_name, table_name, list(columns))

    # 3. The ordered association tables (expand stage of the design's cutover).
    _create_association_tables()
    for index_name, table_name, columns in ASSOCIATION_INDEXES:
        op.create_index(index_name, table_name, list(columns))

    # 4. RLS for everything this revision created or re-scoped. This is the registry
    #    re-apply for owner_revision 2026110319: a table-adding revision that leaves
    #    its own policy to "the next pass" is the defect the install-order gate
    #    exists for (agent_run_leases, agent_run_provider_attempts,
    #    m04_scope_promotion_evidence). The pass is idempotent, so the three
    #    anchored tables simply get their new predicate.
    installed = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        op.execute(policies.upgrade_guard_sql(spec))
        installed += 1
    verified = _verify_policies(op.get_bind())
    logger.info(
        "rls %s: re-applied %d canonical table policies after anchoring %d equipment tables "
        "and creating %d association tables; verified %d existing relations",
        revision,
        installed,
        len(ANCHORED_TABLES),
        len(ASSOCIATION_TABLES),
        verified,
    )


def downgrade() -> None:
    """Remove only this revision's objects, in reverse creation order.

    Policies first (they belong to the association tables about to disappear, and
    the anchored tables' predicates *read* the columns about to be dropped - a
    ``DROP COLUMN`` refuses while a policy depends on it, which is PostgreSQL
    protecting us from leaving a policy that names a missing column).  Then the
    association indexes and tables, then the anchor indexes, then the foreign keys
    and columns, and finally the pre-anchor predicates are restored, because after
    this revision the three tables are back to the tenant-only policy they carried
    from ``2026110305`` on.  Policies owned by earlier installers for other tables
    are never touched.
    """

    removed = 0
    for spec in policies.CANONICAL_TABLE_POLICIES:
        if spec.owner_revision not in _POLICIES_ADDED_HERE:
            continue
        op.execute(policies.downgrade_guard_sql(spec))
        removed += 1

    for index_name, table_name, _columns in reversed(ASSOCIATION_INDEXES):
        op.drop_index(index_name, table_name=table_name)

    for table_name in reversed(ASSOCIATION_TABLES):
        op.drop_table(table_name)

    for index_name, table_name, _columns in reversed(ANCHOR_INDEXES):
        op.drop_index(index_name, table_name=table_name)

    # The anchored tables' predicates name the new columns, so they have to be
    # withdrawn before the columns can go.
    current = {spec.table: spec for spec in policies.CANONICAL_TABLE_POLICIES}
    for table_name in ANCHORED_TABLES:
        spec = current.get(table_name)
        if spec is not None:
            op.execute(policies.downgrade_guard_sql(spec))

    # Columns before keys would drop the key implicitly; drop the key explicitly so
    # the reverse is the exact mirror of ``upgrade``.
    for table_name in reversed(ANCHORED_TABLES):
        for column_name, _target_table, _target_column in reversed(ANCHOR_COLUMNS):
            op.drop_constraint(f"fk_{table_name}_{column_name}", table_name, type_="foreignkey")
            op.drop_column(table_name, column_name)

    # Back to the pre-anchor predicate (tenant only, cabinet/project inherited
    # through the parent chain or absent).  Leaving the tables unpolicied after a
    # downgrade would be a real weakening, not a restore.
    for spec in PRE_ANCHOR_POLICIES:
        op.execute(policies.upgrade_guard_sql(spec))

    logger.info(
        "rls %s: withdrew %d canonical table policies, dropped %d association tables, "
        "removed the anchors of %d equipment tables and restored their pre-anchor policies",
        revision,
        removed,
        len(ASSOCIATION_TABLES),
        len(ANCHORED_TABLES),
    )

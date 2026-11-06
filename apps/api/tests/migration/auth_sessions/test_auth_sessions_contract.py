"""Contract for ``MIG-AUTHSESS-01``: specification ↔ model ↔ revision ↔ RLS decision.

Three artefacts describe ``auth_sessions`` and the point of this module is that none of them
can drift from the others:

* ``application/migration/principal_resolver.py::AUTH_SESSION_CORRELATION_GAP`` — the
  reviewed specification (the nine columns, the two keys, the four semantic rules);
* ``models/auth_sessions.py`` — the declarative schema source of truth;
* ``alembic/versions/2026110317_auth_sessions.py`` — the DDL that reaches the database.

covered here
============
* chain shape: ``2026110317 -> 2026110316``, one head, no fork, no sibling tables;
* column parity (name, PostgreSQL type, nullability, no server default, order), primary key
  parity, index parity, named CHECK parity — revision against ``Base.metadata``;
* the specification is *copied*: every column the gap names exists with the declared
  nullability, and nothing else is invented;
* the CHECK vocabulary cannot drift from the Python one: the source allow-list is rendered
  from ``rls.context.SERVER_RESOLUTION_SOURCES``, not retyped;
* the table is a **platform** anchor: outside ``CANONICAL_TABLE_POLICIES``, inside
  ``EXCLUDED_TABLES`` with a reviewed reason, and the revision installs no policy at all;
* the revision emits no grants, no data statement, no trigger, and its ``downgrade``
  removes its own two objects in reverse order;
* the model is registered in the ``models`` package exactly once.

Row-level behaviour (a reader refusing an expired/revoked/foreign row, the writer's
idempotency) lives in ``test_auth_session_store.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.schema import CreateTable

import prodavan.infrastructure.persistence.models as models
from prodavan.application.migration.principal_resolver import AUTH_SESSION_CORRELATION_GAP
from prodavan.infrastructure.persistence.models.auth_sessions import (
    AUTH_SESSIONS_ACCEPTANCE_CASES,
    AUTH_SESSIONS_SUBJECT_INDEX,
    AUTH_SESSIONS_TABLE,
    AUTH_SESSIONS_TABLES,
    RESOLUTION_SOURCE_CHECK,
    AuthSessionRow,
)
from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.rls import policies
from prodavan.infrastructure.persistence.rls.context import SERVER_RESOLUTION_SOURCES

from .acceptance_fixtures import (
    DOWN_REVISION_ID,
    FORBIDDEN_COLUMN_FRAGMENTS,
    POSTGRESQL,
    REVISION_FILE,
    REVISION_ID,
    SUBJECT_INDEX,
    SUCCESSOR_REVISION_ID,
    TABLE,
    TIMESTAMP_COLUMNS,
    RecordedOps,
    chain_from_head,
    check_catalog,
    column_signature,
    head_revisions,
    index_columns,
    load_revision_module,
    load_revision_module_from,
    revision_graph,
    type_label,
)

TABLE_OBJECT = Base.metadata.tables[TABLE]

#: DDL shapes a table-creation revision of a platform table must never emit.
FORBIDDEN_DDL = (
    "GRANT ",
    "REVOKE ",
    "INSERT INTO",
    "DELETE FROM",
    "TRUNCATE",
    "CREATE TRIGGER",
    "CREATE FUNCTION",
    "CREATE OR REPLACE FUNCTION",
    "CREATE ROLE",
    "SET ROLE",
    "DROP TABLE",
)


@pytest.fixture()
def revision(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, RecordedOps]:
    """Import the revision with a recording ``op`` proxy and run ``upgrade()``."""

    import alembic

    recorder = RecordedOps()
    monkeypatch.setattr(alembic, "op", recorder)
    module = load_revision_module()
    module.upgrade()
    return module, recorder


# ---------------------------------------------------------------------------
# 1. Chain shape
# ---------------------------------------------------------------------------


def test_revision_identifiers_follow_the_chain() -> None:
    module = load_revision_module()

    assert REVISION_FILE.is_file()
    assert module.revision == REVISION_ID
    assert module.down_revision == DOWN_REVISION_ID
    assert module.branch_labels is None
    assert module.depends_on is None
    assert module.AUTH_SESSIONS == TABLE


def test_the_revision_attaches_to_the_cutover_revision() -> None:
    graph = revision_graph()

    assert graph[REVISION_ID] == DOWN_REVISION_ID
    assert DOWN_REVISION_ID in graph, "the parent revision must exist"


def test_there_is_exactly_one_head_and_this_revision_is_on_its_chain() -> None:
    """A second head (or a fork) would make ``upgrade head`` ambiguous.

    Not "the head is this revision": a later revision (``2026110318`` today) legitimately
    extends the chain above ``2026110317`` and must not make this assertion fail for the
    wrong reason. What must hold is one head, and this revision on the path below it.
    """

    heads = head_revisions()
    assert len(heads) == 1, f"expected exactly one head, got {heads}"

    chain = chain_from_head()
    position = chain.index(REVISION_ID)
    # The three-revision window at this revision's position, not "everything below it":
    # the chain continues past ``2026110315`` and that tail is other revisions' business.
    assert chain[position : position + 3] == (REVISION_ID, DOWN_REVISION_ID, "2026110315")


def test_exactly_one_revision_declares_this_one_as_its_parent() -> None:
    """A *second* successor would fork the chain, so exactly one is required."""

    graph = revision_graph()
    children = sorted(revision for revision, down in graph.items() if down == REVISION_ID)

    assert children == [SUCCESSOR_REVISION_ID], (
        f"unexpected successors of {REVISION_ID}: {children}"
    )


def test_the_revision_can_be_loaded_by_path_like_alembic_does() -> None:
    module = load_revision_module_from(REVISION_FILE)

    assert callable(module.upgrade) and callable(module.downgrade)
    assert module.AUTH_SESSIONS == TABLE


# ---------------------------------------------------------------------------
# 2. Model <-> revision parity
# ---------------------------------------------------------------------------


def test_creates_only_the_auth_sessions_table(revision: tuple[Any, RecordedOps]) -> None:
    _, recorder = revision

    assert tuple(recorder.tables) == (TABLE,)
    assert {model.__tablename__ for model in (AuthSessionRow,)} == {TABLE}


def test_columns_match_metadata(revision: tuple[Any, RecordedOps]) -> None:
    _, recorder = revision

    recorded = recorder.tables[TABLE]
    assert recorded.schema is None
    assert column_signature(recorded) == column_signature(TABLE_OBJECT)
    # Column *order* is part of the signature, and no column carries a default.
    assert [row[3] for row in column_signature(recorded)] == [None] * len(TABLE_OBJECT.columns)


def test_primary_key_matches_metadata(revision: tuple[Any, RecordedOps]) -> None:
    _, recorder = revision

    recorded = tuple(column.name for column in recorder.tables[TABLE].primary_key.columns)
    expected = tuple(column.name for column in TABLE_OBJECT.primary_key.columns)

    assert recorded == expected == ("correlation_id",)


def test_check_constraints_match_metadata(revision: tuple[Any, RecordedOps]) -> None:
    _, recorder = revision

    recorded = check_catalog(recorder.tables[TABLE])
    expected = check_catalog(TABLE_OBJECT)

    assert recorded == expected
    assert all(recorded), "every check constraint is explicitly named"
    assert set(recorded) == {
        "ck_auth_sessions_correlation_nonblank",
        "ck_auth_sessions_subject_nonblank",
        "ck_auth_sessions_resolution_source",
        "ck_auth_sessions_expiry_after_issue",
        "ck_auth_sessions_revoked_after_issue",
    }


def test_indexes_match_metadata(revision: tuple[Any, RecordedOps]) -> None:
    _, recorder = revision

    recorded = {
        (call[2], tuple(call[3]))
        for call in recorder.calls
        if call[0] == "create_index" and call[1] == TABLE
    }
    expected = {
        (index.name, tuple(column.name for column in index.columns))
        for index in TABLE_OBJECT.indexes
    }

    assert recorded == expected == {(SUBJECT_INDEX, ("subject_claim", "expires_at"))}


def test_no_foreign_key_is_declared(revision: tuple[Any, RecordedOps]) -> None:
    """A verified subject may have no ``employees`` row yet; a FK would be a login outage."""

    _, recorder = revision

    assert [
        constraint
        for constraint in recorder.tables[TABLE].constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
    ] == []
    assert not any(call[0] == "create_foreign_key" for call in recorder.calls)
    assert list(TABLE_OBJECT.foreign_keys) == []


# ---------------------------------------------------------------------------
# 3. The specification is copied, not paraphrased
# ---------------------------------------------------------------------------


def test_the_specification_columns_are_the_model_columns() -> None:
    spec = AUTH_SESSION_CORRELATION_GAP
    assert spec.table == TABLE

    expected = [
        (
            name,
            # ``text primary key`` / ``text not null`` -> TEXT; ``timestamptz ...`` ->
            # TIMESTAMP WITH TIME ZONE. The specification's spelling is mapped, not trusted:
            # an unrecognised type string fails here instead of being waved through.
            "TIMESTAMP WITH TIME ZONE" if column_type.startswith("timestamptz") else "TEXT",
            "not null" not in column_type and "primary key" not in column_type,
            None,
        )
        for name, column_type, _meaning in spec.columns
    ]

    assert column_signature(TABLE_OBJECT) == expected
    for _name, column_type, meaning in spec.columns:
        assert column_type.strip(), "every column must state its type"
        assert meaning.strip(), "every column must state what the resolver relies on"


def test_the_specification_indexes_are_declared() -> None:
    spec = AUTH_SESSION_CORRELATION_GAP

    assert "primary key (correlation_id)" in spec.indexes
    assert any("subject_claim" in line and "expires_at" in line for line in spec.indexes)
    # …and both really exist.
    assert tuple(column.name for column in TABLE_OBJECT.primary_key.columns) == ("correlation_id",)
    assert ("subject_claim", "expires_at") in index_columns(TABLE_OBJECT)


def test_only_the_named_columns_exist() -> None:
    """Nothing is invented: the table has exactly the specification's columns."""

    specified = [name for name, _type, _meaning in AUTH_SESSION_CORRELATION_GAP.columns]
    assert specified == [column.name for column in TABLE_OBJECT.columns]
    assert set(specified) == {
        "correlation_id",
        "subject_claim",
        "resolution_source",
        "issued_at",
        "expires_at",
        "revoked_at",
        "company_id",
        "cabinet_id",
        "project_id",
    }


def test_the_source_allow_list_is_rendered_from_the_python_vocabulary() -> None:
    """The CHECK is not retyped: a narrowed allow-list must narrow the constraint too."""

    predicate = check_catalog(TABLE_OBJECT)["ck_auth_sessions_resolution_source"]

    assert predicate == RESOLUTION_SOURCE_CHECK
    for source in SERVER_RESOLUTION_SOURCES:
        assert f"'{source}'" in predicate
    # Request-derived sources are absent, exactly like the RLS vocabulary.
    for request_derived in ("request_claim", "header", "plugin", "job_payload"):
        assert request_derived not in predicate


def test_an_expiry_before_issuance_is_refused() -> None:
    """A row expiring before it was issued describes a credential dead when verified."""

    checks = check_catalog(TABLE_OBJECT)

    assert checks["ck_auth_sessions_expiry_after_issue"] == "expires_at > issued_at"
    assert checks["ck_auth_sessions_revoked_after_issue"] == (
        "revoked_at IS NULL OR revoked_at >= issued_at"
    )


def test_blank_identifiers_are_refused_by_the_schema() -> None:
    checks = check_catalog(TABLE_OBJECT)

    assert checks["ck_auth_sessions_correlation_nonblank"] == (
        "length(trim(correlation_id)) > 0"
    )
    assert checks["ck_auth_sessions_subject_nonblank"] == "length(trim(subject_claim)) > 0"


def test_the_models_render_valid_postgresql_ddl() -> None:
    rendered = str(CreateTable(TABLE_OBJECT).compile(dialect=POSTGRESQL))

    assert f"CREATE TABLE {TABLE}" in rendered
    assert "correlation_id TEXT NOT NULL" in rendered
    assert "PRIMARY KEY (correlation_id)" in rendered
    for column in TIMESTAMP_COLUMNS:
        assert column in rendered
    assert rendered.count("TIMESTAMP WITH TIME ZONE") == len(TIMESTAMP_COLUMNS)


def test_no_column_can_hold_credential_material() -> None:
    for column in TABLE_OBJECT.columns:
        assert type_label(column.type) in {"TEXT", "TIMESTAMP WITH TIME ZONE"}, column.name
        for fragment in FORBIDDEN_COLUMN_FRAGMENTS:
            assert fragment not in column.name, f"{TABLE}.{column.name} looks like material"


def test_the_model_records_its_acceptance_cases() -> None:
    assert set(AUTH_SESSIONS_TABLES) == {TABLE}
    assert len(set(AUTH_SESSIONS_ACCEPTANCE_CASES)) == len(AUTH_SESSIONS_ACCEPTANCE_CASES)
    for case in (
        "the_store_rows_are_exactly_the_correlation_gap_specification",
        "an_expired_row_resolves_to_none",
        "a_revoked_row_resolves_to_none",
        "the_table_is_outside_the_canonical_rls_set_and_says_why",
        "the_resolver_reads_the_store_without_a_tenant_guc",
    ):
        assert case in AUTH_SESSIONS_ACCEPTANCE_CASES


# ---------------------------------------------------------------------------
# 4. Platform table: outside the canonical RLS set, with a reviewed reason
# ---------------------------------------------------------------------------


def test_the_table_is_not_in_the_canonical_rls_registry(
    revision: tuple[Any, RecordedOps],
) -> None:
    """A tenant-scoped policy would deny the only legitimate read (no tenant yet)."""

    _, recorder = revision

    assert TABLE not in set(policies.registry_tables())
    owned = [spec for spec in policies.CANONICAL_TABLE_POLICIES if spec.table == TABLE]
    assert owned == []
    executed = [call[1] for call in recorder.calls if call[0] == "execute"]
    assert executed == [], "a platform table's revision installs no policy and runs no SQL"


def test_the_table_is_recorded_as_a_reviewed_exclusion() -> None:
    naming = [names for names, _reason in policies.EXCLUDED_TABLES if TABLE in names]

    assert naming == [(TABLE,)], "exactly one exclusion entry must name it"
    reason = next(reason for names, reason in policies.EXCLUDED_TABLES if TABLE in names)
    assert len(reason.split()) >= 8, "an exclusion needs a reviewed justification"
    assert "tenant" in reason.lower()
    assert TABLE in policies.excluded_table_names()


def test_the_coverage_report_neither_demands_nor_hides_a_policy() -> None:
    assert policies.uncovered_relations(Base.metadata.tables.keys()) == ()
    # The name intentionally matches no canonical prefix: the report cannot demand a policy
    # for it, and a rename into a canonical prefix would be caught by the check above.
    assert not TABLE.startswith(policies.CANONICAL_TABLE_PREFIXES)


def test_the_table_is_readable_without_a_tenant_guc() -> None:
    """The predicate that would have denied the read does not exist, and the schema has no
    ``tenant_id`` column for one to filter on — the reader is not tenant-bound."""

    assert "tenant_id" not in TABLE_OBJECT.c
    assert not any(
        "current_setting" in str(constraint.sqltext)
        for constraint in TABLE_OBJECT.constraints
        if isinstance(constraint, sa.CheckConstraint)
    )


# ---------------------------------------------------------------------------
# 5. Revision hygiene
# ---------------------------------------------------------------------------


def test_revision_emits_no_grants_data_or_unrelated_ddl(
    revision: tuple[Any, RecordedOps],
) -> None:
    _, recorder = revision

    assert {call[0] for call in recorder.calls} == {"create_table", "create_index"}
    for call in recorder.calls:
        if call[0] == "execute":
            upper = call[1].upper()
            for forbidden in FORBIDDEN_DDL:
                assert forbidden not in upper, f"unexpected DDL {forbidden!r}"

    rendered = str(CreateTable(recorder.tables[TABLE]).compile(dialect=POSTGRESQL)).upper()
    for forbidden in FORBIDDEN_DDL:
        assert forbidden not in rendered, forbidden
    assert "DEFAULT" not in rendered, "no column may date or revoke itself"


def test_downgrade_removes_only_its_own_objects_in_reverse_order(
    revision: tuple[Any, RecordedOps],
) -> None:
    module, recorder = revision
    upgrade_call_count = len(recorder.calls)

    module.downgrade()
    downgrade_calls = recorder.calls[upgrade_call_count:]

    assert [call[0] for call in downgrade_calls] == ["drop_index", "drop_table"]
    assert downgrade_calls[0][2] == SUBJECT_INDEX
    assert downgrade_calls[0][1] == TABLE
    assert downgrade_calls[1][1] == TABLE


def test_the_index_name_on_the_model_is_the_one_the_specification_implies() -> None:
    assert AUTH_SESSIONS_SUBJECT_INDEX == SUBJECT_INDEX
    assert {index.name for index in TABLE_OBJECT.indexes} == {SUBJECT_INDEX}


# ---------------------------------------------------------------------------
# 6. Registration
# ---------------------------------------------------------------------------


def test_the_model_is_registered_once_in_the_models_package() -> None:
    assert getattr(models, "AuthSessionRow") is AuthSessionRow
    assert "AuthSessionRow" in models.__all__
    assert Base.metadata.tables[TABLE] is AuthSessionRow.__table__

    owners = [
        mapper.class_ for mapper in Base.registry.mappers if mapper.local_table is AuthSessionRow.__table__
    ]
    assert owners == [AuthSessionRow]


def test_the_table_name_constant_is_the_registered_name() -> None:
    assert AUTH_SESSIONS_TABLE == TABLE == AuthSessionRow.__tablename__

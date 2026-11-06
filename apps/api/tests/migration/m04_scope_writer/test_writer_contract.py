"""MIG-M04-WRITER-01: static contract of the writer module.

These cases are source-level and pure. They pin the properties that cannot be observed from
a single row value:

* append-only: the module issues no ``UPDATE``/``DELETE`` and never imports ``update``/
  ``delete`` — an evidence row is written once and only read afterwards;
* the writer's immutable-field list is exactly the model's ``_APPEND_ONLY_FIELDS`` minus the
  database-stamped ``recorded_at``, so the writer and the ORM guard cannot drift;
* the stored ``outcome`` vocabulary is the model's check vocabulary;
* redaction: the derived envelope carries identifiers, digests, counts, gate names and
  reason codes only, and a payload-shaped key is refused rather than silently dropped;
* the row mapping only ever names declared columns, so a schema change breaks loudly;
* the single transaction shape: one ``write()`` boundary, tenant binding first, the pure
  builder before the session is opened.
"""

from __future__ import annotations

import ast
import inspect
import json

import pytest

from prodavan.application.migration.evidence_ports import (
    MigrationEvidenceBlocked,
    digest,
)
from prodavan.application.migration.m04_scope_evidence_writer import (
    M04_SCOPE_EVIDENCE_IMMUTABLE_FIELDS,
    M04_SCOPE_EVIDENCE_SCHEMA,
    SCOPE_EVIDENCE_OUTCOMES,
    SqlM04ScopeEvidenceWriter,
    _assert_redacted,
    _WriterBoundaries,
    m04_scope_evidence_revision_id,
    plan_scope_evidence_append,
    scope_evidence_row_values,
)
from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
    M04ScopePromotionEvidenceRow,
    _APPEND_ONLY_FIELDS,
    _PROMOTION_STATES,
)

from . import acceptance_fixtures as fixtures
from .acceptance_fixtures import PAYLOAD_MARKER, SECRET_MARKER

MODULE_NAME = "prodavan.application.migration.m04_scope_evidence_writer"


def _module_source() -> tuple[object, ast.Module]:
    import importlib

    module = importlib.import_module(MODULE_NAME)
    return module, ast.parse(inspect.getsource(module))


# ---------------------------------------------------------------------------
# Append-only: no UPDATE, no DELETE
# ---------------------------------------------------------------------------


def test_the_module_issues_no_update_or_delete_statement():
    _, tree = _module_source()
    forbidden_calls = {"update", "delete", "delete_row", "bulk_update"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            assert name not in forbidden_calls, f"the writer must not call {name}()"
        if isinstance(node, ast.ImportFrom):
            imported = {alias.name for alias in node.names}
            assert not imported & forbidden_calls, f"the writer must not import {imported}"


def test_the_module_carries_no_mutating_sql_literal():
    _, tree = _module_source()
    statements = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for fragment in ("UPDATE ", "DELETE FROM", "TRUNCATE ", "DROP "):
        offending = [value for value in statements if fragment in value]
        assert offending == [], f"{fragment!r} in string constants: {offending}"


def test_the_only_write_side_orm_call_is_add():
    source = inspect.getsource(SqlM04ScopeEvidenceWriter.append)
    assert source.count("session.add(") == 1
    assert "M04ScopePromotionEvidenceRow(**values)" in source


def test_the_append_opens_exactly_one_transaction():
    source = inspect.getsource(SqlM04ScopeEvidenceWriter.append)
    assert source.count("self._boundaries.write()") == 1
    boundaries = inspect.getsource(_WriterBoundaries.write)
    assert "session.begin()" in boundaries
    assert "bind_evidence_transaction" in boundaries


def test_the_transaction_binds_the_tenant_before_any_statement():
    source = inspect.getsource(_WriterBoundaries.write)
    assert source.index("bind_evidence_transaction") < source.index("yield session")


def test_the_pure_builder_runs_before_the_session_is_opened():
    source = inspect.getsource(SqlM04ScopeEvidenceWriter.append)
    assert source.index("scope_evidence_row_values") < source.index("self._boundaries.write()")


# ---------------------------------------------------------------------------
# Immutable-field parity with the model
# ---------------------------------------------------------------------------


def test_the_immutable_field_list_is_the_model_guard_minus_recorded_at():
    assert set(M04_SCOPE_EVIDENCE_IMMUTABLE_FIELDS) == set(_APPEND_ONLY_FIELDS) - {"recorded_at"}
    assert len(M04_SCOPE_EVIDENCE_IMMUTABLE_FIELDS) == len(set(M04_SCOPE_EVIDENCE_IMMUTABLE_FIELDS))


def test_the_stored_outcome_vocabulary_is_the_model_check_vocabulary():
    declared = {
        word.strip().strip("'\"")
        for word in _PROMOTION_STATES.strip("()").split(",")
    }
    assert declared == set(SCOPE_EVIDENCE_OUTCOMES)
    assert fixtures.scope_row_values_for("tenant")["outcome"] in declared


# ---------------------------------------------------------------------------
# Row mapping only names declared columns
# ---------------------------------------------------------------------------


def test_every_produced_column_is_declared_by_the_model():
    declared = {column.name for column in M04ScopePromotionEvidenceRow.__table__.columns}
    values = fixtures.scope_row_values_for("tenant")
    assert set(values) <= declared
    # Every NOT NULL column without a server default must be present.
    required = {
        column.name
        for column in M04ScopePromotionEvidenceRow.__table__.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }
    assert required <= set(values), required - set(values)


def test_the_row_never_enables_canonical_positive_writes():
    values = fixtures.scope_row_values_for("tenant")
    assert values["canonical_positive_writes"] is False
    source = inspect.getsource(scope_evidence_row_values)
    assert '"canonical_positive_writes": False' in source


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_the_envelope_carries_the_writer_schema_and_no_payload_material():
    values = fixtures.scope_row_values_for("tenant")
    envelope = values["gate_evidence"]
    assert envelope["schema"] == M04_SCOPE_EVIDENCE_SCHEMA
    serialized = json.dumps(envelope, sort_keys=True)
    for marker in (SECRET_MARKER, PAYLOAD_MARKER):
        assert marker not in serialized
    for key in _envelope_keys(envelope):
        lowered = key.lower()
        for forbidden in ("payload", "manifest", "token", "secret", "password", "body"):
            assert forbidden not in lowered, key


def test_the_envelope_digest_column_is_the_digest_of_the_stored_envelope():
    values = fixtures.scope_row_values_for("tenant")
    assert values["gate_evidence_digest"] == digest(values["gate_evidence"])


def test_a_payload_shaped_key_is_refused_rather_than_dropped():
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        _assert_redacted({"source_payload": {"raw": "x"}}, where="gate_evidence")
    assert raised.value.blockers[0].startswith(
        "redaction_violation:m04_scope_evidence:gate_evidence:"
    )


def test_a_payload_shaped_key_is_refused_at_any_depth():
    """The envelope is nested; a guard that only walked the top level would be a hole."""
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        _assert_redacted(
            {"checkpoint": {"manifest": "raw-manifest-body"}}, where="gate_evidence"
        )
    assert raised.value.blockers == (
        "redaction_violation:m04_scope_evidence:gate_evidence:checkpoint:manifest",
    )
    with pytest.raises(MigrationEvidenceBlocked):
        _assert_redacted(
            {"gates": [{"name": "x", "secret_material": "y"}]}, where="gate_evidence"
        )


def test_the_redaction_guard_accepts_the_evidence_vocabulary():
    _assert_redacted(
        {
            "schema": M04_SCOPE_EVIDENCE_SCHEMA,
            "reason_code": "active_typed_scope",
            "policy_names": ["scope_registry_tenant_all"],
            "digest": digest({"a": 1}),
            "counts": {"tenant_matches": 1},
        },
        where="gate_evidence",
    )


def _envelope_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys |= _envelope_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            keys |= _envelope_keys(nested)
    return keys


# ---------------------------------------------------------------------------
# Pure append decision
# ---------------------------------------------------------------------------


def test_the_append_plan_is_insert_for_an_absent_row():
    plan = plan_scope_evidence_append(
        stored=None, values=fixtures.scope_row_values_for("tenant")
    )
    assert plan.is_insert and plan.blockers == ()


def test_the_append_plan_is_replay_for_identical_immutable_content():
    values = fixtures.scope_row_values_for("tenant")
    stored = fixtures.stored_row(**{key: values[key] for key in M04_SCOPE_EVIDENCE_IMMUTABLE_FIELDS})
    plan = plan_scope_evidence_append(stored=stored, values=values)
    assert plan.is_replay
    assert plan.stored is stored


def test_the_append_plan_is_a_conflict_when_any_immutable_field_differs():
    values = fixtures.scope_row_values_for("tenant")
    stored = fixtures.stored_row(**{
        key: values[key] for key in M04_SCOPE_EVIDENCE_IMMUTABLE_FIELDS
    })
    stored.canonical_digest = "sha256:" + "0" * 64
    plan = plan_scope_evidence_append(stored=stored, values=values)
    assert plan.is_conflict
    assert "m04_scope_evidence_revision_content_conflict:canonical_digest" in plan.blockers


def test_the_revision_identity_is_deterministic_and_partition_bound():
    values = fixtures.scope_row_values_for("tenant")
    first = m04_scope_evidence_revision_id(values=values)
    assert first == m04_scope_evidence_revision_id(values=dict(values))
    assert len(first) == len("sha256:") + 64
    other = dict(values)
    other["tenant_id"] = "tenant-m04-writer-other"
    assert m04_scope_evidence_revision_id(values=other) != first


def test_the_row_id_fits_its_column():
    values = fixtures.scope_row_values_for("project")
    assert len(values["id"]) <= 80
    assert len(values["revision_id"]) <= 80
    assert values["id"].startswith("m04scope:")

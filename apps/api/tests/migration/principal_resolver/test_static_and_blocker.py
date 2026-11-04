"""Static obligations of the resolver module, plus the recorded blocker it leaves open.

Three things cannot be proven by calling the resolver:

* that it never *constructs* a principal directly (the seal is the whole point) — proven on the
  module's AST;
* that it never caches a correlation -> subject mapping in process memory, and never writes to
  the identity tables — proven on the module's text/AST;
* that the recorded schema gap is real, i.e. the table it names genuinely does not exist —
  proven against the registered models and the Alembic revision files.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from prodavan.application.migration import principal_resolver as module
from prodavan.application.migration.principal_resolver import (
    AUTH_SESSION_CORRELATION_GAP,
    PRINCIPAL_RESOLVER_BLOCKER,
    PrincipalRefusal,
    SchemaGap,
)
from prodavan.infrastructure.persistence.models.base import Base

SOURCE_PATH = Path(module.__file__)
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

API_ROOT = Path(__file__).resolve().parents[3] / "src" / "prodavan"
ALEMBIC_VERSIONS = Path(__file__).resolve().parents[3] / "alembic" / "versions"


def calls_named(name: str) -> list[ast.Call]:
    found: list[ast.Call] = []
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name) and target.id == name:
            found.append(node)
        elif isinstance(target, ast.Attribute) and target.attr == name:
            found.append(node)
    return found


# ---------------------------------------------------------------------------
# The seal
# ---------------------------------------------------------------------------


def test_the_principal_is_never_constructed_directly() -> None:
    """``TrustedPrincipal(...)`` and ``ServerResolution(...)`` must not appear as calls.

    The only sanctioned path is ``issue_server_resolution`` -> ``from_resolution``; both are
    asserted present so the test cannot pass by deleting the construction entirely.
    """

    assert calls_named("TrustedPrincipal") == [], "the principal must be sealed by from_resolution"
    assert calls_named("ServerResolution") == [], "the resolution must be issued, not built"
    assert calls_named("issue_server_resolution"), "issue_server_resolution must be used"
    assert calls_named("from_resolution"), "TrustedPrincipal.from_resolution must be used"


def test_the_module_imports_the_rls_seal_from_its_owning_module() -> None:
    imported = {
        alias.name
        for node in TREE.body
        if isinstance(node, ast.ImportFrom) and node.module == "prodavan.infrastructure.persistence.rls.context"
        for alias in node.names
    }
    assert {"TrustedPrincipal", "issue_server_resolution"} <= imported


# ---------------------------------------------------------------------------
# No process-local authority, no writes
# ---------------------------------------------------------------------------


def test_nothing_is_cached_in_module_state() -> None:
    """No decorator or global accumulator may remember a correlation -> subject answer."""

    assert "lru_cache" not in SOURCE
    assert "functools.cache" not in SOURCE
    assert not [node for node in ast.walk(TREE) if isinstance(node, ast.Global)]


def test_the_resolver_never_writes_identity_state() -> None:
    """Resolution is a read. Activating an invite or bumping a row is the auth boundary's job."""

    for forbidden in ("session.add(", "commit(", "flush(", "insert(", "update(", "delete("):
        assert forbidden not in SOURCE, f"the resolver must not write: {forbidden}"


def test_the_resolver_stays_out_of_the_web_layer() -> None:
    """It has to be constructible from a worker/CLI as well as from a request."""

    for forbidden in ("import fastapi", "from fastapi", "from starlette", "import starlette"):
        assert forbidden not in SOURCE


def test_every_sql_statement_is_a_select() -> None:
    selects = SOURCE.count("select(")
    assert selects >= 6, "each directory read must be an explicit select()"
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"insert", "update", "delete"}, "only selects are allowed"


# ---------------------------------------------------------------------------
# Refusal vocabulary
# ---------------------------------------------------------------------------


def test_refusal_codes_are_named_reason_codes() -> None:
    codes = [member.value for member in PrincipalRefusal]
    assert len(codes) == len(set(codes)), "reason codes must be unique"
    for code in codes:
        assert re.fullmatch(r"principal_[a-z_]+", code), code


def test_the_refusal_vocabulary_covers_the_contract_negatives() -> None:
    """Unknown, revoked and expired each have their own code — they are not one bucket."""

    assert PrincipalRefusal.CORRELATION_UNKNOWN.value == "principal_correlation_unknown"
    assert "expired" in PrincipalRefusal.CREDENTIAL_EXPIRED.value
    assert "revoked" in PrincipalRefusal.MEMBERSHIP_REVOKED.value
    # A revoked cabinet assignment is reported as "not granted": the grant a tenant holds and
    # an employee's own access are different facts, and only the latter was revoked.
    assert "granted" in PrincipalRefusal.CABINET_NOT_GRANTED.value
    assert "inactive" in PrincipalRefusal.CABINET_INACTIVE.value
    assert "ambiguous" in PrincipalRefusal.TENANT_AMBIGUOUS.value


# ---------------------------------------------------------------------------
# The recorded blocker
# ---------------------------------------------------------------------------


def test_the_recorded_gap_names_a_table_and_its_contract() -> None:
    gap = AUTH_SESSION_CORRELATION_GAP
    assert isinstance(gap, SchemaGap)
    assert gap.table == "auth_sessions"
    columns = {name for name, _type, _meaning in gap.columns}
    assert {
        "correlation_id",
        "subject_claim",
        "resolution_source",
        "issued_at",
        "expires_at",
        "revoked_at",
    } <= columns
    for _name, column_type, meaning in gap.columns:
        assert column_type.strip(), "every column must state its type"
        assert meaning.strip(), "every column must state what the resolver relies on"
    assert gap.indexes, "the lookup needs its keys described"
    assert any("revoked_at" in line for line in gap.semantics), (
        "the blocker must state that revocation denies immediately"
    )


def test_the_blocker_is_reported_for_the_composition_root() -> None:
    assert "auth_sessions" in PRINCIPAL_RESOLVER_BLOCKER
    assert "None" in PRINCIPAL_RESOLVER_BLOCKER


def test_the_named_table_still_does_not_exist() -> None:
    """The gap is honest: no registered model declares it, and no revision creates it.

    When someone adds ``auth_sessions`` this test fails on purpose — the blocker must be
    lifted (and the resolver switched to it) in the same change, not left to rot.
    """

    assert "auth_sessions" not in Base.metadata.tables
    revisions = sorted(ALEMBIC_VERSIONS.glob("*.py"))
    assert revisions, "the Alembic revision directory must be readable"
    creating = [
        path.name
        for path in revisions
        if re.search(r"""create_table\(\s*["']auth_sessions["']""", path.read_text(encoding="utf-8"))
    ]
    assert creating == [], f"auth_sessions is created by {creating}; lift the blocker"


def test_a_stateless_credential_store_is_documented_as_the_current_path() -> None:
    """The module docstring must say *why* the credential path is per-request, not durable."""

    docstring = ast.get_docstring(TREE) or ""
    assert "auth_sessions" in docstring
    assert "no such table" in docstring.lower() or "absent" in docstring.lower()


# ---------------------------------------------------------------------------
# Layer boundary
# ---------------------------------------------------------------------------


def test_the_module_lives_on_the_auth_boundary_side_of_the_application_layer() -> None:
    assert SOURCE_PATH.parent == API_ROOT / "application" / "migration"


def test_the_protocol_is_matched_structurally_not_by_importing_the_contract() -> None:
    """``TrustedPrincipalResolver`` is a ``runtime_checkable`` Protocol.

    ``test_resolver.py`` proves the conformance at runtime (``isinstance``); the module itself
    needs no import of the contract, and deliberately has none — the dependency between the
    two modules must not be bidirectional, or the contract module could start depending on an
    auth-boundary implementation.
    """

    imported_modules = {
        node.module for node in TREE.body if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "prodavan.application.migration.tenant_context" not in imported_modules


def test_the_contract_module_does_not_import_the_implementation() -> None:
    """The dependency points one way: tenant_context declares the Protocol, this implements it."""

    contract = (API_ROOT / "application" / "migration" / "tenant_context.py").read_text(encoding="utf-8")
    assert "from prodavan.application.migration.principal_resolver import" not in contract


@pytest.mark.parametrize("forbidden", ["Base.metadata", "op.create_table", "alembic", "CREATE TABLE"])
def test_no_ddl_is_invented_in_the_module(forbidden: str) -> None:
    assert forbidden not in SOURCE

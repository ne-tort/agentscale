"""Static and structural acceptance for the tenant-context port implementations.

Nothing is executed by the migration verification freeze; these tests are meant to be
run later and to fail if any of the following invariants is broken:

* every statement that writes a GUC is textually ``set_config(..., true)`` — there is no
  bare ``SET``, no ``SET LOCAL`` and no interpolated value anywhere in the module;
* the static guard runs at import time, so an edited statement cannot slip through, and
  the guard is not vacuous (it rejects a non-local or batched statement);
* both adapters keep the exact public shapes of the two declared ports (no contract was
  changed to accommodate the implementation);
* the module stays free of logging/printing, so no secret or raw payload can be logged
  from this path.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from prodavan.application.migration import tenant_context as module
from prodavan.application.migration.m04_scope.scope_persistence import TenantScopeContextPort
from prodavan.application.migration.m05_instance_runtime.instance_persistence import (
    TrustedTenantContextPort,
)
from prodavan.application.migration.tenant_context import (
    M04_SCOPE_RELATIONS,
    M05_RUNTIME_RELATIONS,
    SECURITY_REVIEW_ID,
    RlsTenantScopeContextAdapter,
    RlsTrustedTenantContextAdapter,
    TrustedPrincipalResolver,
)
from prodavan.infrastructure.persistence.rls import context as rls_context
from prodavan.infrastructure.persistence.rls.policies import REGISTRY_VERSION, registry_tables

MODULE_SOURCE = Path(module.__file__).read_text(encoding="utf-8")
MODULE_TREE = ast.parse(MODULE_SOURCE)

#: Names of the transaction-owning helpers of ``rls.context``.  They open their own unit
#: of work, so a port that binds inside the caller's transaction must not use them.
WRAPPER_HELPERS = (
    "trusted_tenant_transaction",
    "trusted_tenant_transaction_sync",
    "trusted_service_transaction",
    "trusted_tenant_context",
    "trusted_tenant_context_sync",
)


def _string_constants() -> list[str]:
    return [
        node.value
        for node in ast.walk(MODULE_TREE)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _referenced_names() -> set[str]:
    """Every identifier the module actually references (not prose in a docstring)."""

    names: set[str] = set()
    for node in ast.walk(MODULE_TREE):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit(".", 1)[-1])
    return names


def _parameter_names(callable_object: object) -> tuple[str, ...]:
    return tuple(inspect.signature(callable_object).parameters)


def _parameter_kinds(callable_object: object) -> tuple[object, ...]:
    return tuple(
        parameter.kind for parameter in inspect.signature(callable_object).parameters.values()
    )


# ---------------------------------------------------------------------------
# "No SET without LOCAL" — static text proof
# ---------------------------------------------------------------------------


def test_no_bare_set_and_no_set_local_in_any_statement() -> None:
    for value in _string_constants():
        assert not re.search(r"\bSET\s+(?:LOCAL\s+)?app\.", value, re.IGNORECASE), value
        assert not value.strip().upper().startswith("SET "), value


def test_every_guc_write_is_transaction_local() -> None:
    # ``set_config('`` selects the statement literals (a docstring or the guard's own
    # fragment ``set_config(`` does not carry a quoted setting name).
    writes = [value for value in _string_constants() if "set_config('" in value]
    assert writes, "the module must contain the statements that write the settings"
    for value in writes:
        assert value.count("set_config('") == value.count(", true)"), value


def test_no_guc_write_is_built_by_interpolation() -> None:
    for node in ast.walk(MODULE_TREE):
        if isinstance(node, ast.JoinedStr):
            literal = "".join(
                str(part.value) for part in node.values if isinstance(part, ast.Constant)
            )
            assert "set_config" not in literal
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            assert "set_config" not in ast.dump(node)


def test_transaction_local_guard_runs_at_import_time() -> None:
    called = [
        node
        for node in MODULE_TREE.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_raise_if_not_transaction_local"
    ]
    assert called, "the static guard must be invoked at module import"
    module._raise_if_not_transaction_local()  # must not raise for the shipped statements


def test_guard_rejects_a_session_level_variant() -> None:
    """The guard is not vacuous: a non-local, bare or batched statement fails."""

    for statement in module._WRITE_STATEMENTS:
        assert "set_config(" in statement
        assert statement.count("set_config(") == statement.count(", true)")

    rejected = (
        "SELECT set_config('app.tenant_id', :tenant_id, false)",
        "SET app.tenant_id = :tenant_id",
        "SELECT set_config('app.tenant_id', :tenant_id, true); DROP TABLE scope_registry",
    )
    original = module._WRITE_STATEMENTS
    try:
        for statement in rejected:
            module._WRITE_STATEMENTS = (statement,)
            raised = False
            try:
                module._raise_if_not_transaction_local()
            except rls_context.RlsContextError as error:
                raised = error.reason_code == "transaction_local_set_forbidden"
            assert raised, statement
    finally:
        module._WRITE_STATEMENTS = original


# ---------------------------------------------------------------------------
# Contracts unchanged
# ---------------------------------------------------------------------------


def test_bind_signatures_match_the_declared_ports() -> None:
    assert _parameter_names(RlsTenantScopeContextAdapter.bind) == _parameter_names(
        TenantScopeContextPort.bind
    )
    assert _parameter_kinds(RlsTenantScopeContextAdapter.bind) == _parameter_kinds(
        TenantScopeContextPort.bind
    )
    assert _parameter_names(RlsTrustedTenantContextAdapter.bind) == _parameter_names(
        TrustedTenantContextPort.bind
    )
    assert _parameter_kinds(RlsTrustedTenantContextAdapter.bind) == _parameter_kinds(
        TrustedTenantContextPort.bind
    )


def test_narrow_signature_matches_the_declared_port() -> None:
    assert _parameter_names(RlsTenantScopeContextAdapter.narrow) == _parameter_names(
        TenantScopeContextPort.narrow
    )
    assert _parameter_kinds(RlsTenantScopeContextAdapter.narrow) == _parameter_kinds(
        TenantScopeContextPort.narrow
    )


def test_ports_declare_no_clear_method_so_it_stays_additive() -> None:
    # ``clear`` is an addition on this side only: neither contract was widened.
    assert not hasattr(TenantScopeContextPort, "clear")
    assert not hasattr(TrustedTenantContextPort, "clear")
    assert callable(RlsTenantScopeContextAdapter.clear)
    assert callable(RlsTrustedTenantContextAdapter.clear)


def test_resolver_protocol_is_async_and_correlation_scoped() -> None:
    assert _parameter_names(TrustedPrincipalResolver.resolve) == ("self", "correlation_id")
    assert inspect.iscoroutinefunction(TrustedPrincipalResolver.resolve)


def test_adapters_never_use_the_transaction_owning_wrappers() -> None:
    """The documented adapter decision, enforced statically.

    ``rls.context`` also offers factory-based helpers that open and commit their own
    transaction.  Both ports bind *inside* a transaction the caller owns, so using those
    helpers would silently create a second unit of work.
    """

    referenced = _referenced_names()
    for helper in WRAPPER_HELPERS:
        assert helper not in referenced, helper


# ---------------------------------------------------------------------------
# Registry and documentation facts
# ---------------------------------------------------------------------------


def test_review_id_tracks_the_canonical_registry_version() -> None:
    assert SECURITY_REVIEW_ID == f"rls-registry:{REGISTRY_VERSION}"


def test_configured_relations_are_reviewed_canonical_relations() -> None:
    reviewed = set(registry_tables())
    assert set(M04_SCOPE_RELATIONS) <= reviewed
    assert set(M05_RUNTIME_RELATIONS) <= reviewed
    assert "scope_registry" in M04_SCOPE_RELATIONS
    assert "scope_registry" in M05_RUNTIME_RELATIONS


def test_module_logs_nothing() -> None:
    referenced = _referenced_names()
    for name in ("logging", "getLogger", "logger", "basicConfig", "print"):
        assert name not in referenced, name
    assert "import logging" not in MODULE_SOURCE


def test_module_is_a_production_adapter_not_a_stub() -> None:
    referenced = _referenced_names()
    for name in ("InMemory", "Fake", "Stub", "noop"):
        assert name not in referenced, name
    assert "NotImplementedError" not in referenced

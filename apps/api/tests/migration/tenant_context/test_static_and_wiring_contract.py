"""Static and structural acceptance for the tenant-context port implementations.

These checks are executed in the normal test run and fail if any of the following
invariants is broken:

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
# "No SET without LOCAL" — static proof over the statements the module *executes*
#
# The scan must never read the raw file text.  This module legitimately contains the
# fragment ``set_config('`` outside of any statement: the docstrings, and the f-string in
# ``_raise_if_settings_drifted`` that checks a literal SQL string for a setting name.  A
# text scan counts those as statements and reports the module's own guard as a violation,
# which is why the previous version failed.  The check instead parses the module, visits
# only the expressions that are handed to a statement-executing call, resolves each to the
# literals it can evaluate to, and treats an expression it cannot resolve (an f-string or a
# ``%`` format) as a violation in its own right.
# ---------------------------------------------------------------------------

#: Functions that hand a statement to the driver.  Every SQL literal of this module reaches
#: PostgreSQL through one of them, so their call sites — not every string in the file — are
#: the authoritative thing to inspect.
EXECUTOR_FUNCTIONS = ("_one", "_maybe_one")
#: The session method that finally executes a statement (used by the executors themselves).
EXECUTOR_METHODS = ("execute",)
#: A session-level assignment of a tenant GUC; the statement must never be emitted.
_BARE_SET = re.compile(r"\bSET\s+(?:LOCAL\s+)?app\.", re.IGNORECASE)


def _assigned_expressions(statements: list[ast.stmt]) -> dict[str, list[ast.AST]]:
    """``name -> every expression assigned to it`` for the given statements."""

    bindings: dict[str, list[ast.AST]] = {}
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, ast.Assign):
                targets: list[ast.expr] = list(node.targets)
                value: ast.expr = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
                value = node.value
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(value)
    return bindings


def _module_bindings(tree: ast.Module) -> dict[str, list[ast.AST]]:
    """Bindings of the module body only (a function local is not a module constant)."""

    bindings: dict[str, list[ast.AST]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                bindings.setdefault(node.target.id, []).append(node.value)
    return bindings


def _resolved_statements(
    node: ast.AST,
    bindings: dict[str, list[ast.AST]],
    seen: frozenset[str] = frozenset(),
) -> tuple[list[str], bool]:
    """Resolve a statement expression to the literals it can be; ``True`` = built at runtime."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value], False
    if isinstance(node, (ast.JoinedStr, ast.BinOp)):
        # An f-string or a ``%``-format: the executed text is not statically known, so the
        # statement cannot be proven transaction-local by reading the source.
        return [], True
    if isinstance(node, ast.IfExp):
        body, body_dynamic = _resolved_statements(node.body, bindings, seen)
        other, other_dynamic = _resolved_statements(node.orelse, bindings, seen)
        return body + other, body_dynamic or other_dynamic
    if isinstance(node, ast.Name):
        candidates = bindings.get(node.id)
        if not candidates or node.id in seen:
            return [], True
        values: list[str] = []
        dynamic = False
        for candidate in candidates:
            resolved, candidate_dynamic = _resolved_statements(
                candidate, bindings, seen | {node.id}
            )
            values.extend(resolved)
            dynamic = dynamic or candidate_dynamic
        return values, dynamic
    return [], True


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    return getattr(node.func, "attr", None)


def _executed_statements(tree: ast.Module) -> tuple[list[str], list[ast.AST]]:
    """Every statement the module hands to an executor, as literals, plus dynamic ones.

    Functions are visited *everywhere* in the tree, not only at module level: most of the
    production statements are executed from methods (``_apply_tenant_context``,
    ``_restore_previous``, ``_narrow``), so a module-body-only walk would silently skip
    them and the check would pass for the wrong reason.
    """

    module_bindings = _module_bindings(tree)
    values: list[str] = []
    dynamic: list[ast.AST] = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if function.name in EXECUTOR_FUNCTIONS:
            # These *are* the executors: their ``statement`` argument is a parameter whose
            # value comes from the call sites inspected here, so their own body carries no
            # statement literal to resolve.
            continue
        bindings = {**module_bindings, **_assigned_expressions(function.body)}
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name in EXECUTOR_FUNCTIONS:
                index = 1  # (session, statement, parameters)
            elif name in EXECUTOR_METHODS:
                index = 0  # session.execute(statement, parameters)
            else:
                continue
            if len(node.args) <= index:
                continue
            resolved, is_dynamic = _resolved_statements(node.args[index], bindings)
            values.extend(resolved)
            if is_dynamic:
                dynamic.append(node.args[index])
    return values, dynamic


def _transaction_local_violations(
    statements: list[str], dynamic: list[ast.AST]
) -> list[str]:
    """Everything that could let a setting outlive its transaction, in one place."""

    problems = [f"statement built at runtime: {ast.dump(node)}" for node in dynamic]
    for value in statements:
        if value.strip().upper().startswith("SET ") or _BARE_SET.search(value):
            problems.append(f"session-level SET: {value}")
        if "set_config('" in value and value.count("set_config('") != value.count(", true)"):
            problems.append(f"set_config is not transaction-local: {value}")
    return problems


def test_no_bare_set_and_no_set_local_in_any_statement() -> None:
    for value in _string_constants():
        assert not re.search(r"\bSET\s+(?:LOCAL\s+)?app\.", value, re.IGNORECASE), value
        assert not value.strip().upper().startswith("SET "), value


def test_every_guc_write_is_transaction_local() -> None:
    statements, dynamic = _executed_statements(MODULE_TREE)

    assert statements, "the module must hand literal SQL statements to the driver"
    writes = [value for value in statements if "set_config('" in value]
    assert writes, "the module must execute the statements that write the settings"
    assert _transaction_local_violations(statements, dynamic) == []

    # Stronger than counting occurrences: the import-time guard inspects exactly
    # ``_WRITE_STATEMENTS``, so an executed write that is missing from that tuple would be
    # emitted without ever passing the guard.  Every executed write must be covered.
    assert set(writes) == set(module._WRITE_STATEMENTS)


def test_no_guc_write_is_built_by_interpolation() -> None:
    statements, dynamic = _executed_statements(MODULE_TREE)
    assert statements, "the scan must find the executed statements, not an empty set"
    assert dynamic == [], [ast.dump(node) for node in dynamic]


def test_every_sql_constant_is_reached_by_the_scan() -> None:
    """No statement literal may escape inspection (the scan must not be blind anywhere).

    Every ``_*_SQL`` constant of the module is executed through an executor call site, so
    all of them must be found by :func:`_executed_statements`.  A statement that is
    invoiced but not reached means the scan has a blind spot — and an unchecked statement
    is exactly the one that could smuggle in a non-local ``set_config``.
    """

    statements, _ = _executed_statements(MODULE_TREE)
    sql_constants: dict[str, list[str]] = {}
    for name, expression in _module_bindings(MODULE_TREE).items():
        if not name.endswith("_SQL"):
            continue
        for candidate in expression:
            resolved, _dynamic = _resolved_statements(candidate, {})
            if resolved:
                sql_constants[name] = resolved
    assert sql_constants, "the module must define its statements as module constants"
    missing = {
        name
        for name, values in sql_constants.items()
        if not set(values) <= set(statements)
    }
    assert missing == set(), f"statements never reached by the scan: {sorted(missing)}"
    # The tenant/trust reads and the two executors are covered by the same walk.
    assert any("pg_roles" in value for value in statements)
    assert any("txid_current" in value for value in statements)
    assert any("pg_policies" in value for value in statements)


def test_the_scan_really_catches_a_violation() -> None:
    """The scan is not vacuous: a module that breaks the invariant is reported.

    All three detectors are exercised against a synthetic module that does reach the
    driver with a session-level ``SET``, a non-local ``set_config`` and an interpolated
    statement.  Without this the scan could be green simply because it found nothing.
    """

    violating = ast.parse(
        "\n".join(
            (
                "from sqlalchemy import text",
                "",
                '_NON_LOCAL_SQL = "SELECT set_config(\'app.tenant_id\', :tenant_id, false)"',
                '_BARE_SET_SQL = "SET app.tenant_id = \'comp_aaaaaaaaaaaaaaaaaaaa\'"',
                "",
                "async def _apply(session, value):",
                "    await _one(session, _NON_LOCAL_SQL)",
                "    await _one(session, _BARE_SET_SQL)",
                (
                    "    await session.execute("
                    "text(f\"SELECT set_config('app.role', '{value}', true)\"))"
                ),
            )
        )
    )

    statements, dynamic = _executed_statements(violating)
    problems = _transaction_local_violations(statements, dynamic)

    assert any("session-level SET" in problem for problem in problems), problems
    assert any("not transaction-local" in problem for problem in problems), problems
    assert any("built at runtime" in problem for problem in problems), problems
    # The literals themselves are reached through the executor call sites.
    assert any(statement.startswith("SELECT set_config(") for statement in statements)


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

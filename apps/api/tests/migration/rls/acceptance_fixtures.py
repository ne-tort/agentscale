"""Acceptance fixtures for the RLS revision and trusted tenant context.

Nothing here is executed during the verification freeze.  The fixtures provide:

* :class:`FakePostgres` - an in-memory stand-in that reproduces the only
  PostgreSQL semantics the module relies on (transaction-local GUCs,
  ``current_setting(name, true)`` returning ``NULL`` when unset, catalog role
  flags, ``row_security``);
* :class:`FakeAsyncSession` / :class:`FakeSyncSession` - minimal session shapes
  for the async and sync entry points, recording every statement so the negative
  cases can prove what was (not) emitted;
* :func:`scope_visible` - an independent evaluation of the registry predicates
  over representative rows, used to demonstrate the allow/deny matrix without a
  live database;
* :func:`build_principal` - the documented construction path for tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from prodavan.infrastructure.persistence.rls.context import (
    CallerAssertion,
    TrustedPrincipal,
    issue_server_resolution,
)
from prodavan.infrastructure.persistence.rls.policies import (
    CANONICAL_TABLE_POLICIES,
    ScopedColumn,
    TablePolicySpec,
)

TENANT_A = "comp_aaaaaaaaaaaaaaaaaaaa"
TENANT_B = "comp_bbbbbbbbbbbbbbbbbbbb"
CABINET_A = "cab_aaaaaaaaaaaaaaaaaaaa"
CABINET_B = "cab_bbbbbbbbbbbbbbbbbbbb"
PROJECT_A = "prj_aaaaaaaaaaaaaaaaaaaa"
PROJECT_B = "prj_bbbbbbbbbbbbbbbbbbbb"
INSTANCE_A = "ains_aaaaaaaaaaaaaaaaaaaa"
SUBJECT_A = "emp_aaaaaaaaaaaaaaaaaaaa"

SETTING_OF_PARAMETER = {
    "tenant_id": "app.tenant_id",
    "cabinet_id": "app.cabinet_id",
    "project_id": "app.project_id",
    "user_id": "app.user_id",
    "role": "app.role",
}

#: Tables whose tenant policy additionally requires ``owner_state='active'``.
#: Kept next to the registry so a restriction cannot be added to the SQL without
#: a matching evaluator rule.
RESTRICTIONS = {
    "scope_registry": lambda row: row.get("owner_state") == "active",
}


# ---------------------------------------------------------------------------
# Principal construction (documented path only)
# ---------------------------------------------------------------------------


def build_principal(
    *,
    tenant_id: str = TENANT_A,
    cabinet_id: str | None = CABINET_A,
    project_id: str | None = None,
    subject_id: str = SUBJECT_A,
    role: str = "tenant_admin",
    resolution_source: str = "session_cookie",
    resolved_by: str = "auth.session_middleware",
    correlation_id: str = "corr_aaaaaaaaaaaaaaaaaaaa",
    ttl: timedelta = timedelta(minutes=15),
) -> TrustedPrincipal:
    resolution = issue_server_resolution(
        resolved_by=resolved_by,
        correlation_id=correlation_id,
        expires_at=datetime.now(timezone.utc) + ttl,
        tenant_id=tenant_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
        subject_id=subject_id,
        role=role,
    )
    return TrustedPrincipal.from_resolution(resolution, resolution_source=resolution_source)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# In-memory PostgreSQL semantics
# ---------------------------------------------------------------------------


class _Mappings:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def one(self) -> dict[str, Any]:
        return dict(self._row)


class _Result:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def mappings(self) -> _Mappings:
        return _Mappings(self._row)

    def scalar_one(self) -> Any:
        return self._row.get("relation")


@dataclass
class FakePostgres:
    """Shared connection state: GUCs, role flags and injectable faults."""

    role: str = "prodavan_app"
    bypass_rls: bool = False
    superuser: bool = False
    row_security: str = "on"
    #: Values visible to ``current_setting``; ``None`` means "never set".
    gucs: dict[str, str | None] = field(
        default_factory=lambda: {name: None for name in SETTING_OF_PARAMETER.values()}
    )
    #: Fault injection: drop the applied values so the readback must fail.
    ignore_apply: bool = False
    #: Fault injection: corrupt the values on the Nth ``set_config`` call.
    corrupt_on_call: int | None = None
    set_config_calls: int = 0
    statements: list[str] = field(default_factory=list)

    def reset_transaction(self) -> None:
        for name in self.gucs:
            self.gucs[name] = None
        self.gucs["app.trusted_service"] = None

    # -- request handling -------------------------------------------------
    def handle(self, sql: str, params: dict[str, Any] | None) -> _Result:
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if "rolbypassrls" in normalized:
            return _Result(
                {"role": self.role, "bypass_rls": self.bypass_rls, "superuser": self.superuser}
            )
        if "current_setting('app.tenant_id'" in normalized and "set_config" not in normalized:
            return _Result(
                {
                    "tenant_id": self.gucs.get("app.tenant_id"),
                    "cabinet_id": self.gucs.get("app.cabinet_id"),
                    "project_id": self.gucs.get("app.project_id"),
                    "user_id": self.gucs.get("app.user_id"),
                    "role": self.gucs.get("app.role"),
                    "trusted_service": self.gucs.get("app.trusted_service"),
                    "row_security": self.row_security,
                    "database_role": self.role,
                }
            )
        if "set_config('app.tenant_id'" in normalized:
            self.set_config_calls += 1
            assert params is not None
            applied = self._apply(params, trusted_service="app.trusted_service" in normalized)
            if self.ignore_apply:
                return _Result(applied)
            if self.corrupt_on_call == self.set_config_calls:
                applied["tenant_id"] = applied["tenant_id"] + "_x"
            for parameter, value in applied.items():
                self.gucs[SETTING_OF_PARAMETER.get(parameter, "app.trusted_service")] = value
            return _Result(applied)
        raise AssertionError(f"unexpected statement in fake PostgreSQL: {normalized}")

    @staticmethod
    def _apply(params: dict[str, Any], *, trusted_service: bool) -> dict[str, Any]:
        applied = {name: params.get(name, "") for name in SETTING_OF_PARAMETER}
        if trusted_service:
            applied["trusted_service"] = "true"
        return applied


class _Begin:
    def __init__(self, session: "_BaseFakeSession") -> None:
        self._session = session

    def __enter__(self) -> "_BaseFakeSession":
        self._session.depth += 1
        return self._session

    def __exit__(self, *_exc: object) -> bool:
        self._session.depth -= 1
        if self._session.depth == 0:
            self._session.pg.reset_transaction()
        return False


class _AsyncBegin:
    def __init__(self, session: "_BaseFakeSession") -> None:
        self._session = session

    async def __aenter__(self) -> "_BaseFakeSession":
        self._session.depth += 1
        return self._session

    async def __aexit__(self, *_exc: object) -> bool:
        self._session.depth -= 1
        if self._session.depth == 0:
            self._session.pg.reset_transaction()
        return False


class _BaseFakeSession:
    def __init__(self, pg: FakePostgres | None = None) -> None:
        self.pg = pg or FakePostgres()
        self.depth = 0

    def get_bind(self) -> Any:
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def in_transaction(self) -> bool:
        return self.depth > 0


class FakeSyncSession(_BaseFakeSession):
    def begin(self) -> _Begin:
        return _Begin(self)

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        return self.pg.handle(str(statement), params)


class FakeAsyncSession(_BaseFakeSession):
    def begin(self) -> _AsyncBegin:
        return _AsyncBegin(self)

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        return self.pg.handle(str(statement), params)


class SyncSessionFactory:
    def __init__(self, session: FakeSyncSession) -> None:
        self.session = session

    def __call__(self) -> FakeSyncSession:
        return self.session


class AsyncSessionFactory:
    def __init__(self, session: FakeAsyncSession) -> None:
        self.session = session

    def __call__(self) -> "AsyncSessionFactory":
        return self

    async def __aenter__(self) -> FakeAsyncSession:
        return self.session

    async def __aexit__(self, *_exc: object) -> bool:
        # Closing the session ends its transaction, exactly like SQLAlchemy:
        # transaction-local settings do not survive the connection release.
        self.session.depth = 0
        self.session.pg.reset_transaction()
        return False


# ---------------------------------------------------------------------------
# Independent predicate evaluation
# ---------------------------------------------------------------------------


def _resolve(scoped: ScopedColumn, row: dict[str, Any], tables: dict[str, list[dict[str, Any]]]) -> Any:
    if scoped.column is not None:
        return row.get(scoped.column)
    current: Any = row
    for hop in scoped.chain:
        reference = current.get(hop.on_column)
        if reference is None:
            return None
        match = next((candidate for candidate in tables.get(hop.table, ()) if candidate.get(hop.key) == reference), None)
        if match is None:
            return None
        current = match
    return current.get(scoped.chain[-1].value_column)


def _matches(scoped: ScopedColumn, row: dict[str, Any], tables: dict[str, list[dict[str, Any]]], value: str | None) -> bool:
    resolved = _resolve(scoped, row, tables)
    if resolved is None:
        return scoped.allows_null
    if value is None or value == "":
        return False
    return resolved == value


def scope_visible(
    spec: TablePolicySpec,
    row: dict[str, Any],
    tables: dict[str, list[dict[str, Any]]],
    settings: dict[str, str | None],
) -> bool:
    """Evaluate the tenant policy of ``spec`` for one row and one context."""

    if spec.tenant is None:
        return False
    checks: list[bool] = [_matches(spec.tenant, row, tables, settings.get("app.tenant_id"))]
    if spec.cabinet is not None:
        checks.append(_matches(spec.cabinet, row, tables, settings.get("app.cabinet_id")))
    if spec.project is not None:
        checks.append(_matches(spec.project, row, tables, settings.get("app.project_id")))
    restriction = RESTRICTIONS.get(spec.table)
    if restriction is not None:
        checks.append(bool(restriction(row)))
    return all(checks)


def spec_for(table: str) -> TablePolicySpec:
    for spec in CANONICAL_TABLE_POLICIES:
        if spec.table == table:
            return spec
    raise KeyError(table)


def restricted_tables() -> set[str]:
    return {spec.table for spec in CANONICAL_TABLE_POLICIES if spec.tenant_restriction is not None}


#: Contexts used by the allow/deny matrix.  ``None`` models an unset GUC.
CONTEXTS: dict[str, dict[str, str | None]] = {
    "no_context": {"app.tenant_id": None, "app.cabinet_id": None, "app.project_id": None},
    "empty_context": {"app.tenant_id": "", "app.cabinet_id": "", "app.project_id": ""},
    "tenant_a_cabinet_a_project_a": {
        "app.tenant_id": TENANT_A,
        "app.cabinet_id": CABINET_A,
        "app.project_id": PROJECT_A,
    },
    "tenant_a_cabinet_b": {
        "app.tenant_id": TENANT_A,
        "app.cabinet_id": CABINET_B,
        "app.project_id": None,
    },
    "tenant_b": {"app.tenant_id": TENANT_B, "app.cabinet_id": CABINET_B, "app.project_id": PROJECT_B},
}

ASSERTIONS = {
    "none": None,
    "matching": CallerAssertion(tenant_id=TENANT_A),
    "cross_tenant": CallerAssertion(tenant_id=TENANT_B),
    "cross_cabinet": CallerAssertion(tenant_id=TENANT_A, cabinet_id=CABINET_B),
    "cross_project": CallerAssertion(tenant_id=TENANT_A, cabinet_id=CABINET_A, project_id=PROJECT_B),
}

#: Every negative case that must deny or raise, with the expected reason code.
NEGATIVE_CASES: tuple[dict[str, str], ...] = (
    {"id": "RLS-001", "case": "no_tenant_context", "expect": "deny: tenant_isolation predicate is NULL"},
    {"id": "RLS-002", "case": "empty_tenant_context", "expect": "deny: nullif('') is NULL"},
    {"id": "RLS-003", "case": "cross_tenant_select", "expect": "deny: zero rows"},
    {"id": "RLS-004", "case": "cross_tenant_insert", "expect": "deny: WITH CHECK violation"},
    {"id": "RLS-005", "case": "cross_tenant_update", "expect": "deny: USING excludes, WITH CHECK rejects"},
    {"id": "RLS-006", "case": "set_outside_transaction", "expect": "transaction_required"},
    {"id": "RLS-007", "case": "caller_supplied_tenant", "expect": "cross_tenant_context"},
    {"id": "RLS-008", "case": "principal_not_server_resolved", "expect": "principal_not_server_resolved"},
    {"id": "RLS-009", "case": "expired_principal", "expect": "tenant_context_expired"},
    {"id": "RLS-010", "case": "readback_mismatch", "expect": "context_readback_mismatch"},
    {"id": "RLS-011", "case": "bypassrls_role", "expect": "database_role_bypasses_rls"},
    {"id": "RLS-012", "case": "tenant_session_sets_trusted_flag", "expect": "trusted policy requires prodavan_service"},
    {"id": "RLS-013", "case": "archived_registry_row", "expect": "deny: owner_state <> active"},
    {"id": "RLS-014", "case": "orphan_child_scope", "expect": "deny: parent EXISTS is false"},
    {"id": "RLS-015", "case": "session_level_set", "expect": "transaction_local_set_forbidden"},
)

BOUNDARY = {
    "execution": "not run",
    "alembic_upgrade": False,
    "database": False,
    "positive_canonical_writes": False,
    "verification_freeze": True,
}

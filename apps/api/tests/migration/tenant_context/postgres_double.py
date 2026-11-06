"""Acceptance doubles for the two tenant-context ports, exercised by the test run.

These helpers make every acceptance case provable and runnable:

* :class:`FakePostgres` reproduces exactly the PostgreSQL behaviour the adapters depend
  on: transaction-local GUCs, ``current_setting(name, true)`` returning ``NULL`` when
  unset, ``txid_current()`` changing per transaction, ``pg_roles`` flags, ``row_security``
  and the ``pg_class``/``pg_policies`` manifest.  The manifest rows are rendered from the
  **real** reviewed registry
  (:func:`~prodavan.infrastructure.persistence.rls.policies.tenant_predicate`), so the
  happy path exercises the production DDL text instead of an invented predicate.
* :class:`FakeAsyncSession` is a minimal ORM-session shape that records every statement,
  so a negative case can prove that *no* SQL was emitted at all.
* :func:`build_principal` / :class:`StubResolver` model the auth boundary: only the sealed
  :class:`~prodavan.infrastructure.persistence.rls.context.TrustedPrincipal` is accepted,
  and a resolver that returns nothing, a mapping, or a principal for another
  correlation must be refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from prodavan.application.migration.m04_scope.scope_migration import MigrationEvidenceContext
from prodavan.application.scope_registry.contracts import RlsContext
from prodavan.infrastructure.persistence.rls.context import (
    TrustedPrincipal,
    issue_server_resolution,
)
from prodavan.infrastructure.persistence.rls.policies import (
    CANONICAL_TABLE_POLICIES,
    tenant_predicate,
)

TENANT_A = "comp_aaaaaaaaaaaaaaaaaaaa"
TENANT_B = "comp_bbbbbbbbbbbbbbbbbbbb"
CABINET_A = "cab_aaaaaaaaaaaaaaaaaaaa"
CABINET_B = "cab_bbbbbbbbbbbbbbbbbbbb"
PROJECT_A = "prj_aaaaaaaaaaaaaaaaaaaa"
SUBJECT_A = "emp_aaaaaaaaaaaaaaaaaaaa"
SUBJECT_B = "emp_bbbbbbbbbbbbbbbbbbbb"
SESSION_A = "ses_aaaaaaaaaaaaaaaaaaaa"
CORRELATION = "corr_aaaaaaaaaaaaaaaaaaaa"
CORRELATION_OTHER = "corr_bbbbbbbbbbbbbbbbbbbb"
APPLICATION_TRANSACTION = "txn_aaaaaaaaaaaaaaaaaaaa"
WRITER_GENERATION = 7

SETTINGS: tuple[str, ...] = (
    "app.tenant_id",
    "app.cabinet_id",
    "app.project_id",
    "app.user_id",
    "app.role",
    "app.trusted_service",
)

GUC_OF_PARAMETER: dict[str, str] = {
    "tenant_id": "app.tenant_id",
    "cabinet_id": "app.cabinet_id",
    "project_id": "app.project_id",
    "user_id": "app.user_id",
    "role": "app.role",
    "trusted_service": "app.trusted_service",
}


# ---------------------------------------------------------------------------
# Principal construction (the documented auth-boundary path only)
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
    correlation_id: str = CORRELATION,
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


def expired_principal(**kwargs: Any) -> TrustedPrincipal:
    """A genuine principal whose resolution has expired.

    ``expires_at`` is deliberately outside the evidence digest, so this models the real
    scenario (a resolution that aged out) instead of a forged principal.
    """

    principal = build_principal(**kwargs)
    object.__setattr__(principal, "expires_at", datetime.now(timezone.utc) - timedelta(minutes=1))
    return principal


class StubResolver:
    """Auth-boundary double: returns the sealed principal, or refuses to resolve."""

    def __init__(self, principal: object | None = None, *, error: BaseException | None = None) -> None:
        self._principal = principal
        self._error = error
        self.calls: list[str] = []

    async def resolve(self, *, correlation_id: str) -> object | None:
        self.calls.append(correlation_id)
        if self._error is not None:
            raise self._error
        if self._principal is None:
            return None
        if isinstance(self._principal, TrustedPrincipal) and self._principal.correlation_id != correlation_id:
            # A principal resolved for another request must never be reused.
            return None
        return self._principal


_BUILD = object()


def evidence_context(
    *,
    tenant_id: str = TENANT_A,
    cabinet_id: str | None = CABINET_A,
    project_id: str | None = None,
    subject_id: str = SUBJECT_A,
    role: str = "tenant_admin",
    correlation_id: str = CORRELATION,
    transaction_id: str = APPLICATION_TRANSACTION,
    writer_generation: int | None = WRITER_GENERATION,
    rls_context: RlsContext | None | object = _BUILD,
) -> MigrationEvidenceContext:
    """A server-derived M04 evidence context.

    ``rls_context`` defaults to a matching resolved context; pass ``None`` explicitly to
    model a caller that never resolved one (which must be refused).
    """

    scope: RlsContext | None
    if rls_context is _BUILD:
        scope = (
            RlsContext(
                tenant_id=tenant_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
                user_id=subject_id,
                role=role,
            )
            if tenant_id
            else None
        )
    else:
        scope = rls_context  # type: ignore[assignment]
    return MigrationEvidenceContext(
        tenant_id=tenant_id or None,
        authenticated=True,
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        user_id=subject_id,
        role=role,
        rls_context=scope,
        writer_generation=writer_generation,
    )


def unusable_context(**overrides: Any) -> MigrationEvidenceContext:
    """An M04 context that must be refused before any SQL (no resolved RLS context)."""

    values: dict[str, Any] = {"tenant_id": TENANT_A, "rls_context": None}
    values.update(overrides)
    return evidence_context(**values)


# ---------------------------------------------------------------------------
# In-memory PostgreSQL
# ---------------------------------------------------------------------------


class _Mappings:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def one(self) -> dict[str, Any]:
        if not self._row:
            raise LookupError("no row returned")
        return dict(self._row)

    def first(self) -> dict[str, Any] | None:
        return None if not self._row else dict(self._row)


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> _Mappings:
        return _Mappings(self._row)


def spec_for(relation: str):
    for spec in CANONICAL_TABLE_POLICIES:
        if spec.table == relation:
            return spec
    raise KeyError(relation)


@dataclass
class FakePostgres:
    """Connection state shared by one transaction: GUCs, role flags add policy manifest."""

    role: str = "prodavan_app"
    bypass_rls: bool = False
    superuser: bool = False
    row_security: str = "on"
    #: ``None`` means "never set in this transaction"; ``''`` is the empty setting.
    gucs: dict[str, str | None] = field(default_factory=lambda: {name: None for name in SETTINGS})
    statements: list[str] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)
    #: Per-relation overrides applied on top of the reviewed manifest row.
    manifest: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Relations that do not exist in this schema at all.
    missing_relations: set[str] = field(default_factory=set)
    #: Fault injection: ``set_config`` reports success without changing anything.
    ignore_apply: bool = False
    #: Fault injection: corrupt the values on the Nth ``set_config`` call.
    corrupt_on_call: int | None = None
    set_config_calls: int = 0
    transaction_counter: int = 0
    txid: int = 0

    # -- transaction lifecycle --------------------------------------------
    def begin_transaction(self) -> int:
        self.transaction_counter += 1
        self.txid = self.transaction_counter
        for name in SETTINGS:
            self.gucs[name] = None
        return self.txid

    def end_transaction(self) -> None:
        self.txid = 0
        for name in SETTINGS:
            self.gucs[name] = None

    # -- helpers ----------------------------------------------------------
    def setting(self, name: str) -> str | None:
        return self.gucs.get(name)

    def applied_settings(self) -> dict[str, str | None]:
        return {name: self.gucs.get(name) for name in SETTINGS}

    def statements_with(self, fragment: str) -> list[str]:
        return [statement for statement in self.statements if fragment in statement]

    # -- request handling --------------------------------------------------
    def handle(self, sql: str, params: dict[str, Any] | None) -> _Result:
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if params:
            self.parameters.append(dict(params))

        if normalized.strip().upper().startswith("SET "):
            raise AssertionError(f"session-level SET must never be emitted: {normalized}")

        if "pg_roles" in normalized:
            return _Result({"role": self.role, "bypass_rls": self.bypass_rls, "superuser": self.superuser})
        if "pg_policies" in normalized:
            assert params is not None
            return _Result(
                self._manifest_row(
                    str(params["relation"]),
                    str(params["tenant_policy"]),
                    str(params["trusted_policy"]),
                )
            )
        if "txid_current" in normalized:
            return _Result({"database_transaction_id": str(self.txid)})
        if "set_config(" in normalized:
            return _Result(self._apply(normalized, params or {}))
        if "current_setting(" in normalized:
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
        raise AssertionError(f"unexpected statement in the fake PostgreSQL: {normalized}")

    # -- manifest ----------------------------------------------------------
    def _manifest_row(self, relation: str, tenant_policy: str, trusted_policy: str) -> dict[str, Any] | None:
        if relation in self.missing_relations:
            return None
        spec = spec_for(relation)
        predicate = tenant_predicate(spec)
        row: dict[str, Any] = {
            "relation": relation,
            "row_security": True,
            "force_row_security": True,
            "tenant_policy_name": tenant_policy,
            "tenant_policy_permissive": "PERMISSIVE",
            "tenant_policy_command": "ALL",
            "tenant_policy_using": predicate,
            "tenant_policy_with_check": predicate,
            "trusted_policy_name": trusted_policy,
        }
        row.update(self.manifest.get(relation, {}))
        return row

    # -- settings ----------------------------------------------------------
    def _apply(self, normalized: str, params: dict[str, Any]) -> dict[str, Any]:
        self.set_config_calls += 1
        written = [name for name in SETTINGS if f"set_config('{name}'" in normalized]
        applied: dict[str, Any] = {}
        for name in written:
            parameter = name.removeprefix("app.")
            if name == "app.trusted_service":
                applied[parameter] = "true" if "'true', true)" in normalized else params.get(parameter, "")
            else:
                applied[parameter] = params.get(parameter, "")
        if self.ignore_apply:
            return applied
        if self.corrupt_on_call == self.set_config_calls and "tenant_id" in applied:
            applied["tenant_id"] = str(applied["tenant_id"]) + "_x"
        for parameter, value in applied.items():
            self.gucs[GUC_OF_PARAMETER[parameter]] = value
        return applied


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class _AsyncBegin:
    def __init__(self, session: "_BaseFakeSession") -> None:
        self._session = session

    async def __aenter__(self) -> "_BaseFakeSession":
        self._session.depth += 1
        if self._session.depth == 1:
            self._session.pg.begin_transaction()
        return self._session

    async def __aexit__(self, *_exc: object) -> bool:
        self._session.depth -= 1
        if self._session.depth == 0:
            self._session.pg.end_transaction()
        return False


class _BaseFakeSession:
    def __init__(self, pg: FakePostgres | None = None, *, dialect: str = "postgresql") -> None:
        self.pg = pg or FakePostgres()
        self.dialect = dialect
        self.depth = 0

    def get_bind(self) -> Any:
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def in_transaction(self) -> bool:
        return self.depth > 0


class FakeAsyncSession(_BaseFakeSession):
    """Minimal ``AsyncSession`` shape: statement recording plus transaction depth."""

    def begin(self) -> _AsyncBegin:
        return _AsyncBegin(self)

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        return self.pg.handle(str(statement), params)


class FakeSessionFactory:
    """A session factory that must never be opened by a binder (explicit refusal case)."""

    def __init__(self, session: FakeAsyncSession) -> None:
        self.session = session
        self.opened = False

    def __call__(self) -> FakeAsyncSession:
        self.opened = True
        return self.session

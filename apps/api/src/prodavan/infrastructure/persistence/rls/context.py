"""Trusted, transaction-local tenant context for PostgreSQL row level security.

The policies installed by revision ``2026110307`` read ``app.tenant_id``,
``app.cabinet_id`` and ``app.project_id``.  Those settings are only meaningful if
the application can prove two things:

1. the values come from a **server-side resolution** of the authenticated
   principal, never from request payload, header or plugin claim;
2. the values are **transaction-local**, so a pooled connection cannot leak a
   tenant to the next request.

This module is the only supported way to write those settings.  It fails closed
for every ambiguous input and never widens access:

* a missing, empty, malformed or non-server-resolved principal is rejected
  before any SQL runs (``tenant_context_missing`` / ``tenant_context_invalid`` /
  ``principal_not_server_resolved``);
* caller assertions (what the request *claims*) are only compared with the
  resolved identity.  A differing claim is ``cross_tenant_context``; a matching
  claim is still not the authority - the resolved value is what gets set;
* the context is refused outside a transaction (``transaction_required``); every
  statement uses ``set_config(..., true)`` and a bare ``SET`` is never emitted
  (``transaction_local_set_forbidden``);
* settings are read back after they are applied, and a mismatch
  (``context_readback_mismatch`` / ``row_security_disabled``) aborts the
  transaction instead of silently running with a partially applied context;
* a database role that bypasses RLS (``BYPASSRLS``/superuser) is rejected with
  ``database_role_bypasses_rls``, since setting the GUCs there would only give
  the illusion of isolation;
* the previous values are restored on exit so nested contexts compose and a
  reused transaction never keeps a stale tenant.

Nothing here replaces registry/definition/instance/grant checks or the trusted
writer epoch.  RLS bounds visibility; it never authorizes a positive write.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Literal, Protocol

from sqlalchemy import text

from prodavan.infrastructure.persistence.rls.policies import (
    CABINET_SETTING,
    PROJECT_SETTING,
    TENANT_SETTING,
    TRUSTED_SERVICE_ROLE,
    TRUSTED_SERVICE_SETTING,
)

USER_SETTING: Final = "app.user_id"
ROLE_SETTING: Final = "app.role"

#: Settings owned by this module, in the order they are written and restored.
TENANT_GUCS: Final[tuple[str, ...]] = (
    TENANT_SETTING,
    CABINET_SETTING,
    PROJECT_SETTING,
    USER_SETTING,
    ROLE_SETTING,
)

#: A caller may not mark its own session as trusted; only
#: :func:`trusted_service_transaction` sets this flag.
SERVICE_GUCS: Final[tuple[str, ...]] = (*TENANT_GUCS, TRUSTED_SERVICE_SETTING)

#: GUC name -> column the readback selects it as (``current_setting('app.tenant_id', ...)``
#: is aliased ``tenant_id``).  The GUC name is *not* usable as a readback key: doing so
#: silently returns ``None`` for every setting, which turned the "restore the previous
#: values" step into "overwrite them with blanks" and also fed the restore statement
#: parameters that did not match its placeholders.
_READBACK_KEY: Final[dict[str, str]] = {name: name.removeprefix("app.") for name in TENANT_GUCS}

ResolutionSource = Literal[
    "session_cookie",
    "bearer_jwt_verified",
    "internal_service_mtls",
    "migration_operator",
]

#: Closed allow-list of server-side resolution paths.  Request-derived sources
#: (``request_claim``, ``header``, ``plugin``, ``job_payload``) are absent on
#: purpose: passing one is an explicit denial, not a fallback.
SERVER_RESOLUTION_SOURCES: Final[tuple[str, ...]] = (
    "session_cookie",
    "bearer_jwt_verified",
    "internal_service_mtls",
    "migration_operator",
)

_IDENTIFIER_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
_MAX_IDENTITY_LENGTH = 40

_SEAL: Final = object()


class RlsContextError(RuntimeError):
    """A trusted tenant context could not be established or proven."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


# ---------------------------------------------------------------------------
# Identity value objects
# ---------------------------------------------------------------------------


def _require_identity(value: object, field_name: str) -> str:
    if value is None or value == "":
        raise RlsContextError("tenant_context_missing", f"{field_name} is required")
    if not isinstance(value, str):
        raise RlsContextError("tenant_context_invalid", f"{field_name} must be a string")
    if value != value.strip():
        raise RlsContextError("tenant_context_invalid", f"{field_name} has surrounding whitespace")
    if len(value) > _MAX_IDENTITY_LENGTH:
        raise RlsContextError("tenant_context_invalid", f"{field_name} is longer than 40 characters")
    if not set(value) <= _IDENTIFIER_CHARS:
        raise RlsContextError("tenant_context_invalid", f"{field_name} contains unsupported characters")
    return value


def _optional_identity(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_identity(value, field_name)


@dataclass(frozen=True, slots=True, init=False)
class ServerResolution:
    """Server-side resolution record backing a :class:`TrustedPrincipal`.

    Instances can only be produced by :func:`issue_server_resolution`, which is
    called from the authentication boundary after the session/token has been
    verified.  Construction from request data is a programming error and is
    rejected here so that an accidental ``ServerResolution(...)`` cannot be used
    to launder a caller-supplied tenant.

    This seal is defence in depth inside one process; the real guarantee is that
    only the auth middleware has the verified credential material.
    """

    resolved_by: str
    correlation_id: str
    expires_at: datetime
    tenant_id: str
    cabinet_id: str | None
    project_id: str | None
    subject_id: str
    role: str

    def __init__(self, *_: object, **__: object) -> None:
        raise RlsContextError(
            "principal_not_server_resolved",
            "use issue_server_resolution() from the authentication boundary",
        )

    @classmethod
    def _issue(
        cls,
        *,
        resolved_by: str,
        correlation_id: str,
        expires_at: datetime,
        tenant_id: str,
        cabinet_id: str | None,
        project_id: str | None,
        subject_id: str,
        role: str,
        seal: object,
    ) -> "ServerResolution":
        if seal is not _SEAL:
            raise RlsContextError("principal_not_server_resolved", "seal mismatch")
        resolution = object.__new__(cls)
        object.__setattr__(resolution, "resolved_by", resolved_by)
        object.__setattr__(resolution, "correlation_id", correlation_id)
        object.__setattr__(resolution, "expires_at", expires_at)
        object.__setattr__(resolution, "tenant_id", tenant_id)
        object.__setattr__(resolution, "cabinet_id", cabinet_id)
        object.__setattr__(resolution, "project_id", project_id)
        object.__setattr__(resolution, "subject_id", subject_id)
        object.__setattr__(resolution, "role", role)
        return resolution


def issue_server_resolution(
    *,
    resolved_by: str,
    correlation_id: str,
    expires_at: datetime,
    tenant_id: str,
    cabinet_id: str | None = None,
    project_id: str | None = None,
    subject_id: str,
    role: str,
) -> ServerResolution:
    """Create a resolution record.  Call only after the credential was verified.

    ``resolved_by`` names the trusted component (for example
    ``"auth.session_middleware"``); it is recorded as evidence and must be a
    non-empty identifier-like string.
    """

    _require_identity(resolved_by, "resolved_by")
    _require_identity(correlation_id, "correlation_id")
    _require_identity(subject_id, "subject_id")
    _require_identity(role, "role")
    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
        raise RlsContextError("principal_not_server_resolved", "expires_at must be timezone-aware")
    return ServerResolution._issue(
        resolved_by=resolved_by,
        correlation_id=correlation_id,
        expires_at=expires_at,
        tenant_id=_require_identity(tenant_id, "tenant_id"),
        cabinet_id=_optional_identity(cabinet_id, "cabinet_id"),
        project_id=_optional_identity(project_id, "project_id"),
        subject_id=subject_id,
        role=role,
        seal=_SEAL,
    )


@dataclass(frozen=True, slots=True, init=False)
class TrustedPrincipal:
    """Identity proven by the server; the only input accepted by this module."""

    tenant_id: str
    cabinet_id: str | None
    project_id: str | None
    subject_id: str
    role: str
    resolution_source: str
    resolved_by: str
    correlation_id: str
    expires_at: datetime
    digest: str

    def __init__(self, *_: object, **__: object) -> None:
        raise RlsContextError(
            "principal_not_server_resolved",
            "build the principal with TrustedPrincipal.from_resolution()",
        )

    @classmethod
    def from_resolution(
        cls,
        resolution: ServerResolution,
        *,
        resolution_source: ResolutionSource,
    ) -> "TrustedPrincipal":
        if not isinstance(resolution, ServerResolution):
            raise RlsContextError("principal_not_server_resolved", "a ServerResolution is required")
        if resolution_source not in SERVER_RESOLUTION_SOURCES:
            raise RlsContextError(
                "principal_not_server_resolved",
                f"unsupported resolution source {resolution_source!r}",
            )
        if resolution_source == "migration_operator" and resolution.role != "migration_role":
            raise RlsContextError(
                "principal_not_server_resolved",
                "migration_operator must resolve to the migration_role",
            )
        principal = object.__new__(cls)
        object.__setattr__(principal, "tenant_id", _require_identity(resolution.tenant_id, "tenant_id"))
        object.__setattr__(principal, "cabinet_id", _optional_identity(resolution.cabinet_id, "cabinet_id"))
        object.__setattr__(principal, "project_id", _optional_identity(resolution.project_id, "project_id"))
        object.__setattr__(principal, "subject_id", resolution.subject_id)
        object.__setattr__(principal, "role", resolution.role)
        object.__setattr__(principal, "resolution_source", resolution_source)
        object.__setattr__(principal, "resolved_by", resolution.resolved_by)
        object.__setattr__(principal, "correlation_id", resolution.correlation_id)
        object.__setattr__(principal, "expires_at", resolution.expires_at)
        object.__setattr__(principal, "digest", _principal_digest(
            tenant_id=resolution.tenant_id,
            cabinet_id=resolution.cabinet_id,
            project_id=resolution.project_id,
            subject_id=resolution.subject_id,
            role=resolution.role,
            resolution_source=resolution_source,
            resolved_by=resolution.resolved_by,
            correlation_id=resolution.correlation_id,
        ))
        principal.validate()
        return principal

    def validate(self, *, now: datetime | None = None) -> None:
        """Re-check every invariant; called on construction and per transaction."""

        if not isinstance(self.tenant_id, str) or not self.tenant_id:
            raise RlsContextError("tenant_context_missing", "tenant_id is required")
        if self.project_id is not None and self.cabinet_id is None:
            raise RlsContextError(
                "tenant_context_invalid",
                "a project scope requires an explicit cabinet scope",
            )
        if self.resolution_source not in SERVER_RESOLUTION_SOURCES:
            raise RlsContextError("principal_not_server_resolved", "resolution source is not trusted")
        if not self.resolved_by or not self.correlation_id:
            raise RlsContextError("principal_not_server_resolved", "resolution evidence is incomplete")
        if self.digest != _principal_digest(
            tenant_id=self.tenant_id,
            cabinet_id=self.cabinet_id,
            project_id=self.project_id,
            subject_id=self.subject_id,
            role=self.role,
            resolution_source=self.resolution_source,
            resolved_by=self.resolved_by,
            correlation_id=self.correlation_id,
        ):
            raise RlsContextError("tenant_context_invalid", "principal evidence digest mismatch")
        moment = now or datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None or self.expires_at <= moment:
            raise RlsContextError("tenant_context_expired", "the resolved principal has expired")


def _principal_digest(
    *,
    tenant_id: str,
    cabinet_id: str | None,
    project_id: str | None,
    subject_id: str,
    role: str,
    resolution_source: str,
    resolved_by: str,
    correlation_id: str,
) -> str:
    payload = json.dumps(
        {
            "tenant_id": tenant_id,
            "cabinet_id": cabinet_id,
            "project_id": project_id,
            "subject_id": subject_id,
            "role": role,
            "resolution_source": resolution_source,
            "resolved_by": resolved_by,
            "correlation_id": correlation_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CallerAssertion:
    """What the request claims.  Compared only - never used as authority."""

    tenant_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("tenant_id", "cabinet_id", "project_id"):
            value = getattr(self, name)
            if value is None:
                continue
            if value == "":
                # An empty header/claim asserts nothing; it is not "the empty tenant".
                object.__setattr__(self, name, None)
                continue
            _require_identity(value, f"asserted {name}")

    def disagreements(self, principal: TrustedPrincipal) -> tuple[str, ...]:
        """Return the asserted anchors that contradict the resolved identity."""

        mismatches: list[str] = []
        for name in ("tenant_id", "cabinet_id", "project_id"):
            asserted = getattr(self, name)
            if asserted is None:
                continue
            if asserted != getattr(principal, name):
                mismatches.append(name)
        return tuple(mismatches)


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Applied context evidence; safe to log (identities and digests only)."""

    tenant_id: str
    cabinet_id: str | None
    project_id: str | None
    subject_id: str
    role: str
    database_role: str
    principal_digest: str
    correlation_id: str
    context_reference: str
    previous: tuple[tuple[str, str], ...] = ()

    def previous_value(self, setting: str) -> str | None:
        for name, value in self.previous:
            if name == setting:
                return value
        return None


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_APPLY_SQL = (
    "SELECT "
    "set_config('app.tenant_id', :tenant_id, true) AS tenant_id, "
    "set_config('app.cabinet_id', :cabinet_id, true) AS cabinet_id, "
    "set_config('app.project_id', :project_id, true) AS project_id, "
    "set_config('app.user_id', :user_id, true) AS user_id, "
    "set_config('app.role', :role, true) AS role"
)

_APPLY_SERVICE_SQL = (
    "SELECT "
    "set_config('app.tenant_id', :tenant_id, true) AS tenant_id, "
    "set_config('app.cabinet_id', :cabinet_id, true) AS cabinet_id, "
    "set_config('app.project_id', :project_id, true) AS project_id, "
    "set_config('app.user_id', :user_id, true) AS user_id, "
    "set_config('app.role', :role, true) AS role, "
    "set_config('app.trusted_service', 'true', true) AS trusted_service"
)

_READBACK_SQL = (
    "SELECT "
    "current_setting('app.tenant_id', true) AS tenant_id, "
    "current_setting('app.cabinet_id', true) AS cabinet_id, "
    "current_setting('app.project_id', true) AS project_id, "
    "current_setting('app.user_id', true) AS user_id, "
    "current_setting('app.role', true) AS role, "
    "current_setting('app.trusted_service', true) AS trusted_service, "
    "current_setting('row_security', true) AS row_security, "
    "current_user AS database_role"
)

_RESTORE_SQL = (
    "SELECT "
    "set_config('app.tenant_id', :tenant_id, true) AS tenant_id, "
    "set_config('app.cabinet_id', :cabinet_id, true) AS cabinet_id, "
    "set_config('app.project_id', :project_id, true) AS project_id, "
    "set_config('app.user_id', :user_id, true) AS user_id, "
    "set_config('app.role', :role, true) AS role"
)

#: Restores the tenant settings *and* clears the service flag, so the trusted
#: path cannot leave ``app.trusted_service='true'`` behind in the transaction.
_RESTORE_SERVICE_SQL = (
    _RESTORE_SQL + ", set_config('app.trusted_service', '', true) AS trusted_service"
)

_SERVICE_ROLE_SQL = (
    "SELECT r.rolname AS role, r.rolbypassrls AS bypass_rls, r.rolsuper AS superuser "
    "FROM pg_roles AS r WHERE r.rolname = current_user"
)


def _raise_if_not_transaction_local() -> None:
    """Static guard: this module must never emit a session-level ``SET``."""

    for statement in (_APPLY_SQL, _APPLY_SERVICE_SQL, _RESTORE_SQL):
        if "set_config(" not in statement:
            raise RlsContextError("transaction_local_set_forbidden", "GUC writes must use set_config")
        if ", true)" not in statement:
            raise RlsContextError("transaction_local_set_forbidden", "set_config must be transaction-local")
        if statement.strip().upper().startswith("SET "):
            raise RlsContextError("transaction_local_set_forbidden", "bare SET is not allowed")
        if ";" in statement:
            raise RlsContextError("transaction_local_set_forbidden", "statements must not be batched")


_raise_if_not_transaction_local()


# ---------------------------------------------------------------------------
# Session adapters
# ---------------------------------------------------------------------------


class _Executor(Protocol):
    def dialect_name(self) -> str: ...
    def in_transaction(self) -> bool: ...


class _AsyncExecutor:
    def __init__(self, session: object) -> None:
        self._session = session

    def dialect_name(self) -> str:
        bind = self._session.get_bind()  # type: ignore[attr-defined]
        return str(bind.dialect.name)

    def in_transaction(self) -> bool:
        return bool(self._session.in_transaction())  # type: ignore[attr-defined]

    async def _one(self, statement: str, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        result = await self._session.execute(text(statement), dict(params or {}))  # type: ignore[attr-defined]
        return result.mappings().one()


class _SyncExecutor:
    def __init__(self, session: object) -> None:
        self._session = session

    def dialect_name(self) -> str:
        bind = self._session.get_bind()  # type: ignore[attr-defined]
        return str(bind.dialect.name)

    def in_transaction(self) -> bool:
        return bool(self._session.in_transaction())  # type: ignore[attr-defined]

    def _one(self, statement: str, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        result = self._session.execute(text(statement), dict(params or {}))  # type: ignore[attr-defined]
        return result.mappings().one()


# ---------------------------------------------------------------------------
# Shared validation and statement planning
# ---------------------------------------------------------------------------


def _validate_environment(executor: _Executor) -> None:
    if executor.dialect_name() != "postgresql":
        raise RlsContextError(
            "postgresql_required",
            "transaction-local tenant context is only defined for PostgreSQL",
        )
    if not executor.in_transaction():
        raise RlsContextError(
            "transaction_required",
            "tenant context must be set inside an explicit transaction, never on a session",
        )


def _resolve_parameters(principal: TrustedPrincipal, *, now: datetime | None) -> tuple[TrustedPrincipal, dict[str, str]]:
    if not isinstance(principal, TrustedPrincipal):
        raise RlsContextError(
            "principal_not_server_resolved",
            "only a TrustedPrincipal produced by the authentication boundary is accepted",
        )
    principal.validate(now=now)
    parameters = {
        "tenant_id": principal.tenant_id,
        "cabinet_id": principal.cabinet_id or "",
        "project_id": principal.project_id or "",
        "user_id": principal.subject_id,
        "role": principal.role,
    }
    return principal, parameters


def _reject_assertion(principal: TrustedPrincipal, assertion: CallerAssertion | None) -> None:
    if assertion is None:
        return
    if not isinstance(assertion, CallerAssertion):
        raise RlsContextError("tenant_context_invalid", "caller assertion must be a CallerAssertion")
    mismatches = assertion.disagreements(principal)
    if mismatches:
        raise RlsContextError(
            "cross_tenant_context",
            "request claims contradict the resolved scope: " + ", ".join(mismatches),
        )


def _check_database_role(row: Mapping[str, object], *, trusted_service: bool) -> str:
    database_role = str(row.get("role") or "")
    if bool(row.get("bypass_rls")):
        raise RlsContextError(
            "database_role_bypasses_rls",
            f"role {database_role!r} has BYPASSRLS; RLS isolation would not apply",
        )
    if bool(row.get("superuser")):
        raise RlsContextError(
            "database_role_bypasses_rls",
            f"role {database_role!r} is a superuser; RLS isolation would not apply",
        )
    if trusted_service and database_role != TRUSTED_SERVICE_ROLE:
        raise RlsContextError(
            "trusted_service_role_mismatch",
            f"trusted service context requires the {TRUSTED_SERVICE_ROLE!r} role",
        )
    return database_role


def _verify_readback(
    readback: Mapping[str, object],
    parameters: Mapping[str, str],
    *,
    trusted_service: bool,
) -> None:
    for name, expected in parameters.items():
        if str(readback.get(name) or "") != expected:
            raise RlsContextError(
                "context_readback_mismatch",
                f"{name} was not applied transaction-locally",
            )
    if str(readback.get("row_security") or "").lower() != "on":
        raise RlsContextError(
            "row_security_disabled",
            "row_security is not on; RLS would be skipped for this transaction",
        )
    if trusted_service:
        if str(readback.get("trusted_service") or "") != "true":
            raise RlsContextError("context_readback_mismatch", "trusted_service flag was not applied")


def _previous_values(readback: Mapping[str, object]) -> dict[str, str]:
    """Capture the settings that were live before this context was applied.

    Keyed by GUC name (``app.tenant_id``) because that is what
    :meth:`TenantContext.previous_value` and the audit evidence speak; the values are
    read through :data:`_READBACK_KEY`, since the readback row is keyed by column
    alias and would otherwise yield ``None`` for every setting.
    """

    return {name: str(readback.get(_READBACK_KEY[name]) or "") for name in TENANT_GUCS}


def _restore_parameters(previous: Mapping[str, str]) -> dict[str, str]:
    """Bind parameters for ``_RESTORE_SQL``: its placeholders are the column aliases."""

    return {_READBACK_KEY[name]: previous.get(name, "") for name in TENANT_GUCS}


def _context_from(
    principal: TrustedPrincipal,
    parameters: Mapping[str, str],
    *,
    database_role: str,
    previous: Mapping[str, str],
    context_reference: str,
) -> TenantContext:
    return TenantContext(
        tenant_id=principal.tenant_id,
        cabinet_id=principal.cabinet_id,
        project_id=principal.project_id,
        subject_id=principal.subject_id,
        role=principal.role,
        database_role=database_role,
        principal_digest=principal.digest,
        correlation_id=principal.correlation_id,
        context_reference=context_reference,
        previous=tuple((name, previous[name]) for name in TENANT_GUCS if name in previous),
    )


def _context_reference() -> str:
    """Opaque local marker for log/trace correlation of one applied context."""

    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


@asynccontextmanager
async def trusted_tenant_context(
    session: object,
    *,
    principal: TrustedPrincipal,
    assertion: CallerAssertion | None = None,
    verify_database_role: bool = True,
    clock: Callable[[], datetime] | None = None,
) -> AsyncIterator[TenantContext]:
    """Set the tenant scope on an **existing** transaction and restore it after.

    ``session`` must already be inside ``session.begin()``.  The caller keeps
    ownership of commit/rollback; this function only writes and restores the
    transaction-local settings.
    """

    executor = _AsyncExecutor(session)
    _validate_environment(executor)
    resolved, parameters = _resolve_parameters(
        principal, now=(clock or (lambda: datetime.now(timezone.utc)))()
    )
    _reject_assertion(resolved, assertion)

    if verify_database_role:
        role_row = await executor._one(_SERVICE_ROLE_SQL)
        database_role = _check_database_role(role_row, trusted_service=False)
    else:
        database_role = str((await executor._one(_READBACK_SQL)).get("database_role") or "")

    previous = _previous_values(await executor._one(_READBACK_SQL))
    await executor._one(_APPLY_SQL, parameters)
    _verify_readback(await executor._one(_READBACK_SQL), parameters, trusted_service=False)

    context = _context_from(
        resolved,
        parameters,
        database_role=database_role,
        previous=previous,
        context_reference=_context_reference(),
    )
    try:
        yield context
    except BaseException:
        # The caller rolls the transaction back; transaction-local settings die
        # with it, so there is nothing to restore on this path.
        raise
    else:
        await executor._one(_RESTORE_SQL, _restore_parameters(previous))
        if _previous_values(await executor._one(_READBACK_SQL)) != dict(previous):
            raise RlsContextError("context_restore_failed", "previous tenant context was not restored")


@asynccontextmanager
async def trusted_tenant_transaction(
    session_factory: Callable[[], object],
    *,
    principal: TrustedPrincipal,
    assertion: CallerAssertion | None = None,
    verify_database_role: bool = True,
    clock: Callable[[], datetime] | None = None,
) -> AsyncIterator[tuple[object, TenantContext]]:
    """Own one transaction: open it, apply the context, commit or roll back.

    A trust failure before ``yield`` closes the session without committing, so a
    denied operation can never leave partial writes behind.
    """

    async with session_factory() as session:  # type: ignore[misc]
        async with session.begin():  # type: ignore[attr-defined]
            async with trusted_tenant_context(
                session,
                principal=principal,
                assertion=assertion,
                verify_database_role=verify_database_role,
                clock=clock,
            ) as context:
                yield session, context


@asynccontextmanager
async def trusted_service_transaction(
    session_factory: Callable[[], object],
    *,
    principal: TrustedPrincipal,
    assertion: CallerAssertion | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AsyncIterator[tuple[object, TenantContext]]:
    """Cross-tenant read/write context for the ``prodavan_service`` role.

    The service policy additionally requires ``app.trusted_service='true'`` and
    the exact database role, so a normal tenant session cannot reach other
    tenants by setting the flag itself.
    """

    async with session_factory() as session:  # type: ignore[misc]
        async with session.begin():  # type: ignore[attr-defined]
            executor = _AsyncExecutor(session)
            _validate_environment(executor)
            resolved, parameters = _resolve_parameters(
                principal, now=(clock or (lambda: datetime.now(timezone.utc)))()
            )
            _reject_assertion(resolved, assertion)
            database_role = _check_database_role(await executor._one(_SERVICE_ROLE_SQL), trusted_service=True)
            previous = _previous_values(await executor._one(_READBACK_SQL))
            await executor._one(_APPLY_SERVICE_SQL, parameters)
            _verify_readback(await executor._one(_READBACK_SQL), parameters, trusted_service=True)
            try:
                yield session, _context_from(
                    resolved,
                    parameters,
                    database_role=database_role,
                    previous=previous,
                    context_reference=_context_reference(),
                )
            except BaseException:
                raise
            else:
                await executor._one(_RESTORE_SERVICE_SQL, _restore_parameters(previous))
                restored = (await executor._one(_READBACK_SQL)).get("trusted_service")
                if str(restored or "") not in ("", "None"):
                    raise RlsContextError(
                        "context_restore_failed", "trusted service flag was not cleared"
                    )


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


@contextmanager
def trusted_tenant_context_sync(
    session: object,
    *,
    principal: TrustedPrincipal,
    assertion: CallerAssertion | None = None,
    verify_database_role: bool = True,
    clock: Callable[[], datetime] | None = None,
) -> Iterator[TenantContext]:
    """Synchronous twin of :func:`trusted_tenant_context`."""

    executor = _SyncExecutor(session)
    _validate_environment(executor)
    resolved, parameters = _resolve_parameters(
        principal, now=(clock or (lambda: datetime.now(timezone.utc)))()
    )
    _reject_assertion(resolved, assertion)

    if verify_database_role:
        database_role = _check_database_role(executor._one(_SERVICE_ROLE_SQL), trusted_service=False)
    else:
        database_role = str(executor._one(_READBACK_SQL).get("database_role") or "")

    previous = _previous_values(executor._one(_READBACK_SQL))
    executor._one(_APPLY_SQL, parameters)
    _verify_readback(executor._one(_READBACK_SQL), parameters, trusted_service=False)

    context = _context_from(
        resolved,
        parameters,
        database_role=database_role,
        previous=previous,
        context_reference=_context_reference(),
    )
    try:
        yield context
    except BaseException:
        raise
    else:
        executor._one(_RESTORE_SQL, _restore_parameters(previous))
        if _previous_values(executor._one(_READBACK_SQL)) != dict(previous):
            raise RlsContextError("context_restore_failed", "previous tenant context was not restored")


@contextmanager
def trusted_tenant_transaction_sync(
    session_factory: Callable[[], object],
    *,
    principal: TrustedPrincipal,
    assertion: CallerAssertion | None = None,
    verify_database_role: bool = True,
    clock: Callable[[], datetime] | None = None,
) -> Iterator[tuple[object, TenantContext]]:
    """Synchronous twin of :func:`trusted_tenant_transaction`."""

    session = session_factory()
    with session.begin():  # type: ignore[attr-defined]
        with trusted_tenant_context_sync(
            session,
            principal=principal,
            assertion=assertion,
            verify_database_role=verify_database_role,
            clock=clock,
        ) as context:
            yield session, context


__all__ = [
    "ROLE_SETTING",
    "SERVER_RESOLUTION_SOURCES",
    "SERVICE_GUCS",
    "TENANT_GUCS",
    "USER_SETTING",
    "CallerAssertion",
    "RlsContextError",
    "ServerResolution",
    "TenantContext",
    "TrustedPrincipal",
    "issue_server_resolution",
    "trusted_service_transaction",
    "trusted_tenant_context",
    "trusted_tenant_context_sync",
    "trusted_tenant_transaction",
    "trusted_tenant_transaction_sync",
]

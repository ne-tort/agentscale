"""Production implementations of the two tenant-context ports of the migration chain.

Two ports of the migration chain were declared in ``application/migration/**`` and had
no implementation anywhere in ``src/prodavan``, which is why no step of the chain could
ever be production-bound:

* :class:`~prodavan.application.migration.m04_scope.scope_persistence.TenantScopeContextPort`
  (``m04_scope/scope_persistence.py``) — used by ``SqlAlchemyScopeBackfillAdapter``;
* :class:`~prodavan.application.migration.m05_instance_runtime.instance_persistence.TrustedTenantContextPort`
  (``m05_instance_runtime/instance_persistence.py``) — used by ``SqlAlchemyM05UnitOfWork``.

This module implements both, on top of the only supported writer of the RLS settings,
:mod:`prodavan.infrastructure.persistence.rls.context`.  Neither port contract is
modified; the two adapters satisfy them structurally, and every observed fact they
report is measured inside the caller's open transaction.

Why two classes and not one
---------------------------
The two ports declare a *different* ``bind`` (M04 takes a ``MigrationEvidenceContext``
and a ``TenantScopeMode``; M05 takes raw identities plus a ``trusted`` flag), so no
single object can satisfy both protocols.  The shared machinery (principal resolution,
GUC application, readback, policy manifest, previous-value bookkeeping) lives in
:class:`_RlsContextAdapter`; the two public classes only adapt the shapes.

What is reused from ``rls.context`` and what is restated
-------------------------------------------------------
Reused as-is: the setting names (:data:`TENANT_GUCS` and the individual ``*_SETTING``
constants), the policy registry (:mod:`...rls.policies`), the sealed
:class:`TrustedPrincipal` / :class:`CallerAssertion` and every refusal as
:class:`RlsContextError` with its stable reason code.

Restated (not called): the ``set_config(..., true)`` statements and the readback.  The
public helpers of ``rls.context`` either wrap a ``with`` block
(``trusted_tenant_context``) or open and commit their own transaction
(``trusted_tenant_transaction``), and neither shape fits a port whose ``bind`` must
apply the context *without* owning the transaction and *without* an exit hook.  The
duplication is made safe by :func:`_raise_if_settings_drifted`, which fails at import
time if the literal SQL and the shared constants ever disagree.

Session versus factory (the explicit adapter decision)
------------------------------------------------------
``rls.context`` exposes the trustworthy primitives as context managers that either
*own* a transaction (``trusted_tenant_transaction``, factory-based) or wrap a
``with`` block.  Both ports instead call ``bind`` **inside a transaction they already
own** and expect the values to stay applied for the rest of that transaction (they
never call an ``unbind``).  The decision taken here — and the reason this module exists
rather than a thin forwarder — is:

* the adapters accept an **already-open ORM session** and never open, commit or roll
  back a transaction themselves.  A session factory (a callable without ``execute``)
  is refused with ``session_factory_not_accepted`` instead of being opened silently;
* the values are applied with ``set_config(name, value, true)`` — the transaction-local
  equivalent of ``SET LOCAL`` — and read back before any evidence is returned.  A bare
  ``SET``/``SET LOCAL`` statement is never emitted; :func:`_raise_if_not_transaction_local`
  proves that statically at import time and the acceptance test re-checks the text;
* a tenant-scoped bind also writes ``app.trusted_service=''`` and proves it is empty, so a
  host that previously ran a trusted-service bind in the same transaction cannot leave the
  cross-tenant policy reachable for a tenant-scoped step;
* because ``bind`` cannot rely on a ``with`` block, the pre-existing values are captured
  on the first bind and restored by :meth:`clear`, so a caller that keeps using the
  transaction after the migration step can put the connection back exactly as it was.
  ``narrow`` (M04) restores on context-manager exit, as its contract requires.
  ``clear`` is additive (no port declares it) and restores what the *same* adapter
  captured; a port instance is therefore the unit of bookkeeping;
* a repeated ``bind`` in the same database transaction re-applies the same values,
  returns equal evidence and keeps the *original* captured state, so ``clear`` still
  restores the state that preceded the first bind (idempotency, not a second capture).

Trust model
-----------
Neither port carries a server-resolved principal, so caller-supplied identities
(``tenant_id``, ``subject_id``, ``session_id``, ``MigrationEvidenceContext.tenant_id``)
are treated strictly as **assertions**.  They never become the authority.  The values
that reach the database come from a :class:`TrustedPrincipalResolver` — the auth-boundary
lookup that returns the sealed
:class:`~prodavan.infrastructure.persistence.rls.context.TrustedPrincipal` for the
request's correlation id.  When the resolver is absent, returns nothing, returns a
principal for another correlation, or the principal contradicts the assertion, the bind
is refused before a single statement is written.  There is no fallback and no
in-memory tenant.

Consequences that are deliberate
--------------------------------
* the resolver is required at construction time (a port without it can never be
  production-bound, so the assembly fails loudly instead of at first use);
* an expired principal is refused on every bind (``tenant_context_expired``); a
  long-running run must re-resolve rather than pin one principal for hours;
* the RLS policy manifest of the configured relations is re-measured on every bind from
  ``pg_class``/``pg_policies`` and compared with the reviewed registry
  (:data:`~prodavan.infrastructure.persistence.rls.policies.CANONICAL_TABLE_POLICIES`).
  The evidence booleans are observations, never assumptions: a relation whose tenant
  policy is missing, not ``FOR ALL``, permissive, or not written against
  ``nullif(current_setting(..., true), '')`` is reported as ``False`` and the caller
  refuses the step;
* in trusted-service mode (M04 ``MIGRATION_TRUSTED``, M05 ``trusted=True``) the database
  role must be exactly
  :data:`~prodavan.infrastructure.persistence.rls.policies.TRUSTED_SERVICE_ROLE` and the
  reviewed trusted policy must exist on every configured relation, otherwise the bind is
  refused instead of returning a context whose cross-tenant half is unproven;
* ``M05RlsBinding`` has no cabinet/project fields and ``TrustedRuntimeContext`` carries
  no cabinet/project, so the M05 adapter binds **tenant-wide** (empty cabinet/project,
  which the policies read as NULL — never as "any tenant").  ``session_id`` has no
  reviewed GUC: it is validated as an input, never elevated, and the ownership proof
  stays with ``M05IdentityEvidencePort``.  The functional consequence — only tenant-scope
  installations are reachable — is recorded as :data:`M05_SCOPE_CONTRACT_GAP`;
* nothing here authorizes a positive canonical write.  The registry, parent-scope,
  definition/version/instance/artifact gates and the trusted writer epoch remain
  separate requirements.

Secrets
-------
Nothing in this module logs.  There is no ``logging`` import at all: the evidence
objects carry tenant/cabinet/project/subject identities, the database role, an opaque
correlation id and sha256-backed principal evidence — never a credential, token,
payload or SQL parameter dump.  Refusals are raised as
:class:`~prodavan.infrastructure.persistence.rls.context.RlsContextError` with a stable
redacted ``reason_code``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncContextManager, AsyncIterator, Final, Protocol, runtime_checkable
from weakref import WeakKeyDictionary

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.migration.m04_scope.scope_migration import MigrationEvidenceContext
from prodavan.application.migration.m04_scope.scope_persistence import (
    RlsBindingEvidence,
    TenantScopeMode,
)
from prodavan.application.migration.m05_instance_runtime.instance_persistence import (
    M05RlsBinding,
)
from prodavan.infrastructure.persistence.rls import (
    CABINET_SETTING,
    PROJECT_SETTING,
    REGISTRY_VERSION,
    ROLE_SETTING,
    TENANT_GUCS,
    TENANT_SETTING,
    TRUSTED_SERVICE_ROLE,
    TRUSTED_SERVICE_SETTING,
    USER_SETTING,
    CallerAssertion,
    RlsContextError,
    TrustedPrincipal,
)
from prodavan.infrastructure.persistence.rls.policies import (
    CANONICAL_TABLE_POLICIES,
    TablePolicySpec,
)

__all__ = [
    "M04_SCOPE_RELATIONS",
    "M05_RUNTIME_RELATIONS",
    "M05_SCOPE_CONTRACT_GAP",
    "SECURITY_REVIEW_ID",
    "RlsTenantScopeContextAdapter",
    "RlsTrustedTenantContextAdapter",
    "TrustedPrincipalResolver",
]


# ---------------------------------------------------------------------------
# Reason codes raised by these adapters
#
# Reused from ``rls.context`` (same vocabulary, same meaning): ``tenant_context_missing``,
# ``tenant_context_invalid``, ``tenant_context_expired``, ``principal_not_server_resolved``,
# ``cross_tenant_context``, ``transaction_required``, ``postgresql_required``,
# ``database_role_bypasses_rls``, ``row_security_disabled``, ``context_readback_mismatch``,
# ``context_restore_failed``, ``trusted_service_role_mismatch``,
# ``transaction_local_set_forbidden``.
# ---------------------------------------------------------------------------

#: The caller passed a context that is not server-derived or is not usable.
TRUSTED_TRANSACTION_CONTEXT_MISSING: Final = "trusted_transaction_context_missing"
#: The object handed in is not an already-open session (or is not session-like at all).
ORM_SESSION_REQUIRED: Final = "orm_session_required"
#: The object handed in *is* a session factory.  Explicitly refused, never opened here.
SESSION_FACTORY_NOT_ACCEPTED: Final = "session_factory_not_accepted"
#: Construction was attempted without a server-side principal lookup.
PRINCIPAL_RESOLVER_MISSING: Final = "principal_resolver_missing"
#: The resolved principal belongs to a different correlation than the transaction.
CORRELATION_CONTEXT_MISMATCH: Final = "correlation_context_mismatch"
#: The asserted subject contradicts the resolved principal.
SUBJECT_CONTEXT_MISMATCH: Final = "subject_context_mismatch"
#: ``narrow`` was asked to widen (or to change) the existing scope.
TENANT_SCOPE_WIDENED: Final = "tenant_scope_widened"
#: A binding was used against a transaction it was not issued for.
BINDING_TRANSACTION_MISMATCH: Final = "binding_transaction_mismatch"
#: A configured relation carries no reviewed tenant/trusted policy in the registry.
RLS_POLICY_NOT_REVIEWED: Final = "rls_policy_not_reviewed"
#: No relations were configured, so the policy proof would be vacuous.
RLS_RELATIONS_REQUIRED: Final = "rls_relations_required"
#: Trusted-service mode was requested on a relation whose trusted policy is absent.
TRUSTED_SERVICE_POLICY_MISSING: Final = "trusted_service_policy_missing"

#: Identity of the reviewed RLS manifest this module measures itself against.
SECURITY_REVIEW_ID: Final = f"rls-registry:{REGISTRY_VERSION}"

#: Reviewed canonical relations the M04 scope backfill reads and writes.
M04_SCOPE_RELATIONS: Final[tuple[str, ...]] = (
    "scope_owner_tenant_anchors",
    "scope_owner_cabinet_anchors",
    "scope_owner_project_anchors",
    "scope_registry",
    "scope_owner_mappings",
    "scope_owner_quarantine",
    "scope_owner_audit",
)

#: Reviewed canonical relations the M05 instance import/runtime reads and writes.
M05_RUNTIME_RELATIONS: Final[tuple[str, ...]] = (
    "scope_registry",
    "m04e_dynamic_app_definitions",
    "m04e_dynamic_app_versions",
    "m05_plugin_instances",
    "m05_plugin_instance_registry_links",
    "m05_plugin_instance_promotion_evidence",
)

#: Blocker recorded by this implementation, outside its file zone.
#:
#: ``TrustedTenantContextPort.bind`` (``instance_persistence.py:245``) takes only
#: tenant/subject/session/trusted, and ``M05RlsBinding`` (``:218``) has no cabinet/project
#: fields, while the reviewed tenant policy of ``m05_plugin_instances`` reads
#: ``app.cabinet_id`` / ``app.project_id``.  This adapter therefore binds **tenant-wide**
#: (empty scope, which the policies read as NULL).  A tenant-scope installation
#: (``cabinet_id IS NULL``) is covered; a cabinet/project-scoped installation is *not*
#: visible and its writes fail ``WITH CHECK`` — fail-closed, not wide.  Fixing that needs
#: the port contract to carry the installation scope; it cannot be done here without
#: changing a contract this task must leave untouched.
M05_SCOPE_CONTRACT_GAP: Final = "m05_trusted_tenant_context_port_has_no_cabinet_scope"


# ---------------------------------------------------------------------------
# SQL
#
# Every write uses set_config(name, value, true): the third argument makes the value
# transaction-local, which is the only form that cannot leak through the pool.  The
# statements are literal text (no interpolation of values and no batching) so the
# acceptance artifact can assert them character by character.
# ---------------------------------------------------------------------------

_APPLY_TENANT_SQL: Final = (
    "SELECT "
    "set_config('app.tenant_id', :tenant_id, true) AS tenant_id, "
    "set_config('app.cabinet_id', :cabinet_id, true) AS cabinet_id, "
    "set_config('app.project_id', :project_id, true) AS project_id, "
    "set_config('app.user_id', :user_id, true) AS user_id, "
    "set_config('app.role', :role, true) AS role, "
    "set_config('app.trusted_service', '', true) AS trusted_service"
)

_APPLY_TENANT_SERVICE_SQL: Final = (
    "SELECT "
    "set_config('app.tenant_id', :tenant_id, true) AS tenant_id, "
    "set_config('app.cabinet_id', :cabinet_id, true) AS cabinet_id, "
    "set_config('app.project_id', :project_id, true) AS project_id, "
    "set_config('app.user_id', :user_id, true) AS user_id, "
    "set_config('app.role', :role, true) AS role, "
    "set_config('app.trusted_service', 'true', true) AS trusted_service"
)

_SCOPE_SQL: Final = (
    "SELECT "
    "set_config('app.cabinet_id', :cabinet_id, true) AS cabinet_id, "
    "set_config('app.project_id', :project_id, true) AS project_id"
)

_READBACK_SQL: Final = (
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

_RESTORE_SQL: Final = (
    "SELECT "
    "set_config('app.tenant_id', :tenant_id, true) AS tenant_id, "
    "set_config('app.cabinet_id', :cabinet_id, true) AS cabinet_id, "
    "set_config('app.project_id', :project_id, true) AS project_id, "
    "set_config('app.user_id', :user_id, true) AS user_id, "
    "set_config('app.role', :role, true) AS role, "
    "set_config('app.trusted_service', :trusted_service, true) AS trusted_service"
)

_ROLE_SQL: Final = (
    "SELECT r.rolname AS role, r.rolbypassrls AS bypass_rls, r.rolsuper AS superuser "
    "FROM pg_roles AS r WHERE r.rolname = current_user"
)

_DATABASE_TRANSACTION_SQL: Final = "SELECT txid_current()::text AS database_transaction_id"

_MANIFEST_SQL: Final = (
    "SELECT c.relname AS relation, "
    "c.relrowsecurity AS row_security, "
    "c.relforcerowsecurity AS force_row_security, "
    "tenant_policy.policyname AS tenant_policy_name, "
    "tenant_policy.permissive AS tenant_policy_permissive, "
    "tenant_policy.cmd AS tenant_policy_command, "
    "tenant_policy.qual AS tenant_policy_using, "
    "tenant_policy.with_check AS tenant_policy_with_check, "
    "trusted_policy.policyname AS trusted_policy_name "
    "FROM pg_class AS c "
    "JOIN pg_namespace AS n ON n.oid = c.relnamespace AND n.nspname = current_schema() "
    "LEFT JOIN pg_policies AS tenant_policy "
    "ON tenant_policy.schemaname = n.nspname AND tenant_policy.tablename = c.relname "
    "AND tenant_policy.policyname = :tenant_policy "
    "LEFT JOIN pg_policies AS trusted_policy "
    "ON trusted_policy.schemaname = n.nspname AND trusted_policy.tablename = c.relname "
    "AND trusted_policy.policyname = :trusted_policy "
    "WHERE c.relname = :relation AND c.relkind IN ('r', 'p')"
)

#: Statements that write a setting; the static guard below inspects exactly these.
_WRITE_STATEMENTS: Final[tuple[str, ...]] = (
    _APPLY_TENANT_SQL,
    _APPLY_TENANT_SERVICE_SQL,
    _SCOPE_SQL,
    _RESTORE_SQL,
)

#: Settings owned by this module, in capture/restore order (``rls.context`` order plus
#: the trusted-service flag it deliberately keeps separate).
_SETTINGS: Final[tuple[str, ...]] = (*TENANT_GUCS, TRUSTED_SERVICE_SETTING)

#: GUC name -> the column alias :data:`_READBACK_SQL` selects it as
#: (``current_setting('app.tenant_id', true) AS tenant_id``).  The GUC name is *not* a usable
#: readback key: reading the row by GUC name yields ``None`` for every setting, which turned
#: the "capture the previous values" step into "capture six blanks" and made ``clear`` /
#: ``narrow`` overwrite the caller's live context with empty values instead of restoring it.
#: The restore statement's placeholders are those same aliases, so one map serves both.
_READBACK_KEY: Final[dict[str, str]] = {name: name.removeprefix("app.") for name in _SETTINGS}


def _raise_if_not_transaction_local() -> None:
    """Static guard: no session-level ``SET`` may ever be emitted from this module."""

    for statement in _WRITE_STATEMENTS:
        if "set_config(" not in statement:
            raise RlsContextError("transaction_local_set_forbidden", "GUC writes must use set_config")
        if statement.count("set_config(") != statement.count(", true)"):
            raise RlsContextError(
                "transaction_local_set_forbidden", "every set_config call must be transaction-local"
            )
        if statement.strip().upper().startswith("SET "):
            raise RlsContextError("transaction_local_set_forbidden", "bare SET is not allowed")
        if ";" in statement:
            raise RlsContextError("transaction_local_set_forbidden", "statements must not be batched")


def _raise_if_settings_drifted() -> None:
    """Static guard: the literal SQL and the shared registry must name the same GUCs."""

    names = {
        TENANT_SETTING: "app.tenant_id",
        CABINET_SETTING: "app.cabinet_id",
        PROJECT_SETTING: "app.project_id",
        USER_SETTING: "app.user_id",
        ROLE_SETTING: "app.role",
        TRUSTED_SERVICE_SETTING: "app.trusted_service",
    }
    for setting, expected in names.items():
        if setting != expected:
            raise RlsContextError(
                "transaction_local_set_forbidden",
                f"setting {setting!r} drifted from {expected!r}",
            )
    for setting in names:
        if f"'{setting}'" not in _READBACK_SQL:
            raise RlsContextError("transaction_local_set_forbidden", f"{setting} is never read back")
    for setting in (TENANT_SETTING, CABINET_SETTING, PROJECT_SETTING, USER_SETTING, ROLE_SETTING):
        if f"set_config('{setting}'" not in _APPLY_TENANT_SQL:
            raise RlsContextError("transaction_local_set_forbidden", f"{setting} is not applied")


_raise_if_not_transaction_local()
_raise_if_settings_drifted()


# ---------------------------------------------------------------------------
# Server-side principal lookup
# ---------------------------------------------------------------------------


@runtime_checkable
class TrustedPrincipalResolver(Protocol):
    """Auth-boundary lookup of the sealed principal a transaction runs for.

    The implementation is owned by the authentication boundary (session middleware,
    verified bearer JWT, internal mTLS, migration operator).  It must return ``None``
    rather than a guess when the correlation id is unknown, revoked or expired, and it
    must build the principal with
    :func:`~prodavan.infrastructure.persistence.rls.context.issue_server_resolution` /
    :meth:`~prodavan.infrastructure.persistence.rls.context.TrustedPrincipal.from_resolution`.
    """

    async def resolve(self, *, correlation_id: str) -> TrustedPrincipal | None: ...


# ---------------------------------------------------------------------------
# Observed facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PolicyManifestTruth:
    """What the catalog says about the configured relations, right now."""

    row_security_enabled: bool
    row_security_forced: bool
    tenant_policy_enforced: bool
    using_policy_enforced: bool
    with_check_policy_enforced: bool
    missing_context_denies: bool
    typed_parent_policy_enforced: bool
    policy_names: tuple[str, ...]
    relations_without_trusted_policy: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AppliedContext:
    """Result of one successful GUC application inside the caller's transaction."""

    database_transaction_id: str
    database_role: str
    manifest: _PolicyManifestTruth


# ---------------------------------------------------------------------------
# Session/environment validation
# ---------------------------------------------------------------------------


def _require_session(session: object) -> None:
    """Accept only an already-open ORM session; refuse a factory explicitly."""

    if session is None:
        raise RlsContextError(ORM_SESSION_REQUIRED, "an already-open AsyncSession is required")
    for attribute in ("execute", "get_bind", "in_transaction"):
        if callable(getattr(session, attribute, None)):
            continue
        if attribute == "execute" and callable(session):
            raise RlsContextError(
                SESSION_FACTORY_NOT_ACCEPTED,
                "a session factory was passed; this adapter binds an already-open transaction",
            )
        raise RlsContextError(ORM_SESSION_REQUIRED, f"session.{attribute}() is required")


def _dialect_name(session: object) -> str:
    try:
        bind = session.get_bind()  # type: ignore[attr-defined]
        return str(bind.dialect.name)
    except AttributeError as exc:  # pragma: no cover - defensive
        raise RlsContextError(ORM_SESSION_REQUIRED, f"session has no dialect: {exc}") from exc


def _in_transaction(session: object) -> bool:
    return bool(session.in_transaction())  # type: ignore[attr-defined]


def _require_environment(session: object) -> None:
    if _dialect_name(session) != "postgresql":
        raise RlsContextError(
            "postgresql_required",
            "transaction-local tenant context is only defined for PostgreSQL",
        )
    if not _in_transaction(session):
        raise RlsContextError(
            "transaction_required",
            "tenant context must be applied inside an explicit transaction, never on a session",
        )


async def _one(
    session: object, statement: str, parameters: Mapping[str, object] | None = None
) -> dict[str, object]:
    result = await session.execute(text(statement), dict(parameters or {}))  # type: ignore[attr-defined]
    return dict(result.mappings().one())


async def _maybe_one(
    session: object, statement: str, parameters: Mapping[str, object] | None = None
) -> dict[str, object] | None:
    """Like :func:`_one`, but an absent row (a missing relation) is ``None``, not an error."""

    result = await session.execute(text(statement), dict(parameters or {}))  # type: ignore[attr-defined]
    row = result.mappings().first()
    return None if row is None else dict(row)


# ---------------------------------------------------------------------------
# Setting application, readback and restore
# ---------------------------------------------------------------------------


def _parameters(principal: TrustedPrincipal, *, include_scope: bool) -> dict[str, str]:
    """Values written to the GUCs.  Only principal-derived values are authoritative."""

    return {
        "tenant_id": principal.tenant_id,
        "cabinet_id": (principal.cabinet_id or "") if include_scope else "",
        "project_id": (principal.project_id or "") if include_scope else "",
        "user_id": principal.subject_id,
        "role": principal.role,
    }


def _previous_values(readback: Mapping[str, object]) -> dict[str, str]:
    """Capture the settings live before this context was applied, keyed by GUC name.

    The readback row is keyed by column alias, not by GUC name: :data:`_READBACK_KEY` is
    what makes the capture observe real values instead of ``None``.
    """

    return {name: str(readback.get(name) or "") for name in _SETTINGS}


async def _read_previous(session: object) -> dict[str, str]:
    return _previous_values(await _one(session, _READBACK_SQL))


async def _apply_settings(
    session: object, parameters: Mapping[str, str], *, trusted_service: bool
) -> None:
    statement = _APPLY_TENANT_SERVICE_SQL if trusted_service else _APPLY_TENANT_SQL
    await _one(session, statement, parameters)


async def _apply_scope(session: object, *, cabinet_id: str | None, project_id: str | None) -> None:
    await _one(
        session,
        _SCOPE_SQL,
        {"cabinet_id": cabinet_id or "", "project_id": project_id or ""},
    )


async def _restore(session: object, previous: Mapping[str, str]) -> None:
    """Re-apply captured settings by their statement parameter names, transaction-locally."""

    await _one(
        session,
        _RESTORE_SQL,
        {_READBACK_KEY[name]: previous.get(name, "") for name in _SETTINGS},
    )


def _verify_readback(
    readback: Mapping[str, object],
    expected: Mapping[str, str],
    *,
    trusted_service: bool,
) -> None:
    for name, value in expected.items():
        if str(readback.get(name) or "") != value:
            raise RlsContextError(
                "context_readback_mismatch", f"{name} was not applied transaction-locally"
            )
    if str(readback.get("row_security") or "").lower() != "on":
        raise RlsContextError(
            "row_security_disabled",
            "row_security is not on; RLS would be skipped for this transaction",
        )
    if trusted_service:
        if str(readback.get("trusted_service") or "") != "true":
            raise RlsContextError(
                "context_readback_mismatch", "the trusted service flag was not applied"
            )
    elif str(readback.get("trusted_service") or "") != "":
        # A leftover ``app.trusted_service='true'`` from an earlier bind in the same
        # transaction would keep the cross-tenant policy reachable for a tenant-scoped
        # step.  The non-trusted path clears it and proves it is gone.
        raise RlsContextError(
            "context_readback_mismatch",
            "a trusted service flag survived a tenant-scoped bind",
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


# ---------------------------------------------------------------------------
# Reviewed policy manifest
# ---------------------------------------------------------------------------


def _reviewed_spec(relation: str) -> TablePolicySpec:
    """The reviewed policy spec of ``relation``; an unknown relation is refused."""

    for spec in CANONICAL_TABLE_POLICIES:
        if spec.table == relation:
            return spec
    raise RlsContextError(
        RLS_POLICY_NOT_REVIEWED,
        f"relation {relation!r} carries no reviewed tenant/trusted policy",
    )


def _validated_relations(relations: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for relation in relations:
        if not isinstance(relation, str) or not relation.strip():
            raise RlsContextError(RLS_RELATIONS_REQUIRED, "relation names must be non-empty strings")
        if relation not in ordered:
            ordered.append(relation)
    if not ordered:
        raise RlsContextError(
            RLS_RELATIONS_REQUIRED,
            "at least one reviewed relation is required; an empty manifest proves nothing",
        )
    for relation in ordered:
        _reviewed_spec(relation)
    return tuple(ordered)


def _gucs_for(spec: TablePolicySpec) -> tuple[str, ...]:
    """The GUCs the reviewed tenant predicate of ``spec`` reads."""

    if spec.tenant is None:
        return ()
    names = [TENANT_SETTING]
    if spec.cabinet is not None:
        names.append(CABINET_SETTING)
    if spec.project is not None:
        names.append(PROJECT_SETTING)
    return tuple(names)


def _normalize_predicate(predicate: object) -> str:
    """Collapse whitespace and drop the ``::text`` cast Postgres adds when deparsing.

    ``pg_policies.qual`` is a deparsed expression, not the DDL text, so the comparison
    normalizes instead of matching the source string literally.
    """

    collapsed = " ".join(str(predicate or "").split())
    return collapsed.replace("::text", "").replace('"', "").lower()


def _denies_without_context(predicate: str, gucs: Sequence[str]) -> bool:
    """True when the predicate is false for an unset or empty context.

    A table without a tenant anchor renders ``false``; every other reviewed predicate
    wraps each ``current_setting(name, true)`` in ``nullif(..., '')`` so that *unset* and
    *empty* both make the comparison NULL, and NULL never passes a policy.
    """

    if not gucs:
        return predicate.strip() == "false"
    return all(f"nullif(current_setting('{name}', true), '')" in predicate for name in gucs)


async def _observe_manifest(
    session: object, specs: Sequence[TablePolicySpec]
) -> _PolicyManifestTruth:
    """Measure the tenant policies of ``specs`` against the reviewed registry."""

    row_security_enabled = True
    row_security_forced = True
    tenant_policy_enforced = True
    using_policy_enforced = True
    with_check_policy_enforced = True
    missing_context_denies = True
    typed_parent_policy_enforced = True
    policy_names: list[str] = []
    without_trusted_policy: list[str] = []

    for spec in specs:
        row = await _maybe_one(
            session,
            _MANIFEST_SQL,
            {
                "relation": spec.table,
                "tenant_policy": spec.tenant_policy,
                "trusted_policy": spec.trusted_policy,
            },
        ) or {}
        present = row.get("relation") == spec.table
        using = _normalize_predicate(row.get("tenant_policy_using"))
        with_check = _normalize_predicate(row.get("tenant_policy_with_check"))
        gucs = _gucs_for(spec)
        denies = _denies_without_context(using, gucs) and _denies_without_context(with_check, gucs)
        policy_named = present and row.get("tenant_policy_name") == spec.tenant_policy
        command_ok = policy_named and row.get("tenant_policy_command") == "ALL"
        permissive_ok = policy_named and row.get("tenant_policy_permissive") == "PERMISSIVE"

        row_security_enabled = row_security_enabled and present and bool(row.get("row_security"))
        row_security_forced = row_security_forced and present and bool(
            row.get("force_row_security")
        )
        using_policy_enforced = using_policy_enforced and command_ok and bool(
            row.get("tenant_policy_using")
        )
        with_check_policy_enforced = with_check_policy_enforced and command_ok and bool(
            row.get("tenant_policy_with_check")
        )
        missing_context_denies = missing_context_denies and denies
        tenant_policy_enforced = tenant_policy_enforced and bool(
            command_ok and permissive_ok and denies
        )
        if spec.tenant is not None and spec.tenant.chain:
            typed_parent_policy_enforced = typed_parent_policy_enforced and (
                "exists (select 1 from" in using
            )

        if policy_named:
            policy_names.append(spec.tenant_policy)
        if present and row.get("trusted_policy_name") == spec.trusted_policy:
            policy_names.append(spec.trusted_policy)
        else:
            without_trusted_policy.append(spec.table)

    return _PolicyManifestTruth(
        row_security_enabled=row_security_enabled,
        row_security_forced=row_security_forced,
        tenant_policy_enforced=tenant_policy_enforced,
        using_policy_enforced=using_policy_enforced,
        with_check_policy_enforced=with_check_policy_enforced,
        missing_context_denies=missing_context_denies,
        typed_parent_policy_enforced=typed_parent_policy_enforced,
        policy_names=tuple(policy_names),
        relations_without_trusted_policy=tuple(without_trusted_policy),
    )


def _refuse_when_trusted_policy_is_absent(manifest: _PolicyManifestTruth) -> None:
    if manifest.relations_without_trusted_policy:
        raise RlsContextError(
            TRUSTED_SERVICE_POLICY_MISSING,
            "no reviewed trusted policy on: " + ", ".join(manifest.relations_without_trusted_policy),
        )


# ---------------------------------------------------------------------------
# Shared adapter machinery
# ---------------------------------------------------------------------------


class _RlsContextAdapter:
    """Shared machinery: resolver gating, GUC application, readback, policy proof.

    Instantiated only through the two public adapters.  It owns the per-session record of
    pre-existing settings so that a repeated ``bind`` keeps the *original* capture and
    ``clear`` restores exactly that.
    """

    def __init__(
        self,
        *,
        principal_resolver: TrustedPrincipalResolver,
        relations: Sequence[str],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if principal_resolver is None:
            raise ValueError(
                "a server-side principal resolver is required; caller-supplied tenant ids "
                "never become authority and there is no fallback"
            )
        if not callable(getattr(principal_resolver, "resolve", None)):
            raise ValueError("the principal resolver must expose resolve(correlation_id=...)")
        self._relations = _validated_relations(relations)
        self._specs: tuple[TablePolicySpec, ...] = tuple(
            _reviewed_spec(relation) for relation in self._relations
        )
        self._principal_resolver = principal_resolver
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # session -> (database transaction id, settings captured before the first bind)
        self._previous: WeakKeyDictionary[object, tuple[str, tuple[tuple[str, str], ...]]] = (
            WeakKeyDictionary()
        )

    # -- introspection -----------------------------------------------------

    @property
    def relations(self) -> tuple[str, ...]:
        return self._relations

    # -- principal ---------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock()

    async def _resolve_principal(self, *, correlation_id: str) -> TrustedPrincipal:
        if not isinstance(correlation_id, str) or not correlation_id:
            raise RlsContextError(
                "tenant_context_missing", "a correlation id is required to resolve the principal"
            )
        resolver = self._principal_resolver
        if resolver is None:  # pragma: no cover - guarded at construction
            raise RlsContextError(PRINCIPAL_RESOLVER_MISSING, "no principal resolver is configured")
        principal = await resolver.resolve(correlation_id=correlation_id)
        if principal is None:
            raise RlsContextError(
                "principal_not_server_resolved",
                "the resolver returned no server-side principal for this correlation",
            )
        if not isinstance(principal, TrustedPrincipal):
            raise RlsContextError(
                "principal_not_server_resolved",
                "the resolver must return the sealed TrustedPrincipal",
            )
        if principal.correlation_id != correlation_id:
            raise RlsContextError(
                CORRELATION_CONTEXT_MISMATCH,
                "the resolved principal belongs to another correlation id",
            )
        principal.validate(now=self._now())
        return principal

    @staticmethod
    def _reject_contradicting_assertion(
        principal: TrustedPrincipal, assertion: CallerAssertion
    ) -> None:
        if not isinstance(assertion, CallerAssertion):
            raise RlsContextError(
                "tenant_context_invalid", "caller identities must be a CallerAssertion"
            )
        disagreements = assertion.disagreements(principal)
        if disagreements:
            raise RlsContextError(
                "cross_tenant_context",
                "request claims contradict the resolved scope: " + ", ".join(disagreements),
            )

    # -- application -------------------------------------------------------

    async def _remember_previous(
        self, session: object, database_transaction_id: str
    ) -> dict[str, str]:
        """Capture pre-existing settings once per transaction; a repeat keeps the first."""

        recorded = self._previous.get(session)
        if recorded is not None and recorded[0] == database_transaction_id:
            return dict(recorded[1])
        previous = await _read_previous(session)
        self._previous[session] = (
            database_transaction_id,
            tuple((name, previous[name]) for name in _SETTINGS),
        )
        return previous

    async def _apply_tenant_context(
        self,
        session: object,
        *,
        principal: TrustedPrincipal,
        include_scope: bool,
        trusted_service: bool,
    ) -> _AppliedContext:
        """Apply, prove and measure the context inside the caller's open transaction."""

        _require_session(session)
        _require_environment(session)

        database_role = _check_database_role(
            await _one(session, _ROLE_SQL), trusted_service=trusted_service
        )
        manifest = await _observe_manifest(session, self._specs)
        if trusted_service:
            _refuse_when_trusted_policy_is_absent(manifest)

        database_transaction_id = await self._database_transaction_id(session)
        await self._remember_previous(session, database_transaction_id)

        parameters = _parameters(principal, include_scope=include_scope)
        await _apply_settings(session, parameters, trusted_service=trusted_service)
        _verify_readback(
            await _one(session, _READBACK_SQL),
            parameters,
            trusted_service=trusted_service,
        )
        return _AppliedContext(
            database_transaction_id=database_transaction_id,
            database_role=database_role,
            manifest=manifest,
        )

    @staticmethod
    async def _database_transaction_id(session: object) -> str:
        row = await _one(session, _DATABASE_TRANSACTION_SQL)
        transaction_id = str(row.get("database_transaction_id") or "")
        if not transaction_id:
            raise RlsContextError(
                "tenant_context_invalid", "the database transaction identity is unavailable"
            )
        return transaction_id

    async def _restore_previous(self, session: object, previous: Mapping[str, str]) -> None:
        await _restore(session, previous)
        if _previous_values(await _one(session, _READBACK_SQL)) != dict(previous):
            raise RlsContextError(
                "context_restore_failed", "the previous tenant context was not restored"
            )


# ---------------------------------------------------------------------------
# M04 — TenantScopeContextPort
# ---------------------------------------------------------------------------


class RlsTenantScopeContextAdapter:
    """``TenantScopeContextPort`` for the M04 scope backfill, over ``rls.context``.

    ``bind`` applies the tenant/cabinet/project/user/role GUCs of the *resolved*
    principal into the caller's open transaction and returns
    :class:`~prodavan.application.migration.m04_scope.scope_persistence.RlsBindingEvidence`
    built from measurements of that transaction.  It does not open, commit or roll back a
    transaction; M04 owns the unit of work.

    ``application_transaction_id`` is the identity M04 handed in
    (``MigrationEvidenceContext.transaction_id``) and is attested only after the context
    passed the trust checks; the binding to *this* database transaction is
    ``database_transaction_id``, read back with ``txid_current()``.  M04 compares both, so
    an application transaction cannot be served by a context bound to another connection.

    ``narrow`` pins the cabinet/project part of an existing binding for the duration of a
    ``with`` block and restores the previous cabinet/project on exit.  Widening (dropping
    a pinned cabinet, or replacing it with another one) and any tenant change are refused.
    """

    def __init__(
        self,
        *,
        principal_resolver: TrustedPrincipalResolver,
        relations: Sequence[str] = M04_SCOPE_RELATIONS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._core = _RlsContextAdapter(
            principal_resolver=principal_resolver, relations=relations, clock=clock
        )

    @property
    def relations(self) -> tuple[str, ...]:
        return self._core.relations

    async def bind(
        self,
        *,
        session: AsyncSession,
        context: MigrationEvidenceContext,
        mode: TenantScopeMode,
    ) -> RlsBindingEvidence:
        # Fail on the environment before any lookup or statement: a context can only be
        # applied inside an explicit, PostgreSQL transaction owned by the caller.
        _require_session(session)
        _require_environment(session)
        if not isinstance(context, MigrationEvidenceContext):
            raise RlsContextError(
                TRUSTED_TRANSACTION_CONTEXT_MISSING, "a MigrationEvidenceContext is required"
            )
        if not context.usable:
            raise RlsContextError(
                TRUSTED_TRANSACTION_CONTEXT_MISSING,
                "the migration evidence context is not server-derived, complete or usable",
            )
        if not isinstance(mode, TenantScopeMode):
            raise RlsContextError("tenant_context_invalid", "mode must be a TenantScopeMode")
        rls_context = context.rls_context
        if rls_context is None:  # pragma: no cover - excluded by ``usable``
            raise RlsContextError(
                TRUSTED_TRANSACTION_CONTEXT_MISSING, "the resolved RLS context is missing"
            )

        trusted_service = mode is TenantScopeMode.MIGRATION_TRUSTED
        principal = await self._core._resolve_principal(
            correlation_id=str(context.correlation_id)
        )
        self._core._reject_contradicting_assertion(
            principal,
            CallerAssertion(
                tenant_id=context.tenant_id,
                cabinet_id=rls_context.cabinet_id,
                project_id=rls_context.project_id,
            ),
        )
        if principal.subject_id != context.user_id:
            raise RlsContextError(
                SUBJECT_CONTEXT_MISMATCH,
                "the asserted subject contradicts the resolved principal",
            )
        if principal.role != context.role:
            raise RlsContextError(
                SUBJECT_CONTEXT_MISMATCH, "the asserted role contradicts the resolved principal"
            )

        applied = await self._core._apply_tenant_context(
            session,
            principal=principal,
            include_scope=True,
            trusted_service=trusted_service,
        )
        return RlsBindingEvidence(
            application_transaction_id=str(context.transaction_id),
            database_transaction_id=applied.database_transaction_id,
            tenant_id=principal.tenant_id,
            cabinet_id=principal.cabinet_id,
            project_id=principal.project_id,
            database_role=applied.database_role,
            security_review_id=SECURITY_REVIEW_ID,
            policy_names=applied.manifest.policy_names,
            trusted_service=trusted_service,
            row_security_enabled=applied.manifest.row_security_enabled,
            row_security_forced=applied.manifest.row_security_forced,
            tenant_policy_enforced=applied.manifest.tenant_policy_enforced,
            using_policy_enforced=applied.manifest.using_policy_enforced,
            with_check_policy_enforced=applied.manifest.with_check_policy_enforced,
            missing_context_denies=applied.manifest.missing_context_denies,
            role_not_bypass_rls=True,
            typed_parent_policy_enforced=applied.manifest.typed_parent_policy_enforced,
        )

    def narrow(
        self,
        *,
        session: AsyncSession,
        binding: RlsBindingEvidence,
        cabinet_id: str | None,
        project_id: str | None,
    ) -> AsyncContextManager[RlsBindingEvidence]:
        return self._narrow(
            session=session,
            binding=binding,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )

    @asynccontextmanager
    async def _narrow(
        self,
        *,
        session: object,
        binding: RlsBindingEvidence,
        cabinet_id: str | None,
        project_id: str | None,
    ) -> AsyncIterator[RlsBindingEvidence]:
        if not isinstance(binding, RlsBindingEvidence):
            raise RlsContextError(
                "tenant_context_invalid", "a RlsBindingEvidence produced by bind is required"
            )
        _require_session(session)
        _require_environment(session)
        current_transaction = await self._core._database_transaction_id(session)
        if binding.database_transaction_id != current_transaction:
            raise RlsContextError(
                BINDING_TRANSACTION_MISMATCH,
                "the binding was issued for another database transaction",
            )
        if project_id is not None and cabinet_id is None:
            raise RlsContextError(
                "tenant_context_invalid", "a project scope requires an explicit cabinet scope"
            )
        # Narrowing only: an existing anchor may be pinned further, never dropped or moved.
        if binding.cabinet_id is not None and cabinet_id != binding.cabinet_id:
            raise RlsContextError(
                TENANT_SCOPE_WIDENED,
                "the existing cabinet binding may not be dropped, replaced or cleared",
            )
        if binding.project_id is not None and project_id != binding.project_id:
            raise RlsContextError(
                TENANT_SCOPE_WIDENED,
                "the existing project binding may not be dropped, replaced or cleared",
            )

        previous = await _read_previous(session)
        await _apply_scope(session, cabinet_id=cabinet_id, project_id=project_id)
        readback = await _one(session, _READBACK_SQL)
        if str(readback.get("tenant_id") or "") != binding.tenant_id:
            raise RlsContextError(
                TENANT_SCOPE_WIDENED, "narrowing must never change the tenant"
            )
        if str(readback.get("cabinet_id") or "") != (cabinet_id or ""):
            raise RlsContextError(
                "context_readback_mismatch", "the narrowed cabinet was not applied"
            )
        if str(readback.get("project_id") or "") != (project_id or ""):
            raise RlsContextError(
                "context_readback_mismatch", "the narrowed project was not applied"
            )
        try:
            yield _narrowed_binding(binding, cabinet_id=cabinet_id, project_id=project_id)
        except BaseException:
            raise
        else:
            await self._core._restore_previous(session, previous)

    async def clear(self, *, session: AsyncSession) -> None:
        """Restore the settings that preceded the first ``bind`` of *this* adapter.

        Idempotent: a second call, a different database transaction, a session that never
        bound through this adapter, or a transaction that already ended is a no-op — the
        transaction keeps ownership of commit/rollback and the settings die with it.
        """

        _require_session(session)
        recorded = self._core._previous.pop(session, None)
        if recorded is None:
            return
        transaction_id, previous = recorded
        if not _in_transaction(session):
            return
        if await self._core._database_transaction_id(session) != transaction_id:
            return
        await self._core._restore_previous(session, dict(previous))


def _narrowed_binding(
    binding: RlsBindingEvidence, *, cabinet_id: str | None, project_id: str | None
) -> RlsBindingEvidence:
    """The same transaction facts with the narrowed anchors; nothing else may change."""

    return RlsBindingEvidence(
        application_transaction_id=binding.application_transaction_id,
        database_transaction_id=binding.database_transaction_id,
        tenant_id=binding.tenant_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
        database_role=binding.database_role,
        security_review_id=binding.security_review_id,
        policy_names=binding.policy_names,
        trusted_service=binding.trusted_service,
        row_security_enabled=binding.row_security_enabled,
        row_security_forced=binding.row_security_forced,
        tenant_policy_enforced=binding.tenant_policy_enforced,
        using_policy_enforced=binding.using_policy_enforced,
        with_check_policy_enforced=binding.with_check_policy_enforced,
        missing_context_denies=binding.missing_context_denies,
        role_not_bypass_rls=binding.role_not_bypass_rls,
        typed_parent_policy_enforced=binding.typed_parent_policy_enforced,
    )


# ---------------------------------------------------------------------------
# M05 — TrustedTenantContextPort
# ---------------------------------------------------------------------------


class RlsTrustedTenantContextAdapter:
    """``TrustedTenantContextPort`` for the M05 instance runtime, over ``rls.context``.

    ``bind`` takes the identities ``SqlAlchemyM05UnitOfWork`` already validated as
    present but which are still only *assertions*: the tenant and subject are compared
    with the principal resolved from ``correlation_id``, and only the resolved values are
    written.  The M05 binding is tenant-wide — ``TrustedRuntimeContext`` carries no
    cabinet/project and ``M05RlsBinding`` has no fields for them — so those GUCs are set
    empty, which every reviewed policy reads as NULL, never as "any cabinet".

    ``session_id`` has no reviewed GUC.  It is accepted, checked for shape and otherwise
    not elevated: session ownership is proven by ``M05IdentityEvidencePort``
    (``prove_subject_session``), not by this adapter.

    Known limitation, recorded as :data:`M05_SCOPE_CONTRACT_GAP`: because the port and the
    binding cannot express a cabinet/project scope, only tenant-scope installations are
    reachable.  A cabinet- or project-scoped installation stays invisible (and its writes
    are rejected by ``WITH CHECK``) until the port contract carries the installation scope.
    ``M05_SCOPE_CONTRACT_GAP`` is exported so the composition root can surface it instead of
    silently running a narrower context.
    """

    def __init__(
        self,
        *,
        principal_resolver: TrustedPrincipalResolver,
        relations: Sequence[str] = M05_RUNTIME_RELATIONS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._core = _RlsContextAdapter(
            principal_resolver=principal_resolver, relations=relations, clock=clock
        )

    @property
    def relations(self) -> tuple[str, ...]:
        return self._core.relations

    async def bind(
        self,
        *,
        session: AsyncSession,
        tenant_id: str,
        subject_id: str,
        session_id: str,
        correlation_id: str,
        trusted: bool,
    ) -> M05RlsBinding:
        # Fail on the environment before any lookup or statement (see the M04 adapter).
        _require_session(session)
        _require_environment(session)
        if trusted is not True and trusted is not False:
            raise RlsContextError(
                "tenant_context_invalid", "trusted must be an explicit bool, not a truthy value"
            )
        for name, value in (
            ("tenant_id", tenant_id),
            ("subject_id", subject_id),
            ("session_id", session_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise RlsContextError("tenant_context_missing", f"{name} is required")

        principal = await self._core._resolve_principal(correlation_id=correlation_id)
        self._core._reject_contradicting_assertion(
            principal, CallerAssertion(tenant_id=tenant_id)
        )
        if principal.subject_id != subject_id:
            raise RlsContextError(
                SUBJECT_CONTEXT_MISMATCH,
                "the asserted subject contradicts the resolved principal",
            )

        applied = await self._core._apply_tenant_context(
            session,
            principal=principal,
            include_scope=False,
            trusted_service=trusted,
        )
        return M05RlsBinding(
            tenant_id=principal.tenant_id,
            correlation_id=principal.correlation_id,
            database_transaction_id=applied.database_transaction_id,
            row_security_enabled=applied.manifest.row_security_enabled,
            row_security_forced=applied.manifest.row_security_forced,
            tenant_policy_enforced=applied.manifest.tenant_policy_enforced,
            using_policy_enforced=applied.manifest.using_policy_enforced,
            with_check_policy_enforced=applied.manifest.with_check_policy_enforced,
            missing_context_denies=applied.manifest.missing_context_denies,
            role_not_bypass_rls=True,
            trusted_service=trusted,
        )

    async def clear(self, *, session: AsyncSession) -> None:
        """Restore the settings that preceded the first ``bind`` of *this* adapter.

        Same no-op rules as the M04 adapter: repeated, foreign-transaction, never-bound and
        already-ended cases restore nothing and emit no SQL.
        """

        _require_session(session)
        recorded = self._core._previous.pop(session, None)
        if recorded is None:
            return
        transaction_id, previous = recorded
        if not _in_transaction(session):
            return
        if await self._core._database_transaction_id(session) != transaction_id:
            return
        await self._core._restore_previous(session, dict(previous))

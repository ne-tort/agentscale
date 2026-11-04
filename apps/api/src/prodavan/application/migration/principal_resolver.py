"""Auth-boundary implementation of ``TrustedPrincipalResolver``.

The RLS tenant-context adapters (``application/migration/tenant_context.py``) refuse to
write a single GUC unless a *server-side* lookup turns a correlation id into a sealed
:class:`~prodavan.infrastructure.persistence.rls.context.TrustedPrincipal`.  This module is
that lookup: the last collaborator between the migration chain (M04 -> M06) and a
production binding.

Two authorities, and nothing else
---------------------------------

A principal is built from exactly two server-side answers:

1. **Who is calling** — a credential the authentication boundary already verified.  The
   repository authenticates with a JWKS-verified bearer JWT
   (``infrastructure/auth/jwt.py`` -> ``api/deps.py:get_principal``); the same validator is
   used here, so a forged or expired token can never name a subject.  The credential is
   supplied by the auth boundary as a :class:`VerifiedCredential`; the correlation id only
   *selects* which verified credential is in play, it never carries identity.
2. **What that subject is entitled to** — ``employees`` + ``memberships`` + ``companies``
   + ``cabinet_instances``/``cabinet_company_grants`` + ``projects`` read through
   :class:`SqlTenantIdentityDirectory`.  Tenant, cabinet, project and role all come from
   those rows.  A claim that the credential *asserts* (``asserted_tenant_id`` and friends)
   is only a key into the membership set and a filter for the scope: it never becomes the
   value written to ``app.tenant_id``/``app.cabinet_id``/``app.project_id``.

Refuse, never guess
-------------------

Every negative answer is ``None``, as the protocol demands, and never an exception:

* unknown correlation id, no verified credential, expired credential, revoked session ->
  ``None``;
* no ``employees`` row for the verified subject, disabled/invited employee -> ``None``;
* no membership, deleted or non-active company, membership in another tenant -> ``None``;
* a project/cabinet that does not belong to the resolved tenant, an inactive project or
  cabinet, a revoked cabinet assignment -> ``None``;
* several memberships and no asserted tenant -> ``None``; picking one would be the guess
  this module exists to prevent;
* an identity the RLS vocabulary cannot represent (an id longer than 40 characters, a role
  outside the identifier alphabet) -> ``None`` with ``principal_context_unrepresentable``.

Only "the server has an authoritative answer and it is negative" becomes ``None``.  A
database failure is *not* an answer: it propagates, because a resolver that silently
returns ``None`` on an outage turns a broken database into a pile of
``principal_not_server_resolved`` denials that look like revoked access.

The seal is never bypassed
--------------------------

The principal is created through
:func:`~prodavan.infrastructure.persistence.rls.context.issue_server_resolution` and
:meth:`~prodavan.infrastructure.persistence.rls.context.TrustedPrincipal.from_resolution`
only.  Constructing ``TrustedPrincipal`` directly is refused by that module, and this one
does not try.

Two credential sources, one answer
---------------------------------

The protocol speaks about *correlation ids*, which is the right shape for a worker or a
long-lived operator session: a durable record ``correlation_id -> verified subject`` that
outlives the request.  ``agent_sessions`` is chat session state (``projects``/provider/model,
no credential) and ``identity_links`` maps an employee to an external IdP subject
(``provider``, ``provider_subject``, no lifetime, no revocation, no correlation), so neither
could serve as that record.  The relation is ``auth_sessions`` — declared by
``infrastructure/persistence/models/auth_sessions.py``, created by revision ``2026110317``
and specified by :data:`AUTH_SESSION_CORRELATION_GAP` — and it is written by the auth
boundary (``application/auth/auth_sessions.py``) after the credential was verified.

:class:`SqlAuthSessionCredentialSource` reads it.  That is the source a **worker, a
background job or a second replica** uses: it holds no token, only the correlation id the
auth boundary issued in the request that started the work.  The row is the authority for
«which verified subject this correlation id belongs to»; its ``expires_at`` is the
credential's own expiry, and a row that is expired, revoked or absent resolves to ``None``.

:class:`BearerJwtCredentialSource` stays the **request path**: it re-verifies the bearer
token through the repository's JWKS validator on every call and answers for exactly one
correlation id — the one the auth boundary issued for the request that carried the token.
There is no cache and no process-wide map, so a correlation id belonging to another request
cannot select this credential.

Neither source caches anything, and the durable one is not a fallback for the other: the
composition root picks the source by *where the caller runs* (request -> bearer, process ->
SQL) instead of trying one and then the other, because «try the token, else look it up» would
let a caller pass a correlation id it never proved in order to reach the stored subject.  A
correlation id nothing recorded — no row — still resolves to ``None`` and the tenant-context
bind refuses.

`Resolved by` evidence
----------------------

``resolved_by`` records which boundary path produced the answer, and ``resolution_source``
is one of the four values ``rls.context`` accepts.  Both are derived from the credential's
source, never from caller input: a caller cannot promote its own resolution to
``migration_operator`` (that source additionally requires the ``migration_role`` role,
which this module never assigns).
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Protocol

from sqlalchemy import select

from prodavan.domain.cabinets.types import (
    CabinetAssignmentStatus,
    CabinetCompanyGrantScope,
    CabinetGrantStatus,
    CabinetOwnerScope,
    CabinetStatus,
)
from prodavan.domain.identity import EmployeeStatus
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.persistence.rls.context import (
    SERVER_RESOLUTION_SOURCES,
    RlsContextError,
    TrustedPrincipal,
    issue_server_resolution,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVE_COMPANY_STATUS",
    "AUTH_SESSION_CORRELATION_GAP",
    "DEFAULT_MAX_PRINCIPAL_TTL",
    "AuthBoundaryPrincipalResolver",
    "BearerJwtCredentialSource",
    "PrincipalCredentialSource",
    "PrincipalRefusal",
    "PrincipalRefusalRecord",
    "PrincipalRefusalSink",
    "SchemaGap",
    "SqlAuthSessionCredentialSource",
    "SqlTenantIdentityDirectory",
    "TenantIdentity",
    "TenantIdentityDirectory",
    "TenantIdentityRefusal",
    "TokenVerifier",
    "VerifiedCredential",
    "build_durable_principal_resolver",
    "build_sql_principal_resolver",
    "credential_from_claims",
]

#: The only ``companies.status`` value a tenant binding may be issued for.  ``companies``
#: also carries ``paused`` (a restored-but-not-reactivated company) and ``purged``; both
#: withhold the principal rather than binding a tenant nobody re-activated.  Deletion is
#: expressed separately by ``companies.deleted_at``.
ACTIVE_COMPANY_STATUS: Final = "active"

#: Longest lifetime this resolver will put on a principal, on top of the credential's own
#: expiry.  A token minted with a long ``exp`` must not produce a long-lived sealed
#: principal: the tenant context is re-resolved per transaction, and a short ceiling keeps
#: a stale membership from outliving its revocation by more than one interval.
DEFAULT_MAX_PRINCIPAL_TTL: Final = timedelta(minutes=5)

#: ``resolution_source`` -> the boundary path recorded in ``TrustedPrincipal.resolved_by``.
#: Kept here (not in the caller) so the evidence cannot be chosen by whoever resolves.
_RESOLVED_BY: Final[Mapping[str, str]] = {
    "session_cookie": "auth.session_middleware",
    "bearer_jwt_verified": "auth.bearer_jwt",
    "internal_service_mtls": "auth.internal_mtls",
    "migration_operator": "auth.migration_operator",
}


# ---------------------------------------------------------------------------
# Redacted refusal vocabulary
# ---------------------------------------------------------------------------


class PrincipalRefusal(StrEnum):
    """Why a correlation id produced no principal.

    Returned to nobody (the protocol answers ``None``), recorded for operators through
    :class:`PrincipalRefusalSink`.  Reason codes only: a refusal record never carries a
    token, a claim payload or a database row.
    """

    CORRELATION_UNKNOWN = "principal_correlation_unknown"
    CORRELATION_MISMATCH = "principal_correlation_mismatch"
    CORRELATION_UNREPRESENTABLE = "principal_correlation_unrepresentable"
    CREDENTIAL_UNVERIFIED = "principal_credential_unverified"
    CREDENTIAL_EXPIRED = "principal_credential_expired"
    CREDENTIAL_REVOKED = "principal_credential_revoked"
    EXPIRY_NOT_IN_FUTURE = "principal_expiry_not_in_future"
    SUBJECT_UNBOUND = "principal_subject_unbound"
    SUBJECT_INACTIVE = "principal_subject_inactive"
    TENANT_AMBIGUOUS = "principal_tenant_ambiguous"
    TENANT_INACTIVE = "principal_tenant_inactive"
    MEMBERSHIP_REVOKED = "principal_membership_revoked"
    CABINET_NOT_GRANTED = "principal_cabinet_not_granted"
    CABINET_INACTIVE = "principal_cabinet_inactive"
    PROJECT_NOT_IN_SCOPE = "principal_project_not_in_scope"
    PROJECT_INACTIVE = "principal_project_inactive"
    SCOPE_CONTRADICTION = "principal_scope_contradiction"
    CONTEXT_UNREPRESENTABLE = "principal_context_unrepresentable"


@dataclass(frozen=True, slots=True)
class PrincipalRefusalRecord:
    """One refusal, safe to log: a reason code, the correlation id and a short detail."""

    reason: PrincipalRefusal
    correlation_id: str
    detail: str = ""

    def __str__(self) -> str:
        tail = f": {self.detail}" if self.detail else ""
        return f"{self.reason.value}{tail}"


class PrincipalRefusalSink(Protocol):
    """Observer for refusals (metrics, audit).  Must not raise on the resolver's behalf."""

    def record(self, record: PrincipalRefusalRecord) -> None: ...


# ---------------------------------------------------------------------------
# Verified credential (authority #1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifiedCredential:
    """A subject the authentication boundary has already proven.

    Constructed by the auth boundary (session middleware, verified bearer JWT, internal
    mTLS, migration operator) — never from a request body, header or job payload.  The
    resolver re-checks every field it can and refuses the record when it is inconsistent,
    but the signature/expiry proof itself stays where the credential material is.

    ``asserted_*`` fields mirror :class:`~prodavan.infrastructure.persistence.rls.context.CallerAssertion`:
    they say which tenant/cabinet/project the verified session believes it acts for.  They
    select a membership and narrow the scope; the values written to the RLS GUCs are the
    ones read back from ``memberships``/``projects``.
    """

    correlation_id: str
    subject_claim: str
    resolution_source: str
    expires_at: datetime
    asserted_tenant_id: str | None = None
    asserted_cabinet_id: str | None = None
    asserted_project_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("correlation_id", "subject_claim"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a verified credential requires a non-empty {name}")
            if value != value.strip():
                raise ValueError(f"the verified {name} has surrounding whitespace")
        if self.resolution_source not in SERVER_RESOLUTION_SOURCES:
            raise ValueError(
                f"unsupported resolution source {self.resolution_source!r}; only "
                f"{', '.join(SERVER_RESOLUTION_SOURCES)} are server-side"
            )
        if not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be a timezone-aware datetime")
        for name in ("asserted_tenant_id", "asserted_cabinet_id", "asserted_project_id"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string when present")
            if not value.strip():
                # An empty assertion asserts nothing, exactly like ``CallerAssertion``.
                object.__setattr__(self, name, None)
                continue
            if value != value.strip():
                raise ValueError(f"{name} has surrounding whitespace")


class PrincipalCredentialSource(Protocol):
    """Auth-boundary lookup: correlation id -> the verified credential in play.

    Returns ``None`` when this boundary holds no verified credential for the correlation
    id (unknown, revoked, expired, or simply not this process's request).  It must never
    build a credential out of unverified request data.
    """

    async def lookup(self, *, correlation_id: str) -> VerifiedCredential | None: ...


#: A verifier takes the raw credential and returns its **verified** claims, or raises.
#: ``{"sub": ..., "exp": ...}`` is the minimum this module uses.
TokenVerifier = Callable[[str], Mapping[str, Any]]


def _unverified_payload(token: str) -> Mapping[str, Any]:
    """Read a JWT payload *after* the token itself was verified.

    This is not a verification step and must never be the only parsing of a token: it is
    used to read ``exp``/scope claims from the very string :func:`_jwks_verified_claims`
    has already validated, so a caller cannot inject claims this way.
    """

    parts = token.split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _jwks_verified_claims(token: str) -> Mapping[str, Any]:
    """Verify a bearer token with the repository's JWKS validator.

    ``JwtValidator.validate`` enforces signature, issuer, audience and ``exp`` and returns
    the :class:`~prodavan.domain.identity.Principal`; the payload is only re-read to pick
    up the expiry and any tenant claims attached to the *verified* token.
    """

    from prodavan.infrastructure.auth.jwt import get_jwt_validator

    principal = get_jwt_validator().validate(token)
    claims: dict[str, Any] = dict(_unverified_payload(token))
    # The validator's answer wins over the peeked payload, always.
    claims["sub"] = principal.sub
    return claims


def _expiry_of(claims: Mapping[str, Any]) -> datetime | None:
    raw = claims.get("exp")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        try:
            raw = float(raw)
        except ValueError:
            return None
    if not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


class BearerJwtCredentialSource:
    """Verified-bearer-JWT path: the credential the auth boundary already accepted.

    Constructed per request by the authentication boundary, which knows both the
    correlation id it issued for the request and the credential presented with it.  There
    is deliberately no map of correlation ids: ``lookup`` answers only for that one id, so
    a correlation id harvested from another request cannot select this credential.

    The token is re-verified on every call (JWKS signature, issuer, audience, ``exp``); the
    intermediate result is not cached, so revoking the token or shortening its lifetime
    takes effect at the next resolution.  A token that carries no usable ``exp`` is
    refused: an unbounded credential cannot produce a bounded principal.
    """

    def __init__(
        self,
        *,
        correlation_id: str,
        token: str,
        verifier: TokenVerifier | None = None,
        resolution_source: str = "bearer_jwt_verified",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            raise ValueError("a correlation id is required to bind a verified credential")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("a bearer token is required to bind a verified credential")
        if resolution_source not in SERVER_RESOLUTION_SOURCES:
            raise ValueError(f"unsupported resolution source {resolution_source!r}")
        self._correlation_id = correlation_id
        self._token = token.strip()
        self._verifier: TokenVerifier = verifier or _jwks_verified_claims
        self._resolution_source = resolution_source
        self._clock = clock or (lambda: datetime.now(UTC))

    async def lookup(self, *, correlation_id: str) -> VerifiedCredential | None:
        if not isinstance(correlation_id, str) or correlation_id != self._correlation_id:
            # Another request's correlation id; this credential does not answer for it.
            return None
        try:
            claims = self._verifier(self._token)
        except Exception as exc:  # noqa: BLE001 - any verification failure is a denial
            logger.debug(
                "bearer credential rejected for correlation %s (%s)",
                correlation_id,
                type(exc).__name__,
            )
            return None
        return credential_from_claims(
            correlation_id=correlation_id,
            claims=claims,
            resolution_source=self._resolution_source,
            now=self._clock(),
        )


def _claim_text(claims: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def credential_from_claims(
    *,
    correlation_id: str,
    claims: Mapping[str, Any],
    resolution_source: str = "bearer_jwt_verified",
    now: datetime | None = None,
) -> VerifiedCredential | None:
    """The credential a **verified** claim set describes, or ``None``.

    ``claims`` must be the answer of a verifier (the JWKS validator, the auth boundary's own
    token exchange) — never a payload decoded from an unverified string.  The same rules the
    bearer path applies are applied here, so both sources and the auth-boundary writer agree
    on what a usable credential is: a non-blank ``sub``, a usable ``exp`` in the future, and
    a ``resolution_source`` the RLS vocabulary knows.

    The auth-boundary writer (``application/auth/auth_sessions.py``) uses this to turn the
    claims of the token it just verified into the exact :class:`VerifiedCredential` it
    stores, so a stored row and a re-verified token cannot describe different shapes.
    """

    if not isinstance(claims, Mapping):
        return None
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        return None
    expires_at = _expiry_of(claims)
    if expires_at is None:
        # No usable lifetime: the principal could not carry a meaningful expires_at.
        return None
    moment = now if now is not None else datetime.now(UTC)
    if expires_at <= moment:
        return None
    try:
        return VerifiedCredential(
            correlation_id=correlation_id,
            subject_claim=subject.strip(),
            resolution_source=resolution_source,
            expires_at=expires_at,
            asserted_tenant_id=_claim_text(claims, "company_id", "tenant_id"),
            asserted_cabinet_id=_claim_text(claims, "cabinet_id"),
            asserted_project_id=_claim_text(claims, "project_id"),
        )
    except ValueError:
        # A source outside the allow-list, or an id the shape refuses: not a credential.
        return None


# ---------------------------------------------------------------------------
# Durable correlation -> subject store (the auth_sessions reader)
# ---------------------------------------------------------------------------


def _as_aware(value: datetime) -> datetime:
    """A stored instant as UTC.

    ``TIMESTAMPTZ`` is stored in UTC, but not every driver hands it back with a timezone
    (``sqlite``/``aiosqlite``, which the acceptance harness uses, returns a naive value for
    ``DateTime(timezone=True)``).  Assuming UTC is therefore a *reading* normalisation and
    not a policy: it never extends a lifetime that a PostgreSQL read would refuse.
    """

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAuthSessionCredentialSource:
    """Durable credential source: ``correlation_id`` -> the row the auth boundary wrote.

    This is the source a worker, a queued job, a background task or a second replica uses.
    It holds no token and no in-process state: the only thing that answers is the
    ``auth_sessions`` row, which the authentication boundary wrote *after* it verified the
    credential (``application/auth/auth_sessions.py``).

    Fail-closed, in this order:

    * an empty, blank or whitespace-padded correlation id selects nothing (the resolver
      refuses it before ever calling here, and this source refuses it too);
    * no row -> ``None`` (the correlation id was never verified, or the id is another
      process's);
    * ``revoked_at`` set -> ``None``, immediately, whatever ``expires_at`` says;
    * ``expires_at`` not in the future -> ``None`` (``exp`` is exclusive: at the boundary
      instant the credential is already dead);
    * a row whose stored ``resolution_source`` is not one of
      ``SERVER_RESOLUTION_SOURCES``, or that cannot be turned into a
      :class:`VerifiedCredential`, -> ``None`` with a refusal code, because a stored row is
      data and data can be wrong; it is never laundered into a principal.

    A **database failure propagates**.  Like the identity directory, this class treats an
    outage as "no answer" rather than as "no access": returning ``None`` on a broken
    connection would turn every worker bind into ``principal_correlation_unknown``, which
    reads exactly like a revoked session.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        refusals: PrincipalRefusalSink | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._refusals = refusals

    def _factory(self) -> Callable[[], Any]:
        if self._session_factory is not None:
            return self._session_factory
        from prodavan.infrastructure.persistence.database import get_session_factory

        return get_session_factory()

    async def lookup(self, *, correlation_id: str) -> VerifiedCredential | None:
        if not isinstance(correlation_id, str) or not correlation_id:
            return self._refuse(
                PrincipalRefusal.CORRELATION_UNKNOWN, correlation_id, "empty correlation id"
            )
        if correlation_id != correlation_id.strip():
            # The row is written with a trimmed id; a padded id is another id, not this one.
            return self._refuse(
                PrincipalRefusal.CORRELATION_UNREPRESENTABLE,
                correlation_id,
                "the correlation id has surrounding whitespace",
            )
        return await self._lookup(correlation_id=correlation_id)

    async def _lookup(self, *, correlation_id: str) -> VerifiedCredential | None:
        from prodavan.infrastructure.persistence.models.auth_sessions import AuthSessionRow

        async with self._factory()() as session:
            row = (
                await session.execute(
                    select(
                        AuthSessionRow.subject_claim,
                        AuthSessionRow.resolution_source,
                        AuthSessionRow.expires_at,
                        AuthSessionRow.revoked_at,
                        AuthSessionRow.company_id,
                        AuthSessionRow.cabinet_id,
                        AuthSessionRow.project_id,
                    ).where(AuthSessionRow.correlation_id == correlation_id)
                )
            ).first()
        if row is None:
            return self._refuse(
                PrincipalRefusal.CORRELATION_UNKNOWN,
                correlation_id,
                "no auth_sessions row is bound to this correlation id",
            )
        subject_claim, resolution_source, expires_at, revoked_at, company_id, cabinet_id, project_id = (
            row
        )
        if revoked_at is not None:
            # Revocation outranks expiry: the credential is dead either way, but the
            # operator needs the right code.
            return self._refuse(
                PrincipalRefusal.CREDENTIAL_REVOKED,
                correlation_id,
                "the stored session was revoked",
            )
        if not isinstance(expires_at, datetime):
            # A column the schema declares NOT NULL came back unusable: refuse, do not guess.
            return self._refuse(
                PrincipalRefusal.CREDENTIAL_UNVERIFIED,
                correlation_id,
                "the stored session has no usable expiry",
            )
        if _as_aware(expires_at) <= self._clock():
            return self._refuse(
                PrincipalRefusal.CREDENTIAL_EXPIRED,
                correlation_id,
                "the stored session has expired",
            )
        if not isinstance(subject_claim, str) or not subject_claim.strip():
            return self._refuse(
                PrincipalRefusal.SUBJECT_UNBOUND,
                correlation_id,
                "the stored session names no subject",
            )
        try:
            return VerifiedCredential(
                correlation_id=correlation_id,
                subject_claim=subject_claim.strip(),
                resolution_source=str(resolution_source),
                expires_at=_as_aware(expires_at),
                asserted_tenant_id=company_id,
                asserted_cabinet_id=cabinet_id,
                asserted_project_id=project_id,
            )
        except ValueError:
            # A source outside the allow-list (a direct-SQL writer bypassing the CHECK, or a
            # vocabulary narrowed since the row was written). The row is not a credential.
            return self._refuse(
                PrincipalRefusal.CREDENTIAL_UNVERIFIED,
                correlation_id,
                "the stored session names a resolution source that is not server-side",
            )

    def _refuse(
        self, reason: PrincipalRefusal, correlation_id: str, detail: str
    ) -> VerifiedCredential | None:
        record = PrincipalRefusalRecord(
            reason=reason, correlation_id=correlation_id or "", detail=detail
        )
        if self._refusals is not None:
            try:
                self._refusals.record(record)
            except Exception:  # noqa: BLE001 - an observer must not change the decision
                logger.warning("principal refusal sink failed for %s", reason.value)
        logger.info("auth session refused: %s correlation=%s", record, correlation_id or "-")
        return None



# ---------------------------------------------------------------------------
# Tenant identity (authority #2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TenantIdentity:
    """What the database says the subject may act as.  Every field is a stored value."""

    subject_id: str
    tenant_id: str
    cabinet_id: str | None
    project_id: str | None
    role: str


@dataclass(frozen=True, slots=True)
class TenantIdentityRefusal:
    """A directory answer that is a *decision*, not an identity.

    Falsy on purpose: ``if refusal:`` reads as "we have an identity", so a caller cannot
    accidentally treat a denial as a resolved subject.  Same shape as
    ``m04e_ports.PortRefusal``.
    """

    reason: PrincipalRefusal
    detail: str = ""

    def __bool__(self) -> bool:
        return False


class TenantIdentityDirectory(Protocol):
    """Server-side directory: verified subject claim -> tenant/cabinet/project/role."""

    async def identity_for(
        self,
        *,
        subject_claim: str,
        asserted_tenant_id: str | None = None,
        asserted_cabinet_id: str | None = None,
        asserted_project_id: str | None = None,
    ) -> TenantIdentity | TenantIdentityRefusal: ...


class SqlTenantIdentityDirectory:
    """Directory over ``employees``/``memberships``/``companies``/cabinets/``projects``.

    Scope rules, in the order they are applied:

    1. ``employees.keycloak_sub`` must name exactly one non-deleted row whose
       ``status`` is ``active``.  ``invited`` means the invite was never activated;
       ``disabled`` is the visible pause state.
    2. the tenant comes from ``memberships.company_id``, joined to ``companies``.  A
       deleted or non-active company is a revoked tenant.  With several memberships and no
       asserted tenant the answer is ``principal_tenant_ambiguous`` — never "the first
       row".
    3. an asserted project must exist, belong to the resolved tenant and be active; its
       ``cabinet_id`` is the cabinet, not whatever the credential claimed.  A credential
       that asserts a different cabinet contradicts the project and is refused.
    4. a cabinet (asserted or project-derived) must be active and granted to the tenant:
       ``company_grant_scope='all'``, company-owned by this tenant, or an active
       ``cabinet_company_grants`` row.  This mirrors
       ``application/relations/query.py:RelationsQuery.has_cabinet_company_grant`` as one
       query instead of three round-trips.
    5. an explicit ``cabinet_employee_assignments`` row for this employee governs access
       when it exists; a revoked assignation is a revocation even though the company still
       holds the grant.  An active assignation does not replace the role.
    6. the role is ``memberships.role`` — the same value ``EntitlementService`` and
       ``RelationsQuery`` authorize with.  ``cabinet_employee_assignments.role``
       (``operator``) is a cabinet duty, not the tenant role written to ``app.role``.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def _factory(self) -> Callable[[], Any]:
        if self._session_factory is not None:
            return self._session_factory
        from prodavan.infrastructure.persistence.database import get_session_factory

        return get_session_factory()

    async def identity_for(
        self,
        *,
        subject_claim: str,
        asserted_tenant_id: str | None = None,
        asserted_cabinet_id: str | None = None,
        asserted_project_id: str | None = None,
    ) -> TenantIdentity | TenantIdentityRefusal:
        if not isinstance(subject_claim, str) or not subject_claim.strip():
            return TenantIdentityRefusal(PrincipalRefusal.SUBJECT_UNBOUND, "no subject claim")
        async with self._factory()() as session:
            return await self._identity(
                session,
                subject_claim=subject_claim.strip(),
                asserted_tenant_id=asserted_tenant_id,
                asserted_cabinet_id=asserted_cabinet_id,
                asserted_project_id=asserted_project_id,
            )

    async def _identity(
        self,
        session: Any,
        *,
        subject_claim: str,
        asserted_tenant_id: str | None,
        asserted_cabinet_id: str | None,
        asserted_project_id: str | None,
    ) -> TenantIdentity | TenantIdentityRefusal:
        employee = await self._employee(session, subject_claim)
        if isinstance(employee, TenantIdentityRefusal):
            return employee
        employee_id, _status = employee

        membership = await self._membership(
            session, employee_id=employee_id, asserted_tenant_id=asserted_tenant_id
        )
        if isinstance(membership, TenantIdentityRefusal):
            return membership
        tenant_id, role = membership

        project_id: str | None = None
        cabinet_id: str | None = None
        if asserted_project_id is not None:
            project = await self._project(session, project_id=asserted_project_id, tenant_id=tenant_id)
            if isinstance(project, TenantIdentityRefusal):
                return project
            project_id, cabinet_id = project
            if asserted_cabinet_id is not None and asserted_cabinet_id != cabinet_id:
                return TenantIdentityRefusal(
                    PrincipalRefusal.SCOPE_CONTRADICTION,
                    "the asserted cabinet contradicts the project's cabinet",
                )
        elif asserted_cabinet_id is not None:
            cabinet_id = asserted_cabinet_id

        if cabinet_id is not None:
            cabinet = await self._cabinet(
                session, cabinet_id=cabinet_id, tenant_id=tenant_id, employee_id=employee_id
            )
            if isinstance(cabinet, TenantIdentityRefusal):
                return cabinet

        return TenantIdentity(
            subject_id=employee_id,
            tenant_id=tenant_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            role=role,
        )

    async def _employee(self, session: Any, subject_claim: str) -> tuple[str, str] | TenantIdentityRefusal:
        rows = (
            await session.execute(
                select(EmployeeRow.id, EmployeeRow.status).where(
                    EmployeeRow.keycloak_sub == subject_claim,
                    EmployeeRow.deleted_at.is_(None),
                )
            )
        ).all()
        if not rows:
            return TenantIdentityRefusal(
                PrincipalRefusal.SUBJECT_UNBOUND,
                "no live employees row is bound to the verified subject",
            )
        if len(rows) > 1:
            # keycloak_sub is unique; more than one live row means the schema guarantee is
            # broken, and choosing one of them would be a guess.
            return TenantIdentityRefusal(
                PrincipalRefusal.SUBJECT_UNBOUND,
                "several employees rows are bound to the verified subject",
            )
        employee_id, status = rows[0]
        if str(status) != EmployeeStatus.ACTIVE:
            return TenantIdentityRefusal(
                PrincipalRefusal.SUBJECT_INACTIVE,
                f"employee status is {status!r}, not {EmployeeStatus.ACTIVE.value!r}",
            )
        return str(employee_id), str(status)

    async def _membership(
        self, session: Any, *, employee_id: str, asserted_tenant_id: str | None
    ) -> tuple[str, str] | TenantIdentityRefusal:
        rows = (
            await session.execute(
                select(MembershipRow.company_id, MembershipRow.role, CompanyRow.status)
                .join(CompanyRow, CompanyRow.id == MembershipRow.company_id)
                .where(
                    MembershipRow.employee_id == employee_id,
                    CompanyRow.deleted_at.is_(None),
                )
            )
        ).all()
        live = [(str(company_id), str(role), str(status)) for company_id, role, status in rows]
        active = [row for row in live if row[2] == ACTIVE_COMPANY_STATUS]
        if asserted_tenant_id is not None:
            matches = [row for row in active if row[0] == asserted_tenant_id]
            if not matches:
                live_match = next((row for row in live if row[0] == asserted_tenant_id), None)
                if live_match is not None:
                    return TenantIdentityRefusal(
                        PrincipalRefusal.TENANT_INACTIVE,
                        f"the asserted company is {live_match[2]!r}, not {ACTIVE_COMPANY_STATUS!r}",
                    )
                return TenantIdentityRefusal(
                    PrincipalRefusal.MEMBERSHIP_REVOKED,
                    "the subject holds no membership in the asserted tenant",
                )
            company_id, role, _status = matches[0]
            return company_id, role
        if not live:
            return TenantIdentityRefusal(
                PrincipalRefusal.MEMBERSHIP_REVOKED,
                "the subject holds no live membership",
            )
        if not active:
            return TenantIdentityRefusal(
                PrincipalRefusal.TENANT_INACTIVE,
                "every membership points at a company that is not active",
            )
        if len(active) > 1:
            return TenantIdentityRefusal(
                PrincipalRefusal.TENANT_AMBIGUOUS,
                "the subject holds memberships in several tenants and none was asserted",
            )
        company_id, role, _status = active[0]
        return company_id, role

    async def _project(
        self, session: Any, *, project_id: str, tenant_id: str
    ) -> tuple[str, str] | TenantIdentityRefusal:
        row = (
            await session.execute(
                select(ProjectRow.id, ProjectRow.company_id, ProjectRow.cabinet_id, ProjectRow.status).where(
                    ProjectRow.id == project_id
                )
            )
        ).first()
        if row is None:
            return TenantIdentityRefusal(
                PrincipalRefusal.PROJECT_NOT_IN_SCOPE, "the asserted project does not exist"
            )
        _id, company_id, cabinet_id, status = row
        if str(company_id) != tenant_id:
            return TenantIdentityRefusal(
                PrincipalRefusal.PROJECT_NOT_IN_SCOPE,
                "the asserted project belongs to another tenant",
            )
        if str(status) != "active":
            return TenantIdentityRefusal(
                PrincipalRefusal.PROJECT_INACTIVE, f"project status is {status!r}"
            )
        return str(_id), str(cabinet_id)

    async def _cabinet(
        self, session: Any, *, cabinet_id: str, tenant_id: str, employee_id: str
    ) -> None | TenantIdentityRefusal:
        row = (
            await session.execute(
                select(
                    CabinetInstanceRow.status,
                    CabinetInstanceRow.owner_scope,
                    CabinetInstanceRow.owner_company_id,
                    CabinetInstanceRow.company_grant_scope,
                ).where(CabinetInstanceRow.id == cabinet_id)
            )
        ).first()
        if row is None:
            return TenantIdentityRefusal(
                PrincipalRefusal.CABINET_NOT_GRANTED, "the asserted cabinet does not exist"
            )
        status, owner_scope, owner_company_id, grant_scope = row
        if str(status) != CabinetStatus.ACTIVE:
            return TenantIdentityRefusal(
                PrincipalRefusal.CABINET_INACTIVE, f"cabinet status is {status!r}"
            )
        granted = str(grant_scope) == CabinetCompanyGrantScope.ALL or (
            str(owner_scope) == CabinetOwnerScope.COMPANY and str(owner_company_id or "") == tenant_id
        )
        if not granted:
            grant = (
                await session.execute(
                    select(CabinetCompanyGrantRow.id).where(
                        CabinetCompanyGrantRow.cabinet_id == cabinet_id,
                        CabinetCompanyGrantRow.company_id == tenant_id,
                        CabinetCompanyGrantRow.status == CabinetGrantStatus.ACTIVE,
                    )
                )
            ).first()
            granted = grant is not None
        if not granted:
            return TenantIdentityRefusal(
                PrincipalRefusal.CABINET_NOT_GRANTED,
                "the tenant holds no active grant for the cabinet",
            )
        assignment = (
            await session.execute(
                select(CabinetEmployeeAssignmentRow.status).where(
                    CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
                    CabinetEmployeeAssignmentRow.employee_id == employee_id,
                )
            )
        ).first()
        if assignment is not None and str(assignment[0]) != CabinetAssignmentStatus.ACTIVE:
            return TenantIdentityRefusal(
                PrincipalRefusal.CABINET_NOT_GRANTED,
                "the employee's cabinet assignment is revoked",
            )
        return None


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


class AuthBoundaryPrincipalResolver:
    """``TrustedPrincipalResolver`` over a verified credential plus the identity tables.

    ``resolve`` is total: it returns a sealed :class:`TrustedPrincipal` or ``None``.  The
    principal's lifetime is the credential's expiry capped by ``max_ttl``, so ``expires_at``
    is always present and always in the future — the RLS module refuses a principal without
    one, and this class never produces a principal it would have to refuse later.
    """

    def __init__(
        self,
        *,
        credential_source: PrincipalCredentialSource,
        directory: TenantIdentityDirectory,
        clock: Callable[[], datetime] | None = None,
        max_ttl: timedelta = DEFAULT_MAX_PRINCIPAL_TTL,
        refusals: PrincipalRefusalSink | None = None,
    ) -> None:
        if credential_source is None or not callable(getattr(credential_source, "lookup", None)):
            raise ValueError("a verified-credential source exposing lookup() is required")
        if directory is None or not callable(getattr(directory, "identity_for", None)):
            raise ValueError("a tenant identity directory exposing identity_for() is required")
        if not isinstance(max_ttl, timedelta) or max_ttl <= timedelta(0):
            raise ValueError("max_ttl must be a positive timedelta")
        self._credentials = credential_source
        self._directory = directory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_ttl = max_ttl
        self._refusals = refusals

    # -- public API (the protocol) -----------------------------------------

    async def resolve(self, *, correlation_id: str) -> TrustedPrincipal | None:
        if not isinstance(correlation_id, str) or not correlation_id or not correlation_id.strip():
            return self._refuse(PrincipalRefusal.CORRELATION_UNKNOWN, str(correlation_id or ""), "empty correlation id")
        if correlation_id != correlation_id.strip():
            return self._refuse(
                PrincipalRefusal.CORRELATION_UNREPRESENTABLE,
                correlation_id,
                "the correlation id has surrounding whitespace",
            )
        now = self._clock()

        credential = await self._credentials.lookup(correlation_id=correlation_id)
        if credential is None:
            return self._refuse(
                PrincipalRefusal.CORRELATION_UNKNOWN,
                correlation_id,
                "no verified credential is bound to this correlation id",
            )
        if not isinstance(credential, VerifiedCredential):  # pragma: no cover - defensive
            return self._refuse(
                PrincipalRefusal.CREDENTIAL_UNVERIFIED,
                correlation_id,
                "the credential source did not return a VerifiedCredential",
            )
        if credential.correlation_id != correlation_id:
            return self._refuse(
                PrincipalRefusal.CORRELATION_MISMATCH,
                correlation_id,
                "the verified credential belongs to another correlation id",
            )
        if credential.expires_at <= now:
            return self._refuse(
                PrincipalRefusal.CREDENTIAL_EXPIRED,
                correlation_id,
                "the verified credential has expired",
            )

        identity = await self._directory.identity_for(
            subject_claim=credential.subject_claim,
            asserted_tenant_id=credential.asserted_tenant_id,
            asserted_cabinet_id=credential.asserted_cabinet_id,
            asserted_project_id=credential.asserted_project_id,
        )
        if not identity:
            refusal = identity if isinstance(identity, TenantIdentityRefusal) else None
            return self._refuse(
                refusal.reason if refusal is not None else PrincipalRefusal.SUBJECT_UNBOUND,
                correlation_id,
                refusal.detail if refusal is not None else "",
            )
        if not isinstance(identity, TenantIdentity):  # pragma: no cover - defensive
            return self._refuse(
                PrincipalRefusal.CONTEXT_UNREPRESENTABLE,
                correlation_id,
                "the directory did not return a TenantIdentity",
            )

        expires_at = min(credential.expires_at, now + self._max_ttl)
        if expires_at <= now:
            # Unreachable while the credential is unexpired and ``max_ttl > 0`` (both are
            # checked above). Kept so a future relaxation of either cannot pass a principal
            # to ``rls.context`` that it would reject as ``tenant_context_expired`` — a
            # misleading reason code for "the resolver built a dead principal".
            return self._refuse(
                PrincipalRefusal.EXPIRY_NOT_IN_FUTURE,
                correlation_id,
                "the bounded principal lifetime is not in the future",
            )

        try:
            resolution = issue_server_resolution(
                resolved_by=_RESOLVED_BY[credential.resolution_source],
                correlation_id=correlation_id,
                expires_at=expires_at,
                tenant_id=identity.tenant_id,
                cabinet_id=identity.cabinet_id,
                project_id=identity.project_id,
                subject_id=identity.subject_id,
                role=identity.role,
            )
            principal = TrustedPrincipal.from_resolution(
                resolution,
                resolution_source=credential.resolution_source,  # type: ignore[arg-type]
            )
            principal.validate(now=now)
        except RlsContextError as exc:
            # The identity is real but cannot be expressed in the RLS vocabulary (an id
            # outside the identifier alphabet, a role longer than 40 characters, ...).
            # Refusing is the only option that does not truncate a tenant into another one.
            return self._refuse(
                PrincipalRefusal.CONTEXT_UNREPRESENTABLE,
                correlation_id,
                f"the resolved identity is not representable ({exc.reason_code})",
            )
        if principal.correlation_id != correlation_id:  # pragma: no cover - defensive
            return self._refuse(
                PrincipalRefusal.CORRELATION_MISMATCH,
                correlation_id,
                "the sealed principal belongs to another correlation id",
            )
        return principal

    # -- refusals -----------------------------------------------------------

    def _refuse(self, reason: PrincipalRefusal, correlation_id: str, detail: str) -> None:
        record = PrincipalRefusalRecord(
            reason=reason, correlation_id=correlation_id or "", detail=detail
        )
        if self._refusals is not None:
            try:
                self._refusals.record(record)
            except Exception:  # noqa: BLE001 - an observer must not change the decision
                logger.warning("principal refusal sink failed for %s", reason.value)
        logger.info("principal refused: %s correlation=%s", record, correlation_id or "-")
        return None


def build_sql_principal_resolver(
    *,
    credential_source: PrincipalCredentialSource,
    session_factory: Callable[[], Any] | None = None,
    clock: Callable[[], datetime] | None = None,
    max_ttl: timedelta = DEFAULT_MAX_PRINCIPAL_TTL,
    refusals: PrincipalRefusalSink | None = None,
) -> AuthBoundaryPrincipalResolver:
    """Wire the resolver over the platform database.

    ``credential_source`` stays the host's decision: only the authentication boundary can
    say which credential a correlation id is bound to.  Everything else — the identity
    directory, the clock, the lifetime ceiling — is assembled here so the composition root
    cannot accidentally hand in a directory that reads somewhere else.
    """

    return AuthBoundaryPrincipalResolver(
        credential_source=credential_source,
        directory=SqlTenantIdentityDirectory(session_factory=session_factory, clock=clock),
        clock=clock,
        max_ttl=max_ttl,
        refusals=refusals,
    )


def build_durable_principal_resolver(
    *,
    session_factory: Callable[[], Any] | None = None,
    clock: Callable[[], datetime] | None = None,
    max_ttl: timedelta = DEFAULT_MAX_PRINCIPAL_TTL,
    refusals: PrincipalRefusalSink | None = None,
) -> AuthBoundaryPrincipalResolver:
    """The resolver a **process** owns: durable store in, no request credential needed.

    This is the source that closes the recorded blocker. A worker, a queued job, a
    background task or a second replica has no bearer token in hand, but it does have the
    correlation id the auth boundary issued in the request that started the work, and
    :class:`SqlAuthSessionCredentialSource` turns that id into the verified subject the
    boundary stored.

    The request path must keep using :class:`BearerJwtCredentialSource` (built per request,
    with the token in hand, through :func:`build_sql_principal_resolver`): a process-scope
    resolver serves callers that could not otherwise be answered, it does not replace the
    credential the request already proved.
    """

    return AuthBoundaryPrincipalResolver(
        credential_source=SqlAuthSessionCredentialSource(
            session_factory=session_factory, clock=clock, refusals=refusals
        ),
        directory=SqlTenantIdentityDirectory(session_factory=session_factory, clock=clock),
        clock=clock,
        max_ttl=max_ttl,
        refusals=refusals,
    )


# ---------------------------------------------------------------------------
# Recorded schema specification (materialised by revision 2026110317)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SchemaGap:
    """A durable table this module needs, written down as a reviewable specification.

    Specification only — no DDL is emitted or invented here.  The columns are what the
    resolver reads; the constraints are the guarantees it relies on.  ``auth_sessions``
    (revision ``2026110317``) is the first instance of this type, and the tests compare the
    model and the revision against it column by column.
    """

    table: str
    columns: tuple[tuple[str, str, str], ...]
    indexes: tuple[str, ...]
    semantics: tuple[str, ...]


AUTH_SESSION_CORRELATION_GAP: Final = SchemaGap(
    table="auth_sessions",
    columns=(
        ("correlation_id", "text primary key", "the id the auth boundary issued for the request"),
        ("subject_claim", "text not null", "verified OIDC sub (employees.keycloak_sub), never a request value"),
        ("resolution_source", "text not null", "one of rls.context.SERVER_RESOLUTION_SOURCES"),
        ("issued_at", "timestamptz not null", "when verification happened"),
        ("expires_at", "timestamptz not null", "credential/token expiry; must be in the future to resolve"),
        ("revoked_at", "timestamptz null", "explicit revocation; a non-null value denies immediately"),
        ("company_id", "text null", "the tenant the session selected, validated against memberships"),
        ("cabinet_id", "text null", "the cabinet the session selected, validated against the grant"),
        ("project_id", "text null", "the project the session selected, validated against projects"),
    ),
    indexes=(
        "primary key (correlation_id)",
        "index (subject_claim, expires_at) for revocation sweeps",
    ),
    semantics=(
        "one row per verified credential binding; the row is the only authority that maps a "
        "correlation id to a subject",
        "a row whose expires_at is past or whose revoked_at is set resolves to None, never to a "
        "degraded principal",
        "writing the row happens in the auth boundary only, after signature/expiry verification",
        "reading it must not require a tenant GUC: like companies/memberships it is a platform "
        "table outside the canonical RLS set",
    ),
)

#: One-line summary for the composition root / acceptance report.
#:
#: Kept as a constant (and kept in this module) because the composition root and the
#: acceptance reports quote it; it now records that the gap is **closed** rather than open.
PRINCIPAL_RESOLVER_BLOCKER: Final = (
    "closed by auth_sessions (revision 2026110317, see AUTH_SESSION_CORRELATION_GAP): a "
    "correlation id whose credential is no longer in hand is resolved by "
    "SqlAuthSessionCredentialSource from the row the auth boundary recorded, so a queued / "
    "worker / second-replica bind no longer depends on the request holding the token; an id "
    "nothing recorded — or a row that is expired or revoked — still resolves to None and the "
    "tenant-context bind refuses"
)

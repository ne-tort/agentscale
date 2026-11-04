"""The auth boundary's writer for the durable ``correlation_id -> subject`` store.

Task ``MIG-AUTHSESS-01``. ``auth_sessions`` is only worth anything if the row is written by
the component that *proved* the subject: a worker that later resolves the correlation id
must be able to trust the subject in that row even though it holds no token. So this module
has one rule and enforces it structurally:

    ``record_auth_session`` accepts a :class:`VerifiedCredential` — the shape
    ``application/migration/principal_resolver.py`` produces from claims a verifier has
    already accepted — and nothing else. There is no ``(correlation_id, subject)`` overload
    that would let a caller name a subject it never proved.

What is recorded
================
One row per verified credential binding: ``correlation_id`` (the id the boundary issued for
this request), ``subject_claim`` (the verified ``sub``), ``resolution_source`` (one of
``SERVER_RESOLUTION_SOURCES``), ``issued_at`` (when *this* process verified it),
``expires_at`` (the credential's own expiry) and the selected scope anchors
(``company_id``/``cabinet_id``/``project_id``).

The anchors are stored as *assertions*, never as authority: the resolver still reads the
tenant from ``memberships`` and the project from ``projects``. They are stored so a worker
binds the same scope the request had instead of a tenant-wide one, and a stale anchor is a
refusal (``principal_tenant_ambiguous`` / ``principal_membership_revoked``) rather than a
wider principal.

Idempotency and conflicts
=========================
The primary key is the correlation id, so a replayed write is a *replay*, not a second row:

* the same binding written twice (a retry after a lost acknowledgement, a second replica
  handling the same request) returns :attr:`AuthSessionWriteOutcome.REPLAYED` and changes
  nothing;
* a **different** subject, source, expiry or scope under the same correlation id raises
  :class:`AuthSessionConflict`. It is not overwritten: silently rebinding an id that a job
  may already be carrying would hand that job a different subject than the one recorded
  when it was queued;
* a row that is already ``revoked_at`` is **never resurrected**. A fresh verification of the
  same correlation id returns :attr:`AuthSessionWriteOutcome.REVOKED` and leaves the
  revocation in place; lifting a revocation is an explicit operator action, not a side
  effect of somebody logging in again.

Recording never decides an authentication
=========================================
The call site is the auth boundary *after* the credential has been verified
(``api/deps.py::get_principal``), and this module deliberately does not raise on anything
except :class:`AuthSessionConflict` — a programming/traffic anomaly the caller must decide
about. A storage outage raises as well (a caller may retry it) and the caller records a
redacted code. Recording is evidence for *later* callers; refusing the current, already
verified request because the evidence could not be stored would convert an auxiliary write
into an auth outage, and the fail-closed direction is preserved anyway: without a row, a
worker's correlation id resolves to ``None``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from prodavan.application.migration.principal_resolver import VerifiedCredential

logger = logging.getLogger(__name__)

__all__ = [
    "AUTH_SESSION_WRITER_CALL_SITE",
    "AuthSessionConflict",
    "AuthSessionWrite",
    "AuthSessionWriteOutcome",
    "record_auth_session",
    "record_verified_bearer_session",
]

#: Where the writer is called from in the real process, recorded here so a reviewer can
#: check one place instead of grepping. ``api/deps.py::get_principal`` verifies the bearer
#: token with the JWKS validator and then records the session under the request's trace id —
#: the correlation id the request path already propagates (``X-Trace-Id`` in,
#: ``request.state.trace_id`` / ``core.trace_context.current_trace_id()`` out). Nothing
#: else in the repository calls the writer.
AUTH_SESSION_WRITER_CALL_SITE: Final = "prodavan.api.deps.get_principal"


class AuthSessionWriteOutcome(StrEnum):
    """What a write actually did. Never "the credential is valid" — that was already proven."""

    RECORDED = "recorded"
    """A new row was inserted."""

    REPLAYED = "replayed"
    """An identical row was already present; nothing changed."""

    REVOKED = "revoked"
    """The row exists but is revoked. The revocation stands; nothing changed."""


@dataclass(frozen=True, slots=True)
class AuthSessionWrite:
    """Outcome plus the binding it applies to (identifiers only, no credential material)."""

    outcome: AuthSessionWriteOutcome
    correlation_id: str
    subject_claim: str

    @property
    def written(self) -> bool:
        return self.outcome is AuthSessionWriteOutcome.RECORDED


class AuthSessionConflict(RuntimeError):
    """The same correlation id is already bound to a different verified credential.

    Raised instead of overwriting: a job that already carries this id must keep resolving
    to the subject that was recorded when it was queued. The message carries the
    correlation id and the *names* of the fields that differ — never a claim value.
    """

    def __init__(self, correlation_id: str, differing: tuple[str, ...]) -> None:
        self.correlation_id = correlation_id
        self.differing = differing
        super().__init__(
            f"auth session {correlation_id!r} is already bound to a different credential "
            f"({', '.join(differing) or 'unknown field'})"
        )


#: Fields compared for idempotency. ``issued_at`` is deliberately absent: two processes
#: verifying the same credential at different instants describe the same binding, and
#: ``revoked_at`` is handled separately (it is never lifted).
_BINDING_FIELDS: Final[tuple[str, ...]] = (
    "subject_claim",
    "resolution_source",
    "expires_at",
    "company_id",
    "cabinet_id",
    "project_id",
)


def _as_aware(value: datetime) -> datetime:
    """A stored instant as UTC (see the reader's note about drivers that drop tzinfo)."""

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _optional_text(value: str | None) -> str | None:
    """An empty/blank scope anchor asserts nothing, exactly like ``CallerAssertion``."""

    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _resolved_factory(session_factory: Callable[[], Any] | None) -> Callable[[], Any]:
    if session_factory is not None:
        return session_factory
    from prodavan.infrastructure.persistence.database import get_session_factory

    return get_session_factory()


def _differing(row: Any, credential: VerifiedCredential) -> tuple[str, ...]:
    """Which binding fields the stored row and the freshly verified credential disagree on."""

    expected = {
        "subject_claim": credential.subject_claim,
        "resolution_source": credential.resolution_source,
        "expires_at": credential.expires_at,
        "company_id": _optional_text(credential.asserted_tenant_id),
        "cabinet_id": _optional_text(credential.asserted_cabinet_id),
        "project_id": _optional_text(credential.asserted_project_id),
    }
    differing: list[str] = []
    for name in _BINDING_FIELDS:
        stored = getattr(row, name)
        wanted = expected[name]
        if name == "expires_at":
            if stored is None or _as_aware(stored) != _as_aware(wanted):
                differing.append(name)
            continue
        if stored != wanted:
            differing.append(name)
    return tuple(differing)


async def record_auth_session(
    credential: VerifiedCredential,
    *,
    session_factory: Callable[[], Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AuthSessionWrite | None:
    """Record one verified credential binding; ``None`` when there is nothing worth storing.

    ``credential`` must be a :class:`VerifiedCredential` — the auth boundary's own shape, so
    the subject in the row is one a verifier produced. A credential that is already expired
    at ``clock()`` is not recorded: the row could never resolve, and writing dead evidence
    would make an expired binding look like a live one to an operator running a sweep.

    Raises :class:`AuthSessionConflict` when the correlation id is already bound to a
    different credential, and propagates a storage failure (the caller decides whether to
    log it; the request it belongs to has already been authenticated by the credential).
    """

    if not isinstance(credential, VerifiedCredential):
        raise TypeError(
            "record_auth_session takes a VerifiedCredential produced by the auth boundary; "
            "a bare (correlation_id, subject) pair would let a caller name a subject it "
            "never proved"
        )
    now = (clock or (lambda: datetime.now(UTC)))()
    if _as_aware(credential.expires_at) <= now:
        logger.debug(
            "auth session not recorded: the credential for correlation %s is already expired",
            credential.correlation_id,
        )
        return None

    from prodavan.infrastructure.persistence.models.auth_sessions import AuthSessionRow

    values = {
        "correlation_id": credential.correlation_id,
        "subject_claim": credential.subject_claim,
        "resolution_source": credential.resolution_source,
        "issued_at": now,
        "expires_at": _as_aware(credential.expires_at),
        "revoked_at": None,
        "company_id": _optional_text(credential.asserted_tenant_id),
        "cabinet_id": _optional_text(credential.asserted_cabinet_id),
        "project_id": _optional_text(credential.asserted_project_id),
    }

    async with _resolved_factory(session_factory)() as session:
        existing = await _existing(session, AuthSessionRow, credential.correlation_id)
        if existing is not None:
            return _replay(existing, credential)
        session.add(AuthSessionRow(**values))
        try:
            await session.commit()
        except IntegrityError:
            # Another replica inserted the same pk between the read and the insert. Re-read
            # and apply the same idempotency rules instead of reporting a storage error.
            await session.rollback()
            existing = await _existing(session, AuthSessionRow, credential.correlation_id)
            if existing is None:  # pragma: no cover - a constraint other than the pk
                raise
            return _replay(existing, credential)
        logger.info(
            "auth session recorded correlation=%s source=%s",
            credential.correlation_id,
            credential.resolution_source,
        )
        return AuthSessionWrite(
            outcome=AuthSessionWriteOutcome.RECORDED,
            correlation_id=credential.correlation_id,
            subject_claim=credential.subject_claim,
        )


async def _existing(session: Any, model: Any, correlation_id: str) -> Any | None:
    return (
        await session.execute(select(model).where(model.correlation_id == correlation_id))
    ).scalar_one_or_none()


def _replay(row: Any, credential: VerifiedCredential) -> AuthSessionWrite:
    """Apply the idempotency rules to a row that already exists."""

    if getattr(row, "revoked_at", None) is not None:
        # Revocation wins over a fresh verification; see the module docstring.
        logger.info(
            "auth session already revoked correlation=%s; the revocation stands",
            credential.correlation_id,
        )
        return AuthSessionWrite(
            outcome=AuthSessionWriteOutcome.REVOKED,
            correlation_id=credential.correlation_id,
            subject_claim=str(row.subject_claim),
        )
    differing = _differing(row, credential)
    if differing:
        raise AuthSessionConflict(credential.correlation_id, differing)
    return AuthSessionWrite(
        outcome=AuthSessionWriteOutcome.REPLAYED,
        correlation_id=credential.correlation_id,
        subject_claim=str(row.subject_claim),
    )


async def record_verified_bearer_session(
    *,
    token: str,
    correlation_id: str,
    claims: Any = None,
    verifier: Callable[[str], Any] | None = None,
    session_factory: Callable[[], Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AuthSessionWrite | None:
    """Record the session of a bearer token the caller has **already verified**.

    Convenience for the one call site (``api/deps.py::get_principal``): it takes the token
    and the claims that the JWKS validator produced for *this* token, turns them into the
    resolver's :class:`VerifiedCredential` through
    :func:`~prodavan.application.migration.principal_resolver.credential_from_claims` — the
    same function ``BearerJwtCredentialSource`` uses — and records it.

    ``claims`` is required unless a ``verifier`` is supplied: this helper never decodes a
    token by itself, because a decoded payload is not a verification. When neither is given
    it refuses (returns ``None``) rather than trusting the token string.
    """

    from prodavan.application.migration.principal_resolver import (
        _jwks_verified_claims,
        credential_from_claims,
    )

    moment = (clock or (lambda: datetime.now(UTC)))()
    if claims is None:
        if verifier is None:
            verifier = _jwks_verified_claims
        try:
            claims = verifier(token)
        except Exception as exc:  # noqa: BLE001 - an unverifiable token is simply not recorded
            logger.debug(
                "auth session not recorded for correlation %s: verification failed (%s)",
                correlation_id,
                type(exc).__name__,
            )
            return None
    credential = credential_from_claims(
        correlation_id=correlation_id,
        claims=claims,
        resolution_source="bearer_jwt_verified",
        now=moment,
    )
    if credential is None:
        return None
    return await record_auth_session(
        credential, session_factory=session_factory, clock=lambda: moment
    )

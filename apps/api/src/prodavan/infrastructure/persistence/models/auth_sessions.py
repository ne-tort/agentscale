"""Durable ``correlation_id -> verified subject`` store (task ``MIG-AUTHSESS-01``).

``application/migration/principal_resolver.py`` could resolve a principal only while the
auth boundary still had the credential in hand: ``BearerJwtCredentialSource`` re-verifies
the token of *this* request and answers for exactly one correlation id. A queued job, a
worker that woke up later or a second replica holds no token, so its correlation id
resolved to ``None`` and the tenant-context bind refused. That module recorded the missing
relation as :data:`~prodavan.application.migration.principal_resolver.AUTH_SESSION_CORRELATION_GAP`;
``auth_sessions`` is that relation, and this is its declaration.

What a row is
=============

One row per verified credential binding, keyed by the correlation id the auth boundary
issued for the request that carried it:

* ``subject_claim`` is the **verified** OIDC ``sub`` (``employees.keycloak_sub``), written
  only after the signature/issuer/audience/``exp`` check succeeded — never a value taken
  from a request body, header or job payload;
* ``resolution_source`` is one of
  :data:`~prodavan.infrastructure.persistence.rls.context.SERVER_RESOLUTION_SOURCES`, so
  the row can never name a request-derived path (the CHECK below makes that true for a
  direct-SQL writer too);
* ``expires_at`` is the credential's own expiry. A row whose ``expires_at`` is in the past,
  or whose ``revoked_at`` is set, resolves to ``None`` — the reader never degrades to a
  weaker principal, and there is no "expired but probably still fine" branch;
* ``company_id``/``cabinet_id``/``project_id`` are the scope anchors the session selected.
  They are **assertions**, exactly like ``VerifiedCredential.asserted_*``: the resolver
  uses them to select a membership and to narrow the scope, and the values written to the
  RLS GUCs still come from ``memberships``/``projects``. Storing them is what lets a
  worker bind the same scope the request had instead of a tenant-wide one.

Why this table is not in the RLS registry
========================================

``auth_sessions`` is a **platform** table, like ``companies``/``memberships``: it is read
*before* a tenant exists, to decide which tenant a correlation id may bind. A tenant-scoped
policy would make it unreadable at the only moment it is needed — the reader has no
``app.tenant_id`` yet, and ``rls/policies.py`` refuses a policy whose predicate would be
``false`` for a tenantless session. It is therefore declared in
``policies.EXCLUDED_TABLES`` with a reviewed reason (the same list that carries
``companies``/``memberships``) and is **not** added to ``CANONICAL_TABLE_POLICIES``; the
``auth_sessions`` row is protected by the fact that only the auth boundary writes it and by
the grants review the exclusion records, not by a row filter. Its name matches no
``CANONICAL_TABLE_PREFIXES`` entry, so the coverage report neither demands a policy nor
hides one.

Deliberate decisions
====================

* **``TEXT``, not ``VARCHAR(n)``**, for every identifier: a length limit would truncate a
  correct correlation id at insert time instead of refusing an unrepresentable one (the
  writer refuses those up front, see ``application/auth/auth_sessions.py``).
* **No server default on any column.** ``issued_at``/``expires_at`` are the *credential's*
  stated lifetime; a default would let a row date itself, and a default on ``revoked_at``
  would revoke everything.
* **The integrity CHECKs are part of the specification's guarantees**, not extra policy: a
  blank correlation id or subject could never be resolved, a source outside the allow-list
  could never be mapped to ``resolved_by``, an expiry before issuance describes a
  credential that was dead when it was verified, and a revocation before issuance
  describes an event that never happened. ``ck_auth_sessions_resolution_source`` is
  rendered from ``SERVER_RESOLUTION_SOURCES`` itself, so the database vocabulary cannot
  drift from the Python one.
* **One index beyond the primary key**: ``(subject_claim, expires_at)``, the sweep key for
  revoking or expiring every session of one subject without a table scan. It is the index
  :data:`~prodavan.application.migration.principal_resolver.AUTH_SESSION_CORRELATION_GAP`
  names.
* **No foreign key to ``employees``/``companies``.** The row must be recordable for a
  verified subject whose ``employees`` row does not exist yet (an invited-but-unactivated
  user), and the directory is the authority that turns a subject into a tenant anyway: a
  FK here would turn "no employee row" from a refusal (``principal_subject_unbound``) into
  an insert failure at the auth boundary, i.e. a login outage.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.rls.context import SERVER_RESOLUTION_SOURCES

#: The table name, as the resolver specification spells it.
AUTH_SESSIONS_TABLE = "auth_sessions"

#: The sweep index the specification names.
AUTH_SESSIONS_SUBJECT_INDEX = "ix_auth_sessions_subject_expiry"

#: ``resolution_source IN (...)`` rendered from the one closed allow-list, so a value the
#: reader could not map to ``resolved_by`` can never reach the table.
RESOLUTION_SOURCE_CHECK = (
    "resolution_source IN ("
    + ", ".join(f"'{source}'" for source in SERVER_RESOLUTION_SOURCES)
    + ")"
)

#: ``length(x) > 0`` over the *trimmed* value, rendered per column. ``trim()`` rather than
#: ``btrim()`` on purpose: the two are the same predicate on PostgreSQL (``btrim`` defaults
#: to both sides) and ``trim`` also exists in the SQLite harness the acceptance tests run on,
#: so the constraint the tests exercise is the constraint production installs.
AUTH_SESSIONS_NON_BLANK_TEMPLATE = "length(trim({column})) > 0"


class AuthSessionRow(Base):
    """One verified credential binding: correlation id -> subject (+ selected scope).

    Written only by the auth boundary (``application/auth/auth_sessions.py``) after the
    credential was verified, read only by
    :class:`~prodavan.application.migration.principal_resolver.SqlAuthSessionCredentialSource`.
    The row is the *only* durable authority that maps a correlation id to a subject, and an
    expired or revoked row resolves to ``None`` rather than to a degraded principal.
    """

    __tablename__ = AUTH_SESSIONS_TABLE
    __table_args__ = (
        CheckConstraint(
            AUTH_SESSIONS_NON_BLANK_TEMPLATE.format(column="correlation_id"),
            name="ck_auth_sessions_correlation_nonblank",
        ),
        CheckConstraint(
            AUTH_SESSIONS_NON_BLANK_TEMPLATE.format(column="subject_claim"),
            name="ck_auth_sessions_subject_nonblank",
        ),
        CheckConstraint(
            RESOLUTION_SOURCE_CHECK,
            name="ck_auth_sessions_resolution_source",
        ),
        CheckConstraint(
            "expires_at > issued_at",
            name="ck_auth_sessions_expiry_after_issue",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_auth_sessions_revoked_after_issue",
        ),
        Index(AUTH_SESSIONS_SUBJECT_INDEX, "subject_claim", "expires_at"),
    )

    #: The id the auth boundary issued for the request that carried the credential.
    correlation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    #: Verified OIDC ``sub`` (``employees.keycloak_sub``); never a request value.
    subject_claim: Mapped[str] = mapped_column(Text, nullable=False)
    #: One of ``rls.context.SERVER_RESOLUTION_SOURCES``.
    resolution_source: Mapped[str] = mapped_column(Text, nullable=False)
    #: When verification happened.
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Credential/token expiry; must be in the future to resolve.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Explicit revocation; a non-null value denies immediately and is never overwritten.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The tenant the session selected, validated against ``memberships`` on resolution.
    company_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The cabinet the session selected, validated against the tenant's grant.
    cabinet_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The project the session selected, validated against ``projects``.
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Review inputs (written down; the acceptance cases live in the tests)
# ---------------------------------------------------------------------------

#: The table this module owns. The RLS registry must **not** contain it (see the module
#: docstring); ``policies.EXCLUDED_TABLES`` records the reviewed reason instead.
AUTH_SESSIONS_TABLES = {AUTH_SESSIONS_TABLE: "AuthSessionRow"}

AUTH_SESSIONS_ACCEPTANCE_CASES = (
    "the_store_rows_are_exactly_the_correlation_gap_specification",
    "an_expired_row_resolves_to_none",
    "a_revoked_row_resolves_to_none",
    "a_row_for_another_correlation_id_is_not_selected",
    "a_row_whose_source_is_not_a_server_source_is_refused",
    "recording_the_same_binding_twice_is_idempotent",
    "recording_a_different_subject_for_an_existing_correlation_id_is_a_conflict",
    "recording_onto_a_revoked_row_never_resurrects_it",
    "the_table_is_outside_the_canonical_rls_set_and_says_why",
    "the_resolver_reads_the_store_without_a_tenant_guc",
)

__all__ = [
    "AUTH_SESSIONS_ACCEPTANCE_CASES",
    "AUTH_SESSIONS_NON_BLANK_TEMPLATE",
    "AUTH_SESSIONS_SUBJECT_INDEX",
    "AUTH_SESSIONS_TABLE",
    "AUTH_SESSIONS_TABLES",
    "RESOLUTION_SOURCE_CHECK",
    "AuthSessionRow",
]

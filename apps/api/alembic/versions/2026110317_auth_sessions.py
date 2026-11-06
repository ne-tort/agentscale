"""Durable ``correlation_id -> verified subject`` store (task ``MIG-AUTHSESS-01``).

Revision ``2026110317`` (``down_revision`` ``2026110316``) creates exactly one table and
one index. It installs **no** RLS policy, and that omission is the reviewed decision this
module records.

Sources of truth
================
* ``src/prodavan/application/migration/principal_resolver.py`` —
  ``AUTH_SESSION_CORRELATION_GAP`` is the specification: the nine columns, the two keys and
  the four semantic rules the resolver relies on. The column list here is copied from it
  verbatim, and ``tests/migration/auth_sessions`` asserts that it stays copied.
* ``src/prodavan/infrastructure/persistence/models/auth_sessions.py`` — the declarative
  schema source of truth this revision mirrors column for column, constraint for
  constraint and index for index. ``RESOLUTION_SOURCE_CHECK`` is imported from the model
  rather than retyped, so the database vocabulary and the Python allow-list
  (``rls.context.SERVER_RESOLUTION_SOURCES``) cannot drift.
  (``alembic/versions`` already imports from ``src``: ``2026110316`` imports
  ``rls.policies``.)

Why this table
==============
``principal_resolver.py`` could answer only for the correlation id of the request that
still held the bearer token. A queued job, a worker that woke up later or a second
replica holds no token, so its correlation id resolved to ``None`` and
``trusted_tenant_context`` refused with ``principal_not_server_resolved`` — the last
blocker between the M04 -> M06 chain and a production binding outside the request path.
``auth_sessions`` is the missing relation: one row per verified credential binding,
written by the auth boundary after signature/expiry verification
(``application/auth/auth_sessions.py``), read by
``principal_resolver.SqlAuthSessionCredentialSource``.

Decisions recorded in this revision
===================================
* **No policy, on purpose.** ``auth_sessions`` is a *platform* table, exactly like
  ``companies``/``memberships``: it is read **before** a tenant exists, to decide which
  tenant a correlation id may bind. A tenant-scoped policy would make it unreadable at the
  only moment it is needed, because the reader runs with no ``app.tenant_id`` and the
  registry refuses a predicate that would be ``false`` for a tenantless session. The
  exclusion is declared in ``rls/policies.py::EXCLUDED_TABLES`` — the same reviewed list
  that carries the other platform anchors — and its name matches no
  ``CANONICAL_TABLE_PREFIXES`` entry, so ``uncovered_relations()`` neither demands a policy
  nor hides a missing one. This revision therefore does **not** re-apply the canonical
  registry: it adds nothing to it.
* **``TEXT`` for every identifier**, ``TIMESTAMPTZ`` for the three instants: a length limit
  on a correlation id would truncate a correct id at insert time instead of refusing an
  unrepresentable one, which the writer does up front.
* **No server default anywhere.** ``issued_at``/``expires_at`` are the credential's stated
  lifetime; ``revoked_at`` stays null until someone revokes.
* **Integrity CHECKs, not policy**: non-blank correlation id and subject, a source inside
  the closed allow-list, an expiry after issuance, and a revocation that is never before
  the issuance it revokes. Each is a fact the readers rely on, and each would otherwise be
  a value they had to defend against at resolution time.
* **No foreign key.** A verified subject may not have an ``employees`` row yet (invited
  but not activated), and the identity directory — not this table — is the authority that
  turns a subject into a tenant. A FK would turn ``principal_subject_unbound`` (a refusal)
  into an insert failure at the auth boundary (a login outage).
* **One index beyond the key**: ``(subject_claim, expires_at)``, the sweep key the
  specification names for revoking or expiring every session of one subject.

What this revision does not do
==============================
No privilege/grant statement, no backfill, no data statement, no writer, no trigger, and no
change to any earlier table or revision; and it enables no resolution by itself — the
reader refuses until the auth boundary records a row (see
``AUTH_SESSION_STORE_DEFERRED`` in the model's test suite for the residual, non-schema
gaps: grants and the sweep job).
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op
from prodavan.infrastructure.persistence.models.auth_sessions import (
    AUTH_SESSIONS_NON_BLANK_TEMPLATE,
    AUTH_SESSIONS_SUBJECT_INDEX,
    AUTH_SESSIONS_TABLE,
    RESOLUTION_SOURCE_CHECK,
)

revision = "2026110317"
down_revision = "2026110316"
branch_labels = None
depends_on = None

#: The table this revision creates; also the table ``downgrade`` drops.
AUTH_SESSIONS = AUTH_SESSIONS_TABLE

#: Blank-identifier checks, imported from the model rather than retyped so the two cannot
#: drift (the module docstring's "copied, not paraphrased" rule).
_NON_BLANK = AUTH_SESSIONS_NON_BLANK_TEMPLATE

logger = logging.getLogger("alembic.runtime.migration")


def _create_auth_sessions_table() -> None:
    op.create_table(
        AUTH_SESSIONS,
        # The id the auth boundary issued for the request that carried the credential.
        sa.Column("correlation_id", sa.Text(), nullable=False),
        # Verified OIDC ``sub`` (``employees.keycloak_sub``); never a request value.
        sa.Column("subject_claim", sa.Text(), nullable=False),
        # One of ``rls.context.SERVER_RESOLUTION_SOURCES`` (the allow-list is not retyped).
        sa.Column("resolution_source", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Null until revoked; a non-null value denies immediately and is never overwritten.
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("company_id", sa.Text(), nullable=True),
        sa.Column("cabinet_id", sa.Text(), nullable=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("correlation_id"),
        sa.CheckConstraint(
            _NON_BLANK.format(column="correlation_id"),
            name="ck_auth_sessions_correlation_nonblank",
        ),
        sa.CheckConstraint(
            _NON_BLANK.format(column="subject_claim"),
            name="ck_auth_sessions_subject_nonblank",
        ),
        # The database vocabulary is the Python allow-list, rendered once.
        sa.CheckConstraint(
            RESOLUTION_SOURCE_CHECK,
            name="ck_auth_sessions_resolution_source",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name="ck_auth_sessions_expiry_after_issue",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_auth_sessions_revoked_after_issue",
        ),
    )


def upgrade() -> None:
    # 1. The store itself.
    _create_auth_sessions_table()

    # 2. The subject/expiry sweep key the specification names.
    op.create_index(
        AUTH_SESSIONS_SUBJECT_INDEX,
        AUTH_SESSIONS,
        ["subject_claim", "expires_at"],
    )

    # 3. No policy. The registry re-apply that other table-adding revisions perform is
    #    deliberately absent: this table is a platform anchor read before any tenant
    #    exists (see the module docstring). policies.uncovered_relations() stays empty
    #    because "auth_sessions" matches no CANONICAL_TABLE_PREFIXES entry.
    logger.info(
        "rls %s: created %s with no policy — platform anchor read before a tenant exists",
        revision,
        AUTH_SESSIONS,
    )


def downgrade() -> None:
    """Remove only this revision's objects, in reverse creation order."""

    op.drop_index(AUTH_SESSIONS_SUBJECT_INDEX, table_name=AUTH_SESSIONS)
    op.drop_table(AUTH_SESSIONS)
    logger.info("rls %s: dropped %s", revision, AUTH_SESSIONS)

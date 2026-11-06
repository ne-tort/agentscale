"""Behaviour of the durable store: what the reader answers, what the writer records.

The contract module (``test_auth_sessions_contract.py``) proves the *shape*. This one proves
the *decisions*:

* the **reader** (``SqlAuthSessionCredentialSource``) resolves a live row and refuses an
  unknown, expired, revoked, foreign or malformed one — never a degraded principal;
* the **writer** (``record_auth_session``) records one verified credential binding, is
  idempotent for a replay, refuses to rebind a correlation id to a different subject, and
  never resurrects a revocation;
* the resolver driven by the reader produces the principal the stored binding describes —
  that is the whole point of the table, because it is what lets a worker bind the tenant a
  request bound.

Every test takes its instants from the ``now`` fixture: one wall-clock anchor per test, so a
stored horizon and the assertion about it cannot drift apart mid-run (and a principal
validated by ``TrustedPrincipal.from_resolution`` against the real clock stays valid).

Everything runs on the SQLite harness in ``acceptance_fixtures`` (the real ``auth_sessions``
model, mirrored identity tables), so no database is required.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from prodavan.application.auth.auth_sessions import (
    AUTH_SESSION_WRITER_CALL_SITE,
    AuthSessionConflict,
    AuthSessionWriteOutcome,
    record_auth_session,
    record_verified_bearer_session,
)
from prodavan.application.migration.principal_resolver import (
    AUTH_SESSION_CORRELATION_GAP,
    AuthBoundaryPrincipalResolver,
    PrincipalRefusal,
    PrincipalRefusalRecord,
    SqlAuthSessionCredentialSource,
    SqlTenantIdentityDirectory,
    VerifiedCredential,
    build_durable_principal_resolver,
)
from prodavan.infrastructure.persistence.models.auth_sessions import AuthSessionRow
from tests.migration.principal_resolver.db_fixtures import (
    CABINET_A,
    PROJECT_A,
    SUBJECT_A,
    TENANT_A,
    TENANT_B,
    seed_baseline,
)

from .acceptance_fixtures import (
    CORRELATION,
    CORRELATION_OTHER,
    as_utc,
    auth_session_database,
    credential,
    moment,
    seed_session,
)

TENANT_ROLE = "company.admin"


@pytest.fixture()
def now() -> datetime:
    """One wall-clock anchor per test (see the module docstring)."""

    return moment()


class Recorder:
    """A ``PrincipalRefusalSink`` that keeps what it was told."""

    def __init__(self) -> None:
        self.records: list[PrincipalRefusalRecord] = []

    def record(self, record: PrincipalRefusalRecord) -> None:
        self.records.append(record)

    @property
    def reasons(self) -> list[PrincipalRefusal]:
        return [record.reason for record in self.records]


def reader(factory, anchor: datetime, **kwargs) -> SqlAuthSessionCredentialSource:
    """The SQL source under test, frozen to the test's anchor."""

    return SqlAuthSessionCredentialSource(session_factory=factory, clock=lambda: anchor, **kwargs)


# ---------------------------------------------------------------------------
# Reader: the live row
# ---------------------------------------------------------------------------


async def test_a_live_row_resolves_to_the_stored_subject_and_scope(now: datetime) -> None:
    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_session(
                session,
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(minutes=15),
                company_id=TENANT_A,
                cabinet_id=CABINET_A,
                project_id=PROJECT_A,
            )
            await session.commit()

        stored = await reader(factory, now).lookup(correlation_id=CORRELATION)

    assert isinstance(stored, VerifiedCredential)
    assert stored.correlation_id == CORRELATION
    assert stored.subject_claim == SUBJECT_A
    assert stored.resolution_source == "bearer_jwt_verified"
    assert stored.expires_at == now + timedelta(minutes=15)
    # The anchors are carried as assertions; they are not authority.
    assert stored.asserted_tenant_id == TENANT_A
    assert stored.asserted_cabinet_id == CABINET_A
    assert stored.asserted_project_id == PROJECT_A


async def test_the_stored_row_is_the_only_thing_that_answers(now: datetime) -> None:
    """No row -> ``None``: a correlation id nobody verified cannot name a subject."""

    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_session(session, issued_at=now - timedelta(minutes=1))
            await session.commit()

        recorder = Recorder()
        source = reader(factory, now, refusals=recorder)

        assert await source.lookup(correlation_id=CORRELATION_OTHER) is None
        assert await source.lookup(correlation_id=CORRELATION) is not None

    assert recorder.reasons == [PrincipalRefusal.CORRELATION_UNKNOWN]


# ---------------------------------------------------------------------------
# Reader: the negatives, each with its own reason code
# ---------------------------------------------------------------------------


async def test_an_expired_row_resolves_to_none(now: datetime) -> None:
    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_session(
                session,
                issued_at=now - timedelta(minutes=30),
                expires_at=now - timedelta(seconds=1),
            )
            await session.commit()

        recorder = Recorder()

        assert (
            await reader(factory, now, refusals=recorder).lookup(correlation_id=CORRELATION)
            is None
        )

    assert recorder.reasons == [PrincipalRefusal.CREDENTIAL_EXPIRED]


async def test_a_row_expiring_exactly_now_resolves_to_none(now: datetime) -> None:
    """``exp`` is exclusive: at the boundary instant the credential is already dead."""

    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_session(
                session, issued_at=now - timedelta(minutes=5), expires_at=now
            )
            await session.commit()

        assert await reader(factory, now).lookup(correlation_id=CORRELATION) is None


async def test_a_revoked_row_resolves_to_none_even_while_unexpired(now: datetime) -> None:
    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_session(
                session,
                issued_at=now - timedelta(minutes=5),
                expires_at=now + timedelta(hours=1),
                revoked_at=now - timedelta(minutes=1),
            )
            await session.commit()

        recorder = Recorder()

        assert (
            await reader(factory, now, refusals=recorder).lookup(correlation_id=CORRELATION)
            is None
        )

    assert recorder.reasons == [PrincipalRefusal.CREDENTIAL_REVOKED]


async def test_the_schema_refuses_a_request_derived_source(now: datetime) -> None:
    """A direct-SQL writer cannot store a source the RLS vocabulary does not know."""

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_session(session, issued_at=now - timedelta(minutes=1))
            await session.commit()
            with pytest.raises(IntegrityError, match="ck_auth_sessions_resolution_source"):
                await session.execute(
                    text(
                        "UPDATE auth_sessions SET resolution_source = 'job_payload' "
                        "WHERE correlation_id = :correlation_id"
                    ),
                    {"correlation_id": CORRELATION},
                )


async def test_the_reader_refuses_a_row_whose_source_is_not_server_side(
    now: datetime,
) -> None:
    """A stored row is data and can be wrong; it is never laundered into a credential.

    The row is written with the CHECK out of the way, which is the shape a database can
    really hold: one written before the allow-list narrowed, or by a direct-SQL writer that
    dropped the constraint.
    """

    async with auth_session_database(enforce_checks=False) as factory:
        async with factory() as session:
            await seed_session(
                session,
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(minutes=15),
                resolution_source="job_payload",
            )
            await session.commit()

        recorder = Recorder()

        assert (
            await reader(factory, now, refusals=recorder).lookup(correlation_id=CORRELATION)
            is None
        )

    assert recorder.reasons == [PrincipalRefusal.CREDENTIAL_UNVERIFIED]


@pytest.mark.parametrize("correlation_id", ["", "   ", f" {CORRELATION}"])
async def test_a_blank_or_padded_correlation_id_selects_nothing(
    now: datetime, correlation_id: str
) -> None:
    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_session(session, issued_at=now - timedelta(minutes=1))
            await session.commit()

        assert await reader(factory, now).lookup(correlation_id=correlation_id) is None


async def test_a_database_failure_is_not_an_answer() -> None:
    """An outage must not look like a revoked session: the error propagates."""

    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        await reader(BrokenFactory(), moment()).lookup(correlation_id=CORRELATION)


# ---------------------------------------------------------------------------
# Reader: end-to-end through the resolver
# ---------------------------------------------------------------------------


async def test_the_durable_source_binds_what_the_stored_binding_describes(now: datetime) -> None:
    """The point of the table: a caller with no token binds the tenant the request bound."""

    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            await seed_session(
                session,
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(minutes=15),
                company_id=TENANT_A,
                cabinet_id=CABINET_A,
                project_id=PROJECT_A,
            )
            await session.commit()

        resolver = AuthBoundaryPrincipalResolver(
            credential_source=reader(factory, now),
            directory=SqlTenantIdentityDirectory(session_factory=factory),
            clock=lambda: now,
        )
        principal = await resolver.resolve(correlation_id=CORRELATION)

    assert principal is not None
    assert principal.correlation_id == CORRELATION
    assert principal.tenant_id == TENANT_A
    assert principal.cabinet_id == CABINET_A
    assert principal.project_id == PROJECT_A
    # ``employees.id`` and the stored role — not the OIDC subject, not the row's text.
    assert principal.subject_id != SUBJECT_A
    assert principal.role == TENANT_ROLE
    assert principal.resolution_source == "bearer_jwt_verified"
    assert principal.resolved_by == "auth.bearer_jwt"


async def test_a_worker_for_an_unknown_correlation_id_gets_no_principal(now: datetime) -> None:
    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            await seed_session(
                session,
                correlation_id=CORRELATION_OTHER,
                issued_at=now - timedelta(minutes=30),
                expires_at=now - timedelta(seconds=1),
            )
            await session.commit()

        resolver = build_durable_principal_resolver(session_factory=factory, clock=lambda: now)
        assert await resolver.resolve(correlation_id="corr_cccccccccccccccccccc") is None
        # A row that exists but is expired is refused at the credential layer.
        assert await resolver.resolve(correlation_id=CORRELATION_OTHER) is None


async def test_the_principal_lifetime_is_capped_below_the_stored_expiry(
    now: datetime,
) -> None:
    """A long-lived row must not produce a long-lived principal."""

    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            await seed_session(
                session,
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=8),
            )
            await session.commit()

        resolver = build_durable_principal_resolver(session_factory=factory, clock=lambda: now)
        principal = await resolver.resolve(correlation_id=CORRELATION)

    assert principal is not None
    assert principal.expires_at <= now + timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Writer: one row per verified binding
# ---------------------------------------------------------------------------


async def test_recording_a_credential_writes_the_specification_row(now: datetime) -> None:
    async with auth_session_database() as factory:
        outcome = await record_auth_session(
            credential(
                expires_at=now + timedelta(minutes=15),
                tenant=TENANT_A,
                cabinet=CABINET_A,
                project=PROJECT_A,
            ),
            session_factory=factory,
            clock=lambda: now,
        )

        async with factory() as session:
            row = await session.get(AuthSessionRow, CORRELATION)

    assert outcome is not None
    assert outcome.outcome is AuthSessionWriteOutcome.RECORDED
    assert outcome.written is True
    assert row is not None
    assert row.subject_claim == SUBJECT_A
    assert row.resolution_source == "bearer_jwt_verified"
    assert as_utc(row.expires_at) == now + timedelta(minutes=15)
    assert as_utc(row.issued_at) == now
    assert row.revoked_at is None
    assert (row.company_id, row.cabinet_id, row.project_id) == (TENANT_A, CABINET_A, PROJECT_A)


async def test_the_written_row_is_exactly_what_the_gap_specifies(now: datetime) -> None:
    """Every specified column is populated (or deliberately null) by a real write."""

    async with auth_session_database() as factory:
        await record_auth_session(
            credential(expires_at=now + timedelta(minutes=15)),
            session_factory=factory,
            clock=lambda: now,
        )

        async with factory() as session:
            row = await session.get(AuthSessionRow, CORRELATION)

    written = {column.name for column in AuthSessionRow.__table__.columns}
    specified = {name for name, _type, _meaning in AUTH_SESSION_CORRELATION_GAP.columns}
    assert written == specified

    assert row is not None
    assert row.correlation_id and row.subject_claim and row.resolution_source
    assert row.issued_at is not None and row.expires_at is not None
    # A credential that asserted no scope stores nulls, not empty strings.
    assert (row.company_id, row.cabinet_id, row.project_id) == (None, None, None)


async def test_recording_the_same_binding_twice_is_a_replay(now: datetime) -> None:
    async with auth_session_database() as factory:
        binding = credential(expires_at=now + timedelta(minutes=15))
        first = await record_auth_session(binding, session_factory=factory, clock=lambda: now)
        # A retry one minute later: same credential, same binding, different ``issued_at``.
        second = await record_auth_session(
            binding,
            session_factory=factory,
            clock=lambda: now + timedelta(minutes=1),
        )

        async with factory() as session:
            rows = (await session.execute(AuthSessionRow.__table__.select())).all()

    assert first is not None and first.outcome is AuthSessionWriteOutcome.RECORDED
    assert second is not None and second.outcome is AuthSessionWriteOutcome.REPLAYED
    assert second.written is False
    assert len(rows) == 1, "a replay must not append a second row"
    assert as_utc(rows[0].issued_at) == now, "the original issuance instant is not rewritten"


async def test_rebinding_a_correlation_id_to_another_subject_is_a_conflict(
    now: datetime,
) -> None:
    async with auth_session_database() as factory:
        await record_auth_session(
            credential(expires_at=now + timedelta(minutes=15)),
            session_factory=factory,
            clock=lambda: now,
        )

        with pytest.raises(AuthSessionConflict) as raised:
            await record_auth_session(
                credential(
                    subject_claim="kc-somebody-else", expires_at=now + timedelta(minutes=15)
                ),
                session_factory=factory,
                clock=lambda: now,
            )

        async with factory() as session:
            row = await session.get(AuthSessionRow, CORRELATION)

    assert raised.value.correlation_id == CORRELATION
    assert raised.value.differing == ("subject_claim",)
    # The message names the field, never a claim value.
    assert "kc-somebody-else" not in str(raised.value)
    assert row is not None and row.subject_claim == SUBJECT_A


@pytest.mark.parametrize(
    "changed",
    [
        {"tenant": TENANT_B},
        {"cabinet": CABINET_A},
        {"expiry_minutes": 60},
        {"resolution_source": "session_cookie"},
    ],
    ids=["tenant", "cabinet", "expiry", "source"],
)
async def test_any_different_binding_under_one_correlation_id_is_a_conflict(
    now: datetime, changed: dict
) -> None:
    async with auth_session_database() as factory:
        await record_auth_session(
            credential(expires_at=now + timedelta(minutes=15)),
            session_factory=factory,
            clock=lambda: now,
        )

        options = dict(changed)
        expiry_minutes = options.pop("expiry_minutes", 15)
        with pytest.raises(AuthSessionConflict):
            await record_auth_session(
                credential(
                    expires_at=now + timedelta(minutes=expiry_minutes),
                    **options,
                ),
                session_factory=factory,
                clock=lambda: now,
            )


async def test_a_revoked_row_is_never_resurrected(now: datetime) -> None:
    async with auth_session_database() as factory:
        async with factory() as session:
            await seed_session(
                session,
                issued_at=now - timedelta(minutes=10),
                expires_at=now + timedelta(hours=1),
                revoked_at=now - timedelta(minutes=5),
            )
            await session.commit()

        outcome = await record_auth_session(
            credential(expires_at=now + timedelta(hours=1)),
            session_factory=factory,
            clock=lambda: now,
        )

        async with factory() as session:
            row = await session.get(AuthSessionRow, CORRELATION)

        # …and the reader still refuses it.
        still_refused = await reader(factory, now).lookup(correlation_id=CORRELATION)

    assert outcome is not None
    assert outcome.outcome is AuthSessionWriteOutcome.REVOKED
    assert row is not None and row.revoked_at is not None
    assert still_refused is None


async def test_an_already_expired_credential_is_not_recorded(now: datetime) -> None:
    """Dead evidence would make an expired binding look live to a sweep."""

    async with auth_session_database() as factory:
        outcome = await record_auth_session(
            credential(expires_at=now - timedelta(seconds=1)),
            session_factory=factory,
            clock=lambda: now,
        )

        async with factory() as session:
            row = await session.get(AuthSessionRow, CORRELATION)

    assert outcome is None
    assert row is None


async def test_the_writer_refuses_a_bare_subject_pair(now: datetime) -> None:
    """No overload lets a caller name a subject it never proved."""

    async with auth_session_database() as factory:
        with pytest.raises(TypeError, match="VerifiedCredential"):
            await record_auth_session(
                {"correlation_id": CORRELATION, "subject_claim": SUBJECT_A},  # type: ignore[arg-type]
                session_factory=factory,
                clock=lambda: now,
            )


# ---------------------------------------------------------------------------
# Writer: the verified-claims helper (what the call site uses)
# ---------------------------------------------------------------------------


async def test_a_verified_claim_set_is_recorded_and_a_dead_one_is_not(now: datetime) -> None:
    async with auth_session_database() as factory:
        live = await record_verified_bearer_session(
            token="opaque",
            correlation_id=CORRELATION,
            claims={
                "sub": SUBJECT_A,
                "exp": int((now + timedelta(minutes=10)).timestamp()),
                "company_id": TENANT_A,
            },
            session_factory=factory,
            clock=lambda: now,
        )
        expired = await record_verified_bearer_session(
            token="opaque",
            correlation_id=CORRELATION_OTHER,
            claims={
                "sub": SUBJECT_A,
                "exp": int((now - timedelta(minutes=10)).timestamp()),
            },
            session_factory=factory,
            clock=lambda: now,
        )

    assert live is not None and live.written is True
    assert expired is None


async def test_a_claim_set_without_a_subject_or_expiry_is_not_recorded(now: datetime) -> None:
    async with auth_session_database() as factory:
        for claims in (
            {"exp": int((now + timedelta(minutes=10)).timestamp())},
            {"sub": "", "exp": int((now + timedelta(minutes=10)).timestamp())},
            {"sub": SUBJECT_A},
        ):
            assert (
                await record_verified_bearer_session(
                    token="opaque",
                    correlation_id=CORRELATION,
                    claims=claims,
                    session_factory=factory,
                    clock=lambda: now,
                )
                is None
            )


async def test_the_helper_verifies_when_it_is_given_only_a_token(now: datetime) -> None:
    """No silent payload decoding: an unverifiable token is simply not recorded."""

    def rejecting(_token: str) -> dict:
        raise ValueError("signature mismatch")

    async with auth_session_database() as factory:
        assert (
            await record_verified_bearer_session(
                token="not-a-jwt",
                correlation_id=CORRELATION,
                verifier=rejecting,
                session_factory=factory,
                clock=lambda: now,
            )
            is None
        )


def test_the_call_site_is_named_and_lives_on_the_auth_boundary() -> None:
    """The writer's one call site is recorded where a reviewer can check it."""

    from pathlib import Path

    from prodavan.api import deps

    assert AUTH_SESSION_WRITER_CALL_SITE == "prodavan.api.deps.get_principal"
    source = deps.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    assert "record_verified_bearer_session" in text
    assert "get_jwt_validator().validate(token)" in text
    # The writer runs *after* validation: the recorded subject is the validator's answer.
    assert text.index("get_jwt_validator().validate(token)") < text.index(
        "_record_auth_session(request"
    )

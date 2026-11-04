"""The verified-credential half of the resolver: what counts as proof of a subject.

Every case here is about authority #1 — *who is calling*.  The rule under test is that the
correlation id only selects a credential the auth boundary already verified, and that nothing
else (a claim in the token, a second correlation id, an unverified or unbounded token) can
produce a credential.  The identity tables are irrelevant at this layer, so no database is
involved.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import jwt
import pytest

from prodavan.application.migration.principal_resolver import (
    BearerJwtCredentialSource,
    VerifiedCredential,
)
from tests.migration.principal_resolver.db_fixtures import (
    clock_now,
    CORRELATION,
    CORRELATION_OTHER,
    SUBJECT_A,
    frozen_clock,
)

SECRET = "test-only-secret-long-enough-for-hs256"

#: JWT ``exp`` is a NumericDate (epoch seconds), never a datetime.
EXP_IN_15_MIN = int((clock_now() + timedelta(minutes=15)).timestamp())
EXP_NOW = int(clock_now().timestamp())


def exp_in(delta: timedelta) -> int:
    return int((clock_now() + delta).timestamp())


def token_for(**claims: object) -> str:
    payload = {"sub": SUBJECT_A, "exp": EXP_IN_15_MIN, **claims}
    return jwt.encode(payload, SECRET, algorithm="HS256")


def verifier_returning(claims: dict[str, object]):
    def verify(_token: str) -> dict[str, object]:
        return dict(claims)

    return verify


def source(*, token: str | None = None, claims: dict[str, object] | None = None, clock=None):
    return BearerJwtCredentialSource(
        correlation_id=CORRELATION,
        token=token if token is not None else token_for(),
        verifier=verifier_returning(
            claims if claims is not None else {"sub": SUBJECT_A, "exp": EXP_IN_15_MIN}
        ),
        clock=clock or frozen_clock(),
    )


# ---------------------------------------------------------------------------
# Only the correlation id it was issued for
# ---------------------------------------------------------------------------


async def test_answers_for_its_own_correlation_id_only() -> None:
    credential_source = source()

    assert await credential_source.lookup(correlation_id=CORRELATION_OTHER) is None
    credential = await credential_source.lookup(correlation_id=CORRELATION)
    assert isinstance(credential, VerifiedCredential)
    assert credential.correlation_id == CORRELATION
    assert credential.subject_claim == SUBJECT_A
    assert credential.resolution_source == "bearer_jwt_verified"


async def test_an_empty_correlation_id_selects_nothing() -> None:
    credential_source = source()

    assert await credential_source.lookup(correlation_id="") is None
    assert await credential_source.lookup(correlation_id="   ") is None


# ---------------------------------------------------------------------------
# The credential must be alive and bounded
# ---------------------------------------------------------------------------


async def test_expired_credential_is_refused() -> None:
    credential_source = source(
        claims={"sub": SUBJECT_A, "exp": exp_in(timedelta(seconds=-1))},
    )

    assert await credential_source.lookup(correlation_id=CORRELATION) is None


async def test_credential_expiring_exactly_now_is_refused() -> None:
    """``exp`` is exclusive: at the boundary instant the token is already dead."""

    credential_source = source(claims={"sub": SUBJECT_A, "exp": EXP_NOW})

    assert await credential_source.lookup(correlation_id=CORRELATION) is None


async def test_credential_without_usable_expiry_is_refused() -> None:
    """An unbounded credential cannot produce a principal with a real ``expires_at``."""

    assert await source(claims={"sub": SUBJECT_A}).lookup(correlation_id=CORRELATION) is None
    assert await source(claims={"sub": SUBJECT_A, "exp": "soon"}).lookup(correlation_id=CORRELATION) is None


async def test_revocation_takes_effect_without_a_cache() -> None:
    """A token that expires between two lookups stops resolving: nothing is memoized."""

    moment = [clock_now()]
    credential_source = BearerJwtCredentialSource(
        correlation_id=CORRELATION,
        token=token_for(),
        verifier=verifier_returning({"sub": SUBJECT_A, "exp": exp_in(timedelta(minutes=1))}),
        clock=lambda: moment[0],
    )

    assert await credential_source.lookup(correlation_id=CORRELATION) is not None
    moment[0] = clock_now() + timedelta(minutes=2)
    assert await credential_source.lookup(correlation_id=CORRELATION) is None


# ---------------------------------------------------------------------------
# Unverifiable material never becomes a credential
# ---------------------------------------------------------------------------


async def test_verifier_failure_is_a_denial_not_an_exception() -> None:
    def explode(_token: str) -> dict[str, object]:
        raise ValueError("signature mismatch")

    credential_source = BearerJwtCredentialSource(
        correlation_id=CORRELATION, token="not-a-jwt", verifier=explode, clock=frozen_clock()
    )

    assert await credential_source.lookup(correlation_id=CORRELATION) is None


async def test_missing_subject_claim_is_refused() -> None:
    assert await source(claims={"sub": "", "exp": exp_in(timedelta(minutes=5))}).lookup(
        correlation_id=CORRELATION
    ) is None
    assert await source(claims={"exp": exp_in(timedelta(minutes=5))}).lookup(
        correlation_id=CORRELATION
    ) is None


async def test_default_verifier_uses_the_repository_jwt_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default path must verify through ``infrastructure/auth/jwt.py``.

    A token whose *payload* names another subject is not enough: the validator's answer is the
    subject, so a caller cannot choose who it is by editing an unverified payload.
    """

    from prodavan.domain.identity import Principal
    from prodavan.infrastructure.auth import jwt as jwt_module

    seen: list[str] = []

    class Validator:
        def validate(self, token: str) -> Principal:
            seen.append(token)
            return Principal(sub="verified-subject", roles=frozenset({"employee"}))

    monkeypatch.setattr(jwt_module, "get_jwt_validator", lambda: Validator())

    credential_source = BearerJwtCredentialSource(
        correlation_id=CORRELATION,
        token=token_for(sub="payload-subject"),
        clock=frozen_clock(),
    )
    credential = await credential_source.lookup(correlation_id=CORRELATION)

    assert seen, "the JWKS validator must actually be consulted"
    assert credential is not None
    assert credential.subject_claim == "verified-subject"


async def test_default_verifier_refuses_a_token_the_validator_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prodavan.infrastructure.auth import jwt as jwt_module

    class Validator:
        def validate(self, _token: str) -> object:
            raise ValueError("Invalid or expired token")

    monkeypatch.setattr(jwt_module, "get_jwt_validator", lambda: Validator())

    credential_source = BearerJwtCredentialSource(
        correlation_id=CORRELATION, token=token_for(), clock=frozen_clock()
    )

    assert await credential_source.lookup(correlation_id=CORRELATION) is None


# ---------------------------------------------------------------------------
# Assertions travel, but are only assertions
# ---------------------------------------------------------------------------


async def test_tenant_assertions_are_carried_not_authoritative() -> None:
    """Claims become ``asserted_*``; the resolver only uses them to select a membership."""

    credential = await source(
        claims={
            "sub": SUBJECT_A,
            "exp": exp_in(timedelta(minutes=5)),
            "company_id": "comp_bbbbbbbbbbbbbbbbbbbb",
            "cabinet_id": "cab_bbbbbbbbbbbbbbbbbbbb",
            "project_id": "prj_bbbbbbbbbbbbbbbbbbbb",
        }
    ).lookup(correlation_id=CORRELATION)

    assert credential is not None
    assert credential.asserted_tenant_id == "comp_bbbbbbbbbbbbbbbbbbbb"
    assert credential.asserted_cabinet_id == "cab_bbbbbbbbbbbbbbbbbbbb"
    assert credential.asserted_project_id == "prj_bbbbbbbbbbbbbbbbbbbb"


async def test_blank_assertions_assert_nothing() -> None:
    credential = await source(
        claims={"sub": SUBJECT_A, "exp": exp_in(timedelta(minutes=5)), "company_id": ""}
    ).lookup(correlation_id=CORRELATION)

    assert credential is not None
    assert credential.asserted_tenant_id is None


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("resolution_source", ["request_claim", "header", "plugin", "job_payload", ""])
def test_request_derived_sources_are_refused_at_construction(resolution_source: str) -> None:
    with pytest.raises(ValueError):
        BearerJwtCredentialSource(
            correlation_id=CORRELATION,
            token=token_for(),
            verifier=verifier_returning({"sub": SUBJECT_A}),
            resolution_source=resolution_source,
            clock=frozen_clock(),
        )


def test_source_requires_a_correlation_id_and_a_token() -> None:
    with pytest.raises(ValueError):
        BearerJwtCredentialSource(correlation_id="", token=token_for(), clock=frozen_clock())
    with pytest.raises(ValueError):
        BearerJwtCredentialSource(correlation_id=CORRELATION, token="  ", clock=frozen_clock())


def test_credential_requires_aware_expiry_and_a_server_side_source() -> None:
    with pytest.raises(ValueError):
        VerifiedCredential(
            correlation_id=CORRELATION,
            subject_claim=SUBJECT_A,
            resolution_source="bearer_jwt_verified",
            expires_at=datetime(2026, 11, 4, 10, 0),  # naive
        )
    with pytest.raises(ValueError):
        VerifiedCredential(
            correlation_id=CORRELATION,
            subject_claim=SUBJECT_A,
            resolution_source="job_payload",
            expires_at=clock_now(),
        )
    with pytest.raises(ValueError):
        VerifiedCredential(
            correlation_id=CORRELATION,
            subject_claim="",
            resolution_source="bearer_jwt_verified",
            expires_at=clock_now(),
        )

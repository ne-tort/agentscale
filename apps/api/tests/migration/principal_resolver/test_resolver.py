"""The resolver as a whole: correlation id -> sealed ``TrustedPrincipal``, or ``None``.

The obligations proven here are the ones the RLS adapters rely on:

* the protocol is satisfied structurally and the principal is *sealed* by
  ``issue_server_resolution`` / ``from_resolution`` (never assembled directly);
* every field comes from a stored row, and an assertion is only a key — never the value;
* ``expires_at`` is always present, always aware and always in the future;
* revocation (membership gone, employee disabled, credential expired, grant revoked) is
  ``None`` rather than an exception, and a database failure is *not* silently a ``None``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from prodavan.application.migration.principal_resolver import (
    DEFAULT_MAX_PRINCIPAL_TTL,
    AuthBoundaryPrincipalResolver,
    PrincipalRefusal,
    PrincipalRefusalRecord,
    SqlTenantIdentityDirectory,
    TenantIdentity,
    TenantIdentityRefusal,
    VerifiedCredential,
    build_sql_principal_resolver,
)
from prodavan.application.migration.tenant_context import (
    RlsTenantScopeContextAdapter,
    RlsTrustedTenantContextAdapter,
    TrustedPrincipalResolver,
)
from prodavan.infrastructure.persistence.rls.context import (
    RlsContextError,
    TrustedPrincipal,
)
from tests.migration.principal_resolver.db_fixtures import (
    CABINET_A,
    CABINET_B,
    CLOCK_NOW,
    CORRELATION,
    CORRELATION_OTHER,
    EMPLOYEE_A,
    PROJECT_A,
    SUBJECT_A,
    TENANT_A,
    TENANT_B,
    frozen_clock,
    identity_database,
    seed_baseline,
    seed_cabinet,
    seed_company,
    seed_employee,
    seed_membership,
)


class CredentialSource:
    """Auth-boundary double: one verified credential, or nothing."""

    def __init__(self, credential: VerifiedCredential | None) -> None:
        self._credential = credential
        self.calls: list[str] = []

    async def lookup(self, *, correlation_id: str) -> VerifiedCredential | None:
        self.calls.append(correlation_id)
        return self._credential


class RaisingDirectory:
    async def identity_for(self, **_kwargs: object) -> TenantIdentity | TenantIdentityRefusal:
        raise RuntimeError("the platform database is unreachable")


def credential(
    *,
    correlation_id: str = CORRELATION,
    subject: str = SUBJECT_A,
    ttl: timedelta = timedelta(minutes=15),
    source: str = "bearer_jwt_verified",
    tenant: str | None = None,
    cabinet: str | None = None,
    project: str | None = None,
) -> VerifiedCredential:
    return VerifiedCredential(
        correlation_id=correlation_id,
        subject_claim=subject,
        resolution_source=source,
        expires_at=CLOCK_NOW + ttl,
        asserted_tenant_id=tenant,
        asserted_cabinet_id=cabinet,
        asserted_project_id=project,
    )


def resolver(factory, *, cred: VerifiedCredential | None = None, **kwargs) -> AuthBoundaryPrincipalResolver:
    return AuthBoundaryPrincipalResolver(
        credential_source=CredentialSource(cred if cred is not None else credential()),
        directory=SqlTenantIdentityDirectory(session_factory=factory),
        clock=frozen_clock(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Contract conformance
# ---------------------------------------------------------------------------


async def test_resolver_satisfies_the_protocol_and_the_adapters_accept_it() -> None:
    async with identity_database() as factory:
        instance = resolver(factory)

        assert isinstance(instance, TrustedPrincipalResolver)
        assert callable(instance.resolve)
        # The tenant-context adapters gate on ``callable(resolve)``; a resolver that fails
        # that gate is a stage that never becomes production-binding.
        assert RlsTenantScopeContextAdapter(principal_resolver=instance)
        assert RlsTrustedTenantContextAdapter(principal_resolver=instance)


async def test_construction_refuses_a_credential_source_that_cannot_look_up() -> None:
    with pytest.raises(ValueError):
        AuthBoundaryPrincipalResolver(credential_source=object(), directory=RaisingDirectory())
    with pytest.raises(ValueError):
        AuthBoundaryPrincipalResolver(credential_source=CredentialSource(None), directory=object())


@pytest.mark.parametrize("max_ttl", [timedelta(0), timedelta(seconds=-1), "5 minutes"])
async def test_max_ttl_must_be_a_positive_timedelta(max_ttl: object) -> None:
    with pytest.raises(ValueError):
        AuthBoundaryPrincipalResolver(
            credential_source=CredentialSource(None),
            directory=RaisingDirectory(),
            max_ttl=max_ttl,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Positive path: every field is a stored value
# ---------------------------------------------------------------------------


async def test_principal_fields_come_from_the_identity_tables() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        principal = await resolver(factory).resolve(correlation_id=CORRELATION)

    assert isinstance(principal, TrustedPrincipal)
    assert principal.tenant_id == TENANT_A
    assert principal.subject_id == EMPLOYEE_A
    assert principal.role == "company.admin"
    assert principal.cabinet_id is None
    assert principal.project_id is None
    assert principal.correlation_id == CORRELATION
    assert principal.resolution_source == "bearer_jwt_verified"
    assert principal.resolved_by == "auth.bearer_jwt"
    assert principal.digest.startswith("sha256:")
    # The seal is real: the principal re-validates, including its digest and lifetime.
    principal.validate(now=CLOCK_NOW)


async def test_project_scope_is_derived_from_the_project_row() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        principal = await resolver(factory, cred=credential(project=PROJECT_A)).resolve(
            correlation_id=CORRELATION
        )

    assert isinstance(principal, TrustedPrincipal)
    assert principal.project_id == PROJECT_A
    assert principal.cabinet_id == CABINET_A
    assert principal.tenant_id == TENANT_A


async def test_assertion_cannot_move_the_tenant() -> None:
    """A credential that names a foreign tenant does not become that tenant's principal.

    The claimed tenant is only a key into the membership set; there is no membership there,
    so the answer is ``None`` — not a principal for ``TENANT_B`` and not a silent fallback to
    the employee's real tenant.
    """

    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        principal = await resolver(factory, cred=credential(tenant=TENANT_B)).resolve(
            correlation_id=CORRELATION
        )

    assert principal is None


async def test_assertion_cannot_move_the_cabinet() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            await seed_cabinet(session, cabinet_id=CABINET_B)
            await session.commit()

        principal = await resolver(factory, cred=credential(cabinet=CABINET_B)).resolve(
            correlation_id=CORRELATION
        )

    assert principal is None


# ---------------------------------------------------------------------------
# Lifetime
# ---------------------------------------------------------------------------


async def test_lifetime_is_capped_by_max_ttl() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        principal = await resolver(
            factory, cred=credential(ttl=timedelta(hours=12)), max_ttl=timedelta(minutes=5)
        ).resolve(correlation_id=CORRELATION)

    assert isinstance(principal, TrustedPrincipal)
    assert principal.expires_at == CLOCK_NOW + timedelta(minutes=5)


async def test_lifetime_follows_a_shorter_credential_expiry() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        principal = await resolver(factory, cred=credential(ttl=timedelta(seconds=45))).resolve(
            correlation_id=CORRELATION
        )

    assert isinstance(principal, TrustedPrincipal)
    assert principal.expires_at == CLOCK_NOW + timedelta(seconds=45)
    assert DEFAULT_MAX_PRINCIPAL_TTL > timedelta(0)


async def test_expired_credential_yields_no_principal() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        principal = await resolver(factory, cred=credential(ttl=timedelta(seconds=-1))).resolve(
            correlation_id=CORRELATION
        )

    assert principal is None


# ---------------------------------------------------------------------------
# Revocations: None, never an exception
# ---------------------------------------------------------------------------


async def test_unknown_correlation_id_yields_no_principal() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        assert await resolver(factory).resolve(correlation_id=CORRELATION_OTHER) is None


@pytest.mark.parametrize("correlation_id", ["", "   ", " corr_padded "])
async def test_malformed_correlation_id_yields_no_principal(correlation_id: str) -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        assert await resolver(factory).resolve(correlation_id=correlation_id) is None


async def test_credential_for_another_correlation_id_is_refused() -> None:
    """A source that hands back somebody else's credential must not resolve this id."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        instance = resolver(factory, cred=credential(correlation_id=CORRELATION_OTHER))
        assert await instance.resolve(correlation_id=CORRELATION) is None


async def test_over_long_correlation_id_yields_no_principal() -> None:
    """The RLS vocabulary caps identity width at 40 characters.

    A correlation id past that cap cannot be sealed into a resolution, and the resolver turns
    the seal's refusal into ``None`` rather than truncating the id — a truncated correlation
    would be a *different* correlation id, i.e. forged provenance.
    """

    long_correlation = "corr_" + "a" * 60
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        instance = resolver(factory, cred=credential(correlation_id=long_correlation))
        assert await instance.resolve(correlation_id=long_correlation) is None


async def test_disabled_employee_yields_no_principal() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session, status="disabled")
            await seed_membership(session)
            await session.commit()

        assert await resolver(factory).resolve(correlation_id=CORRELATION) is None


async def test_removed_membership_yields_no_principal() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            from prodavan.infrastructure.persistence.models.identity import MembershipRow

            membership = await session.get(MembershipRow, f"mem_{TENANT_A[-8:]}{EMPLOYEE_A[-8:]}")
            assert membership is not None
            await session.delete(membership)
            await session.commit()

        assert await resolver(factory).resolve(correlation_id=CORRELATION) is None


async def test_unrepresentable_identity_is_refused_rather_than_truncated() -> None:
    """An id the RLS vocabulary cannot express is a refusal, never a mangled tenant."""

    long_tenant = "comp_" + "a" * 60
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session, company_id=long_tenant)
            await seed_employee(session)
            await seed_membership(session, company_id=long_tenant)
            await session.commit()

        principal = await resolver(factory).resolve(correlation_id=CORRELATION)

    assert principal is None


async def test_migration_operator_source_cannot_be_claimed_by_a_tenant_role() -> None:
    """``from_resolution`` refuses a ``migration_operator`` that is not ``migration_role``.

    The resolver must surface that as a refusal, not raise, and must not rewrite the role to
    make the seal succeed.
    """

    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        principal = await resolver(
            factory, cred=credential(source="migration_operator")
        ).resolve(correlation_id=CORRELATION)

    assert principal is None


async def test_migration_operator_source_resolves_for_the_migration_role() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session)
            await seed_membership(session, role="migration_role")
            await session.commit()

        principal = await resolver(
            factory, cred=credential(source="migration_operator")
        ).resolve(correlation_id=CORRELATION)

    assert isinstance(principal, TrustedPrincipal)
    assert principal.resolution_source == "migration_operator"
    assert principal.resolved_by == "auth.migration_operator"
    assert principal.role == "migration_role"


# ---------------------------------------------------------------------------
# A database failure is not a denial
# ---------------------------------------------------------------------------


async def test_database_failure_propagates_instead_of_becoming_none() -> None:
    """Returning ``None`` on an outage would forge revoked-access evidence out of an error."""

    instance = AuthBoundaryPrincipalResolver(
        credential_source=CredentialSource(credential()),
        directory=RaisingDirectory(),
        clock=frozen_clock(),
    )

    with pytest.raises(RuntimeError):
        await instance.resolve(correlation_id=CORRELATION)


# ---------------------------------------------------------------------------
# Refusal evidence
# ---------------------------------------------------------------------------


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[PrincipalRefusalRecord] = []

    def record(self, record: PrincipalRefusalRecord) -> None:
        self.records.append(record)


async def test_refusals_are_recorded_as_reason_codes_without_payloads() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session, status="disabled")
            await seed_membership(session)
            await session.commit()

        sink = RecordingSink()
        principal = await resolver(factory, refusals=sink).resolve(correlation_id=CORRELATION)

    assert principal is None
    assert [record.reason for record in sink.records] == [PrincipalRefusal.SUBJECT_INACTIVE]
    assert sink.records[0].correlation_id == CORRELATION
    # Only identities and a reason code: no token, no claims, no row.
    assert "disabled" in sink.records[0].detail


async def test_a_failing_sink_does_not_change_the_decision() -> None:
    class BrokenSink:
        def record(self, _record: PrincipalRefusalRecord) -> None:
            raise RuntimeError("metrics backend down")

    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        assert await resolver(factory, refusals=BrokenSink()).resolve(
            correlation_id=CORRELATION_OTHER
        ) is None


async def test_resolution_does_not_read_the_connection_or_row_of_another_tenant() -> None:
    """Two subjects, two tenants: each correlation resolves to its own tenant only."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session, company_id=TENANT_A)
            await seed_company(session, company_id=TENANT_B)
            await seed_employee(session)
            await seed_membership(session, company_id=TENANT_A, role="member")
            from prodavan.infrastructure.persistence.models.identity import EmployeeRow

            session.add(
                EmployeeRow(
                    id="emp_bbbbbbbbbbbbbbbbbbbb",
                    keycloak_sub="kc-subject-b",
                    login="b@agentscale.local",
                    email="b@agentscale.local",
                    status="active",
                )
            )
            await session.flush()
            await seed_membership(
                session, employee_id="emp_bbbbbbbbbbbbbbbbbbbb", company_id=TENANT_B, role="member"
            )
            await session.commit()

        first = await resolver(factory, cred=credential()).resolve(correlation_id=CORRELATION)
        second = await resolver(
            factory,
            cred=credential(correlation_id=CORRELATION_OTHER, subject="kc-subject-b"),
        ).resolve(correlation_id=CORRELATION_OTHER)

    assert isinstance(first, TrustedPrincipal) and isinstance(second, TrustedPrincipal)
    assert first.tenant_id == TENANT_A
    assert second.tenant_id == TENANT_B
    assert first.digest != second.digest


# ---------------------------------------------------------------------------
# Wiring helper
# ---------------------------------------------------------------------------


async def test_build_helper_assembles_a_working_resolver() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        instance = build_sql_principal_resolver(
            credential_source=CredentialSource(credential()), session_factory=factory, clock=frozen_clock()
        )
        principal = await instance.resolve(correlation_id=CORRELATION)

    assert isinstance(principal, TrustedPrincipal)
    assert principal.tenant_id == TENANT_A


async def test_principal_is_refused_by_rls_context_when_it_expires() -> None:
    """Defence in depth: a principal that aged out is refused by ``rls.context`` itself."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        principal = await resolver(factory, cred=credential(ttl=timedelta(minutes=1))).resolve(
            correlation_id=CORRELATION
        )

    assert isinstance(principal, TrustedPrincipal)
    with pytest.raises(RlsContextError) as excinfo:
        principal.validate(now=CLOCK_NOW + timedelta(minutes=2))
    assert excinfo.value.reason_code == "tenant_context_expired"

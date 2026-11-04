"""The seam: the resolver actually drives the tenant-context adapters.

`tests/migration/tenant_context/` proves the adapters against a stub resolver;
`test_resolver.py` proves this resolver in isolation.  Neither answers the question that
matters for the migration chain: with a *real* resolver, does a bind succeed with the stored
tenant, and does a lying caller still get refused before any SQL?

The adapters run on the shared in-memory PostgreSQL double (transaction-local GUCs, the
reviewed policy manifest, ``pg_roles``) while the resolver reads its identity rows from the
SQLite harness — two implementations of the same decisions, which is what makes the seam
worth asserting.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from prodavan.application.migration.m04_scope.scope_migration import MigrationEvidenceContext
from prodavan.application.migration.m04_scope.scope_persistence import TenantScopeMode
from prodavan.application.migration.principal_resolver import (
    AuthBoundaryPrincipalResolver,
    SqlTenantIdentityDirectory,
    VerifiedCredential,
)
from prodavan.application.migration.tenant_context import (
    RlsTenantScopeContextAdapter,
    RlsTrustedTenantContextAdapter,
)
from prodavan.application.scope_registry.contracts import RlsContext
from prodavan.infrastructure.persistence.rls.context import RlsContextError
from tests.migration.principal_resolver.db_fixtures import (
    CABINET_A,
    clock_now,
    CORRELATION,
    EMPLOYEE_A,
    PROJECT_A,
    SUBJECT_A,
    TENANT_A,
    TENANT_B,
    frozen_clock,
    identity_database,
    seed_baseline,
    seed_company,
    seed_employee,
)
from tests.migration.tenant_context.postgres_double import (
    APPLICATION_TRANSACTION,
    SESSION_A,
    WRITER_GENERATION,
    FakeAsyncSession,
    FakePostgres,
)

TENANT_ROLE = "company.admin"


class Source:
    def __init__(self, credential: VerifiedCredential) -> None:
        self._credential = credential

    async def lookup(self, *, correlation_id: str):
        return self._credential if correlation_id == self._credential.correlation_id else None


def credential(**overrides: object) -> VerifiedCredential:
    """``project``/``cabinet``/``tenant`` name assertions, matching the credential's fields."""

    aliases = {
        "project": "asserted_project_id",
        "cabinet": "asserted_cabinet_id",
        "tenant": "asserted_tenant_id",
    }
    values: dict[str, object] = {
        "correlation_id": CORRELATION,
        "subject_claim": SUBJECT_A,
        "resolution_source": "bearer_jwt_verified",
        "expires_at": clock_now() + timedelta(minutes=10),
    }
    for name, value in overrides.items():
        values[aliases.get(name, name)] = value
    return VerifiedCredential(**values)  # type: ignore[arg-type]


def resolver(factory, **overrides: object) -> AuthBoundaryPrincipalResolver:
    return AuthBoundaryPrincipalResolver(
        credential_source=Source(credential(**overrides)),
        directory=SqlTenantIdentityDirectory(session_factory=factory),
        clock=frozen_clock(),
    )


def m05_adapter(instance: AuthBoundaryPrincipalResolver) -> RlsTrustedTenantContextAdapter:
    return RlsTrustedTenantContextAdapter(principal_resolver=instance)


def m04_adapter(instance: AuthBoundaryPrincipalResolver) -> RlsTenantScopeContextAdapter:
    return RlsTenantScopeContextAdapter(principal_resolver=instance)


def m04_context(
    *,
    tenant_id: str = TENANT_A,
    cabinet_id: str | None = None,
    project_id: str | None = None,
    user_id: str = EMPLOYEE_A,
    role: str = TENANT_ROLE,
    correlation_id: str = CORRELATION,
) -> MigrationEvidenceContext:
    """A self-consistent M04 context: ``rls_context`` always mirrors the asserted anchors.

    ``MigrationEvidenceContext.usable`` requires the RLS context to agree with the outer
    fields, so a context that disagrees with *itself* is refused before the resolver is even
    consulted.  These helpers therefore keep the context coherent and let each test make it
    disagree with the *directory* instead, which is the case under test.
    """

    return MigrationEvidenceContext(
        tenant_id=tenant_id,
        authenticated=True,
        transaction_id=APPLICATION_TRANSACTION,
        correlation_id=correlation_id,
        user_id=user_id,
        role=role,
        rls_context=RlsContext(
            tenant_id=tenant_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            user_id=user_id,
            role=role,
        ),
        writer_generation=WRITER_GENERATION,
    )


# ---------------------------------------------------------------------------
# M05: tenant-wide bind
# ---------------------------------------------------------------------------


async def test_m05_bind_writes_the_stored_tenant_and_employee_id() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        pg = FakePostgres()
        session = FakeAsyncSession(pg)
        port = m05_adapter(resolver(factory))

        async with session.begin():
            binding = await port.bind(
                session=session,
                tenant_id=TENANT_A,
                subject_id=EMPLOYEE_A,
                session_id=SESSION_A,
                correlation_id=CORRELATION,
                trusted=False,
            )

            assert binding.tenant_id == TENANT_A
            assert binding.correlation_id == CORRELATION
            assert binding.all_verified() is True
            # ``employees.id`` — not the OIDC subject that arrived in the credential.
            assert pg.setting("app.user_id") == EMPLOYEE_A
            assert pg.setting("app.tenant_id") == TENANT_A
            assert pg.setting("app.role") == TENANT_ROLE


async def test_m05_bind_refuses_a_caller_that_names_another_tenant() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        pg = FakePostgres()
        session = FakeAsyncSession(pg)
        port = m05_adapter(resolver(factory))

        async with session.begin():
            with pytest.raises(RlsContextError) as excinfo:
                await port.bind(
                    session=session,
                    tenant_id=TENANT_B,
                    subject_id=EMPLOYEE_A,
                    session_id=SESSION_A,
                    correlation_id=CORRELATION,
                    trusted=False,
                )

        assert excinfo.value.reason_code == "cross_tenant_context"
        assert pg.statements == [], "a refused bind must not emit SQL"


async def test_m05_bind_refuses_the_oidc_subject_as_the_employee_id() -> None:
    """The adapter compares ``subject_id`` with the resolved principal; a raw claim fails."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        pg = FakePostgres()
        session = FakeAsyncSession(pg)
        port = m05_adapter(resolver(factory))

        async with session.begin():
            with pytest.raises(RlsContextError) as excinfo:
                await port.bind(
                    session=session,
                    tenant_id=TENANT_A,
                    subject_id=SUBJECT_A,
                    session_id=SESSION_A,
                    correlation_id=CORRELATION,
                    trusted=False,
                )

        assert excinfo.value.reason_code == "subject_context_mismatch"


async def test_m05_bind_refuses_a_revoked_membership() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session)
            await session.commit()

        pg = FakePostgres()
        session = FakeAsyncSession(pg)
        port = m05_adapter(resolver(factory))

        async with session.begin():
            with pytest.raises(RlsContextError) as excinfo:
                await port.bind(
                    session=session,
                    tenant_id=TENANT_A,
                    subject_id=EMPLOYEE_A,
                    session_id=SESSION_A,
                    correlation_id=CORRELATION,
                    trusted=False,
                )

        assert excinfo.value.reason_code == "principal_not_server_resolved"
        assert pg.statements == []


# ---------------------------------------------------------------------------
# M04: scoped bind with cabinet/project
# ---------------------------------------------------------------------------


async def test_m04_bind_uses_the_project_scope_from_the_principal() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        pg = FakePostgres()
        session = FakeAsyncSession(pg)
        port = m04_adapter(resolver(factory, project=PROJECT_A))

        async with session.begin():
            binding = await port.bind(
                session=session,
                context=m04_context(cabinet_id=CABINET_A, project_id=PROJECT_A),
                mode=TenantScopeMode.TENANT_SCOPED,
            )

            assert binding.tenant_id == TENANT_A
            assert binding.cabinet_id == CABINET_A
            assert binding.project_id == PROJECT_A
            assert binding.application_transaction_id == APPLICATION_TRANSACTION
            assert pg.setting("app.project_id") == PROJECT_A
            assert pg.setting("app.cabinet_id") == CABINET_A


async def test_m04_bind_refuses_a_context_that_asserts_a_foreign_cabinet() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        pg = FakePostgres()
        session = FakeAsyncSession(pg)
        port = m04_adapter(resolver(factory))

        async with session.begin():
            with pytest.raises(RlsContextError) as excinfo:
                await port.bind(
                    session=session,
                    context=m04_context(cabinet_id="cab_bbbbbbbbbbbbbbbbbbbb"),
                    mode=TenantScopeMode.TENANT_SCOPED,
                )

        assert excinfo.value.reason_code == "cross_tenant_context"
        assert pg.statements == []


async def test_m04_bind_refuses_a_role_that_the_directory_did_not_grant() -> None:
    """The context is internally consistent, but the membership says another role."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        pg = FakePostgres()
        session = FakeAsyncSession(pg)
        port = m04_adapter(resolver(factory))

        async with session.begin():
            with pytest.raises(RlsContextError) as excinfo:
                await port.bind(
                    session=session,
                    context=m04_context(role="tenant_admin"),
                    mode=TenantScopeMode.TENANT_SCOPED,
                )

        assert excinfo.value.reason_code == "subject_context_mismatch"
        assert pg.statements == []


async def test_m04_bind_refuses_an_expired_correlation_id() -> None:
    """The adapter asks the resolver every time, so an aged-out credential cannot bind."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)

        pg = FakePostgres()
        session = FakeAsyncSession(pg)
        expired = AuthBoundaryPrincipalResolver(
            credential_source=Source(credential(expires_at=clock_now() - timedelta(seconds=1))),
            directory=SqlTenantIdentityDirectory(session_factory=factory),
            clock=frozen_clock(),
        )

        async with session.begin():
            with pytest.raises(RlsContextError) as excinfo:
                await m04_adapter(expired).bind(
                    session=session,
                    context=m04_context(),
                    mode=TenantScopeMode.TENANT_SCOPED,
                )

        assert excinfo.value.reason_code == "principal_not_server_resolved"
        assert pg.statements == []

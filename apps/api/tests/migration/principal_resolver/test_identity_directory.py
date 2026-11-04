"""The identity-directory half of the resolver: what the database actually authorises.

Each case seeds platform rows and asserts the directory's answer: either a
:class:`TenantIdentity` whose every field is a stored value, or a falsy
:class:`TenantIdentityRefusal` naming the revocation.  The recurring obligation is that the
directory never *derives* a scope from the caller's assertion — an assertion only selects a
membership or narrows the cabinet, and a contradiction is a refusal.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.migration.principal_resolver import (
    PrincipalRefusal,
    SqlTenantIdentityDirectory,
    TenantIdentity,
    TenantIdentityRefusal,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from tests.migration.principal_resolver.db_fixtures import (
    CABINET_A,
    CABINET_B,
    EMPLOYEE_A,
    EMPLOYEE_B,
    PROJECT_A,
    PROJECT_B,
    SUBJECT_A,
    TENANT_A,
    TENANT_B,
    identity_database,
    seed_baseline,
    seed_cabinet,
    seed_cabinet_assignment,
    seed_cabinet_grant,
    seed_company,
    seed_employee,
    seed_membership,
    seed_project,
)


def reason(answer: object) -> PrincipalRefusal:
    assert isinstance(answer, TenantIdentityRefusal)
    assert not answer, "a refusal must be falsy so it can never be read as an identity"
    return answer.reason


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_membership_row_supplies_the_tenant_and_role() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
        identity = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A
        )

    assert isinstance(identity, TenantIdentity)
    assert identity.tenant_id == TENANT_A
    assert identity.subject_id == EMPLOYEE_A
    # ``memberships.role`` — the value the rest of the app authorises with.
    assert identity.role == "company.admin"
    # No cabinet was asserted and no project was asserted: a tenant-wide identity.
    assert identity.cabinet_id is None
    assert identity.project_id is None


async def test_project_supplies_its_own_cabinet() -> None:
    """The cabinet next to a project comes from ``projects.cabinet_id``, not from a claim."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
        identity = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_project_id=PROJECT_A
        )

    assert isinstance(identity, TenantIdentity)
    assert identity.project_id == PROJECT_A
    assert identity.cabinet_id == CABINET_A


async def test_asserted_cabinet_is_verified_against_the_grant() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
        identity = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_cabinet_id=CABINET_A
        )

    assert isinstance(identity, TenantIdentity)
    assert identity.cabinet_id == CABINET_A
    assert identity.project_id is None


async def test_cabinet_role_assignment_does_not_replace_the_membership_role() -> None:
    """``cabinet_employee_assignments.role`` is a cabinet duty, not the tenant role."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            # The seeded assignment carries role='operator' by default.
            directory = SqlTenantIdentityDirectory(session_factory=factory)
            identity = await directory.identity_for(
                subject_claim=SUBJECT_A, asserted_cabinet_id=CABINET_A
            )

    assert isinstance(identity, TenantIdentity)
    assert identity.role == "company.admin"


# ---------------------------------------------------------------------------
# Subject
# ---------------------------------------------------------------------------


async def test_unbound_subject_has_no_identity() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
        directory = SqlTenantIdentityDirectory(session_factory=factory)

        assert reason(await directory.identity_for(subject_claim="kc-unknown")) == (
            PrincipalRefusal.SUBJECT_UNBOUND
        )
        assert reason(await directory.identity_for(subject_claim="")) == PrincipalRefusal.SUBJECT_UNBOUND


async def test_soft_deleted_employee_is_unbound() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            employee = await session.get(EmployeeRow, EMPLOYEE_A)
            employee.deleted_at = datetime(2026, 11, 1, tzinfo=UTC)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A
        )

    assert reason(answer) == PrincipalRefusal.SUBJECT_UNBOUND


@pytest.mark.parametrize("status", ["disabled", "invited"])
async def test_non_active_employee_is_refused(status: str) -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session, status=status)
            await seed_membership(session)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A
        )

    assert reason(answer) == PrincipalRefusal.SUBJECT_INACTIVE


# ---------------------------------------------------------------------------
# Tenant / membership
# ---------------------------------------------------------------------------


async def test_membership_into_a_soft_deleted_company_is_revoked() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session, deleted_at=datetime(2026, 11, 1, tzinfo=UTC))
            await seed_employee(session)
            await seed_membership(session)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A
        )

    assert reason(answer) == PrincipalRefusal.MEMBERSHIP_REVOKED


@pytest.mark.parametrize("status", ["paused", "purged", "suspended"])
async def test_membership_into_a_non_active_company_is_refused(status: str) -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session, status=status)
            await seed_employee(session)
            await seed_membership(session)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A
        )

    assert reason(answer) == PrincipalRefusal.TENANT_INACTIVE


async def test_several_memberships_without_an_assertion_are_ambiguous() -> None:
    """Picking "the first membership" would be exactly the guess the resolver must avoid."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session, company_id=TENANT_A)
            await seed_company(session, company_id=TENANT_B)
            await seed_employee(session)
            await seed_membership(session, company_id=TENANT_A)
            await seed_membership(session, company_id=TENANT_B)
            await session.commit()
        directory = SqlTenantIdentityDirectory(session_factory=factory)

        assert reason(await directory.identity_for(subject_claim=SUBJECT_A)) == (
            PrincipalRefusal.TENANT_AMBIGUOUS
        )


async def test_asserted_tenant_selects_one_membership() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session, company_id=TENANT_A)
            await seed_company(session, company_id=TENANT_B)
            await seed_employee(session)
            await seed_membership(session, company_id=TENANT_A, role="member")
            await seed_membership(session, company_id=TENANT_B, role="company.admin")
            await session.commit()
        identity = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_tenant_id=TENANT_B
        )

    assert isinstance(identity, TenantIdentity)
    assert identity.tenant_id == TENANT_B
    assert identity.role == "company.admin"


async def test_asserted_tenant_without_membership_is_revoked() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session, company_id=TENANT_A)
            await seed_company(session, company_id=TENANT_B)
            await seed_employee(session)
            await seed_membership(session, company_id=TENANT_A)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_tenant_id=TENANT_B
        )

    assert reason(answer) == PrincipalRefusal.MEMBERSHIP_REVOKED


async def test_asserted_tenant_that_is_paused_is_reported_as_inactive() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session, company_id=TENANT_A, status="paused")
            await seed_employee(session)
            await seed_membership(session, company_id=TENANT_A)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_tenant_id=TENANT_A
        )

    assert reason(answer) == PrincipalRefusal.TENANT_INACTIVE


async def test_employee_without_membership_is_revoked() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A
        )

    assert reason(answer) == PrincipalRefusal.MEMBERSHIP_REVOKED


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


async def test_unknown_project_is_out_of_scope() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_project_id="prj_does_not_exist"
        )

    assert reason(answer) == PrincipalRefusal.PROJECT_NOT_IN_SCOPE


async def test_project_of_another_tenant_is_out_of_scope() -> None:
    """A credential naming a foreign project must not move the tenant with it."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session, company_id=TENANT_A)
            await seed_company(session, company_id=TENANT_B)
            await seed_employee(session)
            await seed_membership(session, company_id=TENANT_A)
            await seed_cabinet(session, cabinet_id=CABINET_B)
            await seed_cabinet_grant(session, cabinet_id=CABINET_B, company_id=TENANT_B)
            await seed_project(session, project_id=PROJECT_B, company_id=TENANT_B, cabinet_id=CABINET_B)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_project_id=PROJECT_B
        )

    assert reason(answer) == PrincipalRefusal.PROJECT_NOT_IN_SCOPE


async def test_archived_project_is_refused() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            await seed_project(session, project_id=PROJECT_B, status="archived")
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_project_id=PROJECT_B
        )

    assert reason(answer) == PrincipalRefusal.PROJECT_INACTIVE


async def test_cabinet_that_contradicts_the_project_is_refused() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_project_id=PROJECT_A, asserted_cabinet_id=CABINET_B
        )

    assert reason(answer) == PrincipalRefusal.SCOPE_CONTRADICTION


# ---------------------------------------------------------------------------
# Cabinet grants
# ---------------------------------------------------------------------------


async def test_cabinet_without_a_grant_is_refused() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            await seed_cabinet(session, cabinet_id=CABINET_B)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_cabinet_id=CABINET_B
        )

    assert reason(answer) == PrincipalRefusal.CABINET_NOT_GRANTED


async def test_revoked_cabinet_grant_is_refused() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session)
            await seed_membership(session)
            await seed_cabinet(session)
            await seed_cabinet_grant(session, status="revoked")
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_cabinet_id=CABINET_A
        )

    assert reason(answer) == PrincipalRefusal.CABINET_NOT_GRANTED


async def test_unknown_cabinet_is_refused() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_cabinet_id="cab_does_not_exist"
        )

    assert reason(answer) == PrincipalRefusal.CABINET_NOT_GRANTED


async def test_company_owned_cabinet_needs_no_grant_row() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session)
            await seed_membership(session)
            await seed_cabinet(
                session, owner_scope="company", owner_company_id=TENANT_A
            )
            await session.commit()
        identity = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_cabinet_id=CABINET_A
        )

    assert isinstance(identity, TenantIdentity)
    assert identity.cabinet_id == CABINET_A


async def test_grant_scope_all_opens_every_cabinet() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session)
            await seed_membership(session)
            await seed_cabinet(session, company_grant_scope="all")
            await session.commit()
        identity = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_cabinet_id=CABINET_A
        )

    assert isinstance(identity, TenantIdentity)


async def test_archived_cabinet_is_refused() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session)
            await seed_membership(session)
            await seed_cabinet(session, status="archived")
            await seed_cabinet_grant(session)
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_cabinet_id=CABINET_A
        )

    assert reason(answer) == PrincipalRefusal.CABINET_INACTIVE


async def test_revoked_employee_cabinet_assignment_is_refused() -> None:
    """The tenant still holds the grant, but this employee's access was revoked."""

    async with identity_database() as factory:
        async with factory() as session:
            await seed_company(session)
            await seed_employee(session)
            await seed_membership(session)
            await seed_cabinet(session)
            await seed_cabinet_grant(session)
            await seed_cabinet_assignment(session, status="revoked")
            await session.commit()
        answer = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_cabinet_id=CABINET_A
        )

    assert reason(answer) == PrincipalRefusal.CABINET_NOT_GRANTED


async def test_another_employees_assignment_does_not_leak() -> None:
    async with identity_database() as factory:
        async with factory() as session:
            await seed_baseline(session)
            await seed_employee(session, employee_id=EMPLOYEE_B, subject="kc-subject-b")
            await seed_cabinet_assignment(
                session, cabinet_id=CABINET_A, employee_id=EMPLOYEE_B, status="revoked"
            )
            await session.commit()
        # Employee A's own assignment is still active.
        identity = await SqlTenantIdentityDirectory(session_factory=factory).identity_for(
            subject_claim=SUBJECT_A, asserted_cabinet_id=CABINET_A
        )

    assert isinstance(identity, TenantIdentity)
    assert identity.subject_id == EMPLOYEE_A

"""Acceptance harness for the auth-boundary principal resolver (test-owned).

The resolver reads four kinds of platform table — ``companies``/``employees``/
``memberships`` (identity), ``cabinet_instances``/``cabinet_company_grants``/
``cabinet_employee_assignments`` (grants) and ``projects`` (scope).  Two of them are declared
with PostgreSQL ``JSONB``, which the SQLite dialect cannot render, so the harness mirrors each
model's columns (``JSONB`` -> ``JSON``) exactly like
``tests/migration/m06_cutover/sqlite_evidence.py`` does for the cutover tables.

The mirrors are only containers: every read in the tests goes through the real ORM model
classes, so what is proven is that the resolver's queries agree with the registered models
rather than with an invented schema.  Seeded rows are written through the same ORM classes for
the same reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

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

#: Every model the resolver queries.  Mirrored, never exported.
IDENTITY_MODELS: tuple[Any, ...] = (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
    CabinetInstanceRow,
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    ProjectRow,
)

# --- stable identities used by every test -----------------------------------

TENANT_A = "comp_aaaaaaaaaaaaaaaaaaaa"
TENANT_B = "comp_bbbbbbbbbbbbbbbbbbbb"
CABINET_A = "cab_aaaaaaaaaaaaaaaaaaaa"
CABINET_B = "cab_bbbbbbbbbbbbbbbbbbbb"
PROJECT_A = "prj_aaaaaaaaaaaaaaaaaaaa"
PROJECT_B = "prj_bbbbbbbbbbbbbbbbbbbb"
EMPLOYEE_A = "emp_aaaaaaaaaaaaaaaaaaaa"
EMPLOYEE_B = "emp_bbbbbbbbbbbbbbbbbbbb"
SUBJECT_A = "kc-subject-a"
SUBJECT_B = "kc-subject-b"
CORRELATION = "corr_aaaaaaaaaaaaaaaaaaaa"
CORRELATION_OTHER = "corr_bbbbbbbbbbbbbbbbbbbb"

#: A frozen "now" so lifetime assertions are exact rather than flaky.
#:
#: Anchored to the wall clock at *call* time, not at import time.
#:
#: ``TrustedPrincipal.from_resolution`` validates against the real clock, so a fixture
#: frozen in the past would make every positive case fail for the wrong reason. It must
#: not be frozen at import either: a full-suite run takes minutes, and a case using a
#: short credential TTL (45 seconds) would find its credential already expired by the
#: time it executed -- which is exactly how this flaked, and only in the full run.
def clock_now() -> datetime:
    """The wall clock, truncated to a whole second, read when the fixture is used."""

    return datetime.now(UTC).replace(microsecond=0)


def frozen_clock(moment: datetime | None = None) -> Callable[[], datetime]:
    anchor = moment if moment is not None else clock_now()
    return lambda: anchor


def retry_until_principal_anchor() -> datetime:
    """Compatibility alias for fixtures that still read a module-level constant.

    Prefer :func:`clock_now`: a constant bound at import drifts out of agreement with
    the real clock as soon as a run takes a while.
    """

    return clock_now()


def mirror_table(model: Any, metadata: sa.MetaData) -> sa.Table:
    """Copy one registered model's columns, rendering ``JSONB`` as ``JSON``."""

    columns = []
    for column in model.__table__.columns:
        column_type = sa.JSON() if isinstance(column.type, JSONB) else column.type
        columns.append(
            sa.Column(
                column.name,
                column_type,
                primary_key=column.primary_key,
                nullable=not column.primary_key,
            )
        )
    return sa.Table(model.__tablename__, metadata, *columns)


@asynccontextmanager
async def identity_database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An in-memory database with the seven mirrored platform tables."""

    metadata = sa.MetaData()
    for model in IDENTITY_MODELS:
        mirror_table(model, metadata)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


# --- seeding helpers --------------------------------------------------------


async def seed_company(
    session: AsyncSession,
    *,
    company_id: str = TENANT_A,
    status: str = "active",
    deleted_at: datetime | None = None,
) -> CompanyRow:
    row = CompanyRow(
        id=company_id,
        name=f"Company {company_id}",
        login_slug=f"slug-{company_id}",
        status=status,
        deleted_at=deleted_at,
    )
    session.add(row)
    return row


async def seed_employee(
    session: AsyncSession,
    *,
    employee_id: str = EMPLOYEE_A,
    subject: str = SUBJECT_A,
    status: str = "active",
    deleted_at: datetime | None = None,
) -> EmployeeRow:
    row = EmployeeRow(
        id=employee_id,
        keycloak_sub=subject,
        login=f"{employee_id}@agentscale.local",
        email=f"{employee_id}@agentscale.local",
        status=status,
        deleted_at=deleted_at,
    )
    session.add(row)
    return row


async def seed_membership(
    session: AsyncSession,
    *,
    employee_id: str = EMPLOYEE_A,
    company_id: str = TENANT_A,
    role: str = "member",
    membership_id: str | None = None,
) -> MembershipRow:
    row = MembershipRow(
        id=membership_id or f"mem_{company_id[-8:]}{employee_id[-8:]}",
        employee_id=employee_id,
        company_id=company_id,
        role=role,
    )
    session.add(row)
    return row


async def seed_cabinet(
    session: AsyncSession,
    *,
    cabinet_id: str = CABINET_A,
    status: str = "active",
    owner_scope: str = "platform",
    owner_company_id: str | None = None,
    company_grant_scope: str = "selected",
) -> CabinetInstanceRow:
    row = CabinetInstanceRow(
        id=cabinet_id,
        name=f"Cabinet {cabinet_id}",
        schema_name=f"cab_inst_{cabinet_id[-8:]}",
        status=status,
        owner_scope=owner_scope,
        owner_company_id=owner_company_id,
        company_grant_scope=company_grant_scope,
    )
    session.add(row)
    return row


async def seed_cabinet_grant(
    session: AsyncSession,
    *,
    cabinet_id: str = CABINET_A,
    company_id: str = TENANT_A,
    status: str = "active",
) -> CabinetCompanyGrantRow:
    row = CabinetCompanyGrantRow(
        id=f"ccg_{cabinet_id[-6:]}{company_id[-6:]}",
        cabinet_id=cabinet_id,
        company_id=company_id,
        status=status,
    )
    session.add(row)
    return row


async def seed_cabinet_assignment(
    session: AsyncSession,
    *,
    cabinet_id: str = CABINET_A,
    employee_id: str = EMPLOYEE_A,
    status: str = "active",
) -> CabinetEmployeeAssignmentRow:
    row = CabinetEmployeeAssignmentRow(
        id=f"cas_{cabinet_id[-6:]}{employee_id[-6:]}",
        cabinet_id=cabinet_id,
        employee_id=employee_id,
        status=status,
    )
    session.add(row)
    return row


async def seed_project(
    session: AsyncSession,
    *,
    project_id: str = PROJECT_A,
    company_id: str = TENANT_A,
    cabinet_id: str = CABINET_A,
    status: str = "active",
) -> ProjectRow:
    row = ProjectRow(
        id=project_id,
        company_id=company_id,
        cabinet_id=cabinet_id,
        name=f"Project {project_id}",
        slug=project_id,
        status=status,
        visibility_mode="cabinet_shared",
        workspace_key=f"ws-{project_id}",
        container_ref=f"container-{project_id}",
    )
    session.add(row)
    return row


async def seed_baseline(session: AsyncSession) -> None:
    """One active tenant, one active employee, one membership, one granted cabinet."""

    await seed_company(session)
    await seed_employee(session)
    await seed_membership(session, role="company.admin")
    await seed_cabinet(session)
    await seed_cabinet_grant(session)
    await seed_cabinet_assignment(session)
    await seed_project(session)
    await session.commit()

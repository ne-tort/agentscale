"""Unit tests — employee create/login validation + login handles."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prodavan.api.v1.identity import CreateEmployeeBody
from prodavan.domain.companies.login import (
    company_effective_login,
    slugify_company_name,
    validate_login_username,
)
from prodavan.domain.employees.login import employee_kc_email, employee_login_handle
from prodavan.domain.errors import AppError


def test_create_employee_body_requires_password() -> None:
    with pytest.raises(ValidationError):
        CreateEmployeeBody(login="alice", password="short")


def test_create_employee_body_accepts_min_fields() -> None:
    body = CreateEmployeeBody(login="alice", password="secure-pass")
    assert body.login == "alice"
    assert body.password == "secure-pass"
    assert body.contact_email is None
    assert body.role == "member"


def test_validate_login_username_rejects_invalid() -> None:
    with pytest.raises(AppError) as exc:
        validate_login_username("ab")
    assert exc.value.status == 422


def test_validate_login_username_accepts_dotted() -> None:
    assert validate_login_username("alice.dev") == "alice.dev"


def test_company_effective_login_is_handle() -> None:
    company = type("Co", (), {"id": "co_abc123", "login_slug": "romashka"})()
    assert company_effective_login(company) == "romashka@agentscale.local"


def test_company_effective_login_falls_back_to_id() -> None:
    company = type("Co", (), {"id": "co_abc123", "login_slug": None})()
    assert company_effective_login(company) == "co_abc123"


def test_slugify_company_name() -> None:
    assert slugify_company_name("Ромашка") == "romashka"
    assert slugify_company_name("ООО «Ромашка+»") == "ooo-romashka"
    assert slugify_company_name("ACME Corp") == "acme-corp"
    assert slugify_company_name("  ") == "company"


def test_employee_login_handle() -> None:
    assert employee_login_handle("alice", "romashka") == "alice@romashka.local"


def test_employee_login_handle_rejects_bad_local() -> None:
    with pytest.raises(AppError):
        employee_login_handle("ab", "romashka")


def test_employee_login_handle_same_local_different_companies() -> None:
    a = employee_login_handle("alice", "romashka")
    b = employee_login_handle("alice", "lily")
    assert a == "alice@romashka.local"
    assert b == "alice@lily.local"
    assert a != b


def test_employee_kc_email_is_the_handle() -> None:
    assert employee_kc_email("alice", "romashka") == "alice@romashka.local"


# --- upsert_invited: короткий local part email не должен ронять создание ----


@pytest.mark.asyncio
async def test_upsert_invited_short_email_local_part() -> None:
    """Регресс nightly test_admin_cabinet_crud: company admin с email
    «a@cabadmin.test» (local part < 3 символов) ронял создание компании с
    422 «login must be 3-64 chars» — employee_login_handle валидирует local,
    а upsert_invited звал его без обработки. Теперь: временный уникальный
    логин, который _assign_login заменяет на employee.id."""
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.ext.compiler import compiles

    from prodavan.application.employees.service import EmployeesCommandService
    from prodavan.infrastructure.persistence.models.identity import EmployeeRow

    @compiles(JSONB, "sqlite")
    def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
        return "JSON"

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(EmployeeRow.__table__.create, checkfirst=True)
    session = AsyncSession(engine)
    try:
        svc = EmployeesCommandService(session)
        emp = await svc.upsert_invited(
            email="a@cabadmin.test",
            display_name="Admin",
            company_login_slug="cabadminco",
        )
        # короткий local part → fallback на id сотрудника (валидный логин)
        assert emp.login == emp.id
        # валидный local part по-прежнему получает handle
        emp2 = await svc.upsert_invited(
            email="alice@cabadmin.test",
            display_name=None,
            company_login_slug="cabadminco",
        )
        assert emp2.login == "alice@cabadminco.local"
    finally:
        await session.close()
        await engine.dispose()

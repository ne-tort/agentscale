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

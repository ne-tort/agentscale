"""Unit tests — employee create/login validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prodavan.api.v1.identity import CreateEmployeeBody
from prodavan.domain.companies.login import validate_login_username
from prodavan.domain.employees.login import employee_kc_email
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


def test_employee_kc_email() -> None:
    assert employee_kc_email("alice") == "alice@prodavan.local"

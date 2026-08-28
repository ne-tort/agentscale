"""Employee login validation (company org login is always company.id)."""

from __future__ import annotations

import re

from prodavan.domain.errors import AppError

_LOGIN_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")


def company_effective_login(company) -> str:
    """Public login username for Keycloak and UI — always company id."""
    return company.id


def validate_login_username(raw: str) -> str:
    value = raw.strip()
    if not _LOGIN_USERNAME_RE.fullmatch(value):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="login must be 3-64 chars: letters, digits, . _ -",
        )
    return value

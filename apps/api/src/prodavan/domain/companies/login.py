"""Company org login helpers."""

from __future__ import annotations

import re

from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.identity import CompanyRow

_LOGIN_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")


def company_effective_login(company: CompanyRow) -> str:
    """Public login username for Keycloak and UI."""
    custom = (getattr(company, "login_username", None) or "").strip()
    return custom if custom else company.id


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

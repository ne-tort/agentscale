"""Company login handles (L02/L03) — «{name}@agentscale.local».

Company name → login slug (lowercase [a-z0-9._-]); the Keycloak username
and email of a company org principal is `{slug}@agentscale.local`.
Company names are unique (service check + DB unique index on lower(name)).
"""

from __future__ import annotations

import re

from prodavan.domain.errors import AppError

COMPANY_LOGIN_DOMAIN = "agentscale.local"

# Login local part: 3-64 chars, letters/digits/._- (same as employee names).
_LOGIN_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")

# Cyrillic → latin transliteration for readable slugs of Russian names.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(value: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in value.lower())


def slugify_company_name(name: str) -> str:
    """Company name → login slug: lowercase translit, invalid runs → '-',
    keep [a-z0-9._-]."""
    translit = _translit((name or "").strip())
    slug = re.sub(r"[^a-z0-9._-]+", "-", translit)
    slug = slug.strip("-") or "company"
    return slug


def company_login_handle(login_slug: str) -> str:
    """Keycloak username/email of a company org principal."""
    slug = (login_slug or "").strip().lower()
    if not slug:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="company login slug required",
        )
    return f"{slug}@{COMPANY_LOGIN_DOMAIN}"


def company_effective_login(company) -> str:
    """Public login username for Keycloak and UI — `{slug}@agentscale.local`.

    Falls back to the legacy company id for rows created before the
    login_slug backfill (migration fills it; fallback is belt-and-braces).
    """
    slug = (getattr(company, "login_slug", None) or "").strip()
    if slug:
        return company_login_handle(slug)
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

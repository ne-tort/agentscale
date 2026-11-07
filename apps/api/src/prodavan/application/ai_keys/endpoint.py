"""Admin-entered provider endpoint → `ai.http_providers` catalog entry.

The admin configures an AI key by typing an **endpoint URL + API key**; there is
no provider dropdown to pick from. `ai.http_providers` entries stay the single
source of truth for every consumer of the endpoint (``ProviderResolver`` → probe,
live model list, project materialization), so an endpoint is *matched to* or
*created as* one catalog entry and the key is linked to it through
``ai_provider_keys.catalog_entry_id``.

Why the catalog entry and not a column: ``ai_provider_keys`` has no ``base_url``
column, and adding one would duplicate what the catalog already stores (and
what the resolver already prefers to read: ``catalog_entry_id`` wins over the
legacy ``api_kind`` + ``provider`` match).

Reuse rule
----------
``ensure_endpoint_entry`` never creates a second entry for the same endpoint:
two keys on ``https://api.example.com/v1`` share one entry. Endpoints are
compared after normalisation — scheme and host case-insensitive, default port
elided, trailing slash ignored (``base_url_key``). Archived entries are not
reused (the resolver skips them) and their ids are not recycled.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.catalog.service import (
    CATALOG_AI_HTTP_PROVIDERS,
    CatalogService,
)
from prodavan.domain.ai_keys import AiProvider, ApiKind
from prodavan.domain.ai_keys.http_url import ensure_scheme
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.catalog import ReferenceCatalogEntryRow

#: Providers a key row may carry (runtime selection keys off this value).
PROVIDERS: frozenset[str] = frozenset(p.value for p in AiProvider)
#: api kinds a key row may carry.
API_KINDS: frozenset[str] = frozenset(k.value for k in ApiKind)

AUTH_SCHEMES: tuple[str, ...] = ("bearer", "x-api-key")
DEFAULT_AUTH_SCHEME = "bearer"
#: Written into a *new* endpoint entry (matches a `{base_url}` without `/v1`).
DEFAULT_MODELS_PATH = "/models"
DEFAULT_CHAT_COMPLETIONS_PATH = "/chat/completions"
#: Fallbacks for entries that omit the key (seeded/legacy rows) — the resolver
#: and the list response must agree on where a key actually points.
LEGACY_MODELS_PATH_DEFAULT = "/v1/models"
LEGACY_CHAT_COMPLETIONS_PATH_DEFAULT = "/v1/chat/completions"

CUSTOM_API_KIND = ApiKind.CUSTOM.value
#: Provider for an unknown host. `custom` entries run on the openai-compatible
#: dialect and the runtime maps an unknown provider to `codex`
#: (`agent_provider_from_key_row`), so the key row carries the runtime value.
DEFAULT_ENDPOINT_PROVIDER = AiProvider.CODEX.value
#: The pre-endpoint defaults of `CreateKeyBody` (provider/api_kind optional now,
#: but a legacy body that omits them must still produce the same key).
DEFAULT_LEGACY_PROVIDER = AiProvider.CURSOR.value
DEFAULT_LEGACY_API_KIND = ApiKind.CURSOR_SDK.value
#: Admin-added endpoints sort after the seeded catalog entries.
ENDPOINT_ENTRY_SORT_ORDER = 500

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {("http", 80), ("https", 443)}
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_ENTRY_ID = 64


def validation_error(detail: str) -> AppError:
    """422 in the project's `AppError` shape (not a bare pydantic error)."""
    return AppError(
        code="VALIDATION_ERROR",
        title="Validation Error",
        status=422,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# URL / field normalisation
# ---------------------------------------------------------------------------


def normalize_base_url(raw: str | None) -> str:
    """Validate an admin-entered endpoint and return it without a trailing slash.

    Only absolute ``http(s)`` URLs are accepted: a scheme-less host would be
    silently upgraded to https by ``ensure_scheme`` downstream, which hides a
    typo from the admin. Raises ``AppError`` 422 otherwise.
    """
    text = (raw or "").strip()
    if not text:
        raise validation_error("base_url is required")
    if any(ch.isspace() for ch in text):
        raise validation_error("base_url must not contain whitespace")
    if "://" not in text:
        raise validation_error(
            "base_url must be an absolute URL, e.g. https://api.example.com/v1"
        )
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise validation_error(f"base_url is not a valid URL: {exc}") from exc
    if parts.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        raise validation_error("base_url must use http or https")
    if not parts.hostname:
        raise validation_error("base_url must include a host")
    if parts.username or parts.password:
        raise validation_error("base_url must not embed credentials")
    if parts.query or parts.fragment:
        raise validation_error("base_url must not contain a query string or fragment")
    return text.rstrip("/")


def base_url_key(raw: str | None) -> str:
    """Comparison key for endpoint equality.

    Case-insensitive scheme/host, default port elided, trailing slash ignored,
    path compared as-is. Scheme-less legacy payloads ("cheapai.lol/v1") are
    read the same way the resolver reads them (https by default), so a key
    created from an endpoint matches the entry an earlier admin added by hand.
    """
    text = ensure_scheme((raw or "").strip())
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        try:
            port = parts.port
        except ValueError:
            port = None
    except ValueError:
        return text.rstrip("/").lower()
    if not host:
        return text.rstrip("/").lower()
    scheme = (parts.scheme or "https").lower()
    port_part = "" if port is None or (scheme, port) in _DEFAULT_PORTS else f":{port}"
    return f"{scheme}://{host}{port_part}{parts.path.rstrip('/')}"


def display_host(raw: str | None) -> str:
    """Readable host for the catalog entry title ("api.example.com", "localhost:11434")."""
    text = ensure_scheme((raw or "").strip())
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
        try:
            port = parts.port
        except ValueError:
            port = None
    except ValueError:
        return text
    if port is not None and (parts.scheme.lower(), port) not in _DEFAULT_PORTS:
        return f"{host}:{port}"
    return host


def slug_from_host(host: str) -> str:
    """`api.example.com` → `api-example-com` (agent_provider / catalog entry id stem)."""
    slug = _SLUG_RE.sub("-", (host or "").lower()).strip("-")
    return slug or "endpoint"


def normalize_path(value: str | None, default: str) -> str:
    """A path component (leading ``/``) — never an absolute URL."""
    text = (value or "").strip()
    if not text:
        return default
    if "://" in text:
        raise validation_error("path fields must be paths, not absolute URLs")
    if any(ch.isspace() for ch in text) or "?" in text or "#" in text:
        raise validation_error("path fields must not contain whitespace, query or fragment")
    return text if text.startswith("/") else f"/{text}"


def normalize_auth_scheme(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not text:
        return DEFAULT_AUTH_SCHEME
    if text not in AUTH_SCHEMES:
        raise validation_error(
            f"auth_scheme must be one of: {', '.join(AUTH_SCHEMES)}"
        )
    return text


def api_kind_from_entry(payload: dict[str, Any] | None) -> str:
    """api_kind a key inherits from the entry it points at."""
    kind = str((payload or {}).get("api_kind") or "").strip()
    return kind if kind in API_KINDS else CUSTOM_API_KIND


def agent_provider_from_entry(payload: dict[str, Any] | None) -> str:
    """Provider a key inherits from the entry it points at.

    Reusing a seeded entry (``api.anthropic.com`` → ``anthropic``) must keep the
    provider the runtime expects (``claude_code``); an endpoint entry created by
    the admin carries a host slug, which is not a runtime provider — such keys
    run as the openai-compatible default.
    """
    provider = str((payload or {}).get("agent_provider") or "").strip()
    return provider if provider in PROVIDERS else DEFAULT_ENDPOINT_PROVIDER


def endpoint_public(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Where a key points, as shown to the administrator (never the secret)."""
    data = payload or {}
    raw_base = data.get("base_url")
    base_url = ensure_scheme(str(raw_base).rstrip("/")) if raw_base else None
    return {
        "base_url": base_url,
        "models_path": str(data.get("models_path") or LEGACY_MODELS_PATH_DEFAULT),
        "auth_scheme": str(data.get("auth_scheme") or DEFAULT_AUTH_SCHEME),
    }


EMPTY_ENDPOINT: dict[str, Any] = {
    "base_url": None,
    "models_path": None,
    "auth_scheme": None,
}


# ---------------------------------------------------------------------------
# Catalog lookup / creation
# ---------------------------------------------------------------------------


async def find_endpoint_entry(
    session: AsyncSession,
    base_url: str,
) -> ReferenceCatalogEntryRow | None:
    """Existing, non-archived `ai.http_providers` entry for this endpoint."""
    target = base_url_key(base_url)
    if not target:
        return None
    q = await session.execute(
        select(ReferenceCatalogEntryRow)
        .where(
            ReferenceCatalogEntryRow.catalog_id == CATALOG_AI_HTTP_PROVIDERS,
            ReferenceCatalogEntryRow.archived_at.is_(None),
        )
        .order_by(
            ReferenceCatalogEntryRow.sort_order.asc(),
            ReferenceCatalogEntryRow.id.asc(),
        )
    )
    for row in q.scalars().all():
        payload = row.payload or {}
        if not payload.get("base_url"):
            continue
        if base_url_key(str(payload["base_url"])) == target:
            return row
    return None


async def ensure_endpoint_entry(
    session: AsyncSession,
    *,
    base_url: str,
    models_path: str | None = None,
    chat_completions_path: str | None = None,
    auth_scheme: str | None = None,
    title: str | None = None,
) -> ReferenceCatalogEntryRow:
    """Match or create the `ai.http_providers` entry an endpoint describes.

    Reuse is keyed on the normalised ``base_url`` only (that is the identity of
    an endpoint): two keys on the same endpoint share one entry, and an
    endpoint already present in the seeded catalog (``api.openai.com``) is
    reused with its own api_kind / agent_provider / paths rather than
    duplicated as ``custom``.

    A newly created entry gets
    ``{api_kind: "custom", agent_provider: <host slug>, base_url, models_path,
    chat_completions_path, auth_scheme, openai_compatible: true,
    supports_models_list: true}`` and the host as its title. Creation goes
    through ``CatalogService.create_entry`` — the same code path the admin
    catalogs API uses (id clash check, seeding, commit).
    """
    normalized = normalize_base_url(base_url)
    # Validate every supplied field even when the entry is reused: a bad
    # models_path must fail the request, not be silently dropped.
    resolved_auth = normalize_auth_scheme(auth_scheme)
    resolved_models = normalize_path(models_path, DEFAULT_MODELS_PATH)
    resolved_chat = normalize_path(chat_completions_path, DEFAULT_CHAT_COMPLETIONS_PATH)

    catalog = CatalogService(session)
    # Idempotent: guarantees the seeded providers exist before we look, so
    # `https://api.openai.com` reuses the seed instead of duplicating it.
    await catalog.ensure_seeded(CATALOG_AI_HTTP_PROVIDERS)

    existing = await find_endpoint_entry(session, normalized)
    if existing is not None:
        _reject_entry_conflicts(
            existing,
            base_url=normalized,
            models_path=models_path,
            chat_completions_path=chat_completions_path,
            auth_scheme=auth_scheme,
        )
        return existing

    host = display_host(normalized)
    slug = slug_from_host(host)
    payload: dict[str, Any] = {
        "api_kind": CUSTOM_API_KIND,
        "agent_provider": slug,
        "base_url": normalized,
        "models_path": resolved_models,
        "chat_completions_path": resolved_chat,
        "auth_scheme": resolved_auth,
        "openai_compatible": True,
        "supports_models_list": True,
    }
    entry_id = await _free_entry_id(session, slug, normalized)
    await catalog.create_entry(
        CATALOG_AI_HTTP_PROVIDERS,
        title=(title or host or slug)[:200],
        id=entry_id,
        subtitle=normalized[:500],
        payload=payload,
        sort_order=ENDPOINT_ENTRY_SORT_ORDER,
    )
    created = await _get_entry(session, entry_id)
    if created is None:  # pragma: no cover — create_entry just committed it
        raise AppError(
            code="INTERNAL_ERROR",
            title="Internal Error",
            status=500,
            detail="endpoint catalog entry was not persisted",
        )
    return created


async def endpoint_fields_for(
    session: AsyncSession,
    entry_ids: list[str | None],
) -> dict[str, dict[str, Any]]:
    """Batch-resolve ``catalog_entry_id`` → endpoint fields for list responses."""
    ids = [entry_id for entry_id in dict.fromkeys(entry_ids) if entry_id]
    if not ids:
        return {}
    q = await session.execute(
        select(ReferenceCatalogEntryRow).where(
            ReferenceCatalogEntryRow.catalog_id == CATALOG_AI_HTTP_PROVIDERS,
            ReferenceCatalogEntryRow.id.in_(ids),
        )
    )
    # Archived entries are included on purpose: a key may still point at one,
    # and the administrator must still see where that key goes.
    return {row.id: endpoint_public(row.payload) for row in q.scalars().all()}


def _reject_entry_conflicts(
    entry: ReferenceCatalogEntryRow,
    *,
    base_url: str,
    models_path: str | None,
    chat_completions_path: str | None,
    auth_scheme: str | None,
) -> None:
    """Refuse explicit settings that contradict the entry this endpoint reuses.

    The entry is shared (that is the point of reuse) and `ai.http_providers` is
    the source of truth, so silently dropping a different `auth_scheme` would
    hand the admin a key that cannot authenticate. The same endpoint with the
    same settings is fine — only real contradictions are rejected.
    """
    effective = endpoint_public(entry.payload)
    conflicts: list[str] = []
    if models_path is not None and normalize_path(models_path, DEFAULT_MODELS_PATH) != effective["models_path"]:
        conflicts.append(f"models_path={effective['models_path']}")
    if (
        chat_completions_path is not None
        and normalize_path(chat_completions_path, DEFAULT_CHAT_COMPLETIONS_PATH)
        != effective["chat_completions_path"]
    ):
        conflicts.append(f"chat_completions_path={effective['chat_completions_path']}")
    if auth_scheme is not None and normalize_auth_scheme(auth_scheme) != effective["auth_scheme"]:
        conflicts.append(f"auth_scheme={effective['auth_scheme']}")
    if not conflicts:
        return
    raise AppError(
        code="CONFLICT",
        title="Conflict",
        status=409,
        detail=(
            f"endpoint {base_url} already exists as catalog entry "
            f"'{entry.id}' with {', '.join(conflicts)}; edit that entry "
            "via /admin/catalogs instead of contradicting it here"
        ),
    )


async def _get_entry(
    session: AsyncSession,
    entry_id: str,
) -> ReferenceCatalogEntryRow | None:
    q = await session.execute(
        select(ReferenceCatalogEntryRow).where(
            ReferenceCatalogEntryRow.catalog_id == CATALOG_AI_HTTP_PROVIDERS,
            ReferenceCatalogEntryRow.id == entry_id,
        )
    )
    return q.scalar_one_or_none()


async def _free_entry_id(
    session: AsyncSession,
    slug: str,
    base_url: str,
) -> str:
    """Readable id ("api-example-com"), falling back to a hashed suffix.

    Two endpoints on the same host (``/v1`` and ``/api/v2``) both slug to the
    same stem, and archived entry ids are still occupied (unique constraint on
    ``catalog_id, id``) — both cases get a deterministic suffix derived from the
    normalised endpoint.
    """
    q = await session.execute(
        select(ReferenceCatalogEntryRow.id).where(
            ReferenceCatalogEntryRow.catalog_id == CATALOG_AI_HTTP_PROVIDERS
        )
    )
    taken = {str(row_id) for row_id in q.scalars().all()}
    stem = (slug or "endpoint")[:_MAX_ENTRY_ID]
    if stem not in taken:
        return stem
    digest = hashlib.sha1(base_url.encode("utf-8")).hexdigest()[:8]
    candidate = f"{stem[: _MAX_ENTRY_ID - 9]}-{digest}"
    if candidate not in taken:
        return candidate
    for n in range(2, 100):  # pragma: no cover — same endpoint, concurrent create
        suffixed = f"{candidate[: _MAX_ENTRY_ID - 4]}-{n}"
        if suffixed not in taken:
            return suffixed
    raise AppError(
        code="CONFLICT",
        title="Conflict",
        status=409,
        detail="cannot allocate a catalog entry id for this endpoint",
    )

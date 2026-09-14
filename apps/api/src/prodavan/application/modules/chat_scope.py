"""Chat-scoped module rows (`scope.chats`) — orthogonal to bind local/global."""

from __future__ import annotations

from typing import Any

from prodavan.domain.errors import AppError

CHAT_SCOPE_ALL = "all"
CHAT_SCOPE_CURRENT = "current"
SESSION_HEADER = "X-Prodavan-Session-Id"

# Synthetic session when no agent chat is active — still stamps chats=current rows.
DEFAULT_CHAT_SESSION_ID = "main"

ACTIVE_CHAT_OPTIONAL = "optional"
ACTIVE_CHAT_REQUIRED = "required"


def normalize_chats_scope(raw: Any) -> str:
    value = str(raw or CHAT_SCOPE_ALL).strip().lower()
    if value == CHAT_SCOPE_CURRENT:
        return CHAT_SCOPE_CURRENT
    return CHAT_SCOPE_ALL


def normalize_active_chat(raw: Any) -> str:
    """UI opt-in: hide nav/hub entry when no live agent chat.

    Default ``optional`` — always show. ``required`` — hide without active session.
    Orthogonal to ``scope.chats`` row filtering.
    """
    value = str(raw or ACTIVE_CHAT_OPTIONAL).strip().lower()
    if value == ACTIVE_CHAT_REQUIRED:
        return ACTIVE_CHAT_REQUIRED
    return ACTIVE_CHAT_OPTIONAL


def chats_scope_from_tables_body(tables_body: Any, table_slug: str) -> str:
    """Read ``scope.chats`` for ``table_slug`` from tables meta body (list or map)."""
    if tables_body is None:
        return CHAT_SCOPE_ALL
    entries: list[Any]
    if isinstance(tables_body, list):
        entries = tables_body
    elif isinstance(tables_body, dict):
        nested = tables_body.get("tables") or tables_body.get("items")
        if isinstance(nested, list):
            entries = nested
        else:
            entry = tables_body.get(table_slug)
            if isinstance(entry, dict):
                return normalize_chats_scope((entry.get("scope") or {}).get("chats"))
            return CHAT_SCOPE_ALL
    else:
        return CHAT_SCOPE_ALL
    for item in entries:
        if not isinstance(item, dict):
            continue
        if str(item.get("slug") or "") != table_slug:
            continue
        scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
        return normalize_chats_scope(scope.get("chats"))
    return CHAT_SCOPE_ALL


def is_synthetic_chat_session(session_id: str | None) -> bool:
    return (session_id or "").strip() == DEFAULT_CHAT_SESSION_ID


def resolve_session_id(session_id: str | None) -> str:
    """Active chat id, or synthetic ``main`` when absent."""
    sid = (session_id or "").strip() or None
    return sid or DEFAULT_CHAT_SESSION_ID


def require_session_id(session_id: str | None, *, for_write: bool) -> str | None:
    """Resolve session for chats=current.

    Missing session → ``main`` (no 422). ``for_write`` kept for call-site clarity.
    """
    _ = for_write
    return resolve_session_id(session_id)


def stamp_session_on_body(body: dict[str, Any], session_id: str) -> dict[str, Any]:
    out = dict(body)
    out["session_id"] = session_id
    return out


def assert_row_session_access(
    *,
    row_session_id: str | None,
    active_session_id: str,
) -> None:
    stored = (row_session_id or "").strip() or None
    if stored != active_session_id:
        raise AppError(
            code="NOT_FOUND",
            title="Not Found",
            status=404,
            detail="row not found",
        )

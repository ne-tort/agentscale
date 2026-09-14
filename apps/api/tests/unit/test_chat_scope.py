"""Unit tests for scope.chats helpers."""

from __future__ import annotations

import pytest

from prodavan.application.modules.chat_scope import (
    CHAT_SCOPE_ALL,
    CHAT_SCOPE_CURRENT,
    DEFAULT_CHAT_SESSION_ID,
    assert_row_session_access,
    chats_scope_from_tables_body,
    is_synthetic_chat_session,
    normalize_active_chat,
    normalize_chats_scope,
    require_session_id,
    stamp_session_on_body,
)
from prodavan.domain.errors import AppError


def test_normalize_chats_scope() -> None:
    assert normalize_chats_scope(None) == CHAT_SCOPE_ALL
    assert normalize_chats_scope("all") == CHAT_SCOPE_ALL
    assert normalize_chats_scope("CURRENT") == CHAT_SCOPE_CURRENT
    assert normalize_chats_scope("other") == CHAT_SCOPE_ALL


def test_normalize_active_chat() -> None:
    assert normalize_active_chat(None) == "optional"
    assert normalize_active_chat("required") == "required"
    assert normalize_active_chat("OPTIONAL") == "optional"


def test_chats_scope_from_tables_list() -> None:
    body = [
        {"slug": "catalogs", "scope": {"chats": "all"}},
        {"slug": "request_lines", "scope": {"chats": "current"}},
    ]
    assert chats_scope_from_tables_body(body, "catalogs") == CHAT_SCOPE_ALL
    assert chats_scope_from_tables_body(body, "request_lines") == CHAT_SCOPE_CURRENT
    assert chats_scope_from_tables_body(body, "missing") == CHAT_SCOPE_ALL


def test_chats_scope_from_tables_map() -> None:
    body = {
        "request_lines": {"scope": {"chats": "current"}},
    }
    assert chats_scope_from_tables_body(body, "request_lines") == CHAT_SCOPE_CURRENT


def test_require_session_id_falls_back_to_main() -> None:
    assert require_session_id(None, for_write=False) == DEFAULT_CHAT_SESSION_ID
    assert require_session_id(None, for_write=True) == DEFAULT_CHAT_SESSION_ID
    assert require_session_id("  ags_1  ", for_write=False) == "ags_1"
    assert is_synthetic_chat_session("main")
    assert not is_synthetic_chat_session("ags_1")


def test_stamp_and_assert_row_session() -> None:
    stamped = stamp_session_on_body({"name": "x"}, "ags_a")
    assert stamped == {"name": "x", "session_id": "ags_a"}
    assert_row_session_access(row_session_id="ags_a", active_session_id="ags_a")
    with pytest.raises(AppError) as ei:
        assert_row_session_access(row_session_id="ags_b", active_session_id="ags_a")
    assert ei.value.status == 404

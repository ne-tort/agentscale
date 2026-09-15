"""Unit tests — composer draft normalization and scope keys."""

from __future__ import annotations

from prodavan.application.agent.composer_draft_service import (
    COMPOSER_DRAFT_MIN_CHARS,
    normalize_composer_draft_text,
)
from prodavan.infrastructure.persistence.models.composer_draft import composer_draft_scope_key


def test_normalize_rejects_short_and_whitespace() -> None:
    assert normalize_composer_draft_text(None) is None
    assert normalize_composer_draft_text("") is None
    assert normalize_composer_draft_text("   ") is None
    assert normalize_composer_draft_text("abcd") is None
    assert len("abcd") < COMPOSER_DRAFT_MIN_CHARS
    assert normalize_composer_draft_text("abcde") == "abcde"
    assert normalize_composer_draft_text("  hello world  ") == "hello world"


def test_scope_keys_distinct() -> None:
    assert composer_draft_scope_key(session_id="ags_1", project_id="p") == "s:ags_1"
    assert composer_draft_scope_key(session_id=None, project_id="p1") == "p:p1"
    assert composer_draft_scope_key(session_id=None, project_id="p1") != composer_draft_scope_key(
        session_id="ags_1", project_id="p1"
    )

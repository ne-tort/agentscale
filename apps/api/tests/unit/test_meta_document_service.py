"""Unit — meta document JSON validation."""

from __future__ import annotations

import pytest

from prodavan.application.cabinets.meta_document_service import _ensure_json_body
from prodavan.domain.errors import AppError


def test_ensure_json_body_accepts_object_and_array() -> None:
    assert _ensure_json_body({"a": 1}) == {"a": 1}
    assert _ensure_json_body([1, 2]) == [1, 2]
    assert _ensure_json_body('{"x": true}') == {"x": True}


def test_ensure_json_body_rejects_scalar_and_bad_json() -> None:
    with pytest.raises(AppError) as ei:
        _ensure_json_body("not-json")
    assert ei.value.code == "VALIDATION_ERROR"
    with pytest.raises(AppError):
        _ensure_json_body(42)
    with pytest.raises(AppError):
        _ensure_json_body(None)

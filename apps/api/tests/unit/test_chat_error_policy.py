"""Unit tests for the chat error-retry policy domain."""

from __future__ import annotations

import pytest

from prodavan.domain.errors import AppError
from prodavan.domain.projects.chat_error_policy import (
    DEFAULT_INTERVAL_SEC,
    DEFAULT_MAX_ATTEMPTS,
    normalize_chat_error_policy,
    policy_to_send_fields,
)


def test_defaults_when_no_policy_stored() -> None:
    assert normalize_chat_error_policy(None) == {
        "interval_sec": DEFAULT_INTERVAL_SEC,
        "max_attempts": DEFAULT_MAX_ATTEMPTS,
        "fallback_models": [],
    }


def test_partial_input_keeps_defaults() -> None:
    policy = normalize_chat_error_policy({"interval_sec": 30})
    assert policy["interval_sec"] == 30
    assert policy["max_attempts"] == DEFAULT_MAX_ATTEMPTS
    assert policy["fallback_models"] == []


def test_none_values_reset_to_defaults() -> None:
    policy = normalize_chat_error_policy({"interval_sec": None, "max_attempts": None})
    assert policy["interval_sec"] == DEFAULT_INTERVAL_SEC
    assert policy["max_attempts"] == DEFAULT_MAX_ATTEMPTS


def test_send_fields_shape() -> None:
    fields = policy_to_send_fields({"interval_sec": 5, "max_attempts": 3, "fallback_models": ["m1"]})
    assert fields == {
        "retry_interval_ms": 5000,
        "retry_max_attempts": 3,
        "fallback_models": ["m1"],
    }


def test_unlimited_attempts_zero() -> None:
    fields = policy_to_send_fields({"max_attempts": 0})
    assert fields["retry_max_attempts"] == 0


def test_fallback_models_dedup_trim_and_drop_empty() -> None:
    policy = normalize_chat_error_policy({"fallback_models": ["a", "a", "  ", "b "]})
    assert policy["fallback_models"] == ["a", "b"]


@pytest.mark.parametrize(
    "raw",
    [
        {"interval_sec": 0},
        {"interval_sec": 3601},
        {"max_attempts": -1},
        {"max_attempts": 1001},
        {"fallback_models": "a"},
        {"fallback_models": [1]},
        {"interval_sec": "10"},
        {"max_attempts": True},
    ],
)
def test_validation_rejects_bad_input(raw: dict) -> None:
    with pytest.raises(AppError) as exc:
        normalize_chat_error_policy(raw)
    assert exc.value.status == 422

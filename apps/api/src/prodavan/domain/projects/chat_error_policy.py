"""Chat error-retry policy domain (reconnects for provider errors).

Stored per project (``projects.chat_error_policy`` JSONB, migration
2026100202); threaded into every agent send as the bridge
``SendRetryPolicy`` (``retry_interval_ms`` / ``retry_max_attempts`` /
``fallback_models`` in the send body). The engine itself lives in the
agent-runtime query loop; this module only owns defaults, validation and
the wire shape.
"""

from __future__ import annotations

from typing import Any

from prodavan.domain.errors import AppError

#: Server default when the project has no policy stored: 10s interval,
#: unlimited reconnect attempts, no fallback models.
DEFAULT_INTERVAL_SEC = 10
DEFAULT_MAX_ATTEMPTS = 0  # 0 = unlimited

_MIN_INTERVAL_SEC = 1
_MAX_INTERVAL_SEC = 3600
_MAX_ATTEMPTS = 1000
_MAX_FALLBACK_MODELS = 20
_MAX_MODEL_ID_LEN = 200


def normalize_chat_error_policy(raw: dict | None) -> dict:
    """Validated policy dict with defaults filled; raises AppError(422) on bad input.

    ``raw`` may be partial (UI PATCH semantics): missing keys keep the
    server defaults, ``None`` resets to defaults.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise _policy_error("policy must be an object")

    interval = raw.get("interval_sec", DEFAULT_INTERVAL_SEC)
    if interval is None:
        interval = DEFAULT_INTERVAL_SEC
    if not isinstance(interval, int) or isinstance(interval, bool):
        raise _policy_error("interval_sec must be an integer")
    if not (_MIN_INTERVAL_SEC <= interval <= _MAX_INTERVAL_SEC):
        raise _policy_error(f"interval_sec must be between {_MIN_INTERVAL_SEC} and {_MAX_INTERVAL_SEC}")

    max_attempts = raw.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
    if max_attempts is None:
        max_attempts = DEFAULT_MAX_ATTEMPTS
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool):
        raise _policy_error("max_attempts must be an integer")
    if not (0 <= max_attempts <= _MAX_ATTEMPTS):
        raise _policy_error(f"max_attempts must be between 0 (unlimited) and {_MAX_ATTEMPTS}")

    models_raw = raw.get("fallback_models")
    if models_raw is None:
        models_raw = []
    if not isinstance(models_raw, list):
        raise _policy_error("fallback_models must be a list")
    if len(models_raw) > _MAX_FALLBACK_MODELS:
        raise _policy_error(f"fallback_models must hold at most {_MAX_FALLBACK_MODELS} models")
    fallback_models: list[str] = []
    for item in models_raw:
        if not isinstance(item, str):
            raise _policy_error("fallback_models entries must be strings")
        model_id = item.strip()
        if not model_id:
            continue
        if len(model_id) > _MAX_MODEL_ID_LEN:
            raise _policy_error(f"fallback model id too long: {model_id[:40]}…")
        if model_id not in fallback_models:
            fallback_models.append(model_id)

    return {
        "interval_sec": interval,
        "max_attempts": max_attempts,
        "fallback_models": fallback_models,
    }


def policy_to_send_fields(policy: dict | None) -> dict[str, Any]:
    """Bridge send-body fields (SendRetryPolicy) from a stored policy.

    Always returns the three fields (normalize defaults first) — the chat
    turn passes the effective server default when nothing is configured,
    so the runtime engine engages with 10s/unlimited out of the box.
    """
    normalized = normalize_chat_error_policy(policy)
    return {
        "retry_interval_ms": normalized["interval_sec"] * 1000,
        "retry_max_attempts": normalized["max_attempts"],
        "fallback_models": normalized["fallback_models"],
    }


def _policy_error(detail: str) -> AppError:
    return AppError(
        code="VALIDATION_ERROR",
        title="Validation Error",
        status=422,
        detail=detail,
    )

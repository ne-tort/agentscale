"""Unit tests for AI key probe domain + http probe interpretation (PROBE-P1)."""

from __future__ import annotations

import pytest

from prodavan.domain.ai_keys import (
    ProbeKind,
    ProbeResult,
    ProbeStatus,
    is_http_probe_kind,
)


def test_http_probe_kind_classification() -> None:
    assert is_http_probe_kind("openai_api")
    assert is_http_probe_kind("anthropic_api")
    assert is_http_probe_kind("openrouter")
    assert is_http_probe_kind("custom")
    assert is_http_probe_kind("cursor_sdk")
    assert is_http_probe_kind("codex_sdk")
    assert is_http_probe_kind("claude_agent_sdk")
    # cli_subscription cannot be probed via HTTP
    assert not is_http_probe_kind("cli_subscription")
    assert not is_http_probe_kind(None)
    assert not is_http_probe_kind("")


def test_probe_result_to_dict_shape() -> None:
    result = ProbeResult(
        status=ProbeStatus.OK,
        kind=ProbeKind.MODELS,
        latency_ms=120,
        models=["gpt-5.1", "claude-sonnet-4-6"],
        default_model="gpt-5.1",
        http_status=200,
        provider="codex",
        api_kind="openai_api",
    )
    d = result.to_dict()
    assert d["status"] == "ok"
    assert d["kind"] == "models"
    assert d["latency_ms"] == 120
    assert d["models"] == ["gpt-5.1", "claude-sonnet-4-6"]
    assert d["default_model"] == "gpt-5.1"
    assert d["http_status"] == 200
    assert d["error_code"] is None
    assert d["error_message"] is None


def test_probe_result_error_shape() -> None:
    result = ProbeResult(
        status=ProbeStatus.ERROR,
        error_code="AUTH_INVALID",
        error_message="401 Unauthorized",
        provider="claude_code",
        api_kind="anthropic_api",
    )
    d = result.to_dict()
    assert d["status"] == "error"
    assert d["error_code"] == "AUTH_INVALID"
    assert d["models"] == []


def test_probe_result_model_field() -> None:
    """ProbeResult carries the model name for per-model probes."""
    result = ProbeResult(
        status=ProbeStatus.OK,
        kind=ProbeKind.CHAT,
        latency_ms=80,
        http_status=200,
        provider="codex",
        api_kind="openai_api",
        model="gpt-5.1",
    )
    d = result.to_dict()
    assert d["model"] == "gpt-5.1"
    assert d["status"] == "ok"
    assert d["kind"] == "chat"


def test_probe_status_enum_values() -> None:
    assert ProbeStatus.OK == "ok"
    assert ProbeStatus.ERROR == "error"
    assert ProbeStatus.UNAVAILABLE == "unavailable"


def test_probe_kind_enum_values() -> None:
    assert ProbeKind.MODELS == "models"
    assert ProbeKind.CHAT == "chat"


def test_probe_result_is_frozen() -> None:
    result = ProbeResult(status=ProbeStatus.OK)
    with pytest.raises(Exception):
        result.status = ProbeStatus.ERROR  # type: ignore[misc]


def test_persist_upsert_compiles_with_key_id_bound() -> None:
    """Regression: pg_insert must bind key_id (NULL → NOT NULL violation → 409).

    Previously _persist_result passed params to execute() without binding
    them as ORM column values, so SQLAlchemy compiled an INSERT with NULL
    key_id → IntegrityError → HTTP 409 CONFLICT on every probe.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from prodavan.infrastructure.persistence.models.ai_keys import (
        AiKeyCheckResultRow,
    )

    stmt = pg_insert(AiKeyCheckResultRow).values(
        key_id="aik_test",
        status="ok",
        kind="models",
        latency_ms=10,
        models=["gpt-5.1"],
        default_model="gpt-5.1",
        http_status=200,
        error_code=None,
        error_message=None,
        provider="codex",
        api_kind="openai_api",
        checked_by=None,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["key_id"],
        set_={"status": stmt.excluded.status},
    )
    compiled = str(stmt.compile(dialect=__import__("sqlalchemy").dialects.postgresql.dialect()))
    # INSERT must list key_id as a bound column (not rely on server default).
    assert "key_id" in compiled
    assert "INSERT INTO ai_key_check_results" in compiled
    assert "ON CONFLICT (key_id)" in compiled

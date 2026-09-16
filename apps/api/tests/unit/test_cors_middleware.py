"""Unit tests — CORS registration (audit API-P1b)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from prodavan.config.settings import settings as _settings
from prodavan.core.middleware import _CORS_HEADERS, _CORS_METHODS, register_cors


def _make_app() -> FastAPI:
    return FastAPI()


def test_register_cors_explicit_methods_and_headers() -> None:
    # API-P1b: no wildcards for methods/headers.
    assert _CORS_METHODS == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    assert "Authorization" in _CORS_HEADERS
    assert "X-Cabinet-Id" in _CORS_HEADERS
    assert "X-Project-Id" in _CORS_HEADERS
    assert "X-Prodavan-Session-Id" in _CORS_HEADERS
    assert "*" not in _CORS_METHODS
    assert "*" not in _CORS_HEADERS


def test_register_cors_credentials_off_by_default() -> None:
    # Prodavan auth is Bearer JWT; credentials default off so a misconfigured
    # wildcard origin cannot leak cookies.
    assert _settings.cors_allow_credentials is False


def test_register_cors_rejects_wildcard_origin_with_credentials() -> None:
    app = _make_app()
    _settings.cors_allow_credentials = True
    try:
        with pytest.raises(RuntimeError, match="wildcard"):
            register_cors(app, allow_origins=["*"])
    finally:
        _settings.cors_allow_credentials = False


def test_register_cors_rejects_wildcard_origin_when_forbid_set() -> None:
    app = _make_app()
    _settings.cors_allow_credentials = False
    _settings.cors_forbid_wildcard_origin = True
    try:
        with pytest.raises(RuntimeError, match="wildcard"):
            register_cors(app, allow_origins=["*"])
    finally:
        _settings.cors_forbid_wildcard_origin = True


def test_register_cors_accepts_explicit_origins() -> None:
    app = _make_app()
    # Should not raise.
    register_cors(app, allow_origins=["http://localhost:8088", "http://127.0.0.1:8088"])

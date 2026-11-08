"""Тесты allowlist'а доверенных заголовков (§6.4, §7.2).

Главный регресс v1: клиентские заголовки распаковывались ПОСЛЕ доверенных и
перезаписывали их. Эти тесты фиксируют обратный порядок и безусловный отброс
входящих X-Plugin-*/Cookie/Authorization/X-Forwarded-*/hop-by-hop.
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.gateway.headers import (
    HEADER_CAPABILITY_TOKEN,
    HEADER_COMPANY_ID,
    HEADER_PLUGIN_ID,
    HEADER_PROJECT_ID,
    build_forward_headers,
    build_trusted_headers,
    sanitize_incoming,
)
from prodavan.application.plugins.gateway.ports import (
    GatewayClaims,
    GatewayGrant,
    SESSION_SCOPES,
)


@pytest.fixture
def claims() -> GatewayClaims:
    return GatewayClaims(
        plugin_id="mod_equipment",
        company_id="co-123",
        subject_id="employee:emp-1",
        scopes=SESSION_SCOPES,
        project_id="proj-9",
        grant_id="grant-1",
        employee_id="emp-1",
    )


@pytest.fixture
def grant() -> GatewayGrant:
    return GatewayGrant(
        grant_id="grant-1",
        active=True,
        capability_token="trusted-capability-token",
    )


def test_build_trusted_headers_uses_claims_and_grant(claims, grant) -> None:
    out = build_trusted_headers(claims, grant)
    assert out[HEADER_PLUGIN_ID] == "mod_equipment"
    assert out[HEADER_COMPANY_ID] == "co-123"
    assert out[HEADER_PROJECT_ID] == "proj-9"
    assert out[HEADER_CAPABILITY_TOKEN] == "trusted-capability-token"


def test_incoming_x_company_id_evil_does_not_override_trusted(claims, grant) -> None:
    """КРИТЕРИЙ: входящий ``X-Company-ID: evil`` НЕ подменяет доверенный."""
    incoming = {
        "X-Company-ID": "evil",
        "X-Plugin-ID": "evil-plugin",
        "X-Project-ID": "evil-project",
        "X-Plugin-Capability-Token": "evil-token",
        "Accept": "application/json",
    }
    out = build_forward_headers(incoming, claims, grant)
    assert out[HEADER_COMPANY_ID] == "co-123"
    assert out[HEADER_PLUGIN_ID] == "mod_equipment"
    assert out[HEADER_PROJECT_ID] == "proj-9"
    assert out[HEADER_CAPABILITY_TOKEN] == "trusted-capability-token"
    # Безобидный заголовок представления сохраняется.
    assert out["Accept"] == "application/json"
    # Ни одного «evil»-значения в итоговом наборе.
    assert "evil" not in out.values()


def test_case_insensitive_duplicate_cannot_survive(claims, grant) -> None:
    """Клиент может подать ``x-company-id`` в другом регистре.

    Если оставить оба ключа (канонический + клиентский), downstream может
    прочитать «злой». Регистронезависимая зачистка обязательна.
    """
    incoming = {
        "x-company-id": "evil",
        "X-COMPANY-ID": "evil-too",
        "x-plugin-capability-token": "evil-token",
    }
    out = build_forward_headers(incoming, claims, grant)
    lowered = {k.lower(): v for k, v in out.items()}
    assert lowered["x-company-id"] == "co-123"
    assert lowered["x-plugin-capability-token"] == "trusted-capability-token"
    assert "evil" not in out.values() and "evil-too" not in out.values()


def test_sanitize_incoming_strips_forbidden_families() -> None:
    incoming = {
        "Cookie": "session=steal-me",
        "Authorization": "Bearer stolen",
        "X-Forwarded-For": "1.2.3.4",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "evil.example",
        "X-Real-Ip": "1.2.3.4",
        "Forwarded": "for=1.2.3.4",
        "X-Original-URL": "/admin",
        "X-Plugin-Foo": "bar",
        "X-Prodavan-Signature": "sha256=deadbeef",
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=5",
        "Transfer-Encoding": "chunked",
        "TE": "trailers",
        "Trailer": "Expires",
        "Upgrade": "websocket",
        "Proxy-Authorization": "Basic xxx",
        "Host": "agentscale.dev",
        "Content-Length": "999",
        "Accept": "text/html",
        "Accept-Encoding": "gzip",
        "If-None-Match": "etag-1",
    }
    out = sanitize_incoming(incoming)
    lowered = {k.lower() for k in out}
    for forbidden in (
        "cookie",
        "authorization",
        "x-forwarded-for",
        "x-forwarded-proto",
        "x-forwarded-host",
        "x-real-ip",
        "forwarded",
        "x-original-url",
        "x-plugin-foo",
        "x-prodavan-signature",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-authorization",
        "host",
        "content-length",
    ):
        assert forbidden not in lowered, f"{forbidden} must be dropped"
    # Безобидные заголовки остаются.
    assert out["Accept"] == "text/html"
    assert out["Accept-Encoding"] == "gzip"
    assert out["If-None-Match"] == "etag-1"


def test_anon_claims_get_no_project_and_no_capability_token() -> None:
    anon = GatewayClaims(
        plugin_id="mod_equipment",
        company_id="co-123",
        subject_id="anon",
        scopes=frozenset({"pluginpage:public:read"}),
        anonymous=True,
        grant_id="grant-1",
    )
    grant = GatewayGrant(
        grant_id="grant-1", active=True, capability_token="SHOULD-NOT-LEAK"
    )
    out = build_trusted_headers(anon, grant)
    assert HEADER_PROJECT_ID not in out
    assert HEADER_CAPABILITY_TOKEN not in out
    assert out[HEADER_PLUGIN_ID] == "mod_equipment"
    assert out[HEADER_COMPANY_ID] == "co-123"


def test_inactive_grant_drops_capability_token(claims) -> None:
    grant = GatewayGrant(
        grant_id="grant-1", active=False, capability_token="stale"
    )
    out = build_trusted_headers(claims, grant)
    assert HEADER_CAPABILITY_TOKEN not in out


def test_sanitize_does_not_mutate_input() -> None:
    incoming = {"Cookie": "a=b", "Accept": "application/json"}
    copy_before = dict(incoming)
    sanitize_incoming(incoming)
    assert incoming == copy_before

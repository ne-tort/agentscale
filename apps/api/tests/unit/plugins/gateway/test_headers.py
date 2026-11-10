"""Тесты allowlist'а доверенных заголовков (§6.4, §7.2).

Главный регресс v1: клиентские заголовки распаковывались ПОСЛЕ доверенных и
перезаписывали их. Эти тесты фиксируют обратный порядок и безусловный отброс
входящих X-Plugin-*/Cookie/Authorization/X-Forwarded-*/hop-by-hop.
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.gateway.headers import (
    HEADER_ARTIFACT_DIGEST,
    HEADER_CAPABILITY_TOKEN,
    HEADER_COMPANY_ID,
    HEADER_PLUGIN_ID,
    HEADER_PLUGIN_INSTANCE_ID,
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

from .conftest import ARTIFACT_DIGEST


@pytest.fixture
def claims() -> GatewayClaims:
    return GatewayClaims(
        plugin_id="mod_equipment",
        company_id="co-123",
        subject_id="employee:emp-1",
        plugin_instance_id="inst-alpha-1",
        artifact_digest=ARTIFACT_DIGEST,
        scopes=SESSION_SCOPES,
        project_id="proj-9",
        grant_id="grant-1",
        employee_id="emp-1",
    )


@pytest.fixture
def grant() -> GatewayGrant:
    return GatewayGrant(
        grant_id="grant-1",
        plugin_instance_id="inst-alpha-1",
        active=True,
        capability_token="trusted-capability-token",
        artifact_digest=ARTIFACT_DIGEST,
    )


def test_build_trusted_headers_uses_claims_and_grant(claims, grant) -> None:
    out = build_trusted_headers(claims, grant)
    assert out[HEADER_PLUGIN_ID] == "mod_equipment"
    assert out[HEADER_PLUGIN_INSTANCE_ID] == "inst-alpha-1"
    assert out[HEADER_ARTIFACT_DIGEST] == ARTIFACT_DIGEST
    assert out[HEADER_COMPANY_ID] == "co-123"
    assert out[HEADER_PROJECT_ID] == "proj-9"
    assert out[HEADER_CAPABILITY_TOKEN] == "trusted-capability-token"


def test_incoming_x_company_id_evil_does_not_override_trusted(claims, grant) -> None:
    """КРИТЕРИЙ: входящий ``X-Company-ID: evil`` НЕ подменяет доверенный."""
    incoming = {
        "X-Company-ID": "evil",
        "X-Plugin-ID": "evil-plugin",
        "X-Plugin-Instance-ID": "evil-instance",
        "X-Project-ID": "evil-project",
        "X-Plugin-Capability-Token": "evil-token",
        "X-Plugin-Artifact-Digest": "sha256:" + "f" * 64,
        "Accept": "application/json",
    }
    out = build_forward_headers(incoming, claims, grant)
    assert out[HEADER_COMPANY_ID] == "co-123"
    assert out[HEADER_PLUGIN_ID] == "mod_equipment"
    assert out[HEADER_PLUGIN_INSTANCE_ID] == "inst-alpha-1"
    assert out[HEADER_PROJECT_ID] == "proj-9"
    assert out[HEADER_ARTIFACT_DIGEST] == ARTIFACT_DIGEST
    assert out[HEADER_CAPABILITY_TOKEN] == "trusted-capability-token"
    # Безобидный заголовок представления сохраняется.
    assert out["Accept"] == "application/json"
    # Ни одного «evil»-значения в итоговом наборе.
    assert "evil" not in out.values()
    assert "sha256:" + "f" * 64 not in out.values()


def test_case_insensitive_duplicate_cannot_survive(claims, grant) -> None:
    """Клиент может подать ``x-company-id`` в другом регистре.

    Если оставить оба ключа (канонический + клиентский), downstream может
    прочитать «злой». Регистронезависимая зачистка обязательна.
    """
    incoming = {
        "x-company-id": "evil",
        "X-COMPANY-ID": "evil-too",
        "x-plugin-capability-token": "evil-token",
        "x-plugin-artifact-digest": "sha256:" + "f" * 64,
    }
    out = build_forward_headers(incoming, claims, grant)
    lowered = {k.lower(): v for k, v in out.items()}
    assert lowered["x-company-id"] == "co-123"
    assert lowered["x-plugin-capability-token"] == "trusted-capability-token"
    assert lowered["x-plugin-artifact-digest"] == ARTIFACT_DIGEST
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
        "X-Plugin-Instance-ID": "client-spoof",
        "X-Plugin-Artifact-Digest": "sha256:" + "f" * 64,
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
        "x-plugin-instance-id",
        "x-plugin-artifact-digest",
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
        plugin_instance_id="inst-alpha-1",
        scopes=frozenset({"pluginpage:public:read"}),
        anonymous=True,
        grant_id="grant-1",
    )
    grant = GatewayGrant(
        grant_id="grant-1", plugin_instance_id="inst-alpha-1",
        active=True, capability_token="SHOULD-NOT-LEAK"
    )
    out = build_trusted_headers(anon, grant)
    assert HEADER_PROJECT_ID not in out
    assert HEADER_CAPABILITY_TOKEN not in out
    assert out[HEADER_PLUGIN_ID] == "mod_equipment"
    assert out[HEADER_COMPANY_ID] == "co-123"


def test_inactive_grant_drops_capability_token(claims) -> None:
    grant = GatewayGrant(
        grant_id="grant-1", plugin_instance_id="inst-alpha-1",
        active=False, capability_token="stale"
    )
    out = build_trusted_headers(claims, grant)
    assert HEADER_CAPABILITY_TOKEN not in out


def test_sanitize_does_not_mutate_input() -> None:
    incoming = {"Cookie": "a=b", "Accept": "application/json"}
    copy_before = dict(incoming)
    sanitize_incoming(incoming)
    assert incoming == copy_before



def test_client_instance_spoof_is_replaced_only_by_durable_grant(claims, grant) -> None:
    out = build_forward_headers(
        {
            "X-Plugin-Instance-ID": "inst-client-spoof",
            "X-Plugin-Artifact-Digest": "sha256:" + "f" * 64,
            "Accept": "application/json",
        },
        claims,
        grant,
    )
    assert out[HEADER_PLUGIN_INSTANCE_ID] == "inst-alpha-1"
    assert out[HEADER_ARTIFACT_DIGEST] == ARTIFACT_DIGEST
    assert "inst-client-spoof" not in out.values()
    assert "sha256:" + "f" * 64 not in out.values()


def test_absent_instance_never_gets_a_fictitious_header(claims, grant) -> None:
    from dataclasses import replace

    out = build_trusted_headers(
        replace(claims, plugin_instance_id=None),
        replace(grant, plugin_instance_id=None),
    )
    assert HEADER_PLUGIN_INSTANCE_ID not in out


def test_claim_grant_digest_mismatch_is_not_forwarded(claims) -> None:
    other = "sha256:" + "b" * 64
    grant = GatewayGrant(
        grant_id="grant-1",
        plugin_instance_id="inst-alpha-1",
        active=True,
        capability_token="token",
        artifact_digest=other,
    )
    with pytest.raises(ValueError, match="artifact_digest mismatch"):
        build_trusted_headers(claims, grant)


# ---------------------------------------------------------------------------
# Webhook-ветка: подпись восстанавливается ТОЛЬКО из успешной GatewayAuth.
# ---------------------------------------------------------------------------

def _verified(**overrides):
    from prodavan.application.plugins.gateway.ports import VerifiedWebhook

    fields = {
        "signature": "sha256=trusted",
        "event": "document.created",
        "delivery_id": "dlv-1",
    }
    fields.update(overrides)
    return VerifiedWebhook(**fields)


def test_verified_webhook_headers_are_restored_after_sanitize(claims, grant) -> None:
    """Клиентская подпись удаляется, доверенная восстанавливается."""
    incoming = {
        "X-Prodavan-Signature": "sha256=forged",
        "X-Prodavan-Event": "forged.event",
        "X-Prodavan-Delivery": "dlv-forged",
        "Accept": "application/json",
    }
    out = build_forward_headers(incoming, claims, grant, verified_webhook=_verified())
    assert out["X-Prodavan-Signature"] == "sha256=trusted"
    assert out["X-Prodavan-Event"] == "document.created"
    assert out["X-Prodavan-Delivery"] == "dlv-1"
    # Санитизация не сломала обычный заголовок.
    assert out["Accept"] == "application/json"


def test_without_verified_webhook_the_client_signature_never_survives(claims, grant) -> None:
    """Без успешной HMAC-проверки клиент не может «заявить» подпись."""
    out = build_forward_headers({"X-Prodavan-Signature": "sha256=forged"}, claims, grant)
    assert "X-Prodavan-Signature" not in out


def test_legacy_webhook_omits_version_and_timestamp(claims, grant) -> None:
    out = build_forward_headers({}, claims, grant, verified_webhook=_verified())
    assert "X-Prodavan-Signature-Version" not in out
    assert "X-Prodavan-Timestamp" not in out


def test_versioned_webhook_carries_version_and_timestamp(claims, grant) -> None:
    out = build_forward_headers(
        {},
        claims,
        grant,
        verified_webhook=_verified(signature_version="v2", timestamp="1700000000"),
    )
    assert out["X-Prodavan-Signature-Version"] == "v2"
    assert out["X-Prodavan-Timestamp"] == "1700000000"

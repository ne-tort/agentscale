"""Unit-тесты резолвера и сервиса: fail-closed, R-10, квота, аудит.

Тесты проходят в текущем прогоне (pytest tests/unit/plugins).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from prodavan.application.plugins.egress.errors import EgressDenied
from prodavan.application.plugins.egress.policy import EgressMode
from prodavan.application.plugins.egress.resolver import GrantLookup, GrantPolicyResolver
from prodavan.application.plugins.egress.service import (
    EgressDecisionCode,
    EgressService,
    HopFacts,
)


def _grant(
    *,
    mode: str = "webhooks-only",
    hosts: list[str] | None = None,
    paths: list[str] | None = None,
    max_bytes: int = 1024,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "grant_id": "grant_1",
        "egress": {
            "mode": mode,
            "allowed_hosts": hosts if hosts is not None else ["hooks.slack.com"],
            "allowed_paths": paths if paths is not None else ["/services"],
            "max_egress_bytes": max_bytes,
            **extra,
        },
    }


class FakeGrantStore:
    """In-memory ``GrantStorePort`` для unit-тестов (без БД и сети)."""

    def __init__(self, grants: dict[str, GrantLookup] | None = None) -> None:
        self.grants = grants or {}
        self.calls: list[str] = []

    def get_grant(self, grant_id: str) -> GrantLookup | None:
        self.calls.append(grant_id)
        return self.grants.get(grant_id)


def _store(grant: dict[str, Any] | None = None) -> FakeGrantStore:
    return FakeGrantStore({"grant_1": GrantLookup(raw=grant or _grant())})


def _claims(**extra: Any) -> dict[str, Any]:
    base = {
        "grant_id": "grant_1",
        "company_id": "co_123",
        "sub": "plugin:mod_equipment:co_123:inst_abc",
        "exp": 1794163200,
    }
    return {**base, **extra}


def _service(store: FakeGrantStore, **kwargs: Any) -> EgressService:
    return EgressService(store=store, **kwargs)


def _hops(**kwargs: Any) -> dict[str, Any]:
    """По умолчанию — корректная цепочка R-10 для разрешённого хоста."""
    base = {
        "resolved_host": "hooks.slack.com",
        "hops": [],
        "redirect_checked": True,
        "tls_verified": True,
    }
    return {**base, **kwargs}


# ------------------------------------------------------- Fail-closed резолвер


def test_unknown_grant_raises_egress_denied_not_allow() -> None:
    """Неизвестный grant -> ``EgressDenied``, а НЕ «разрешить по умолчанию»."""
    resolver = GrantPolicyResolver(FakeGrantStore())
    with pytest.raises(EgressDenied) as ei:
        resolver.resolve("grant_unknown")
    assert ei.value.reason == "grant_missing"
    # Через сервис это превращается в deny-решение + аудит.
    decision = _service(FakeGrantStore()).check(
        _claims(grant_id="grant_unknown"), "hooks.slack.com", "/services/x", "POST", 10, **_hops()
    )
    assert decision.allowed is False
    assert decision.code is EgressDecisionCode.GRANT_MISSING
    assert decision.status == 403


def test_revoked_grant_raises_egress_denied() -> None:
    """Отзыв grant'а = отказ, даже если токен ещё «свежий»."""
    revoked = GrantLookup(raw=_grant(), active=False, revoked_at=datetime.now(UTC))
    resolver = GrantPolicyResolver(FakeGrantStore({"grant_1": revoked}))
    with pytest.raises(EgressDenied) as ei:
        resolver.resolve("grant_1")
    assert ei.value.reason == "grant_revoked"


@pytest.mark.parametrize(
    "lookup",
    [
        GrantLookup(raw=_grant(), issued_at=datetime.now(UTC) + timedelta(hours=1)),
        GrantLookup(raw=_grant(), expires_at=datetime.now(UTC) - timedelta(seconds=1)),
    ],
)
def test_not_yet_issued_or_expired_grant_is_denied(lookup: GrantLookup) -> None:
    with pytest.raises(EgressDenied):
        GrantPolicyResolver(FakeGrantStore({"grant_1": lookup})).resolve("grant_1")


def test_grant_store_failure_is_fail_closed() -> None:
    """Хранилище упало -> отказ, а не allow."""

    class BrokenStore:
        def get_grant(self, grant_id: str) -> GrantLookup | None:
            raise RuntimeError("db down")

    with pytest.raises(EgressDenied) as ei:
        GrantPolicyResolver(BrokenStore()).resolve("grant_1")
    assert ei.value.reason == "store_unavailable"


def test_missing_store_is_rejected() -> None:
    with pytest.raises(EgressDenied):
        GrantPolicyResolver(None)  # type: ignore[arg-type]
    with pytest.raises(EgressDenied):
        EgressService(store=None)  # type: ignore[arg-type]


def test_resolver_reads_only_grant_id_from_claims() -> None:
    """В токене нет ``egress``: подсунутые поля игнорируются, политика из БД."""
    resolver = GrantPolicyResolver(_store())
    claims = _claims(
        egress={"mode": "full", "allowed_hosts": ["evil.com"]},
        allowed_hosts=["evil.com"],
    )
    policy = resolver.resolve_claims(claims)
    assert policy.mode is EgressMode.WEBHOOKS_ONLY
    assert policy.allowed_hosts == ("hooks.slack.com",)


def test_missing_grant_id_is_denied() -> None:
    with pytest.raises(EgressDenied) as ei:
        GrantPolicyResolver(_store()).resolve_claims({"company_id": "co_123"})
    assert ei.value.reason == "grant_id_missing"


# ---------------------------------------- Критерий 22: TTL токена не меняет политику


def test_expired_token_does_not_change_policy_from_grant() -> None:
    """Истёкший ``exp`` не влияет на allowlist: правила из grant в БД."""
    resolver = GrantPolicyResolver(_store())
    fresh = resolver.resolve_claims(_claims(exp=4102444800))  # далёкое будущее
    expired = resolver.resolve_claims(_claims(exp=1))  # давно истёк
    assert fresh == expired
    assert expired.allowed_hosts == ("hooks.slack.com",)
    assert expired.allowed_paths == ("/services",)
    assert expired.mode is EgressMode.WEBHOOKS_ONLY
    # Ни одного обращения к полю egress токена: только get_grant(grant_id).
    assert len(resolver._store.calls) == 2  # type: ignore[attr-defined]


def test_policy_is_stable_across_token_rotation() -> None:
    """Ротация короткого токена (300 с) не пересобирает сетевые правила."""
    store = _store()
    resolver = GrantPolicyResolver(store)
    policies = [
        resolver.resolve_claims(_claims(exp=exp))
        for exp in (1, 1794163200, 4102444800, 1794163500)
    ]
    assert all(policy == policies[0] for policy in policies)
    # Один и тот же grant_id, ни одного чтения egress из токена.
    assert store.calls == ["grant_1"] * len(policies)


# ------------------------------------------------------------- EgressService


def test_none_mode_denies_all_requests_via_service() -> None:
    service = _service(FakeGrantStore({"grant_1": GrantLookup(raw=_grant(mode="none"))}))
    decision = service.check(_claims(), "hooks.slack.com", "/services/x", "POST", 10, **_hops())
    assert decision.allowed is False
    assert decision.code is EgressDecisionCode.MODE_NONE
    assert decision.status == 403
    assert decision.audit_event is not None
    assert decision.audit_event["event"] == "plugin_egress_request"
    assert decision.audit_event["decision"] == "deny"


def test_host_evil_com_gets_403_via_service() -> None:
    """Критерий 8: ``Host: evil.com`` при allowlist slack -> 403 + аудит."""
    service = _service(_store())
    decision = service.check(_claims(), "evil.com", "/services/x", "POST", 10, **_hops())
    assert decision.allowed is False
    assert decision.code is EgressDecisionCode.HOST_NOT_ALLOWED
    assert decision.status == 403
    assert service.events[0]["decision"] == "deny"


def test_private_and_metadata_hosts_denied_via_service() -> None:
    for host in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "metadata.google.internal"):
        service = _service(_store())
        decision = service.check(_claims(), host, "/services/x", "POST", 10, **_hops())
        assert decision.allowed is False
        assert decision.code in (
            EgressDecisionCode.HOST_NOT_ALLOWED,
            EgressDecisionCode.RESOLVED_HOST_DENIED,
        )


def test_full_without_approved_denied_via_service() -> None:
    service = _service(
        FakeGrantStore({"grant_1": GrantLookup(raw=_grant(mode="full", approved=False))})
    )
    decision = service.check(_claims(), "hooks.slack.com", "/services/x", "POST", 10, **_hops())
    assert decision.allowed is False
    assert decision.code is EgressDecisionCode.POLICY_INVALID


def test_allowed_request_writes_audit_event() -> None:
    service = _service(_store())
    decision = service.check(_claims(), "hooks.slack.com", "/services/T/B/X", "POST", 64, **_hops())
    assert decision.allowed is True
    assert decision.code is EgressDecisionCode.ALLOWED
    assert decision.status == 200
    event = decision.audit_event
    assert event is not None
    assert event["event"] == "plugin_egress_request"
    assert event["host"] == "hooks.slack.com"
    assert event["path"] == "/services/T/B/X"
    assert event["method"] == "POST"
    assert event["bytes"] == 64
    assert event["grant_id"] == "grant_1"
    assert event["tenant"] == "co_123"
    assert len(service.events) == 1


def test_agent_scope_uses_agent_audit_event_name() -> None:
    service = _service(_store(), scope="agent")
    decision = service.check(_claims(), "hooks.slack.com", "/services/x", "POST", 10, **_hops())
    assert decision.allowed is True
    assert decision.audit_event["event"] == "agent_egress_request"  # type: ignore[index]


def test_quota_bytes_exceeded_denies() -> None:
    service = _service(_store())
    decision = service.check(_claims(), "hooks.slack.com", "/services/x", "POST", 2048, **_hops())
    assert decision.allowed is False
    assert decision.code is EgressDecisionCode.QUOTA_EXCEEDED
    assert decision.status == 429


def test_invalid_bytes_denies() -> None:
    service = _service(_store())
    assert service.check(_claims(), "hooks.slack.com", "/services/x", "POST", -1, **_hops()).allowed is False
    assert service.check(_claims(), "hooks.slack.com", "/services/x", "POST", "10", **_hops()).allowed is False  # type: ignore[arg-type]


# ------------------------------------------------------------------- R-10


def test_r10_redirect_to_metadata_is_denied() -> None:
    """Редирект на 169.254.169.254 ловится проверкой КАЖДОГО хопа."""
    service = _service(_store())
    decision = service.check(
        _claims(),
        "hooks.slack.com",
        "/services/x",
        "POST",
        10,
        **_hops(
            hops=["hooks.slack.com", "169.254.169.254"],
            resolved_host="169.254.169.254",
        ),
    )
    assert decision.allowed is False
    assert decision.code is EgressDecisionCode.RESOLVED_HOST_DENIED


def test_r10_dns_rebinding_to_loopback_is_denied() -> None:
    """DNS-rebinding: финальный resolved host = 127.0.0.1 -> отказ."""
    service = _service(_store())
    decision = service.check(
        _claims(),
        "hooks.slack.com",
        "/services/x",
        "POST",
        10,
        **_hops(resolved_host="127.0.0.1"),
    )
    assert decision.allowed is False
    assert decision.code is EgressDecisionCode.RESOLVED_HOST_DENIED


def test_r10_requires_final_resolved_host() -> None:
    """Нет финального resolved host -> отказ (fail-closed)."""
    service = _service(_store())
    decision = service.check(
        _claims(),
        "hooks.slack.com",
        "/services/x",
        "POST",
        10,
        **_hops(resolved_host=None),
    )
    assert decision.allowed is False
    assert decision.code is EgressDecisionCode.RESOLVED_HOST_DENIED


def test_r10_requires_redirect_and_tls_checked_per_hop() -> None:
    """Неподтверждённые redirect/TLS на каждом хопе -> отказ."""
    service = _service(_store())
    for overrides in ({"redirect_checked": False}, {"tls_verified": False}):
        decision = service.check(
            _claims(), "hooks.slack.com", "/services/x", "POST", 10, **_hops(**overrides)
        )
        assert decision.allowed is False
        assert decision.code is EgressDecisionCode.RESOLVED_HOST_DENIED


def test_r10_hop_inspector_port_is_used() -> None:
    """Порт контроллера сообщает факты о хопах; злой resolved host -> отказ."""

    class MaliciousInspector:
        def inspect(self, *, host: str, path: str, method: str) -> HopFacts:
            return HopFacts(
                hop_hosts=(),
                resolved_host="10.0.0.5",
                redirects_checked=True,
                tls_verified=True,
            )

    service = _service(_store(), hop_inspector=MaliciousInspector())  # type: ignore[arg-type]
    decision = service.check(_claims(), "hooks.slack.com", "/services/x", "POST", 10)
    assert decision.allowed is False
    assert decision.code is EgressDecisionCode.RESOLVED_HOST_DENIED


# --------------------------------------------------------- Аудит: append failure


def test_audit_append_failure_denies_positive_mutation() -> None:
    """§4.5: append failure = deny/system failure, а не allow."""

    class BrokenAudit:
        def record(self, event: Any) -> None:
            raise RuntimeError("sink down")

    service = _service(_store(), audit=BrokenAudit())  # type: ignore[arg-type]
    decision = service.check(_claims(), "hooks.slack.com", "/services/x", "POST", 10, **_hops())
    assert decision.allowed is False
    assert decision.code is EgressDecisionCode.AUDIT_FAILED
    assert decision.status == 503


def test_explicit_audit_sink_receives_event() -> None:
    class Collector:
        def __init__(self) -> None:
            self.events: list[Any] = []

        def record(self, event: Any) -> None:
            self.events.append(dict(event))

    collector = Collector()
    service = _service(_store(), audit=collector)  # type: ignore[arg-type]
    decision = service.check(_claims(), "hooks.slack.com", "/services/x", "POST", 10, **_hops())
    assert decision.allowed is True
    assert len(collector.events) == 1
    assert collector.events[0] == decision.audit_event

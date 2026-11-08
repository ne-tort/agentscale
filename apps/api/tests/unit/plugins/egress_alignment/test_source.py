"""Барьер №2: egress ТОЛЬКО из `CapabilityGrant` по `grant_id`, не из JWT.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЛИСЬ (verification freeze, план миграции §0.2).

Проверяем ``assert_policy_source_is_grant`` на реальных объектах задачи 08:
``EgressPolicy`` из ``resolve_policy``, Envoy-конфиг из ``build_envoy_routes``
(там ``_meta.source == "CapabilityGrant"``), ``GrantPolicyResolver`` с
in-memory ``GrantStorePort``.
"""

from __future__ import annotations

from typing import Any

import pytest

from prodavan.application.plugins.egress.alignment import (
    GRANT_SOURCE,
    PolicyAlignmentError,
    assert_policy_source_is_grant,
)
from prodavan.application.plugins.egress.generator import EgressConfigGenerator
from prodavan.application.plugins.egress.policy import resolve_policy
from prodavan.application.plugins.egress.resolver import GrantLookup, GrantPolicyResolver


def _grant(*, grant_id: str = "grant_1") -> dict[str, Any]:
    return {
        "grant_id": grant_id,
        "egress": {
            "mode": "webhooks-only",
            "allowed_hosts": ["hooks.slack.com"],
            "allowed_paths": ["/services"],
            "max_egress_bytes": 1024,
        },
    }


class _FakeStore:
    """In-memory `GrantStorePort` (без БД и сети)."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.calls: list[str] = []

    def get_grant(self, grant_id: str) -> GrantLookup | None:
        self.calls.append(grant_id)
        return GrantLookup(raw=self.raw) if grant_id == self.raw["grant_id"] else None


# Цепочка резолвинга по grant (как в resolver.py / service.py).
GRANT_CHAIN = [
    "PluginClaims.grant_id=grant_1",
    "GrantPolicyResolver.resolve_claims -> _claim_grant_id",
    "GrantStorePort.get_grant('grant_1')",
    "CapabilityGrant.egress -> resolve_policy",
    "build_envoy_routes(policy)",
]


# --------------------------------------------------------------- ПОЛОЖИТЕЛЬНЫЕ


def test_policy_resolved_from_capability_grant_is_ok() -> None:
    """Egress из `CapabilityGrant` в БД по `grant_id` -> OK."""
    store = _FakeStore(_grant())
    policy = GrantPolicyResolver(store).resolve_claims({"grant_id": "grant_1"})
    # У EgressPolicy нет атрибута source — доказываем источник ЦЕПОЧКОЙ.
    assert assert_policy_source_is_grant(
        policy,
        chain=GRANT_CHAIN,
        claims={"grant_id": "grant_1", "company_id": "co_123"},
        grant_id="grant_1",
    ) is None
    assert store.calls == ["grant_1"]
    assert policy.grant_id == "grant_1"


def test_envoy_config_meta_from_grant_is_ok() -> None:
    """Задача 08 ставит `_meta.source = 'CapabilityGrant'` + grant_id."""
    policy = resolve_policy(_grant(grant_id="grant_envoy"))
    config = EgressConfigGenerator().build_envoy_routes(policy)
    assert config["_meta"]["source"] == GRANT_SOURCE
    assert config["_meta"]["grant_id"] == "grant_envoy"
    assert assert_policy_source_is_grant(config, grant_id="grant_envoy") is None
    assert assert_policy_source_is_grant(config, chain=GRANT_CHAIN, grant_id="grant_envoy") is None


def test_expired_claims_do_not_change_grant_source() -> None:
    """Критерий №22: истёкший токен не меняет allowlist из grant."""
    policy = resolve_policy(_grant())
    expired_claims = {"grant_id": "grant_1", "exp": 1, "company_id": "co_123"}
    assert assert_policy_source_is_grant(
        policy,
        chain=GRANT_CHAIN,
        claims=expired_claims,
        grant_id="grant_1",
    ) is None


# ------------------------------------------------------------------- ОТКАЗЫ


def test_egress_from_token_egress_is_rejected() -> None:
    """Ключевой сценарий: `token.egress` в цепочке -> ЯВНЫЙ отказ."""
    policy = resolve_policy(_grant())
    jwt_chain = [
        "PluginClaims token.egress.allowed_hosts=['evil.com']",
        "build_envoy_routes(token.egress)",
        "grant_id=grant_1",  # даже при наличии grant_id маркер JWT запрещает
    ]
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_policy_source_is_grant(policy, chain=jwt_chain, grant_id="grant_1")
    assert ei.value.reason == "chain_jwt_marker"
    assert "token.egress" in ei.value.selector


def test_egress_from_claims_allowed_hosts_is_rejected() -> None:
    """`claims.allowed_hosts` в цепочке -> отказ."""
    policy = resolve_policy(_grant())
    chain = [
        "claims.allowed_hosts=['hooks.slack.com']",
        "CapabilityGrant",
    ]
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_policy_source_is_grant(policy, chain=chain)
    assert ei.value.reason == "chain_jwt_marker"
    assert "claims.allowed_hosts" in ei.value.selector


@pytest.mark.parametrize(
    "marker",
    [
        "jwt.egress",
        "plugin_claims.egress",
        "token.allowed_paths",
        "claims.allowed_paths",
        "token.quotas",
        "claims.max_egress_bytes",
        "jwt.allowed_hosts",
    ],
)
def test_every_jwt_chain_marker_is_rejected(marker: str) -> None:
    """assert на отсутствие ЛЮБОГО поля JWT в цепочке резолвинга."""
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_policy_source_is_grant(
            resolve_policy(_grant()),
            chain=[f"step: {marker}", "GrantStorePort.get_grant"],
        )
    assert ei.value.reason == "chain_jwt_marker"
    assert ei.value.selector == marker


def test_forbidden_fields_in_claims_or_token_are_rejected() -> None:
    """Поля `egress` / `allowed_hosts` в payload токена запрещены (§4.2)."""
    policy = resolve_policy(_grant())
    for container_name, payload in (
        ("claims", {"egress": {"allowed_hosts": ["evil.com"]}, "grant_id": "grant_1"}),
        ("token", {"allowed_hosts": ["evil.com"], "grant_id": "grant_1"}),
        ("claims", {"quotas": {"ops_per_minute": 999}, "grant_id": "grant_1"}),
        ("token", {"allowed_paths": ["/"], "grant_id": "grant_1"}),
        ("claims", {"max_egress_bytes": 999999, "grant_id": "grant_1"}),
    ):
        kwargs = {"claims": payload} if container_name == "claims" else {"token": payload}
        with pytest.raises(PolicyAlignmentError) as ei:
            assert_policy_source_is_grant(policy, chain=GRANT_CHAIN, **kwargs)
        assert ei.value.reason == "jwt_egress_field_present"
        assert container_name in ei.value.selector


def test_explicit_jwt_source_is_rejected() -> None:
    """Объект, помеченный source='JWT' / 'PluginClaims' -> отказ."""
    for source in ("JWT", "PluginClaims", "token", "egress.from.jwt"):
        with pytest.raises(PolicyAlignmentError) as ei:
            assert_policy_source_is_grant(
                {"_meta": {"source": source, "grant_id": "grant_1"}}
            )
        assert ei.value.reason == "source_is_jwt"
        assert source in ei.value.selector


def test_missing_grant_id_is_rejected() -> None:
    """Нет grant_id -> политика не резолвилась по БД; отказ."""
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_policy_source_is_grant(
            {"_meta": {"source": GRANT_SOURCE}}, chain=GRANT_CHAIN
        )
    assert ei.value.reason == "grant_id_missing"


def test_grant_id_mismatch_is_rejected() -> None:
    """grant_id из токена != grant_id политики -> отказ."""
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_policy_source_is_grant(
            {"_meta": {"source": GRANT_SOURCE, "grant_id": "grant_1"}},
            chain=GRANT_CHAIN,
            grant_id="grant_other",
        )
    assert ei.value.reason == "grant_id_mismatch"


def test_missing_chain_and_undeclared_source_is_rejected() -> None:
    """Fail-closed: цепочки нет и source не доказан -> отказ."""
    policy = resolve_policy(_grant())
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_policy_source_is_grant(policy)
    assert ei.value.reason == "chain_missing"


def test_chain_without_grant_evidence_is_rejected() -> None:
    """Нет ни одного доказательства резолвинга по grant -> отказ."""
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_policy_source_is_grant(
            {"_meta": {"source": GRANT_SOURCE, "grant_id": "grant_1"}},
            chain=["static envoy.yaml", "webhooks.example.com (v1/v3)"],
        )
    assert ei.value.reason == "chain_grant_evidence_missing"


@pytest.mark.parametrize("chain", [None, "", [], "grant_id"])
def test_empty_or_invalid_chain_is_fail_closed(chain: Any) -> None:
    """Пустая цепочка / строка вместо списка = источник не доказан."""
    with pytest.raises(PolicyAlignmentError):
        assert_policy_source_is_grant(
            {"_meta": {"source": "not-grant", "grant_id": "grant_1"}}, chain=chain
        )


def test_none_result_is_rejected() -> None:
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_policy_source_is_grant(None)
    assert ei.value.reason == "source_unknown"

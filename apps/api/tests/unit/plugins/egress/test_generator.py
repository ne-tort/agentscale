"""Unit-тесты генератора: Envoy-роуты, ДВЕ NetworkPolicy, единый аудит.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЛИСЬ (verification freeze, план миграции §0.2).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.egress.generator import (
    EgressConfigGenerator,
    EgressScope,
    build_audit_event,
)
from prodavan.application.plugins.egress.policy import resolve_policy

GEN = EgressConfigGenerator()


def _policy(mode: str, hosts: list[str], paths: list[str] | None = None, **extra: object):
    return resolve_policy(
        {
            "grant_id": "grant_gen",
            "egress": {
                "mode": mode,
                "allowed_hosts": hosts,
                "allowed_paths": paths or ["/"],
                "max_egress_bytes": 1024,
                **extra,
            },
        }
    )


def _ports(np: dict) -> set[int]:
    """Все TCP/UDP-порты из egress-правил NP."""
    return {
        port["port"]
        for rule in np["spec"]["egress"]
        for port in rule.get("ports", [])
    }


# ------------------------------------------------------------- Envoy-роуты


def test_envoy_clusters_are_built_from_grant_allowlist() -> None:
    """Кластеры ДИНАМИЧЕСКИЕ из allowlist, без статического webhooks.example.com."""
    policy = _policy("webhooks-only", ["hooks.slack.com", "api.github.com"], ["/services", "/repos"])
    config = GEN.build_envoy_routes(policy)
    clusters = config["static_resources"]["clusters"]
    names = {cluster["name"] for cluster in clusters}
    assert names == {"egress_hooks_slack_com", "egress_api_github_com"}
    assert config["_meta"]["source"] == "CapabilityGrant"
    assert config["_meta"]["grant_id"] == "grant_gen"
    # Никакого захардкоженного хоста из v1/v3.
    assert "webhooks.example.com" not in str(config)
    assert all(cluster["type"] == "LOGICAL_DNS" for cluster in clusters)


def test_envoy_routes_cover_host_path_method_and_tls() -> None:
    """Маршруты = хост × path_prefix × метод; TLS/SAN проверяется на каждом хопе."""
    policy = _policy("webhooks-only", ["hooks.slack.com"], ["/services"])
    config = GEN.build_envoy_routes(policy)
    vhosts = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]["route_config"]["virtual_hosts"]
    allowed = next(v for v in vhosts if v["name"] == "allow_egress_hooks_slack_com")
    assert allowed["domains"] == ["hooks.slack.com"]
    prefixes = {
        route["match"]["path_separated_prefix"]
        for route in allowed["routes"]
        if "path_separated_prefix" in route.get("match", {})
    }
    assert prefixes == {"/services"}
    cluster = config["static_resources"]["clusters"][0]
    tls = cluster["transport_socket"]["typed_config"]
    assert tls["sni"] == "hooks.slack.com"
    assert tls["common_tls_context"]["validation_context"]["match_typed_subject_alt_names"]


def test_envoy_deny_route_403_for_non_match() -> None:
    """Критерий 8: несовпадение -> ``direct_response: 403``."""
    policy = _policy("webhooks-only", ["hooks.slack.com"], ["/services"])
    config = GEN.build_envoy_routes(policy)
    vhosts = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]["route_config"]["virtual_hosts"]
    default_deny = next(v for v in vhosts if v["name"] == "default_deny")
    assert default_deny["domains"] == ["*"]
    last = default_deny["routes"][-1]
    assert last["direct_response"]["status"] == 403
    # Host: evil.com не имеет собственного vhost -> уходит в default_deny.
    assert all(v["domains"] != ["evil.com"] for v in vhosts)


def test_envoy_none_mode_is_fail_closed() -> None:
    """``none`` -> ноль кластеров, только 403."""
    config = GEN.build_envoy_routes(_policy("none", []))
    assert config["static_resources"]["clusters"] == []
    assert config["_meta"]["cluster_count"] == 0
    vhosts = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]["route_config"]["virtual_hosts"]
    assert len(vhosts) == 1
    assert vhosts[0]["routes"][-1]["direct_response"]["status"] == 403


# --------------------------------------------------------- NetworkPolicy


def test_plugin_np_contains_5432_and_9000() -> None:
    """§6.2 / §13.3: plugin-NP включает tenant-postgres :5432 и MinIO :9000."""
    np = GEN.build_network_policy(EgressScope.PLUGIN)
    ports = _ports(np)
    assert 5432 in ports
    assert 9000 in ports
    assert 6432 in ports  # pgbouncer
    assert 53 in ports  # DNS
    assert np["metadata"]["name"] == "plugin-egress"
    assert np["spec"]["policyTypes"] == ["Ingress", "Egress"]


def test_agent_np_has_no_5432_and_no_9000() -> None:
    """Агент: DNS + 80/443 + :8001, БЕЗ плагиновых портов."""
    np = GEN.build_network_policy(EgressScope.AGENT)
    ports = _ports(np)
    assert 53 in ports
    assert 80 in ports
    assert 443 in ports
    assert 8001 in ports
    for forbidden in (5432, 6432, 9000, 9092, 8080):
        assert forbidden not in ports
    assert np["metadata"]["name"] == "agent-egress-reference"


def test_two_np_from_one_generator_are_different() -> None:
    """ДВЕ NP из одного генератора, НЕ «единая NP» (§6.1, §13.3)."""
    plugin = GEN.build_network_policy("plugin")
    agent = GEN.build_network_policy("agent")
    assert plugin["metadata"]["name"] != agent["metadata"]["name"]
    assert plugin["spec"]["podSelector"] != agent["spec"]["podSelector"]
    assert _ports(plugin) - _ports(agent) >= {5432, 6432, 9000, 9092}
    assert EgressConfigGenerator(namespace="agentscale").build_network_policy("plugin") is not None


def test_minio_uses_as_built_label_not_design_placeholder() -> None:
    """Уточнение задачи 14: MinIO = app.kubernetes.io/name: agentscale-minio."""
    np = GEN.build_network_policy(EgressScope.PLUGIN)
    selectors = [
        peer["podSelector"]["matchLabels"]
        for rule in np["spec"]["egress"]
        for peer in rule["to"]
        if "podSelector" in peer
    ]
    assert {"app.kubernetes.io/name": "agentscale-minio"} in selectors
    assert {"app": "minio"} not in selectors
    assert {"app": "tenant-postgres"} in selectors
    # prodavan (платформенный Postgres) НЕ входит в egress.
    assert {"app": "agentscale-postgres"} not in selectors


def test_unknown_scope_is_rejected() -> None:
    with pytest.raises(ValueError):
        GEN.build_network_policy("unknown")  # type: ignore[arg-type]


# --------------------------------------------------------------- Аудит


def _audit_kwargs(scope: str) -> dict:
    return {
        "scope": scope,
        "host": "hooks.slack.com",
        "path": "/services/xxx",
        "method": "POST",
        "status": 403,
        "bytes": 0,
        "decision": "deny",
        "tenant": "co_123",
        "subject": "plugin:mod_equipment",
        "occurred_at": "2026-11-08T09:00:00+00:00",
        "grant_id": "grant_1",
        "request_id": "req_1",
        "code": "host_not_allowed",
        "reason": "test",
    }


def test_plugin_and_agent_audit_events_have_identical_fields() -> None:
    """§4.5 / §6.3: одинаковая схема, различается только имя события."""
    plugin = build_audit_event(**_audit_kwargs("plugin"))
    agent = build_audit_event(**_audit_kwargs("agent"))
    assert plugin["event"] == "plugin_egress_request"
    assert agent["event"] == "agent_egress_request"
    assert set(plugin) == set(agent)
    assert tuple(plugin) == tuple(agent)
    assert tuple(plugin) == GEN.audit_fields()
    assert {key: value for key, value in plugin.items() if key != "event"} == {
        key: value for key, value in agent.items() if key != "event"
    }


def test_audit_event_contains_no_secrets() -> None:
    """§4.5: ни токенов, ни тел, ни raw SQL в событии."""
    event = build_audit_event(**_audit_kwargs("plugin"))
    assert set(event) == set(GEN.audit_fields())
    assert "payload" not in event and "token" not in event and "sql" not in event


def test_audit_event_rejects_invalid_decision_or_bytes() -> None:
    with pytest.raises(Exception):
        build_audit_event(**{**_audit_kwargs("plugin"), "decision": "maybe"})
    with pytest.raises(Exception):
        build_audit_event(**{**_audit_kwargs("agent"), "bytes": -1})

"""Статический regression-audit emergency fail-closed контура плагинов.

Проверяются непосредственно исходные YAML, без kustomize и обращения к кластеру.
Egress нормализуется в набор destination/protocol/port, поэтому перестановка rules,
peers или ports не влияет на результат. Любой дополнительный destination или порт,
включая tenant-postgres, PgBouncer, MinIO, Redpanda и общий Envoy, закрывает тест.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
GATEWAY_MANIFEST = REPO_ROOT / "infra" / "k3s" / "base" / "plugins" / "plugin-gateway.yaml"
POLICY_MANIFEST = (
    REPO_ROOT / "infra" / "k3s" / "base" / "plugins" / "networkpolicy-plugin.yaml"
)


def _documents(path: Path) -> list[dict]:
    return [
        doc
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if doc
    ]


def _one_document(docs: list[dict], kind: str, name: str) -> dict:
    matches = [
        doc
        for doc in docs
        if doc.get("kind") == kind
        and (doc.get("metadata") or {}).get("name") == name
    ]
    assert len(matches) == 1, f"ожидался ровно один {kind}/{name}"
    return matches[0]


def _egress_flows(policy: dict) -> set[tuple]:
    """Разворачивает peers × ports и отклоняет неоднозначные/широкие selectors."""
    flows: set[tuple] = set()
    rules = (policy.get("spec") or {}).get("egress")
    assert isinstance(rules, list) and rules, "plugin egress обязан быть явным"

    for rule in rules:
        assert set(rule) == {"to", "ports"}, (
            "каждое egress rule обязано явно ограничивать и peers, и ports"
        )
        peers = rule["to"]
        ports = rule["ports"]
        assert peers and ports, "пустой to/ports недопустим в fail-closed allowlist"

        for peer in peers:
            assert set(peer) == {"namespaceSelector", "podSelector"}, (
                "разрешены только namespaceSelector + podSelector, без ipBlock"
            )
            namespace_selector = peer["namespaceSelector"]
            pod_selector = peer["podSelector"]
            assert set(namespace_selector) == {"matchLabels"}
            assert set(pod_selector) == {"matchLabels"}

            namespace_labels = namespace_selector["matchLabels"]
            pod_labels = pod_selector["matchLabels"]
            assert set(namespace_labels) == {"kubernetes.io/metadata.name"}
            assert pod_labels, "пустой podSelector выбрал бы все pod'ы namespace"
            destination = (
                namespace_labels["kubernetes.io/metadata.name"],
                tuple(sorted(pod_labels.items())),
            )

            for port in ports:
                assert set(port) == {"protocol", "port"}, (
                    "protocol и числовой port обязаны быть явными"
                )
                assert isinstance(port["port"], int)
                flows.add((*destination, port["protocol"], port["port"]))

    return flows


def test_plugin_platform_is_emergency_disabled() -> None:
    docs = _documents(GATEWAY_MANIFEST)
    config = _one_document(docs, "ConfigMap", "plugin-gateway-config")
    assert (config.get("data") or {}).get("PLUGIN_PLATFORM_ENABLED") == "false", (
        "plugin platform запрещено включать до security/lifecycle remediation"
    )


def test_plugin_egress_allows_only_gateway_and_dns() -> None:
    policy = _one_document(
        _documents(POLICY_MANIFEST), "NetworkPolicy", "plugin-egress"
    )
    flows = _egress_flows(policy)

    gateway = (
        "agentscale-dev",
        (("app", "plugin-gateway"),),
        "TCP",
        8080,
    )
    dns = {
        ("kube-system", (("k8s-app", "kube-dns"),), "UDP", 53),
        ("kube-system", (("k8s-app", "kube-dns"),), "TCP", 53),
    }
    assert gateway in flows, "потерян обязательный egress к plugin-gateway:8080"
    assert dns <= flows, "потерян обязательный UDP/TCP egress к kube-dns:53"
    assert flows == {gateway} | dns, (
        "разрешены только plugin-gateway:8080 и DNS; запрещённые или иные "
        f"destinations/ports обнаружены: {sorted(flows - ({gateway} | dns))}"
    )

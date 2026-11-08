"""Общие фикстуры для тестов network-alignment (НАПИСАНЫ, НЕ ЗАПУСКАЛИСЬ)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prodavan.application.plugins.egress.generator import EgressConfigGenerator, EgressScope

REPO_ROOT = Path(__file__).resolve().parents[6]
MANIFEST_PLUGIN = REPO_ROOT / "infra" / "k3s" / "base" / "plugins" / "networkpolicy-plugin.yaml"
MANIFEST_AGENT = REPO_ROOT / "infra" / "k3s" / "base" / "plugins" / "networkpolicy-agent.yaml"
PLATFORM_JOB = REPO_ROOT / "infra" / "k3s" / "base" / "platform" / "plugin-topics-init.yaml"
PROJECT_WHITELIST = REPO_ROOT / "infra" / "argocd" / "apps" / "00-project.yaml"


def generated_np(scope: str = "plugin") -> dict[str, Any]:
    """Реальный вывод генератора задачи 08, а не синтетический словарь."""
    return EgressConfigGenerator().build_network_policy(
        EgressScope.PLUGIN if scope == "plugin" else EgressScope.AGENT
    )


def with_legacy_labels(np_dict: dict[str, Any]) -> dict[str, Any]:
    """Подменить MinIO/Kafka на выдуманные лейблы раннего дизайна §6.2."""
    import copy

    np = copy.deepcopy(np_dict)
    for rule in np["spec"]["egress"]:
        for peer in rule.get("to", []):
            labels = peer.get("podSelector", {}).get("matchLabels", {})
            if labels.get("app.kubernetes.io/name") == "agentscale-minio":
                labels.clear()
                labels["app"] = "minio"
            elif labels.get("app.kubernetes.io/name") == "agentscale-kafka":
                labels.clear()
                labels["app"] = "kafka"
    return np

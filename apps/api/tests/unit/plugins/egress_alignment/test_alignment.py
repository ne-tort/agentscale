"""Барьер №1: NP с выдуманными лейблами НЕ применяется, as-built — применяется.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЛИСЬ (verification freeze, план миграции §0.2).

Ключевой сценарий (постановка задачи): задача 08 могла сгенерировать NP по
дизайну дословно (`app: minio` / `app: kafka`). Такая NP «успешно» применяется,
но не совпадает ни с одним подом. Барьер обязан ЯВНО ЗАПРЕТИТЬ применение и
НЕ вызвать `apply`.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from prodavan.application.plugins.egress.alignment import (
    AGENT_FORBIDDEN_PORTS,
    AS_BUILT_PROVENANCE,
    AS_BUILT_SELECTORS,
    FORBIDDEN_SELECTORS,
    POLICY_ALIGNMENT_ERROR_CODE,
    PolicyAlignmentError,
    apply_generated_policy_guarded,
    assert_generated_policy_matches_as_built,
    load_as_built_manifest_text,
)
from prodavan.application.plugins.egress.errors import EgressDenied

from .helpers import MANIFEST_AGENT, MANIFEST_PLUGIN, generated_np, with_legacy_labels


def test_as_built_selectors_are_the_single_source_of_truth() -> None:
    """7 эталонных селекторов, каждый — с подтверждением файл:строка."""
    assert set(AS_BUILT_SELECTORS) == {
        "minio",
        "kafka",
        "tenant-postgres",
        "pgbouncer",
        "plugin-gateway",
        "egress-proxy",
        "kube-dns",
    }
    assert AS_BUILT_SELECTORS["minio"] == {"app.kubernetes.io/name": "agentscale-minio"}
    assert AS_BUILT_SELECTORS["kafka"] == {"app.kubernetes.io/name": "agentscale-kafka"}
    assert AS_BUILT_SELECTORS["tenant-postgres"] == {"app": "tenant-postgres"}
    assert AS_BUILT_SELECTORS["pgbouncer"] == {"app": "pgbouncer"}
    assert AS_BUILT_SELECTORS["plugin-gateway"] == {"app": "plugin-gateway"}
    assert AS_BUILT_SELECTORS["egress-proxy"] == {"app": "egress-proxy"}
    assert AS_BUILT_SELECTORS["kube-dns"] == {"k8s-app": "kube-dns"}
    # Ни одного выдуманного лейбла в эталоне.
    assert {"app": "minio"} not in AS_BUILT_SELECTORS.values()
    assert {"app": "kafka"} not in AS_BUILT_SELECTORS.values()
    # У каждого селектора есть подтверждение as-built (для таблицы в docs/).
    assert set(AS_BUILT_PROVENANCE) == set(AS_BUILT_SELECTORS)
    assert all(":" in value for value in AS_BUILT_PROVENANCE.values())
    assert {"app": "minio"} in FORBIDDEN_SELECTORS.values()
    assert {"app": "kafka"} in FORBIDDEN_SELECTORS.values()
    assert {"app": "agentscale-postgres"} in FORBIDDEN_SELECTORS.values()


# ------------------------------------------------------- КЛЮЧЕВОЙ СЦЕНАРИЙ


def test_np_with_app_minio_is_rejected_with_concrete_selector() -> None:
    """NP с `app: minio` -> PolicyAlignmentError с указанием конкретного селектора."""
    np = with_legacy_labels(generated_np("plugin"))
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_generated_policy_matches_as_built(np, "plugin")
    err = ei.value
    assert err.selector == "{app: minio}"
    assert err.scope == "plugin"
    assert err.reason == "forbidden_selector"
    assert err.code == POLICY_ALIGNMENT_ERROR_CODE
    assert isinstance(err, EgressDenied)  # problem+json без доп. маппинга
    assert "minio" in str(err)
    # В сообщении есть отсылка к подтверждению as-built.
    assert "minio.yaml" in str(err)


def test_np_with_app_minio_never_reaches_apply() -> None:
    """ЯВНЫЙ ЗАПРЕТ применения: `apply` не вызывается ни при каком расхождении."""
    np = with_legacy_labels(generated_np("plugin"))
    calls: list[Any] = []

    def apply(policy: Any) -> str:
        calls.append(policy)
        return "applied"

    with pytest.raises(PolicyAlignmentError):
        apply_generated_policy_guarded(np, "plugin", apply)
    assert calls == []  # политика физически не дошла до Kubernetes


def test_np_with_app_kafka_is_rejected() -> None:
    """Второй выдуманный лейбл раннего дизайна — тоже запрет."""
    np = copy.deepcopy(generated_np("plugin"))
    for rule in np["spec"]["egress"]:
        for peer in rule.get("to", []):
            labels = peer.get("podSelector", {}).get("matchLabels", {})
            if labels.get("app.kubernetes.io/name") == "agentscale-kafka":
                labels.clear()
                labels["app"] = "kafka"
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_generated_policy_matches_as_built(np, "plugin")
    assert ei.value.selector == "{app: kafka}"
    assert ei.value.reason == "forbidden_selector"


def test_unknown_selector_is_rejected_as_well() -> None:
    """Любой НЕ эталонный селектор (даже не из списка legacy) = запрет."""
    np = copy.deepcopy(generated_np("plugin"))
    np["spec"]["egress"][0]["to"][0]["podSelector"]["matchLabels"] = {"app": "something-else"}
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_generated_policy_matches_as_built(np, "plugin")
    assert ei.value.reason == "selector_mismatch"
    assert "something-else" in ei.value.selector


# ------------------------------------------------------------- ПОЛОЖИТЕЛЬНЫЕ


def test_generated_np_with_as_built_labels_is_ok() -> None:
    """NP задачи 08 с as-built лейблами проходит барьер (реальный вывод!)."""
    np = generated_np("plugin")
    assert assert_generated_policy_matches_as_built(np, "plugin") is None
    # И применяется ТОЛЬКО через защищённую точку.
    applied: list[Any] = []
    result = apply_generated_policy_guarded(np, "plugin", lambda p: applied.append(p) or "ok")
    assert result == "ok"
    assert applied == [np]


def test_generated_np_matches_live_manifest_of_task_14() -> None:
    """`manifest="auto"`: живой манифест задачи 14 тоже сверяется с эталоном."""
    np = generated_np("plugin")
    assert assert_generated_policy_matches_as_built(np, "plugin", manifest="auto") is None
    # Явная передача пути и текста тоже работают.
    assert assert_generated_policy_matches_as_built(np, "plugin", manifest=MANIFEST_PLUGIN) is None
    text = MANIFEST_PLUGIN.read_text(encoding="utf-8")
    assert assert_generated_policy_matches_as_built(np, "plugin", manifest=text) is None


def test_live_manifest_contains_all_as_built_labels() -> None:
    """Комментарии (`# app: minio`) НЕ должны ложно срабатывать как дрейф."""
    text = load_as_built_manifest_text("plugin")
    assert "app.kubernetes.io/name: agentscale-minio" in text
    assert "app.kubernetes.io/name: agentscale-kafka" in text
    # В комментариях файла есть `app: agentscale-postgres` — чистим #-строки.
    assert_generated_policy_matches_as_built(
        generated_np("plugin"), "plugin", manifest=text
    )


def test_manifest_drift_is_detected() -> None:
    """Если в k3s переименовали лейбл, а замок не обновили -> отказ."""
    text = load_as_built_manifest_text("plugin").replace(
        "app.kubernetes.io/name: agentscale-minio", "app.kubernetes.io/name: minio-renamed"
    )
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_generated_policy_matches_as_built(
            generated_np("plugin"), "plugin", manifest=text
        )
    assert ei.value.reason == "manifest_drift"
    assert "agentscale-minio" in ei.value.selector


def test_manifest_with_legacy_label_is_detected() -> None:
    """Живой манифест с `app: minio` (дизайн дословно) -> отказ."""
    text = load_as_built_manifest_text("plugin").replace(
        "app.kubernetes.io/name: agentscale-minio", "app: minio"
    )
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_generated_policy_matches_as_built(
            generated_np("plugin"), "plugin", manifest=text
        )
    assert ei.value.reason == "manifest_drift"


# ------------------------------------------------------------------ AGENT NP


def test_agent_np_has_no_5432_9000_9092() -> None:
    """Критерий задачи: agent-NP не содержит плагиновых портов."""
    np = generated_np("agent")
    assert assert_generated_policy_matches_as_built(np, "agent") is None
    ports = {
        port["port"]
        for rule in np["spec"]["egress"]
        for port in rule.get("ports", [])
    }
    for forbidden in (5432, 6432, 9000, 9092):
        assert forbidden not in ports
    assert AGENT_FORBIDDEN_PORTS & ports == set()
    # И живой эталон агента подтверждён.
    assert assert_generated_policy_matches_as_built(np, "agent", manifest="auto") is None
    assert MANIFEST_AGENT.exists()


def test_agent_np_with_plugin_port_is_rejected() -> None:
    """Если генератор «перетащил» 9000 в агента — барьер запрещает применение."""
    np = copy.deepcopy(generated_np("agent"))
    np["spec"]["egress"].append(
        {
            "to": [
                {
                    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "agentscale-dev"}},
                    "podSelector": {"matchLabels": dict(AS_BUILT_SELECTORS["minio"])},
                }
            ],
            "ports": [{"protocol": "TCP", "port": 9000}],
        }
    )
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_generated_policy_matches_as_built(np, "agent")
    assert ei.value.reason in ("selector_mismatch", "agent_port_forbidden")


def test_agent_np_uses_pod_gateway_and_kube_dns_only() -> None:
    """У агента нет plugin-gateway / MinIO / Kafka / tenant-PG селекторов."""
    np = generated_np("agent")
    selectors = [
        peer["podSelector"]["matchLabels"]
        for rule in np["spec"]["egress"]
        for peer in rule["to"]
        if "podSelector" in peer
    ]
    assert {"app": "agentscale-api"} in selectors
    assert {"k8s-app": "kube-dns"} in selectors
    assert {"app.kubernetes.io/name": "agentscale-minio"} not in selectors
    assert {"app.kubernetes.io/name": "agentscale-kafka"} not in selectors
    assert {"app": "tenant-postgres"} not in selectors


# ------------------------------------------------------- Прочие отказы (fail-closed)


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda np: np["metadata"].__setitem__("name", "renamed-policy"), "np_name_mismatch"),
        (lambda np: np["spec"].__setitem__("policyTypes", ["Ingress"]), "policy_types_incomplete"),
        (lambda np: np["spec"].__setitem__("podSelector", {"matchLabels": {"app": "agent"}}), "subject_selector_mismatch"),
        (lambda np: np["spec"]["egress"].clear(), "selector_missing"),
        (lambda np: np["spec"]["egress"].append(
            {"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}], "ports": [{"protocol": "TCP", "port": 443}]}
        ), "direct_egress_forbidden"),
        (lambda np: np["spec"]["egress"][3]["to"][0]["namespaceSelector"]["matchLabels"].__setitem__(
            "kubernetes.io/metadata.name", "agentscale"
        ), "namespace_selector_invalid"),
    ],
)
def test_any_structural_deviation_is_rejected(mutate: Any, reason: str) -> None:
    """Любая структурная неоднозначность = явный отказ, а не молчаливый успех."""
    np = copy.deepcopy(generated_np("plugin"))
    mutate(np)
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_generated_policy_matches_as_built(np, "plugin")
    assert ei.value.reason == reason


def test_prodavan_postgres_selector_is_forbidden() -> None:
    """Платформенный Postgres `prodavan` не должен попасть в egress плагинов."""
    np = copy.deepcopy(generated_np("plugin"))
    np["spec"]["egress"][3]["to"][0]["podSelector"]["matchLabels"] = {"app": "agentscale-postgres"}
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_generated_policy_matches_as_built(np, "plugin")
    assert ei.value.reason == "forbidden_selector"
    assert ei.value.selector == "{app: agentscale-postgres}"


def test_missing_or_broken_document_is_rejected() -> None:
    for broken in (None, {}, [], {"kind": "NetworkPolicy"}, "not-a-dict"):
        with pytest.raises(PolicyAlignmentError):
            assert_generated_policy_matches_as_built(broken, "plugin")


def test_unknown_scope_is_rejected() -> None:
    with pytest.raises(PolicyAlignmentError) as ei:
        assert_generated_policy_matches_as_built(generated_np("plugin"), "unknown")
    assert ei.value.reason == "scope_unknown"


def test_scope_accepts_str_and_enum() -> None:
    """Барьер не зависит от файла задачи 08: принимает и str, и EgressScope."""
    from prodavan.application.plugins.egress.generator import EgressScope

    np = generated_np("plugin")
    assert assert_generated_policy_matches_as_built(np, EgressScope.PLUGIN) is None
    assert assert_generated_policy_matches_as_built(np, "PLUGIN") is None


def test_manifest_missing_is_fail_closed() -> None:
    """Недоступный эталон = отказ, а не «проверку пропустили»."""
    from pathlib import Path

    with pytest.raises(PolicyAlignmentError) as ei:
        assert_generated_policy_matches_as_built(
            generated_np("plugin"), "plugin", manifest=Path("/definitely/missing/manifest.yaml")
        )
    assert ei.value.reason == "manifest_missing"

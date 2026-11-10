"""Статические resource-contract проверки plugin runtime (без kustomize/кластера).

Написаны в verification freeze и намеренно не запускались в этом срезе.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = REPO_ROOT / "infra" / "k3s" / "base" / "plugins"
OVERLAY = REPO_ROOT / "infra" / "k3s" / "overlays" / "dev" / "plugins"
GATEWAY = BASE / "plugin-gateway.yaml"
TEMPLATE = OVERLAY / "plugin-deployment-template.yaml"
ARGO = REPO_ROOT / "infra" / "argocd" / "apps" / "plugin-platform.yaml"


def _documents(path: Path) -> list[dict]:
    return [
        doc
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if doc
    ]


def _one(docs: list[dict], kind: str, name: str) -> dict:
    found = [
        doc
        for doc in docs
        if doc.get("kind") == kind
        and (doc.get("metadata") or {}).get("name") == name
    ]
    assert len(found) == 1, f"ожидался один {kind}/{name}, получено {len(found)}"
    return found[0]


def test_minimal_runtime_objects_are_in_base_render_not_template() -> None:
    resources = set(yaml.safe_load((BASE / "kustomization.yaml").read_text(encoding="utf-8"))["resources"])
    assert "plugin-runtime-serviceaccount.yaml" in resources
    assert "plugin-activation-state.yaml" in resources
    assert "plugin-deployment-template.yaml" not in resources

    overlay_resources = set(
        yaml.safe_load((OVERLAY / "kustomization.yaml").read_text(encoding="utf-8"))["resources"]
    )
    assert "plugin-deployment-template.yaml" not in overlay_resources

    service_account = _one(
        _documents(BASE / "plugin-runtime-serviceaccount.yaml"),
        "ServiceAccount",
        "plugin-runtime",
    )
    assert service_account["automountServiceAccountToken"] is False

    activation = _one(
        _documents(BASE / "plugin-activation-state.yaml"),
        "ConfigMap",
        "plugin-activation-state",
    )
    assert activation["data"] == {}


def test_activation_rbac_separates_named_verbs_from_create() -> None:
    role = _one(_documents(GATEWAY), "Role", "plugin-gateway-activator")
    configmap_rules = [
        rule for rule in role["rules"] if rule.get("resources") == ["configmaps"]
    ]
    named = [
        rule
        for rule in configmap_rules
        if rule.get("resourceNames") == ["plugin-activation-state"]
    ]
    assert len(named) == 1
    assert set(named[0]["verbs"]) == {"get", "update", "patch"}
    assert "create" not in named[0]["verbs"]

    # Kubernetes не поддерживает resourceNames для create: отдельное правило
    # фиксирует реальный, а не воображаемый RBAC scope.
    create = [rule for rule in configmap_rules if "create" in rule["verbs"]]
    assert len(create) == 1
    assert create[0]["verbs"] == ["create"]
    assert "resourceNames" not in create[0]

    scale = next(
        rule
        for rule in role["rules"]
        if rule.get("resources") == ["deployments/scale"]
    )
    assert set(scale["verbs"]) == {"get", "patch", "update"}
    assert "resourceNames" not in scale, "namespace-wide blocker не должен маскироваться"


def test_template_has_hardened_security_and_sigterm_contract() -> None:
    docs = _documents(TEMPLATE)
    assert [doc["kind"] for doc in docs] == ["Deployment", "Service"]
    deployment = _one(docs, "Deployment", "plugin-template")
    service = _one(docs, "Service", "plugin-template")
    assert deployment["metadata"]["name"] == service["metadata"]["name"]

    pod = deployment["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "plugin-runtime"
    assert pod["automountServiceAccountToken"] is False
    assert pod["terminationGracePeriodSeconds"] == 30
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 10001,
        "runAsGroup": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }

    container = pod["containers"][0]
    security = container["securityContext"]
    assert security["allowPrivilegeEscalation"] is False
    assert security["readOnlyRootFilesystem"] is True
    assert security["capabilities"]["drop"] == ["ALL"]
    assert "lifecycle" not in container
    assert "preStop" not in yaml.safe_dump(container)

    assert {"name": "tmp", "emptyDir": {}} in pod["volumes"]
    assert {"name": "tmp", "mountPath": "/tmp"} in container["volumeMounts"]
    assert service["spec"]["ports"][0]["port"] == 8080


def test_startup_budget_equals_gateway_readiness_timeout() -> None:
    deployment = _one(_documents(TEMPLATE), "Deployment", "plugin-template")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    startup = container["startupProbe"]
    startup_budget = startup["periodSeconds"] * startup["failureThreshold"]
    assert startup_budget == 300
    assert startup["httpGet"] == {"path": "/health", "port": "http"}

    config = _one(_documents(GATEWAY), "ConfigMap", "plugin-gateway-config")
    assert int(config["data"]["PLUGIN_READY_TIMEOUT_SECONDS"]) == startup_budget
    assert config["data"]["PLUGIN_RUNTIME_DEPLOYMENT_PREFIX"] == "plugin"
    assert config["data"]["PLUGIN_PLATFORM_ENABLED"] == "false"


def test_argo_ignores_only_activation_data_not_replicas_or_git_config() -> None:
    app = _documents(ARGO)[0]
    ignored = app["spec"].get("ignoreDifferences") or []
    for rule in ignored:
        pointers = set(rule.get("jsonPointers") or [])
        jq = set(rule.get("jqPathExpressions") or [])
        assert not (
            rule.get("kind") == "Deployment"
            and ("/spec/replicas" in pointers or ".spec.replicas" in jq)
        ), "широкий replicas ignore отключил бы Git-managed always-on workloads"
        if rule.get("name") in {
            "tenant-postgres-config",
            "pgbouncer-credentials",
            "egress-proxy-config",
        }:
            assert "/data" not in pointers

    assert ignored == [
        {
            "group": "",
            "kind": "ConfigMap",
            "name": "plugin-activation-state",
            "jsonPointers": ["/data"],
        }
    ]

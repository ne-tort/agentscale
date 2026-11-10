"""Static least-privilege contracts for plugin lifecycle and registry pull config.

Offline only: source YAML is parsed directly; no kustomize, cluster, network,
subprocess, Docker, kubectl, or Git command is used. Written under the migration
verification freeze and intentionally not executed in this slice.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = REPO_ROOT / "infra" / "k3s" / "base" / "plugins"
OVERLAY = REPO_ROOT / "infra" / "k3s" / "overlays" / "dev" / "plugins"
LIFECYCLE = OVERLAY / "plugin-lifecycle-rbac.yaml"
TOKEN_REQUEST = OVERLAY / "plugin-deployment-admin-token-request-rbac.yaml"
SEALED_EXAMPLE = OVERLAY / "plugin-ghcr-pull.sealed.yaml.example"
KUSTOMIZATION = OVERLAY / "kustomization.yaml"
NAMESPACE = "agentscale-dev"
LIFECYCLE_NAME = "plugin-deployment-admin"
TOKEN_ROLE_NAME = "plugin-deployment-admin-token-request"


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
    assert len(found) == 1, f"expected exactly one {kind}/{name}"
    return found[0]


def _assert_no_wildcards(role: dict) -> None:
    for rule in role["rules"]:
        assert "*" not in rule.get("apiGroups", [])
        assert "*" not in rule.get("resources", [])
        assert "*" not in rule.get("verbs", [])
        assert not str(rule.get("resourceNames", "")).startswith("admin.")


def test_lifecycle_rbac_is_exact_namespaced_and_not_shared() -> None:
    docs = _documents(LIFECYCLE)
    assert {doc["kind"] for doc in docs} == {
        "ServiceAccount", "Role", "RoleBinding"
    }
    assert all(doc["metadata"]["namespace"] == NAMESPACE for doc in docs)

    service_account = _one(docs, "ServiceAccount", LIFECYCLE_NAME)
    assert service_account["automountServiceAccountToken"] is False
    assert service_account["metadata"]["name"] not in {
        "plugin-gateway", "plugin-runtime", "agentscale-api", "agentscale-project-pod"
    }

    role = _one(docs, "Role", LIFECYCLE_NAME)
    assert role["rules"] == [
        {
            "apiGroups": ["apps"],
            "resources": ["deployments"],
            "verbs": ["get", "create", "patch", "delete"],
        },
        {
            "apiGroups": ["apps"],
            "resources": ["deployments/scale"],
            "verbs": ["get", "patch"],
        },
        {
            "apiGroups": [""],
            "resources": ["services"],
            "verbs": ["get", "create", "patch", "delete"],
        },
        {
            "apiGroups": [""],
            "resources": ["services/proxy"],
            "verbs": ["get", "create"],
        },
        {
            "apiGroups": [""],
            "resources": ["secrets"],
            "verbs": ["get", "create", "patch", "delete"],
        },
        {
            "apiGroups": [""],
            "resources": ["configmaps"],
            "resourceNames": ["plugin-activation-state"],
            "verbs": ["get", "patch"],
        },
        {
            "apiGroups": [""],
            "resources": ["configmaps"],
            "verbs": ["create"],
        },
        {
            "apiGroups": [""],
            "resources": ["pods"],
            "verbs": ["list"],
        },
    ]
    _assert_no_wildcards(role)
    named_rules = [rule for rule in role["rules"] if "resourceNames" in rule]
    assert named_rules == [
        {
            "apiGroups": [""],
            "resources": ["configmaps"],
            "resourceNames": ["plugin-activation-state"],
            "verbs": ["get", "patch"],
        }
    ]
    configmap_create = next(
        rule
        for rule in role["rules"]
        if rule["resources"] == ["configmaps"] and rule["verbs"] == ["create"]
    )
    assert "resourceNames" not in configmap_create
    secret_rule = next(
        rule for rule in role["rules"] if rule["resources"] == ["secrets"]
    )
    assert "create" in secret_rule["verbs"]
    assert "resourceNames" not in secret_rule

    binding = _one(docs, "RoleBinding", LIFECYCLE_NAME)
    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Role",
        "name": LIFECYCLE_NAME,
    }
    assert binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": LIFECYCLE_NAME,
            "namespace": NAMESPACE,
        }
    ]


def test_existing_activator_rules_are_preserved_without_dev_expansion() -> None:
    base_role = _one(
        _documents(BASE / "plugin-gateway.yaml"),
        "Role",
        "plugin-gateway-activator",
    )
    scale = next(
        rule for rule in base_role["rules"]
        if rule["resources"] == ["deployments/scale"]
    )
    assert scale["verbs"] == ["get", "patch", "update"]
    deployments = next(
        rule for rule in base_role["rules"]
        if rule["resources"] == ["deployments"]
    )
    assert deployments["verbs"] == ["get", "list", "watch"]
    named_cas = next(
        rule for rule in base_role["rules"]
        if rule.get("resourceNames") == ["plugin-activation-state"]
    )
    assert named_cas["resources"] == ["configmaps"]
    assert named_cas["verbs"] == ["get", "update", "patch"]
    create_cas = next(
        rule for rule in base_role["rules"]
        if rule["resources"] == ["configmaps"] and "create" in rule["verbs"]
    )
    assert create_cas["verbs"] == ["create"]
    assert "resourceNames" not in create_cas

    # Pre-existing controller permission is intentionally retained in this task.
    # The dev overlay must not add any new gateway/backend Secret permissions.
    existing_secret = next(
        rule for rule in base_role["rules"]
        if rule.get("resourceNames") == ["pgbouncer-credentials"]
    )
    assert existing_secret == {
        "apiGroups": [""],
        "resources": ["secrets"],
        "resourceNames": ["pgbouncer-credentials"],
        "verbs": ["get", "patch", "update"],
    }
    for manifest in (LIFECYCLE, TOKEN_REQUEST):
        assert all(
            doc.get("kind") != "Role"
            or (doc.get("metadata") or {}).get("name") != "plugin-gateway-activator"
            for doc in _documents(manifest)
        )


def test_main_and_gateway_have_only_single_sa_token_request_permission() -> None:
    docs = _documents(TOKEN_REQUEST)
    assert {doc["kind"] for doc in docs} == {"Role", "RoleBinding"}
    assert all(doc["metadata"]["namespace"] == NAMESPACE for doc in docs)

    role = _one(docs, "Role", TOKEN_ROLE_NAME)
    assert role["rules"] == [
        {
            "apiGroups": [""],
            "resources": ["serviceaccounts/token"],
            "resourceNames": [LIFECYCLE_NAME],
            "verbs": ["create"],
        }
    ]
    _assert_no_wildcards(role)

    binding = _one(docs, "RoleBinding", TOKEN_ROLE_NAME)
    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Role",
        "name": TOKEN_ROLE_NAME,
    }
    assert binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "agentscale-api",
            "namespace": NAMESPACE,
        },
        {
            "kind": "ServiceAccount",
            "name": "plugin-gateway",
            "namespace": NAMESPACE,
        },
    ]

    main_deployment = _one(
        _documents(REPO_ROOT / "infra/k3s/base/agentscale-api/deployment.yaml"),
        "Deployment",
        "agentscale-api",
    )
    gateway_deployment = _one(
        _documents(BASE / "plugin-gateway.yaml"),
        "Deployment",
        "plugin-gateway",
    )
    main_pod = main_deployment["spec"]["template"]["spec"]
    gateway_pod = gateway_deployment["spec"]["template"]["spec"]
    assert main_pod["serviceAccountName"] == "agentscale-api"
    assert gateway_pod["serviceAccountName"] == "plugin-gateway"
    assert any(
        "prodavan.main_plugin_gateway:app" in container.get("command", [])
        for container in gateway_pod["containers"]
    )

    allowed_names = {subject["name"] for subject in binding["subjects"]}
    assert {
        "plugin-runtime",
        "agentscale-project-pod",
        "plugin-deployment-admin",
        "unrelated-service-account",
    }.isdisjoint(allowed_names)


def test_overlay_wires_rbac_but_never_applies_the_sealed_example() -> None:
    kustomization = yaml.safe_load(KUSTOMIZATION.read_text(encoding="utf-8"))
    assert "plugin-lifecycle-rbac.yaml" in kustomization["resources"]
    assert "plugin-deployment-admin-token-request-rbac.yaml" in kustomization["resources"]
    assert all(
        "plugin-gateway-activator" not in str(patch)
        for patch in kustomization.get("patches", [])
    )
    assert all(
        not str(resource).endswith(".example")
        for resource in kustomization["resources"]
    )


def test_ghcr_example_contains_only_sealed_placeholder_and_required_type() -> None:
    documents = _documents(SEALED_EXAMPLE)
    assert len(documents) == 1
    sealed = documents[0]
    assert sealed["kind"] == "SealedSecret"
    assert sealed["metadata"] == {
        "name": "plugin-ghcr-pull", "namespace": NAMESPACE
    }
    assert "data" not in sealed
    assert "stringData" not in sealed
    assert sealed["spec"]["encryptedData"] == {
        ".dockerconfigjson": "SEALED_CIPHERTEXT_REQUIRED"
    }
    template = sealed["spec"]["template"]
    assert template["metadata"] == {
        "name": "plugin-ghcr-pull", "namespace": NAMESPACE
    }
    assert template["type"] == "kubernetes.io/dockerconfigjson"
    assert "data" not in template
    assert "stringData" not in template


def test_deployment_template_has_only_the_managed_pull_secret_name() -> None:
    deployment = _one(
        _documents(OVERLAY / "plugin-deployment-template.yaml"),
        "Deployment",
        "plugin-template",
    )
    pod = deployment["spec"]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": "plugin-ghcr-pull"}]
    assert set(pod["imagePullSecrets"][0]) == {"name"}
    assert pod["automountServiceAccountToken"] is False
    assert pod["serviceAccountName"] == "plugin-runtime"

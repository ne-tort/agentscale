"""Статические regression-контракты cluster-internal Service plugin-static (Scope B1).

Offline-аудит исходных YAML: без kustomize, кластера, сети, subprocess, Docker,
kubectl и Git. Контур B1 — CI evidence, НЕ публичный browser static hosting:

* cluster-internal Service plugin-static:8081 в agentscale-dev, БЕЗ Ingress;
* Deployment prodavan.main_plugin_static:app, SA без token/RBAC, non-root,
  readOnlyRootFilesystem, явные probes/resources;
* GitHub App registry_read auth ТОЛЬКО через k8s-secret:// REF смонтированного
  SealedSecret'а — никаких inline-ключей/PAT/литералов секретов;
* egress NP: kube-dns + внешний TCP 443 (api.github.com/ghcr.io, hostname-pinning
  в приложении), БЕЗ tenant DB/MinIO/внутренних сервисов;
* prod-оверлей и Ingress-объекты плагинов к plugin-static не подключены;
* dev-only JSON6902 patch допускает CI evidence HEAD с self-hosted runner VM
  172.31.156.203/32 на TCP 8081, НЕ заменяя base-правила ingress.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = REPO_ROOT / "infra" / "k3s" / "base" / "plugins"
OVERLAY = REPO_ROOT / "infra" / "k3s" / "overlays" / "dev" / "plugins"
PROD = REPO_ROOT / "infra" / "k3s" / "overlays" / "prod"
ARGO_PROJECT = REPO_ROOT / "infra" / "argocd" / "apps" / "00-project.yaml"
MANIFEST = BASE / "plugin-static.yaml"
RUNNER_PATCH = OVERLAY / "patch-plugin-static-runner-ingress.yaml"
SEALED_EXAMPLE = OVERLAY / "plugin-module-source-github-app.sealed.yaml.example"
PRIVATE_KEY_REF = "k8s-secret://plugin-module-source-github-app/private-key"
EXPECTED_KINDS = {
    "ServiceAccount",
    "ConfigMap",
    "Deployment",
    "Service",
    "NetworkPolicy",
}


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
    assert len(found) == 1, "ожидался ровно один " + kind + "/" + name
    return found[0]


def _static_docs() -> list[dict]:
    return _documents(MANIFEST)


def test_object_set_is_minimal_and_has_no_rbac_ingress_or_secret() -> None:
    docs = _static_docs()
    kinds = {doc["kind"] for doc in docs}
    assert kinds == EXPECTED_KINDS
    forbidden = {
        "Ingress",
        "Role",
        "RoleBinding",
        "ClusterRole",
        "ClusterRoleBinding",
        "Secret",
        "IngressRoute",
    }
    assert not kinds & forbidden
    assert len(docs) == len(EXPECTED_KINDS)


def test_service_account_disables_token_and_deployment_uses_it() -> None:
    docs = _static_docs()
    account = _one(docs, "ServiceAccount", "plugin-static")
    assert account["automountServiceAccountToken"] is False

    pod = _one(docs, "Deployment", "plugin-static")["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "plugin-static"
    assert pod["automountServiceAccountToken"] is False
    assert "rbac.authorization.k8s.io" not in MANIFEST.read_text(encoding="utf-8")


def test_deployment_runs_static_entrypoint_with_platform_pull_secret() -> None:
    docs = _static_docs()
    deployment = _one(docs, "Deployment", "plugin-static")
    pod = deployment["spec"]["template"]["spec"]
    assert deployment["spec"]["selector"]["matchLabels"] == {"app": "plugin-static"}
    assert pod["imagePullSecrets"] == [{"name": "ghcr-pull"}]

    container = pod["containers"][0]
    assert container["name"] == "static"
    assert container["image"] == "ghcr.io/ne-tort/agentscale-api:latest"
    assert container["imagePullPolicy"] == "Always"
    assert container["command"] == [
        "uvicorn",
        "prodavan.main_plugin_static:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8081",
    ]
    assert container["ports"] == [{"name": "http", "containerPort": 8081}]
    assert pod["terminationGracePeriodSeconds"] == 30


def test_deployment_pod_and_container_security_context() -> None:
    docs = _static_docs()
    pod = _one(docs, "Deployment", "plugin-static")["spec"]["template"]["spec"]
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 10001,
        "runAsGroup": 10001,
        "fsGroup": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }

    container = pod["containers"][0]
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["tmp"] == {"name": "tmp", "emptyDir": {}}
    assert {"name": "tmp", "mountPath": "/tmp"} in container["volumeMounts"]


def test_probes_split_live_from_digest_verifying_readiness() -> None:
    docs = _static_docs()
    container = _one(docs, "Deployment", "plugin-static")["spec"]["template"][
        "spec"
    ]["containers"][0]

    startup = container["startupProbe"]
    assert startup["httpGet"] == {"path": "/health/live", "port": "http"}
    assert startup["periodSeconds"] * startup["failureThreshold"] == 300

    readiness = container["readinessProbe"]
    # /health/ready — configured-gate (200 только при composed loader+policy);
    # реальная digest-верификация OCI manifest + frontend tar.gz (B1) живёт на
    # evidence-маршруте /internal/plugin-static/v1/bundles/{...}/{digest}/ready,
    # поэтому сама проба НЕ ходит в GHCR. Тест пинит путь и тайминги.
    assert readiness["httpGet"] == {"path": "/health/ready", "port": "http"}
    assert readiness["timeoutSeconds"] == 10
    assert readiness["periodSeconds"] == 30

    liveness = container["livenessProbe"]
    assert liveness["httpGet"] == {"path": "/health/live", "port": "http"}


def test_resources_are_explicit_and_within_limit_range() -> None:
    container = _one(_static_docs(), "Deployment", "plugin-static")[
        "spec"
    ]["template"]["spec"]["containers"][0]
    resources = container["resources"]
    assert resources["requests"] == {"cpu": "50m", "memory": "128Mi"}
    assert resources["limits"] == {"cpu": "500m", "memory": "512Mi"}


def test_github_app_auth_is_mounted_secret_ref_only() -> None:
    docs = _static_docs()
    pod = _one(docs, "Deployment", "plugin-static")["spec"]["template"]["spec"]
    container = pod["containers"][0]

    # envFrom — ТОЛЬКО configMap; никаких secretRef с литералами окружения.
    assert container["envFrom"] == [
        {"configMapRef": {"name": "plugin-static-config"}}
    ]
    # Единственный env — REF на смонтированный ключ, не сам ключ.
    assert container["env"] == [
        {
            "name": "PLUGIN_MODULE_SOURCE_GITHUB_APP_PRIVATE_KEY_SECRET_REF",
            "value": PRIVATE_KEY_REF,
        }
    ]

    mounts = {m["name"]: m for m in container["volumeMounts"]}
    key_mount = mounts["github-app-private-key"]
    assert key_mount["mountPath"] == "/var/run/secrets/plugin-module-source-github-app"
    assert key_mount["readOnly"] is True

    volumes = {v["name"]: v for v in pod["volumes"]}
    secret_volume = volumes["github-app-private-key"]["secret"]
    assert secret_volume["secretName"] == "plugin-module-source-github-app"
    assert secret_volume["defaultMode"] in (0o440, "0440")
    assert secret_volume["optional"] is True
    assert secret_volume["items"] == [
        {"key": "private-key", "path": "private-key"}
    ]

    # Сам Secret в Git отсутствует: только SealedSecret-пример с плейсхолдером.
    assert not [doc for doc in docs if doc.get("kind") == "Secret"]


def test_manifest_contains_no_secret_literals_or_tenant_credentials() -> None:
    text = MANIFEST.read_text(encoding="utf-8")
    for forbidden in (
        "TENANT_DB",
        "PLUGIN_MINIO",
        "postgresql://",
        "postgres://",
        "mysql://",
        "agentscale-api-secrets",
        "agentscale-b2b-secrets",
        "plugin-gateway-secrets",
        "plugin-instance-secrets",
        "pgbouncer-credentials",
        "plugin-ghcr-pull",
        "change-me",
        "GITHUB_TOKEN",
        "AKIA",
        "ghp_",
    ):
        assert forbidden not in text, "запрещённый литерал: " + forbidden
    assert re.search(r"BEGIN [A-Z ]*PRIVATE", text) is None

    config = _one(_static_docs(), "ConfigMap", "plugin-static-config")
    for key in config["data"]:
        assert "SECRET" not in key or key.endswith("_SECRET_REF")


def test_configmap_keeps_contour_fail_closed_and_digest_pinned() -> None:
    config = _one(_static_docs(), "ConfigMap", "plugin-static-config")
    data = config["data"]
    assert data["PLUGIN_PLATFORM_ENABLED"] == "false"
    # До заполнения GitHub App установки контур fail-closed: включение —
    # отдельный шаг вместе с запечатанным ключом и id установки.
    assert data["PLUGIN_MODULE_SOURCE_ENABLED"] == "false"
    assert data["PLUGIN_MODULE_SOURCE_GITHUB_APP_ORGANIZATION"] == "ne-tort"
    assert data["PLUGIN_OCI_REGISTRY"] == "ghcr.io/ne-tort/plugins"
    assert data["PLUGIN_ARTIFACT_PIN_MODE"] == "digest"
    # Server-owned CSP frame-ancestors для evidence-ответов StaticResponsePolicy:
    # exact https dev shell origin, публичное не-секретное значение.
    assert data["PLUGIN_SHELL_ORIGIN"] == "https://agentscale.dev"
    assert set(data) == {
        "PLUGIN_PLATFORM_ENABLED",
        "PLUGIN_MODULE_SOURCE_ENABLED",
        "PLUGIN_MODULE_SOURCE_GITHUB_APP_ORGANIZATION",
        "PLUGIN_OCI_REGISTRY",
        "PLUGIN_ARTIFACT_PIN_MODE",
        "PLUGIN_SHELL_ORIGIN",
    }


def test_service_is_cluster_internal_port_8081() -> None:
    service = _one(_static_docs(), "Service", "plugin-static")
    spec = service["spec"]
    assert spec.get("type", "ClusterIP") == "ClusterIP"
    assert "externalTrafficPolicy" not in spec
    assert "loadBalancerIP" not in spec
    assert spec["selector"] == {"app": "plugin-static"}
    assert spec["ports"] == [{"name": "http", "port": 8081, "targetPort": "http"}]


def _normalized_ingress(policy: dict) -> set:
    flows = set()
    for rule in policy["spec"]["ingress"]:
        for peer in rule["from"]:
            namespace_name = peer["namespaceSelector"]["matchLabels"][
                "kubernetes.io/metadata.name"
            ]
            pod_labels = tuple(sorted(peer["podSelector"]["matchLabels"].items()))
            for port in rule["ports"]:
                flows.add((namespace_name, pod_labels, port["protocol"], port["port"]))
    return flows


def _normalized_egress(policy: dict) -> set:
    flows = set()
    for rule in policy["spec"]["egress"]:
        for peer in rule["to"]:
            assert set(peer) in ({"namespaceSelector", "podSelector"}, {"ipBlock"})
            if "ipBlock" in peer:
                block = peer["ipBlock"]
                destination = (
                    "ipBlock",
                    block["cidr"],
                    tuple(sorted(block.get("except") or [])),
                )
            else:
                destination = (
                    "namespace",
                    peer["namespaceSelector"]["matchLabels"][
                        "kubernetes.io/metadata.name"
                    ],
                    tuple(sorted(peer["podSelector"]["matchLabels"].items())),
                )
            for port in rule["ports"]:
                assert isinstance(port["port"], int)
                flows.add((*destination, port["protocol"], port["port"]))
    return flows


def test_network_policy_scope_is_static_pods_only() -> None:
    policy = _one(_static_docs(), "NetworkPolicy", "plugin-static-egress")
    spec = policy["spec"]
    assert spec["podSelector"] == {"matchLabels": {"app": "plugin-static"}}
    assert spec["policyTypes"] == ["Ingress", "Egress"]


def test_network_policy_ingress_is_internal_callers_only() -> None:
    policy = _one(_static_docs(), "NetworkPolicy", "plugin-static-egress")
    for rule in policy["spec"]["ingress"]:
        for peer in rule["from"]:
            # Base NP остаётся без ipBlock: VM-runner допускается ТОЛЬКО
            # dev-only overlay patch'ем, не публичным/world расширением base.
            assert "ipBlock" not in peer
    assert _normalized_ingress(policy) == {
        (
            "agentscale-dev",
            (("app", "agentscale-api"),),
            "TCP",
            8081,
        ),
        (
            "agentscale-dev",
            (("app", "plugin-gateway"),),
            "TCP",
            8081,
        ),
    }


def test_network_policy_egress_allows_only_dns_and_external_https() -> None:
    policy = _one(_static_docs(), "NetworkPolicy", "plugin-static-egress")
    flows = _normalized_egress(policy)

    dns = {
        ("namespace", "kube-system", (("k8s-app", "kube-dns"),), "UDP", 53),
        ("namespace", "kube-system", (("k8s-app", "kube-dns"),), "TCP", 53),
    }
    # api.github.com + ghcr.io: NP не матчит DNS-имена, hostname-pinning в
    # приложении (GitHubAppClient / allowed_registries=("ghcr.io",)); NP даёт
    # только TCP 443 наружу с отсечёнными pod/service CIDR k3s.
    external = {
        ("ipBlock", "0.0.0.0/0", ("10.42.0.0/16", "10.43.0.0/16"), "TCP", 443),
    }
    assert dns <= flows, "потерян обязательный UDP/TCP egress к kube-dns:53"
    assert external <= flows, "потерян обязательный внешний TCP 443"
    assert flows == dns | external, (
        "лишние egress-потоки в plugin-static-egress: "
        + str(sorted(flows - (dns | external)))
    )
    ports = {flow[-2:] for flow in flows}
    for forbidden in {
        ("TCP", 80),
        ("TCP", 5432),
        ("TCP", 6432),
        ("TCP", 9000),
        ("TCP", 9092),
    }:
        assert forbidden not in ports


def test_base_kustomization_wires_manifest_and_overlay_stays_clean() -> None:
    base = yaml.safe_load((BASE / "kustomization.yaml").read_text(encoding="utf-8"))
    assert "plugin-static.yaml" in base["resources"]

    overlay = yaml.safe_load(
        (OVERLAY / "kustomization.yaml").read_text(encoding="utf-8")
    )
    resources = [str(item) for item in overlay["resources"]]
    assert "../../../base/plugins" in resources
    # SealedSecret-примеры никогда не применяются kustomization'ом.
    assert all(not item.endswith(".example") for item in resources)
    # Plugin-static не заводит новых оверлейных РЕСУРСОВ (namespace
    # подставляет transformer), а из patch'ей разрешён РОВНО ОДИН — dev-only
    # runner ingress к NP (CI evidence с self-hosted runner VM).
    assert all("plugin-static" not in item for item in resources)
    static_patches = [
        patch
        for patch in overlay.get("patches", [])
        if "plugin-static" in str(patch)
    ]
    assert len(static_patches) == 1
    runner_patch = static_patches[0]
    assert runner_patch["path"] == "patch-plugin-static-runner-ingress.yaml"
    assert runner_patch["target"] == {
        "group": "networking.k8s.io",
        "version": "v1",
        "kind": "NetworkPolicy",
        "name": "plugin-static-egress",
    }


def test_dev_patch_appends_exact_runner_vm_peer() -> None:
    # CI evidence HEAD идёт с self-hosted runner на VM, а не из подов:
    # dev-only JSON6902 patch ДОБАВЛЯЕТ peer, не заменяя base ingress-список
    # (у NetworkPolicy spec.ingress нет merge-ключа — strategic merge затирает).
    operations = yaml.safe_load(RUNNER_PATCH.read_text(encoding="utf-8"))
    assert isinstance(operations, list) and len(operations) == 1
    operation = operations[0]
    assert operation["op"] == "add"
    assert operation["path"] == "/spec/ingress/-"

    rule = operation["value"]
    assert set(rule) == {"from", "ports"}
    peers = rule["from"]
    assert len(peers) == 1
    peer = peers[0]
    # Ровно один хост — сама VM self-hosted CI runner'а, /32.
    assert set(peer) == {"ipBlock"}
    assert peer["ipBlock"] == {"cidr": "172.31.156.203/32"}
    assert rule["ports"] == [{"protocol": "TCP", "port": 8081}]

    text = RUNNER_PATCH.read_text(encoding="utf-8")
    for forbidden in (
        "0.0.0.0/0",
        "LoadBalancer",
        "NodePort",
        "namespaceSelector",
        "podSelector",
        "kind: Ingress",
        "IngressRoute",
    ):
        assert forbidden not in text, "запрещённый элемент dev patch: " + forbidden


def test_no_ingress_routes_to_plugin_static() -> None:
    docs = _documents(OVERLAY / "ingress-plugins.yaml")
    for doc in docs:
        if doc.get("kind") in {"Ingress", "IngressRoute"}:
            rendered = yaml.safe_dump(doc)
            assert "plugin-static" not in rendered
    assert not [
        doc for doc in _static_docs() if doc.get("kind") in {"Ingress", "IngressRoute"}
    ]


def test_prod_overlay_never_references_plugin_static() -> None:
    for path in sorted(PROD.rglob("*.y*ml")):
        assert "plugin-static" not in path.read_text(encoding="utf-8"), (
            "prod-оверлей не должен подключать plugin-static: " + str(path)
        )


def test_sealed_example_is_placeholder_only() -> None:
    documents = _documents(SEALED_EXAMPLE)
    assert len(documents) == 1
    sealed = documents[0]
    assert sealed["apiVersion"] == "bitnami.com/v1alpha1"
    assert sealed["kind"] == "SealedSecret"
    assert sealed["metadata"] == {
        "name": "plugin-module-source-github-app",
        "namespace": "agentscale-dev",
    }
    assert "data" not in sealed and "stringData" not in sealed
    assert sealed["spec"]["encryptedData"] == {
        "private-key": "SEALED_CIPHERTEXT_REQUIRED"
    }
    template = sealed["spec"]["template"]
    assert template["metadata"] == sealed["metadata"]
    assert template["type"] == "Opaque"
    assert "data" not in template and "stringData" not in template


def test_new_objects_fit_argo_project_namespace_whitelist() -> None:
    project = yaml.safe_load(ARGO_PROJECT.read_text(encoding="utf-8"))
    whitelist = {
        (entry["group"], entry["kind"])
        for entry in project["spec"]["namespaceResourceWhitelist"]
    }
    for doc in _static_docs():
        api_version = str(doc["apiVersion"])
        group = api_version.split("/")[0] if "/" in api_version else ""
        assert (group, doc["kind"]) in whitelist, (
            doc["kind"] + " отсутствует в namespaceResourceWhitelist"
        )

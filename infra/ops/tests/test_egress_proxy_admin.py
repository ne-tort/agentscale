"""Пины двух последних дефектов dev-контура (Ready, 2026-11-08).

1. egress-proxy 0/1 Running ~140 мин: readiness `GET http://<PodIP>:9901/ready`
   падал «connection refused», т.к. `admin.address...address: 127.0.0.1` в
   ConfigMap `egress-proxy-config` и в эталоне образа. Kubelet пробует PodIP
   С УЗЛА, loopback пода ему недоступен. Лечение — `0.0.0.0:9901`. Порт :9901
   НЕ экспортируется Service (только :8080) и закрыт NetworkPolicy, поэтому
   это не ослабляет fail-closed (`clusters: []` + `direct_response 403`).

2. Ingress `plugins-gateway` отсутствует в кластере, Argo `plugin-platform`
   показывает OutOfSync/Missing. Манифест КОРРЕКТЕН: локальный
   `kubectl kustomize` (БЕЗ кластера) рендерит Ingress с
   `router.priority: "120"`, `traefik`, websecure, TLS без secretName
   (default TLSStore), kind Ingress разрешён whitelist'ом 00-project.yaml,
   дубля имени нет. Корень: Ingress был в wave 25, а App оставался Missing
   (Ingress нет в live) — более поздняя волна не применялась до здоровья
   предыдущих, получался замкнутый круг. Решение: wave 1, одной волной со
   всеми ресурсами plugin-platform; приоритет 120 задан аннотацией Traefik,
   а не волной.

Тесты читают YAML/рендер и НЕ обращаются к кластеру, kubectl apply, argocd.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_PLUGINS = REPO_ROOT / "infra" / "k3s" / "base" / "plugins"
DEV_PLUGINS_OVERLAY = REPO_ROOT / "infra" / "k3s" / "overlays" / "dev" / "plugins"
IMAGE_ENVOY = REPO_ROOT / "infra" / "images" / "plugins" / "egress-proxy" / "envoy.yaml"
ARGO_PROJECT = REPO_ROOT / "infra" / "argocd" / "apps" / "00-project.yaml"
PLUGIN_PLATFORM = REPO_ROOT / "infra" / "argocd" / "apps" / "plugin-platform.yaml"

EGRESS_MANIFEST = BASE_PLUGINS / "egress-proxy.yaml"
PLUGINS_INGRESS = DEV_PLUGINS_OVERLAY / "ingress-plugins.yaml"
PLUGINS_KUSTOMIZATION = DEV_PLUGINS_OVERLAY / "kustomization.yaml"

ADMIN_BIND_ADDRESS = "0.0.0.0"
ADMIN_PORT = 9901
EXPECTED_PRIORITY = "120"


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _admin_socket(config: dict) -> dict:
    return config["admin"]["address"]["socket_address"]


def _assert_fail_closed(config: dict) -> None:
    """Стартовый конфиг остался deny-all: ни одного upstream-кластера + 403."""
    assert config["static_resources"]["clusters"] == []
    vhosts = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]["route_config"]["virtual_hosts"]
    assert vhosts[-1]["routes"][0]["direct_response"]["status"] == 403


# ---------------------------------------------------------------------------
# 1. egress-proxy: admin биндится на PodIP (0.0.0.0), а не loopback пода
# ---------------------------------------------------------------------------


def test_configmap_envoy_admin_binds_0_0_0_0() -> None:
    """Ключ `envoy.yaml` ConfigMap'а — то, что монтируется в /etc/envoy."""
    cm = next(d for d in _docs(EGRESS_MANIFEST) if d.get("kind") == "ConfigMap")
    assert cm["metadata"]["name"] == "egress-proxy-config"
    raw = cm["data"]["envoy.yaml"]
    config = yaml.safe_load(raw)
    socket = _admin_socket(config)
    assert socket["address"] == ADMIN_BIND_ADDRESS, (
        "admin :9901 снова на 127.0.0.1: kubelet GET <PodIP>:9901/ready с узла "
        "получит connection refused → под вечно 0/1 Running"
    )
    assert socket["port_value"] == ADMIN_PORT
    _assert_fail_closed(config)
    # Статический хост из ошибки v1/v3 не должен появиться в ДАННЫХ конфига.
    # В комментариях манифеста он упомянут как запрет — это нормально, поэтому
    # проверяем распарсенный конфиг, а не сырой текст ключа.
    assert "webhooks.example.com" not in str(config)


def test_deployment_readiness_probe_matches_admin_port() -> None:
    """readinessProbe ходит на :9901/ready — именно туда, где слушает admin."""
    dep = next(d for d in _docs(EGRESS_MANIFEST) if d.get("kind") == "Deployment")
    assert dep["metadata"]["name"] == "egress-proxy"
    probe = dep["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]["httpGet"]
    assert probe["port"] == ADMIN_PORT
    assert probe["path"] == "/ready"
    # Конфиг монтируется КАТАЛОГОМ без subPath, ключ совпадает с -c аргументом.
    container = dep["spec"]["template"]["spec"]["containers"][0]
    assert container["command"][:3] == ["envoy", "-c", "/etc/envoy/envoy.yaml"]
    mounts = [m for m in container["volumeMounts"] if m["name"] == "config"]
    assert mounts and "subPath" not in mounts[0] and mounts[0]["mountPath"] == "/etc/envoy"


def test_image_reference_envoy_admin_binds_0_0_0_0() -> None:
    """Эталон образа (используется до первой генерации задачей 08)."""
    config = yaml.safe_load(IMAGE_ENVOY.read_text(encoding="utf-8"))
    assert _admin_socket(config)["address"] == ADMIN_BIND_ADDRESS
    assert _admin_socket(config)["port_value"] == ADMIN_PORT
    _assert_fail_closed(config)


def test_service_does_not_expose_admin_port() -> None:
    """0.0.0.0:9901 безопасно: Service экспортирует ТОЛЬКО :8080."""
    svc = next(d for d in _docs(EGRESS_MANIFEST) if d.get("kind") == "Service")
    ports = {p["port"] for p in svc["spec"]["ports"]}
    assert ports == {8080}, "admin :9901 не должен быть доступен через Service"


def test_networkpolicy_does_not_open_admin_port() -> None:
    """NP плагинов разрешает egress-proxy только :8080 (не :9901)."""
    np = yaml.safe_load(
        (BASE_PLUGINS / "networkpolicy-plugin.yaml").read_text(encoding="utf-8")
    )
    proxy_ports = {
        p["port"]
        for rule in np["spec"]["egress"]
        for peer in rule.get("to", [])
        if peer.get("podSelector", {}).get("matchLabels", {}).get("app") == "egress-proxy"
        for p in rule.get("ports", [])
    }
    assert proxy_ports == {8080}
    assert ADMIN_PORT not in proxy_ports


# ---------------------------------------------------------------------------
# 2. Ingress plugins-gateway: рендерится, whitelist ок, sync НЕ делаем
# ---------------------------------------------------------------------------


def test_plugins_ingress_is_in_overlay_resources() -> None:
    """Иначе Argo не увидит объект в desired state (OutOfSync навсегда)."""
    kustomization = PLUGINS_KUSTOMIZATION.read_text(encoding="utf-8")
    assert "- ingress-plugins.yaml" in kustomization
    parsed = yaml.safe_load(kustomization)
    assert "ingress-plugins.yaml" in parsed["resources"]


def test_project_whitelist_allows_ingress() -> None:
    """kind Ingress разрешён AppProject `agentscale` (синк не упадёт целиком)."""
    project = next(d for d in _docs(ARGO_PROJECT) if d.get("kind") == "AppProject")
    allowed = {
        (e["group"], e["kind"]) for e in project["spec"]["namespaceResourceWhitelist"]
    }
    assert ("networking.k8s.io", "Ingress") in allowed


def test_plugin_platform_points_at_plugins_overlay() -> None:
    """Единственный владелец Ingress — Application `plugin-platform`."""
    app = next(d for d in _docs(PLUGIN_PLATFORM) if d.get("kind") == "Application")
    assert app["spec"]["source"]["path"] == "infra/k3s/overlays/dev/plugins"
    assert app["spec"]["syncPolicy"]["automated"]["selfHeal"] is True
    # prune:false НЕ мешает СОЗДАНИЮ нового ресурса (отвечает только за удаление).


def test_plugins_ingress_manifest_is_valid() -> None:
    """Реальной ошибки в манифесте нет: править нечего, нужен Argo sync."""
    doc = yaml.safe_load(PLUGINS_INGRESS.read_text(encoding="utf-8"))
    assert doc["kind"] == "Ingress"
    assert doc["metadata"]["name"] == "plugins-gateway"
    assert doc["metadata"]["namespace"] == "agentscale-dev"
    ann = doc["metadata"]["annotations"]
    assert ann["traefik.ingress.kubernetes.io/router.priority"] == EXPECTED_PRIORITY
    assert ann["traefik.ingress.kubernetes.io/router.entrypoints"] == "websecure"
    assert ann["argocd.argoproj.io/sync-wave"] == "1"
    assert doc["spec"]["ingressClassName"] == "traefik"
    # TLS без secretName → self-signed default TLSStore (cert-manager в кластере нет).
    assert all("secretName" not in entry for entry in doc["spec"]["tls"])
    paths = {
        p["path"]
        for rule in doc["spec"]["rules"]
        for p in rule["http"]["paths"]
    }
    assert paths == {"/webhook", "/pluginpage"}


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl is not installed")
def test_plugins_ingress_is_present_in_local_kustomize_render() -> None:
    """Локальный рендер БЕЗ кластера: Ingress есть, priority 120, имя не дубль."""
    rendered = subprocess.run(
        ["kubectl", "kustomize", str(DEV_PLUGINS_OVERLAY)],
        capture_output=True,
        text=True,
        # В манифестах есть кириллица; на Windows cp1251 ломает decode.
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert rendered.returncode == 0, rendered.stderr

    docs = [d for d in yaml.safe_load_all(rendered.stdout) if d]
    ingresses = [d for d in docs if d.get("kind") == "Ingress"]
    by_name = {d["metadata"]["name"]: d for d in ingresses}
    assert "plugins-gateway" in by_name, (
        "Ingress не рендерится — проверь overlays/dev/plugins/kustomization.yaml"
    )
    assert len(ingresses) == len(by_name), "дубль имени Ingress в рендере"
    ingress = by_name["plugins-gateway"]
    assert ingress["metadata"]["annotations"][
        "traefik.ingress.kubernetes.io/router.priority"
    ] == EXPECTED_PRIORITY
    assert ingress["spec"]["ingressClassName"] == "traefik"
    assert ingress["metadata"]["namespace"] == "agentscale-dev"

    # ConfigMap egress-proxy-config в том же рендере: admin тоже 0.0.0.0.
    cm = next(
        d
        for d in docs
        if d.get("kind") == "ConfigMap"
        and d["metadata"]["name"] == "egress-proxy-config"
    )
    assert _admin_socket(yaml.safe_load(cm["data"]["envoy.yaml"]))["address"] == (
        ADMIN_BIND_ADDRESS
    )

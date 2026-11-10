"""Приоритеты `plugins-gateway` и GitOps-защита activator'а (дизайн v4 §7.1, §7.4).

По образцу infra/ops/tests/test_ingress_priority.py, но для плагинового контура.

Инцидент, который закрепляет тест: Traefik превращает КАЖДЫЙ path Ingress'а
в отдельный router, а `traefik.ingress.kubernetes.io/router.priority` — это
аннотация на Ingress, а не на path. В dev-контуре /api (110) и / (100, SPA,
отвечает index.html на любой незнакомый путь) уже разнесены по двум
Ingress'ам. Пути плагинов `/webhook` и `/pluginpage` обязаны быть ВЫШЕ обоих
(120), иначе POST вебхука уедет в SPA и вернёт HTML вместо обработки — ровно
тот же «login POST → index.html», который чинили в #331. Prod wildcard (:443,
catch-all) остаётся последним (50).

Второй блок — GitOps: runtime-created Deployment плагинов вне Application, поэтому
широкий ignore /spec/replicas не должен подавлять Git масштабы always-on workloads.
Один runtime-owned /data state — единственный защищённый дрейф. Также проверяем,
что в манифест НЕ просочились kinds новых control plane
(KEDA / Knative / cert-manager / HPA), которых нет в whitelist 00-project.yaml.

Тесты читают YAML как текст/данные и НЕ запускают kubectl/argocd/кластер.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_OVERLAY = REPO_ROOT / "infra" / "k3s" / "overlays" / "dev"
ARGO_APPS = REPO_ROOT / "infra" / "argocd" / "apps"

PLUGINS_INGRESS = DEV_OVERLAY / "plugins" / "ingress-plugins.yaml"
API_INGRESS = DEV_OVERLAY / "ingress-https-api.yaml"
WEB_INGRESS = DEV_OVERLAY / "ingress-https-web.yaml"
PROD_PATCH = DEV_OVERLAY.parent / "prod" / "patch-prod.yaml"
PLUGIN_PLATFORM = ARGO_APPS / "plugin-platform.yaml"

# Явные числа из дизайна v4 §7.1 и as-built прод-оверлея (patch-prod.yaml:164).
PLUGINS_PRIORITY = 120
API_PRIORITY = 110
WEB_PRIORITY = 100
PROD_WILDCARD_PRIORITY = 50

# Ни один из этих control plane не вводится в v4: whitelist 27 kinds в
# 00-project.yaml, бюджет 45 подов / 12Gi requests, 3 OOM-инцидента.
FORBIDDEN_KINDS = {
    "ScaledObject",                 # keda.sh/*
    "HTTPScaledObject",             # http.keda.sh/* (KEDA HTTP Add-on)
    "Certificate",                 # cert-manager.io/*
    "ClusterIssuer",                # cert-manager.io/*
    "HorizontalPodAutoscaler",      # autoscaling/HPA (создаётся KEDA)
}


def _load(path: Path) -> dict:
    """Один документ Ingress/Application; fail-closed на пустом/многодокументном."""
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
    assert len(docs) == 1, f"{path.name} должен содержать ровно один документ"
    return docs[0]


def _priority(doc: dict, source: Path) -> int:
    raw = doc["metadata"].get("annotations", {}).get(
        "traefik.ingress.kubernetes.io/router.priority"
    )
    assert raw is not None, (
        f"{source.name}: нет router.priority — повторится инцидент "
        "'login POST -> index.html'"
    )
    return int(raw)


def _paths(doc: dict) -> set[str]:
    return {
        path["path"]
        for rule in doc["spec"]["rules"]
        for path in rule["http"]["paths"]
    }


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Приоритеты: plugins (120) > api (110) > web (100) > prod wildcard (50)
# ---------------------------------------------------------------------------


def test_plugins_gateway_outranks_every_dev_router() -> None:
    plugins = _load(PLUGINS_INGRESS)
    api = _load(API_INGRESS)
    web = _load(WEB_INGRESS)

    assert _priority(plugins, PLUGINS_INGRESS) == PLUGINS_PRIORITY
    assert _priority(api, API_INGRESS) == API_PRIORITY
    assert _priority(web, WEB_INGRESS) == WEB_PRIORITY

    # Строгая цепочка, а не просто «plugins больше остальных»: если кто-то
    # поменяет приоритеты api/web местами, тест тоже должен упасть.
    assert (
        PLUGINS_PRIORITY
        > _priority(api, API_INGRESS)
        > _priority(web, WEB_INGRESS)
        > PROD_WILDCARD_PRIORITY
    )
    assert _priority(plugins, PLUGINS_INGRESS) > _priority(api, API_INGRESS), (
        "/webhook и /pluginpage не должны уезжать в /api или в SPA"
    )


def test_prod_wildcard_is_still_the_lowest() -> None:
    """Prod catch-all (:443) остаётся на 50; плагины его не отодвинули вниз."""
    patch = _text(PROD_PATCH)
    assert "router.priority" in patch
    prod_priority = int(
        next(
            line.split(":", 1)[1].strip().strip('"').strip("'")
            for line in patch.splitlines()
            if "traefik.ingress.kubernetes.io/router.priority" in line
        )
    )
    assert prod_priority == PROD_WILDCARD_PRIORITY
    assert PLUGINS_PRIORITY > prod_priority


# ---------------------------------------------------------------------------
# 2. Пути ровно /webhook и /pluginpage, без catch-all `/`
# ---------------------------------------------------------------------------


def test_plugins_gateway_paths_are_exactly_the_two_prefixes() -> None:
    doc = _load(PLUGINS_INGRESS)
    paths = _paths(doc)

    assert paths == {"/webhook", "/pluginpage"}, (
        f"ожидались ровно /webhook и /pluginpage, получено {sorted(paths)}"
    )
    # Fail-closed: catch-all `/` в этом Ingress'е с приоритетом 120 перехватил
    # бы ВСЁ остальное (включая /api и здоровье) и положил бы dev-контур.
    assert "/" not in paths, "plugins-gateway не имеет права на catch-all /"
    assert "/api" not in paths and "/health" not in paths, (
        "маршруты API принадлежат agentscale-https-api, не плагинам"
    )

    for rule in doc["spec"]["rules"]:
        for path in rule["http"]["paths"]:
            assert path["pathType"] == "Prefix", (
                "под пути вида /webhook/{plugin}/{company}/* нужен Prefix"
            )
            assert path["backend"]["service"]["name"] == "plugin-gateway"
            assert path["backend"]["service"]["port"]["number"] == 8080


def test_plugins_gateway_does_not_repeat_api_or_spa_paths() -> None:
    """На всякий случай: пересечение маршрутов = неоднозначный роутинг."""
    plugins = _paths(_load(PLUGINS_INGRESS))
    api = _paths(_load(API_INGRESS))
    web = _paths(_load(WEB_INGRESS))
    assert plugins.isdisjoint(api)
    assert plugins.isdisjoint(web)


# ---------------------------------------------------------------------------
# 3. websecure / traefik / TLS / hosts / sync-wave
# ---------------------------------------------------------------------------


def test_plugins_gateway_is_pinned_to_websecure_and_traefik() -> None:
    doc = _load(PLUGINS_INGRESS)
    annotations = doc["metadata"].get("annotations", {})

    # При split-out Ingress'а аннотации надо объявить заново: базовый объект
    # их не наследует, иначе router уедет не на тот entrypoint.
    assert annotations.get(
        "traefik.ingress.kubernetes.io/router.entrypoints"
    ) == "websecure", "плагины доступны только на :443 (websecure)"
    assert annotations.get("traefik.ingress.kubernetes.io/router.tls") in (
        "true",
        True,
    ), "без router.tls маршрут не попадёт в HTTPS-роутер"
    assert doc["spec"].get("ingressClassName") == "traefik"


def test_plugins_gateway_tls_hosts_include_agentscale_dev() -> None:
    doc = _load(PLUGINS_INGRESS)
    tls_hosts = {host for entry in doc["spec"]["tls"] for host in entry["hosts"]}
    assert "agentscale.dev" in tls_hosts, (
        "TLS должен покрывать явное dev-имя agentscale.dev"
    )
    hosts = {rule["host"] for rule in doc["spec"]["rules"]}
    assert hosts, "Ingress без host-правил не соберётся в router"
    assert "agentscale.dev" in hosts
    # Fail-closed: продовый catch-all не должен «протечь» в плагиновый Ingress.
    assert tls_hosts.issubset(hosts | {"localhost"}), (
        f"TLS объявлен для хостов без правил: {sorted(tls_hosts - hosts)}"
    )


def test_plugins_gateway_sync_wave_matches_plugin_platform() -> None:
    doc = _load(PLUGINS_INGRESS)
    wave = doc["metadata"].get("annotations", {}).get(
        "argocd.argoproj.io/sync-wave"
    )
    # wave 1, как и остальные ресурсы оверлея plugin-platform. Раньше здесь
    # было 25: Argo считал Application «Missing» (Ingress отсутствует в live),
    # а более поздняя волна не применялась до здоровья предыдущих — Ingress
    # не создавался вообще. Порядок роутеров на :443 задаётся НЕ волной, а
    # аннотацией Traefik router.priority (120 > 110 > 100 > 50), проверенной
    # отдельным тестом выше; api/web Ingress'ы живут в ДРУГОМ Argo App.
    assert wave == "1", (
        "Ingress плагинов должен синхронизироваться одной волной с остальными "
        "ресурсами plugin-platform, иначе App остаётся Missing и wave 25 "
        "никогда не применяется"
    )
    assert doc["metadata"]["annotations"][
        "traefik.ingress.kubernetes.io/router.priority"
    ] == "120"


# ---------------------------------------------------------------------------
# 4. GitOps: runtime objects вне Argo, runtime state и запрет новых CRD
# ---------------------------------------------------------------------------


def _plugin_platform_docs() -> list[dict]:
    return [d for d in yaml.safe_load_all(_text(PLUGIN_PLATFORM)) if d]


def test_plugin_platform_does_not_ignore_replicas_and_protects_only_state() -> None:
    """Runtime Deployment вне Argo; always-on масштабы остаются Git-owned."""
    docs = _plugin_platform_docs()
    assert len(docs) == 1, "plugin-platform.yaml — ровно один Application"
    app = docs[0]
    assert app["kind"] == "Application"
    assert app["spec"]["project"] == "agentscale"

    ignore = app["spec"].get("ignoreDifferences") or []
    for rule in ignore:
        if rule.get("kind") != "Deployment":
            continue
        pointers = rule.get("jsonPointers") or []
        jq_paths = rule.get("jqPathExpressions") or []
        assert "/spec/replicas" not in pointers
        assert ".spec.replicas" not in jq_paths

    assert ignore == [
        {
            "group": "",
            "kind": "ConfigMap",
            "name": "plugin-activation-state",
            "jsonPointers": ["/data"],
        }
    ]
    policy = app["spec"]["syncPolicy"]
    assert policy["automated"]["selfHeal"] is True
    assert "RespectIgnoreDifferences=true" in (policy.get("syncOptions") or []), (
        "без RespectIgnoreDifferences узкий /data ignore не защищает activation CAS"
    )


def test_plugin_platform_has_no_new_control_plane_kinds() -> None:
    """KEDA / Knative / cert-manager / HPA в v4 не вводятся (§1, §7.4, §4 плана)."""
    docs = _plugin_platform_docs()
    found = {str(d.get("kind")) for d in docs if d.get("kind")}
    assert not (found & FORBIDDEN_KINDS), (
        f"запрещённые kinds в манифесте: {sorted(found & FORBIDDEN_KINDS)}"
    )
    assert docs[0]["kind"] == "Application", (
        "манифест должен быть Argo Application, не набор CRD"
    )

    # Ни объявлений kinds, ни apiVersion новых control plane, ни CRD-групп.
    # Текстовая проверка ловит и «на будущее» блоки, если кто-то вставит их
    # рабочим YAML; обоснование в манифесте намеренно сформулировано без
    # запрещённых токенов, чтобы эта проверка оставалась строгой.
    raw = _text(PLUGIN_PLATFORM)
    for forbidden in (
        "keda.sh",
        "http.keda.sh",
        "serving.knative.dev",
        "cert-manager.io",
        "acme.cert-manager.io",
        "HorizontalPodAutoscaler",
        "ScaledObject",
        "HTTPScaledObject",
        "kind: Certificate",
        "kind: ClusterIssuer",
        "kind: CustomResourceDefinition",
    ):
        assert forbidden not in raw, (
            f"упоминание '{forbidden}' в plugin-platform.yaml: новые control "
            "plane требуют правки 00-project.yaml и отдельной задачи"
        )


def test_plugin_platform_destination_is_permitted_by_project() -> None:
    """Fail-closed: namespace должен быть среди destinations 00-project.yaml."""
    app = _plugin_platform_docs()[0]
    namespace = app["spec"]["destination"]["namespace"]
    assert namespace in {"agentscale-dev", "agentscale-dev-sandboxes"}, (
        "новый namespace плагинов требует правки destinations в 00-project.yaml "
        "(см. plugin-apps-project-notes.md, раздел 3)"
    )
    assert app["spec"]["destination"]["server"] == (
        "https://kubernetes.default.svc"
    )

    project = next(
        d
        for d in yaml.safe_load_all(_text(ARGO_APPS / "00-project.yaml"))
        if d and d.get("kind") == "AppProject"
    )
    allowed = {
        entry["namespace"] for entry in project["spec"]["destinations"]
    }
    assert namespace in allowed, (
        f"namespace {namespace} не разрешён проектом agentscale — синк упадёт целиком"
    )


def test_plugin_platform_sync_wave_is_after_crds() -> None:
    """Application плагинов идёт ПОСЛЕ AppProject (-10) и CRD-платформы (-5)."""
    app = _plugin_platform_docs()[0]
    wave = int(app["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])
    assert wave > -5, "плагины не должны применяться раньше CRD agent-sandbox"
    assert wave > 0, (
        "плагины зависят от namespace/сервисов agentscale-dev (wave 0)"
    )

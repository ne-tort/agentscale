"""Пин: Envoy admin :9901 слушает 0.0.0.0, а не 127.0.0.1.

Инцидент 2026-11-08: `egress-proxy-769b44bbf7-lpx28` был 0/1 Running ~140 мин.
`readinessProbe: httpGet :9901/ready` падал «connection refused», при том что
процесс Envoy был жив (логи/варнинги штатные, listener 0.0.0.0:8080 отвечал).

Причина: `admin.address.socket_address.address: 127.0.0.1` в ConfigMap
`egress-proxy-config` (infra/k3s/base/plugins/egress-proxy.yaml) и в ЭТАЛОНЕ
образа (infra/images/plugins/egress-proxy/envoy.yaml). Kubelet выполняет probe
`GET http://<PodIP>:9901/ready` С УЗЛА: loopback пода (`127.0.0.1`) узлу
недоступен, поэтому TCP-соединение отклонялось. Startup/liveness TCP :8080
проходили (там 0.0.0.0), отчего под выглядел «живым, но не Ready».

КРИТИЧНО: конфиг НЕ статический. Контроллер (задача 08) ПЕРЕЗАПИСЫВАЕТ ключ
`envoy.yaml` ConfigMap'а при каждом grant/revoke через
`render_envoy_config` / `EgressConfigGenerator.build_envoy_routes`. Исправление
только манифеста было бы стёрто первым же sync. Поэтому эти тесты пинят и
стартовый (fail-closed) рендер, и рендер с активной egress-политикой.

Fail-closed НЕ ослаблен: `clusters: []` + `direct_response 403` сохраняются.
Тесты чистые: без сети, k8s, БД и кластера.
"""

from __future__ import annotations

import yaml

from prodavan.application.plugins.egress.generator import EgressConfigGenerator
from prodavan.application.plugins.egress.policy import (
    EgressMode,
    EgressPolicy,
    EgressRule,
)
from prodavan.infrastructure.plugins.controllers.renderer import (
    render_envoy_config,
    render_envoy_yaml,
)

#: Обязательный адрес admin-консоли. Kubelet стучится на PodIP С УЗЛА.
ADMIN_BIND_ADDRESS = "0.0.0.0"
ADMIN_PORT = 9901


def _admin_socket(config: dict) -> dict:
    """Вынуть socket_address admin-блока (fail-closed на отсутствии)."""
    return config["admin"]["address"]["socket_address"]


def _assert_admin_binds_pod_ip(config: dict) -> None:
    socket = _admin_socket(config)
    assert socket["address"] == ADMIN_BIND_ADDRESS, (
        "admin :9901 снова слушает loopback пода! Kubelet делает "
        "GET http://<PodIP>:9901/ready с узла → connection refused → "
        "под вечно 0/1 Running. Держи 0.0.0.0 (порт закрыт NP и не в Service)."
    )
    assert socket["port_value"] == ADMIN_PORT
    # readinessProbe в egress-proxy.yaml ходит именно на /ready :9901.
    assert "127.0.0.1" not in str(config["admin"])


def _active_policy() -> EgressPolicy:
    """Одна валидная egress-политика: путь контроллера с НЕпустым grant."""
    return EgressPolicy(
        mode=EgressMode.WEBHOOKS_ONLY,
        rules=(EgressRule(host="hooks.slack.com", path_prefix="/services"),),
        allowed_hosts=("hooks.slack.com",),
        allowed_paths=("/services",),
        grant_id="grant_admin_bind",
    )


# ---------------------------------------------------------------------------
# 1) Стартовый fail-closed конфиг контроллера (_empty_envoy_config)
# ---------------------------------------------------------------------------


def test_empty_envoy_config_admin_listens_on_pod_ip() -> None:
    """Без grant'ов контроллер пишет 0.0.0.0:9901 (не 127.0.0.1)."""
    config = render_envoy_config([])
    _assert_admin_binds_pod_ip(config)
    # Исправление НЕ должно открыть egress: стартовое состояние deny-all.
    assert config["static_resources"]["clusters"] == []
    vhosts = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]["route_config"]["virtual_hosts"]
    assert vhosts[-1]["name"] == "default_deny"
    assert vhosts[-1]["routes"][0]["direct_response"]["status"] == 403


def test_empty_envoy_yaml_admin_is_reachable_by_kubelet() -> None:
    """Сериализованный YAML (то, что реально уезжает в ConfigMap /data)."""
    parsed = __import__("yaml").safe_load(render_envoy_yaml(render_envoy_config([])))
    _assert_admin_binds_pod_ip(parsed)
    assert parsed["static_resources"]["clusters"] == []


# ---------------------------------------------------------------------------
# 2) Генератор задачи 08 (build_envoy_routes) — конфиг ПОСЛЕ grant'а
# ---------------------------------------------------------------------------


def test_generator_admin_listens_on_pod_ip_for_active_grant() -> None:
    """Первый же grant не должен вернуть readiness-пробу к connection refused."""
    config = EgressConfigGenerator().build_envoy_routes(_active_policy())
    _assert_admin_binds_pod_ip(config)
    # Политика применена, но fail-closed fallback на месте.
    assert config["static_resources"]["clusters"]
    vhosts = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]["route_config"]["virtual_hosts"]
    assert vhosts[-1]["name"] == "default_deny"
    assert vhosts[-1]["routes"][-1]["direct_response"]["status"] == 403


def test_renderer_merged_config_admin_listens_on_pod_ip() -> None:
    """render_envoy_config([policy]) объединяет генератор — admin тоже 0.0.0.0."""
    config = render_envoy_config([_active_policy()])
    _assert_admin_binds_pod_ip(config)
    assert "_meta" not in config  # метаданные контроллера не в envoy.yaml
    assert config["static_resources"]["clusters"]

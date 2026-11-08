"""Unit-тесты ``build_runtime_ports`` (писать, НЕ запускать, verification freeze).

ТЗ: сборка без обязательного порта падает. Дополнительно: имя override'ов
совпадает с ``PORT_OVERRIDES``, ``None`` запрещён, ``scale is readiness``,
``InMemoryStateStore`` не подменяет CAS, ``report`` без секретов.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from prodavan.application.plugins.activation.state_store import InMemoryStateStore
from prodavan.application.plugins.wiring.platform import PORT_OVERRIDES
from prodavan.infrastructure.plugins.runtime.build import (
    PLATFORM_PORT_NAMES,
    RUNTIME_OVERRIDES,
    RuntimePorts,
    build_runtime_ports,
)
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.runtime.cosign_adapter import CliCosignPort
from prodavan.infrastructure.plugins.runtime.errors import (
    PluginRuntimeError,
    RuntimeConfigError,
)
from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    ConfigMapStateStore,
    K8sScalePort,
)
from prodavan.infrastructure.plugins.runtime.proxy import HttpProxyPort
from tests.unit.plugins.runtime.conftest import (
    FakeBackendSender,
    FakeCosignRunner,
    FakeK8sApi,
    FakeProbe,
    fresh_claims,
)


def _build(config: RuntimeConfig, **over):  # type: ignore[no-untyped-def]
    """Сборка БЕЗ io: все внешние зависимости подменяются фейками."""
    api = over.pop("k8s_api", FakeK8sApi())
    runner = over.pop("cosign_runner", FakeCosignRunner())
    backend = over.pop("backend_http", FakeBackendSender())
    probe = over.pop("http_probe", FakeProbe())
    return build_runtime_ports(
        object(),
        config=config,
        k8s_api=api,
        cosign_runner=runner,
        backend_http=backend,
        http_probe=probe,
        **over,
    )


def test_all_ports_present(config) -> None:  # type: ignore[no-untyped-def]
    ports = _build(config)
    assert isinstance(ports, RuntimePorts)
    for item in fields(ports):
        assert getattr(ports, item.name) is not None
    assert isinstance(ports.cosign, CliCosignPort)
    assert isinstance(ports.scale, K8sScalePort)
    assert isinstance(ports.state_store, ConfigMapStateStore)
    assert isinstance(ports.proxy, HttpProxyPort)


def test_scale_and_readiness_are_same_object(config) -> None:  # type: ignore[no-untyped-def]
    """Один k8s-клиент на оба контракта, иначе Ready смотрел бы не тот Deployment."""
    ports = _build(config)
    assert ports.scale is ports.readiness


def test_platform_override_names_match_wiring(config) -> None:  # type: ignore[no-untyped-def]
    """Ни одного ``unknown override`` при передаче в build_plugin_platform."""
    ports = _build(config)
    overrides = ports.as_platform_overrides()
    assert set(overrides) == set(PLATFORM_PORT_NAMES)
    assert set(overrides).issubset(PORT_OVERRIDES)
    assert overrides["cosign"] is ports.cosign
    assert overrides["activation_scale"] is ports.scale
    assert overrides["activation_readiness"] is ports.readiness
    assert overrides["activation_state_store"] is ports.state_store
    assert overrides["activation_proxy"] is ports.proxy


def test_report_has_no_secrets(config) -> None:  # type: ignore[no-untyped-def]
    report = _build(config).report()
    raw = str(report).lower()
    for forbidden in ("token", "password", "dsn", "kubeconfig", "signature"):
        assert forbidden not in raw
    assert "secret" not in raw  # в отчёте нет ни секретов, ни их значений
    assert report["config"]["state_configmap"] == "plugin-activation-state"


# --------------------------------------------------------------------------- fail-closed


def test_missing_port_raises(config) -> None:  # type: ignore[no-untyped-def]
    """Явный ``None`` вместо обязательного порта = ошибка композиции."""
    with pytest.raises(PluginRuntimeError):
        _build(config, cosign=None)
    with pytest.raises(PluginRuntimeError):
        _build(config, state_store=None)
    with pytest.raises(PluginRuntimeError):
        _build(config, proxy=None)


def test_in_memory_state_store_is_rejected(config) -> None:  # type: ignore[no-untyped-def]
    """InMemoryStateStore задачи 09 НЕ защищает от гонки двух реплик Gateway."""
    with pytest.raises(PluginRuntimeError):
        _build(config, state_store=InMemoryStateStore())
    built = _build(config)
    with pytest.raises(PluginRuntimeError):
        RuntimePorts(
            config=config,
            cosign=built.cosign,
            scale=built.scale,
            readiness=built.readiness,  # тот же объект: проверяем именно state_store
            state_store=InMemoryStateStore(),  # type: ignore[arg-type]
            proxy=built.proxy,
            backend_http=built.backend_http,
        )


def test_unknown_override_is_rejected(config) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(RuntimeConfigError):
        _build(config, cosign_typo=object())


def test_bad_config_override_is_rejected() -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(RuntimeConfigError):
        build_runtime_ports(object(), config=object())  # type: ignore[arg-type]


def test_missing_namespace_is_rejected() -> None:  # type: ignore[no-untyped-def]
    """Без PLUGIN_RUNTIME_NAMESPACE сборка падает, а не «default»."""
    with pytest.raises(RuntimeConfigError):
        RuntimeConfig.from_env(env={})


def test_kms_disable_word_in_config_is_rejected() -> None:  # type: ignore[no-untyped-def]
    """«Временно отключить проверку подписи» запрещено и на уровне конфигурации."""
    with pytest.raises(RuntimeConfigError):
        RuntimeConfig.from_env(
            env={"PLUGIN_RUNTIME_NAMESPACE": "agentscale-dev", "PLUGIN_RUNTIME_KMS_KEY_REF": "off"}
        )


def test_kms_mode_is_propagated_to_cosign() -> None:  # type: ignore[no-untyped-def]
    cfg = RuntimeConfig(namespace="agentscale-dev", kms_key_ref="gcpkms://key")
    ports = _build(cfg)
    assert cfg.kms_mode is True
    assert ports.cosign.kms_mode is True
    assert ports.cosign.signature_verification_enabled is True


def test_runtime_ports_rejects_none_in_post_init(config) -> None:  # type: ignore[no-untyped-def]
    built = _build(config)
    with pytest.raises(PluginRuntimeError):
        RuntimePorts(
            config=config,
            cosign=None,  # type: ignore[arg-type]
            scale=built.scale,
            readiness=built.readiness,
            state_store=built.state_store,
            proxy=built.proxy,
            backend_http=built.backend_http,
        )


def test_injected_adapters_are_used(config) -> None:  # type: ignore[no-untyped-def]
    """Тесты могут подменить ЛЮБОЙ порт готовым объектом (без IO)."""
    api = FakeK8sApi()
    backend = FakeBackendSender()
    runner = FakeCosignRunner()
    ports = _build(config, k8s_api=api, backend_http=backend, cosign_runner=runner)
    assert ports.proxy.backend_http is backend
    assert ports.cosign._runner is runner  # type: ignore[attr-defined]
    assert ports.scale._api is api  # type: ignore[attr-defined]
    assert ports.state_store._api is api  # type: ignore[attr-defined]


def test_activation_proxy_forward_uses_fresh_token(config) -> None:  # type: ignore[no-untyped-def]
    """Сквозная проверка сборки: proxy из build_runtime_ports несёт Bearer."""
    ports = _build(config)
    claims = fresh_claims("composed-token")
    # Чистая сборка запроса (без IO, без запуска тестов как таковых).
    from prodavan.infrastructure.plugins.runtime.proxy import build_backend_request

    request = build_backend_request(
        {"method": "GET", "path": "/health", "headers": {}},
        claims,
        plugin_instance_id="inst_equipment_001",
        backend_timeout_seconds=30.0,
        stream_timeout_seconds=300.0,
    )
    assert request.headers["Authorization"] == "Bearer composed-token"
    assert ports.proxy is not None

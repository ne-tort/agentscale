"""Сборка runtime-портов плагинов (F11, композиция для ``build_plugin_platform``).

Зачем этот модуль
-----------------
``build_plugin_platform(settings, **overrides)`` НЕ создаёт cosign-CLI, k8s-
клиент, httpx и ConfigMap-хранилище: всё внешнее приходит в ``overrides``
(``wiring/platform.py:51-57``). Без обязательного ``cosign=`` сборка падает
(``_build_verifier``), а activation-зона без ``ScalePort``/``ReadinessPort``/
``ProxyPort`` остаётся с ``_MissingActivationPort`` — «почти собранная»
платформа, которая откажет на первом же wake.

:func:`build_runtime_ports` — ЕДИНАЯ точка, которая собирает весь runtime-слой
и отдаёт неизменяемую связку :class:`RuntimePorts`. Родитель делает ровно два
шага::

    runtime = build_runtime_ports(settings)
    platform = build_plugin_platform(settings, **runtime.as_platform_overrides(), ...)

Порты
-----
=========================  =====================================  =========================
Поле ``RuntimePorts``       Реализация                             override платформы
=========================  =====================================  =========================
``cosign``                 :class:`CliCosignPort`                 ``cosign``
``scale``                  :class:`K8sScalePort`                  ``activation_scale``
``readiness``              тот же :class:`K8sScalePort`           ``activation_readiness``
``state_store``            :class:`ConfigMapStateStore`           ``activation_state_store``
``proxy``                  :class:`HttpProxyPort`                 ``activation_proxy``
``backend_http``           per-instance ``HttpProxyPort``         ``gateway_backend_http``
=========================  =====================================  =========================

``scale`` и ``readiness`` — ОДИН объект: контракты ``ScalePort`` и
``ReadinessPort`` реализуются одним k8s-клиентом, поэтому не должно быть
двух разных namespace/имён Deployment.

Fail-closed
-----------
Отсутствие обязательного порта = ЯВНАЯ ошибка :class:`PluginRuntimeError`:
* ни одного ``None`` в :class:`RuntimePorts` (проверка в ``__post_init__``);
* ни одного ``None`` в ``overrides`` (``overrides`` с ``None`` отсекаются);
* ``config`` обязан быть :class:`RuntimeConfig` и быть провалидированным;
* ``enabled=False`` НЕ обрабатывается здесь: платформу выключает сам
  ``build_plugin_platform`` (он вернёт ``None`` и НЕ создаст сервисы).

``**overrides`` — инъекция готовых адаптеров (тесты/кастомная интеграция).
Неизвестные имена НЕ игнорируются молча: опечатка в имени порта = ошибка
композиции (паттерн ``_validate_overrides`` в ``wiring/platform.py:270-275``).

Риски (подробнее в отчёте родителю)
-----------------------------------
* **ConfigMap RBAC**: get/patch сужены до ``plugin-activation-state``;
  create вынесен отдельно, потому что Kubernetes RBAC не применяет
  ``resourceNames`` к create. Collection-wide create — STOP-ENABLED blocker.
 * **Admin resolver / DeploymentAdmin**: optional instance_store override supplies
 *   an async durable instance_id -> plugin_id projection to scale/readiness after
 *   restart. Proxy still derives canonical Service names from verified plugin_id
 *   claims, so an async resolver is never stringified. install.py passes the same
 *   durable store to this factory and K8sDeploymentAdmin.
* **in-cluster kubeconfig**: ``InClusterAuth`` требует SA token + CA; вне
  кластера адаптер честно отказывает, а не «считает Ready».
* **CAS-гонки**: ``resourceVersion`` ОДИН на весь ConfigMap, поэтому CAS разных
  инстансов конфликтуют даже при разных ключах ``data``; activator повторяет
  цикл, но при высокой частоте wake возможен ``ACTIVATION_CONFLICT``.
* **self-hosted OIDC**: при недоступном Fulcio включать KMS-fallback с
  ЗАФИКСИРОВАННЫМ trust root, НИКОГДА не отключать проверку подписи.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field, fields
from typing import Any, Final, Mapping

from prodavan.application.plugins.artifacts.verification import CosignPort

from .config import RuntimeConfig
from .cosign_adapter import CliCosignPort
from .errors import PluginRuntimeError, RuntimeConfigError
from .k8s_scale import (
    ConfigMapStateStore,
    K8sApiClient,
    K8sScalePort,
    _resolve_scoped_k8s_api,
    plugin_resource_name,
)
from .proxy import HttpProxyPort
from .token_provider import ServiceAccountTokenProvider

#: Имена override'ов, которые ``build_runtime_ports`` принимает как ГОТОВЫЕ
#: адаптеры. Всё остальное = ошибка (опечатка не должна теряться молча).
RUNTIME_OVERRIDES: Final[frozenset[str]] = frozenset(
    {
        "config",
        "cosign",
        "scale",
        "readiness",
        "state_store",
        "proxy",
        "backend_http",
        "k8s_api",
        "lifecycle_api",
        "lifecycle_client",
        "token_provider",
        "token_request_api",
        "token_request_client",
        "http_probe",
        "cosign_runner",
        "attestation_verifier",
        "client",
        "resource_name_resolver",
        "instance_store",
    }
)

#: Имена портов в ``build_plugin_platform(**overrides)``. Сверены с
#: ``wiring/platform.py::PORT_OVERRIDES`` (строки 141-155, 154).
PLATFORM_PORT_NAMES: Final[tuple[str, ...]] = (
    "cosign",
    "activation_scale",
    "activation_readiness",
    "activation_state_store",
    "activation_proxy",
    "gateway_backend_http",
)


@dataclass(frozen=True, slots=True)
class RuntimePorts:
    """Неизменяемая связка всех runtime-адаптеров для одной сборки платформы.

    Ни одно поле не может быть ``None``: «частично собранной» платформы с
    тихим allow не бывает (§0.5 плана, F11). ``scale is readiness`` — осознанный
    инвариант (один k8s-клиент на оба контракта activation'а).
    """

    config: RuntimeConfig
    cosign: CosignPort
    scale: K8sScalePort
    readiness: K8sScalePort
    state_store: ConfigMapStateStore
    proxy: HttpProxyPort
    backend_http: Any
    lifecycle_api: Any
    token_provider: Any
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.config, RuntimeConfig):
            raise RuntimeConfigError("RuntimePorts.config обязан быть RuntimeConfig")
        for item in fields(self):
            value = getattr(self, item.name)
            if value is None:
                raise PluginRuntimeError(
                    f"runtime port {item.name!r} отсутствует: fail-closed композиция запрещена"
                )
        if self.scale is not self.readiness:
            # Два разных k8s-клиента = рассинхрон namespace/имён Deployment:
            # readiness проверяла бы не тот объект, который масштабирует scale.
            raise PluginRuntimeError(
                "scale и readiness обязаны быть ОДНИМ K8sScalePort (activation)"
            )
        if not isinstance(self.cosign, CosignPort):
            raise PluginRuntimeError("cosign обязан реализовывать CosignPort (Protocol)")
        if not isinstance(self.state_store, ConfigMapStateStore):
            raise PluginRuntimeError("state_store обязан быть ConfigMapStateStore (межпроцессный CAS)")
        if not isinstance(self.proxy, HttpProxyPort):
            raise PluginRuntimeError("proxy обязан быть HttpProxyPort (ProxyPort)")
        if not callable(getattr(self.backend_http, "send", None)):
            raise PluginRuntimeError("backend_http обязан реализовывать send()")
        if not callable(getattr(self.lifecycle_api, "request", None)):
            raise PluginRuntimeError("lifecycle_api обязан реализовывать async request()")
        _resolve_scoped_k8s_api(
            api=self.lifecycle_api,
            token_provider=self.token_provider,
        )
        if not callable(getattr(self.token_provider, "get_token", None)):
            raise PluginRuntimeError("token_provider обязан реализовывать async get_token()")

    @property
    def activation_ports(self) -> dict[str, Any]:
        """Порты зоны activation (без proxy — он отдаётся отдельно)."""
        return {
            "activation_scale": self.scale,
            "activation_readiness": self.readiness,
            "activation_state_store": self.state_store,
        }

    def as_platform_overrides(self, *, include_gateway_backend: bool = True) -> dict[str, Any]:
        """Спроецировать порты в ``build_plugin_platform(**overrides)``.

        Имена В ТОЧНОСТИ совпадают с ``PORT_OVERRIDES`` в
        ``wiring/platform.py`` (никаких самодельных ключей — иначе
        ``_validate_overrides`` уронит сборку ``unknown override``).

        ``gateway_backend_http`` можно исключить, если задача 11 собирает
        ``PluginGatewayRuntime`` сама через ``build_gateway_runtime``.
        """
        overrides: dict[str, Any] = {
            "cosign": self.cosign,
            **self.activation_ports,
            "activation_proxy": self.proxy,
        }
        if include_gateway_backend:
            overrides["gateway_backend_http"] = self.backend_http
        return overrides

    def report(self) -> dict[str, Any]:
        """Безопасная диагностика БЕЗ секретов/токенов/kubeconfig (§4.5)."""
        return {
            "config": {
                "namespace": self.config.namespace,
                "state_configmap": self.config.state_configmap,
                "deployment_prefix": self.config.deployment_prefix,
                "health_path": self.config.health_path,
                "kms_mode": self.config.kms_mode,
            },
            "cosign": type(self.cosign).__name__,
            "scale": type(self.scale).__name__,
            "readiness": type(self.readiness).__name__,
            "state_store": type(self.state_store).__name__,
            "proxy": type(self.proxy).__name__,
            "backend_http": type(self.backend_http).__name__,
            "lifecycle_api": type(self.lifecycle_api).__name__,
        }

    async def aclose(self) -> None:
        """Release: закрыть удерживаемые HTTP-соединения при shutdown приложения.

        SSE-потоки держат соединение до потребителя; закрывать их в ``forward``
        нельзя. Вызывать из lifespan родителя.
        """
        if self._closed:
            return
        object.__setattr__(self, "_closed", True)
        first_error: Exception | None = None
        seen: set[int] = set()
        for resource in (self.proxy, self.lifecycle_api, self.token_provider):
            if id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "aclose", None)
            if not callable(close):
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # close every resource without leaking details
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise PluginRuntimeError(
                f"plugin runtime shutdown failed ({type(first_error).__name__})"
            ) from None


def _validate_overrides(overrides: Mapping[str, Any]) -> None:
    unknown = sorted(set(overrides) - RUNTIME_OVERRIDES)
    if unknown:
        raise RuntimeConfigError(
            "unknown build_runtime_ports override(s): " + ", ".join(unknown)
        )
    for name, value in overrides.items():
        if value is None:
            # Явный None = попытка отключить обязательный порт. Запрещено
            # так же, как ``_service`` в wiring/platform.py:278-288.
            raise PluginRuntimeError(
                f"override {name!r} is None; fail-closed composition forbids "
                "explicitly disabling a required runtime port"
            )


def _resolve_config(settings: Any, overrides: Mapping[str, Any]) -> RuntimeConfig:
    """Взять конфиг из overrides или построить из окружения (fail-closed).

    ``settings`` НЕ читается: у runtime-адаптеров свои ``PLUGIN_RUNTIME_*``
    переменные (см. ``config.py``), чтобы не тащить агентский контур. Аргумент
    оставлен для единообразия сигнатуры с ``build_plugin_platform`` и для
    будущей явной проекции; сейчас игнорируется намеренно.
    """
    del settings
    config = overrides.get("config")
    if config is None:
        return RuntimeConfig.from_env()
    if not isinstance(config, RuntimeConfig):
        raise RuntimeConfigError("config override must be a RuntimeConfig")
    return config


def build_runtime_ports(settings: Any, **overrides: Any) -> RuntimePorts:
    """Собрать cosign + k8s + proxy + state store (обязательные порты).

    Любая ошибка композиции = исключение (fail-closed): родитель НЕ должен
    подключать «почти собранную» платформу. Тесты подменяют готовые адаптеры
    через ``**overrides`` и не делают IO (``k8s_api``/``http_probe``/
    ``cosign_runner``/``attestation_verifier``/``client`` — инъекции).

    Пример для родителя::

        runtime = build_runtime_ports(settings)
        platform = build_plugin_platform(
            settings,
            grant_store=...,
            admin_ports=...,
            **runtime.as_platform_overrides(),
        )

    ``settings`` здесь не читается (см. :func:`_resolve_config`).
    """
    _validate_overrides(overrides)
    config = _resolve_config(settings, overrides)

    # --- 1) cosign: обязателен, иначе build_plugin_platform упадёт сам -------
    cosign = overrides.get("cosign")
    if cosign is None:
        cosign = CliCosignPort(
            binary=config.cosign_binary,
            timeout_seconds=config.cosign_timeout_seconds,
            kms_key_ref=config.kms_key_ref or None,
            runner=overrides.get("cosign_runner"),
            attestation_verifier=overrides.get("attestation_verifier"),
        )
    if not isinstance(cosign, CosignPort):
        raise PluginRuntimeError("cosign override обязан реализовывать CosignPort")

    # --- 1b) dedicated lifecycle identity and shared admin/projection client --
    token_provider = overrides.get("token_provider")
    if token_provider is None:
        token_request_api = overrides.get("token_request_api")
        if token_request_api is None:
            token_request_api = K8sApiClient(
                client=overrides.get("token_request_client")
            )
        token_provider = ServiceAccountTokenProvider(
            namespace=config.namespace,
            api=token_request_api,
        )
    if not callable(getattr(token_provider, "get_token", None)):
        raise PluginRuntimeError("token_provider override must implement get_token()")

    lifecycle_api = overrides.get("lifecycle_api")
    if lifecycle_api is None:
        lifecycle_api = K8sApiClient(
            client=overrides.get("lifecycle_client"),
            token_provider=token_provider,
        )
    if not callable(getattr(lifecycle_api, "request", None)):
        raise PluginRuntimeError("lifecycle_api override must implement request()")
    _resolve_scoped_k8s_api(api=lifecycle_api, token_provider=token_provider)

    # --- 2) тот же scoped lifecycle API для scale/readiness и CAS состояния ---
    resource_name_resolver = overrides.get("resource_name_resolver")
    if resource_name_resolver is not None and not callable(resource_name_resolver):
        raise RuntimeConfigError("resource_name_resolver обязан быть вызываемым")
    instance_store = overrides.get("instance_store")
    if instance_store is not None:
        if resource_name_resolver is not None:
            raise RuntimeConfigError(
                "instance_store и resource_name_resolver взаимоисключающи: "
                "canonical name должен иметь источник истины"
            )
        if not callable(getattr(instance_store, "get_by_id", None)):
            raise RuntimeConfigError("instance_store override must implement get_by_id()")

        async def durable_name_resolver(plugin_instance_id: str) -> str:
            record = await instance_store.get_by_id(
                plugin_instance_id=plugin_instance_id
            )
            if (
                record is None
                or getattr(record, "plugin_instance_id", None) != plugin_instance_id
                or not str(getattr(record, "plugin_id", "")).strip()
            ):
                raise RuntimeConfigError(
                    "durable instance projection не содержит canonical plugin_id"
                )
            return plugin_resource_name(
                record.plugin_id,
                plugin_instance_id,
                prefix=config.deployment_prefix,
            )

        scale_name_resolver = durable_name_resolver
    else:
        scale_name_resolver = resource_name_resolver

    # ``k8s_api`` remains an explicit test/custom-integration override. The
    # constructors below reject it unless it proves scoped identity. By default
    # every activation and admin path shares the one TokenRequest-backed client.
    api = overrides["k8s_api"] if "k8s_api" in overrides else lifecycle_api
    client = overrides.get("client")
    scale = overrides.get("scale")
    readiness = overrides.get("readiness")
    if scale is None:
        scale = K8sScalePort(
            config=config,
            api=api,
            probe=overrides.get("http_probe"),
            client=client,
            token_provider=token_provider,
            deployment_name_resolver=scale_name_resolver,
        )
    if readiness is None:
        # ОДИН объект на оба контракта: иначе readiness смотрел бы не тот Deployment.
        readiness = scale
    if not isinstance(scale, K8sScalePort):
        raise PluginRuntimeError("scale override обязан быть K8sScalePort (или None)")
    if not isinstance(readiness, K8sScalePort):
        raise PluginRuntimeError("readiness override обязан быть K8sScalePort (или None)")

    # --- 3) состояние с межпроцессным CAS -----------------------------------
    state_store = overrides.get("state_store")
    if state_store is None:
        state_store = ConfigMapStateStore(
            config=config,
            api=api,
            client=client,
            token_provider=token_provider,
        )
    if not isinstance(state_store, ConfigMapStateStore):
        raise PluginRuntimeError(
            "state_store override обязан быть ConfigMapStateStore; "
            "InMemoryStateStore задачи 09 НЕ защищает от гонки двух реплик Gateway"
        )

    # --- 4) per-instance HTTP-транспорт Gateway + тонкий ProxyPort -----------
    # НЕ создаём BackendHttpAdapter с несуществующим общим plugin-runtime.<ns>.svc.
    # HttpProxyPort сам строит BackendHttpAdapter по canonical Service URL и
    # реализует send() для Gateway. Явный backend_http остаётся DI для тестов и
    # кастомного транспорта; тогда proxy использует именно его.
    backend_http = overrides.get("backend_http")
    if backend_http is not None and not callable(getattr(backend_http, "send", None)):
        raise PluginRuntimeError(
            "backend_http override обязан реализовывать BackendHttpPort.send()"
        )

    proxy = overrides.get("proxy")
    if proxy is None:
        proxy = HttpProxyPort(
            config=config,
            backend_http=backend_http,
            service_name_resolver=resource_name_resolver,
            client=client,
        )
    if not isinstance(proxy, HttpProxyPort):
        raise PluginRuntimeError("proxy override обязан быть HttpProxyPort (ProxyPort)")
    if backend_http is None:
        backend_http = proxy

    ports = RuntimePorts(
        config=config,
        cosign=cosign,
        scale=scale,
        readiness=readiness,
        state_store=state_store,
        proxy=proxy,
        backend_http=backend_http,
        lifecycle_api=lifecycle_api,
        token_provider=token_provider,
    )
    # Финальная проверка: ни одно поле не осталось None / заглушкой.
    for item in fields(ports):
        if getattr(ports, item.name) is None:
            raise PluginRuntimeError(f"runtime port {item.name!r} is None")
    return ports


__all__ = [
    "PLATFORM_PORT_NAMES",
    "RUNTIME_OVERRIDES",
    "RuntimePorts",
    "build_runtime_ports",
]

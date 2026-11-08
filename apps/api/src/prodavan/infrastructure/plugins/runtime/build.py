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
``backend_http``           ``BackendHttpAdapter`` (общий)         ``gateway_backend_http``
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
* **ConfigMap RBAC**: имя ``plugin-activation-state`` зафиксировано в
  ``resourceNames`` задачи 14. Родителю нужно сверить, что
  ``RuntimeConfig.state_configmap`` совпадает с манифестом: иначе CAS получит
  403 -> ``StateStoreUnavailable`` (не «тихий успех»).
* **in-cluster kubeconfig**: ``InClusterAuth`` требует SA token + CA; вне
  кластера адаптер честно отказывает, а не «считает Ready».
* **CAS-гонки**: ``resourceVersion`` ОДИН на весь ConfigMap, поэтому CAS разных
  инстансов конфликтуют даже при разных ключах ``data``; activator повторяет
  цикл, но при высокой частоте wake возможен ``ACTIVATION_CONFLICT``.
* **self-hosted OIDC**: при недоступном Fulcio включать KMS-fallback с
  ЗАФИКСИРОВАННЫМ trust root, НИКОГДА не отключать проверку подписи.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Final, Mapping

from prodavan.application.plugins.artifacts.verification import CosignPort

from .config import RuntimeConfig
from .cosign_adapter import CliCosignPort
from .errors import PluginRuntimeError, RuntimeConfigError
from .k8s_scale import ConfigMapStateStore, K8sScalePort
from .proxy import BackendSenderPort, HttpProxyPort

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
        "http_probe",
        "cosign_runner",
        "client",
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
        }

    async def aclose(self) -> None:
        """Release: закрыть удерживаемые HTTP-соединения при shutdown приложения.

        SSE-потоки держат соединение до потребителя; закрывать их в ``forward``
        нельзя. Вызывать из lifespan родителя.
        """
        await self.proxy.aclose()


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
    ``cosign_runner``/``client`` — инъекции).

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
        )
    if not isinstance(cosign, CosignPort):
        raise PluginRuntimeError("cosign override обязан реализовывать CosignPort")

    # --- 2) k8s API (общий для scale/readiness и CAS состояния) --------------
    api = overrides.get("k8s_api")
    client = overrides.get("client")
    scale = overrides.get("scale")
    readiness = overrides.get("readiness")
    if scale is None:
        scale = K8sScalePort(
            config=config,
            api=api,
            probe=overrides.get("http_probe"),
            client=client,
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
        state_store = ConfigMapStateStore(config=config, api=api, client=client)
    if not isinstance(state_store, ConfigMapStateStore):
        raise PluginRuntimeError(
            "state_store override обязан быть ConfigMapStateStore; "
            "InMemoryStateStore задачи 09 НЕ защищает от гонки двух реплик Gateway"
        )

    # --- 4) общий HTTP-транспорт Gateway + тонкий ProxyPort ------------------
    # ВАЖНО: backend_http создаётся ДО proxy и передаётся в него, чтобы не было
    # двух httpx.AsyncClient. BackendHttpAdapter задачи 11 уже добавляет
    # доверенный Bearer (inject_trusted_bearer=True) и НЕ буферизует SSE.
    backend_http = overrides.get("backend_http")
    if backend_http is None:
        from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
            BackendHttpAdapter,
        )

        backend_http = BackendHttpAdapter(
            base_url=_default_gateway_base_url(config),
            client=client,
            max_timeout_seconds=config.stream_timeout_seconds,
            inject_trusted_bearer=True,
        )
    # ВАЖНО: ``BackendHttpPort`` НЕ помечен ``@runtime_checkable`` в задаче 11,
    # поэтому isinstance() по нему вызвал бы TypeError. Проверяем структурно.
    if not callable(getattr(backend_http, "send", None)):
        raise PluginRuntimeError(
            "backend_http override обязан реализовывать BackendHttpPort.send()"
        )

    proxy = overrides.get("proxy")
    if proxy is None:
        proxy = HttpProxyPort(config=config, backend_http=backend_http)
    if not isinstance(proxy, HttpProxyPort):
        raise PluginRuntimeError("proxy override обязан быть HttpProxyPort (ProxyPort)")

    ports = RuntimePorts(
        config=config,
        cosign=cosign,
        scale=scale,
        readiness=readiness,
        state_store=state_store,
        proxy=proxy,
        backend_http=backend_http,
    )
    # Финальная проверка: ни одно поле не осталось None / заглушкой.
    for item in fields(ports):
        if getattr(ports, item.name) is None:
            raise PluginRuntimeError(f"runtime port {item.name!r} is None")
    return ports


def _default_gateway_base_url(config: RuntimeConfig) -> str:
    """Базовый URL Service плагинов для общего ``BackendHttpAdapter``.

    ``plugin-runtime`` — имя Service из шаблона задачи 14
    (``plugin-deployment-template.yaml:164-184``). Конкретный путь к инстансу
    Gateway прокладывает сам через ``plugin_instance_id``; здесь важен лишь
    in-cluster DNS (а не внешний адрес).
    """
    return f"http://plugin-runtime.{config.namespace}.svc:{config.service_port}"


__all__ = [
    "PLATFORM_PORT_NAMES",
    "RUNTIME_OVERRIDES",
    "RuntimePorts",
    "build_runtime_ports",
]

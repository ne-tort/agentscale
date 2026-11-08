"""Сборка ``AdapterRegistry`` плагинов (F11, стык F2/§4.3).

Регистрируются ровно 9 адаптеров из единого реестра §4.4:
``cache``, ``objects``, ``docs``, ``events``, ``jobs``, ``pods``, ``mcp``,
``secrets``, ``network``. ``db.direct`` **НЕ регистрируется** принципиально:
БД плагина — прямой DSN к своей БД компании, граница = привилегии
PostgreSQL + отдельная БД + NetworkPolicy, а не вызов gateway
(§2, §4.3, ADR-007, ``capabilities/ports.py::AdapterRegistry.resolve``).

Fail-closed вместо «молчаливого успеха»
---------------------------------------
Если реальная реализация адаптера не передана через ``overrides``, мы НЕ
пропускаем capability и НЕ возвращаем ``None``/пустой типизированный ответ.
Вместо этого регистрируется ``UnavailableAdapter`` — объект, у которого есть
все методы нужного ``Protocol`` (поэтому ``AdapterRegistry.register`` его
принимает), но КАЖДЫЙ вызов бросает ``AdapterMissingError``. Таким образом:

* ``registry.resolve(name)`` не падает «не зарегистрирован» (реестр полон);
* ``PluginCapabilityService`` доходит до адаптера и получает исключение,
  которое его ``try/except`` превращает в ``ADAPTER_FAILED``/system deny
  (``capabilities/service.py:480-485``), а НЕ в «allow» и НЕ в «200 с None»;
* в отчёте сборки явно перечислено, какие адаптеры остаются заглушками и
  требуют F11-доработки (см. ``adapter_report`` / ``STUB_ADAPTER_NAMES``).

Это честнее, чем оставить имя незарегистрированным: незарегистрированное имя
тоже даёт deny, но не позволяет отличить «ещё не реализовано» от «опечатка
в capability» и не даёт родителю явного списка долгов.

ВАЖНО: ``UnavailableAdapter`` — НЕ «песочница для разработки». Включённая
платформа с такими адаптерами способна проверять identity/grant/quota/audit,
но НЕ способна исполнить ни одну capability. Родитель обязан либо передать
реальные адаптеры через ``overrides``, либо держать ``enabled=False``.
"""

from __future__ import annotations

from typing import Any, Final, Mapping

from prodavan.application.plugins.capabilities.contracts import (
    DB_DIRECT,
    PLUGIN_CAPABILITIES,
    CapabilityName,
    TypedResponse,
)
from prodavan.application.plugins.capabilities.ports import (
    CAPABILITY_OPERATIONS,
    AdapterMissingError,
    AdapterRegistry,
    UnknownCapabilityError,
)
from prodavan.application.plugins.wiring.config import (
    PluginPlatformConfig,
    PluginPlatformConfigError,
)

# Все имена, для которых сборка registers адаптер. ``db.direct`` отсутствует.
WIRED_CAPABILITIES: Final[tuple[str, ...]] = tuple(
    name.value for name in CapabilityName
)

# Явный список адаптеров-заглушек, требующих реальной F11-доработки.
# ``adapter_report`` возвращает фактически незаполненные на конкретной сборке;
# эта константа — полный перечень «что вообще надо реализовать по-настоящему».
STUB_ADAPTER_NAMES: Final[tuple[str, ...]] = WIRED_CAPABILITIES

# Человекочитаемое пояснение, ЧТО именно надо доработать по каждому адаптеру
# (для отчёта родителю и для логов startup).
STUB_ADAPTER_WORK: Final[Mapping[str, str]] = {
    "cache": "Redis-адаптер с key-rewrite tenant:{company}:plugin:{module}:{instance}:* (§4.3); сейчас нет get/set/delete/incr/publish",
    "objects": "MinIO bucket-per-tenant + STS/session policy или scoped presigned (§5.4, задача 06); адаптер к PluginObjectStore не подключён",
    "docs": "Mongo namespace per tenant, typed DocsQueryRequest (§4.3); нет motor-реализации query",
    "events": "Redpanda-адаптер к PluginEventDispatcher (задача 10): ручной commit, earliest, DLQ; publish/subscribe не подключены",
    "jobs": "Celery + outbox адаптер с job_id/run_id/lease (§4.3, §7.3); submit/get/cancel не подключены",
    "pods": "Typed invoke-action адаптер (плагин НЕ имеет k8s API, §4.3); реализация отсутствует",
    "mcp": "Регистрация typed tools через gateway (MCP как адаптер, §4.3); реализация отсутствует",
    "secrets": "Чтение по secret_ref без plaintext наружу (§4.3, k8s Secret); реализация отсутствует",
    "network": "Egress-прокси/Envoy по правилам из CapabilityGrant в БД (§6, задача 08/14); network.egress не подключён",
}


class UnavailableAdapter:
    """Заглушка, которая ЯВНО отказывает при вызове (fail-closed).

    Динамически предоставляет все методы, объявленные для capability в
    ``CAPABILITY_OPERATIONS``, поэтому структурная проверка
    ``AdapterRegistry.register`` проходит. Но каждый метод бросает
    ``AdapterMissingError`` — НЕ возвращает ``None``, НЕ возвращает «успешный»
    ``TypedResponse``, НЕ пишет «тихий» лог и не продолжает.

    ``AdapterMissingError`` намеренно взят из ``capabilities/ports.py``: задача
    04 маппит его в 503, а ``PluginCapabilityService.invoke`` перехватывает
    исключение адаптера как system deny (``ADAPTER_FAILED``).
    """

    __slots__ = ("_name", "_operations")

    def __init__(self, name: str) -> None:
        if name not in CAPABILITY_OPERATIONS:
            raise UnknownCapabilityError(name)
        self._name = name
        self._operations = tuple(sorted(CAPABILITY_OPERATIONS[name]))

    @property
    def name(self) -> str:
        return self._name

    @property
    def operations(self) -> tuple[str, ...]:
        return self._operations

    @staticmethod
    def _refuse(name: str, operation: str) -> None:
        # Никаких данных запроса в сообщении: аудит/логи не должны содержать
        # payload/ключи/токены (§4.5). Только имя capability и операции.
        raise AdapterMissingError(
            f"{name}.{operation}: adapter is not implemented "
            "(F11 wiring stub; real adapter must be injected via overrides)"
        )

    def __getattr__(self, operation: str) -> Any:
        # ``AdapterRegistry.register`` проверяет ``callable(getattr(impl, m))``;
        # для объявленных операций возвращаем-bound отказ, для всего прочего —
        # AttributeError, чтобы не «продать» случайный метод как адаптер.
        if operation in self._operations:
            def refuse(request: object) -> TypedResponse:
                # Аннотация возврата намеренно ``TypedResponse``: сигнализирует
                # статике, что успешный путь мог бы вернуть типизированный
                # ответ, но по факту ВСЕГДА бросает (fail-closed).
                self._refuse(self._name, operation)
                raise AssertionError("unreachable: _refuse always raises")  # pragma: no cover

            refuse.__name__ = operation
            return refuse
        raise AttributeError(
            f"{type(self).__name__!s} has no operation {operation!r} for capability {self._name!r}"
        )


def _adapter_from_override(name: str, impl: object) -> object:
    """Проверить явно переданную реализацию до регистрации.

    ``AdapterRegistry.register`` и сам проверит методы, но здесь даём более
    понятную ошибку композиции и запрещаем ``None`` явно (иначе ``getattr``
    на ``None`` просто тихо сказал бы «методов нет»).
    """
    if impl is None:
        raise PluginPlatformConfigError(
            f"adapter override for {name!r} is None; pass a real implementation "
            "or omit the override to register a fail-closed UnavailableAdapter"
        )
    missing = [
        method for method in CAPABILITY_OPERATIONS[name]
        if not callable(getattr(impl, method, None))
    ]
    if missing:
        raise PluginPlatformConfigError(
            f"adapter override for {name!r} does not implement its protocol: "
            f"missing methods {sorted(missing)}"
        )
    return impl


def build_adapter_registry(
    config: PluginPlatformConfig,
    **overrides: object,
) -> AdapterRegistry:
    """Собрать полный ``AdapterRegistry`` (9 имён, ``db.direct`` исключён).

    ``overrides`` — реальные реализации по имени capability (``cache=...``,
    ``objects=...`` и т.д.). Всё остальное заполняется ``UnavailableAdapter``.

    Fail-closed:
      * неизвестное имя в ``overrides`` -> ``UnknownCapabilityError``
        (опечатка не станет тихо проигнорированным адаптером);
      * ``db.direct`` в ``overrides`` -> ``UnknownCapabilityError`` (прямой DSN
        не может быть адаптером gateway ни при каких условиях);
      * ``enabled=False`` -> отказ: для выключенной платформы реестр не нужен,
        ``build_plugin_platform`` обязан вернуть ``None``;
      * ``config`` не того типа / невалиден -> явная ошибка.
    """
    if not isinstance(config, PluginPlatformConfig):
        raise PluginPlatformConfigError("build_adapter_registry expects PluginPlatformConfig")
    config.validate()
    if not config.enabled:
        raise PluginPlatformConfigError(
            "refusing to build an AdapterRegistry for a disabled platform; "
            "build_plugin_platform must return None instead"
        )

    unknown = sorted(set(overrides) - set(WIRED_CAPABILITIES))
    if DB_DIRECT in overrides:
        # Явная причина для прямого DSN (он не входит в WIRED_CAPABILITIES и
        # попал бы в ``unknown``, но сообщение должно быть предметным).
        raise UnknownCapabilityError(DB_DIRECT)
    if unknown:
        raise UnknownCapabilityError(unknown[0])
    assert set(WIRED_CAPABILITIES) == set(PLUGIN_CAPABILITIES), (
        "реестр capability §4.4 и WIRED_CAPABILITIES расходятся — "
        "проверь capabilities/contracts.py::CapabilityName"
    )

    registry = AdapterRegistry()
    for name in WIRED_CAPABILITIES:
        impl = _adapter_from_override(name, overrides[name]) if name in overrides else UnavailableAdapter(name)
        registry.register(name, impl)
    return registry


def stub_names(registry: AdapterRegistry, **overrides: object) -> tuple[str, ...]:
    """Имена, оставшиеся ``UnavailableAdapter`` в конкретной сборке."""
    return tuple(
        name for name in WIRED_CAPABILITIES
        if name not in overrides and _is_unavailable(registry, name)
    )


def _is_unavailable(registry: AdapterRegistry, name: str) -> bool:
    try:
        return isinstance(registry.resolve(name), UnavailableAdapter)
    except (AdapterMissingError, UnknownCapabilityError):
        # Незарегистрированное имя тоже считаем «не реализовано», но это уже
        # ошибка композиции: в штатной сборке registry полон.
        return True


def adapter_report(registry: AdapterRegistry, **overrides: object) -> Mapping[str, Any]:
    """Явный отчёт: что подключено по-настоящему, а что требует F11-доработки.

    Родитель включает этот отчёт в лог startup / свой отчёт: «молчаливых»
    заглушек быть не должно. Секретов и payload в отчёте нет (§4.5).
    """
    stubs = list(stub_names(registry, **overrides))
    real = [name for name in WIRED_CAPABILITIES if name not in stubs]
    return {
        "registered": sorted(registry.registered),
        "db_direct_registered": DB_DIRECT in registry.registered,
        "real_adapters": real,
        "stub_adapters": stubs,
        "stub_count": len(stubs),
        "requires_f11_work": [
            {"capability": name, "work": STUB_ADAPTER_WORK[name]}
            for name in stubs
        ],
        "all_known_stub_work": {
            name: STUB_ADAPTER_WORK[name] for name in STUB_ADAPTER_NAMES
        },
    }


__all__ = [
    "STUB_ADAPTER_NAMES",
    "STUB_ADAPTER_WORK",
    "WIRED_CAPABILITIES",
    "UnavailableAdapter",
    "adapter_report",
    "build_adapter_registry",
    "stub_names",
]

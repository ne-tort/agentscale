"""Адаптеры активации: Admin API (задача 12) И Plugin Gateway (задача 11).

Здесь сходятся ТРИ разных контракта с одинаковыми именами:

================================================  ==========================================
Потребитель                                       Сигнатура
================================================  ==========================================
``admin/ports.py::ActivationPort`` (задача 12)     ``wake(*, company_id, plugin_id)
                                                    -> WakeResult`` (admin-контракт),
                                                    + ``ensure_deployment``, ``suspend``,
                                                    ``runtime_status``, ``purge_infra``
``gateway/ports.py::ActivationPort`` (задача 11)   ``wake(*, plugin_id, company_id)
                                                    -> WakeLease`` + ``wait_ready``
``activation/ports.py::MintPort`` (задача 09)      ``mint(*, plugin_instance_id, reason)
                                                    -> MintedClaims``
``admin/ports.py::MintPort`` (задача 12)           ``mint(*, request: MintRequest)
                                                    -> IssuedPluginClaims``
================================================  ==========================================

**КОЛЛИЗИЯ ИМЁН.** ``MintPort`` в ``admin/ports.py`` и ``activation/ports.py``
— РАЗНЫЕ протоколы, ``ActivationPort`` в ``admin/ports.py`` и
``gateway/ports.py`` — тоже разные. Все импорты aqui выполняются с алиасами:
``AdminMintPort``, ``ActivatorMintPort``, ``AdminActivationPort``,
``GatewayActivationPort``. См. test ``test_mint_port_alias_collision.py``.

Реальное ядро (задача 09):

* ``PluginActivator.wake(instance_id, *, timeout) -> WakeResult`` — УЖЕ ждёт
  Ready и выпускает новый короткий ``PluginClaims`` (``WakeResult.token``);
* ``suspend(instance_id) -> bool``, ``hold_request(instance_id, request)``;
* ``PluginActivator`` НЕ имеет ``ensure_deployment`` / ``purge_infra`` /
  ``runtime_status``. Это ответственность k8s-порта, который подключает
  родитель (F11/задача 14); без него адаптер отказывает, а не «успех».

``(company_id, plugin_id)`` → ``instance_id``
--------------------------------------------
Адаптер НЕ придумывает instance_id из строки и НЕ подставляет «чужую»
компанию. Используется авторитетный резолвер ``InstanceIdResolver``:
ровно та же пара, что пришла в вызов, должна вернуть тот же instance_id,
который был зарегистрирован при ``ensure_deployment``. Резолвер может быть
БД-репозиторием ``CapabilityGrant`` (задача A10); in-memory вариант —
только для тестов/dev. Если пара не зарегистрирована — явный отказ.

Два тонких адаптера (по заданию: «сделай пригодным для ОБОИХ потребителей
или два тонких адаптера»):

* :class:`AdminActivationAdapter` — все 5 методов admin-порта;
* :class:`GatewayActivationAdapter` — ``wake``/``wait_ready`` для
  ``GatewayProxy``; оба используют тот же резолвер и тот же ``PluginActivator``.

Оба не «успешны по умолчанию»: нет Deployment, нет Ready, нет резолвера,
нет подтверждения purge → ``PluginAdminError``/``ActivationError``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# --- АЛИАСЫ: три разных ActivationPort/MintPort, не перепутать -------------
from prodavan.application.plugins.admin.contracts import (
    PluginRuntimeStatus,
    WakeResult as AdminWakeResult,
)
from prodavan.application.plugins.admin.errors import PluginAdminError
from prodavan.application.plugins.admin.ports import (
    ActivationPort as AdminActivationPort,
)
from prodavan.application.plugins.admin.ports import (
    MintPort as AdminMintPort,
)
from prodavan.application.plugins.activation.activator import PluginActivator
from prodavan.application.plugins.activation.errors import ActivationError
from prodavan.application.plugins.activation.ports import (
    MintPort as ActivatorMintPort,
)
from prodavan.application.plugins.activation.state import (
    WakeResult as ActivatorWakeResult,
)
from prodavan.application.plugins.gateway.ports import (
    ActivationPort as GatewayActivationPort,
)
from prodavan.application.plugins.gateway.ports import WakeLease

from .errors import require_wired


@runtime_checkable
class InstanceIdResolver(Protocol):
    """Авторитетный резолвер ``(company_id, plugin_id)`` → ``instance_id``.

    Реализация в проде — БД-репозиторий ``CapabilityGrant`` (задача A10),
    а не словарь в процессе: иначе второй репликой Gateway installation
    будет «не найдена» и wake откажет. Инжектится явно, без чтения settings.
    """

    async def resolve(self, *, company_id: str, plugin_id: str) -> str | None: ...


@runtime_checkable
class DeploymentAdminPort(Protocol):
    """k8s-часть lifecycle, которой нет в ``PluginActivator`` (задача 14/F11).

    ``ensure_deployment`` создаёт Deployment ``replicas: 0`` по ПРОВЕРЕННОМУ
    digest; ``purge_infra`` удаляет Deployment/Secret/маршруты ЭТОЙ пары;
    ``runtime_status`` читает фактическое состояние. Реализация обязана быть
    идемпотентной и не трогать другие компании.
    """

    async def ensure_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        dsn_ref: str,
    ) -> str: ...

    async def purge_infra(self, *, company_id: str, plugin_id: str) -> bool: ...

    async def runtime_status(
        self, *, company_id: str, plugin_id: str
    ) -> PluginRuntimeStatus: ...


@dataclass
class InMemoryInstanceResolver:
    """In-memory резолвер для тестов/dev (НЕ для кластера, см. задачу A10).

    Ключ — СТРОГО пара ``(company_id, plugin_id)``: подменить компанию нельзя,
    потому что поиск идёт по обоим полям, а не «найдём любой instance».
    """

    _instances: dict[tuple[str, str], str] = field(default_factory=dict)

    def register(
        self, *, company_id: str, plugin_id: str, instance_id: str
    ) -> None:
        if not instance_id.strip():
            raise ValueError("InMemoryInstanceResolver: instance_id не должен быть пустым")
        self._instances[(company_id, plugin_id)] = instance_id

    async def resolve(self, *, company_id: str, plugin_id: str) -> str | None:
        return self._instances.get((company_id, plugin_id))


class _ActivationBridge:
    """Общая логика перевода ``(company, plugin)`` → ``WakeResult`` активатора."""

    def __init__(
        self,
        *,
        activator: PluginActivator,
        resolver: InstanceIdResolver,
    ) -> None:
        self._activator = require_wired("activation.activator", activator)
        self._resolver = require_wired("activation.resolver", resolver)
        if not isinstance(activator, PluginActivator):
            raise TypeError("_ActivationBridge: ожидался PluginActivator")

    @property
    def activator(self) -> PluginActivator:
        return self._activator

    @property
    def resolver(self) -> InstanceIdResolver:
        return self._resolver

    async def _instance_id(self, *, company_id: str, plugin_id: str) -> str:
        """Резолвить instance_id СТРОГО по авторитетной паре."""

        instance_id = await self._resolver.resolve(
            company_id=company_id, plugin_id=plugin_id
        )
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise PluginAdminError(
                code="GRANT_NOT_FOUND",
                detail=(
                    "не найден plugin_instance_id для пары (company, plugin): "
                    "установка не зарегистрирована"
                ),
                status=503,
                extra={"adapter_operation": "resolve_instance"},
            )
        return instance_id

    async def _wake(self, *, company_id: str, plugin_id: str) -> ActivatorWakeResult:
        """Вызвать реальный activator и перепроверить область результата."""

        instance_id = await self._instance_id(company_id=company_id, plugin_id=plugin_id)
        try:
            result = await self._activator.wake(instance_id)
        except ActivationError:
            raise
        except Exception as exc:  # noqa: BLE001 - k8s/читатель мог упасть как угодно
            raise PluginAdminError(
                code="GRANT_NOT_FOUND",
                detail=f"активатор недоступен: {type(exc).__name__}",
                status=503,
                extra={"adapter_operation": "wake"},
            ) from None
        if not isinstance(result, ActivatorWakeResult):
            raise PluginAdminError(
                code="GRANT_NOT_FOUND",
                detail="PluginActivator.wake вернул не WakeResult",
                status=503,
                extra={"adapter_operation": "wake"},
            )
        if result.plugin_instance_id != instance_id:
            # Активатор разбудил ЧУЖОЙ instance — это отказ, не «почти успех».
            raise PluginAdminError(
                code="GRANT_NOT_FOUND",
                detail="PluginActivator.wake вернул чужой plugin_instance_id",
                status=503,
                extra={"adapter_operation": "wake"},
            )
        return result


class AdminActivationAdapter(_ActivationBridge):
    """``admin.ActivationPort``: 5 методов lifecycle поверх реальных сервисов."""

    def __init__(
        self,
        *,
        activator: PluginActivator,
        resolver: InstanceIdResolver,
        deployments: DeploymentAdminPort,
        resolver_registrar: InMemoryInstanceResolver | None = None,
    ) -> None:
        super().__init__(activator=activator, resolver=resolver)
        self._deployments = require_wired("activation.deployments", deployments)
        if not isinstance(deployments, DeploymentAdminPort):
            raise TypeError("AdminActivationAdapter: ожидался DeploymentAdminPort")
        self._registrar = resolver_registrar

    @property
    def deployments(self) -> DeploymentAdminPort:
        return self._deployments

    async def ensure_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        dsn_ref: str,
    ) -> str:
        """Создать Deployment ``replicas: 0`` по проверенному digest.

        Регистрируем пару → instance в резолвере ТОЛЬКО после подтверждения
        k8s-порта: иначе ``wake`` мог бы обратиться к несуществующему pod'у.
        """

        name = await self._deployments.ensure_deployment(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            artifact_digest=artifact_digest,
            dsn_ref=dsn_ref,
        )
        if not isinstance(name, str) or not name.strip():
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="DeploymentAdminPort не подтвердил имя Deployment",
                status=503,
                extra={"adapter_operation": "ensure_deployment"},
            )
        if self._registrar is not None:
            self._registrar.register(
                company_id=company_id,
                plugin_id=plugin_id,
                instance_id=plugin_instance_id,
            )
        return name

    async def suspend(self, *, company_id: str, plugin_id: str) -> bool:
        """``scale_to(0)`` через activator; БД/схема сохраняются (§5.5)."""

        instance_id = await self._instance_id(company_id=company_id, plugin_id=plugin_id)
        try:
            suspended = await self._activator.suspend(instance_id)
        except ActivationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PluginAdminError(
                code="REVOKE_FAILED",
                detail=f"усыпление плагина недоступно: {type(exc).__name__}",
                status=503,
                extra={"adapter_operation": "suspend"},
            ) from None
        # ``False`` — честный «запрещено/не требуется» (активные job'ы, уже
        # SUSPENDED), а не провал. Admin-сервис не проверяет это значение.
        return bool(suspended)

    async def wake(self, *, company_id: str, plugin_id: str) -> AdminWakeResult:
        """0→1 с ожиданием Ready → admin-``WakeResult``.

        ``ready`` = успешно вызванный ``PluginActivator.wake`` (внутри уже
        дождался Ready, иначе было бы исключение). ``replicas`` берём из
        ``previous_replicas`` только как справочное значение; реальное число
        реплик после 0→1 равно 1. ``waited_ms`` — из ``waited_seconds``.
        """

        result = await self._wake(company_id=company_id, plugin_id=plugin_id)
        if not result.token.strip():
            # Токен пуст — Ready не подтверждён issuance'ом, отказ.
            raise PluginAdminError(
                code="GRANT_NOT_FOUND",
                detail="PluginActivator.wake вернул пустой capability-токен",
                status=503,
                extra={"adapter_operation": "wake"},
            )
        return AdminWakeResult(
            ready=True,
            replicas=1,
            waited_ms=int(max(0.0, result.waited_seconds) * 1000),
        )

    async def runtime_status(
        self, *, company_id: str, plugin_id: str
    ) -> PluginRuntimeStatus:
        """Фактический статус из k8s-порта (не «угадай по токену»)."""

        status = await self._deployments.runtime_status(
            company_id=company_id, plugin_id=plugin_id
        )
        if not isinstance(status, PluginRuntimeStatus):
            raise PluginAdminError(
                code="GRANT_NOT_FOUND",
                detail="DeploymentAdminPort.runtime_status вернул некорректный статус",
                status=503,
                extra={"adapter_operation": "runtime_status"},
            )
        return status

    async def purge_infra(self, *, company_id: str, plugin_id: str) -> bool:
        """Purge Deployment/Secret/маршрутов ЭТОЙ пары (критерий №15)."""

        result = await self._deployments.purge_infra(
            company_id=company_id, plugin_id=plugin_id
        )
        if not isinstance(result, bool):
            raise PluginAdminError(
                code="UNINSTALL_PARTIAL",
                detail="DeploymentAdminPort.purge_infra не подтвердил удаление",
                status=503,
                extra={"adapter_operation": "purge_infra"},
            )
        return result


class GatewayActivationAdapter(_ActivationBridge):
    """``gateway.ActivationPort`` (задача 11): ``wake`` → ``WakeLease``.

    ``GatewayProxy`` вызывает ``wake(plugin_id=..., company_id=...)`` ДО
    ``forward`` и затем ``wait_ready(lease, timeout_seconds=...)``. Activator
    УЖЕ удержал запрос до Ready внутри ``wake``, поэтому ``wait_ready``
    возвращает ``True`` (дополнительно перепроверяя, что lease не истёк);
    повторного опроса ``ReadinessPort`` здесь не требуется — это явно
    разрешено контрактом шлюза (см. ``gateway/ports.py``, docstring
    ``ActivationPort``).
    """

    def __init__(
        self,
        *,
        activator: PluginActivator,
        resolver: InstanceIdResolver,
        clock: Any = None,
    ) -> None:
        super().__init__(activator=activator, resolver=resolver)
        self._clock = clock or time

    async def wake(self, *, plugin_id: str, company_id: str) -> WakeLease:
        """Пробудить backend и обернуть ``WakeResult.token`` в ``WakeLease``."""

        result = await self._wake(company_id=company_id, plugin_id=plugin_id)
        if not result.token.strip():
            raise PluginAdminError(
                code="GRANT_NOT_FOUND",
                detail="PluginActivator.wake вернул пустой capability-токен",
                status=503,
                extra={"adapter_operation": "gateway.wake"},
            )
        return WakeLease(
            plugin_id=plugin_id,
            company_id=company_id,
            capability_token=result.token,
            expires_at=float(result.expires_at.timestamp()),
            ready=True,
        )

    async def wait_ready(
        self, lease: WakeLease, *, timeout_seconds: float
    ) -> bool:
        """Activator уже дождался Ready; проверяем область и свежесть lease."""

        if not isinstance(lease, WakeLease):
            return False
        if not lease.plugin_id.strip() or not lease.company_id.strip():
            return False
        # Подтверждаем, что lease всё ещё относится к ЗАРЕГИСТРИРОВАННОЙ
        # паре: чужую компанию «подменить» через готовый lease нельзя.
        try:
            await self._instance_id(
                company_id=lease.company_id, plugin_id=lease.plugin_id
            )
        except PluginAdminError:
            return False
        if timeout_seconds <= 0 or timeout_seconds != timeout_seconds:
            return False
        if float(lease.expires_at) <= float(self._clock.time()):
            return False
        return bool(lease.ready)


__all__ = [
    # Алиасы (коллизия имён — обязательна для теста).
    "ActivatorMintPort",
    "AdminActivationPort",
    "AdminMintPort",
    "GatewayActivationPort",
    # Адаптеры.
    "AdminActivationAdapter",
    "GatewayActivationAdapter",
    # Порты/резолверы.
    "DeploymentAdminPort",
    "InMemoryInstanceResolver",
    "InstanceIdResolver",
]

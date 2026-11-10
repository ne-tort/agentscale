"""Адаптеры активации: Admin API (задача 12) И Plugin Gateway (задача 11).

Здесь сходятся ТРИ разных контракта с одинаковыми именами:

================================================  ==========================================
Потребитель                                       Сигнатура
================================================  ==========================================
``admin/ports.py::ActivationPort`` (задача 12)     ``wake(*, company_id, plugin_id)
                                                    -> WakeResult`` (admin-контракт),
                                                    + ``ensure/update_deployment``, ``suspend``,
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
``gateway/ports.py`` — тоже разные. Все импорты здесь выполнены с алиасами:
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

* :class:`AdminActivationAdapter` — все 6 методов admin-порта;
* :class:`GatewayActivationAdapter` — ``wake``/``wait_ready`` для
  ``GatewayProxy``; оба используют тот же резолвер и тот же ``PluginActivator``.

Оба не «успешны по умолчанию»: нет Deployment, нет Ready, нет резолвера,
нет подтверждения purge → ``PluginAdminError``/``ActivationError``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# --- АЛИАСЫ: три разных ActivationPort/MintPort, не перепутать -------------
from prodavan.application.plugins.admin.contracts import (
    PluginRuntimeBinding,
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
from prodavan.application.plugins.gateway.ports import (
    WakeLease,
    is_valid_artifact_digest,
)

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
        webhook_secret_ref: str,
    ) -> str: ...

    async def update_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        expected_current_artifact_digest: str,
        update_owner_token: str | None,
        expected_current_update_owner_token: str | None,
        dsn_ref: str,
        webhook_secret_ref: str,
    ) -> str: ...

    async def purge_infra(self, *, company_id: str, plugin_id: str) -> bool: ...

    async def runtime_status(
        self, *, company_id: str, plugin_id: str
    ) -> PluginRuntimeStatus: ...

    async def runtime_binding(
        self, *, company_id: str, plugin_id: str
    ) -> PluginRuntimeBinding: ...


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

    async def _instance_id(
        self,
        *,
        company_id: str,
        plugin_id: str,
        expected_instance_id: str | None = None,
    ) -> str:
        """Сверить resolver с exact durable instance, не перенаправлять трафик."""

        instance_id = await self._resolver.resolve(
            company_id=company_id, plugin_id=plugin_id
        )
        mapped = instance_id.strip() if isinstance(instance_id, str) and instance_id.strip() else None
        if expected_instance_id is not None:
            if not isinstance(expected_instance_id, str) or not expected_instance_id.strip():
                raise PluginAdminError(
                    code="GRANT_NOT_FOUND",
                    detail="durable grant plugin_instance_id пуст; wake запрещён",
                    status=503,
                    extra={"adapter_operation": "resolve_instance"},
                )
            expected = expected_instance_id.strip()
            if mapped != expected:
                raise PluginAdminError(
                    code="GRANT_NOT_FOUND",
                    detail="resolver не совпадает с durable grant plugin_instance_id",
                    status=503,
                    extra={"adapter_operation": "resolve_instance"},
                )
            return expected
        if mapped is None:
            raise PluginAdminError(
                code="GRANT_NOT_FOUND",
                detail=(
                    "не найден plugin_instance_id для пары (company, plugin): "
                    "установка не зарегистрирована"
                ),
                status=503,
                extra={"adapter_operation": "resolve_instance"},
            )
        return mapped

    async def _wake(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str | None = None,
    ) -> ActivatorWakeResult:
        """Вызвать реальный activator и перепроверить область результата."""

        instance_id = await self._instance_id(
            company_id=company_id,
            plugin_id=plugin_id,
            expected_instance_id=plugin_instance_id,
        )
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
    """``admin.ActivationPort``: 6 методов lifecycle поверх реальных сервисов."""

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
        webhook_secret_ref: str,
    ) -> str:
        """Создать Deployment ``replicas: 0`` по проверенному digest.

        Регистрируем пару → instance в резолвере ТОЛЬКО после подтверждения
        k8s-порта: иначе ``wake`` мог бы обратиться к несуществующему pod'у.
        """

        if not isinstance(webhook_secret_ref, str) or not webhook_secret_ref.strip():
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="Отсутствует durable webhook_secret_ref",
                status=503,
                extra={"adapter_operation": "ensure_deployment"},
            )
        try:
            name = await self._deployments.ensure_deployment(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=plugin_instance_id,
                artifact_digest=artifact_digest,
                dsn_ref=dsn_ref,
                webhook_secret_ref=webhook_secret_ref,
            )
        except PluginAdminError:
            raise
        except Exception as exc:  # noqa: BLE001 - infrastructure errors stay redacted
            deployment_code = str(getattr(exc, "code", ""))
            raise PluginAdminError(
                code=(
                    "ARTIFACT_NOT_PUBLISHED"
                    if deployment_code == "ARTIFACT_PROVENANCE_INVALID"
                    else "GRANT_EXISTS"
                ),
                detail=f"Deployment lifecycle отклонён: {type(exc).__name__}",
                status=(409 if deployment_code == "ARTIFACT_PROVENANCE_INVALID" else 503),
                extra={"adapter_operation": "ensure_deployment"},
            ) from None
        if not isinstance(name, str) or not name.strip():
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="DeploymentAdminPort не подтвердил имя Deployment",
                status=503,
                extra={"adapter_operation": "ensure_deployment"},
            )
        status = await self._deployments.runtime_status(
            company_id=company_id, plugin_id=plugin_id
        )
        if not isinstance(status, PluginRuntimeStatus) or status is PluginRuntimeStatus.ABSENT:
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="Deployment не подтверждён фактическим runtime status",
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

    async def update_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        expected_current_artifact_digest: str,
        update_owner_token: str | None,
        expected_current_update_owner_token: str | None,
        dsn_ref: str,
        webhook_secret_ref: str,
    ) -> str:
        """Patch an existing backend without replacing its durable pin early.

        ``expected_current_artifact_digest`` fences the old durable instance pin;
        ``artifact_digest`` selects the new published bundle/backend. DeploymentAdmin
        preserves replicas. A previously RUNNING runtime must still be RUNNING after
        the patch; IDLE/SUSPENDED runtimes must at least remain present.
        """

        if not isinstance(webhook_secret_ref, str) or not webhook_secret_ref.strip():
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="Отсутствует durable webhook_secret_ref",
                status=503,
                extra={"adapter_operation": "update_deployment", "patch_attempted": False},
            )
        try:
            before = await self.runtime_status(company_id=company_id, plugin_id=plugin_id)
        except PluginAdminError as exc:
            raise PluginAdminError(
                code=exc.code,
                detail="Update preflight runtime status не подтверждён",
                status=503,
                extra={"adapter_operation": "update_deployment", "patch_attempted": False},
            ) from None
        if before is PluginRuntimeStatus.ABSENT:
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="Update запрещён: текущий Deployment отсутствует",
                status=503,
                extra={"adapter_operation": "update_deployment", "patch_attempted": False},
            )
        try:
            name = await self._deployments.update_deployment(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=plugin_instance_id,
                artifact_digest=artifact_digest,
                expected_current_artifact_digest=expected_current_artifact_digest,
                update_owner_token=update_owner_token,
                expected_current_update_owner_token=expected_current_update_owner_token,
                dsn_ref=dsn_ref,
                webhook_secret_ref=webhook_secret_ref,
            )
        except PluginAdminError:
            raise
        except Exception as exc:  # noqa: BLE001 - infrastructure errors stay redacted
            deployment_code = str(getattr(exc, "code", ""))
            artifact_live_conflict = (
                deployment_code == "DEPLOYMENT_ARTIFACT_DIGEST_CONFLICT"
            )
            preflight_denied = artifact_live_conflict or deployment_code in {
                "ARTIFACT_PROVENANCE_INVALID",
                "RESOURCE_OWNERSHIP_MISMATCH",
                "DEPLOYMENT_STATE_INVALID",
                "DEPLOYMENT_CONFIG_INVALID",
            }
            raise PluginAdminError(
                code=(
                    "PLUGIN_UPDATE_CONFLICT"
                    if artifact_live_conflict
                    else (
                        "ARTIFACT_NOT_PUBLISHED"
                        if deployment_code == "ARTIFACT_PROVENANCE_INVALID"
                        else "GRANT_EXISTS"
                    )
                ),
                detail=f"Deployment update отклонён: {type(exc).__name__}",
                status=(
                    409
                    if artifact_live_conflict
                    or deployment_code == "ARTIFACT_PROVENANCE_INVALID"
                    else 503
                ),
                extra={
                    "adapter_operation": "update_deployment",
                    "patch_attempted": not preflight_denied,
                },
            ) from None
        if not isinstance(name, str) or not name.strip():
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="DeploymentAdminPort не подтвердил имя обновлённого Deployment",
                status=503,
                extra={"adapter_operation": "update_deployment", "patch_attempted": True},
            )
        after = await self.runtime_status(company_id=company_id, plugin_id=plugin_id)
        if after is PluginRuntimeStatus.ABSENT or (
            before is PluginRuntimeStatus.RUNNING
            and after is not PluginRuntimeStatus.RUNNING
        ):
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="Deployment update не подтвердил health/replicas post-state",
                status=503,
                extra={"adapter_operation": "update_deployment", "patch_attempted": True},
            )
        if self._registrar is not None:
            self._registrar.register(
                company_id=company_id,
                plugin_id=plugin_id,
                instance_id=plugin_instance_id,
            )
        return name

    async def suspend(self, *, company_id: str, plugin_id: str) -> bool:
        """Admin revoke: scale-to-zero and explicitly clean up delivered capabilities."""

        direct_suspend = getattr(self._deployments, "suspend", None)
        if callable(direct_suspend):
            try:
                suspended = await direct_suspend(
                    company_id=company_id,
                    plugin_id=plugin_id,
                    cleanup_delivery=True,
                )
            except Exception as exc:  # noqa: BLE001
                raise PluginAdminError(
                    code="REVOKE_FAILED",
                    detail=f"K8s scale-to-zero не подтверждён: {type(exc).__name__}",
                    status=503,
                    extra={"adapter_operation": "deployment.suspend"},
                ) from None
            if suspended is not True:
                raise PluginAdminError(
                    code="REVOKE_FAILED",
                    detail="K8s DeploymentAdmin не подтвердил scale-to-zero",
                    status=503,
                    extra={"adapter_operation": "deployment.suspend"},
                )
            return True

        # Backward-compatible structural port fakes may only implement the
        # original three methods. Real K8sDeploymentAdmin takes the path above.
        instance_id = await self._instance_id(company_id=company_id, plugin_id=plugin_id)
        try:
            suspended = await self._activator.force_suspend(instance_id)
        except ActivationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PluginAdminError(
                code="REVOKE_FAILED",
                detail=f"усыпление плагина недоступно: {type(exc).__name__}",
                status=503,
                extra={"adapter_operation": "suspend"},
            ) from None
        if suspended is not True:
            raise PluginAdminError(
                code="REVOKE_FAILED",
                detail="forced suspend не подтвердил replicas=0",
                status=503,
                extra={"adapter_operation": "force_suspend"},
            )
        return True

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

    async def runtime_binding(
        self, *, company_id: str, plugin_id: str
    ) -> PluginRuntimeBinding:
        binding = await self._deployments.runtime_binding(
            company_id=company_id, plugin_id=plugin_id
        )
        if not isinstance(binding, PluginRuntimeBinding):
            raise PluginAdminError(
                code="PLUGIN_UPDATE_CONFLICT",
                detail="DeploymentAdminPort.runtime_binding вернул некорректный fence",
                status=503,
                extra={"adapter_operation": "runtime_binding", "patch_attempted": False},
            )
        return binding

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

    async def wake(
        self,
        *,
        plugin_id: str,
        company_id: str,
        plugin_instance_id: str | None = None,
    ) -> WakeLease:
        """Пробудить exact durable instance и обернуть токен в ``WakeLease``."""

        result = await self._wake(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
        )
        if not result.token.strip():
            raise PluginAdminError(
                code="GRANT_NOT_FOUND",
                detail="PluginActivator.wake вернул пустой capability-токен",
                status=503,
                extra={"adapter_operation": "gateway.wake"},
            )
        claims = result.claims
        artifact_digest = (
            claims.get("artifact_digest") if isinstance(claims, Mapping) else None
        )
        grant_id = claims.get("grant_id") if isinstance(claims, Mapping) else None
        expected_claims = {
            "company_id": company_id,
            "plugin_id": plugin_id,
            "plugin_instance_id": result.plugin_instance_id,
        }
        if (
            not isinstance(claims, Mapping)
            or not is_valid_artifact_digest(artifact_digest)
            or not isinstance(grant_id, str)
            or not grant_id.strip()
            or any(
                claims.get(name) != expected
                for name, expected in expected_claims.items()
            )
            or claims.get("sub")
            != f"plugin:{plugin_id}:{company_id}:{result.plugin_instance_id}"
        ):
            raise PluginAdminError(
                code="GRANT_NOT_FOUND",
                detail=(
                    "PluginActivator.wake returned invalid "
                    "grant/company/plugin/instance/artifact claims"
                ),
                status=503,
                extra={"adapter_operation": "gateway.wake"},
            )
        return WakeLease(
            plugin_id=plugin_id,
            company_id=company_id,
            plugin_instance_id=result.plugin_instance_id,
            capability_token=result.token,
            artifact_digest=artifact_digest,
            grant_id=grant_id,
            expires_at=float(result.expires_at.timestamp()),
            ready=True,
        )

    async def wait_ready(
        self, lease: WakeLease, *, timeout_seconds: float
    ) -> bool:
        """Activator уже дождался Ready; проверяем область и свежесть lease."""

        if not isinstance(lease, WakeLease):
            return False
        if (
            not lease.plugin_id.strip()
            or not lease.company_id.strip()
            or not lease.plugin_instance_id
            or not is_valid_artifact_digest(lease.artifact_digest)
            or not lease.grant_id
        ):
            return False
        # Подтверждаем, что lease всё ещё относится к ЗАРЕГИСТРИРОВАННОЙ
        # паре: чужую компанию «подменить» через готовый lease нельзя.
        try:
            await self._instance_id(
                company_id=lease.company_id,
                plugin_id=lease.plugin_id,
                expected_instance_id=lease.plugin_instance_id,
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

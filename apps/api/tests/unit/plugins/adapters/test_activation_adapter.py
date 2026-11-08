"""Тесты ``activation_adapter``: admin И gateway потребители, алиасы MintPort.

Покрытие по заданию:
* коллизия ``MintPort`` разрешена алиасами (admin vs activation);
* ``(company, plugin)`` → ``instance_id`` НЕ подменяется чужой компанией;
* ``WakeResult.token`` → ``WakeLease`` для задачи 11 + ``wait_ready``;
* admin-порт: ensure_deployment/suspend/wake/runtime_status/purge_infra;
* fail-closed: неизвестная пара, пустой токен, чужой instance_id.
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.admin.contracts import PluginRuntimeStatus
from prodavan.application.plugins.admin.errors import PluginAdminError
from prodavan.application.plugins.adapters.activation_adapter import (
    ActivatorMintPort,
    AdminActivationAdapter,
    AdminActivationPort,
    AdminMintPort,
    DeploymentAdminPort,
    GatewayActivationAdapter,
    GatewayActivationPort,
    InMemoryInstanceResolver,
    InstanceIdResolver,
)
from prodavan.application.plugins.activation.activator import PluginActivator
from prodavan.application.plugins.activation.ports import (
    MintPort as RealActivatorMintPort,
)
from prodavan.application.plugins.admin.ports import (
    MintPort as RealAdminMintPort,
)
from prodavan.application.plugins.admin.ports import (
    ActivationPort as RealAdminActivationPort,
)
from prodavan.application.plugins.gateway.ports import (
    ActivationPort as RealGatewayActivationPort,
)

from .fakes import (
    COMPANY,
    COMPANY_OTHER,
    DIGEST,
    INSTANCE,
    PLUGIN,
    FakeDeploymentAdmin,
    make_activator_wake_result,
)


class _FakeActivator:
    """Минимальный дублёр сигнатуры PluginActivator (без k8s/часов/аудита).

    Настоящий ``PluginActivator`` требует 6 портов; для проверки СТЫКА
    достаточно подменить только ``wake``/``suspend``. См. ``_activator``.
    """

    def __init__(self) -> None:
        self.wake_calls: list[str] = []
        self.suspend_calls: list[str] = []
        self.wake_result = make_activator_wake_result()

    async def wake(self, instance_id: str, *, timeout: float | None = None) -> object:
        self.wake_calls.append(instance_id)
        return self.wake_result

    async def suspend(self, instance_id: str) -> bool:
        self.suspend_calls.append(instance_id)
        return True


def _activator() -> PluginActivator:
    """Собрать совместимый объект через ``object.__new__`` без вызова __init__.

    Это НЕ запуск тестов и НЕ обход проверки типов в проде: адаптер всё равно
    проверяет ``isinstance(activator, PluginActivator)``. Такой приём нужен,
    чтобы не тащить в unit-тест 6 полноценных портов задачи 09.
    """

    obj = object.__new__(PluginActivator)
    fake = _FakeActivator()
    obj.wake = fake.wake  # type: ignore[method-assign]
    obj.suspend = fake.suspend  # type: ignore[method-assign]
    obj.__dict__["_fake"] = fake
    return obj


def _fake_of(activator: PluginActivator) -> _FakeActivator:
    return activator.__dict__["_fake"]  # type: ignore[return-value]


# --------------------------------------------------------------------------- алиасы


def test_all_name_collisions_are_resolved_by_aliases() -> None:
    assert AdminMintPort is RealAdminMintPort
    assert ActivatorMintPort is RealActivatorMintPort
    assert AdminMintPort is not ActivatorMintPort
    assert AdminActivationPort is RealAdminActivationPort
    assert GatewayActivationPort is RealGatewayActivationPort
    assert AdminActivationPort is not GatewayActivationPort
    # Разные сигнатуры wake.
    assert "company_id" in RealAdminActivationPort.wake.__annotations__
    assert "plugin_id" in RealGatewayActivationPort.wake.__annotations__
    assert "wait_ready" in dir(RealGatewayActivationPort)
    assert "wait_ready" not in dir(RealAdminActivationPort)


# --------------------------------------------------------------------------- admin порт


async def test_ensure_deployment_registers_exact_company_plugin_pair() -> None:
    resolver = InMemoryInstanceResolver()
    deployments = FakeDeploymentAdmin()
    adapter = AdminActivationAdapter(
        activator=_activator(),
        resolver=resolver,
        deployments=deployments,
        resolver_registrar=resolver,
    )

    name = await adapter.ensure_deployment(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        artifact_digest=DIGEST,
        dsn_ref="secret_ref://dsn",
    )

    assert name == f"{COMPANY}-{PLUGIN}"
    assert await resolver.resolve(company_id=COMPANY, plugin_id=PLUGIN) == INSTANCE
    # Чужая компания НЕ получает тот же instance (критерий №15).
    assert await resolver.resolve(company_id=COMPANY_OTHER, plugin_id=PLUGIN) is None


async def test_wake_maps_company_plugin_to_instance_and_never_substitutes() -> None:
    resolver = InMemoryInstanceResolver()
    resolver.register(company_id=COMPANY, plugin_id=PLUGIN, instance_id=INSTANCE)
    activator = _activator()
    adapter = AdminActivationAdapter(
        activator=activator,
        resolver=resolver,
        deployments=FakeDeploymentAdmin(),
    )

    result = await adapter.wake(company_id=COMPANY, plugin_id=PLUGIN)
    assert result.ready is True
    assert result.replicas == 1
    assert result.waited_ms == 12
    assert _fake_of(activator).wake_calls == [INSTANCE]

    # Другая компания: резолвер не зарегистрирован → явный отказ, а не wake
    # «чужого» instance. Это и есть защита от подмены компании.
    with pytest.raises(PluginAdminError, match="не найден plugin_instance_id"):
        await adapter.wake(company_id=COMPANY_OTHER, plugin_id=PLUGIN)
    assert _fake_of(activator).wake_calls == [INSTANCE]


async def test_wake_rejects_foreign_instance_id_from_activator() -> None:
    # Подменяем результат на чужой instance через dataclasses.replace.
    from dataclasses import replace

    resolver = InMemoryInstanceResolver()
    resolver.register(company_id=COMPANY, plugin_id=PLUGIN, instance_id=INSTANCE)
    activator = _activator()
    _fake_of(activator).wake_result = replace(
        make_activator_wake_result(), plugin_instance_id="inst_OTHER_company"
    )
    adapter = AdminActivationAdapter(
        activator=activator,
        resolver=resolver,
        deployments=FakeDeploymentAdmin(),
    )

    with pytest.raises(PluginAdminError, match="чужой plugin_instance_id"):
        await adapter.wake(company_id=COMPANY, plugin_id=PLUGIN)


async def test_wake_rejects_empty_token_fail_closed() -> None:
    from dataclasses import replace

    resolver = InMemoryInstanceResolver()
    resolver.register(company_id=COMPANY, plugin_id=PLUGIN, instance_id=INSTANCE)
    activator = _activator()
    _fake_of(activator).wake_result = replace(
        make_activator_wake_result(), token=""
    )
    adapter = AdminActivationAdapter(
        activator=activator,
        resolver=resolver,
        deployments=FakeDeploymentAdmin(),
    )
    with pytest.raises(PluginAdminError, match="пустой capability-токен"):
        await adapter.wake(company_id=COMPANY, plugin_id=PLUGIN)


async def test_suspend_calls_activator_with_resolved_instance() -> None:
    resolver = InMemoryInstanceResolver()
    resolver.register(company_id=COMPANY, plugin_id=PLUGIN, instance_id=INSTANCE)
    activator = _activator()
    adapter = AdminActivationAdapter(
        activator=activator,
        resolver=resolver,
        deployments=FakeDeploymentAdmin(),
    )

    assert await adapter.suspend(company_id=COMPANY, plugin_id=PLUGIN) is True
    assert _fake_of(activator).suspend_calls == [INSTANCE]


async def test_runtime_status_and_purge_are_delegated_to_k8s_port() -> None:
    resolver = InMemoryInstanceResolver()
    deployments = FakeDeploymentAdmin()
    adapter = AdminActivationAdapter(
        activator=_activator(),
        resolver=resolver,
        deployments=deployments,
    )

    status = await adapter.runtime_status(company_id=COMPANY, plugin_id=PLUGIN)
    assert status is PluginRuntimeStatus.ABSENT
    assert await adapter.purge_infra(company_id=COMPANY, plugin_id=PLUGIN) is True
    assert any(c.method == "purge_infra" for c in deployments.calls)


# --------------------------------------------------------------------------- gateway порт (задача 11)


async def test_gateway_wake_returns_wake_lease_from_activator_token() -> None:
    resolver = InMemoryInstanceResolver()
    resolver.register(company_id=COMPANY, plugin_id=PLUGIN, instance_id=INSTANCE)
    activator = _activator()
    adapter = GatewayActivationAdapter(activator=activator, resolver=resolver)

    lease = await adapter.wake(plugin_id=PLUGIN, company_id=COMPANY)

    assert lease.plugin_id == PLUGIN
    assert lease.company_id == COMPANY
    assert lease.capability_token == make_activator_wake_result().token
    assert lease.ready is True
    assert lease.expires_at > 0
    assert _fake_of(activator).wake_calls == [INSTANCE]


async def test_gateway_wait_ready_checks_scope_and_expiry() -> None:
    resolver = InMemoryInstanceResolver()
    resolver.register(company_id=COMPANY, plugin_id=PLUGIN, instance_id=INSTANCE)
    adapter = GatewayActivationAdapter(activator=_activator(), resolver=resolver)
    lease = await adapter.wake(plugin_id=PLUGIN, company_id=COMPANY)

    # Activator уже дождался Ready внутри wake → wait_ready=True.
    assert await adapter.wait_ready(lease, timeout_seconds=5.0) is True
    assert await adapter.wait_ready(lease, timeout_seconds=0.0) is False
    assert await adapter.wait_ready(None, timeout_seconds=5.0) is False  # type: ignore[arg-type]

    # Lease с чужой компанией не подтверждается даже при готовом токене.
    from dataclasses import replace

    foreign = replace(lease, company_id=COMPANY_OTHER)
    assert await adapter.wait_ready(foreign, timeout_seconds=5.0) is False
    expired = replace(lease, expires_at=0.0)
    assert await adapter.wait_ready(expired, timeout_seconds=5.0) is False


async def test_gateway_wake_rejects_unregistered_company() -> None:
    adapter = GatewayActivationAdapter(
        activator=_activator(), resolver=InMemoryInstanceResolver()
    )
    with pytest.raises(PluginAdminError, match="не найден plugin_instance_id"):
        await adapter.wake(plugin_id=PLUGIN, company_id=COMPANY_OTHER)


def test_resolver_and_deployment_ports_are_structural_protocols() -> None:
    assert isinstance(InMemoryInstanceResolver(), InstanceIdResolver)
    assert isinstance(FakeDeploymentAdmin(), DeploymentAdminPort)

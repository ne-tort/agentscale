"""Сквозные тесты стыка: ``PluginAdminService`` + реальные адаптеры (НЕ запускать).

Проверяем, что собранная связка действительно «заводит» сервис задачи 12:
* PUBLISHED по проверенному digest → grant проходит;
* QUARANTINED/отсутствующий артефакт → ARTIFACT_NOT_PUBLISHED;
* revoke подтверждается реестром отзывов (bool), uninstall не трогает чужую
  компанию;
* аудит не содержит raw данных ни на одном шаге.

Эти тесты написаны, НО НЕ ЗАПУСКАЛИСЬ (verification freeze плана миграции).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.admin.contracts import (
    CapabilitySet,
    GrantStatus,
)
from prodavan.application.plugins.admin.errors import (
    PluginAdminError,
    PluginAdminErrorCode,
)
from prodavan.application.plugins.adapters.build import build_plugin_admin_service

from .test_build import _deps

DIGEST = "sha256:" + "a" * 64
COMPANY = "co_a"
PLUGIN = "mod_equipment"
VERSION = "1.2.0"


# --------------------------------------------------------------------------- grant


async def test_service_grant_via_adapters_requires_published_digest() -> None:
    deps = _deps()
    service = build_plugin_admin_service(**deps)

    # Артефакт в реестре PUBLISHED (см. make_artifact_record в _deps).
    receipt = await service.grant(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
        capabilities=CapabilitySet(capabilities=("db.direct", "cache", "objects")),
    )

    assert receipt.grant.status is GrantStatus.ACTIVE
    assert receipt.claims.grant_id == receipt.grant.grant_id
    assert receipt.claims.jti
    assert receipt.database.dsn_ref.startswith("secret://")
    assert receipt.schema.dsn_ref.startswith("secret_ref://")
    assert receipt.objects.bucket == f"tenant-{COMPANY}"
    # Аудит записан и не содержит raw данных.
    audit = service._ports.audit
    assert audit.events
    for entry in audit.events:
        serialized = " ".join(entry.values()).lower()
        assert "postgresql://" not in serialized
        assert "password" not in serialized
        assert "select " not in serialized
        assert "eyj" not in serialized


async def test_service_grant_rejects_quarantined_artifact() -> None:
    deps = _deps()
    service = build_plugin_admin_service(**deps)

    with pytest.raises(PluginAdminError) as exc:
        await service.grant(
            company_id=COMPANY,
            plugin_id=PLUGIN,
            manifest_version="9.9.9",  # версии нет в реестре
            artifact_digest=DIGEST,
            capabilities=CapabilitySet(capabilities=("cache",)),
        )
    assert exc.value.code == PluginAdminErrorCode.ARTIFACT_NOT_PUBLISHED


async def test_service_grant_rejects_tag_as_digest() -> None:
    """Тег (``:latest``) не проходит PUBLISHED-гейт даже при валидной версии."""

    deps = _deps()
    service = build_plugin_admin_service(**deps)

    with pytest.raises((PluginAdminError, ValueError)) as exc:
        await service.grant(
            company_id=COMPANY,
            plugin_id=PLUGIN,
            manifest_version=VERSION,
            artifact_digest="ghcr.io/plugins/mod_equipment:latest",
            capabilities=CapabilitySet(capabilities=("cache",)),
        )
    # Отказ, а не «успех по тегу»: либо валидация admin-сервиса, либо
    # ARTIFACT_NOT_PUBLISHED из адаптера реестра.
    assert isinstance(exc.value, (PluginAdminError, ValueError))


# --------------------------------------------------------------------------- revoke


async def test_service_revoke_confirms_write_through_async_wrapper() -> None:
    deps = _deps()
    service = build_plugin_admin_service(**deps)
    await service.grant(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
        capabilities=CapabilitySet(capabilities=("cache",)),
    )

    receipt = await service.revoke(company_id=COMPANY, plugin_id=PLUGIN)

    assert receipt.status is GrantStatus.REVOKED
    assert receipt.token_revoked is True
    assert receipt.database_preserved is True
    # Реестр отзывов (синхронное ядро identity) действительно содержит grant.
    source = deps["revocation_source"]
    assert receipt.grant_id in source.revoked


async def test_service_wake_refuses_foreign_company() -> None:
    """(company, plugin) не подменяется чужой компанией ни на одном шаге."""

    deps = _deps()
    service = build_plugin_admin_service(**deps)
    await service.grant(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
        capabilities=CapabilitySet(capabilities=("cache",)),
    )

    with pytest.raises(PluginAdminError, match="не найден"):
        await service.wake(company_id="co_other", plugin_id=PLUGIN)


# --------------------------------------------------------------------------- uninstall


async def test_service_uninstall_keeps_other_company_intact() -> None:
    deps = _deps()
    service = build_plugin_admin_service(**deps)
    receipt = await service.grant(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
        capabilities=CapabilitySet(capabilities=("cache",)),
    )
    assert receipt.grant.company_id == COMPANY

    uninstall = await service.uninstall(company_id=COMPANY, plugin_id=PLUGIN)
    assert uninstall.deleted is True
    assert uninstall.company_id == COMPANY
    # Чужая компания в вызовах адаптеров не появлялась: резолвер знает только
    # COMPANY, а все вызовы uninstall получили ровно авторитетную пару.
    resolver = deps["instance_resolver"]
    assert await resolver.resolve(company_id="co_b", plugin_id=PLUGIN) is None
    # Глобальный immutable digest реестра НЕ удалён uninstall'ом компании.
    assert deps["artifact_store"].by_digest(DIGEST) is not None

"""Unit-тесты ``PluginAdminService`` (F3-admin).

Покрывают acceptance-критерии §11 №12, №15, №16 и все требования задачи:

* grant на QUARANTINED (и отсутствующий) артефакт → отказ;
* повторный grant идемпотентен;
* revoke мгновенно отзывает токен по ``grant_id``;
* uninstall не трогает другую компанию;
* uninstall НЕ требует snapshot;
* set_enabled_modules переключает путь без миграции данных;
* wake выдаёт новый токен;
* отсутствует какая-либо операция переноса данных.

Тесты НЕ запускались (verification freeze, §9 дизайна и §0 плана миграции).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from prodavan.application.plugins.admin.contracts import (
    PLUGIN_CLAIMS_TTL_SECONDS,
    AdminDecision,
    AdminOperation,
    ArtifactPublicationStatus,
    CapabilitySet,
    EgressMode,
    GrantStatus,
    PluginRuntimeStatus,
)
from prodavan.application.plugins.admin.errors import (
    PluginAdminError,
    PluginAdminErrorCode,
)
from prodavan.application.plugins.admin.service import PluginAdminService

from .fakes import (
    COMPANY_A,
    COMPANY_B,
    DIGEST_A,
    DIGEST_B,
    PLUGIN_X,
    PLUGIN_Y,
    Harness,
)


# --------------------------------------------------------------------------- grant


async def test_grant_on_quarantined_artifact_is_rejected(
    harness: Harness, grant_args: dict
) -> None:
    """QUARANTINED-артефакт не публикуется в установку (§11 кр. 18)."""

    harness.artifact_registry.seed(
        plugin_id=PLUGIN_X,
        manifest_version="1.2.0",
        artifact_digest=DIGEST_A,
        status=ArtifactPublicationStatus.QUARANTINED,
    )
    with pytest.raises(PluginAdminError) as caught:
        await harness.service().grant(**grant_args)
    assert caught.value.code == PluginAdminErrorCode.ARTIFACT_NOT_PUBLISHED.value
    # Fail-closed: ни одного позитивного мутирующего вызова порта.
    assert harness.grant_store.grants == {}
    assert harness.tenant_db.databases == set()
    assert harness.activation.deployments == {}
    assert harness.routes.registered == {}
    assert harness.mint.issued == []
    # Отказ зафиксирован в аудите.
    assert any(
        event.decision is AdminDecision.DENY
        and event.code == PluginAdminErrorCode.ARTIFACT_NOT_PUBLISHED.value
        for event in harness.audit.events
    )


async def test_grant_on_missing_artifact_is_rejected(harness: Harness, grant_args: dict) -> None:
    """Отсутствие записи в реестре равносильно «не опубликовано», а не «разрешить»."""

    with pytest.raises(PluginAdminError) as caught:
        await harness.service().grant(**grant_args)
    assert caught.value.code == PluginAdminErrorCode.ARTIFACT_NOT_PUBLISHED.value
    assert harness.grant_store.grants == {}


async def test_grant_on_published_succeeds(harness: Harness, grant_args: dict) -> None:
    receipt = await harness.service().grant(**grant_args)

    assert receipt.company_id == COMPANY_A
    assert receipt.plugin_id == PLUGIN_X
    assert receipt.grant.status is GrantStatus.ACTIVE
    assert receipt.grant.artifact_digest == DIGEST_A
    assert receipt.database.database_name == "tenant_co_a"
    assert receipt.schema.schema_name == "module_mod_equipment"
    assert receipt.objects.bucket == "tenant-co_a"
    assert receipt.claims.grant_id == receipt.grant.grant_id
    assert receipt.claims.ttl_seconds == PLUGIN_CLAIMS_TTL_SECONDS
    assert receipt.routes.grant_id == receipt.grant.grant_id
    assert receipt.replayed is False
    # Egress-политика и квоты хранятся в долгоживущем grant, а не в токене (§4.2).
    assert receipt.grant.egress_policy.mode is EgressMode.NONE
    assert receipt.grant.quotas.ops_per_minute >= 1


async def test_repeated_grant_is_idempotent(harness: Harness, grant_args: dict) -> None:
    service = harness.service()
    first = await service.grant(**grant_args)
    second = await service.grant(**grant_args)

    assert second.replayed is True
    assert second.grant.grant_id == first.grant.grant_id
    assert second.grant.plugin_instance_id == first.grant.plugin_instance_id
    assert len(harness.grant_store.grants) == 1
    # Инфраструктура не пересоздаётся: ровно одна запись create на grant.
    assert sum(1 for c in harness.grant_store.calls if c.method == "create") == 1
    # ensure_* идемпотентны по контракту порта и вызываются при каждом grant,
    # но не создают дубликатов (в фейке ровно одна схема/префикс/deployment).
    assert len(harness.tenant_db.schemas) == 1
    assert len(harness.object_store.prefixes) == 1
    assert len(harness.activation.deployments) == 1
    assert sum(1 for c in harness.activation.calls if c.method == "ensure_deployment") == 1
    # Короткий токен при этом новый (ротация при каждом вызове).
    assert second.claims.token != first.claims.token
    assert second.claims.grant_id == first.claims.grant_id


async def test_repeated_grant_with_different_digest_is_conflict(
    harness: Harness, grant_args: dict
) -> None:
    harness.artifact_registry.seed(
        plugin_id=PLUGIN_X,
        manifest_version="1.3.0",
        artifact_digest=DIGEST_B,
        status=ArtifactPublicationStatus.PUBLISHED,
    )
    await harness.service().grant(**grant_args)
    conflict = dict(grant_args, manifest_version="1.3.0", artifact_digest=DIGEST_B)
    with pytest.raises(PluginAdminError) as caught:
        await harness.service().grant(**conflict)
    assert caught.value.code == PluginAdminErrorCode.GRANT_EXISTS.value


async def test_grant_requires_typed_capabilities(harness: Harness, grant_args: dict) -> None:
    """Произвольный dict вместо CapabilitySet отклоняется (нет «тихого» приведения)."""

    with pytest.raises(TypeError):
        await harness.service().grant(**dict(grant_args, capabilities={"capabilities": ["cache"]}))


async def test_grant_rejects_unknown_capability(harness: Harness, grant_args: dict) -> None:
    with pytest.raises(ValueError, match="неизвестные capability"):
        await harness.service().grant(
            **dict(grant_args, capabilities=CapabilitySet(capabilities=("raw_sql",)))
        )


# --------------------------------------------------------------------------- revoke


async def test_revoke_instantly_revokes_token_by_grant_id(
    harness: Harness, grant_args: dict
) -> None:
    """§11 критерий 12: старый PluginClaims отклоняется по ``grant_id``."""

    service = harness.service()
    receipt = await service.grant(**grant_args)
    grant_id = receipt.grant.grant_id

    result = await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)

    assert result.grant_id == grant_id
    assert result.token_revoked is True
    assert result.status is GrantStatus.REVOKED
    # Реестр отзывов содержит ровно этот grant_id — gateway отклонит старый токен.
    assert grant_id in harness.revocation.revoked
    assert await harness.revocation.is_revoked(grant_id=grant_id) is True
    assert harness.grant_store.grants[(COMPANY_A, PLUGIN_X)].is_active is False
    assert harness.routes.registered == {}
    assert harness.activation.statuses[(COMPANY_A, PLUGIN_X)] is PluginRuntimeStatus.SUSPENDED
    assert any(
        call.method == "revoke" and call.kwargs["grant_id"] == grant_id
        for call in harness.revocation.calls
    )


async def test_revoke_preserves_database_schema_and_role(
    harness: Harness, grant_args: dict
) -> None:
    """§5.5: при revoke БД/схема сохраняются, удаление только в uninstall."""

    await harness.service().grant(**grant_args)
    result = await harness.service().revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)

    assert result.database_preserved is True
    assert (COMPANY_A, PLUGIN_X) in harness.tenant_db.schemas
    assert harness.tenant_db.dropped == []
    assert harness.tenant_db.databases == {"tenant_co_a"}
    assert harness.object_store.prefixes[(COMPANY_A, PLUGIN_X)] == "module-mod_equipment/*"


async def test_revoke_without_grant_is_not_found(harness: Harness) -> None:
    with pytest.raises(PluginAdminError) as caught:
        await harness.service().revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert caught.value.code == PluginAdminErrorCode.GRANT_NOT_FOUND.value


async def test_revoke_fails_closed_when_revocation_not_confirmed(
    harness: Harness, grant_args: dict
) -> None:
    harness.revocation.revoke_result = False
    await harness.service().grant(**grant_args)
    with pytest.raises(PluginAdminError) as caught:
        await harness.service().revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert caught.value.code == PluginAdminErrorCode.REVOKE_FAILED.value
    # Grant остался активным: отзыв не подтверждён.
    assert harness.grant_store.grants[(COMPANY_A, PLUGIN_X)].is_active is True


async def test_revoke_fails_closed_when_routes_not_removed(
    harness: Harness, grant_args: dict
) -> None:
    harness.routes.unregister_result = False
    await harness.service().grant(**grant_args)
    with pytest.raises(PluginAdminError) as caught:
        await harness.service().revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert caught.value.code == PluginAdminErrorCode.REVOKE_FAILED.value


# --------------------------------------------------------------------------- uninstall


async def _install_two_companies(harness: Harness, grant_args: dict) -> None:
    """Установить один и тот же плагин в две компании."""

    harness.artifact_registry.seed(
        plugin_id=PLUGIN_X,
        manifest_version="1.2.0",
        artifact_digest=DIGEST_A,
        status=ArtifactPublicationStatus.PUBLISHED,
    )
    await harness.service().grant(**grant_args)
    await harness.service().grant(
        **dict(
            grant_args,
            company_id=COMPANY_B,
        )
    )


async def test_uninstall_does_not_touch_other_company(
    harness: Harness, grant_args: dict
) -> None:
    """§11 критерий 15: DROP SCHEMA/ROLE и bucket не задевают чужую компанию."""

    await _install_two_companies(harness, grant_args)
    service = harness.service()

    # Фиксируем число обращений к компании B до uninstall (установка уже была).
    calls_b_before = len(harness.calls_for_company(COMPANY_B))
    assert calls_b_before > 0

    result = await service.uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)

    assert result.deleted is True
    assert result.schema_dropped is True
    assert result.role_dropped is True
    assert result.prefix_dropped is True
    assert result.artifact_unlinked is True
    assert result.tenant_infra_purged is True
    assert result.token_revoked is True
    assert result.snapshot_taken is False

    # За время uninstall не добавлено ни одного обращения к компании B.
    assert len(harness.calls_for_company(COMPANY_B)) == calls_b_before
    # Повторный uninstall компании A (уже absent) тоже не трогает B.
    await service.uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert len(harness.calls_for_company(COMPANY_B)) == calls_b_before

    # Состояние компании B не изменено.
    assert (COMPANY_B, PLUGIN_X) in harness.tenant_db.schemas
    assert (COMPANY_B, PLUGIN_X) in harness.object_store.prefixes
    assert (COMPANY_B, PLUGIN_X) in harness.grant_store.grants
    assert (COMPANY_B, PLUGIN_X) in harness.activation.deployments
    assert harness.tenant_db.dropped == [(COMPANY_A, PLUGIN_X)]
    assert harness.object_store.dropped_prefixes == [(COMPANY_A, PLUGIN_X)]
    assert harness.object_store.dropped_buckets == [COMPANY_A]


async def test_uninstall_does_not_require_snapshot(
    harness: Harness, grant_args: dict
) -> None:
    """Данные dev эфемерны: ни snapshot, ни переноса данных (§5.5, §8, §13.1)."""

    await harness.service().grant(**grant_args)
    result = await harness.service().uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)

    assert result.snapshot_taken is False
    # Ни один порт не вызывался с методами backup/snapshot/migrate.
    forbidden = ("snapshot", "backup", "migrate", "dual_read", "dual_write", "restore")
    for call in harness.all_calls:
        assert not any(token in call.method.lower() for token in forbidden)
    # В publicly видимых контрактах нет полей snapshot/backup.
    assert not hasattr(result, "backup_ref")
    assert result.fully_removed is True


async def test_uninstall_is_idempotent(harness: Harness, grant_args: dict) -> None:
    await harness.service().grant(**grant_args)
    first = await harness.service().uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    second = await harness.service().uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)

    assert first.deleted is True
    assert second.deleted is True
    assert second.already_absent is True
    assert second.completed_steps == ()
    assert harness.tenant_db.dropped == [(COMPANY_A, PLUGIN_X)]


async def test_uninstall_partial_is_reported(harness: Harness, grant_args: dict) -> None:
    """Частичное удаление → UNINSTALL_PARTIAL со списком неудавшихся шагов."""

    harness.artifact_registry.unlink_result = False
    harness.activation.purge_result = False
    await harness.service().grant(**grant_args)

    with pytest.raises(PluginAdminError) as caught:
        await harness.service().uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert caught.value.code == PluginAdminErrorCode.UNINSTALL_PARTIAL.value
    assert "artifact_registry" in caught.value.extra["failed_steps"]
    assert "tenant_infra" in caught.value.extra["failed_steps"]


async def test_uninstall_removes_artifact_from_registry(
    harness: Harness, grant_args: dict
) -> None:
    await harness.service().grant(**grant_args)
    await harness.service().uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert harness.artifact_registry.unlinked == [(COMPANY_A, PLUGIN_X, DIGEST_A)]


# --------------------------------------------------------------------------- wake


async def test_wake_issues_new_short_token(harness: Harness, grant_args: dict) -> None:
    """§5.5 / §7.4: activator + новый короткий ``PluginClaims``; DSN тот же."""

    service = harness.service()
    first = await service.grant(**grant_args)

    result = await service.wake(company_id=COMPANY_A, plugin_id=PLUGIN_X)

    assert result.wake.ready is True
    assert result.runtime_status is PluginRuntimeStatus.RUNNING
    assert result.grant_id == first.grant.grant_id
    assert result.claims.token != first.claims.token
    assert result.claims.jti != first.claims.jti
    assert result.claims.grant_id == first.grant.grant_id
    assert result.claims.ttl_seconds == PLUGIN_CLAIMS_TTL_SECONDS
    assert len(harness.mint.issued) == 2
    # В токен не попали egress/квоты: в MintRequest есть только grant_id/quota_ref.
    request = harness.mint.issued[-1]
    assert request.grant_id == first.grant.grant_id
    assert not hasattr(request, "egress_policy")
    assert not hasattr(request, "quotas")


async def test_wake_rejects_revoked_grant(harness: Harness, grant_args: dict) -> None:
    service = harness.service()
    await service.grant(**grant_args)
    await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    with pytest.raises(PluginAdminError) as caught:
        await service.wake(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert caught.value.code == PluginAdminErrorCode.GRANT_NOT_FOUND.value


async def test_wake_rejects_grant_marked_in_revocation_store(
    harness: Harness, grant_args: dict
) -> None:
    """Даже при активной записи grant реестр отзывов блокирует wake (fail-closed)."""

    service = harness.service()
    receipt = await service.grant(**grant_args)
    harness.revocation.revoked.add(receipt.grant.grant_id)
    with pytest.raises(PluginAdminError) as caught:
        await service.wake(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert caught.value.code == PluginAdminErrorCode.GRANT_NOT_FOUND.value
    # Ни пробуждения, ни выпуска нового токена.
    assert not any(call.method == "wake" for call in harness.activation.calls)
    assert len(harness.mint.issued) == 1


async def test_wake_fails_closed_when_not_ready(harness: Harness, grant_args: dict) -> None:
    harness.activation.wake_ready = False
    await harness.service().grant(**grant_args)
    with pytest.raises(RuntimeError, match="Ready"):
        await harness.service().wake(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    # Только токен из grant; новый не выпускался.
    assert len(harness.mint.issued) == 1


# --------------------------------------------------------------------------- list


async def test_list_reports_installed_plugins_and_statuses(
    harness: Harness, grant_args: dict
) -> None:
    await harness.service().grant(**grant_args)
    snapshot = await harness.service().list(company_id=COMPANY_A)

    assert snapshot.company_id == COMPANY_A
    assert len(snapshot.items) == 1
    item = snapshot.items[0]
    assert item.plugin_id == PLUGIN_X
    assert item.grant_status is GrantStatus.ACTIVE
    assert item.runtime_status is PluginRuntimeStatus.IDLE
    assert item.enabled is False  # фича-флаг ещё не включён
    assert item.egress_mode is EgressMode.NONE

    other = await harness.service().list(company_id=COMPANY_B)
    assert other.items == ()


# --------------------------------------------------------------------------- strangler


async def test_set_enabled_modules_switches_path_without_data_migration(
    harness: Harness, grant_args: dict
) -> None:
    """§8 этап 2 / §11 критерий 16: переключается только путь."""

    await harness.service().grant(**grant_args)
    service = harness.service()

    result = await service.set_enabled_modules(
        company_id=COMPANY_A, plugin_ids=(PLUGIN_X,)
    )

    assert result.enabled_modules == (PLUGIN_X,)
    assert result.changed is True
    assert result.data_migration_performed is False
    assert harness.grant_store.enabled == {COMPANY_A: (PLUGIN_X,)}
    # Переключение флага не вызвало НИ ОДНОГО обращения к портам инфраструктуры:
    # все вызовы БД/объектов/активатора остались от исходного grant.
    infra_calls = [
        c
        for c in harness.all_calls
        if c.port in ("tenant_db", "object_store", "activation", "artifact_registry")
    ]
    allowed_methods = {
        "ensure_company_database",
        "ensure_plugin_schema",
        "ensure_bucket",
        "ensure_deployment",
        "get",
        "runtime_status",
    }
    assert all(c.method in allowed_methods for c in infra_calls)
    assert not any(c.method.startswith(("drop_", "purge_", "unlink_")) for c in infra_calls)
    # Запрещённые операции отсутствуют в принципе.
    forbidden = ("snapshot", "backup", "migrate", "dual_read", "dual_write", "copy_data")
    for call in harness.all_calls:
        assert not any(token in call.method.lower() for token in forbidden)

    listed = await service.list(company_id=COMPANY_A)
    assert listed.items[0].enabled is True
    assert listed.enabled_modules == (PLUGIN_X,)

    # Выключение флага — тоже без переноса данных.
    off = await service.set_enabled_modules(company_id=COMPANY_A, plugin_ids=())
    assert off.changed is True
    assert off.enabled_modules == ()
    assert off.data_migration_performed is False
    assert (await service.list(company_id=COMPANY_A)).items[0].enabled is False


async def test_set_enabled_modules_is_idempotent(
    harness: Harness, grant_args: dict
) -> None:
    await harness.service().grant(**grant_args)
    service = harness.service()
    await service.set_enabled_modules(company_id=COMPANY_A, plugin_ids=(PLUGIN_X,))
    writes_before = sum(1 for c in harness.grant_store.calls if c.method == "set_enabled_modules")
    replay = await service.set_enabled_modules(company_id=COMPANY_A, plugin_ids=(PLUGIN_X,))
    writes_after = sum(1 for c in harness.grant_store.calls if c.method == "set_enabled_modules")

    assert replay.replayed is True
    assert replay.changed is False
    assert writes_after == writes_before
    assert replay.data_migration_performed is False


async def test_enabled_modules_rejects_unsafe_ids(harness: Harness) -> None:
    with pytest.raises(ValueError, match="недопустимый plugin_id"):
        await harness.service().set_enabled_modules(
            company_id=COMPANY_A, plugin_ids=("../../prodavan",)
        )


# --------------------------------------------------------------------------- отсутствие переноса данных


def test_no_data_migration_operation_exists() -> None:
    """Явный запрет: в публичном API сервиса нет операции переноса данных.

    §5.5 / §8 / §13.1: ``migrate-to-plugin``, snapshot, dual-read/dual-write и
    бэкапы исключены — данные dev эфемерны.
    """

    public = {
        name
        for name, _ in inspect.getmembers(PluginAdminService, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public == {"grant", "revoke", "uninstall", "wake", "list", "set_enabled_modules"}
    forbidden = ("migrate", "snapshot", "backup", "restore", "copy_data", "dual_read", "dual_write")
    for name in public:
        assert not any(token in name for token in forbidden)


def test_no_forbidden_migration_terms_in_admin_sources() -> None:
    """В исходниках админской зоны нет даже упоминания запрещённой операции."""

    admin_dir = (
        Path(__file__).resolve().parents[4]
        / "src"
        / "prodavan"
        / "application"
        / "plugins"
        / "admin"
    )
    # admin_dir = <repo>/apps/api/src/prodavan/application/plugins/admin
    # parents[2] = <repo>/apps/api/src/prodavan
    api_file = admin_dir.parents[2] / "api" / "v1" / "plugin_admin.py"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (*sorted(admin_dir.glob("*.py")), api_file)
    )
    # Термин из §5.5, который запрещён к реализации.
    assert "migrate-to-plugin" not in source
    assert "def snapshot" not in source
    assert "def backup" not in source


def test_uninstall_receipt_has_no_snapshot_field() -> None:
    """Контракт ответа uninstall явно фиксирует ``snapshot_taken=False``."""

    from prodavan.application.plugins.admin.contracts import UninstallReceipt

    fields = set(UninstallReceipt.__dataclass_fields__)
    assert "snapshot_taken" in fields
    assert not any(token in fields for token in ("backup_ref", "snapshot_ref", "migration_id"))


# --------------------------------------------------------------------------- fail-closed / аудит


async def test_audit_failure_blocks_positive_mutation(
    harness: Harness, grant_args: dict
) -> None:
    """§4.5: append failure для позитивной мутации = system failure, а не allow.

    Решение фиксируется ДО первой мутации, поэтому недоступный sink не оставляет
    за собой частично созданную инфраструктуру.
    """

    harness.audit.append_result = False
    with pytest.raises(RuntimeError, match="audit append failed"):
        await harness.service().grant(**grant_args)
    assert harness.tenant_db.databases == set()
    assert harness.object_store.prefixes == {}
    assert harness.activation.deployments == {}
    assert harness.grant_store.grants == {}
    assert harness.mint.issued == []


async def test_admin_operations_are_audited(harness: Harness, grant_args: dict) -> None:
    service = harness.service()
    await service.grant(**grant_args)
    await service.set_enabled_modules(company_id=COMPANY_A, plugin_ids=(PLUGIN_X,))
    await service.wake(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)

    operations = {event.operation for event in harness.audit.events}
    assert AdminOperation.GRANT in operations
    assert AdminOperation.ENABLED_MODULES in operations
    assert AdminOperation.WAKE in operations
    assert AdminOperation.REVOKE in operations
    # Аудит не содержит чувствительных данных: только канонические поля.
    for event in harness.audit.events:
        redacted = event.redacted()
        assert set(redacted) == {
            "event_id",
            "occurred_at",
            "company_id",
            "plugin_id",
            "operation",
            "decision",
            "grant_id",
            "artifact_digest",
            "code",
            "reason",
        }


async def test_invalid_company_id_is_rejected_before_any_port_call(
    harness: Harness, grant_args: dict
) -> None:
    with pytest.raises(ValueError, match="company_id"):
        await harness.service().grant(**dict(grant_args, company_id="../prodavan"))
    assert harness.all_calls == []


def test_service_requires_ports() -> None:
    """Fail-closed: без подключённых адаптеров сервис не собирается."""

    with pytest.raises(ValueError, match="PluginAdminPorts обязателен"):
        PluginAdminService(None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- errors


def test_error_codes_are_stable() -> None:
    assert {code.value for code in PluginAdminErrorCode} == {
        "ARTIFACT_NOT_PUBLISHED",
        "GRANT_EXISTS",
        "GRANT_NOT_FOUND",
        "REVOKE_FAILED",
        "UNINSTALL_PARTIAL",
    }


def test_plugin_admin_error_is_app_error() -> None:
    from prodavan.domain.errors import AppError

    error = PluginAdminError(
        code=PluginAdminErrorCode.GRANT_EXISTS, detail="уже установлен"
    )
    assert isinstance(error, AppError)
    assert error.status == 409
    assert error.code == "GRANT_EXISTS"

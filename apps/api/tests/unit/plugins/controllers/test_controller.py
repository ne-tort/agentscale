"""Unit-тесты ``PluginInfraController.sync()`` (fail-closed, идемпотентность).

Acceptance-критерии задачи:
  * sync читает активные grant'ы → рендерит 3 артефакта → patch → reload
    в порядке tenant-PG → PgBouncer → Envoy → audit;
  * совпадает с текущим /data → НЕ дёргать reload (идемпотентность);
  * revoke grant'а убирает пару/секрет/кластер и снова sync;
  * любая ошибка patch/reload → ``SyncError`` с указанием шага, БЕЗ «успеха»
    (caller не получает SyncReceipt);
  * audit не содержит секретов/DSN.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЛИСЬ (verification freeze, план миграции §0.2).
"""

from __future__ import annotations

import pytest

from prodavan.infrastructure.plugins.controllers.controller import (
    AUDIT_EVENT_SYNC,
    ControllerPorts,
    PluginInfraController,
)
from prodavan.infrastructure.plugins.controllers.errors import (
    STEP_AUDIT,
    STEP_ENVOY_PATCH,
    STEP_ENVOY_RELOAD,
    STEP_PGBOUNCER_RELOAD,
    STEP_TENANT_HBA_PATCH,
    STEP_TENANT_PG_RELOAD,
    STEP_USERLIST_PATCH,
    SyncError,
)
from prodavan.infrastructure.plugins.controllers.ports import (
    EGRESS_PROXY_CONFIGMAP,
    ENVOY_CONFIG_KEY,
    PGBOUNCER_CREDENTIALS_SECRET,
    PGBOUNCER_USERLIST_KEY,
    TENANT_HBA_KEY,
    TENANT_POSTGRES_CONFIGMAP,
)

from .conftest import (
    ADMIN_LINE,
    GRANT_A,
    GRANT_B,
    PLAINTEXT_PASSWORD,
    SCRAM_A,
    build_harness,
    grant,
)


# --------------------------------------------------------------------------- порядок


def test_sync_patches_and_reloads_in_required_order() -> None:
    """grant → patch/reload tenant-PG → PgBouncer → Envoy → audit (ровно этот порядок)."""
    harness = build_harness([GRANT_A])
    receipt = harness.controller.sync()

    assert receipt.grant_count == 1
    assert receipt.changed_steps == (
        STEP_TENANT_HBA_PATCH,
        STEP_USERLIST_PATCH,
        STEP_ENVOY_PATCH,
    )
    assert receipt.reloaded_steps == (
        STEP_TENANT_PG_RELOAD,
        STEP_PGBOUNCER_RELOAD,
        STEP_ENVOY_RELOAD,
    )
    assert harness.postgres.calls == 1
    assert harness.pgbouncer.calls == 1
    assert harness.envoy.calls == 1
    assert len(harness.audit.events) == 1
    assert harness.audit.events[0]["event"] == AUDIT_EVENT_SYNC

    # Порядок patch: tenant_hba → userlist → envoy (по журналам фейков).
    cm_patches = harness.configmaps.patches
    assert cm_patches[0][:2] == (TENANT_POSTGRES_CONFIGMAP, TENANT_HBA_KEY)
    assert cm_patches[1][:2] == (EGRESS_PROXY_CONFIGMAP, ENVOY_CONFIG_KEY)
    assert harness.secrets.patches[0][:2] == (
        PGBOUNCER_CREDENTIALS_SECRET,
        PGBOUNCER_USERLIST_KEY,
    )


def test_sync_writes_all_three_artifacts() -> None:
    """Все три /data записаны и соответствуют рендерам."""
    from prodavan.infrastructure.plugins.controllers.renderer import (
        build_envoy_policies,
        render_envoy_config,
        render_envoy_yaml,
        render_pgbouncer_userlist_preserving_admin,
        render_tenant_hba,
    )

    harness = build_harness([GRANT_A])
    harness.controller.sync()
    hba = harness.configmaps.data[TENANT_POSTGRES_CONFIGMAP][TENANT_HBA_KEY]
    userlist = harness.secrets.data[PGBOUNCER_CREDENTIALS_SECRET][PGBOUNCER_USERLIST_KEY]
    envoy = harness.configmaps.data[EGRESS_PROXY_CONFIGMAP][ENVOY_CONFIG_KEY]
    assert hba == render_tenant_hba([GRANT_A])
    assert userlist == render_pgbouncer_userlist_preserving_admin([GRANT_A], current=ADMIN_LINE)
    assert envoy == render_envoy_yaml(render_envoy_config(build_envoy_policies([GRANT_A])))
    assert ADMIN_LINE in userlist, "строка admin-консоли не должна быть потеряна"


# --------------------------------------------------------------------------- идемпотентность


def test_second_sync_without_changes_does_not_reload() -> None:
    """Повторный sync без изменений не вызывает НИ patch, НИ reload, НИ новый patch."""
    harness = build_harness([GRANT_A])
    first = harness.controller.sync()
    assert first.changed_steps

    second = harness.controller.sync()
    assert second.changed_steps == ()
    assert second.reloaded_steps == ()
    assert harness.postgres.calls == 1
    assert harness.pgbouncer.calls == 1
    assert harness.envoy.calls == 1
    assert len(harness.configmaps.patches) == 2  # ровно два ConfigMap-patch за первый sync
    assert len(harness.secrets.patches) == 1
    # Аудит всё равно пишется на каждый вызов (событие самого sync), но без
    # changed/reloaded шагов.
    assert len(harness.audit.events) == 2
    assert harness.audit.events[1]["changed_steps"] == ()


def test_sync_is_idempotent_after_restart() -> None:
    """Новый экземпляр контроллера на том же кластере не дёргает reload."""
    harness = build_harness([GRANT_A])
    harness.controller.sync()
    restarted = PluginInfraController(
        ControllerPorts(
            grants=harness.store,
            configmaps=harness.configmaps,
            secrets=harness.secrets,
            postgres=harness.postgres,
            pgbouncer=harness.pgbouncer,
            envoy=harness.envoy,
            audit=harness.audit,
        )
    )
    receipt = restarted.sync()
    assert receipt.changed_steps == ()
    assert harness.postgres.calls == 1


# --------------------------------------------------------------------------- revoke


def test_revoke_removes_pair_secret_and_cluster() -> None:
    """Отозванный grant исчезает из всех трёх артефактов и перезагружает компоненты."""
    harness = build_harness([GRANT_A, GRANT_B])
    harness.controller.sync()
    assert GRANT_A.role in harness.secrets.data[PGBOUNCER_CREDENTIALS_SECRET][PGBOUNCER_USERLIST_KEY]
    assert GRANT_B.role in harness.secrets.data[PGBOUNCER_CREDENTIALS_SECRET][PGBOUNCER_USERLIST_KEY]

    # revoke: адаптер больше не возвращает GRANT_B.
    harness.store.grants = [GRANT_A]
    receipt = harness.controller.sync()

    hba = harness.configmaps.data[TENANT_POSTGRES_CONFIGMAP][TENANT_HBA_KEY]
    userlist = harness.secrets.data[PGBOUNCER_CREDENTIALS_SECRET][PGBOUNCER_USERLIST_KEY]
    envoy = harness.configmaps.data[EGRESS_PROXY_CONFIGMAP][ENVOY_CONFIG_KEY]
    assert GRANT_B.role not in hba
    assert GRANT_B.database not in hba
    assert GRANT_B.role not in userlist
    assert SCRAM_A in userlist  # секрет оставшегося grant'а на месте
    assert "api.github.com" not in envoy
    assert "hooks.slack.com" in envoy
    assert ADMIN_LINE in userlist
    # Все три компонента перечитали урезанный конфиг.
    assert receipt.changed_steps == (
        STEP_TENANT_HBA_PATCH,
        STEP_USERLIST_PATCH,
        STEP_ENVOY_PATCH,
    )
    assert harness.postgres.calls == 2
    assert harness.pgbouncer.calls == 2
    assert harness.envoy.calls == 2


def test_empty_grants_sync_is_fail_closed_but_consistent() -> None:
    """Ноль активных grant'ов = пустые пары/секреты/кластеры + 403, без ошибок."""
    harness = build_harness([GRANT_A])
    harness.controller.sync()
    harness.store.grants = []
    receipt = harness.controller.sync()
    assert receipt.grant_count == 0
    envoy = harness.configmaps.data[EGRESS_PROXY_CONFIGMAP][ENVOY_CONFIG_KEY]
    assert "clusters: []" in envoy
    assert "403" in envoy
    hba = harness.configmaps.data[TENANT_POSTGRES_CONFIGMAP][TENANT_HBA_KEY]
    assert GRANT_A.role not in hba


# --------------------------------------------------------------------------- fail-closed


@pytest.mark.parametrize(
    ("fail_attr", "expected_step"),
    [
        ("configmaps", STEP_TENANT_HBA_PATCH),
        ("secrets", STEP_USERLIST_PATCH),
        ("postgres", STEP_TENANT_PG_RELOAD),
        ("pgbouncer", STEP_PGBOUNCER_RELOAD),
        ("envoy", STEP_ENVOY_RELOAD),
    ],
)
def test_any_patch_or_reload_failure_raises_sync_error(fail_attr: str, expected_step: str) -> None:
    """Ошибка ЛЮБОГО patch/reload → SyncError с шагом, БЕЗ SyncReceipt/«успеха»."""
    harness = build_harness([GRANT_A])
    fake = getattr(harness, fail_attr)
    if fail_attr in ("configmaps", "secrets"):
        name = (
            TENANT_POSTGRES_CONFIGMAP
            if fail_attr == "configmaps"
            else PGBOUNCER_CREDENTIALS_SECRET
        )
        fake.fail_on = name
    else:
        fake.fail = True

    with pytest.raises(SyncError) as exc_info:
        harness.controller.sync()
    assert exc_info.value.step == expected_step
    # Ни одного успешного audit-события «allow»: контроллер не вернул успех.
    assert not any(e.get("decision") == "allow" for e in harness.audit.events)
    # Ошибка записана в аудит как deny (диагностика без секретов).
    assert any(e.get("decision") == "deny" for e in harness.audit.events)


def test_audit_failure_raises_sync_error_not_partial_success() -> None:
    """Недоступный AuditPort = SyncError(STEP_AUDIT), а не квитанция без следа."""
    harness = build_harness([GRANT_A])
    harness.audit.fail = True
    with pytest.raises(SyncError) as exc_info:
        harness.controller.sync()
    assert exc_info.value.step == STEP_AUDIT
    assert exc_info.value.reason == "audit_failed"


def test_grant_store_failure_is_fail_closed() -> None:
    """Упавшее хранилище grant'ов → SyncError, а не «пустой allowlist == успех»."""
    harness = build_harness([GRANT_A])
    harness.store.fail = True
    with pytest.raises(SyncError) as exc_info:
        harness.controller.sync()
    assert exc_info.value.reason == "store_failed"
    assert harness.postgres.calls == 0  # ни одного reload до чтения данных


def test_inactive_grant_in_active_selection_is_contract_violation() -> None:
    """Адаптер вернул отозванный grant → явный отказ, а не молчаливый фильтр."""
    harness = build_harness([GRANT_A])
    harness.store.grants = [
        grant(grant_id="g_dead", company="co_dead", plugin="mod_dead", active=False)
    ]
    with pytest.raises(SyncError) as exc_info:
        harness.controller.sync()
    assert exc_info.value.reason == "store_contract_violation"
    assert harness.postgres.calls == 0


def test_plaintext_secret_in_store_is_rejected_before_any_patch() -> None:
    """Plaintext-пароль в grant'е → RenderError до ЛЮБОГО patch (fail-closed)."""
    harness = build_harness(
        [grant(grant_id="g_plain", company="co_p", plugin="mod_p", scram=PLAINTEXT_PASSWORD)]
    )
    with pytest.raises(SyncError) as exc_info:
        harness.controller.sync()
    assert exc_info.value.step in (STEP_TENANT_HBA_PATCH, STEP_USERLIST_PATCH)
    assert PLAINTEXT_PASSWORD not in repr(exc_info.value)
    assert harness.postgres.calls == 0
    assert harness.configmaps.patches == []


def test_missing_port_is_adapter_missing_error() -> None:
    """Собрать «полуподключённый» контроллер нельзя (правило §0 плана)."""
    from .conftest import FakeAudit, FakeConfigMap, FakeEnvoy, FakeGrantStore, FakePgBouncer, FakePostgres, FakeSecret

    with pytest.raises(SyncError) as exc_info:
        ControllerPorts(
            grants=FakeGrantStore(),
            configmaps=FakeConfigMap(),
            secrets=FakeSecret(),
            postgres=FakePostgres(),
            pgbouncer=FakePgBouncer(),
            envoy=FakeEnvoy(),
            audit=None,  # type: ignore[arg-type]
        )
    assert exc_info.value.reason == "adapter_missing"


def test_sync_error_never_contains_secret_material() -> None:
    """Текст SyncError не содержит SCRAM/plaintext/DSN даже при падении адаптера."""
    harness = build_harness([GRANT_A])
    harness.pgbouncer.fail = True
    with pytest.raises(SyncError) as exc_info:
        harness.controller.sync()
    rendered = repr(exc_info.value) + str(exc_info.value)
    for forbidden in (SCRAM_A, PLAINTEXT_PASSWORD, "postgresql://", "pgbouncer-admin-change-me"):
        assert forbidden not in rendered


# --------------------------------------------------------------------------- аудит


def test_audit_has_no_secrets_or_dsn() -> None:
    """Audit-события содержат только канонические поля; секретов/DSN нет."""
    harness = build_harness([GRANT_A, GRANT_B])
    receipt = harness.controller.sync()
    for event in (*harness.audit.events, *receipt.audit_events):
        text = repr(event)
        for forbidden in (
            SCRAM_A,
            PLAINTEXT_PASSWORD,
            "postgresql://",
            "dsn",
            "SCRAM-SHA-256",
        ):
            assert forbidden not in text, f"audit утёк: {forbidden}"
        assert event.get("decision") == "allow"
        assert event.get("grant_count") == 2
        assert "objects" in event and "keys" in event
        # В аудите только ИМЕНА ключей, не содержимое артефактов.
        assert "userlist.txt" in event["keys"]
        assert "reject" not in text


def test_receipt_has_no_secret_material() -> None:
    """SyncReceipt безопасен для логов: имён и счётчиков достаточно."""
    harness = build_harness([GRANT_A])
    receipt = harness.controller.sync()
    text = repr(receipt)
    assert SCRAM_A not in text
    assert PLAINTEXT_PASSWORD not in text
    assert "postgresql://" not in text
    assert receipt.grant_count == 1

"""In-memory fakes портов контроллера синхронизации (unit-тесты).

НИКАКИХ реальных k8s / PostgreSQL / PgBouncer / Envoy / сети. Все адаптеры
эмулируются в памяти и ведут журнал вызовов, чтобы тесты могли проверить:

* порядок patch → reload (tenant-PG → PgBouncer → Envoy → audit);
* идемпотентность (повторный sync без изменений не дёргает reload);
* fail-closed (ошибка любого шага → SyncError, без «успеха»);
* дебаунс (5 событий → 1 sync);
* отсутствие секретов/DSN в аудите.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЛИСЬ (verification freeze, план миграции §0.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prodavan.infrastructure.plugins.controllers.controller import ControllerPorts
from prodavan.infrastructure.plugins.controllers.ports import (
    EGRESS_PROXY_CONFIGMAP,
    ENVOY_CONFIG_KEY,
    PGBOUNCER_CREDENTIALS_SECRET,
    PGBOUNCER_USERLIST_KEY,
    TENANT_HBA_KEY,
    TENANT_POSTGRES_CONFIGMAP,
    ActiveGrant,
    GrantChangeEvent,
)

# ---------------------------------------------------------------------------
# Тестовые данные
# ---------------------------------------------------------------------------

#: Валидный SCRAM-секрет в формате PgBouncer (base64-подобные поля).
SCRAM_A = "SCRAM-SHA-256$4096:c2FsdA==$c3RvcmVk:c2VydmVy"
SCRAM_B = "SCRAM-SHA-256$4096:c2FsdGI=$c3RvcmVkYg==:c2VydmVyYg=="
PLAINTEXT_PASSWORD = "super-secret-plaintext-DO-NOT-LOG"

#: Стартовое содержимое userlist.txt из pgbouncer.yaml:50. Админская строка
#: ОБЯЗАНА сохраниться, иначе RELOAD; станет недоступен.
ADMIN_LINE = '"pgbouncer_admin" "pgbouncer-admin-change-me"'
START_USERLIST = f"{ADMIN_LINE}\n# Роли плагинов генерирует задача 05 при grant.\n"

#: Стартовый fail-closed envoy.yaml (упрощённая строка для «до первого sync»).
START_ENVOY = "# стартовый clusters: [] -> 403\n"

GRANT_A = ActiveGrant(
    grant_id="grant_a",
    company_id="co_123",
    plugin_id="mod_equipment",
    role="tenant_co_123_plugin_mod_equipment",
    database="tenant_co_123",
    scram_secret=SCRAM_A,
    egress={
        "mode": "webhooks-only",
        "allowed_hosts": ["hooks.slack.com"],
        "allowed_paths": ["/services"],
        "max_egress_bytes": 1024,
    },
)

GRANT_B = ActiveGrant(
    grant_id="grant_b",
    company_id="co_456",
    plugin_id="mod_invoices",
    role="tenant_co_456_plugin_mod_invoices",
    database="tenant_co_456",
    scram_secret=SCRAM_B,
    egress={
        "mode": "webhooks-only",
        "allowed_hosts": ["api.github.com"],
        "allowed_paths": ["/repos"],
        "max_egress_bytes": 2048,
    },
)


def grant(*, grant_id: str, company: str, plugin: str, scram: str = SCRAM_A, **kw: Any) -> ActiveGrant:
    """Собрать ActiveGrant с нормализованными именами."""
    return ActiveGrant(
        grant_id=grant_id,
        company_id=company,
        plugin_id=plugin,
        role=f"tenant_{company}_plugin_{plugin}",
        database=f"tenant_{company}",
        scram_secret=scram,
        egress=kw.get("egress"),
        active=kw.get("active", True),
    )


# ---------------------------------------------------------------------------
# Фейки /data
# ---------------------------------------------------------------------------


@dataclass
class FakeConfigMap:
    """ConfigMap /data в памяти + журнал patch'ей."""

    data: dict[str, dict[str, str]] = field(default_factory=dict)
    patches: list[tuple[str, str, str]] = field(default_factory=list)
    fail_on: str | None = None

    def read_data(self, *, name: str) -> dict[str, str]:
        return dict(self.data.get(name, {}))

    def patch_data(self, *, name: str, key: str, value: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"simulated ConfigMap patch failure: {name}")
        self.patches.append((name, key, value))
        self.data.setdefault(name, {})[key] = value


@dataclass
class FakeSecret:
    """Secret /data в памяти (значения УЖЕ раскодированы, как требует порт)."""

    data: dict[str, dict[str, str]] = field(default_factory=dict)
    patches: list[tuple[str, str, str]] = field(default_factory=list)
    fail_on: str | None = None

    def read_data(self, *, name: str) -> dict[str, str]:
        return dict(self.data.get(name, {}))

    def patch_data(self, *, name: str, key: str, value: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"simulated Secret patch failure: {name}")
        self.patches.append((name, key, value))
        self.data.setdefault(name, {})[key] = value


# ---------------------------------------------------------------------------
# Фейки reload
# ---------------------------------------------------------------------------


@dataclass
class FakePostgres:
    calls: int = 0
    fail: bool = False

    def pg_reload_conf(self) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated pg_reload_conf failure")


@dataclass
class FakePgBouncer:
    calls: int = 0
    fail: bool = False

    def reload(self) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated PgBouncer RELOAD failure")


@dataclass
class FakeEnvoy:
    calls: int = 0
    fail: bool = False

    def hot_reload(self) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated Envoy hot_reload failure")


# ---------------------------------------------------------------------------
# Фейки store / audit / clock / events
# ---------------------------------------------------------------------------


@dataclass
class FakeGrantStore:
    grants: list[ActiveGrant] = field(default_factory=list)
    fail: bool = False

    def list_active_grants(self) -> list[ActiveGrant]:
        if self.fail:
            raise RuntimeError("simulated grant store failure")
        return list(self.grants)


@dataclass
class FakeAudit:
    events: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False

    def record(self, event: Any) -> None:
        if self.fail:
            raise RuntimeError("simulated audit failure (must NOT be logged)")
        self.events.append(dict(event))


@dataclass
class FakeEventPort:
    queue: list[GrantChangeEvent] = field(default_factory=list)
    fail: bool = False

    def poll(self) -> list[GrantChangeEvent]:
        if self.fail:
            raise RuntimeError("simulated event poll failure")
        batch, self.queue = self.queue, []
        return batch


@dataclass
class FakeClock:
    """Монотонные часы с ручным управлением (никакого реального sleep)."""

    t: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def advance(self, seconds: float) -> None:
        self.t += seconds


@dataclass
class FakeNow:
    """Фабрика timezone-aware datetime для SyncReceipt/audit."""

    value: datetime = datetime(2026, 11, 8, 6, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


# ---------------------------------------------------------------------------
# Сборка harness
# ---------------------------------------------------------------------------


@dataclass
class Harness:
    """Все фейки + контроллер, готовые к ``controller.sync()``."""

    configmaps: FakeConfigMap
    secrets: FakeSecret
    postgres: FakePostgres
    pgbouncer: FakePgBouncer
    envoy: FakeEnvoy
    store: FakeGrantStore
    audit: FakeAudit
    controller: Any

    @property
    def call_log(self) -> list[str]:
        """Имена шагов в порядке выполнения (patch → reload → audit)."""
        log: list[str] = []
        for name, key, _ in self.configmaps.patches:
            log.append(f"patch:{name}:{key}")
        for name, key, _ in self.secrets.patches:
            log.append(f"patch:{name}:{key}")
        for _ in range(self.postgres.calls):
            log.append("reload:tenant_pg")
        for _ in range(self.pgbouncer.calls):
            log.append("reload:pgbouncer")
        for _ in range(self.envoy.calls):
            log.append("reload:envoy")
        for _ in self.audit.events:
            log.append("audit")
        return log


def build_harness(
    grants: list[ActiveGrant] | None = None,
    *,
    start_userlist: str = START_USERLIST,
    start_envoy: str = START_ENVOY,
    start_hba: str = "",
) -> Harness:
    """Собрать контроллер на стартовом (fail-closed) состоянии кластера."""
    configmaps = FakeConfigMap(
        data={
            TENANT_POSTGRES_CONFIGMAP: {TENANT_HBA_KEY: start_hba},
            EGRESS_PROXY_CONFIGMAP: {ENVOY_CONFIG_KEY: start_envoy},
        }
    )
    secrets = FakeSecret(
        data={PGBOUNCER_CREDENTIALS_SECRET: {PGBOUNCER_USERLIST_KEY: start_userlist}}
    )
    postgres = FakePostgres()
    pgbouncer = FakePgBouncer()
    envoy = FakeEnvoy()
    store = FakeGrantStore(grants=list(grants or []))
    audit = FakeAudit()
    from prodavan.infrastructure.plugins.controllers.controller import PluginInfraController

    controller = PluginInfraController(
        ControllerPorts(
            grants=store,
            configmaps=configmaps,
            secrets=secrets,
            postgres=postgres,
            pgbouncer=pgbouncer,
            envoy=envoy,
            audit=audit,
        ),
        now=FakeNow(),
    )
    return Harness(
        configmaps=configmaps,
        secrets=secrets,
        postgres=postgres,
        pgbouncer=pgbouncer,
        envoy=envoy,
        store=store,
        audit=audit,
        controller=controller,
    )

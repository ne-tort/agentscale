"""Порты контроллера синхронизации controller-managed данных плагинов.

Проблема (отчёты задач 14 / 05 / 08)
------------------------------------
Argo CD ``selfHeal`` с ``ignoreDifferences: /data`` (см.
``infra/argocd/apps/plugin-platform.yaml``) НЕ стирает три объекта, которые
контроллер правит ВНЕ Git при grant/revoke:

* ``ConfigMap tenant-postgres-config``, ключ ``tenant_hba.conf`` — пары
  ``(role, tenant_<company>)``; после записи нужен ``pg_reload_conf()``;
* ``Secret pgbouncer-credentials``, ключ ``userlist.txt`` — SCRAM-секреты
  ролей; после записи нужен ``RELOAD;`` на admin-консоли PgBouncer
  (**НЕ SIGHUP** — PgBouncer читает ``auth_file`` по ``RELOAD``);
* ``ConfigMap egress-proxy-config``, ключ ``envoy.yaml`` — Envoy-кластеры из
  ``CapabilityGrant`` (задача 08); после записи нужен hot-reload Envoy.

Без контроллера выданный grant НЕ вступит в силу: ``pg_hba`` останется пустым,
PgBouncer не узнает пароль роли, Envoy продолжит отвечать ``403`` из стартового
``clusters: []``.

Здесь ТОЛЬКО контракты (``Protocol``) и транспортно-нейтральные типы данных.
Реализации адаптеров (kubernetes client, psycopg/asyncpg, TCP-консоль PgBouncer,
Envoy admin / filesystem watch) подключаются родителем в F11. Ни один порт не
имеет «тихого» фолбэка: отсутствие адаптера = ``SyncError`` (fail-closed).

Синхронные контракты выбраны намеренно: контроллер шагает по СТРОГОМУ порядку
patch → reload (tenant-PG → PgBouncer → Envoy) и должен видеть результат каждого
шага до перехода к следующему. Асинхронный «fire-and-forget» размыл бы границу
«частичного успеха», которая здесь запрещена.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

# ---------------------------------------------------------------------------
# Имена controller-managed объектов (совпадают с infra/k3s/base/plugins/*.yaml
# и ignoreDifferences в infra/argocd/apps/plugin-platform.yaml).
# ---------------------------------------------------------------------------

TENANT_POSTGRES_CONFIGMAP = "tenant-postgres-config"
TENANT_HBA_KEY = "tenant_hba.conf"
PGBOUNCER_CREDENTIALS_SECRET = "pgbouncer-credentials"
PGBOUNCER_USERLIST_KEY = "userlist.txt"
EGRESS_PROXY_CONFIGMAP = "egress-proxy-config"
ENVOY_CONFIG_KEY = "envoy.yaml"

#: Сети pod/service k3s из tenant-postgres.yaml (``pg_hba.conf``). Пары
#: (role, tenant_<company>) генерируются для КАЖДОЙ сети, чтобы плагин мог
#: подключиться и через PgBouncer, и напрямую к tenant-postgres :5432.
POD_CIDRS: tuple[str, ...] = ("10.42.0.0/16", "10.43.0.0/16")

#: Роль admin-консоли PgBouncer. НЕ роль tenant-БД и НЕ роль плагина
#: (pgbouncer.yaml:124,125). Контроллер NEVER пишет её SCRAM-секрет в рендер.
PGBOUNCER_ADMIN_USER = "pgbouncer_admin"


# ---------------------------------------------------------------------------
# Доменный тип активного grant'а
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActiveGrant:
    """Один АКТИВНЫЙ ``CapabilityGrant`` для рендеринга трёх артефактов.

    Transport-neutral: адаптер ``GrantStorePort`` приводит записи из БД
    (``CapabilityGrantRecord`` задачи 12, ORM-модель или dict) к этому типу.
    В контроллер попадают ТОЛЬКО активные grant'ы: отозванный grant не должен
    оставить за собой пару в ``pg_hba``, секрет в ``userlist.txt`` или кластер
    в Envoy (иначе revoke не снимет доступ).

    Поля:
      * ``grant_id``        — стабильный идентификатор (§4.2, только ``grant_id``
        несёт короткий токен);
      * ``company_id`` / ``plugin_id`` — для имён БД/роли (``naming.py`` задачи 05);
      * ``role``            — УЖЕ нормализованная LOGIN-роль плагина
        ``tenant_<company>_plugin_<plugin>``;
      * ``database``        — УЖЕ нормализованная БД ``tenant_<company>``;
      * ``scram_secret``    — SCRAM-секрет роли в формате PgBouncer
        ``SCRAM-SHA-256$<iterations>:<salt>$<stored>:<server>``;
        **НЕ plaintext-пароль**;
      * ``egress``          — сырая секция egress из grant (dict/dataclass/объект),
        которую ``resolve_policy()`` задачи 08 разберёт сама;
      * ``active``          — явный признак неотозванного grant'а. Адаптер
        ``GrantStorePort`` обязан вернуть только активные, но контроллер
        ДОПОЛНИТЕЛЬНО фильтрует по этому полю (defense in depth: revoke не
        должен оставить за собой пару/секрет/кластер).

    ``__repr__`` намеренно скрывает ``scram_secret``: объект может случайно
    попасть в лог/audit/исключение, а секрет туда попадать НЕ должен.
    """

    grant_id: str
    company_id: str
    plugin_id: str
    role: str
    database: str
    scram_secret: str
    egress: Any = None
    active: bool = True

    def __repr__(self) -> str:  # pragma: no cover - защитный контракт
        return (
            f"ActiveGrant(grant_id={self.grant_id!r}, company_id={self.company_id!r}, "
            f"plugin_id={self.plugin_id!r}, role={self.role!r}, database={self.database!r}, "
            f"scram_secret=<redacted>, egress=<omitted>)"
        )

    __str__ = __repr__

    @property
    def safe_dict(self) -> dict[str, str]:
        """Безопасное представление для аудита: без SCRAM-секрета и без egress."""
        return {
            "grant_id": self.grant_id,
            "company_id": self.company_id,
            "plugin_id": self.plugin_id,
            "role": self.role,
            "database": self.database,
        }


# ---------------------------------------------------------------------------
# Порты чтения/записи /data (ConfigMap и Secret)
# ---------------------------------------------------------------------------


@runtime_checkable
class ConfigMapPort(Protocol):
    """Чтение и точечный patch ключа ``/data`` ConfigMap'а.

    Адаптер обязан:
      * вернуть ``dict`` текущих строковых значений ``/data`` (в k8s ConfigMap
        значения — plain ``str``, НЕ base64);
      * ``patch`` — именно PATCH по ОДНОМУ ключу, а не полная замена объекта:
        соседние ключи (``pg_hba.conf``, ``pg_ident.conf``) и ``metadata``
        контроллер не трогает;
      * при отсутствии объекта/ключа, конфликте ``resourceVersion``, 403/409/500
        или недоступности API — поднять исключение, а не вернуть «пусто».
        Пустой dict вместо ошибки привёл бы к молчаливому revoke всех пар.
    """

    def read_data(self, *, name: str) -> dict[str, str]: ...

    def patch_data(self, *, name: str, key: str, value: str) -> None:
        """Записать ОДИН ключ ``/data``. Успех = отсутствие исключения."""
        ...


@runtime_checkable
class SecretPort(Protocol):
    """Чтение и точечный patch ключа ``/data`` Secret'а.

    ВАЖНО: в Kubernetes API ``Secret.data`` — **base64** от байтов значения.
    Адаптер сам кодирует/декодирует: наружу и внутрь порт отдаёт/plain-строку
    ``userlist.txt``. Контроллер НЕ должен знать про base64 и НЕ должен логировать
    содержимое ``data``.

    Тот же fail-closed контракт, что у ``ConfigMapPort``: никаких «пустых»
    значений вместо ошибки, иначе отказ Secret'а выглядел бы как «у всех ролей
    отозвали пароли» и контроллер записал бы пустой userlist.
    """

    def read_data(self, *, name: str) -> dict[str, str]:
        """Вернуть /data с РАСКОДИРОВАННЫМИ строковыми значениями."""
        ...

    def patch_data(self, *, name: str, key: str, value: str) -> None:
        """Записать ОДИН ключ /data (адаптер кодирует в base64)."""
        ...


# ---------------------------------------------------------------------------
# Порты reload (строго по порядку tenant-PG → PgBouncer → Envoy)
# ---------------------------------------------------------------------------


@runtime_checkable
class PostgresReloadPort(Protocol):
    """``SELECT pg_reload_conf()`` на tenant-кластере PostgreSQL.

    ``pg_hba.conf`` смонтирован КАТАЛОГОМ без ``subPath`` (tenant-postgres.yaml),
    поэтому kubelet проецирует обновление ConfigMap, но PostgreSQL НЕ перечитывает
    ``hba_file`` сам: нужен явный ``pg_reload_conf()``. Адаптер выполняет запрос
    через отдельное админское соединение (НЕ роль плагина, НЕ ``prodavan``).

    НЕ ``SIGHUP``-process и НЕ рестарт пода: это разорвало бы активные сессии.
    """

    def pg_reload_conf(self) -> None:
        """Успех = отсутствие исключения. Ошибка = ``SyncError`` на шаге reload."""
        ...


@runtime_checkable
class PgBouncerReloadPort(Protocol):
    """``RELOAD;`` на admin-консоли PgBouncer (порт 6432, **НЕ SIGHUP**).

    PgBouncer читает ``auth_file`` (``userlist.txt``) при старте и по команде
    ``RELOAD`` админской консоли; SIGHUP НЕ перечитывает список пользователей
    (см. ``infra/k3s/base/plugins/pgbouncer.yaml:39-41,121-123``). Консоль
    доступна только пользователю ``pgbouncer_admin`` из userlist, отдельного
    Service для неё НЕТ — адаптер подключается по TCP к ``pgbouncer:6432`` как
    к БД ``pgbouncer``.

    Адаптер НЕ должен логировать строку подключения/пароль консоли: в аудите
    контроллера остаётся только имя шага.
    """

    def reload(self) -> None:
        """Выполнить ``RELOAD;``. Успех = отсутствие исключения."""
        ...


@runtime_checkable
class EnvoyReloadPort(Protocol):
    """Hot-reload Envoy после правки ``egress-proxy-config/envoy.yaml``.

    Стартовый ConfigMap — fail-closed ``clusters: []`` + ``direct_response 403``
    (``infra/k3s/base/plugins/egress-proxy.yaml``). Обновление ConfigMap
    проецируется kubelet'ом (монтирование каталогом без subPath), но статический
    ``-c /etc/envoy/envoy.yaml`` сам НЕ перечитывается. Поддержано два штатных
    способа (выбор — за адаптером, см. риски в отчёте родителю):

    1. admin ``POST /reload`` (или ``/ready``-polling до подтверждения, что новый
       конфиг загружен) — admin-консоль слушает ``0.0.0.0:9901`` (НЕ
       ``127.0.0.1``!). Kubelet проверяет ``/ready`` по ``<PodIP>:9901`` с узла,
       поэтому loopback-биндинг делал под вечно 0/1; снаружи ns порт закрыт
       NetworkPolicy и НЕ экспортируется Service (только :8080);
    2. filesystem watch + CDS: контроллер пишет конфиг, sidecar/watcher дёргает
       Envoy CDS; ``/ready`` подтверждает готовность.

    xDS-сервера в v1 НЕТ (whitelist kinds, 45 подов) — риск зафиксирован.
    Адаптер ОБЯЗАН подтвердить применение (не «отправили и забыли»): до
    подтверждения контроллер не имеет права записать аудит «успех».
    """

    def hot_reload(self) -> None:
        """Применить новый конфиг и ДОЖДАТЬСЯ подтверждения (/ready)."""
        ...


# ---------------------------------------------------------------------------
# Порты источников и observability
# ---------------------------------------------------------------------------


@runtime_checkable
class GrantStorePort(Protocol):
    """Источник АКТИВНЫХ ``CapabilityGrant`` (реализация — в БД, задача 12).

    Ключевое требование: метод возвращает ТОЛЬКО активные (неотозванные,
    неистёкшие) grant'ы. Отозванный grant должен ИСЧЕЗНУТЬ из выборки — тогда
    следующий ``sync()`` уберёт его пару/секрет/кластер. Если адаптер вернёт
    отозванные записи, контроллер отфильтрует их дополнительно по ``active``.

    ``scram_secret`` приходит УЖЕ в формате SCRAM: контроллер НЕ генерирует и
    НЕ хранит plaintext-пароль и НЕ вычисляет SCRAM из пароля (это зона задачи 05).
    """

    def list_active_grants(self) -> Sequence[ActiveGrant]: ...


@runtime_checkable
class GrantEventPort(Protocol):
    """Подписка на изменения ``CapabilityGrant`` (grant / revoke / ротация).

    Адаптер (БД-триггер → outbox, Kafka ``agentscale.plugin.events``, k8s watch
    или in-process шина) отдаёт события в ``poll()``. Контроллер НЕ делает
    сетевых вызовов в ``sync()``: watcher сам решает, когда дёрнуть sync.
    """

    def poll(self) -> Sequence[GrantChangeEvent]:
        """Забрать накопленные события (можно пустой список, но не ``None``)."""
        ...


@dataclass(frozen=True, slots=True)
class GrantChangeEvent:
    """Одно изменение grant'а для ``GrantChangeWatcher``.

    ``kind`` — стабильная строка (``grant`` / ``revoke`` / ``rotate``); содержимое
    события НЕ содержит SCRAM-секретов, DSN и plaintext-паролей: дебаунсу нужен
    только факт изменения и (опционально) ``grant_id`` для аудита.
    """

    kind: str
    grant_id: str = ""
    occurred_at: datetime | None = None


@runtime_checkable
class ClockPort(Protocol):
    """Монотонное время для дебаунса (инъецируется в тестах)."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None:
        """Синхронная пауза; в проде — ``time.sleep`` в выделенном потоке."""
        ...


@runtime_checkable
class AuditPort(Protocol):
    """Append-only аудит синхронизации (§4.5, без секретов/DSN).

    Контракт намеренно узкий: контроллер передаёт УЖЕ отредактированное событие.
    В ``event`` ЗАПРЕЩЕНЫ SCRAM-секреты, plaintext-пароли, DSN, содержимое
    ``userlist.txt`` / ``envoy.yaml`` / ``tenant_hba.conf`` и тексты исключений
    адаптеров (в них теоретически может быть строка подключения). Разрешены:
    имя события, шаг, список имён объектов/ключей, счётчики, коды решения.

    Ошибка записи аудита = ``SyncError`` (fail-closed): «успех без следа» здесь
    так же недопустим, как «частичный успех».
    """

    def record(self, event: Mapping[str, Any]) -> None: ...


# ---------------------------------------------------------------------------
# Вспомогательная структура результата (используется controller.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyncReceipt:
    """Квитанция успешного ``sync()``. НЕ содержит секретов/DSN/сырых данных.

    ``changed_steps`` — шаги, где содержимое действительно отличалось и был
    выполнен patch; ``reloaded_steps`` — шаги, где после patch вызван reload.
    При повторном sync без изменений оба кортежа ПУСТЫЕ: ни patch, ни reload
    не дёргаются (идемпотентность). ``audit_events`` — безопасные события,
    ушедшие в AuditPort (без secret-материала).
    """

    grant_count: int
    changed_steps: tuple[str, ...] = ()
    reloaded_steps: tuple[str, ...] = ()
    audit_events: tuple[Mapping[str, Any], ...] = ()
    occurred_at: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "TENANT_POSTGRES_CONFIGMAP",
    "TENANT_HBA_KEY",
    "PGBOUNCER_CREDENTIALS_SECRET",
    "PGBOUNCER_USERLIST_KEY",
    "EGRESS_PROXY_CONFIGMAP",
    "ENVOY_CONFIG_KEY",
    "POD_CIDRS",
    "PGBOUNCER_ADMIN_USER",
    "ActiveGrant",
    "ConfigMapPort",
    "SecretPort",
    "PostgresReloadPort",
    "PgBouncerReloadPort",
    "EnvoyReloadPort",
    "GrantStorePort",
    "GrantEventPort",
    "GrantChangeEvent",
    "ClockPort",
    "AuditPort",
    "SyncReceipt",
]

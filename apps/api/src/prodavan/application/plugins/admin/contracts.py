"""Типизированные контракты Admin API lifecycle плагинов (F3-admin, задача 12).

Контракты намеренно не зависят от FastAPI, Pydantic и БД: слой приложения говорит
только на собственных неизменяемых типах, а HTTP-слой маппит их в Pydantic-модели.
Все значения проходят валидацию здесь, до обращения к любому порту: это и есть
fail-closed — неполный/неизвестный/противоречивый запрос не доходит до инфраструктуры.

Опорные точки дизайна v4:
  * §4.2 — короткий ``PluginClaims`` несёт только ``grant_id`` и TTL; egress-политика
    и квоты живут в долгоживущем ``CapabilityGrant`` в БД, а не в токене;
  * §4.4 — единый реестр capability / quota / event;
  * §5.5 / §13.3 — grant / revoke / uninstall / wake, ``migrate-to-plugin`` исключён;
  * §8 (этап 2) — strangler через фича-флаг ``plugin_enabled_modules[]``;
  * §11 (критерии 12, 15, 16) — отзыв по ``grant_id``, изоляция компаний, отсутствие
    переноса данных при переключении пути.

ВАЖНО: данные dev эфемерны. Ни один контракт не описывает snapshot, бэкап, dual-read,
dual-write или перенос данных — по §5.5, §8 и §13.1 они не требуются.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final


# --------------------------------------------------------------------------- реестры


#: Единый реестр capability из §4.4 (плюс ``db.direct`` / ``db.migrations`` из §2.3).
#: Расширять только вместе с документом архитектуры: неизвестное имя = отказ.
ALLOWED_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "db.direct",
        "db.migrations",
        "cache",
        "objects",
        "docs",
        "events",
        "jobs",
        "pods",
        "mcp",
        "secrets",
        "network",
    }
)

#: Ключи квот из §4.4. Ноль запрещён: адаптер может трактовать 0 как «без лимита».
QUOTA_KEYS: Final[tuple[str, ...]] = (
    "ops_per_minute",
    "max_rows",
    "max_bytes",
    "max_keys",
    "max_object_bytes",
    "max_egress_bytes",
)

#: TTL короткого ``PluginClaims`` из §4.2 (300 с). Egress/квоты в токен не кладутся.
PLUGIN_CLAIMS_TTL_SECONDS: Final[int] = 300

#: Префиксы, зафиксированные в §2.1 / §5.4. Используются и сервисом, и адаптерами.
TENANT_DATABASE_PREFIX: Final[str] = "tenant_"
TENANT_BUCKET_PREFIX: Final[str] = "tenant-"
PLUGIN_SCHEMA_PREFIX: Final[str] = "module_"
PLUGIN_OBJECT_PREFIX: Final[str] = "module-"


# --------------------------------------------------------------------------- перечисления


class EgressMode(StrEnum):
    """Регулируемый egress из §6.3. Источник правил — grant в БД, не JWT."""

    NONE = "none"
    WEBHOOKS_ONLY = "webhooks-only"
    FULL = "full"


class ArtifactPublicationStatus(StrEnum):
    """Состояние артефакта в реестре (§5.1, §11 критерий 18)."""

    PUBLISHED = "published"
    QUARANTINED = "quarantined"


class GrantStatus(StrEnum):
    """Состояние долгоживущего ``CapabilityGrant`` (§11 критерий 12)."""

    ACTIVE = "active"
    REVOKED = "revoked"


class AdminOperation(StrEnum):
    """Имена audit-событий (§4.4, §4.5)."""

    GRANT = "plugin_grant"
    REVOKE = "plugin_revoke"
    UNINSTALL = "plugin_uninstall"
    WAKE = "plugin_wake_up"
    ENABLED_MODULES = "plugin_enabled_modules_changed"
    LIST = "plugin_admin_list"


class AdminDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    SYSTEM_FAILURE = "system_failure"


class PluginRuntimeStatus(StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    SUSPENDED = "suspended"
    ABSENT = "absent"


# --------------------------------------------------------------------------- политики


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """Egress-политика, хранимая в ``CapabilityGrant`` (§6.1, §6.3, §13.3).

    ``none`` не должен содержать хостов: иначе адаптер может неявно открыть egress.
    ``full`` допустим только при явном approval. Валидация здесь — до записи в grant.
    """

    mode: EgressMode = EgressMode.NONE
    allowed_hosts: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    approved_for_full: bool = False

    def __post_init__(self) -> None:
        for value in (*self.allowed_hosts, *self.allowed_paths):
            if not value.strip():
                raise ValueError("egress: allowed_hosts/allowed_paths не должны содержать пустых значений")
        if self.mode is EgressMode.NONE and (self.allowed_hosts or self.allowed_paths):
            raise ValueError("egress mode=none не допускает allowed_hosts/allowed_paths")
        if self.mode is EgressMode.WEBHOOKS_ONLY:
            if not self.allowed_hosts or not self.allowed_paths:
                raise ValueError("egress mode=webhooks-only требует allowed_hosts и allowed_paths")
        if self.mode is EgressMode.FULL and not self.approved_for_full:
            raise ValueError("egress mode=full требует явного approval (§6.3)")


@dataclass(frozen=True, slots=True)
class QuotaSet:
    """Жёсткие квоты из §4.4, привязанные к (company, instance, digest)."""

    ops_per_minute: int = 60
    max_rows: int = 10_000
    max_bytes: int = 10_000_000
    max_keys: int = 1_000
    max_object_bytes: int = 50_000_000
    max_egress_bytes: int = 10_000_000

    def __post_init__(self) -> None:
        # 0 запрещён намеренно: некоторые адаптеры читают его как «лимит не задан»,
        # а это ровно то, чего fail-closed допустить не может.
        for name in QUOTA_KEYS:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"quota {name} должна быть целым числом >= 1")


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    """Типизированный набор capability + egress-политика + квоты.

    Передаётся одним аргументом ``capabilities`` в ``PluginAdminService.grant``:
    grant обязан быть долгоживущим и содержать и egress, и квоты (§4.2, §5.5).
    """

    capabilities: tuple[str, ...]
    egress_policy: EgressPolicy = field(default_factory=EgressPolicy)
    quotas: QuotaSet = field(default_factory=QuotaSet)

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise ValueError("список capability не должен быть пустым")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capability не должны повторяться")
        unknown = sorted(set(self.capabilities) - ALLOWED_CAPABILITIES)
        if unknown:
            # Fail-closed: неизвестное capability не «тихо игнорируется», а отклоняется.
            raise ValueError(f"неизвестные capability (§4.4): {', '.join(unknown)}")


# --------------------------------------------------------------------------- реестр артефактов


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """Запись реестра артефактов (задача 07, доменная модель ``AppVersion``).

    Только статус ``PUBLISHED`` разрешает установку. Всё остальное, включая
    ``QUARANTINED`` и отсутствие записи, трактуется как «не опубликовано».
    """

    plugin_id: str
    manifest_version: str
    artifact_digest: str
    status: ArtifactPublicationStatus
    published_at: datetime | None = None

    @property
    def is_published(self) -> bool:
        return self.status is ArtifactPublicationStatus.PUBLISHED


# --------------------------------------------------------------------------- инфраструктура компании


@dataclass(frozen=True, slots=True)
class TenantDatabase:
    """БД компании ``tenant_<company_id>`` (§2.1). DSN — только ``secret_ref``."""

    company_id: str
    database_name: str
    dsn_ref: str
    created: bool


@dataclass(frozen=True, slots=True)
class PluginSchema:
    """Схема ``module_<plugin_id>`` + роль плагина (§2.2)."""

    company_id: str
    plugin_id: str
    database_name: str
    schema_name: str
    role_name: str
    dsn_ref: str
    search_path: str
    created: bool


@dataclass(frozen=True, slots=True)
class ObjectStoreBinding:
    """bucket-per-tenant + префикс плагина (§5.4)."""

    company_id: str
    plugin_id: str
    bucket: str
    prefix: str
    created: bool


@dataclass(frozen=True, slots=True)
class RouteRegistration:
    """Маршруты и egress-правила, зарегистрированные из grant (§5.5, §6.3)."""

    company_id: str
    plugin_id: str
    grant_id: str
    webhook_path: str
    pluginpage_path: str
    capability_path: str
    egress_rule_count: int
    deployment: str


@dataclass(frozen=True, slots=True)
class WakeResult:
    """Результат пробуждения activator'ом (§7.4, §11 критерий 13)."""

    ready: bool
    replicas: int
    waited_ms: int


# --------------------------------------------------------------------------- grant и токен


@dataclass(frozen=True, slots=True)
class CapabilityGrantRecord:
    """Долгоживущий ``CapabilityGrant`` в БД (§4.2, §5.5, §11 критерий 12).

    Именно по ``grant_id`` gateway проверяет каждый запрос; деактивация записи и
    реестр отзывов дают мгновенный отзыв даже для ещё не истёкшего короткого токена.
    """

    grant_id: str
    company_id: str
    plugin_id: str
    plugin_instance_id: str
    manifest_version: str
    artifact_digest: str
    capabilities: tuple[str, ...]
    egress_policy: EgressPolicy
    quotas: QuotaSet
    status: GrantStatus
    created_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.grant_id,
                self.company_id,
                self.plugin_id,
                self.plugin_instance_id,
                self.manifest_version,
                self.artifact_digest,
            )
        ):
            raise ValueError("идентификаторы grant не должны быть пустыми")
        if not self.artifact_digest.startswith("sha256:") or len(self.artifact_digest) != 71:
            raise ValueError("artifact_digest должен быть sha256:<64 hex>")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at должен быть timezone-aware")

    @property
    def is_active(self) -> bool:
        return self.status is GrantStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class MintRequest:
    """Запрос на выпуск короткого ``PluginClaims`` (задача 02, §4.2)."""

    company_id: str
    plugin_id: str
    plugin_instance_id: str
    manifest_version: str
    artifact_digest: str
    grant_id: str
    scopes: tuple[str, ...]
    quota_ref: str
    ttl_seconds: int = PLUGIN_CLAIMS_TTL_SECONDS
    cabinet_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.ttl_seconds < 1 or self.ttl_seconds > PLUGIN_CLAIMS_TTL_SECONDS:
            raise ValueError("TTL короткого PluginClaims должен быть от 1 до 300 с")


@dataclass(frozen=True, slots=True)
class IssuedPluginClaims:
    """Короткий токен. В токене нет egress/квот — только ссылка на grant (§4.2)."""

    token: str
    grant_id: str
    jti: str
    expires_at: datetime
    ttl_seconds: int

    def __post_init__(self) -> None:
        if not self.token.strip() or not self.jti.strip() or not self.grant_id.strip():
            raise ValueError("поля выпущенного токена не должны быть пустыми")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at должен быть timezone-aware")


@dataclass(frozen=True, slots=True)
class AdminIdentity:
    """Результат проверки PlatformAdmin-порта (реальную auth подключает родитель)."""

    actor_id: str
    is_platform_admin: bool
    scopes: tuple[str, ...] = ()


# --------------------------------------------------------------------------- аудит


@dataclass(frozen=True, slots=True)
class AdminAuditEvent:
    """Append-only событие (§4.5). Без raw SQL, параметров, payload, токенов и PII."""

    event_id: str
    occurred_at: datetime
    company_id: str
    plugin_id: str
    operation: AdminOperation
    decision: AdminDecision
    grant_id: str | None = None
    artifact_digest: str | None = None
    code: str | None = None
    reason: str | None = None

    def redacted(self) -> dict[str, str]:
        """Каноническая форма: только идентификаторы и коды, без чувствительных данных."""

        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
            "company_id": self.company_id,
            "plugin_id": self.plugin_id,
            "operation": self.operation.value,
            "decision": self.decision.value,
            "grant_id": self.grant_id or "",
            "artifact_digest": self.artifact_digest or "",
            "code": self.code or "",
            "reason": self.reason or "",
        }


# --------------------------------------------------------------------------- ответы сервиса


@dataclass(frozen=True, slots=True)
class GrantReceipt:
    company_id: str
    plugin_id: str
    plugin_instance_id: str
    manifest_version: str
    artifact_digest: str
    grant: CapabilityGrantRecord
    claims: IssuedPluginClaims
    routes: RouteRegistration
    database: TenantDatabase
    schema: PluginSchema
    objects: ObjectStoreBinding
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class RevokeReceipt:
    company_id: str
    plugin_id: str
    grant_id: str
    status: GrantStatus
    token_revoked: bool
    replayed: bool = False
    #: §5.5: при revoke БД, схема и роль сохраняются — удаление только в uninstall.
    database_preserved: bool = True


@dataclass(frozen=True, slots=True)
class UninstallReceipt:
    company_id: str
    plugin_id: str
    grant_id: str | None
    deleted: bool
    already_absent: bool
    schema_dropped: bool
    role_dropped: bool
    prefix_dropped: bool
    bucket_dropped: bool
    artifact_unlinked: bool
    tenant_infra_purged: bool
    token_revoked: bool
    completed_steps: tuple[str, ...]
    #: §5.5 / §8 / §13.1: данные dev эфемерны, snapshot и перенос не требуются.
    snapshot_taken: bool = False

    @property
    def fully_removed(self) -> bool:
        return self.deleted and all(
            (
                self.schema_dropped,
                self.role_dropped,
                self.prefix_dropped,
                self.artifact_unlinked,
                self.tenant_infra_purged,
                self.token_revoked,
            )
        )


@dataclass(frozen=True, slots=True)
class WakeReceipt:
    company_id: str
    plugin_id: str
    grant_id: str
    claims: IssuedPluginClaims
    wake: WakeResult
    runtime_status: PluginRuntimeStatus


@dataclass(frozen=True, slots=True)
class InstalledPlugin:
    """Одна строка ``GET /admin/plugins`` (установленные плагины + статусы)."""

    plugin_id: str
    plugin_instance_id: str
    manifest_version: str
    artifact_digest: str
    grant_id: str
    grant_status: GrantStatus
    runtime_status: PluginRuntimeStatus
    enabled: bool
    capabilities: tuple[str, ...]
    egress_mode: EgressMode


@dataclass(frozen=True, slots=True)
class PluginListSnapshot:
    company_id: str
    items: tuple[InstalledPlugin, ...]
    enabled_modules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EnabledModulesReceipt:
    company_id: str
    enabled_modules: tuple[str, ...]
    changed: bool
    replayed: bool = False
    #: §8 этап 2 / §11 критерий 16: переключается только путь, данные не переносятся.
    data_migration_performed: bool = False


__all__ = [
    "ALLOWED_CAPABILITIES",
    "AdminAuditEvent",
    "AdminDecision",
    "AdminIdentity",
    "AdminOperation",
    "ArtifactPublicationStatus",
    "ArtifactRecord",
    "CapabilityGrantRecord",
    "CapabilitySet",
    "EgressMode",
    "EgressPolicy",
    "EnabledModulesReceipt",
    "GrantReceipt",
    "GrantStatus",
    "InstalledPlugin",
    "IssuedPluginClaims",
    "MintRequest",
    "ObjectStoreBinding",
    "PLUGIN_CLAIMS_TTL_SECONDS",
    "PLUGIN_OBJECT_PREFIX",
    "PLUGIN_SCHEMA_PREFIX",
    "PluginListSnapshot",
    "PluginRuntimeStatus",
    "PluginSchema",
    "QUOTA_KEYS",
    "QuotaSet",
    "RevokeReceipt",
    "RouteRegistration",
    "TENANT_BUCKET_PREFIX",
    "TENANT_DATABASE_PREFIX",
    "TenantDatabase",
    "UninstallReceipt",
    "WakeReceipt",
    "WakeResult",
]

"""Порты Admin API lifecycle плагинов (F3-admin, задача 12).

Сервис не знает ни о PostgreSQL, ни о MinIO, ни о k8s, ни о JWT: всё внешнее — за
``Protocol``. Родитель (F11) подключает реальные адаптеры задач 02/05/06/07/09 и
настоящую PlatformAdmin-auth. Пока адаптер не подключён, ``PluginAdminPorts`` нельзя
собрать, а значит operations не выполняются: fail-closed по конструкции.

Соответствие задачам плана миграции (§2, строка 12):
  * ``TenantDbPort``       → задача 05 (``application/plugins/tenantdb``);
  * ``ObjectStorePort``    → задача 06 (``application/plugins/objects``);
  * ``ArtifactRegistryPort`` → задача 07 (``application/plugins/artifacts``);
  * ``RevocationPort``     → задача 02 (``application/plugins/identity``);
  * ``ActivationPort``     → задача 09 (``application/plugins/activation``);
  * ``GrantStorePort`` / ``RouteRegistryPort`` / ``MintPort`` / ``AuditPort`` /
    ``PlatformAdminPort``  → собственные контракты админского слоя.

``RouteRegistryPort`` и ``PlatformAdminPort`` добавлены сверх перечисленного в задании
списка: без первого нельзя выполнить требование «маршруты / снятие маршрутов» (§5.5),
без второго — требование «PlatformAdmin-зависимость через порт». Оба определены здесь
же, чтобы не трогать чужие каталоги.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .contracts import (
    AdminAuditEvent,
    AdminIdentity,
    ArtifactRecord,
    CapabilityGrantRecord,
    IssuedPluginClaims,
    MintRequest,
    ObjectStoreBinding,
    PluginRuntimeStatus,
    PluginSchema,
    RouteRegistration,
    TenantDatabase,
    WakeResult,
)


# --------------------------------------------------------------------------- задача 05


class TenantDbPort(Protocol):
    """БД / роль / схема компании (§2.1, §2.2).

    Все методы идемпотентны и обязаны работать **только** с ``company_id`` из
    авторитетного контекста: адаптер не должен уметь «найти другую компанию».
    Роль создаётся с ``NOCREATEDB NOCREATEROLE NOSUPERUSER`` и ``CONNECT`` только
    на свою БД; DSN возвращается как ``secret_ref``, никогда как plaintext.
    """

    async def ensure_company_database(self, *, company_id: str) -> TenantDatabase:
        """Создать ``tenant_<company_id>`` при отсутствии, иначе вернуть существующую."""
        ...

    async def ensure_plugin_schema(
        self, *, company_id: str, plugin_id: str, dsn_ref: str
    ) -> PluginSchema:
        """Создать ``module_<plugin_id>`` с owner = роль плагина + ``search_path``."""
        ...

    async def drop_plugin_schema(self, *, company_id: str, plugin_id: str) -> tuple[bool, bool]:
        """``DROP SCHEMA`` + дроп роли. Возвращает ``(schema_dropped, role_dropped)``.

        Идемпотентно: уже отсутствующие объекты дают ``True``. БД компании (и другие
        схемы/компании) не затрагиваются — §11 критерий 15.
        """
        ...


# --------------------------------------------------------------------------- задача 06


class ObjectStorePort(Protocol):
    """bucket-per-tenant + префикс плагина (§5.4)."""

    async def ensure_bucket(self, *, company_id: str, plugin_id: str) -> ObjectStoreBinding:
        """Создать ``tenant-<company_id>`` и префикс ``module-<plugin_id>/*``."""
        ...

    async def drop_prefix(self, *, company_id: str, plugin_id: str) -> bool:
        """Удалить только префикс этого плагина; остальные плагины компании живы."""
        ...

    async def drop_bucket_if_empty(self, *, company_id: str) -> bool:
        """Удалить бакет компании, только если в нём не осталось других префиксов.

        Возвращает ``False`` — это **не** ошибка, а нормальный исход: бакет всё ещё
        используется другими плагинами той же компании. Чужой бакет не трогается.
        """
        ...


# --------------------------------------------------------------------------- задача 07


class ArtifactRegistryPort(Protocol):
    """Реестр OCI-артефактов / ``AppVersion`` / ``RegistryOutcome`` (§5.1)."""

    async def get(
        self, *, plugin_id: str, manifest_version: str, artifact_digest: str
    ) -> ArtifactRecord | None:
        """Вернуть запись артефакта или ``None``, если она отсутствует."""
        ...

    async def unlink_installation(
        self, *, company_id: str, plugin_id: str, artifact_digest: str
    ) -> bool:
        """Удалить запись установки артефакта из реестра (§5.5 uninstall).

        Глобальный immutable digest в GHCR не удаляется: удаляется tenant-scoped
        регистрация этого digest у компании. Идемпотентно; ``False`` означает, что
        адаптер не подтвердил удаление (fail-closed → ``UNINSTALL_PARTIAL``).
        """
        ...


# --------------------------------------------------------------------------- хранилище grant'ов


class GrantStorePort(Protocol):
    """Долгоживущие ``CapabilityGrant`` и фича-флаг strangler (§4.2, §8 этап 2).

    Egress-политика и квоты хранятся именно здесь, а не в коротком JWT (§13.3).
    """

    async def get(
        self, *, company_id: str, plugin_id: str
    ) -> CapabilityGrantRecord | None: ...

    async def get_by_grant_id(self, *, grant_id: str) -> CapabilityGrantRecord | None: ...

    async def create(self, *, grant: CapabilityGrantRecord) -> CapabilityGrantRecord:
        """Идемпотентно по ``grant_id``: существующая запись возвращается как есть."""
        ...

    async def deactivate(self, *, grant_id: str) -> CapabilityGrantRecord:
        """Деактивация (мгновенный отзыв по ``grant_id``, §11 критерий 12)."""
        ...

    async def delete(self, *, company_id: str, plugin_id: str) -> bool:
        """Удалить запись grant при uninstall. Идемпотентно."""
        ...

    async def list_by_company(
        self, *, company_id: str
    ) -> Sequence[CapabilityGrantRecord]: ...

    async def get_enabled_modules(self, *, company_id: str) -> Sequence[str]:
        """Текущее значение ``plugin_enabled_modules[]``."""
        ...

    async def set_enabled_modules(
        self, *, company_id: str, plugin_ids: Sequence[str]
    ) -> Sequence[str]:
        """Записать новое значение фича-флага. Идемпотентно.

        Адаптер **не** должен трогать никакие данные declarative-app: переключается
        только путь обработки (§8 этап 2, §11 критерий 16).
        """
        ...


# --------------------------------------------------------------------------- задача 02


class RevocationPort(Protocol):
    """Реестр отзывов по ``grant_id`` (задача 02, §4.2, §11 критерий 12)."""

    async def revoke(self, *, grant_id: str, reason: str) -> bool:
        """Записать отзыв. ``True`` = запись подтверждена (иначе ``REVOKE_FAILED``).

        Идемпотентно: повторный отзыв того же ``grant_id`` возвращает ``True``.
        Запись должна проверяться gateway на **каждом** запросе, чтобы ещё не
        истёкший короткий ``PluginClaims`` перестал действовать немедленно.
        """
        ...

    async def is_revoked(self, *, grant_id: str) -> bool:
        """Проверка для ``wake``: отозванный grant нельзя разбудить."""
        ...


# --------------------------------------------------------------------------- задача 02 (mint)


class MintPort(Protocol):
    """Выпуск короткого ``PluginClaims`` (§4.2, §5.5)."""

    async def mint(self, *, request: MintRequest) -> IssuedPluginClaims:
        """В токен кладутся только ``grant_id``/``jti``/TTL/scopes, не egress и не квоты."""
        ...


# --------------------------------------------------------------------------- задача 09


class ActivationPort(Protocol):
    """Собственный activator плагинов (задача 09, §7.4, §5.5).

    Workload — обычный ``Deployment`` с ``replicas: 0``, а не ``SandboxClaim``.
    """

    async def ensure_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        dsn_ref: str,
    ) -> str:
        """Создать Deployment ``replicas: 0`` по проверенному digest. Вернуть имя."""
        ...

    async def suspend(self, *, company_id: str, plugin_id: str) -> bool:
        """``Suspended`` при revoke: БД/схема сохраняются, трафик снимается."""
        ...

    async def wake(self, *, company_id: str, plugin_id: str) -> WakeResult:
        """0→1 с удержанием соединения до Ready (§11 критерий 13)."""
        ...

    async def runtime_status(
        self, *, company_id: str, plugin_id: str
    ) -> PluginRuntimeStatus: ...

    async def purge_infra(self, *, company_id: str, plugin_id: str) -> bool:
        """Purge tenant infra: Deployment / Secret / маршруты этого плагина.

        Строго в границах ``(company_id, plugin_id)``; БД компании и другие компании
        не затрагиваются (§11 критерий 15).
        """
        ...


# --------------------------------------------------------------------------- маршруты


class RouteRegistryPort(Protocol):
    """Регистрация маршрутов и egress-правил из grant (§5.5, §6.3, §7.1).

    Политика egress берётся из ``CapabilityGrant`` в БД по ``grant_id``, а не из
    короткого JWT (§13.3): истечение токена не должно пересобирать сетевые правила.
    """

    async def register(
        self, *, company_id: str, plugin_id: str, grant_id: str
    ) -> RouteRegistration: ...

    async def unregister(
        self, *, company_id: str, plugin_id: str, grant_id: str
    ) -> bool:
        """Снятие маршрутов при revoke/uninstall. Идемпотентно."""
        ...


# --------------------------------------------------------------------------- аудит


class AuditPort(Protocol):
    """Append-only аудит (§4.5)."""

    async def append(self, *, event: AdminAuditEvent) -> bool:
        """``True`` = событие зафиксировано.

        Для позитивной мутации неудачная запись аудита = deny / system failure,
        а не молчаливый успех (§4.5). Событие не содержит raw SQL, параметров,
        payload, токенов и PII — см. ``AdminAuditEvent.redacted()``.
        """
        ...


# --------------------------------------------------------------------------- авторизация админа


class PlatformAdminPort(Protocol):
    """Проверка PlatformAdmin. Реальную auth подключает родитель (F11).

    Порт получает сырой ``Authorization`` и сам решает, кто это; сервис дополнительно
    проверяет ``AdminIdentity.is_platform_admin`` (защита в глубину). Пока адаптер не
    подключён, HTTP-слой отказывает с 503 — fail-closed, а не «пустить всех».
    """

    async def require_platform_admin(self, *, authorization: str | None) -> AdminIdentity: ...


# --------------------------------------------------------------------------- связка портов


@dataclass(frozen=True, slots=True)
class PluginAdminPorts:
    """Все зависимости ``PluginAdminService`` одной неизменяемой связкой.

    Ни одно поле не имеет значения по умолчанию: собрать сервис «частично
    подключённым» невозможно. Родитель создаёт экземпляр при старте приложения и
    кладёт его в ``app.state.plugin_admin_ports``.
    """

    tenant_db: TenantDbPort
    object_store: ObjectStorePort
    artifact_registry: ArtifactRegistryPort
    grant_store: GrantStorePort
    revocation: RevocationPort
    mint: MintPort
    activation: ActivationPort
    routes: RouteRegistryPort
    audit: AuditPort
    platform_admin: PlatformAdminPort


__all__ = [
    "ActivationPort",
    "ArtifactRegistryPort",
    "AuditPort",
    "GrantStorePort",
    "MintPort",
    "ObjectStorePort",
    "PlatformAdminPort",
    "PluginAdminPorts",
    "RevocationPort",
    "RouteRegistryPort",
    "TenantDbPort",
]

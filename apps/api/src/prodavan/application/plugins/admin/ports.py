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
from datetime import datetime
from typing import Protocol, Sequence
from urllib.parse import quote

from prodavan.application.plugins.capabilities.adapters.secrets import SecretsDeliveryResponse
from prodavan.application.plugins.installation_scope.contracts import InstallationScope
from prodavan.application.plugins.tenantdb.service import StoredSecret

from .contracts import (
    AdminAuditEvent,
    AdminIdentity,
    ArtifactPinUpdateResult,
    ArtifactRecord,
    CapabilityGrantRecord,
    IssuedPluginClaims,
    MintRequest,
    ObjectStoreBinding,
    PluginInstanceRecord,
    PluginInstanceStatus,
    PluginPublicationApproval,
    PluginRuntimeBinding,
    PluginRuntimeStatus,
    PluginSchema,
    PluginUpdateLockLease,
    RouteRegistration,
    SecretPreDeliveryRequest,
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

    async def rotate_plugin_schema(
        self, *, company_id: str, plugin_id: str, dsn_ref: str
    ) -> PluginSchema:
        """ЯВНАЯ ротация пароля/DSN только для repair или re-grant после revoke."""
        ...

    async def revoke_plugin(self, *, company_id: str, plugin_id: str) -> bool:
        """NOLOGIN + REVOKE + terminate sessions + secret cleanup; идемпотентно."""
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


# --------------------------------------------------------------------------- distributed update lock


class PluginUpdateLockPort(Protocol):
    """Cross-process per-installation update mutex with an owned release lease."""

    async def acquire(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        expected_manifest_version: str,
        expected_artifact_digest: str,
    ) -> PluginUpdateLockLease | None:
        """Return an owned lease, or ``None`` when another process holds the lock."""
        ...

    async def release(self, lease: PluginUpdateLockLease) -> None:
        """Release only the exact acquired lease; never release another owner."""
        ...


# --------------------------------------------------------------------------- durable instance saga


class InstanceStorePort(Protocol):
    """Persistent запись установки, переживающая сбой между любыми saga-шагами."""

    async def create(self, *, instance: PluginInstanceRecord) -> PluginInstanceRecord:
        """Idempotent по ``(company_id, plugin_id)``; конфликт immutable полей = ошибка."""
        ...

    async def get(
        self, *, company_id: str, plugin_id: str
    ) -> PluginInstanceRecord | None: ...

    async def get_by_id(self, *, plugin_instance_id: str) -> PluginInstanceRecord | None: ...

    async def update_status(
        self, *, plugin_instance_id: str, status: PluginInstanceStatus
    ) -> PluginInstanceRecord: ...

    async def update_artifact_pin(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        expected_manifest_version: str,
        expected_artifact_digest: str,
        manifest_version: str,
        artifact_digest: str,
        expected_status: PluginInstanceStatus,
        status: PluginInstanceStatus,
    ) -> PluginInstanceRecord:
        """Fenced old→new CAS с company/plugin binding и verified RETURNING post-state."""
        ...

    async def delete(self, *, company_id: str, plugin_id: str) -> bool:
        """Удалить только после подтверждённой очистки; отсутствие = True."""
        ...

    async def resolve(self, *, company_id: str, plugin_id: str) -> str | None:
        """Стык ``InstanceIdResolver`` для activator/gateway."""
        ...


class InstallationScopeStorePort(Protocol):
    async def ensure_from_source(
        self, *, company_id: str, plugin_id: str, plugin_instance_id: str
    ) -> InstallationScope: ...


# --------------------------------------------------------------------------- webhook secrets


def webhook_secret_key(*, company_id: str, plugin_id: str, plugin_instance_id: str) -> str:
    """Canonical per-instance key; webhook material never uses the tenant DSN key."""
    for value in (company_id, plugin_id, plugin_instance_id):
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 256
            or any(char.isspace() or char == "\0" for char in value)
        ):
            raise ValueError("webhook secret identity is empty or invalid")
    return (
        f"plugins/webhooks/{quote(company_id, safe='')}/{quote(plugin_id, safe='')}/"
        f"{quote(plugin_instance_id, safe='')}/WEBHOOK_SECRET"
    )


class WebhookSecretStorePort(Protocol):
    """Generic SecretStore operations scoped by :func:`webhook_secret_key`."""

    async def get(self, *, key: str) -> StoredSecret | None: ...
    async def put(self, *, key: str, value: str) -> str: ...
    async def rotate(self, *, key: str, value: str) -> str: ...
    async def delete(self, *, secret_ref: str) -> None: ...


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

    async def update_artifact_pin(
        self,
        *,
        grant_id: str,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        expected_manifest_version: str,
        expected_artifact_digest: str,
        manifest_version: str,
        artifact_digest: str,
        expected_instance_status: PluginInstanceStatus,
        instance_status: PluginInstanceStatus,
    ) -> ArtifactPinUpdateResult:
        """Атомарный fenced CAS instance + ACTIVE grant после confirmed Deployment update."""
        ...

    async def regrant(
        self, *, previous_grant_id: str, grant: CapabilityGrantRecord
    ) -> CapabilityGrantRecord:
        """Атомарно заменить revoked grant новым id после repair/re-grant.

        Старый revocation остаётся под старым id; ACTIVE статус появляется только
        после подтверждения Deployment вызывающей saga.
        """
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


# --------------------------------------------------------------------------- publication approval


class PublicationApprovalReaderPort(Protocol):
    """Read-only exact binding для CI publication; никаких grant-статусов."""

    async def get(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
    ) -> PluginPublicationApproval | None: ...


class PublicationApprovalStorePort(PublicationApprovalReaderPort, Protocol):
    """Durable administrative decision + идемпотентная отметка consumption."""

    async def create(
        self, *, approval: PluginPublicationApproval
    ) -> PluginPublicationApproval:
        """INSERT-if-absent; существующая exact binding строка возвращается as-is."""
        ...

    async def mark_consumed(
        self, *, approval_id: str, consumed_at: datetime
    ) -> PluginPublicationApproval | None:
        """Atomic CAS NULL→timestamp; повтор сохраняет первый timestamp."""
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
        webhook_secret_ref: str,
    ) -> str:
        """Создать Deployment ``replicas: 0`` по проверенному digest. Вернуть имя."""
        ...

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
        """Patch уже установленного backend с сохранением replicas и health confirmation."""
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

    async def runtime_binding(
        self, *, company_id: str, plugin_id: str
    ) -> PluginRuntimeBinding:
        """Read the live artifact and owner fence without consulting only SQL."""
        ...

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


# --------------------------------------------------------------------------- control-plane delivery


class SecretDeliveryControlPort(Protocol):
    """Approved installation/artifact phase; never consumes a plugin token."""

    async def pre_deliver(
        self, request: SecretPreDeliveryRequest
    ) -> tuple[SecretsDeliveryResponse, ...]: ...

    async def pre_revoke(
        self, request: SecretPreDeliveryRequest
    ) -> tuple[SecretsDeliveryResponse, ...]: ...


# --------------------------------------------------------------------------- связка портов


@dataclass(frozen=True, slots=True)
class PluginAdminPorts:
    """Все зависимости ``PluginAdminService`` одной неизменяемой связкой.

    Базовые порты обязательны. ``publication_approvals`` временно optional для
    совместимости со старыми composition root/fakes: его отсутствие fail-closed
    запрещает новые approval/grant, но не ломает replay уже ACTIVE legacy grant.
    Родитель создаёт экземпляр при старте приложения и кладёт его в
    ``app.state.plugin_admin_ports``.
    """

    tenant_db: TenantDbPort
    object_store: ObjectStorePort
    artifact_registry: ArtifactRegistryPort
    instance_store: InstanceStorePort
    grant_store: GrantStorePort
    revocation: RevocationPort
    mint: MintPort
    activation: ActivationPort
    routes: RouteRegistryPort
    audit: AuditPort
    platform_admin: PlatformAdminPort
    publication_approvals: PublicationApprovalStorePort | None = None
    #: None keeps legacy composition possible, but grant/revoke requiring webhook
    #: lifecycle fails closed until the trusted SecretStore is explicitly wired.
    webhook_secrets: WebhookSecretStorePort | None = None
    #: Optional only for legacy manifests without secret_delivery. DeploymentAdmin
    #: remains fail-closed when an approved declaration requires pre-delivery.
    secret_delivery: SecretDeliveryControlPort | None = None
    #: None keeps initial/replay grants compatible; an actual update fails closed.
    update_lock: PluginUpdateLockPort | None = None
    #: Required by grant/replay/update. Missing binding remains fail-closed.
    installation_scopes: InstallationScopeStorePort | None = None


__all__ = [
    "ActivationPort",
    "ArtifactPinUpdateResult",
    "ArtifactRegistryPort",
    "AuditPort",
    "GrantStorePort",
    "InstanceStorePort",
    "MintPort",
    "ObjectStorePort",
    "PlatformAdminPort",
    "PluginAdminPorts",
    "PluginRuntimeBinding",
    "PluginUpdateLockLease",
    "PluginUpdateLockPort",
    "PublicationApprovalReaderPort",
    "PublicationApprovalStorePort",
    "RevocationPort",
    "RouteRegistryPort",
    "SecretDeliveryControlPort",
    "TenantDbPort",
    "WebhookSecretStorePort",
    "webhook_secret_key",
]

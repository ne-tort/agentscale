"""Доменные типы плагинов (F0).

Только контракты: без persistence, транспорта и криптографии. Источник истины —
``docs/ARCHITECTURE/plugin-architecture-complete.md`` (v4), §2, §4.2, §5.1, §6.1.

Зеркало контрактов:
  * ``packages/schemas/plugin-manifest/v1/schema.json``
  * ``packages/schemas/plugin-manifest/v1/protocol.json``
  * ``packages/schemas/plugin-manifest/v1/types.ts``

Fail-closed: отсутствие grant/квоты/адаптера фиксируется типизированным
:class:`DecisionCode`, а не молчаливым успехом.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Mapping


class PluginInstanceStatus(StrEnum):
    """Статус экземпляра плагина (доменный, не статус Kubernetes Pod)."""

    INSTALLED = "installed"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    QUARANTINED = "quarantined"


class Capability(StrEnum):
    """Разрешённые capability gateway.

    ``db.direct`` и ``db.migrations`` намеренно отсутствуют: БД плагина доступна
    по прямому DSN к ``tenant_<company>`` (§2 v4), остальная инфраструктура —
    через типизированные адаптеры. Плагин не знает реализации адаптера.
    """

    CACHE = "cache"
    OBJECTS = "objects"
    DOCS = "docs"
    EVENTS = "events"
    JOBS = "jobs"
    PODS = "pods"
    MCP = "mcp"
    SECRETS = "secrets"
    NETWORK = "network"


CAPABILITY_ALLOWLIST: frozenset[str] = frozenset(item.value for item in Capability)
"""Явный allowlist capability (совпадает с enum schema.json)."""


class MigrationEngine(StrEnum):
    """Движок миграций схемы плагина (changelog внутри OCI-артефакта)."""

    LIQUIBASE = "liquibase"
    FLYWAY = "flyway"
    ALEMBIC = "alembic"


class EgressMode(StrEnum):
    """Режим egress-политики (§6.3 v4). Источник правил — grant в БД."""

    NONE = "none"
    WEBHOOKS_ONLY = "webhooks-only"
    FULL = "full"


class RegistryOutcome(StrEnum):
    """Исход регистрации версии в реестре (AppVersion/RegistryOutcome, §5.1 v4)."""

    APPLIED = "applied"
    CONFLICT = "conflict"
    QUARANTINED = "quarantined"
    MISSING = "missing"


class DecisionCode(StrEnum):
    """Коды решений gateway плагинов.

    Переиспользует идею ``application/capability_gateway_service.py::DecisionCode``,
    но это самостоятельный enum домена плагинов: agent-поверхность :8001 и trust
    ``pod_bridge`` не смешиваются с ``/capabilities/v1/*`` (§4.1 v4).
    """

    ALLOWED = "allowed"
    INVALID_REQUEST = "invalid_request"
    CAPABILITY_UNKNOWN = "capability_unknown"
    CAPABILITY_NOT_REQUESTED = "capability_not_requested"
    IDENTITY_DENIED = "identity_denied"
    SCOPE_DENIED = "scope_denied"
    GRANT_MISSING = "grant_missing"
    GRANT_NOT_YET_ISSUED = "grant_not_yet_issued"
    GRANT_EXPIRED = "grant_expired"
    GRANT_REVOKED = "grant_revoked"
    QUOTA_EXCEEDED = "quota_exceeded"
    ADAPTER_MISSING = "adapter_missing"
    ADAPTER_FAILED = "adapter_failed"


PLUGIN_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*$")
SEMVER_PATTERN: re.Pattern[str] = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
ARTIFACT_DIGEST_PATTERN: re.Pattern[str] = re.compile(r"^sha256:[0-9a-f]{64}$")
EGRESS_REF_PATTERN: re.Pattern[str] = re.compile(r"^grant:[A-Za-z0-9_.:/-]+$")
PATH_PATTERN: re.Pattern[str] = re.compile(r"^/[A-Za-z0-9._/-]*$")
RELATIVE_PATH_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._/-]+$")
PROTOCOL_VERSION: str = "plugin-rpc/1"

SELF_REFERENCE_FIELDS: frozenset[str] = frozenset({"artifact_digest", "signature"})
"""Поля, запрещённые в манифесте: подписываемый документ не может нести свой digest/подпись."""

EMBEDDED_EGRESS_FIELDS: frozenset[str] = frozenset(
    {"egress", "allowed_hosts", "allowed_paths", "egress_policy"}
)
"""Встроенный allowlist запрещён: правила egress живут в CapabilityGrant в БД (§6.1 v4)."""


def _validate_semver(value: str, *, field_name: str) -> None:
    """Проверка SemVer 2.0.0 (без внешних зависимостей)."""
    if not isinstance(value, str) or not SEMVER_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a semantic version")


def _validate_relative_path(value: str, *, field_name: str) -> None:
    """Относительный путь внутри артефакта без выхода наверх."""
    if not isinstance(value, str) or not RELATIVE_PATH_PATTERN.fullmatch(value) or ".." in value:
        raise ValueError(f"{field_name} must be a relative path without '..'")


def _validate_absolute_path(value: str, *, field_name: str) -> None:
    """Абсолютный URL-путь без выхода наверх."""
    if not isinstance(value, str) or not PATH_PATTERN.fullmatch(value) or ".." in value:
        raise ValueError(f"{field_name} must be an absolute path without '..'")


@dataclass(frozen=True, slots=True)
class FrontendEntrypoint:
    """Точка входа frontend на отдельном static-origin (§5.2 v4)."""

    entry: str
    base_path: str

    def __post_init__(self) -> None:
        _validate_relative_path(self.entry, field_name="frontend.entry")
        _validate_absolute_path(self.base_path, field_name="frontend.base_path")


@dataclass(frozen=True, slots=True)
class BackendEntrypoint:
    """Backend контейнера плагина; /health Ready только после apply миграций."""

    port: int
    health: str

    def __post_init__(self) -> None:
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise ValueError("backend.port must be an integer in 1..65535")
        _validate_absolute_path(self.health, field_name="backend.health")


@dataclass(frozen=True, slots=True)
class MigrationsDeclaration:
    """Объявление changelog плагина; SQL применяется самим плагином под своей ролью."""

    engine: MigrationEngine
    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.engine, MigrationEngine):
            raise ValueError("migrations.engine must be a MigrationEngine")
        _validate_relative_path(self.path, field_name="migrations.path")


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Манифест плагина v1 (подписан как OCI annotation).

    Не содержит ``artifact_digest``/``signature`` (самореференция) и не содержит
    встроенного egress allowlist — только ``egress_ref`` на grant в БД.
    """

    plugin_id: str
    version: str
    protocol: str
    frontend: FrontendEntrypoint
    backend: BackendEntrypoint
    capabilities: tuple[Capability, ...]
    migrations: MigrationsDeclaration
    egress_ref: str
    public_pages: tuple[str, ...]
    webhooks: tuple[str, ...]
    min_platform_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(self.plugin_id):
            raise ValueError("plugin_id must match ^[a-z][a-z0-9_]*$")
        _validate_semver(self.version, field_name="version")
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError(f"protocol must be {PROTOCOL_VERSION}")
        if not isinstance(self.frontend, FrontendEntrypoint):
            raise ValueError("frontend must be a FrontendEntrypoint")
        if not isinstance(self.backend, BackendEntrypoint):
            raise ValueError("backend must be a BackendEntrypoint")
        if not isinstance(self.capabilities, tuple):
            raise ValueError("capabilities must be a tuple")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must not contain duplicates")
        for capability in self.capabilities:
            if not isinstance(capability, Capability):
                raise ValueError("every capability must be a Capability member")
        if not isinstance(self.migrations, MigrationsDeclaration):
            raise ValueError("migrations must be a MigrationsDeclaration")
        if not isinstance(self.egress_ref, str) or not EGRESS_REF_PATTERN.fullmatch(self.egress_ref):
            raise ValueError("egress_ref must reference a grant (grant:...)")
        for field_name, values in (("public_pages", self.public_pages), ("webhooks", self.webhooks)):
            if not isinstance(values, tuple):
                raise ValueError(f"{field_name} must be a tuple")
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must not contain duplicates")
            for value in values:
                _validate_absolute_path(value, field_name=field_name)
        _validate_semver(self.min_platform_version, field_name="min_platform_version")

    @property
    def requested_capabilities(self) -> frozenset[str]:
        """Запрошенные capability (для compatibility gate до PUBLISHED)."""
        return frozenset(item.value for item in self.capabilities)


@dataclass(frozen=True, slots=True)
class ArtifactDigest:
    """Digest OCI-артефакта: ``sha256:<64 hex>``.

    Вычисляется платформой из cosign/OCI и не может быть частью манифеста.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not ARTIFACT_DIGEST_PATTERN.fullmatch(self.value):
            raise ValueError("artifact digest must match ^sha256:[0-9a-f]{64}$")

    def __str__(self) -> str:
        return self.value

    @property
    def hex(self) -> str:
        """Шестнадцатеричная часть без префикса ``sha256:``."""
        return self.value.removeprefix("sha256:")


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """Egress-политика из grant в БД (§6.1/§6.3 v4), а не из короткого JWT."""

    mode: EgressMode
    allowed_hosts: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, EgressMode):
            raise ValueError("mode must be an EgressMode")
        for field_name, values in (("allowed_hosts", self.allowed_hosts), ("allowed_paths", self.allowed_paths)):
            if not isinstance(values, tuple):
                raise ValueError(f"{field_name} must be a tuple")
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{field_name} must contain non-empty strings")
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must not contain duplicates")
        if self.mode is EgressMode.NONE and (self.allowed_hosts or self.allowed_paths):
            raise ValueError("mode=none forbids allowed_hosts/allowed_paths")
        if self.mode is EgressMode.WEBHOOKS_ONLY and not (self.allowed_hosts and self.allowed_paths):
            raise ValueError("mode=webhooks-only requires allowed_hosts and allowed_paths")

    def allows(self, host: str, path: str) -> bool:
        """Fail-closed проверка: любое отсутствие правила = отказ."""
        if self.mode is EgressMode.NONE:
            return False
        if self.mode is EgressMode.WEBHOOKS_ONLY:
            return host in self.allowed_hosts and path in self.allowed_paths
        return self.mode is EgressMode.FULL


@dataclass(frozen=True, slots=True)
class QuotaRef:
    """Ссылка на тариф/квоту; квоты считаются per-(company, instance, digest)."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("quota_ref must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """Долгоживущий grant в БД (§4.2/§5.5 v4).

    Отзыв = деактивация grant и проверка ``grant_id`` на каждом запросе, а не
    bump generation пода. Egress и квоты не кладутся в короткий JWT.
    """

    grant_id: str
    plugin_id: str
    plugin_instance_id: str
    capabilities: tuple[Capability, ...]
    egress_ref: str
    egress_policy: EgressPolicy
    quota_ref: QuotaRef
    artifact_digest: ArtifactDigest
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.grant_id, str) or not self.grant_id.strip():
            raise ValueError("grant_id must not be empty")
        if not isinstance(self.plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(self.plugin_id):
            raise ValueError("plugin_id must match ^[a-z][a-z0-9_]*$")
        if not isinstance(self.plugin_instance_id, str) or not self.plugin_instance_id.strip():
            raise ValueError("plugin_instance_id must not be empty")
        if not isinstance(self.capabilities, tuple) or any(
            not isinstance(item, Capability) for item in self.capabilities
        ):
            raise ValueError("capabilities must be a tuple of Capability members")
        if not isinstance(self.egress_ref, str) or not EGRESS_REF_PATTERN.fullmatch(self.egress_ref):
            raise ValueError("egress_ref must reference a grant (grant:...)")
        if not isinstance(self.egress_policy, EgressPolicy):
            raise ValueError("egress_policy must be an EgressPolicy")
        if not isinstance(self.quota_ref, QuotaRef):
            raise ValueError("quota_ref must be a QuotaRef")
        if not isinstance(self.artifact_digest, ArtifactDigest):
            raise ValueError("artifact_digest must be an ArtifactDigest")
        if self.expires_at is not None and self.issued_at is not None and self.expires_at < self.issued_at:
            raise ValueError("expires_at must not precede issued_at")

    @property
    def active(self) -> bool:
        """Признак неотозванного grant (без проверки времени — её делает gateway)."""
        return self.revoked_at is None

    def grants(self, capability: Capability) -> bool:
        """Явная проверка наличия capability; отсутствие = False."""
        return capability in self.capabilities and self.active


@dataclass(frozen=True, slots=True)
class PluginInstance:
    """Экземпляр установленного плагина (company-scoped, stateless)."""

    instance_id: str
    plugin_id: str
    company_id: str
    manifest_version: str
    artifact_digest: ArtifactDigest
    status: PluginInstanceStatus
    cabinet_id: str | None = None
    project_id: str | None = None
    grant_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("instance_id", "plugin_id", "company_id", "manifest_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        if not PLUGIN_ID_PATTERN.fullmatch(self.plugin_id):
            raise ValueError("plugin_id must match ^[a-z][a-z0-9_]*$")
        _validate_semver(self.manifest_version, field_name="manifest_version")
        if not isinstance(self.artifact_digest, ArtifactDigest):
            raise ValueError("artifact_digest must be an ArtifactDigest")
        if not isinstance(self.status, PluginInstanceStatus):
            raise ValueError("status must be a PluginInstanceStatus")
        if self.updated_at is not None and self.created_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")


@dataclass(frozen=True, slots=True)
class PluginClaims:
    """Набор полей PluginClaims (§4.2 v4). Только типы, БЕЗ криптографии.

    Mint/verify/JWKS/revocation — зона F1. Egress-политика и квоты в токен
    не входят: token несёт ``grant_id`` и короткий ``exp``.
    """

    iss: str = "agentscale-plugins"
    aud: str = "capability-gateway"
    typ: str = "plugin_capability"
    sub: str = ""
    company_id: str = ""
    cabinet_id: str | None = None
    project_id: str | None = None
    plugin_id: str = ""
    plugin_instance_id: str = ""
    manifest_version: str = ""
    artifact_digest: str = ""
    grant_id: str = ""
    scopes: tuple[Capability, ...] = ()
    quota_ref: str = ""
    jti: str = ""
    exp: int = 0

    def __post_init__(self) -> None:
        if self.iss != "agentscale-plugins" or self.aud != "capability-gateway":
            raise ValueError("PluginClaims requires distinct plugin issuer/audience")
        if self.typ != "plugin_capability":
            raise ValueError("PluginClaims.typ must be plugin_capability")
        for field_name in ("sub", "company_id", "plugin_id", "plugin_instance_id", "grant_id", "jti"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        if not PLUGIN_ID_PATTERN.fullmatch(self.plugin_id):
            raise ValueError("plugin_id must match ^[a-z][a-z0-9_]*$")
        _validate_semver(self.manifest_version, field_name="manifest_version")
        if not ARTIFACT_DIGEST_PATTERN.fullmatch(self.artifact_digest):
            raise ValueError("artifact_digest must match ^sha256:[0-9a-f]{64}$")
        if any(not isinstance(item, Capability) for item in self.scopes):
            raise ValueError("scopes must contain only Capability members")
        if not isinstance(self.exp, int) or isinstance(self.exp, bool) or self.exp <= 0:
            raise ValueError("exp must be a positive unix timestamp")

    def to_claims_dict(self, extra: Mapping[str, object] | None = None) -> dict[str, object]:
        """Плоский словарь claim'ов для последующего mint (без подписи)."""
        payload: dict[str, object] = {
            "iss": self.iss,
            "aud": self.aud,
            "typ": self.typ,
            "sub": self.sub,
            "company_id": self.company_id,
            "cabinet_id": self.cabinet_id,
            "project_id": self.project_id,
            "plugin_id": self.plugin_id,
            "plugin_instance_id": self.plugin_instance_id,
            "manifest_version": self.manifest_version,
            "artifact_digest": self.artifact_digest,
            "grant_id": self.grant_id,
            "scopes": [item.value for item in self.scopes],
            "quota_ref": self.quota_ref,
            "jti": self.jti,
            "exp": self.exp,
        }
        if extra:
            payload.update(extra)
        return payload


@dataclass(frozen=True, slots=True)
class RegistryResult:
    """Результатcompatibility gate / публикации версии (§5.1 v4)."""

    outcome: RegistryOutcome
    artifact_digest: ArtifactDigest
    plugin_id: str
    version: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, RegistryOutcome):
            raise ValueError("outcome must be a RegistryOutcome")
        if not isinstance(self.artifact_digest, ArtifactDigest):
            raise ValueError("artifact_digest must be an ArtifactDigest")
        if not isinstance(self.plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(self.plugin_id):
            raise ValueError("plugin_id must match ^[a-z][a-z0-9_]*$")
        _validate_semver(self.version, field_name="version")

    @property
    def published(self) -> bool:
        """Только APPLIED означает успех; всё остальное — fail-closed отказ."""
        return self.outcome is RegistryOutcome.APPLIED


@dataclass(frozen=True, slots=True)
class GatewayDecision:
    """Решение capability gateway плагинов до вызова адаптера (§4.5 v4)."""

    allowed: bool
    code: DecisionCode
    reason: str | None = None
    grant_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, DecisionCode):
            raise ValueError("code must be a DecisionCode")
        if self.allowed and self.code is not DecisionCode.ALLOWED:
            raise ValueError("allowed decision requires ALLOWED code")
        if not self.allowed and self.code is DecisionCode.ALLOWED:
            raise ValueError("denied decision must not use ALLOWED code")


__all__ = [
    "ARTIFACT_DIGEST_PATTERN",
    "CAPABILITY_ALLOWLIST",
    "EGRESS_REF_PATTERN",
    "EMBEDDED_EGRESS_FIELDS",
    "PATH_PATTERN",
    "PLUGIN_ID_PATTERN",
    "PROTOCOL_VERSION",
    "RELATIVE_PATH_PATTERN",
    "SEMVER_PATTERN",
    "SELF_REFERENCE_FIELDS",
    "ArtifactDigest",
    "BackendEntrypoint",
    "Capability",
    "CapabilityGrant",
    "DecisionCode",
    "EgressMode",
    "EgressPolicy",
    "FrontendEntrypoint",
    "GatewayDecision",
    "MigrationEngine",
    "MigrationsDeclaration",
    "PluginClaims",
    "PluginInstance",
    "PluginInstanceStatus",
    "PluginManifest",
    "QuotaRef",
    "RegistryOutcome",
    "RegistryResult",
]

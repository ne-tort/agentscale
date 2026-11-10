"""Admin API lifecycle плагинов (F3-admin, задача 12).

``APIRouter(prefix="/admin/plugins")`` — семь эндпоинтов из §5.5 / §8 этап 2:

=========================  =============================================
Метод + путь               Операция
=========================  =============================================
``POST /publication-approvals``  exact pre-approval до первой публикации
``POST /grant``            установка по PUBLISHED-артефакту
``POST /revoke``           мгновенный отзыв по ``grant_id``
``POST /uninstall``        полное удаление (без snapshot)
``POST /wake``             activator + новый короткий токен
``GET  /``                 установленные плагины + статусы
``PUT  /enabled-modules``  фича-флаг ``plugin_enabled_modules[]``
=========================  =============================================

Правила контракта:

* **Только типизированные Pydantic-тела**, ``model_config = {"extra": "forbid"}``.
  Никаких произвольных ``dict`` / ``Any`` в запросах и ответах: ответы собираются из
  полей типизированных receipts (``GrantReceipt`` и др.).
* **PlatformAdmin — через порт.** Зависимость ``require_platform_admin`` вызывает
  ``PlatformAdminPort.require_platform_admin`` и дополнительно проверяет
  ``AdminIdentity.is_platform_admin``. Реальную auth подключает родитель в F11; до
  подключения ``app.state.plugin_admin_ports`` отсутствует → 503 (fail-closed), а не
  «пустить всех» и не заглушка с ``True``.
* **Сервис из ``app.state``.** Родитель кладёт ``plugin_admin_service`` /
  ``plugin_admin_ports`` при старте; отсутствие = 503 «интеграция не подключена».
* Ошибки ``PluginAdminError`` наследуют ``AppError`` и автоматически превращаются в
  RFC 7807 problem+json зарегистрированным обработчиком.

Регистрацию в ``api/v1/router.py`` выполняет родитель (общий файл, не наш).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from prodavan.application.plugins.admin.contracts import (
    CapabilitySet,
    EgressMode,
    EgressPolicy,
    EffectiveQuotasStatus,
    GrantStatus,
    IssuedPluginClaims,
    PluginRuntimeStatus,
    QuotaSet,
)
from prodavan.application.plugins.admin.errors import PluginAdminError
from prodavan.application.plugins.admin.ports import PlatformAdminPort, PluginAdminPorts
from prodavan.application.plugins.admin.service import (
    EffectiveGrantReadError,
    PluginAdminService,
)
from prodavan.domain.errors import AppError
from prodavan.domain.plugins.types import SEMVER_PATTERN

router = APIRouter(prefix="/admin/plugins", tags=["admin-plugins"])

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"


# ----------------------------------------------------------------------------- тела


class EgressPolicyBody(BaseModel):
    """Egress-политика, хранимая в ``CapabilityGrant`` (§6.3), не в JWT."""

    model_config = ConfigDict(extra="forbid")

    mode: EgressMode = EgressMode.NONE
    allowed_hosts: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    allowed_paths: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    approved_for_full: bool = False

    def to_contract(self) -> EgressPolicy:
        return EgressPolicy(
            mode=self.mode,
            allowed_hosts=tuple(self.allowed_hosts),
            allowed_paths=tuple(self.allowed_paths),
            approved_for_full=self.approved_for_full,
        )


class QuotaSetBody(BaseModel):
    """Квоты §4.4. Значения >= 1: ноль трактуется адаптером как «без лимита»."""

    model_config = ConfigDict(extra="forbid")

    ops_per_minute: int = Field(default=60, ge=1, strict=True)
    max_rows: int = Field(default=10_000, ge=1, strict=True)
    max_bytes: int = Field(default=10_000_000, ge=1, strict=True)
    max_keys: int = Field(default=1_000, ge=1, strict=True)
    max_object_bytes: int = Field(default=50_000_000, ge=1, strict=True)
    max_egress_bytes: int = Field(default=10_000_000, ge=1, strict=True)

    def to_contract(self) -> QuotaSet:
        return QuotaSet(
            ops_per_minute=self.ops_per_minute,
            max_rows=self.max_rows,
            max_bytes=self.max_bytes,
            max_keys=self.max_keys,
            max_object_bytes=self.max_object_bytes,
            max_egress_bytes=self.max_egress_bytes,
        )


class CapabilitiesBody(BaseModel):
    """Набор capability + egress-политика + квоты для долгоживущего grant."""

    model_config = ConfigDict(extra="forbid")

    capabilities: tuple[str, ...] = Field(min_length=1, max_length=16)
    egress_policy: EgressPolicyBody = Field(default_factory=EgressPolicyBody)
    quotas: QuotaSetBody = Field(default_factory=QuotaSetBody)

    def to_contract(self) -> CapabilitySet:
        return CapabilitySet(
            capabilities=tuple(self.capabilities),
            egress_policy=self.egress_policy.to_contract(),
            quotas=self.quotas.to_contract(),
        )


class GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(pattern=_SAFE_ID_PATTERN, max_length=64)
    plugin_id: str = Field(pattern=_SAFE_ID_PATTERN, max_length=64)
    manifest_version: str = Field(min_length=1, max_length=128, pattern=SEMVER_PATTERN.pattern)
    artifact_digest: str = Field(pattern=_DIGEST_PATTERN)
    capabilities: CapabilitiesBody


class PublicationApprovalBody(GrantBody):
    """Administrative exact pre-approval; no artifact status or Deployment input."""


class RevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(pattern=_SAFE_ID_PATTERN, max_length=64)
    plugin_id: str = Field(pattern=_SAFE_ID_PATTERN, max_length=64)


class UninstallBody(RevokeBody):
    """Тело uninstall идентично revoke: границы — только (company, plugin)."""


class WakeBody(RevokeBody):
    """Тело wake идентично revoke: DSN остаётся тем же, меняется только токен."""


class EnabledModulesBody(BaseModel):
    """Фича-флаг strangler ``plugin_enabled_modules[]`` (§8 этап 2)."""

    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(pattern=_SAFE_ID_PATTERN, max_length=64)
    plugin_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=128)


# --------------------------------------------------------------------------- ответы
# Ответы тоже типизированы: ни одного ``dict[str, Any]`` в контракте эндпоинтов.


class ClaimsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    grant_id: str
    jti: str
    expires_at: str
    ttl_seconds: int


class PublicationApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    company_id: str
    plugin_id: str
    manifest_version: str
    artifact_digest: str
    capabilities: tuple[str, ...]
    egress_policy: EgressPolicyBody
    quotas: QuotaSetBody
    created_by: str
    created_at: str
    consumed_at: str | None
    created: bool
    replayed: bool


class GrantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    plugin_id: str
    plugin_instance_id: str
    manifest_version: str
    artifact_digest: str
    grant_id: str
    grant_status: GrantStatus
    capabilities: tuple[str, ...]
    egress_mode: EgressMode
    database_name: str
    schema_name: str
    role_name: str
    bucket: str
    prefix: str
    webhook_path: str
    pluginpage_path: str
    claims: ClaimsResponse
    replayed: bool
    #: §4.2: egress и квоты живут в долгоживущем grant, а не в коротком токене.
    egress_in_token: bool = False
    quotas_in_token: bool = False


class RevokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    plugin_id: str
    grant_id: str
    grant_status: GrantStatus
    token_revoked: bool
    replayed: bool
    database_preserved: bool


class UninstallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    #: §5.5 / §8: данные dev эфемерны — snapshot и перенос данных не требуются.
    #: Дефолт ``False`` — часть контракта: ответ не может «забыть» сообщить,
    #: что snapshot не делался, и не может неявно утверждать обратное.
    snapshot_taken: bool = False


class WakeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    plugin_id: str
    grant_id: str
    claims: ClaimsResponse
    runtime_status: PluginRuntimeStatus
    replicas: int
    ready: bool
    waited_ms: int


class EffectiveQuotasResponse(BaseModel):
    """Шесть фактических limits из grant; без defaults и неявных coercion."""

    model_config = ConfigDict(extra="forbid")

    ops_per_minute: int = Field(ge=1, strict=True)
    max_rows: int = Field(ge=1, strict=True)
    max_bytes: int = Field(ge=1, strict=True)
    max_keys: int = Field(ge=1, strict=True)
    max_object_bytes: int = Field(ge=1, strict=True)
    max_egress_bytes: int = Field(ge=1, strict=True)


class InstalledPluginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    quotas: EffectiveQuotasResponse | None
    quotas_status: EffectiveQuotasStatus


class PluginListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    items: tuple[InstalledPluginResponse, ...]
    enabled_modules: tuple[str, ...]


class EnabledModulesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    enabled_modules: tuple[str, ...]
    changed: bool
    replayed: bool
    #: §11 критерий 16: переключается только путь, данные не переносятся.
    #: Дефолт ``False`` фиксирует инвариант на уровне контракта ответа.
    data_migration_performed: bool = False


# --------------------------------------------------------------------------- deps


def _ports(request: Request) -> PluginAdminPorts:
    """Взять связку портов из ``app.state`` (подключает родитель в F11).

    Отсутствие интеграции = 503 с явным кодом, а не молчаливое разрешение и не
    подмена реальной auth заглушкой.
    """

    ports = getattr(request.app.state, "plugin_admin_ports", None)
    if not isinstance(ports, PluginAdminPorts):
        raise AppError(
            code="PLUGIN_ADMIN_NOT_CONFIGURED",
            title="Plugin admin not configured",
            status=503,
            detail="PluginAdminPorts не подключён: родительская интеграция F11 не выполнена",
        )
    return ports


def _service(request: Request) -> PluginAdminService:
    service = getattr(request.app.state, "plugin_admin_service", None)
    if not isinstance(service, PluginAdminService):
        raise AppError(
            code="PLUGIN_ADMIN_NOT_CONFIGURED",
            title="Plugin admin not configured",
            status=503,
            detail="PluginAdminService не подключён: родительская интеграция F11 не выполнена",
        )
    return service


async def require_platform_admin(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str:
    """PlatformAdmin-проверка **через порт**, без импорта реальной auth.

    Возвращает ``actor_id`` для аудита. Двойная проверка (порт + флаг в
    ``AdminIdentity``) — защита в глубину: адаптер не может «забыть» про роль.
    """

    port: PlatformAdminPort = _ports(request).platform_admin
    try:
        identity = await port.require_platform_admin(authorization=authorization)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - недоступная auth ≠ успех
        raise AppError(
            code="UNAUTHORIZED",
            title="Unauthorized",
            status=401,
            detail="PlatformAdmin-проверка недоступна",
        ) from exc
    if identity is None or not identity.is_platform_admin or not identity.actor_id.strip():
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="platform.admin required",
        )
    return identity.actor_id


PlatformAdminActor = Annotated[str, Depends(require_platform_admin)]


# --------------------------------------------------------------------------- helpers


def _claims_response(claims: IssuedPluginClaims) -> ClaimsResponse:
    return ClaimsResponse(
        token=claims.token,
        grant_id=claims.grant_id,
        jti=claims.jti,
        expires_at=claims.expires_at.isoformat(),
        ttl_seconds=claims.ttl_seconds,
    )


def _effective_quotas_response(quotas: QuotaSet | None) -> EffectiveQuotasResponse | None:
    if quotas is None:
        return None
    return EffectiveQuotasResponse(
        ops_per_minute=quotas.ops_per_minute,
        max_rows=quotas.max_rows,
        max_bytes=quotas.max_bytes,
        max_keys=quotas.max_keys,
        max_object_bytes=quotas.max_object_bytes,
        max_egress_bytes=quotas.max_egress_bytes,
    )


def _service_error(exc: Exception) -> AppError:
    """Ошибки контрактов (ValueError) → 422, всё остальное пробрасываем как есть."""

    if isinstance(exc, EffectiveGrantReadError):
        return AppError(
            code=exc.code,
            title="Effective grants unavailable",
            status=503,
            detail=str(exc),
            extra={
                "reason": exc.reason,
                "company_id": exc.company_id,
                "plugin_id": exc.plugin_id or "",
            },
        )
    if isinstance(exc, PluginAdminError):
        return exc
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, (ValueError, TypeError)):
        return AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=str(exc),
        )
    raise exc


# --------------------------------------------------------------------------- endpoints


@router.post("/publication-approvals", response_model=PublicationApprovalResponse)
async def approve_plugin_publication(
    body: PublicationApprovalBody,
    request: Request,
    actor_id: PlatformAdminActor,
) -> PublicationApprovalResponse:
    """Pre-approve exact bundle/policy before publication; never install or grant."""

    try:
        receipt = await _service(request).approve_publication(
            actor_id=actor_id,
            company_id=body.company_id,
            plugin_id=body.plugin_id,
            manifest_version=body.manifest_version,
            artifact_digest=body.artifact_digest,
            capabilities=body.capabilities.to_contract(),
        )
    except Exception as exc:  # noqa: BLE001
        raise _service_error(exc) from exc
    approval = receipt.approval
    return PublicationApprovalResponse(
        approval_id=approval.approval_id,
        company_id=approval.company_id,
        plugin_id=approval.plugin_id,
        manifest_version=approval.manifest_version,
        artifact_digest=approval.artifact_digest,
        capabilities=approval.capabilities,
        egress_policy=EgressPolicyBody(
            mode=approval.egress_policy.mode,
            allowed_hosts=approval.egress_policy.allowed_hosts,
            allowed_paths=approval.egress_policy.allowed_paths,
            approved_for_full=approval.egress_policy.approved_for_full,
        ),
        quotas=QuotaSetBody(
            ops_per_minute=approval.quotas.ops_per_minute,
            max_rows=approval.quotas.max_rows,
            max_bytes=approval.quotas.max_bytes,
            max_keys=approval.quotas.max_keys,
            max_object_bytes=approval.quotas.max_object_bytes,
            max_egress_bytes=approval.quotas.max_egress_bytes,
        ),
        created_by=approval.created_by,
        created_at=approval.created_at.isoformat(),
        consumed_at=(
            approval.consumed_at.isoformat() if approval.consumed_at is not None else None
        ),
        created=receipt.created,
        replayed=receipt.replayed,
    )


@router.post("/grant", response_model=GrantResponse, status_code=201)
async def grant_plugin(
    body: GrantBody,
    request: Request,
    _: PlatformAdminActor,
) -> GrantResponse:
    """Установка плагина. QUARANTINED/отсутствующий артефакт → ARTIFACT_NOT_PUBLISHED."""

    try:
        receipt = await _service(request).grant(
            company_id=body.company_id,
            plugin_id=body.plugin_id,
            manifest_version=body.manifest_version,
            artifact_digest=body.artifact_digest,
            capabilities=body.capabilities.to_contract(),
        )
    except Exception as exc:  # noqa: BLE001 - единая точка маппинга ошибок сервиса
        raise _service_error(exc) from exc
    return GrantResponse(
        company_id=receipt.company_id,
        plugin_id=receipt.plugin_id,
        plugin_instance_id=receipt.plugin_instance_id,
        manifest_version=receipt.manifest_version,
        artifact_digest=receipt.artifact_digest,
        grant_id=receipt.grant.grant_id,
        grant_status=receipt.grant.status,
        capabilities=receipt.grant.capabilities,
        egress_mode=receipt.grant.egress_policy.mode,
        database_name=receipt.database.database_name,
        schema_name=receipt.schema.schema_name,
        role_name=receipt.schema.role_name,
        bucket=receipt.objects.bucket,
        prefix=receipt.objects.prefix,
        webhook_path=receipt.routes.webhook_path,
        pluginpage_path=receipt.routes.pluginpage_path,
        claims=_claims_response(receipt.claims),
        replayed=receipt.replayed,
    )


@router.post("/revoke", response_model=RevokeResponse)
async def revoke_plugin(
    body: RevokeBody,
    request: Request,
    _: PlatformAdminActor,
) -> RevokeResponse:
    """Деактивация grant + мгновенный отзыв по ``grant_id``; БД/схема сохраняются."""

    try:
        receipt = await _service(request).revoke(
            company_id=body.company_id, plugin_id=body.plugin_id
        )
    except Exception as exc:  # noqa: BLE001
        raise _service_error(exc) from exc
    return RevokeResponse(
        company_id=receipt.company_id,
        plugin_id=receipt.plugin_id,
        grant_id=receipt.grant_id,
        grant_status=receipt.status,
        token_revoked=receipt.token_revoked,
        replayed=receipt.replayed,
        database_preserved=receipt.database_preserved,
    )


@router.post("/uninstall", response_model=UninstallResponse)
async def uninstall_plugin(
    body: UninstallBody,
    request: Request,
    _: PlatformAdminActor,
) -> UninstallResponse:
    """Purge infra + DROP SCHEMA/ROLE + bucket/prefix + запись реестра. Без snapshot."""

    try:
        receipt = await _service(request).uninstall(
            company_id=body.company_id, plugin_id=body.plugin_id
        )
    except Exception as exc:  # noqa: BLE001
        raise _service_error(exc) from exc
    return UninstallResponse(
        company_id=receipt.company_id,
        plugin_id=receipt.plugin_id,
        grant_id=receipt.grant_id,
        deleted=receipt.deleted,
        already_absent=receipt.already_absent,
        schema_dropped=receipt.schema_dropped,
        role_dropped=receipt.role_dropped,
        prefix_dropped=receipt.prefix_dropped,
        bucket_dropped=receipt.bucket_dropped,
        artifact_unlinked=receipt.artifact_unlinked,
        tenant_infra_purged=receipt.tenant_infra_purged,
        token_revoked=receipt.token_revoked,
        completed_steps=receipt.completed_steps,
        snapshot_taken=receipt.snapshot_taken,
    )


@router.post("/wake", response_model=WakeResponse, status_code=202)
async def wake_plugin(
    body: WakeBody,
    request: Request,
    _: PlatformAdminActor,
) -> WakeResponse:
    """0→1 через activator + новый короткий ``PluginClaims`` (§7.4, §11 кр. 13)."""

    try:
        receipt = await _service(request).wake(
            company_id=body.company_id, plugin_id=body.plugin_id
        )
    except Exception as exc:  # noqa: BLE001
        raise _service_error(exc) from exc
    return WakeResponse(
        company_id=receipt.company_id,
        plugin_id=receipt.plugin_id,
        grant_id=receipt.grant_id,
        claims=_claims_response(receipt.claims),
        runtime_status=receipt.runtime_status,
        replicas=receipt.wake.replicas,
        ready=receipt.wake.ready,
        waited_ms=receipt.wake.waited_ms,
    )


@router.get("/", response_model=PluginListResponse)
async def list_plugins(
    request: Request,
    _: PlatformAdminActor,
    company_id: Annotated[str, Query(pattern=_SAFE_ID_PATTERN, max_length=64)],
) -> PluginListResponse:
    """Установленные плагины компании + статусы grant/runtime/фича-флага.

    Администратор явно указывает компанию в query; сервис валидирует её и не
    выходит за границы ``company_id`` ни в одном вызове порта.
    """

    try:
        snapshot = await _service(request).list(company_id=company_id)
    except Exception as exc:  # noqa: BLE001
        raise _service_error(exc) from exc
    return PluginListResponse(
        company_id=snapshot.company_id,
        items=tuple(
            InstalledPluginResponse(
                plugin_id=item.plugin_id,
                plugin_instance_id=item.plugin_instance_id,
                manifest_version=item.manifest_version,
                artifact_digest=item.artifact_digest,
                grant_id=item.grant_id,
                grant_status=item.grant_status,
                runtime_status=item.runtime_status,
                enabled=item.enabled,
                capabilities=item.capabilities,
                egress_mode=item.egress_mode,
                quotas=_effective_quotas_response(item.quotas),
                quotas_status=item.quotas_status,
            )
            for item in snapshot.items
        ),
        enabled_modules=snapshot.enabled_modules,
    )


@router.put("/enabled-modules", response_model=EnabledModulesResponse)
async def set_enabled_modules(
    body: EnabledModulesBody,
    request: Request,
    _: PlatformAdminActor,
) -> EnabledModulesResponse:
    """Переключение пути strangler без переноса данных (§8 этап 2, §11 кр. 16)."""

    try:
        receipt = await _service(request).set_enabled_modules(
            company_id=body.company_id, plugin_ids=body.plugin_ids
        )
    except Exception as exc:  # noqa: BLE001
        raise _service_error(exc) from exc
    return EnabledModulesResponse(
        company_id=receipt.company_id,
        enabled_modules=receipt.enabled_modules,
        changed=receipt.changed,
        replayed=receipt.replayed,
        data_migration_performed=receipt.data_migration_performed,
    )


__all__ = ["router"]

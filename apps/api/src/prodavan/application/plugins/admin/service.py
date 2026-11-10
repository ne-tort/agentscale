"""``PluginAdminService`` — Admin API lifecycle плагинов (F3-admin, задача 12).

Реализует §5.5 дизайна v4 и acceptance-критерии §11 №12, №15, №16:

* ``grant``   — только по артефакту со статусом PUBLISHED (не QUARANTINED); БД компании,
  роль/схема, bucket/префикс, долгоживущий ``CapabilityGrant`` с egress-политикой и
  квотами, короткий ``PluginClaims``, маршруты, аудит;
* ``revoke``  — деактивация grant + отзыв по ``grant_id`` (мгновенно), снятие маршрутов,
  ``Suspended``; **БД/схема/роль сохраняются**;
* ``uninstall`` — purge tenant infra + drop bucket/prefix + DROP SCHEMA/ROLE + удаление
  артефакта из реестра. Данные dev эфемерны: snapshot НЕ требуется, переноса данных НЕТ;
* ``wake``    — через activator, новый короткий токен, DSN остаётся тем же;
* ``list``    — установленные плагины + статусы;
* ``set_enabled_modules`` — фича-флаг ``plugin_enabled_modules[]`` (§8 этап 2):
  переключается только путь, без миграции данных.

Принципы:
  * **Fail-closed.** Отсутствие записи в реестре, статус не PUBLISHED, неподтверждённый
    отзыв, неподтверждённый аудит, частичный purge — отказ, а не молчаливое разрешение.
    Все проверки выполняются ДО позитивных мутаций инфраструктуры.
  * **Идемпотентность.** Повтор операции с тем же смыслом возвращает тот же результат
    (``replayed=True`` / ``already_absent=True``) и не создаёт дубликатов.
  * **Изоляция компаний.** Любой вызов порта получает ``company_id`` из авторитетного
    аргумента; ни один метод не принимает «чужую» компанию из тела запроса и не умеет
    перечислять/изменять другие компании (§11 критерий 15).
  * **Никакого переноса данных.** Ни ``migrate``, ни snapshot, ни dual-read/dual-write,
    ни бэкапов (§8, §13.1).

Сервис не импортирует FastAPI/Pydantic/SQLAlchemy — только собственные контракты и
``Protocol`` из ``ports.py``. Это позволяет покрыть весь lifecycle unit-тестами на фейках.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import uuid4

from prodavan.application.plugins.capabilities.adapters.secrets import SecretsDeliveryResponse
from prodavan.application.plugins.installation_scope.contracts import InstallationScope
from prodavan.application.plugins.tenantdb.service import StoredSecret
from prodavan.domain.errors import AppError
from prodavan.domain.plugins.helpers import plugin_instance_id_for
from prodavan.domain.plugins.types import SEMVER_PATTERN

from .contracts import (
    PLUGIN_CLAIMS_TTL_SECONDS,
    QUOTA_KEYS,
    REGRANT_ID_CONFLICT_CODE,
    UPDATE_INCONSISTENT_CODE,
    AdminAuditEvent,
    AdminDecision,
    AdminOperation,
    ArtifactPinUpdateResult,
    ArtifactPublicationStatus,
    CapabilityGrantRecord,
    CapabilitySet,
    EffectiveQuotasStatus,
    EnabledModulesReceipt,
    GrantReceipt,
    GrantStatus,
    InstalledPlugin,
    IssuedPluginClaims,
    MintRequest,
    PluginInstanceRecord,
    PluginInstanceStatus,
    PluginListSnapshot,
    PluginPublicationApproval,
    PluginRuntimeBinding,
    PluginRuntimeStatus,
    PluginSchema,
    PluginUpdateLockLease,
    PublicationApprovalReceipt,
    QuotaSet,
    RevokeReceipt,
    SecretPreDeliveryRequest,
    UninstallReceipt,
    WakeReceipt,
)
from .errors import (
    PluginAdminError,
    artifact_not_published,
    grant_exists,
    grant_not_found,
    revoke_failed,
    uninstall_partial,
)
from .ports import PluginAdminPorts, webhook_secret_key

# Идентификаторы, приходящие извне, не должны попадать в SQL-имена, пути Ingress или
# имена k8s-ресурсов без проверки (§2.2, §5.4). Жёсткий allowlist — часть fail-closed.
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _validate_company(*, company_id: str) -> None:
    """Проверить компанию до любого обращения к порту."""

    if not _SAFE_ID.fullmatch(company_id or ""):
        raise ValueError("недопустимый company_id: ожидается [a-z0-9_-] длиной 1..64")


def _validate_identity(*, company_id: str, plugin_id: str) -> None:
    """Проверить компанию и плагин до любого обращения к порту."""

    _validate_company(company_id=company_id)
    if not _SAFE_ID.fullmatch(plugin_id or ""):
        raise ValueError("недопустимый plugin_id: ожидается [a-z0-9_-] длиной 1..64")


def _validate_version(manifest_version: str) -> None:
    # Publication и approval связаны с artifact SemVer; legacy loose-строки
    # из HTTP тела не допускаются даже если они проходят безопасный ID-pattern.
    if SEMVER_PATTERN.fullmatch(manifest_version or "") is None:
        raise ValueError("недопустимый manifest_version: ожидается SemVer 2.0.0")


def _validate_digest(artifact_digest: str) -> None:
    if not _DIGEST.fullmatch(artifact_digest or ""):
        raise ValueError("artifact_digest должен быть sha256:<64 hex>")


class EffectiveGrantReadError(RuntimeError):
    """Типизированный fail-closed отказ read-only effective grants/quotas."""

    code = "EFFECTIVE_GRANTS_UNAVAILABLE"

    def __init__(
        self, *, reason: str, company_id: str, plugin_id: str | None = None
    ) -> None:
        self.reason = reason
        self.company_id = company_id
        self.plugin_id = plugin_id
        super().__init__(f"effective grants/quotas unavailable: {reason}")


class PluginAdminService:
    """Оркестратор lifecycle. Один экземпляр на приложение, без состояния.

    ``ports`` обязателен и содержит все зависимости: собрать «полуподключённый»
    сервис нельзя. ``clock``/``id_factory`` инъецируются для детерминированных
    unit-тестов; выпуск токенов идёт только через ``MintPort`` (задача 02).
    """

    def __init__(
        self,
        ports: PluginAdminPorts,
        *,
        clock: "object | None" = None,
        id_factory: "object | None" = None,
    ) -> None:
        if ports is None:
            # Fail-closed: без адаптеров сервис не должен существовать вовсе.
            raise ValueError("PluginAdminPorts обязателен: отсутствие интеграции = явная ошибка")
        self._ports = ports
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_id = id_factory or (lambda: str(uuid4()))

    # ------------------------------------------------------------------ часы / id

    def _now(self) -> datetime:
        value = self._clock()  # type: ignore[operator]
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("clock должен возвращать timezone-aware datetime")
        return value

    def _uid(self) -> str:
        value = self._new_id()  # type: ignore[operator]
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("id_factory должен возвращать непустую строку")
        return value

    # ------------------------------------------------------------------ webhook secrets
    def _webhook_store(self):
        store = self._ports.webhook_secrets
        if store is None or any(
            not callable(getattr(store, method, None))
            for method in ("get", "put", "rotate", "delete")
        ):
            raise RuntimeError("webhook SecretStore is not wired")
        return store

    async def _read_webhook_secret(
        self,
        instance: PluginInstanceRecord | CapabilityGrantRecord,
        expected_ref: str,
    ) -> StoredSecret:
        if (
            not isinstance(expected_ref, str)
            or not expected_ref
            or expected_ref != expected_ref.strip()
            or len(expected_ref) > 2048
            or any(char.isspace() or char == "\0" for char in expected_ref)
            or not expected_ref.startswith(("file://", "vault://"))
        ):
            raise RuntimeError("webhook secret reference is absent or invalid")
        key = webhook_secret_key(
            company_id=instance.company_id,
            plugin_id=instance.plugin_id,
            plugin_instance_id=instance.plugin_instance_id,
        )
        try:
            stored = await self._webhook_store().get(key=key)
        except Exception:
            raise RuntimeError("webhook secret resolution failed") from None
        if (
            not isinstance(stored, StoredSecret)
            or stored.secret_ref != expected_ref
            or not isinstance(stored.value, str)
            or not stored.value.strip()
        ):
            raise RuntimeError("webhook secret is missing, foreign, or empty")
        return stored

    async def _provision_webhook_secret(
        self,
        instance: PluginInstanceRecord,
        *,
        rotate: bool,
    ) -> str:
        key = webhook_secret_key(
            company_id=instance.company_id,
            plugin_id=instance.plugin_id,
            plugin_instance_id=instance.plugin_instance_id,
        )
        # 48 random bytes = 384 bits; the value exists only in this local call.
        value = token_urlsafe(48)
        try:
            store = self._webhook_store()
            if rotate:
                candidate_ref = await store.rotate(key=key, value=value)
            else:
                candidate_ref = await store.put(key=key, value=value)
        except Exception:
            raise RuntimeError("webhook secret provisioning failed") from None
        # Resolve by the canonical key and return the backend's actual ref, never
        # a fabricated path or the plaintext value.
        stored = await self._read_webhook_secret(instance, candidate_ref)
        return stored.secret_ref

    async def _revoke_webhook_secret(self, grant: CapabilityGrantRecord) -> bool:
        ref = grant.webhook_secret_ref
        key = webhook_secret_key(
            company_id=grant.company_id,
            plugin_id=grant.plugin_id,
            plugin_instance_id=grant.plugin_instance_id,
        )
        try:
            store = self._webhook_store()
            if ref is None:
                # A legacy grant never resolves for verification. Revoke may only
                # remove an actually stored canonical value, never fabricate one.
                existing = await store.get(key=key)
                if existing is None:
                    return True
                if not isinstance(existing, StoredSecret):
                    raise RuntimeError("webhook secret revocation failed")
                ref = existing.secret_ref
                await self._read_webhook_secret(grant, ref)
            await store.delete(secret_ref=ref)
            remaining = await store.get(key=key)
        except Exception:
            raise RuntimeError("webhook secret revocation failed") from None
        return remaining is None

    # ------------------------------------------------------------------ control-plane delivery
    def _delivery_control(self):
        port = self._ports.secret_delivery
        if port is None:
            return None
        if not all(
            callable(getattr(port, method, None))
            for method in ("pre_deliver", "pre_revoke")
        ):
            raise RuntimeError("control-plane secret delivery is not wired")
        return port

    @staticmethod
    def _delivery_request(
        *,
        operation: str,
        instance: PluginInstanceRecord,
        manifest_version: str,
        artifact_digest: str,
    ) -> SecretPreDeliveryRequest:
        return SecretPreDeliveryRequest(
            request_id=(
                f"{operation}:{instance.plugin_instance_id}:{artifact_digest}"
            ),
            installation=instance,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
        )

    async def _pre_deliver_secrets(
        self,
        *,
        operation: str,
        instance: PluginInstanceRecord,
        manifest_version: str,
        artifact_digest: str,
    ) -> tuple[SecretsDeliveryResponse, ...]:
        port = self._delivery_control()
        if port is None:
            return ()
        request = self._delivery_request(
            operation=operation,
            instance=instance,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
        )
        try:
            receipts = await port.pre_deliver(request)
        except Exception:
            raise RuntimeError("control-plane secret pre-delivery failed") from None
        if not isinstance(receipts, tuple) or any(
            not isinstance(receipt, SecretsDeliveryResponse)
            or not receipt.ok
            or not receipt.delivered
            or receipt.request_id != request.request_id
            for receipt in receipts
        ):
            raise RuntimeError("control-plane secret pre-delivery returned invalid receipts")
        return receipts

    async def _pre_revoke_secrets(
        self,
        *,
        operation: str,
        instance: PluginInstanceRecord | None,
    ) -> bool:
        if instance is None:
            return True
        port = self._delivery_control()
        if port is None:
            return True
        request = self._delivery_request(
            operation=operation,
            instance=instance,
            manifest_version=instance.manifest_version,
            artifact_digest=instance.artifact_digest,
        )
        try:
            receipts = await port.pre_revoke(request)
        except Exception:
            raise RuntimeError("control-plane secret revoke failed") from None
        if not isinstance(receipts, tuple) or any(
            not isinstance(receipt, SecretsDeliveryResponse)
            or not receipt.ok
            or receipt.delivered
            or receipt.request_id != request.request_id
            for receipt in receipts
        ):
            raise RuntimeError("control-plane secret revoke returned invalid receipts")
        return True

    # ------------------------------------------------------------------ аудит

    async def _audit(
        self,
        *,
        operation: AdminOperation,
        company_id: str,
        plugin_id: str,
        decision: AdminDecision,
        grant_id: str | None = None,
        artifact_digest: str | None = None,
        code: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Append-only событие. Отказ записи аудита = отказ позитивной мутации (§4.5)."""

        event = AdminAuditEvent(
            event_id=self._uid(),
            occurred_at=self._now(),
            company_id=company_id,
            plugin_id=plugin_id,
            operation=operation,
            decision=decision,
            grant_id=grant_id,
            artifact_digest=artifact_digest,
            code=code,
            reason=reason,
        )
        recorded = await self._ports.audit.append(event=event)
        if not recorded and decision is AdminDecision.ALLOW:
            # Позитивное решение без зафиксированного следа недопустимо.
            raise RuntimeError("audit append failed: позитивная мутация отклонена (fail-closed)")

    async def _deny_audit(
        self,
        *,
        operation: AdminOperation,
        company_id: str,
        plugin_id: str,
        code: str,
        reason: str,
        grant_id: str | None = None,
        artifact_digest: str | None = None,
    ) -> None:
        """Отказ тоже пишется в аудит, но его неудача не маскирует сам отказ."""

        await self._audit(
            operation=operation,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.DENY,
            code=code,
            reason=reason,
            grant_id=grant_id,
            artifact_digest=artifact_digest,
        )

    async def _decision_audit(
        self,
        *,
        operation: AdminOperation,
        company_id: str,
        plugin_id: str,
        grant_id: str | None = None,
    ) -> None:
        """Зафиксировать **решение** до первой мутации (§4.5).

        Если append-only sink недоступен, позитивная мутация не начинается вовсе: это
        устраняет состояние «инфраструктура изменена, а следа нет» и делает отказ
        настоящим fail-closed, а не post-factum исключением.
        """

        await self._audit(
            operation=operation,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.ALLOW,
            grant_id=grant_id,
            reason="decision recorded before mutation",
        )

    # ------------------------------------------------------------------ publication approval

    @staticmethod
    def _approval_error(
        *,
        code: str,
        detail: str,
        company_id: str,
        plugin_id: str,
        status: int = 409,
    ) -> AppError:
        return AppError(
            code=code,
            title=code,
            status=status,
            detail=detail,
            extra={"company_id": company_id, "plugin_id": plugin_id},
        )

    async def _read_publication_approval(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
    ) -> PluginPublicationApproval | None:
        store = self._ports.publication_approvals
        if store is None:
            return None
        try:
            approval = await store.get(
                company_id=company_id,
                plugin_id=plugin_id,
                manifest_version=manifest_version,
                artifact_digest=artifact_digest,
            )
        except Exception as exc:  # noqa: BLE001 - unknown approval is never allow
            raise self._approval_error(
                code="PUBLICATION_APPROVAL_UNAVAILABLE",
                detail="Не удалось прочитать durable publication approval",
                company_id=company_id,
                plugin_id=plugin_id,
                status=503,
            ) from exc
        if approval is not None:
            self._assert_publication_approval(
                approval,
                company_id=company_id,
                plugin_id=plugin_id,
                manifest_version=manifest_version,
                artifact_digest=artifact_digest,
            )
        return approval

    def _assert_publication_approval(
        self,
        approval: PluginPublicationApproval,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
        capabilities: CapabilitySet | None = None,
    ) -> None:
        valid = isinstance(approval, PluginPublicationApproval)
        if valid:
            valid = (
                approval.company_id,
                approval.plugin_id,
                approval.manifest_version,
                approval.artifact_digest,
            ) == (company_id, plugin_id, manifest_version, artifact_digest)
        if valid and capabilities is not None:
            valid = approval.capability_set == capabilities
        if not valid:
            raise self._approval_error(
                code="PUBLICATION_APPROVAL_BINDING_MISMATCH",
                detail="Publication approval не совпадает с exact artifact/capability binding",
                company_id=company_id,
                plugin_id=plugin_id,
            )

    async def approve_publication(
        self,
        *,
        actor_id: str,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
        capabilities: CapabilitySet,
    ) -> PublicationApprovalReceipt:
        """Persist an administrative decision without artifact/grant/Deployment."""
        _validate_identity(company_id=company_id, plugin_id=plugin_id)
        _validate_version(manifest_version)
        _validate_digest(artifact_digest)
        if not isinstance(capabilities, CapabilitySet):
            raise TypeError("capabilities должен быть CapabilitySet (типизированный контракт)")
        if (
            not isinstance(actor_id, str)
            or not actor_id
            or actor_id != actor_id.strip()
            or len(actor_id) > 160
        ):
            raise ValueError("actor_id обязан быть непустой stripped-строкой длиной <= 160")
        store = self._ports.publication_approvals
        if store is None:
            raise self._approval_error(
                code="PUBLICATION_APPROVAL_UNAVAILABLE",
                detail="Publication approval store не подключён",
                company_id=company_id,
                plugin_id=plugin_id,
                status=503,
            )

        existing = await self._read_publication_approval(
            company_id=company_id,
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
        )
        if existing is not None:
            self._assert_publication_approval(
                existing,
                company_id=company_id,
                plugin_id=plugin_id,
                manifest_version=manifest_version,
                artifact_digest=artifact_digest,
                capabilities=capabilities,
            )
            await self._audit(
                operation=AdminOperation.APPROVE_PUBLICATION,
                company_id=company_id,
                plugin_id=plugin_id,
                decision=AdminDecision.ALLOW,
                artifact_digest=artifact_digest,
                reason="idempotent exact publication approval replay",
            )
            return PublicationApprovalReceipt(
                approval=existing, created=False, replayed=True
            )

        candidate = PluginPublicationApproval(
            approval_id=f"ppr_{self._uid()}",
            company_id=company_id,
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            capabilities=capabilities.capabilities,
            egress_policy=capabilities.egress_policy,
            quotas=capabilities.quotas,
            created_by=actor_id,
            created_at=self._now(),
        )
        await self._decision_audit(
            operation=AdminOperation.APPROVE_PUBLICATION,
            company_id=company_id,
            plugin_id=plugin_id,
        )
        try:
            stored = await store.create(approval=candidate)
        except Exception as exc:  # noqa: BLE001
            raise self._approval_error(
                code="PUBLICATION_APPROVAL_UNAVAILABLE",
                detail="Не удалось сохранить durable publication approval",
                company_id=company_id,
                plugin_id=plugin_id,
                status=503,
            ) from exc
        self._assert_publication_approval(
            stored,
            company_id=company_id,
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            capabilities=capabilities,
        )
        created = stored.approval_id == candidate.approval_id
        await self._audit(
            operation=AdminOperation.APPROVE_PUBLICATION,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.ALLOW,
            artifact_digest=artifact_digest,
            reason="publication approval persisted" if created else "exact binding won concurrent replay",
        )
        return PublicationApprovalReceipt(
            approval=stored, created=created, replayed=not created
        )

    async def _consume_publication_approval(
        self, approval: PluginPublicationApproval | None
    ) -> None:
        if approval is None or approval.consumed_at is not None:
            return
        store = self._ports.publication_approvals
        if store is None:
            raise RuntimeError("approval store disappeared before consumption")
        try:
            stored = await store.mark_consumed(
                approval_id=approval.approval_id, consumed_at=self._now()
            )
        except Exception as exc:  # noqa: BLE001
            raise self._approval_error(
                code="PUBLICATION_APPROVAL_UNAVAILABLE",
                detail="ACTIVE grant создан, но approval consumption не подтверждён; повторите grant",
                company_id=approval.company_id,
                plugin_id=approval.plugin_id,
                status=503,
            ) from exc
        if stored is None or stored.approval_id != approval.approval_id:
            raise RuntimeError("approval consumption не подтверждён durable repository")
        self._assert_publication_approval(
            stored,
            company_id=approval.company_id,
            plugin_id=approval.plugin_id,
            manifest_version=approval.manifest_version,
            artifact_digest=approval.artifact_digest,
            capabilities=approval.capability_set,
        )
        if stored.consumed_at is None:
            raise RuntimeError("approval consumption returned NULL consumed_at")

    # ------------------------------------------------------------------ grant

    async def _ensure_installation_scope(
        self, *, company_id: str, plugin_id: str, plugin_instance_id: str
    ) -> InstallationScope:
        store = self._ports.installation_scopes
        if store is None:
            raise AppError(
                code="PLUGIN_INSTALLATION_SCOPE_UNAVAILABLE",
                title="Plugin installation scope unavailable",
                status=503,
                detail="installation scope repository is not wired",
            )
        scope = await store.ensure_from_source(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
        )
        if (
            scope.company_id != company_id
            or scope.plugin_instance_id != plugin_instance_id
            or not scope.cabinet_id
            or not scope.project_id
        ):
            raise AppError(
                code="PLUGIN_BOOTSTRAP_SCOPE_MISMATCH",
                title="Plugin installation scope mismatch",
                status=503,
                detail="scope repository returned an invalid installation binding",
            )
        return scope

    async def grant(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
        capabilities: CapabilitySet,
    ) -> GrantReceipt:
        """Initial/replay saga plus deployment-first update for an installed module."""
        _validate_identity(company_id=company_id, plugin_id=plugin_id)
        _validate_version(manifest_version)
        _validate_digest(artifact_digest)
        if not isinstance(capabilities, CapabilitySet):
            raise TypeError("capabilities должен быть CapabilitySet (типизированный контракт)")

        artifact = await self._ports.artifact_registry.get(
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
        )
        if artifact is None or artifact.status is not ArtifactPublicationStatus.PUBLISHED:
            error = artifact_not_published(
                plugin_id=plugin_id,
                manifest_version=manifest_version,
                artifact_digest=artifact_digest,
            )
            await self._deny_audit(
                operation=AdminOperation.GRANT,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="artifact is not PUBLISHED",
            )
            raise error

        existing = await self._ports.grant_store.get(company_id=company_id, plugin_id=plugin_id)
        instance = await self._ports.instance_store.get(
            company_id=company_id, plugin_id=plugin_id
        )
        update_requested = False
        if existing is not None:
            same_artifact = (
                existing.artifact_digest == artifact_digest
                and existing.manifest_version == manifest_version
            )
            same_policy = (
                existing.capabilities == capabilities.capabilities
                and existing.egress_policy == capabilities.egress_policy
                and existing.quotas == capabilities.quotas
            )
            same = same_artifact and same_policy
            update_requested = bool(
                existing.is_active
                and instance is not None
                and instance.status in {
                    PluginInstanceStatus.ACTIVE,
                    PluginInstanceStatus.QUARANTINED,
                }
                and same_policy
                and not same_artifact
            )
            if not same and not update_requested:
                error = grant_exists(company_id=company_id, plugin_id=plugin_id)
                await self._deny_audit(
                    operation=AdminOperation.GRANT,
                    company_id=company_id,
                    plugin_id=plugin_id,
                    code=error.code,
                    reason="grant with different capabilities or invalid update state",
                )
                raise error
        approval: PluginPublicationApproval | None = None
        try:
            approval = await self._read_publication_approval(
                company_id=company_id,
                plugin_id=plugin_id,
                manifest_version=manifest_version,
                artifact_digest=artifact_digest,
            )
            if approval is not None:
                self._assert_publication_approval(
                    approval,
                    company_id=company_id,
                    plugin_id=plugin_id,
                    manifest_version=manifest_version,
                    artifact_digest=artifact_digest,
                    capabilities=capabilities,
                )
        except AppError as exc:
            await self._deny_audit(
                operation=AdminOperation.GRANT,
                company_id=company_id,
                plugin_id=plugin_id,
                code=exc.code,
                reason="publication approval unavailable or mismatched",
            )
            raise

        if approval is None and (
            existing is None or not existing.is_active or update_requested
        ):
            error = self._approval_error(
                code="PUBLICATION_APPROVAL_REQUIRED",
                detail="Новый/revoked/update grant требует durable publication approval",
                company_id=company_id,
                plugin_id=plugin_id,
            )
            await self._deny_audit(
                operation=AdminOperation.GRANT,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="new grant without administrative pre-approval",
            )
            raise error
        # Единственный путь без approval — exact replay уже существующего ACTIVE
        # legacy grant. Update нового artifact всегда требует отдельный approval.
        if approval is not None and existing is not None and not update_requested:
            grant_matches = (
                existing.artifact_digest == approval.artifact_digest
                and existing.manifest_version == approval.manifest_version
                and existing.capabilities == approval.capabilities
                and existing.egress_policy == approval.egress_policy
                and existing.quotas == approval.quotas
            )
            if not grant_matches:
                error = self._approval_error(
                    code="PUBLICATION_APPROVAL_BINDING_MISMATCH",
                    detail="Persisted grant не совпадает с publication approval",
                    company_id=company_id,
                    plugin_id=plugin_id,
                )
                await self._deny_audit(
                    operation=AdminOperation.GRANT,
                    company_id=company_id,
                    plugin_id=plugin_id,
                    code=error.code,
                    reason="stale persisted grant/approval binding",
                )
                raise error
        if approval is not None and approval.consumed_at is not None and existing is None:
            error = self._approval_error(
                code="PUBLICATION_APPROVAL_CONSUMED",
                detail="Publication approval уже consumed и не может создать новый grant",
                company_id=company_id,
                plugin_id=plugin_id,
            )
            await self._deny_audit(
                operation=AdminOperation.GRANT,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="consumed approval cannot create another grant",
            )
            raise error

        if instance is not None and (
            (
                not update_requested
                and (
                    instance.manifest_version != manifest_version
                    or instance.artifact_digest != artifact_digest
                )
            )
            or (
                update_requested
                and existing is not None
                and (
                    instance.manifest_version != existing.manifest_version
                    or instance.artifact_digest != existing.artifact_digest
                )
            )
            or (
                existing is not None
                and instance.plugin_instance_id != existing.plugin_instance_id
            )
        ):
            raise grant_exists(company_id=company_id, plugin_id=plugin_id)

        if (
            existing is not None
            and existing.is_active
            and instance is not None
            and instance.status is PluginInstanceStatus.QUARANTINED
            and (approval is None or approval.consumed_at is not None)
        ):
            error = self._update_inconsistent_error(
                company_id=company_id,
                plugin_id=plugin_id,
                detail="Replay/wake заблокирован до confirmed update recovery",
                marker_confirmed=True,
            )
            await self._deny_audit(
                operation=AdminOperation.GRANT,
                company_id=company_id,
                plugin_id=plugin_id,
                grant_id=existing.grant_id,
                code=error.code,
                reason="UPDATE_INCONSISTENT blocks old digest reactivation",
            )
            raise error

        if update_requested:
            assert existing is not None and instance is not None
            return await self._update_installed_grant(
                existing=existing,
                instance=instance,
                manifest_version=manifest_version,
                artifact_digest=artifact_digest,
                approval=approval,
            )

        if existing is not None and existing.is_active:
            # Replay never mints a replacement for a legacy/missing reference.
            if existing.webhook_secret_ref is None:
                raise RuntimeError("active grant has no durable webhook_secret_ref")
            webhook_secret_ref = (
                await self._read_webhook_secret(existing, existing.webhook_secret_ref)
            ).secret_ref
            # Replay обязан восстановить недоделанные ensure-шаги, но не пароль.
            await self._decision_audit(
                operation=AdminOperation.GRANT,
                company_id=company_id,
                plugin_id=plugin_id,
                grant_id=existing.grant_id,
            )
            if instance is None:
                instance = await self._ports.instance_store.create(
                    instance=self._new_instance(
                        company_id=company_id,
                        plugin_id=plugin_id,
                        manifest_version=manifest_version,
                        artifact_digest=artifact_digest,
                        instance_id=existing.plugin_instance_id,
                    )
                )
            if instance.plugin_instance_id != existing.plugin_instance_id:
                raise grant_exists(company_id=company_id, plugin_id=plugin_id)
            await self._ensure_installation_scope(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=instance.plugin_instance_id,
            )
            database = await self._ports.tenant_db.ensure_company_database(company_id=company_id)
            schema = await self._ports.tenant_db.ensure_plugin_schema(
                company_id=company_id, plugin_id=plugin_id, dsn_ref=database.dsn_ref
            )
            objects = await self._ports.object_store.ensure_bucket(
                company_id=company_id, plugin_id=plugin_id
            )
            await self._pre_deliver_secrets(
                operation="grant-replay",
                instance=instance,
                manifest_version=manifest_version,
                artifact_digest=artifact_digest,
            )
            await self._ports.activation.ensure_deployment(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=instance.plugin_instance_id,
                artifact_digest=artifact_digest,
                dsn_ref=schema.dsn_ref,
                webhook_secret_ref=webhook_secret_ref,
            )
            routes = await self._ports.routes.register(
                company_id=company_id, plugin_id=plugin_id, grant_id=existing.grant_id
            )
            await self._ports.instance_store.update_status(
                plugin_instance_id=instance.plugin_instance_id,
                status=PluginInstanceStatus.ACTIVE,
            )
            claims = await self._mint(existing=existing)
            await self._consume_publication_approval(approval)
            await self._audit(
                operation=AdminOperation.GRANT,
                company_id=company_id,
                plugin_id=plugin_id,
                decision=AdminDecision.ALLOW,
                grant_id=existing.grant_id,
                artifact_digest=artifact_digest,
                reason="idempotent replay; deployment/routes ensured; password preserved",
            )
            return GrantReceipt(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=instance.plugin_instance_id,
                manifest_version=manifest_version,
                artifact_digest=artifact_digest,
                grant=existing,
                claims=claims,
                routes=routes,
                database=database,
                schema=schema,
                objects=objects,
                replayed=True,
            )

        # Allocate and fence a re-grant PK before the first saga mutation. In
        # particular, a broken/repeating id_factory must never let the SQL adapter
        # UPDATE the revoked row back to ACTIVE under the same primary key.
        regrant_candidate_id: str | None = None
        if existing is not None and not existing.is_active:
            regrant_candidate_id = f"grant_{self._uid()}"
            if regrant_candidate_id == existing.grant_id:
                raise AppError(
                    code=REGRANT_ID_CONFLICT_CODE,
                    title=REGRANT_ID_CONFLICT_CODE,
                    status=409,
                    detail="Re-grant требует fresh grant_id, отличный от revoked grant",
                    extra={
                        "company_id": company_id,
                        "plugin_id": plugin_id,
                        "previous_grant_id": existing.grant_id,
                        "candidate_grant_id": regrant_candidate_id,
                    },
                )

        await self._decision_audit(
            operation=AdminOperation.GRANT,
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=existing.grant_id if existing is not None else None,
        )
        if instance is None:
            instance = await self._ports.instance_store.create(
                instance=self._new_instance(
                    company_id=company_id,
                    plugin_id=plugin_id,
                    manifest_version=manifest_version,
                    artifact_digest=artifact_digest,
                    instance_id=(
                        existing.plugin_instance_id if existing is not None else None
                    ),
                )
            )
        if (
            existing is not None
            and instance.plugin_instance_id != existing.plugin_instance_id
        ):
            raise grant_exists(company_id=company_id, plugin_id=plugin_id)
        await self._ensure_installation_scope(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=instance.plugin_instance_id,
        )

        # Re-grant после revoke — единственный нормальный путь явной ротации.
        # При failed initial grant разрешён repair только после ROTATION_REQUIRED.
        rotate = bool(
            (existing is not None and not existing.is_active)
            or instance.status is PluginInstanceStatus.REVOKED
            or instance.status is PluginInstanceStatus.QUARANTINED
        )
        if existing is not None and existing.webhook_secret_ref is None:
            # Do not silently repair legacy grants by inventing webhook material.
            raise RuntimeError("persisted grant has no durable webhook_secret_ref")
        webhook_secret_ref = await self._provision_webhook_secret(instance, rotate=rotate)
        database = await self._ports.tenant_db.ensure_company_database(company_id=company_id)
        if rotate:
            schema = await self._rotate_schema_for_saga(
                instance=instance, dsn_ref=database.dsn_ref
            )
        else:
            schema = await self._ensure_schema_with_repair(
                company_id=company_id,
                plugin_id=plugin_id,
                dsn_ref=database.dsn_ref,
                instance=instance,
                allow_repair=existing is None,
            )
        objects = await self._ports.object_store.ensure_bucket(
            company_id=company_id, plugin_id=plugin_id
        )

        # Непустое имя + runtime status проверяет adapter. ACTIVE grant раньше
        # этого подтверждения не создаётся и не воскрешается.
        await self._pre_deliver_secrets(
            operation="grant-regrant" if rotate else "grant",
            instance=instance,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
        )
        await self._ports.activation.ensure_deployment(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=instance.plugin_instance_id,
            artifact_digest=artifact_digest,
            dsn_ref=schema.dsn_ref,
            webhook_secret_ref=webhook_secret_ref,
        )
        # Для нового/revoked grant approval обязателен; его поля, а не тело
        # запроса, являются источником долгоживущей политики.
        approved_capabilities = (
            approval.capability_set if approval is not None else capabilities
        )
        candidate = CapabilityGrantRecord(
            grant_id=(
                regrant_candidate_id
                if regrant_candidate_id is not None
                else f"grant_{self._uid()}"
            ),
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=instance.plugin_instance_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            capabilities=approved_capabilities.capabilities,
            egress_policy=approved_capabilities.egress_policy,
            quotas=approved_capabilities.quotas,
            status=GrantStatus.ACTIVE,
            created_at=self._now(),
            webhook_secret_ref=webhook_secret_ref,
        )
        if existing is not None and not existing.is_active:
            grant = await self._ports.grant_store.regrant(
                previous_grant_id=existing.grant_id, grant=candidate
            )
        else:
            grant = await self._ports.grant_store.create(grant=candidate)
        if not isinstance(grant, CapabilityGrantRecord):
            raise RuntimeError("grant store вернул не CapabilityGrantRecord")
        if (
            existing is not None
            and not existing.is_active
            and (grant != candidate or grant.grant_id == existing.grant_id)
        ):
            # Never accept a forged/legacy adapter response which points back at
            # the revoked PK or silently changes the approved durable binding.
            raise RuntimeError("regrant store returned mismatched grant binding")
        if not grant.is_active or grant.revoked_at is not None:
            raise RuntimeError("grant store не подтвердил ACTIVE после ensure Deployment")
        if grant.webhook_secret_ref != webhook_secret_ref:
            raise RuntimeError("grant store не подтвердил durable webhook_secret_ref")
        await self._ports.instance_store.update_status(
            plugin_instance_id=instance.plugin_instance_id,
            status=PluginInstanceStatus.ACTIVE,
        )
        routes = await self._ports.routes.register(
            company_id=company_id, plugin_id=plugin_id, grant_id=grant.grant_id
        )
        claims = await self._mint(existing=grant)
        await self._consume_publication_approval(approval)
        await self._audit(
            operation=AdminOperation.GRANT,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.ALLOW,
            grant_id=grant.grant_id,
            artifact_digest=artifact_digest,
            reason="re-grant after revoke" if rotate else "grant saga completed",
        )
        return GrantReceipt(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=instance.plugin_instance_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            grant=grant,
            claims=claims,
            routes=routes,
            database=database,
            schema=schema,
            objects=objects,
        )

    @staticmethod
    def _update_conflict_error(
        *, company_id: str, plugin_id: str, detail: str
    ) -> PluginAdminError:
        return PluginAdminError(
            code="PLUGIN_UPDATE_CONFLICT",
            detail=detail,
            status=409,
            extra={"company_id": company_id, "plugin_id": plugin_id},
        )

    async def _update_installed_grant(
        self,
        *,
        existing: CapabilityGrantRecord,
        instance: PluginInstanceRecord,
        manifest_version: str,
        artifact_digest: str,
        approval: PluginPublicationApproval | None,
    ) -> GrantReceipt:
        """Serialize the complete update saga across every API process."""

        await self._ensure_installation_scope(
            company_id=existing.company_id,
            plugin_id=existing.plugin_id,
            plugin_instance_id=instance.plugin_instance_id,
        )
        lock = self._ports.update_lock
        if lock is None:
            raise RuntimeError("distributed plugin update lock is not wired")
        lease = await lock.acquire(
            company_id=existing.company_id,
            plugin_id=existing.plugin_id,
            plugin_instance_id=instance.plugin_instance_id,
            expected_manifest_version=instance.manifest_version,
            expected_artifact_digest=instance.artifact_digest,
        )
        if lease is None:
            error = self._update_conflict_error(
                company_id=existing.company_id,
                plugin_id=existing.plugin_id,
                detail="Другой update этой installation уже выполняется; повторите позже",
            )
            await self._deny_audit(
                operation=AdminOperation.GRANT,
                company_id=existing.company_id,
                plugin_id=existing.plugin_id,
                grant_id=existing.grant_id,
                code=error.code,
                reason="distributed per-install update lock is held",
            )
            raise error
        try:
            if not isinstance(lease, PluginUpdateLockLease):
                raise RuntimeError("distributed update lock returned an invalid lease")
            expected_binding = (
                existing.company_id,
                existing.plugin_id,
                instance.plugin_instance_id,
                instance.manifest_version,
                instance.artifact_digest,
            )
            actual_binding = (
                lease.company_id,
                lease.plugin_id,
                lease.plugin_instance_id,
                lease.expected_manifest_version,
                lease.expected_artifact_digest,
            )
            if actual_binding != expected_binding:
                raise RuntimeError("distributed update lock returned a foreign lease")
            return await self._update_installed_grant_locked(
                existing=existing,
                instance=instance,
                manifest_version=manifest_version,
                artifact_digest=artifact_digest,
                approval=approval,
                lease=lease,
            )
        finally:
            await lock.release(lease)

    async def _update_installed_grant_locked(
        self,
        *,
        existing: CapabilityGrantRecord,
        instance: PluginInstanceRecord,
        manifest_version: str,
        artifact_digest: str,
        approval: PluginPublicationApproval | None,
        lease: PluginUpdateLockLease,
    ) -> GrantReceipt:
        """Update saga: old snapshot → Secret/Deployment patch → atomic pin CAS.

        The old immutable records are the only source of the expected fence. Neither
        tenant credentials nor webhook material are rotated by this operation.
        """

        company_id = existing.company_id
        plugin_id = existing.plugin_id
        if existing.webhook_secret_ref is None:
            raise RuntimeError("active update grant has no durable webhook_secret_ref")
        webhook_secret_ref = (
            await self._read_webhook_secret(existing, existing.webhook_secret_ref)
        ).secret_ref
        await self._decision_audit(
            operation=AdminOperation.GRANT,
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=existing.grant_id,
        )
        database = await self._ports.tenant_db.ensure_company_database(company_id=company_id)
        # ensure is intentionally used instead of any repair/rotation path. A changed
        # already-persisted tenant dsn_ref may be projected; passwords stay untouched.
        schema = await self._ports.tenant_db.ensure_plugin_schema(
            company_id=company_id,
            plugin_id=plugin_id,
            dsn_ref=database.dsn_ref,
        )
        objects = await self._ports.object_store.ensure_bucket(
            company_id=company_id, plugin_id=plugin_id
        )

        def _matches_pre_state(candidate: object) -> bool:
            if not isinstance(candidate, ArtifactPinUpdateResult):
                return False
            current_instance = candidate.instance
            current_grant = candidate.grant
            return (
                current_instance.plugin_instance_id == instance.plugin_instance_id
                and current_instance.company_id == company_id
                and current_instance.plugin_id == plugin_id
                and current_instance.manifest_version == instance.manifest_version
                and current_instance.artifact_digest == instance.artifact_digest
                and current_instance.status is instance.status
                and current_grant == existing
            )

        async def _read_pin_state() -> ArtifactPinUpdateResult | None:
            current_instance = await self._ports.instance_store.get(
                company_id=company_id, plugin_id=plugin_id
            )
            current_grant = await self._ports.grant_store.get(
                company_id=company_id, plugin_id=plugin_id
            )
            if current_instance is None or current_grant is None:
                return None
            return ArtifactPinUpdateResult(
                instance=current_instance, grant=current_grant
            )

        def _ownership_conflict() -> PluginAdminError:
            return self._update_conflict_error(
                company_id=company_id,
                plugin_id=plugin_id,
                detail=(
                    "SQL/runtime owner или digest изменился; "
                    "compensation чужого update запрещена"
                ),
            )

        async def _assert_pre_state() -> None:
            try:
                current = await _read_pin_state()
            except Exception as exc:
                raise _ownership_conflict() from exc
            if not _matches_pre_state(current):
                raise _ownership_conflict()

        async def _runtime_binding() -> PluginRuntimeBinding:
            binding = await self._ports.activation.runtime_binding(
                company_id=company_id, plugin_id=plugin_id
            )
            if (
                not isinstance(binding, PluginRuntimeBinding)
                or binding.plugin_instance_id != instance.plugin_instance_id
            ):
                raise RuntimeError("live Deployment binding is absent or foreign")
            return binding

        async def _patch(
            digest: str,
            *,
            expected_current_digest: str,
            owner_token: str | None,
            expected_current_owner: str | None,
        ) -> None:
            await self._pre_deliver_secrets(
                operation="update" if digest == artifact_digest else "update-rollback",
                instance=instance,
                manifest_version=(
                    manifest_version if digest == artifact_digest
                    else instance.manifest_version
                ),
                artifact_digest=digest,
            )
            await self._ports.activation.update_deployment(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=instance.plugin_instance_id,
                artifact_digest=digest,
                expected_current_artifact_digest=expected_current_digest,
                update_owner_token=owner_token,
                expected_current_update_owner_token=expected_current_owner,
                dsn_ref=schema.dsn_ref,
                webhook_secret_ref=webhook_secret_ref,
            )

        async def _rollback(
            runtime_before: PluginRuntimeStatus,
            binding_before: PluginRuntimeBinding,
        ) -> None:
            # A CAS loser or foreign live writer is not compensable. Re-read both
            # authorities before restoring even the old approved secret delivery.
            await _assert_pre_state()
            binding = await _runtime_binding()
            if binding.artifact_digest == artifact_digest:
                if binding.update_owner_token != lease.owner_token:
                    raise _ownership_conflict()
                await _patch(
                    instance.artifact_digest,
                    expected_current_digest=artifact_digest,
                    owner_token=binding_before.update_owner_token,
                    expected_current_owner=lease.owner_token,
                )
            elif (
                binding.artifact_digest == instance.artifact_digest
                and binding.update_owner_token == binding_before.update_owner_token
            ):
                # The old runtime is already present (for example a pre-PATCH
                # denial): restore only pre-delivery, never fabricate a mutation.
                await self._pre_deliver_secrets(
                    operation="update-preflight-rollback",
                    instance=instance,
                    manifest_version=instance.manifest_version,
                    artifact_digest=instance.artifact_digest,
                )
            else:
                raise _ownership_conflict()
            restored_binding = await _runtime_binding()
            if (
                restored_binding.artifact_digest != instance.artifact_digest
                or restored_binding.update_owner_token != binding_before.update_owner_token
            ):
                raise _ownership_conflict()
            restored = await self._ports.activation.runtime_status(
                company_id=company_id, plugin_id=plugin_id
            )
            if restored is PluginRuntimeStatus.ABSENT or (
                runtime_before is PluginRuntimeStatus.RUNNING
                and restored is not PluginRuntimeStatus.RUNNING
            ):
                raise RuntimeError("old Deployment rollback did not restore confirmed health")

        async def _inconsistent(binding_before: PluginRuntimeBinding) -> AppError:
            marker_confirmed = False
            try:
                # Quarantine is legal only while both authorities still belong to
                # this attempt. The subsequent atomic CAS closes the SQL race.
                current = await _read_pin_state()
                binding = await _runtime_binding()
                live_owned = (
                    binding.artifact_digest == instance.artifact_digest
                    and binding.update_owner_token == binding_before.update_owner_token
                ) or (
                    binding.artifact_digest == artifact_digest
                    and binding.update_owner_token == lease.owner_token
                )
                if _matches_pre_state(current) and live_owned:
                    marked = await self._ports.grant_store.update_artifact_pin(
                        grant_id=existing.grant_id,
                        company_id=company_id,
                        plugin_id=plugin_id,
                        plugin_instance_id=instance.plugin_instance_id,
                        expected_manifest_version=instance.manifest_version,
                        expected_artifact_digest=instance.artifact_digest,
                        manifest_version=instance.manifest_version,
                        artifact_digest=instance.artifact_digest,
                        expected_instance_status=instance.status,
                        instance_status=PluginInstanceStatus.QUARANTINED,
                    )
                    marker_confirmed = (
                        marked.instance.plugin_instance_id == instance.plugin_instance_id
                        and marked.instance.company_id == company_id
                        and marked.instance.plugin_id == plugin_id
                        and marked.instance.manifest_version == instance.manifest_version
                        and marked.instance.artifact_digest == instance.artifact_digest
                        and marked.instance.status is PluginInstanceStatus.QUARANTINED
                        and marked.grant == existing
                    )
            except Exception:  # noqa: BLE001 - unconfirmed marker stays explicit
                marker_confirmed = False
            error = self._update_inconsistent_error(
                company_id=company_id,
                plugin_id=plugin_id,
                detail=(
                    "Deployment patch и durable pin рассинхронизированы; "
                    "активируйте recovery повтором grant с новым artifact"
                ),
                marker_confirmed=marker_confirmed,
            )
            await self._deny_audit(
                operation=AdminOperation.GRANT,
                company_id=company_id,
                plugin_id=plugin_id,
                grant_id=existing.grant_id,
                code=error.code,
                reason=(
                    "owned update rollback failed; quarantine requires exact SQL+live fence; "
                    f"quarantine_marker_confirmed={marker_confirmed}"
                ),
            )
            return error

        runtime_before = await self._ports.activation.runtime_status(
            company_id=company_id, plugin_id=plugin_id
        )
        binding_before = await _runtime_binding()
        if binding_before.artifact_digest != instance.artifact_digest:
            raise _ownership_conflict()
        try:
            await _patch(
                artifact_digest,
                expected_current_digest=instance.artifact_digest,
                owner_token=lease.owner_token,
                expected_current_owner=None,
            )
        except Exception as exc:  # noqa: BLE001 - compensate an uncertain patch
            try:
                await _rollback(runtime_before, binding_before)
            except Exception as rollback_exc:  # noqa: BLE001
                if (
                    isinstance(rollback_exc, AppError)
                    and rollback_exc.code == "PLUGIN_UPDATE_CONFLICT"
                ):
                    await self._deny_audit(
                        operation=AdminOperation.GRANT,
                        company_id=company_id,
                        plugin_id=plugin_id,
                        grant_id=existing.grant_id,
                        code=rollback_exc.code,
                        reason="foreign SQL/runtime owner; update compensation skipped",
                    )
                    raise
                error = await _inconsistent(binding_before)
                raise error from rollback_exc
            await self._deny_audit(
                operation=AdminOperation.GRANT,
                company_id=company_id,
                plugin_id=plugin_id,
                grant_id=existing.grant_id,
                code=getattr(exc, "code", "GRANT_EXISTS"),
                reason="adapter update failed; old runtime restored; durable pin unchanged",
            )
            raise

        def _matches_post_state(candidate: object) -> bool:
            if not isinstance(candidate, ArtifactPinUpdateResult):
                return False
            new_instance = candidate.instance
            new_grant = candidate.grant
            return (
                new_instance.plugin_instance_id == instance.plugin_instance_id
                and new_instance.company_id == company_id
                and new_instance.plugin_id == plugin_id
                and new_instance.manifest_version == manifest_version
                and new_instance.artifact_digest == artifact_digest
                and new_instance.status is PluginInstanceStatus.ACTIVE
                and new_grant.grant_id == existing.grant_id
                and new_grant.company_id == company_id
                and new_grant.plugin_id == plugin_id
                and new_grant.plugin_instance_id == instance.plugin_instance_id
                and new_grant.manifest_version == manifest_version
                and new_grant.artifact_digest == artifact_digest
                and new_grant.is_active
                and new_grant.capabilities == existing.capabilities
                and new_grant.egress_policy == existing.egress_policy
                and new_grant.quotas == existing.quotas
                and new_grant.created_at == existing.created_at
                and new_grant.revoked_at is None
                and new_grant.webhook_secret_ref == existing.webhook_secret_ref
            )

        try:
            updated = await self._ports.grant_store.update_artifact_pin(
                grant_id=existing.grant_id,
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=instance.plugin_instance_id,
                expected_manifest_version=instance.manifest_version,
                expected_artifact_digest=instance.artifact_digest,
                manifest_version=manifest_version,
                artifact_digest=artifact_digest,
                expected_instance_status=instance.status,
                instance_status=PluginInstanceStatus.ACTIVE,
            )
            if not _matches_post_state(updated):
                raise RuntimeError("artifact pin CAS post-state binding mismatch")
            new_instance = updated.instance
            new_grant = updated.grant
        except Exception as exc:  # noqa: BLE001 - DB outcome may be unknown
            # A lost response can follow a committed CAS. Re-read both rows before
            # compensating: new→new verified state is success, not UPDATE_INCONSISTENT.
            confirmed: ArtifactPinUpdateResult | None = None
            candidate: ArtifactPinUpdateResult | None = None
            try:
                candidate = await _read_pin_state()
                if _matches_post_state(candidate):
                    confirmed = candidate
            except Exception:  # noqa: BLE001 - unknown state is never compensable
                confirmed = None
                candidate = None
            if confirmed is not None:
                new_instance = confirmed.instance
                new_grant = confirmed.grant
            else:
                if not _matches_pre_state(candidate):
                    error = _ownership_conflict()
                    await self._deny_audit(
                        operation=AdminOperation.GRANT,
                        company_id=company_id,
                        plugin_id=plugin_id,
                        grant_id=existing.grant_id,
                        code=error.code,
                        reason="artifact CAS lost to another durable state; compensation skipped",
                    )
                    raise error from exc
                try:
                    await _rollback(runtime_before, binding_before)
                except Exception as rollback_exc:  # noqa: BLE001
                    if (
                        isinstance(rollback_exc, AppError)
                        and rollback_exc.code == "PLUGIN_UPDATE_CONFLICT"
                    ):
                        await self._deny_audit(
                            operation=AdminOperation.GRANT,
                            company_id=company_id,
                            plugin_id=plugin_id,
                            grant_id=existing.grant_id,
                            code=rollback_exc.code,
                            reason="foreign live owner after CAS; compensation skipped",
                        )
                        raise
                    error = await _inconsistent(binding_before)
                    raise error from rollback_exc
                await self._deny_audit(
                    operation=AdminOperation.GRANT,
                    company_id=company_id,
                    plugin_id=plugin_id,
                    grant_id=existing.grant_id,
                    code="GRANT_EXISTS",
                    reason="pin CAS failed; old runtime restored; retry remains fenced",
                )
                raise PluginAdminError(
                    code="GRANT_EXISTS",
                    detail=(
                        "Deployment update подтверждён, но durable pin CAS не подтверждён; "
                        "старый runtime восстановлен, повторите update"
                    ),
                    status=503,
                    extra={"company_id": company_id, "plugin_id": plugin_id},
                ) from exc

        routes = await self._ports.routes.register(
            company_id=company_id, plugin_id=plugin_id, grant_id=new_grant.grant_id
        )
        claims = await self._mint(existing=new_grant)
        await self._consume_publication_approval(approval)
        await self._audit(
            operation=AdminOperation.GRANT,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.ALLOW,
            grant_id=new_grant.grant_id,
            artifact_digest=artifact_digest,
            reason=(
                "update saga completed; replicas preserved; health confirmed; "
                "instance+grant pin atomically replaced"
            ),
        )
        return GrantReceipt(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=new_instance.plugin_instance_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            grant=new_grant,
            claims=claims,
            routes=routes,
            database=database,
            schema=schema,
            objects=objects,
            replayed=False,
        )

    @staticmethod
    def _update_inconsistent_error(
        *,
        company_id: str,
        plugin_id: str,
        detail: str,
        marker_confirmed: bool,
    ) -> AppError:
        return AppError(
            code=UPDATE_INCONSISTENT_CODE,
            title=UPDATE_INCONSISTENT_CODE,
            status=503,
            detail=detail,
            extra={
                "company_id": company_id,
                "plugin_id": plugin_id,
                "durable_status": PluginInstanceStatus.QUARANTINED.value,
                "marker_confirmed": marker_confirmed,
                "recovery": "retry grant with the approved new artifact",
            },
        )

    def _new_instance(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
        instance_id: str | None,
    ) -> PluginInstanceRecord:
        now = self._now()
        return PluginInstanceRecord(
            plugin_instance_id=(
                instance_id or plugin_instance_id_for(company_id, plugin_id)
            ),
            company_id=company_id,
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            status=PluginInstanceStatus.INSTALLED,
            tenant_db_name=f"tenant_{company_id}",
            bucket_name=f"tenant-{company_id}",
            created_at=now,
            updated_at=now,
        )

    async def _rotate_schema_for_saga(
        self, *, instance: PluginInstanceRecord, dsn_ref: str
    ) -> PluginSchema:
        """Дurable repair marker закрывает crash между ALTER ROLE и secret rotate."""
        await self._ports.instance_store.update_status(
            plugin_instance_id=instance.plugin_instance_id,
            status=PluginInstanceStatus.QUARANTINED,
        )
        schema = await self._ports.tenant_db.rotate_plugin_schema(
            company_id=instance.company_id,
            plugin_id=instance.plugin_id,
            dsn_ref=dsn_ref,
        )
        await self._ports.instance_store.update_status(
            plugin_instance_id=instance.plugin_instance_id,
            status=PluginInstanceStatus.INSTALLED,
        )
        return schema

    async def _ensure_schema_with_repair(
        self,
        *,
        company_id: str,
        plugin_id: str,
        dsn_ref: str,
        instance: PluginInstanceRecord,
        allow_repair: bool,
    ) -> PluginSchema:
        try:
            return await self._ports.tenant_db.ensure_plugin_schema(
                company_id=company_id, plugin_id=plugin_id, dsn_ref=dsn_ref
            )
        except PluginAdminError as exc:
            extra = getattr(exc, "extra", {}) or {}
            if not allow_repair or extra.get("tenant_db_code") != "ROTATION_REQUIRED":
                raise
            # Явный repair после failed initial grant, не скрытая ротация ensure.
            return await self._rotate_schema_for_saga(
                instance=instance, dsn_ref=dsn_ref
            )

    async def _mint(self, *, existing: CapabilityGrantRecord) -> IssuedPluginClaims:
        """Единая точка выпуска/ротации короткого ``PluginClaims``.

        Используется и ``grant``, и ``wake``: токен всегда новый, TTL короткий,
        egress/квоты в него не попадают.
        """

        request = MintRequest(
            company_id=existing.company_id,
            plugin_id=existing.plugin_id,
            plugin_instance_id=existing.plugin_instance_id,
            manifest_version=existing.manifest_version,
            artifact_digest=existing.artifact_digest,
            grant_id=existing.grant_id,
            scopes=existing.capabilities,
            quota_ref=f"{existing.company_id}/plan_default",
            ttl_seconds=PLUGIN_CLAIMS_TTL_SECONDS,
        )
        return await self._ports.mint.mint(request=request)

    # ------------------------------------------------------------------ revoke

    async def revoke(self, *, company_id: str, plugin_id: str) -> RevokeReceipt:
        """Идемпотентный forced revoke; повтор продолжает все недоделанные шаги."""
        _validate_identity(company_id=company_id, plugin_id=plugin_id)
        grant = await self._ports.grant_store.get(company_id=company_id, plugin_id=plugin_id)
        if grant is None:
            error = grant_not_found(company_id=company_id, plugin_id=plugin_id)
            await self._deny_audit(
                operation=AdminOperation.REVOKE,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="grant missing",
            )
            raise error
        instance = await self._ports.instance_store.get(
            company_id=company_id, plugin_id=plugin_id
        )
        replayed = not grant.is_active
        await self._decision_audit(
            operation=AdminOperation.REVOKE,
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant.grant_id,
        )

        failed: list[str] = []

        async def _step(name: str, thunk: object) -> bool:
            try:
                result = await thunk()  # type: ignore[operator]
                ok = bool(result)
            except Exception:  # noqa: BLE001 - продолжаем остальные revoke-шаги
                ok = False
            if not ok:
                failed.append(name)
            return ok

        await _step(
            "token_revocation",
            lambda: self._ports.revocation.revoke(
                grant_id=grant.grant_id, reason="admin revoke"
            ),
        )
        if grant.is_active:
            async def _deactivate() -> bool:
                deactivated = await self._ports.grant_store.deactivate(grant_id=grant.grant_id)
                return deactivated.status is GrantStatus.REVOKED

            await _step("grant_deactivation", _deactivate)
        elif grant.status is not GrantStatus.REVOKED:
            failed.append("grant_deactivation")

        await _step(
            "routes",
            lambda: self._ports.routes.unregister(
                company_id=company_id, plugin_id=plugin_id, grant_id=grant.grant_id
            ),
        )
        await _step(
            "secret_delivery_revoke",
            lambda: self._pre_revoke_secrets(operation="revoke", instance=instance),
        )
        await _step("webhook_secret_revoke", lambda: self._revoke_webhook_secret(grant))
        await _step(
            "tenant_db_revoke",
            lambda: self._ports.tenant_db.revoke_plugin(
                company_id=company_id, plugin_id=plugin_id
            ),
        )
        # Admin revoke вызывает forced scale=0, не idle/jobs-aware suspend.
        await _step(
            "forced_suspend",
            lambda: self._ports.activation.suspend(company_id=company_id, plugin_id=plugin_id),
        )
        if instance is not None:
            async def _mark_revoked() -> bool:
                updated = await self._ports.instance_store.update_status(
                    plugin_instance_id=instance.plugin_instance_id,
                    status=PluginInstanceStatus.REVOKED,
                )
                return updated.status is PluginInstanceStatus.REVOKED

            await _step("instance_status", _mark_revoked)

        if failed:
            error = revoke_failed(
                grant_id=grant.grant_id,
                reason=f"steps not confirmed: {', '.join(dict.fromkeys(failed))}",
            )
            await self._deny_audit(
                operation=AdminOperation.REVOKE,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="revoke incomplete; retry resumes every step",
            )
            raise error

        await self._audit(
            operation=AdminOperation.REVOKE,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.ALLOW,
            grant_id=grant.grant_id,
            reason="idempotent forced revoke" if replayed else "forced revoke completed",
        )
        return RevokeReceipt(
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant.grant_id,
            status=GrantStatus.REVOKED,
            token_revoked=True,
            replayed=replayed,
            database_preserved=True,
        )

    # ------------------------------------------------------------------ uninstall

    async def uninstall(self, *, company_id: str, plugin_id: str) -> UninstallReceipt:
        """Cleanup-first saga; lifecycle rows удаляются только после подтверждений."""
        _validate_identity(company_id=company_id, plugin_id=plugin_id)
        grant = await self._ports.grant_store.get(company_id=company_id, plugin_id=plugin_id)
        instance = await self._ports.instance_store.get(
            company_id=company_id, plugin_id=plugin_id
        )
        grant_id = grant.grant_id if grant is not None else None
        await self._decision_audit(
            operation=AdminOperation.UNINSTALL,
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant_id,
        )

        if grant is None and instance is None:
            # Такое состояние достижимо только после полной saga: при failed cleanup
            # хотя бы одна durable запись намеренно сохраняется для retry.
            await self._audit(
                operation=AdminOperation.UNINSTALL,
                company_id=company_id,
                plugin_id=plugin_id,
                decision=AdminDecision.ALLOW,
                reason="already absent after confirmed prior cleanup",
            )
            return UninstallReceipt(
                company_id=company_id,
                plugin_id=plugin_id,
                grant_id=None,
                deleted=True,
                already_absent=True,
                schema_dropped=True,
                role_dropped=True,
                prefix_dropped=True,
                bucket_dropped=True,
                artifact_unlinked=True,
                tenant_infra_purged=True,
                token_revoked=True,
                completed_steps=(),
                snapshot_taken=False,
            )

        artifact_digest = (
            grant.artifact_digest if grant is not None else instance.artifact_digest
        )
        completed: list[str] = []
        failed: list[str] = []

        async def _step(name: str, thunk: object) -> bool:
            try:
                result = await thunk()  # type: ignore[operator]
                ok = bool(result)
            except Exception:  # noqa: BLE001
                ok = False
            (completed if ok else failed).append(name)
            return ok

        token_revoked = True
        if grant is not None:
            token_revoked = await _step(
                "token_revocation",
                lambda: self._ports.revocation.revoke(
                    grant_id=grant.grant_id, reason="admin uninstall"
                ),
            )
            await _step(
                "routes",
                lambda: self._ports.routes.unregister(
                    company_id=company_id, plugin_id=plugin_id, grant_id=grant.grant_id
                ),
            )
            await _step("webhook_secret_revoke", lambda: self._revoke_webhook_secret(grant))
        await _step(
            "secret_delivery_revoke",
            lambda: self._pre_revoke_secrets(operation="uninstall", instance=instance),
        )
        infra_purged = await _step(
            "tenant_infra",
            lambda: self._ports.activation.purge_infra(
                company_id=company_id, plugin_id=plugin_id
            ),
        )
        prefix_dropped = await _step(
            "object_prefix",
            lambda: self._ports.object_store.drop_prefix(
                company_id=company_id, plugin_id=plugin_id
            ),
        )
        try:
            # False означает shared non-empty bucket, а не неудачу cleanup.
            bucket_dropped = await self._ports.object_store.drop_bucket_if_empty(
                company_id=company_id
            )
            completed.append("bucket_if_empty")
        except Exception:  # noqa: BLE001
            bucket_dropped = False
            failed.append("bucket_if_empty")

        schema_dropped, role_dropped = False, False
        try:
            schema_dropped, role_dropped = await self._ports.tenant_db.drop_plugin_schema(
                company_id=company_id, plugin_id=plugin_id
            )
        except Exception:  # noqa: BLE001
            pass
        (completed if schema_dropped and role_dropped else failed).append("schema_and_role")

        artifact_unlinked = await _step(
            "artifact_registry",
            lambda: self._ports.artifact_registry.unlink_installation(
                company_id=company_id,
                plugin_id=plugin_id,
                artifact_digest=artifact_digest,
            ),
        )

        grant_deleted = grant is None
        instance_deleted = instance is None
        if not failed:
            # FK grant -> instance требует сначала удалить grant. Оба действия
            # находятся ПОСЛЕ обязательной внешней очистки.
            if grant is not None:
                grant_deleted = await _step(
                    "grant_store",
                    lambda: self._ports.grant_store.delete(
                        company_id=company_id, plugin_id=plugin_id
                    ),
                )
            if grant_deleted and instance is not None:
                instance_deleted = await _step(
                    "instance_store",
                    lambda: self._ports.instance_store.delete(
                        company_id=company_id, plugin_id=plugin_id
                    ),
                )

        if failed or not grant_deleted or not instance_deleted:
            error = uninstall_partial(
                company_id=company_id,
                plugin_id=plugin_id,
                failed_steps=tuple(dict.fromkeys(failed)),
            )
            await self._deny_audit(
                operation=AdminOperation.UNINSTALL,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="cleanup incomplete; lifecycle records retained for retry",
            )
            raise error

        receipt = UninstallReceipt(
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant_id,
            deleted=True,
            already_absent=False,
            schema_dropped=schema_dropped,
            role_dropped=role_dropped,
            prefix_dropped=prefix_dropped,
            bucket_dropped=bucket_dropped,
            artifact_unlinked=artifact_unlinked,
            tenant_infra_purged=infra_purged,
            token_revoked=token_revoked,
            completed_steps=tuple(completed),
            snapshot_taken=False,
        )
        await self._audit(
            operation=AdminOperation.UNINSTALL,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.ALLOW,
            grant_id=grant_id,
            artifact_digest=artifact_digest,
            reason="confirmed cleanup and lifecycle record deletion",
        )
        return receipt

    # ------------------------------------------------------------------ wake

    async def wake(self, *, company_id: str, plugin_id: str) -> WakeReceipt:
        """Пробудить backend через activator и выдать новый короткий токен (§5.5, §7.4).

        Отозванный/отсутствующий grant не будится (fail-closed). DSN остаётся тем же:
        новый ``PluginClaims`` не меняет инфраструктуру и не пересобирает egress.
        """

        _validate_identity(company_id=company_id, plugin_id=plugin_id)
        grant = await self._require_active_grant(
            operation=AdminOperation.WAKE, company_id=company_id, plugin_id=plugin_id
        )
        if await self._ports.revocation.is_revoked(grant_id=grant.grant_id):
            error = grant_not_found(company_id=company_id, plugin_id=plugin_id)
            await self._deny_audit(
                operation=AdminOperation.WAKE,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="grant is revoked",
            )
            raise error

        instance = await self._ports.instance_store.get(
            company_id=company_id, plugin_id=plugin_id
        )
        if instance is not None and instance.status is PluginInstanceStatus.QUARANTINED:
            error = self._update_inconsistent_error(
                company_id=company_id,
                plugin_id=plugin_id,
                detail="Wake запрещён до подтверждённого update recovery",
                marker_confirmed=True,
            )
            await self._deny_audit(
                operation=AdminOperation.WAKE,
                company_id=company_id,
                plugin_id=plugin_id,
                grant_id=grant.grant_id,
                code=error.code,
                reason="UPDATE_INCONSISTENT blocks activation",
            )
            raise error

        await self._decision_audit(
            operation=AdminOperation.WAKE,
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant.grant_id,
        )
        wake = await self._ports.activation.wake(company_id=company_id, plugin_id=plugin_id)
        if not wake.ready:
            # Холодный старт не дошёл до Ready — токен не выдаём, иначе плагин
            # получит доступ при connection-refused на первый запрос (§7.4).
            await self._deny_audit(
                operation=AdminOperation.WAKE,
                company_id=company_id,
                plugin_id=plugin_id,
                code="WAKE_NOT_READY",
                reason="activator did not reach Ready",
            )
            raise RuntimeError("wake: activator не дождался Ready (fail-closed)")

        claims = await self._mint(existing=grant)
        await self._audit(
            operation=AdminOperation.WAKE,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.ALLOW,
            grant_id=grant.grant_id,
        )
        return WakeReceipt(
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant.grant_id,
            claims=claims,
            wake=wake,
            runtime_status=PluginRuntimeStatus.RUNNING,
        )

    # ------------------------------------------------------------------ list

    async def _list_company_grants(
        self, *, company_id: str
    ) -> tuple[CapabilityGrantRecord, ...]:
        """Прочитать async DB reader, сохранив совместимость с in-memory портом."""

        store = self._ports.grant_store
        # SQL-репозиторий также имеет старый sync snapshot list_by_company;
        # authoritative async reader называется list_by_company_async.
        reader = getattr(store, "list_by_company_async", None)
        if reader is None:
            reader = store.list_by_company
        try:
            return tuple(await reader(company_id=company_id))
        except Exception as exc:  # noqa: BLE001 - read evidence ≠ молчаливый успех
            raise EffectiveGrantReadError(
                reason="grant_store_unavailable", company_id=company_id
            ) from exc

    async def _read_list_grant(
        self, *, company_id: str, listed: CapabilityGrantRecord
    ) -> tuple[CapabilityGrantRecord, QuotaSet | None, EffectiveQuotasStatus]:
        """Перепроверить binding/active status перед выдачей effective quotas."""

        if not isinstance(listed, CapabilityGrantRecord) or listed.company_id != company_id:
            raise EffectiveGrantReadError(
                reason="grant_binding_mismatch", company_id=company_id
            )
        try:
            _validate_identity(company_id=company_id, plugin_id=listed.plugin_id)
        except ValueError as exc:
            raise EffectiveGrantReadError(
                reason="grant_binding_mismatch",
                company_id=company_id,
                plugin_id=listed.plugin_id,
            ) from exc

        try:
            current = await self._ports.grant_store.get(
                company_id=company_id, plugin_id=listed.plugin_id
            )
        except Exception as exc:  # noqa: BLE001 - typed read-only failure
            raise EffectiveGrantReadError(
                reason="grant_store_unavailable",
                company_id=company_id,
                plugin_id=listed.plugin_id,
            ) from exc
        if current is None:
            raise EffectiveGrantReadError(
                reason="grant_missing", company_id=company_id, plugin_id=listed.plugin_id
            )
        expected = (
            listed.grant_id,
            company_id,
            listed.plugin_id,
            listed.plugin_instance_id,
            listed.manifest_version,
            listed.artifact_digest,
        )
        actual = (
            current.grant_id,
            current.company_id,
            current.plugin_id,
            current.plugin_instance_id,
            current.manifest_version,
            current.artifact_digest,
        )
        if actual != expected:
            raise EffectiveGrantReadError(
                reason="grant_binding_mismatch",
                company_id=company_id,
                plugin_id=listed.plugin_id,
            )

        try:
            instance = await self._ports.instance_store.get(
                company_id=company_id, plugin_id=current.plugin_id
            )
        except Exception as exc:  # noqa: BLE001 - typed read-only failure
            raise EffectiveGrantReadError(
                reason="grant_store_unavailable",
                company_id=company_id,
                plugin_id=current.plugin_id,
            ) from exc
        instance_binding = None if instance is None else (
            instance.company_id,
            instance.plugin_id,
            instance.plugin_instance_id,
            instance.manifest_version,
            instance.artifact_digest,
        )
        if instance_binding != actual[1:]:
            raise EffectiveGrantReadError(
                reason="grant_binding_mismatch",
                company_id=company_id,
                plugin_id=current.plugin_id,
            )

        if not current.is_active or current.revoked_at is not None:
            return current, None, EffectiveQuotasStatus.UNAVAILABLE
        try:
            revoked = await self._ports.revocation.is_revoked(grant_id=current.grant_id)
        except Exception as exc:  # noqa: BLE001 - active status unknown ≠ available
            raise EffectiveGrantReadError(
                reason="active_status_unavailable",
                company_id=company_id,
                plugin_id=current.plugin_id,
            ) from exc
        if not isinstance(revoked, bool):
            raise EffectiveGrantReadError(
                reason="active_status_unavailable",
                company_id=company_id,
                plugin_id=current.plugin_id,
            )
        if revoked:
            return current, None, EffectiveQuotasStatus.UNAVAILABLE

        quotas = current.quotas
        try:
            if not isinstance(quotas, QuotaSet):
                raise TypeError("effective quotas must be QuotaSet")
            for name in QUOTA_KEYS:
                value = getattr(quotas, name, None)
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise ValueError(f"invalid effective quota {name}")
        except (AttributeError, TypeError, ValueError) as exc:
            raise EffectiveGrantReadError(
                reason="quotas_invalid", company_id=company_id, plugin_id=current.plugin_id
            ) from exc
        return current, quotas, EffectiveQuotasStatus.AVAILABLE

    async def list(self, *, company_id: str) -> PluginListSnapshot:
        """Установленные плагины + статусы и фактические effective quotas."""

        _validate_company(company_id=company_id)
        grants = await self._list_company_grants(company_id=company_id)
        enabled = tuple(await self._ports.grant_store.get_enabled_modules(company_id=company_id))
        enabled_set = frozenset(enabled)

        items: list[InstalledPlugin] = []
        for listed in grants:
            grant, quotas, quotas_status = await self._read_list_grant(
                company_id=company_id, listed=listed
            )
            runtime = await self._ports.activation.runtime_status(
                company_id=company_id, plugin_id=grant.plugin_id
            )
            items.append(
                InstalledPlugin(
                    plugin_id=grant.plugin_id,
                    plugin_instance_id=grant.plugin_instance_id,
                    manifest_version=grant.manifest_version,
                    artifact_digest=grant.artifact_digest,
                    grant_id=grant.grant_id,
                    grant_status=grant.status,
                    runtime_status=runtime,
                    enabled=grant.plugin_id in enabled_set and grant.is_active,
                    capabilities=grant.capabilities,
                    egress_mode=grant.egress_policy.mode,
                    quotas=quotas,
                    quotas_status=quotas_status,
                )
            )
        return PluginListSnapshot(
            company_id=company_id,
            items=tuple(items),
            enabled_modules=enabled,
        )

    # ------------------------------------------------------------------ strangler

    async def set_enabled_modules(
        self, *, company_id: str, plugin_ids: tuple[str, ...] | list[str]
    ) -> EnabledModulesReceipt:
        """Фича-флаг ``plugin_enabled_modules[]`` (§8 этап 2, §11 критерий 16).

        Переключает только путь обработки: старый declarative-app путь и новый plugin
        путь работают независимо. Никакой миграции/переноса/синхронизации данных,
        dual-read/dual-write или «сверки контрольных сумм» здесь нет и быть не может.
        """

        _validate_company(company_id=company_id)
        normalized = tuple(dict.fromkeys(str(item) for item in plugin_ids))
        for item in normalized:
            if not _SAFE_ID.fullmatch(item):
                raise ValueError(f"недопустимый plugin_id в enabled_modules: {item!r}")

        current = tuple(await self._ports.grant_store.get_enabled_modules(company_id=company_id))
        if tuple(sorted(current)) == tuple(sorted(normalized)):
            # Идемпотентный повтор: даже порт записи не дёргаем.
            return EnabledModulesReceipt(
                company_id=company_id,
                enabled_modules=current,
                changed=False,
                replayed=True,
                data_migration_performed=False,
            )

        await self._decision_audit(
            operation=AdminOperation.ENABLED_MODULES,
            company_id=company_id,
            plugin_id="enabled-modules",
        )
        stored = tuple(
            await self._ports.grant_store.set_enabled_modules(
                company_id=company_id, plugin_ids=normalized
            )
        )
        await self._audit(
            operation=AdminOperation.ENABLED_MODULES,
            company_id=company_id,
            plugin_id="enabled-modules",
            decision=AdminDecision.ALLOW,
            reason="strangler path switched without data migration",
        )
        return EnabledModulesReceipt(
            company_id=company_id,
            enabled_modules=stored,
            changed=True,
            replayed=False,
            data_migration_performed=False,
        )

    # ------------------------------------------------------------------ helpers

    async def _require_active_grant(
        self, *, operation: AdminOperation, company_id: str, plugin_id: str
    ) -> CapabilityGrantRecord:
        grant = await self._ports.grant_store.get(company_id=company_id, plugin_id=plugin_id)
        if grant is None or not grant.is_active:
            error = grant_not_found(company_id=company_id, plugin_id=plugin_id)
            await self._deny_audit(
                operation=operation,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="active grant missing",
            )
            raise error
        return grant


__all__ = ["EffectiveGrantReadError", "PluginAdminService"]

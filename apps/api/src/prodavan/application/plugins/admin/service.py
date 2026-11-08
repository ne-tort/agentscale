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
from uuid import uuid4

from .contracts import (
    PLUGIN_CLAIMS_TTL_SECONDS,
    AdminAuditEvent,
    AdminDecision,
    AdminOperation,
    ArtifactPublicationStatus,
    CapabilityGrantRecord,
    CapabilitySet,
    EnabledModulesReceipt,
    GrantReceipt,
    GrantStatus,
    InstalledPlugin,
    IssuedPluginClaims,
    MintRequest,
    PluginListSnapshot,
    PluginRuntimeStatus,
    RevokeReceipt,
    UninstallReceipt,
    WakeReceipt,
)
from .errors import (
    artifact_not_published,
    grant_exists,
    grant_not_found,
    revoke_failed,
    uninstall_partial,
)
from .ports import PluginAdminPorts

# Идентификаторы, приходящие извне, не должны попадать в SQL-имена, пути Ingress или
# имена k8s-ресурсов без проверки (§2.2, §5.4). Жёсткий allowlist — часть fail-closed.
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")


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
    if not _VERSION.fullmatch(manifest_version or ""):
        raise ValueError("недопустимый manifest_version")


def _validate_digest(artifact_digest: str) -> None:
    if not _DIGEST.fullmatch(artifact_digest or ""):
        raise ValueError("artifact_digest должен быть sha256:<64 hex>")


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
    ) -> None:
        """Отказ тоже пишется в аудит, но его неудача не маскирует сам отказ."""

        await self._audit(
            operation=operation,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.DENY,
            code=code,
            reason=reason,
        )

    # ------------------------------------------------------------------ grant

    async def grant(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
        capabilities: CapabilitySet,
    ) -> GrantReceipt:
        """Установить плагин компании (§5.5, §5.1 шаги 1–6).

        Порядок проверок: валидация → PUBLISHED-гейт → идемпотентность → только потом
        позитивные мутации инфраструктуры. Ни один шаг не выполняется для
        QUARANTINED/отсутствующего артефакта.
        """

        _validate_identity(company_id=company_id, plugin_id=plugin_id)
        _validate_version(manifest_version)
        _validate_digest(artifact_digest)
        if not isinstance(capabilities, CapabilitySet):
            raise TypeError("capabilities должен быть CapabilitySet (типизированный контракт)")

        # --- 1. Артефакт УЖЕ опубликован. Отсутствие записи = отказ, не «разрешить».
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

        # --- 2. Идемпотентность. Тот же (digest, version, capabilities) → тот же receipt.
        existing = await self._ports.grant_store.get(company_id=company_id, plugin_id=plugin_id)
        if existing is not None and existing.is_active:
            same = (
                existing.artifact_digest == artifact_digest
                and existing.manifest_version == manifest_version
                and existing.capabilities == capabilities.capabilities
                and existing.egress_policy == capabilities.egress_policy
                and existing.quotas == capabilities.quotas
            )
            if not same:
                error = grant_exists(company_id=company_id, plugin_id=plugin_id)
                await self._deny_audit(
                    operation=AdminOperation.GRANT,
                    company_id=company_id,
                    plugin_id=plugin_id,
                    code=error.code,
                    reason="active grant with different artifact",
                )
                raise error

            # Повтор с тем же смыслом: новый короткий токен, но без пересоздания
            # БД/схемы/bucket/grant/маршрутов.
            claims = await self._mint(existing=existing)
            routes = await self._ports.routes.register(
                company_id=company_id, plugin_id=plugin_id, grant_id=existing.grant_id
            )
            await self._audit(
                operation=AdminOperation.GRANT,
                company_id=company_id,
                plugin_id=plugin_id,
                decision=AdminDecision.ALLOW,
                grant_id=existing.grant_id,
                artifact_digest=artifact_digest,
                reason="idempotent replay",
            )
            database = await self._ports.tenant_db.ensure_company_database(company_id=company_id)
            schema = await self._ports.tenant_db.ensure_plugin_schema(
                company_id=company_id, plugin_id=plugin_id, dsn_ref=database.dsn_ref
            )
            objects = await self._ports.object_store.ensure_bucket(
                company_id=company_id, plugin_id=plugin_id
            )
            return GrantReceipt(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=existing.plugin_instance_id,
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

        # --- 3. Инфраструктура компании. DSN остаётся secret_ref, не plaintext.
        database = await self._ports.tenant_db.ensure_company_database(company_id=company_id)
        schema = await self._ports.tenant_db.ensure_plugin_schema(
            company_id=company_id, plugin_id=plugin_id, dsn_ref=database.dsn_ref
        )
        objects = await self._ports.object_store.ensure_bucket(
            company_id=company_id, plugin_id=plugin_id
        )

        # --- 4. Долгоживущий CapabilityGrant: egress_policy + quotas в БД, не в JWT.
        plugin_instance_id = f"inst_{self._uid()}"
        grant = CapabilityGrantRecord(
            grant_id=f"grant_{self._uid()}",
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            capabilities=capabilities.capabilities,
            egress_policy=capabilities.egress_policy,
            quotas=capabilities.quotas,
            status=GrantStatus.ACTIVE,
            created_at=self._now(),
        )
        grant = await self._ports.grant_store.create(grant=grant)

        # --- 5. Deployment replicas: 0 по проверенному digest (задача 09).
        await self._ports.activation.ensure_deployment(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            artifact_digest=artifact_digest,
            dsn_ref=schema.dsn_ref,
        )

        # --- 6. Короткий PluginClaims (только grant_id + TTL).
        claims = await self._mint(existing=grant)

        # --- 7. Маршруты и egress-правила из grant (не из токена, §13.3).
        routes = await self._ports.routes.register(
            company_id=company_id, plugin_id=plugin_id, grant_id=grant.grant_id
        )

        await self._audit(
            operation=AdminOperation.GRANT,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.ALLOW,
            grant_id=grant.grant_id,
            artifact_digest=artifact_digest,
        )
        return GrantReceipt(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            grant=grant,
            claims=claims,
            routes=routes,
            database=database,
            schema=schema,
            objects=objects,
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
        """Деактивировать grant и мгновенно отозвать токен по ``grant_id`` (§11 кр. 12).

        БД, схема и роль **сохраняются** (§5.5): удаление — только в ``uninstall``.
        """

        _validate_identity(company_id=company_id, plugin_id=plugin_id)
        grant = await self._require_active_grant(
            operation=AdminOperation.REVOKE, company_id=company_id, plugin_id=plugin_id
        )

        # Порядок важен: сначала реестр отзывов (мгновенная блокировка запросов),
        # затем деактивация grant, затем снятие маршрутов и Suspended.
        revoked_ok = await self._ports.revocation.revoke(
            grant_id=grant.grant_id, reason="admin revoke"
        )
        if not revoked_ok:
            error = revoke_failed(grant_id=grant.grant_id, reason="revocation store did not confirm")
            await self._deny_audit(
                operation=AdminOperation.REVOKE,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="revocation not confirmed",
            )
            raise error

        deactivated = await self._ports.grant_store.deactivate(grant_id=grant.grant_id)
        if deactivated.status is not GrantStatus.REVOKED:
            error = revoke_failed(grant_id=grant.grant_id, reason="grant still active after deactivate")
            await self._deny_audit(
                operation=AdminOperation.REVOKE,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="deactivation not confirmed",
            )
            raise error

        routes_removed = await self._ports.routes.unregister(
            company_id=company_id, plugin_id=plugin_id, grant_id=grant.grant_id
        )
        if not routes_removed:
            # Маршруты остались — плагин всё ещё достижим; это отказ, а не успех.
            error = revoke_failed(grant_id=grant.grant_id, reason="routes were not removed")
            await self._deny_audit(
                operation=AdminOperation.REVOKE,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason="route unregister not confirmed",
            )
            raise error

        await self._ports.activation.suspend(company_id=company_id, plugin_id=plugin_id)

        await self._audit(
            operation=AdminOperation.REVOKE,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.ALLOW,
            grant_id=grant.grant_id,
        )
        return RevokeReceipt(
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant.grant_id,
            status=GrantStatus.REVOKED,
            token_revoked=True,
            database_preserved=True,
        )

    # ------------------------------------------------------------------ uninstall

    async def uninstall(self, *, company_id: str, plugin_id: str) -> UninstallReceipt:
        """Полное удаление плагина у компании (§5.5, §11 кр. 15).

        Purge tenant infra → drop bucket/prefix → DROP SCHEMA/ROLE → снятие записи
        артефакта из реестра. **Данные dev эфемерны**: snapshot НЕ делается, переноса
        данных НЕТ, DOWN-миграции платформы не запускаются (§8, §13.1).

        Строгая изоляция: каждый вызов порта получает только ``(company_id, plugin_id)``.
        БД компании не дропается, чужие компании/схемы/bucket не затрагиваются.
        """

        _validate_identity(company_id=company_id, plugin_id=plugin_id)
        grant = await self._ports.grant_store.get(company_id=company_id, plugin_id=plugin_id)

        # Идемпотентность: уже удалено — успех без единого шага удаления.
        if grant is None:
            await self._audit(
                operation=AdminOperation.UNINSTALL,
                company_id=company_id,
                plugin_id=plugin_id,
                decision=AdminDecision.ALLOW,
                reason="already absent",
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

        grant_id = grant.grant_id
        completed: list[str] = []
        failed: list[str] = []

        # Все шаги пытаемся выполнить даже при отказе предыдущего, чтобы собрать
        # полный список failed_steps для UNINSTALL_PARTIAL; но итог = отказ.
        async def _step(name: str, coro: object) -> bool:
            try:
                result = await coro  # type: ignore[misc]
                ok = bool(result)
            except Exception:  # noqa: BLE001 - адаптер мог упасть; фиксируем как провал шага
                ok = False
            (completed if ok else failed).append(name)
            return ok

        token_revoked = await _step(
            "token_revocation",
            self._ports.revocation.revoke(grant_id=grant_id, reason="admin uninstall"),
        )
        routes_removed = await _step(
            "routes",
            self._ports.routes.unregister(
                company_id=company_id, plugin_id=plugin_id, grant_id=grant_id
            ),
        )
        infra_purged = await _step(
            "tenant_infra",
            self._ports.activation.purge_infra(company_id=company_id, plugin_id=plugin_id),
        )
        prefix_dropped = await _step(
            "object_prefix",
            self._ports.object_store.drop_prefix(company_id=company_id, plugin_id=plugin_id),
        )
        # Бакет удаляем только если он пуст: False — нормальный исход, НЕ провал.
        bucket_dropped = await self._ports.object_store.drop_bucket_if_empty(company_id=company_id)

        schema_dropped, role_dropped = False, False
        try:
            schema_dropped, role_dropped = await self._ports.tenant_db.drop_plugin_schema(
                company_id=company_id, plugin_id=plugin_id
            )
        except Exception:  # noqa: BLE001 - см. rationale выше
            pass
        if schema_dropped and role_dropped:
            completed.append("schema_and_role")
        else:
            failed.append("schema_and_role")

        artifact_unlinked = await _step(
            "artifact_registry",
            self._ports.artifact_registry.unlink_installation(
                company_id=company_id,
                plugin_id=plugin_id,
                artifact_digest=grant.artifact_digest,
            ),
        )

        grant_deleted = await _step(
            "grant_store",
            self._ports.grant_store.delete(company_id=company_id, plugin_id=plugin_id),
        )

        receipt = UninstallReceipt(
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant_id,
            deleted=grant_deleted and not failed,
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

        if failed or not grant_deleted:
            error = uninstall_partial(
                company_id=company_id, plugin_id=plugin_id, failed_steps=tuple(failed)
            )
            await self._deny_audit(
                operation=AdminOperation.UNINSTALL,
                company_id=company_id,
                plugin_id=plugin_id,
                code=error.code,
                reason=f"failed steps: {', '.join(failed)}",
            )
            raise error

        await self._audit(
            operation=AdminOperation.UNINSTALL,
            company_id=company_id,
            plugin_id=plugin_id,
            decision=AdminDecision.ALLOW,
            grant_id=grant_id,
            artifact_digest=grant.artifact_digest,
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

    async def list(self, *, company_id: str) -> PluginListSnapshot:
        """Установленные плагины компании + их статусы."""

        _validate_company(company_id=company_id)
        grants = await self._ports.grant_store.list_by_company(company_id=company_id)
        enabled = tuple(await self._ports.grant_store.get_enabled_modules(company_id=company_id))
        enabled_set = frozenset(enabled)

        items: list[InstalledPlugin] = []
        for grant in grants:
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


__all__ = ["PluginAdminService"]

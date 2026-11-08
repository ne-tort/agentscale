"""Адаптер Admin API → ``tenantdb.TenantDatabaseService`` (задача 05).

Стык (обе стороны прочитаны):

=================================================  ===============================================
admin (``admin/ports.py::TenantDbPort``)            tenantdb (``service.py``)
=================================================  ===============================================
``ensure_company_database(*, company_id)            ``ensure_company_database(company_id) -> str``
  -> TenantDatabase``                               (позиционный, имя БД, НЕ secret_ref)
``ensure_plugin_schema(*, company_id, plugin_id,    ``grant_plugin(company_id, plugin_id)
  dsn_ref) -> PluginSchema``                        -> TenantDsn`` (позиционный)
``drop_plugin_schema(*, company_id, plugin_id)      ``uninstall_plugin(company_id, plugin_id)
  -> tuple[bool, bool]``                            -> None`` (позиционный)
=================================================  ===============================================

Ключевые решения
----------------
* **Keywords → позиционные.** Admin-порт намеренно keyword-only (авторитетный
  контекст), реальный сервис — позиционный. Адаптер — единственное место
  перевода; чужой каталог не правится.
* **``TenantDsn`` → ``PluginSchema``/``TenantDatabase``.** DSN наружу отдаётся
  ТОЛЬКО как ``secret_ref`` (см. ``TenantDsn.as_ref``), plaintext не покидает
  ``SecretPort``. ``TenantDatabase.dsn_ref`` для компании, у которой ещё нет
  роли плагина, строится как служебная ссылка на БД компании (``secret_ref``
  на ключ tenantdb), а не как выдуманный DSN.
* **``revoke`` ≠ ``uninstall``.** Admin ``revoke`` НЕ вызывает
  ``tenantdb.revoke_plugin``: по §5.5 БД/схема/роль сохраняются, мгновенный
  отзыв делает реестр отзывов identity + деактивация grant. Здесь реализованы
  только три метода админского порта.
* **Идемпотентность.** ``uninstall_plugin`` в чужом сервисе бросает
  ``TenantDbError(NOT_FOUND)``, если роли уже нет; admin-порт требует
  ``(True, True)`` для уже отсутствующих объектов. Адаптер маппит ТОЛЬКО
  ``NOT_FOUND`` в ``(True, True)``; ``ADMIN_UNAVAILABLE`` / ``PRIVILEGE_DENIED``
  / ``INVALID_IDENTIFIER`` остаются явными отказами — fail-closed.
* **Ошибки.** ``TenantDbError`` → ``PluginAdminError`` через
  ``errors.map_tenant_db_error``: исходный ``code``/``operation`` сохраняются в
  ``extra``, DSN/пароль/raw SQL в ``detail`` не попадают.

ОГРАНИЧЕНИЕ: у задачи 12 закрытый набор кодов, отдельного «infra failed»
нет — см. докстринг ``adapters/errors.py``. Родителю рекомендуется добавить
``PLUGIN_ADMIN_INFRA_FAILED`` при F11-интеграции.
"""

from __future__ import annotations

from typing import Any

from prodavan.application.plugins.admin.contracts import (
    PluginSchema,
    TenantDatabase,
)
from prodavan.application.plugins.admin.errors import PluginAdminError
from prodavan.application.plugins.tenantdb.naming import (
    bucket_name,
    plugin_role_name,
    plugin_schema_name,
    plugin_search_path,
    tenant_db_name,
)
from prodavan.application.plugins.tenantdb.service import (
    TenantDatabaseService,
    TenantDsn,
)

from prodavan.application.plugins.tenantdb.errors import (
    NOT_FOUND as TENANT_DB_NOT_FOUND,
)
from prodavan.application.plugins.tenantdb.errors import TenantDbError

from .errors import map_tenant_db_error, map_unexpected_error, require_wired

#: Служебный префикс ссылки на БД компании (НЕ plaintext DSN, не пароль).
#: Реальный DSN живёт в SecretPort; здесь только стабильный идентификатор.
_DB_SECRET_REF_PREFIX = "secret://plugins/tenantdb"


class TenantDbAdapter:
    """``admin.TenantDbPort`` поверх реального ``TenantDatabaseService``.

    Ни один метод не принимает «чужую компанию» извне: ``company_id`` приходит
    только из авторитетного аргумента и сразу валидируется генератором имён
    задачи 05 (``tenant_db_name``/``plugin_role_name``/...), который отвергает
    ``;``, ``-``, верхний регистр, ``../`` и длинные идентификаторы.
    """

    def __init__(self, service: TenantDatabaseService) -> None:
        self._service = require_wired("tenant_db", service)
        if not isinstance(service, TenantDatabaseService):
            raise TypeError(
                "TenantDbAdapter: ожидался TenantDatabaseService "
                "(тонкий адаптер, без подмены чужой реализацией)"
            )

    @property
    def service(self) -> TenantDatabaseService:
        return self._service

    # ------------------------------------------------------------------ ensure БД

    async def ensure_company_database(self, *, company_id: str) -> TenantDatabase:
        """Создать/вернуть БД компании; результат — ``TenantDatabase``.

        Реальный сервис возвращает только имя БД и не сообщает ``created``.
        Поэтому ``created`` вычисляется честно: до вызова запрашиваем
        существование через тот же админский порт (``SELECT 1 ...``), а не
        «ставим True и надеемся». Нет админского соединения → ``TenantDbError``
        → ``PluginAdminError`` (fail-closed), а не «считаем что уже есть».
        """

        db_name = self._db_name(company_id=company_id)
        try:
            created = not await self._database_exists(db_name=db_name)
            returned = await self._service.ensure_company_database(company_id)
        except TenantDbError as exc:
            raise map_tenant_db_error(
                exc,
                operation="ensure_company_database",
                reason="не удалось обеспечить БД компании",
            ) from None
        except PluginAdminError:
            # Наш же отказ (например, AdminConnectionPort не подключён) не
            # перехватываем как «непредвиденное исключение адаптера».
            raise
        except Exception as exc:  # noqa: BLE001 - драйвер/порт мог упасть как угодно
            raise map_unexpected_error(
                exc,
                operation="ensure_company_database",
                reason="не удалось обеспечить БД компании",
            ) from None

        if not isinstance(returned, str) or not returned.strip():
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="TenantDatabaseService.ensure_company_database вернул пустое имя БД",
                status=503,
                extra={"adapter_operation": "ensure_company_database"},
            )
        if returned != db_name:
            # Защита от подмены компании: имя обязано быть выведено из нашего id.
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="TenantDatabaseService вернул имя БД другой компании",
                status=503,
                extra={"adapter_operation": "ensure_company_database"},
            )

        return TenantDatabase(
            company_id=company_id,
            database_name=db_name,
            dsn_ref=f"{_DB_SECRET_REF_PREFIX}/{company_id}/TENANT_DB_DSN",
            created=created,
        )

    # ------------------------------------------------------------- ensure схемы

    async def ensure_plugin_schema(
        self, *, company_id: str, plugin_id: str, dsn_ref: str
    ) -> PluginSchema:
        """Создать роль + схему плагина, вернуть типизированный ``PluginSchema``.

        ``dsn_ref`` приходит от admin-сервиса как служебная ссылка НА БД
        КОМПАНИИ (ещё до создания роли плагина, см.
        ``ensure_company_database``), поэтому сравнивать его напрямую с
        ``TenantDsn.secret_ref`` (ссылка на DSN роли плагина) нельзя — это две
        разные ссылки. Адаптер проверяет, что входящий ``dsn_ref`` указывает
        ровно на нашего ``company_id``, а итоговую ``PluginSchema.dsn_ref``
        берёт из проверенного ``TenantDsn.secret_ref``.
        """

        db_name, role_name, schema_name, search_path = self._names(
            company_id=company_id, plugin_id=plugin_id
        )
        try:
            dsn: TenantDsn = await self._service.grant_plugin(company_id, plugin_id)
        except TenantDbError as exc:
            raise map_tenant_db_error(
                exc,
                operation="ensure_plugin_schema",
                reason="не удалось выдать роль/схему плагина",
            ) from None
        except Exception as exc:  # noqa: BLE001
            raise map_unexpected_error(
                exc,
                operation="ensure_plugin_schema",
                reason="не удалось выдать роль/схему плагина",
            ) from None

        if not isinstance(dsn, TenantDsn):
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="TenantDatabaseService.grant_plugin вернул не TenantDsn",
                status=503,
                extra={"adapter_operation": "ensure_plugin_schema"},
            )
        # Строгая проверка области: все имена выведены из (company, plugin).
        if (
            dsn.database != db_name
            or dsn.role != role_name
            or dsn.schema != schema_name
            or dsn.search_path != search_path
            or dsn.bucket != bucket_name(company_id)
        ):
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="TenantDsn указывает на объекты другой компании/плагина",
                status=503,
                extra={"adapter_operation": "ensure_plugin_schema"},
            )
        incoming = (dsn_ref or "").strip()
        if incoming:
            # Ожидаемая служебная ссылка на БД ЭТОЙ компании (формируется
            # ensure_company_database). Чужая компания в dsn_ref = отказ:
            # иначе схема одной компании ушла бы с DSN другой.
            expected_db_ref = f"{_DB_SECRET_REF_PREFIX}/{company_id}/TENANT_DB_DSN"
            if incoming != expected_db_ref:
                raise PluginAdminError(
                    code="GRANT_EXISTS",
                    detail="dsn_ref указывает не на БД этой компании",
                    status=503,
                    extra={"adapter_operation": "ensure_plugin_schema"},
                )

        return PluginSchema(
            company_id=company_id,
            plugin_id=plugin_id,
            database_name=db_name,
            schema_name=schema_name,
            role_name=role_name,
            dsn_ref=dsn.secret_ref,
            search_path=search_path,
            created=not dsn.rotated,
        )

    # ------------------------------------------------------------ drop схемы/роли

    async def drop_plugin_schema(
        self, *, company_id: str, plugin_id: str
    ) -> tuple[bool, bool]:
        """``DROP SCHEMA`` + ``DROP ROLE``; ``(True, True)`` = подтверждено.

        Уже отсутствующая роль (``NOT_FOUND``) трактуется как идемпотентный
        успех ``(True, True)``: других компаний при этом не касаемся, потому что
        имя роли вычисляется из нашей пары. Любая другая ошибка = отказ, а не
        «тихо вернём False/True».
        """

        self._names(company_id=company_id, plugin_id=plugin_id)  # валидация до DDL
        try:
            await self._service.uninstall_plugin(company_id, plugin_id)
        except TenantDbError as exc:
            if exc.code == TENANT_DB_NOT_FOUND:
                return (True, True)
            raise map_tenant_db_error(
                exc,
                operation="drop_plugin_schema",
                reason="не удалось удалить схему/роль плагина",
            ) from None
        except Exception as exc:  # noqa: BLE001
            raise map_unexpected_error(
                exc,
                operation="drop_plugin_schema",
                reason="не удалось удалить схему/роль плагина",
            ) from None
        return (True, True)

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _db_name(*, company_id: str) -> str:
        """Валидировать company_id и вернуть ``tenant_<company>`` (задача 05)."""

        return tenant_db_name(company_id)

    @staticmethod
    def _names(*, company_id: str, plugin_id: str) -> tuple[str, str, str, str]:
        """Одной пачкой: (БД, роль, схема, search_path) + жёсткая валидация."""

        return (
            tenant_db_name(company_id),
            plugin_role_name(company_id, plugin_id),
            plugin_schema_name(plugin_id),
            plugin_search_path(plugin_id),
        )

    async def _database_exists(self, *, db_name: str) -> bool:
        """Честно вычислить ``created`` через тот же админский порт, что и DDL.

        ``SELECT 1 FROM pg_database`` не мутирует состояние; при недоступности
        админского соединения ошибка пробрасывается наверх (fail-closed), а не
        «считаем, что БД уже есть».
        """

        from prodavan.application.plugins.tenantdb import sql
        from prodavan.application.plugins.tenantdb.service import (
            AdminConnectionPort,
            AdminTarget,
        )

        admin: Any = getattr(self._service, "_admin", None)
        if admin is None or not isinstance(admin, AdminConnectionPort):
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="TenantDbAdapter: AdminConnectionPort не подключён к сервису",
                status=503,
                extra={"adapter_operation": "ensure_company_database"},
            )
        target = AdminTarget(scope="cluster")
        stmt = sql.database_exists(db_name)
        result = await admin.execute(stmt.text, dict(stmt.params), target=target)
        # ``ExecuteResult.first_value`` — первое значение первой строки.
        return bool(result.first_value is not None)


__all__ = ["TenantDbAdapter"]

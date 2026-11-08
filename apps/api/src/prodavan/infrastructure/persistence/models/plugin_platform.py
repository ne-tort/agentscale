"""Постоянное хранение состояния plugin-платформы (F11, задача A10).

五 таблиц ниже — это **состояние платформы** в БД ``prodavan``: долгоживущие
``CapabilityGrant``, реестр экземпляров плагинов, реестр подписанных OCI-артефактов,
append-only аудит и журнал доставки Redpanda. Данные САМИХ плагинов живут в
``tenant_<company>`` (задача 05) и сюда не попадают.

Источник истины по контрактам:
  * ``docs/ARCHITECTURE/plugin-architecture-complete.md`` v4, §4.2 / §4.5 / §5.1 /
    §5.5 / §6.3 / §11;
  * доменные типы ``prodavan/domain/plugins/types.py`` (задача 01);
  * типизированные контракты ``prodavan/application/plugins/admin/contracts.py``
    (задача 12);
  * ``docs/08-migration/audit-append-shape-decision.md:13-19`` — запрет raw-значений
    в аудите.

Важно: данные dev эфемерны. Ни один контракт здесь не описывает snapshot, бэкап,
dual-read/dual-write или перенос данных (§5.5 / §8 / §13.1) — они не требуются.
Ревизия не создаёт БД/роли, не включает RLS и не требует расширений (в том числе
``pgaudit``): tenant-кластер и роли делает задача 05, RLS в v4 отвергнут (§13.1).

Стиль — как в ``dynamic_apps.py`` / ``modules.py``: ``Base``, ``Mapped``/
``mapped_column``, ``JSONB``, именованные ``UniqueConstraint``/``CheckConstraint``/
``Index``, ``server_default=func.now()`` и русские комментарии.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


# --------------------------------------------------------------------------- идентификаторы


def _id(prefix: str) -> str:
    """Суррогатный первичный ключ в стиле соседних моделей (``<prefix>_<16 hex>``)."""

    return f"{prefix}_{uuid.uuid4().hex[:16]}"


#: Формат OCI-digest из ``domain/plugins/types.py::ARTIFACT_DIGEST_PATTERN`` и
#: ``application/plugins/admin/contracts.py::CapabilityGrantRecord`` (длина 71).
ARTIFACT_DIGEST_SQL = "^sha256:[0-9a-f]{64}$"

#: Допустимые состояния долгоживущего grant (``GrantStatus`` из задачи 12).
#: Fail-closed: любое другое значение отклоняется БД, а не трактуется как active.
GRANT_STATUSES: tuple[str, ...] = ("active", "revoked")

#: Состояния экземпляра (``domain/plugins/types.py::PluginInstanceStatus``).
INSTANCE_STATUSES: tuple[str, ...] = (
    "installed",
    "active",
    "suspended",
    "revoked",
    "quarantined",
)

#: Состояния артефакта: published/quarantined из ``ArtifactPublicationStatus``
#: (задача 12) плюс терминальный ``revoked`` (§5.5 uninstall / §11 критерий 18).
ARTIFACT_STATUSES: tuple[str, ...] = ("published", "quarantined", "revoked")


def _in_clause(values: tuple[str, ...]) -> str:
    """Собрать SQL-список литералов для ``CheckConstraint`` (без параметров)."""

    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


#: ЯВНЫЙ запрет колонок с raw-значениями в append-only аудите. Список намеренно
#: экспортируется: тест проверяет, что ни одно из этих имён не появилось в таблице.
#: Разрешены только канонические digest-ы (``sql_digest``, ``payload_digest``,
#: ``artifact_digest``) — см. ``audit-append-shape-decision.md:13-19``.
AUDIT_FORBIDDEN_RAW_COLUMNS: frozenset[str] = frozenset(
    {
        "sql",
        "raw_sql",
        "query",
        "params",
        "parameters",
        "bind_params",
        "body",
        "payload",
        "raw_payload",
        "request_body",
        "response_body",
        "token",
        "tokens",
        "access_token",
        "secret",
        "secrets",
        "credentials",
        "authorization",
        "cookie",
        "pii",
    }
)


def build_app_version_projection(manifest: Mapping[str, object]) -> dict[str, object]:
    """Нормализованная legacy-обёртка для ``AppVersion`` (§5.1 v4).

    Плагинный манифест ``plugin-rpc/1`` напрямую не проходит legacy
    ``AppVersion.validate_manifest`` (там ``protocol == "1.0"`` и ``appId`` без
    подчёркиваний). Поэтому реестр хранит ДВЕ разные формы:

    * ``PluginArtifactRow.manifest`` — канонический подписанный plugin-manifest;
    * ``PluginArtifactRow.app_version_projection`` — эта обёртка, пригодная для
      ``AppVersion.manifest``; исходный подписанный manifest сохранён под ключом
      ``plugin_manifest`` (ровно как предписано §5.1).

    Проекция **всегда** отличается от канонического манифеста: другой набор ключей,
    другой ``protocol`` и нормализованный ``appId``. Инвариант дополнительно
    зафиксирован ``CheckConstraint("manifest <> app_version_projection")`` в БД.
    ``AppVersion`` НЕ ослабляется: его правка сломала бы dynamic_apps.
    """

    plugin_id = str(manifest.get("plugin_id", ""))
    capabilities = manifest.get("capabilities", ())
    frontend = manifest.get("frontend", {})
    backend = manifest.get("backend", {})

    return {
        "protocol": "1.0",
        # Legacy appId не содержит подчёркиваний (§5.1).
        "appId": plugin_id.replace("_", "-"),
        "version": str(manifest.get("version", "")),
        "displayName": plugin_id,
        "entrypoints": {
            "frontend": dict(frontend) if isinstance(frontend, Mapping) else {},
            "backend": dict(backend) if isinstance(backend, Mapping) else {},
        },
        "requestedCapabilities": [
            str(item) for item in capabilities if isinstance(item, (str, bytes))
        ]
        if isinstance(capabilities, (list, tuple))
        else [],
        # Исходный подписанный manifest сохранён без переписывания.
        "plugin_manifest": dict(manifest),
    }


# --------------------------------------------------------------------------- 1. Grant


class CapabilityGrantRow(Base):
    """Долгоживущий ``CapabilityGrant`` (§4.2 / §5.5, ``GrantStatus`` задачи 12).

    Egress-политика и квоты лежат ЗДЕСЬ, а не в коротком ``PluginClaims``: иначе
    истечение JWT пересобирало бы сетевые правила посреди запроса (§6.3).
    Отзыв = ``status='revoked'`` + проверка ``grant_id`` на каждом запросе.
    """

    __tablename__ = "plugin_capability_grants"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "plugin_instance_id",
            name="uq_plugin_capability_grant_company_instance",
        ),
        ForeignKeyConstraint(
            ["plugin_instance_id"],
            ["plugin_instances.id"],
            name="fk_plugin_capability_grant_instance",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN {_in_clause(GRANT_STATUSES)}",
            name="ck_plugin_capability_grant_status",
        ),
        CheckConstraint(
            f"artifact_digest ~ '{ARTIFACT_DIGEST_SQL}'",
            name="ck_plugin_capability_grant_digest",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > issued_at",
            name="ck_plugin_capability_grant_expiry",
        ),
        # Индексы по company_id и plugin_id — выдача grant'ов и поиск по плагину.
        Index("ix_plugin_capability_grants_company", "company_id"),
        Index("ix_plugin_capability_grants_plugin", "plugin_id"),
    )

    id: Mapped[str] = mapped_column(
        String(80), primary_key=True, default=lambda: _id("pcg")
    )
    #: Логический tenant-ключ. FK к ``companies`` намеренно НЕ объявлен: как и
    #: ``tenant_id`` в ``dynamic_apps.py``, это независимый идентификатор; это же
    #: позволяет импортировать модуль без загрузки всей карты моделей.
    company_id: Mapped[str] = mapped_column(String(40), nullable=False)
    plugin_id: Mapped[str] = mapped_column(String(160), nullable=False)
    plugin_instance_id: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    #: JSONB-список capability (allowlist §4.4 валидируется до записи задачей 12).
    capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: JSONB: mode / allowed_hosts / allowed_paths / max_egress_bytes (§6.3).
    egress_policy: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: JSONB: ops_per_minute / max_bytes / max_rows / ... (``QuotaSet`` задачи 12).
    quota: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)


# --------------------------------------------------------------------------- 2. Instance


class PluginInstanceRow(Base):
    """Экземпляр установленного плагина (company-scoped, stateless), §5.5 / §7.4.

    ``desired_replicas`` — 0/1 для собственного activator'а (не KEDA, §13.3).
    ``active_job_ids`` блокирует усыпление backend (задача 09, критерий 14),
    ``wake_in_flight`` защищает от параллельных wake-up (критерий 13).
    """

    __tablename__ = "plugin_instances"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "plugin_id", name="uq_plugin_instance_company_plugin"
        ),
        # Экземпляр может ссылаться только на версию, которая ЕСТЬ в реестре.
        ForeignKeyConstraint(
            ["plugin_id", "manifest_version"],
            ["plugin_artifacts.plugin_id", "plugin_artifacts.version"],
            name="fk_plugin_instance_artifact",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN {_in_clause(INSTANCE_STATUSES)}",
            name="ck_plugin_instance_status",
        ),
        CheckConstraint(
            f"artifact_digest ~ '{ARTIFACT_DIGEST_SQL}'",
            name="ck_plugin_instance_digest",
        ),
        CheckConstraint(
            "desired_replicas IN (0, 1)", name="ck_plugin_instance_replicas"
        ),
        Index(
            "ix_plugin_instances_company_status", "company_id", "status"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(80), primary_key=True, default=lambda: _id("pinst")
    )
    company_id: Mapped[str] = mapped_column(String(40), nullable=False)
    plugin_id: Mapped[str] = mapped_column(String(160), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="installed", server_default="installed"
    )
    desired_replicas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active_job_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    wake_in_flight: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Имена инфраструктуры компании из задачи 05/06 (``tenant_<company>`` /
    #: ``tenant-<company>``). Хранятся как факт платформы, без DSN и секретов.
    tenant_db_name: Mapped[str] = mapped_column(String(128), nullable=False)
    bucket_name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# --------------------------------------------------------------------------- 3. Artifact


class PluginArtifactRow(Base):
    """Реестр подписанных OCI-артефактов (§5.1, ``AppVersion``/``RegistryOutcome``).

    Установка разрешена ТОЛЬКО для ``status='published'``; ``quarantined`` и
    ``revoked`` — fail-closed отказ (критерий 18). Деплой идёт по digest,
    никогда по ``:latest``.
    """

    __tablename__ = "plugin_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "plugin_id", "version", name="uq_plugin_artifact_plugin_version"
        ),
        UniqueConstraint("oci_digest", name="uq_plugin_artifact_oci_digest"),
        CheckConstraint(
            f"status IN {_in_clause(ARTIFACT_STATUSES)}",
            name="ck_plugin_artifact_status",
        ),
        CheckConstraint(
            f"oci_digest ~ '{ARTIFACT_DIGEST_SQL}'",
            name="ck_plugin_artifact_digest",
        ),
        # Проекция НЕ равна каноническому подписанному manifest (§5.1). Иначе
        # реестр незаметно потерял бы исходную подпись и сломал compatibility gate.
        CheckConstraint(
            "manifest <> app_version_projection",
            name="ck_plugin_artifact_projection_differs",
        ),
        Index("ix_plugin_artifacts_plugin_status", "plugin_id", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(80), primary_key=True, default=lambda: _id("part")
    )
    plugin_id: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    oci_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    #: Канонический подписанный plugin-manifest (``PluginManifest`` задачи 01).
    #: Нет ``default``: вызывающий ОБЯЗАН передать подписанную форму явно.
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    #: Нормализованная legacy-обёртка для ``AppVersion`` (см. функцию выше).
    #: Нет ``default``: пустая проекция равнялась бы пустому manifest и была бы
    #: отклонена ``ck_plugin_artifact_projection_differs``.
    app_version_projection: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cosign_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    sbom_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="quarantined", server_default="quarantined"
    )
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Момент регистрации в реестре. Nullable, чтобы совпадать с
    #: ``ArtifactRecord.published_at`` (задача 12): у QUARANTINED публикации нет.
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --------------------------------------------------------------------------- 4. Audit


class PluginAuditEventRow(Base):
    """Append-only событие аудита плагинов (§4.5, критерий 11).

    ЯВНЫЙ запрет колонок с raw SQL / параметрами / payload / токенами / секретами:
    см. ``AUDIT_FORBIDDEN_RAW_COLUMNS`` и ``audit-append-shape-decision.md:13-19``.
    Разрешены только канонические digest-ы: ``sql_digest``, ``payload_digest``,
    ``artifact_digest``. FK и RLS намеренно отсутствуют: аудит не должен
    каскадно удаляться вместе с grant/экземпляром (§6 решения), а RLS в v4
    отвергнут (§13.1). Идентификаторы хранятся как неизменяемые source ID.
    """

    __tablename__ = "plugin_audit_events"
    __table_args__ = (
        CheckConstraint(
            f"artifact_digest ~ '{ARTIFACT_DIGEST_SQL}'",
            name="ck_plugin_audit_artifact_digest",
        ),
        # Единственный обязательный индекс по задаче: (company_id, occurred_at).
        Index("ix_plugin_audit_events_company_occurred", "company_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(
        String(80), primary_key=True, default=lambda: _id("paud")
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    company_id: Mapped[str] = mapped_column(String(40), nullable=False)
    cabinet_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    subject_id: Mapped[str] = mapped_column(String(160), nullable=False)
    plugin_instance_id: Mapped[str] = mapped_column(String(80), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    grant_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    resource: Mapped[str] = mapped_column(String(255), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Короткий код/причина решения, НЕ сырые данные запроса.
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sql_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    payload_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_audit_mutation(mapper, connection, target) -> None:  # noqa: ANN001
    """Append-only на уровне ORM: исправление = новое событие, а не UPDATE."""

    raise ValueError(
        "plugin_audit_events is append-only: исправление пишется новым событием"
    )


event.listen(PluginAuditEventRow, "before_update", _reject_audit_mutation)
event.listen(PluginAuditEventRow, "before_delete", _reject_audit_mutation)


# --------------------------------------------------------------------------- 5. Delivery


class PluginEventDeliveryRow(Base):
    """Журнал доставки Redpanda (задача 10, критерий 20).

    Уникальность ``(subscription_id, source_event_id)`` — идемпотентность
    КОНСЬЮМЕРА (``events/contracts.py::ConsumerDedupKey``): ключ по идентификатору
    ИСТОЧНИКА, а не по URL и не по hash тела. offset коммитится вручную только
    после ACK или подтверждённой записи в DLQ.

    Примечание: ``offset`` — зарезервированное слово PostgreSQL, SQLAlchemy
    экранирует его автоматически; в ревизии колонка объявлена точно так же.
    """

    __tablename__ = "plugin_event_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "source_event_id",
            name="uq_plugin_event_delivery_dedup",
        ),
        Index(
            "ix_plugin_event_deliveries_status_retry", "status", "next_retry_at"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(80), primary_key=True, default=lambda: _id("pdel")
    )
    subscription_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    partition: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # PG-reserved имя: preparer диалекта заключит его в кавычки и в ORM, и в DDL.
    offset: Mapped[int] = mapped_column("offset", BigInteger(), nullable=False)
    #: ``pending`` либо исход ``DeliveryOutcome`` (ACK/RETRY/DLQ) задачи 10.
    #: Словарь намеренно не зажат CHECK на уровне БД: журнал не должен зависеть
    #: от написания enum específico диспетчера; NOT NULL + явный статус достаточны.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "ARTIFACT_DIGEST_SQL",
    "ARTIFACT_STATUSES",
    "AUDIT_FORBIDDEN_RAW_COLUMNS",
    "GRANT_STATUSES",
    "INSTANCE_STATUSES",
    "CapabilityGrantRow",
    "PluginArtifactRow",
    "PluginAuditEventRow",
    "PluginEventDeliveryRow",
    "PluginInstanceRow",
    "build_app_version_projection",
]

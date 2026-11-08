"""Состояние plugin-платформы: 6 таблиц в БД ``prodavan`` (F11, задача A10).

Что создаётся
=============

Пять базовых таблиц по ТЗ A10 плюс одна вспомогательная (решение родителя от
2026-11-08, задача «A10-репозитории»):

============================  ==========================================================
``plugin_artifacts``          реестр подписанных OCI-артефактов + проекция AppVersion
``plugin_instances``          company-scoped экземпляры (activator, active_job_ids)
``plugin_capability_grants``  долгоживущий grant: egress/квоты/capabilities НЕ в JWT
``plugin_audit_events``       append-only аудит, БЕЗ raw SQL/params/payload/tokens
``plugin_event_deliveries``   идемпотентность консьюмера Redpanda + DLQ-поля
``plugin_feature_flags``      company-scoped ``plugin_enabled_modules[]`` (strangler)
============================  ==========================================================

Порядок создания важен: ``plugin_instances`` ссылается на ``plugin_artifacts``,
а ``plugin_capability_grants`` — на ``plugin_instances``. Поэтому сначала
артефакты, затем экземпляры, затем grant'ы; остальные три таблицы независимы.
``downgrade`` дропает строго в обратном порядке (PostgreSQL сам удаляет индексы
и ограничения вместе с таблицей).

Границы (жёстко)
================

* **НЕ трогаем** существующие таблицы platform-БД ``prodavan``: ни одного
  ``ALTER``/``DROP``/``UPDATE`` к ``companies``, ``dynamic_app_*``, ``modules``,
  ``m05_*`` и прочим. Ревизия только добавляет новые отношения.
* **НЕ добавляем RLS-политики** — в v4 они отвергнуты (§13.1); tenant-изоляция
  обеспечивается отдельной БД ``tenant_<company>`` и привилегиями роли (задача 05).
* **НЕ создаём БД, роли и схемы плагинов** — это делает tenant-кластер (задача 05).
* **НЕ требуем расширений**: нет ``pgaudit``, ``pgcrypto``, ``citext`` и т.п.
  Partial unique index и ``jsonb_typeof`` — ядро PostgreSQL, JSONB — встроенный тип.
* **Данные dev эфемерны**: переноса, бэкапов, snapshot, dual-read/dual-write
  и метрик парности НЕТ (§5.5 / §8 / §13.1 / план миграции §4). Здесь хранится
  СОСТОЯНИЕ ПЛАТФОРМЫ, а не данные плагинов — те живут в ``tenant_<company>``.

Колонки, ключи, CHECK и индексы transcribed из
``infrastructure/persistence/models/plugin_platform.py`` один-в-один: ревизия
не является вторым источником истины, она лишь материализует ORM-схему.
Литерал ``is_current = true`` в partial unique индексе ДОСЛОВНО совпадает с
моделью — иначе ``alembic check`` на деплое увидит drift и пересоберёт индекс.

Revision ID: 2026110801
Revises: 2026110321
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2026110801"
down_revision: str | Sequence[str] | None = "2026110321"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --------------------------------------------------------------------------- имена таблиц

ARTIFACT_TABLE = "plugin_artifacts"
INSTANCE_TABLE = "plugin_instances"
GRANT_TABLE = "plugin_capability_grants"
AUDIT_TABLE = "plugin_audit_events"
DELIVERY_TABLE = "plugin_event_deliveries"
FEATURE_FLAGS_TABLE = "plugin_feature_flags"

#: Порядок DROP (дети перед родителями) — обратный к порядку CREATE.
_OWNED_TABLES: tuple[str, ...] = (
    FEATURE_FLAGS_TABLE,
    DELIVERY_TABLE,
    AUDIT_TABLE,
    GRANT_TABLE,
    INSTANCE_TABLE,
    ARTIFACT_TABLE,
)

#: Регулярное выражение ``sha256:<64 hex>`` из domain/plugins/types.py (задача 01)
#: и application/plugins/admin/contracts.py (задача 12). В обычной строке, НЕ в
#: f-строке: ``{64}`` должно остаться литералом регулярного выражения.
_DIGEST_RE = "^sha256:[0-9a-f]{64}$"

_GRANT_STATUSES = "('active', 'revoked')"
_INSTANCE_STATUSES = "('installed', 'active', 'suspended', 'revoked', 'quarantined')"
_ARTIFACT_STATUSES = "('published', 'quarantined', 'revoked')"


def _jsonb() -> postgresql.JSONB:
    """JSONB-колонка с тем же DDL, что генерирует autogenerate из ORM ``JSONB``."""

    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # -- 1. Артефакты: родитель для FK из plugin_instances. Создаётся ПЕРВЫМ. --
    op.create_table(
        ARTIFACT_TABLE,
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("plugin_id", sa.String(160), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("oci_digest", sa.String(71), nullable=False),
        sa.Column("image_ref", sa.String(2048), nullable=True),
        sa.Column("manifest", _jsonb(), nullable=False),
        sa.Column("app_version_projection", _jsonb(), nullable=False),
        sa.Column(
            "cosign_verified", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("sbom_digest", sa.String(71), nullable=True),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="quarantined"
        ),
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
        sa.Column(
            "lifecycle_version", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "plugin_id", "version", name="uq_plugin_artifact_plugin_version"
        ),
        sa.UniqueConstraint("oci_digest", name="uq_plugin_artifact_oci_digest"),
        sa.CheckConstraint(
            f"status IN {_ARTIFACT_STATUSES}", name="ck_plugin_artifact_status"
        ),
        sa.CheckConstraint(
            "oci_digest ~ '" + _DIGEST_RE + "'",
            name="ck_plugin_artifact_digest",
        ),
        # §5.1: проекция НЕ равна каноническому подписанному manifest.
        sa.CheckConstraint(
            "manifest <> app_version_projection",
            name="ck_plugin_artifact_projection_differs",
        ),
    )
    op.create_index(
        "ix_plugin_artifacts_plugin_status", ARTIFACT_TABLE, ["plugin_id", "status"]
    )
    # Индекс от ``index=True`` на колонке is_current (имя генерирует SQLAlchemy).
    op.create_index(
        "ix_plugin_artifacts_is_current", ARTIFACT_TABLE, ["is_current"]
    )
    # Partial UNIQUE: ровно одна «текущая» версия на plugin_id. Ядро PostgreSQL,
    # не расширение. Литерал WHERE дословно как в ORM (иначе drift в alembic check).
    op.create_index(
        "uq_plugin_artifact_current",
        ARTIFACT_TABLE,
        ["plugin_id"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    # -- 2. Экземпляры: FK на (plugin_id, version) реестра артефактов. --
    op.create_table(
        INSTANCE_TABLE,
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("company_id", sa.String(40), nullable=False),
        sa.Column("plugin_id", sa.String(160), nullable=False),
        sa.Column("manifest_version", sa.String(128), nullable=False),
        sa.Column("artifact_digest", sa.String(71), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="installed"
        ),
        sa.Column(
            "desired_replicas", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "active_job_ids", _jsonb(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "wake_in_flight", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("tenant_db_name", sa.String(128), nullable=False),
        sa.Column("bucket_name", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "company_id", "plugin_id", name="uq_plugin_instance_company_plugin"
        ),
        sa.ForeignKeyConstraint(
            ["plugin_id", "manifest_version"],
            [f"{ARTIFACT_TABLE}.plugin_id", f"{ARTIFACT_TABLE}.version"],
            name="fk_plugin_instance_artifact",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"status IN {_INSTANCE_STATUSES}", name="ck_plugin_instance_status"
        ),
        sa.CheckConstraint(
            "artifact_digest ~ '" + _DIGEST_RE + "'",
            name="ck_plugin_instance_digest",
        ),
        sa.CheckConstraint(
            "desired_replicas IN (0, 1)", name="ck_plugin_instance_replicas"
        ),
    )
    op.create_index(
        "ix_plugin_instances_company_status",
        INSTANCE_TABLE,
        ["company_id", "status"],
    )

    # -- 3. Grant'ы: FK на plugin_instances, уникальность (company, instance). --
    op.create_table(
        GRANT_TABLE,
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("company_id", sa.String(40), nullable=False),
        sa.Column("plugin_id", sa.String(160), nullable=False),
        sa.Column("plugin_instance_id", sa.String(80), nullable=False),
        sa.Column("manifest_version", sa.String(128), nullable=False),
        sa.Column("artifact_digest", sa.String(71), nullable=False),
        sa.Column(
            "capabilities", _jsonb(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "egress_policy", _jsonb(), nullable=False, server_default="{}"
        ),
        sa.Column("quota", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.UniqueConstraint(
            "company_id",
            "plugin_instance_id",
            name="uq_plugin_capability_grant_company_instance",
        ),
        sa.ForeignKeyConstraint(
            ["plugin_instance_id"],
            [f"{INSTANCE_TABLE}.id"],
            name="fk_plugin_capability_grant_instance",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"status IN {_GRANT_STATUSES}", name="ck_plugin_capability_grant_status"
        ),
        sa.CheckConstraint(
            "artifact_digest ~ '" + _DIGEST_RE + "'",
            name="ck_plugin_capability_grant_digest",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > issued_at",
            name="ck_plugin_capability_grant_expiry",
        ),
    )
    op.create_index(
        "ix_plugin_capability_grants_company", GRANT_TABLE, ["company_id"]
    )
    op.create_index("ix_plugin_capability_grants_plugin", GRANT_TABLE, ["plugin_id"])

    # -- 4. Append-only аудит. ЯВНЫЙ запрет колонок sql/body/token/secret/... --
    # Разрешены только канонические digest-ы: sql_digest, payload_digest,
    # artifact_digest (audit-append-shape-decision.md:13-19, §4.5 v4).
    op.create_table(
        AUDIT_TABLE,
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("company_id", sa.String(40), nullable=False),
        sa.Column("cabinet_id", sa.String(40), nullable=True),
        sa.Column("project_id", sa.String(40), nullable=True),
        sa.Column("subject_id", sa.String(160), nullable=False),
        sa.Column("plugin_instance_id", sa.String(80), nullable=False),
        sa.Column("artifact_digest", sa.String(71), nullable=False),
        sa.Column("grant_id", sa.String(80), nullable=True),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("resource", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("sql_digest", sa.String(71), nullable=True),
        sa.Column("payload_digest", sa.String(71), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "artifact_digest ~ '" + _DIGEST_RE + "'",
            name="ck_plugin_audit_artifact_digest",
        ),
    )
    op.create_index(
        "ix_plugin_audit_events_company_occurred",
        AUDIT_TABLE,
        ["company_id", "occurred_at"],
    )

    # -- 5. Доставка Redpanda. ``offset`` — reserved word PG: preparer диалекта --
    # экранирует имя автоматически (как и в ORM-модели). --
    op.create_table(
        DELIVERY_TABLE,
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("subscription_id", sa.String(160), nullable=False),
        sa.Column("source_event_id", sa.String(200), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=True),
        sa.Column("topic", sa.String(255), nullable=False),
        sa.Column("dlq_topic", sa.String(255), nullable=True),
        sa.Column("partition", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("offset", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_kind", sa.String(128), nullable=True),
        sa.Column("payload_digest", sa.String(71), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "subscription_id",
            "source_event_id",
            name="uq_plugin_event_delivery_dedup",
        ),
    )
    op.create_index(
        "ix_plugin_event_deliveries_status_retry",
        DELIVERY_TABLE,
        ["status", "next_retry_at"],
    )

    # -- 6. Company-scoped фича-флаг strangler'а ( plugin_enabled_modules[] ). --
    op.create_table(
        FEATURE_FLAGS_TABLE,
        sa.Column("company_id", sa.String(40), primary_key=True, nullable=False),
        sa.Column(
            "enabled_modules", _jsonb(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(enabled_modules) = 'array'",
            name="ck_plugin_feature_flags_enabled_modules_array",
        ),
    )


def downgrade() -> None:
    """Дроп 6 таблиц в обратном порядке (дети перед родителями).

    PostgreSQL удаляет индексы и ограничения вместе с таблицей, поэтому
    отдельные ``drop_index``/``drop_constraint`` не нужны. Никакие существующие
    таблицы platform-БД ``prodavan`` не затрагиваются.
    """

    for name in _OWNED_TABLES:
        op.drop_table(name)

"""Unit-тесты ORM-моделей plugin-платформы (A10).

Проверяется форма метаданных SQLAlchemy, а не поведение БД: обязательные поля,
формат digest, уникальности, CHECK-ограничения, ЯВНЫЙ запрет raw-колонок в аудите
(``audit-append-shape-decision.md:13-19``), уникальность доставки и различие
канонического манифеста и проекции ``AppVersion`` (§5.1 v4).

Тесты НЕ запускаются (verification freeze). Никаких подключений к PostgreSQL.
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from prodavan.infrastructure.persistence.models.plugin_platform import (
    ARTIFACT_DIGEST_SQL,
    ARTIFACT_STATUSES,
    AUDIT_FORBIDDEN_RAW_COLUMNS,
    GRANT_STATUSES,
    INSTANCE_STATUSES,
    CapabilityGrantRow,
    PluginArtifactRow,
    PluginAuditEventRow,
    PluginEventDeliveryRow,
    PluginFeatureFlagRow,
    PluginInstanceRow,
    build_app_version_projection,
)

DIGEST = "sha256:" + "a" * 64


def _column_names(row: type) -> set[str]:
    return set(row.__table__.c.keys())


def _unique_tuples(row: type) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for constraint in row.__table__.constraints:
        if isinstance(constraint, UniqueConstraint):
            result.add(tuple(sorted(column.name for column in constraint.columns)))
    return result


def _check_texts(row: type) -> list[str]:
    return [
        str(constraint.sqltext)
        for constraint in row.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    ]


def _indexes(row: type) -> dict[str, Index]:
    return {index.name: index for index in row.__table__.indexes if index.name}


# --------------------------------------------------------------------------- обязательные поля


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            CapabilityGrantRow,
            {
                "id", "company_id", "plugin_id", "plugin_instance_id",
                "manifest_version", "artifact_digest", "capabilities",
                "egress_policy", "quota", "status", "issued_at", "expires_at",
                "revoked_at", "created_by",
            },
        ),
        (
            PluginInstanceRow,
            {
                "id", "company_id", "plugin_id", "manifest_version",
                "artifact_digest", "status", "desired_replicas", "last_active_at",
                "active_job_ids", "wake_in_flight", "tenant_db_name", "bucket_name",
                "created_at", "updated_at",
            },
        ),
        (
            PluginArtifactRow,
            {
                "id", "plugin_id", "version", "oci_digest", "image_ref", "manifest",
                "app_version_projection", "cosign_verified", "sbom_digest", "status",
                "quarantine_reason", "lifecycle_version", "is_current",
                "published_at", "created_at",
            },
        ),
        (
            PluginAuditEventRow,
            {
                "id", "request_id", "run_id", "company_id", "cabinet_id",
                "project_id", "subject_id", "plugin_instance_id", "artifact_digest",
                "grant_id", "capability", "operation", "resource", "decision",
                "code", "reason", "sql_digest", "payload_digest", "occurred_at",
            },
        ),
        (
            PluginEventDeliveryRow,
            {
                "id", "subscription_id", "source_event_id", "event_id", "topic",
                "dlq_topic", "partition", "offset", "status", "attempts",
                "error_kind", "payload_digest", "next_retry_at", "created_at",
            },
        ),
        (PluginFeatureFlagRow, {"company_id", "enabled_modules", "updated_at"}),
    ],
)
def test_model_has_exact_expected_columns(row: type, expected: set[str]) -> None:
    """Модель создаётся с обязательными полями, без лишних/недокументированных."""

    assert _column_names(row) == expected


def test_audit_event_has_no_raw_columns() -> None:
    """Критерий 11: НЕТ колонок sql/body/token/secret (и любых их синонимов)."""

    columns = _column_names(PluginAuditEventRow)
    assert columns.isdisjoint(AUDIT_FORBIDDEN_RAW_COLUMNS)
    for forbidden in ("sql", "body", "token", "secret"):
        assert forbidden not in columns
    # Разрешены только канонические digest-ы, не сырые значения.
    assert {"sql_digest", "payload_digest", "artifact_digest"} <= columns


def test_all_six_rows_are_registered_tables() -> None:
    """Все 6 Row-классов объявлены и имеют корректные имена таблиц."""

    assert {
        CapabilityGrantRow.__tablename__,
        PluginInstanceRow.__tablename__,
        PluginArtifactRow.__tablename__,
        PluginAuditEventRow.__tablename__,
        PluginEventDeliveryRow.__tablename__,
        PluginFeatureFlagRow.__tablename__,
    } == {
        "plugin_capability_grants",
        "plugin_instances",
        "plugin_artifacts",
        "plugin_audit_events",
        "plugin_event_deliveries",
        "plugin_feature_flags",
    }


# --------------------------------------------------------------------------- artifact_digest


_DIGEST_COLUMNS = (
    (CapabilityGrantRow, "artifact_digest"),
    (PluginInstanceRow, "artifact_digest"),
    (PluginArtifactRow, "oci_digest"),
    (PluginAuditEventRow, "artifact_digest"),
)


@pytest.mark.parametrize(("row", "column"), _DIGEST_COLUMNS)
def test_digest_column_is_sha256_sized_and_checked(row: type, column: str) -> None:
    """Формат ``sha256:<64 hex>``: ширина 71 + CHECK по регулярному выражению."""

    col = row.__table__.c[column]
    assert col.type.length == 71
    assert not col.nullable
    texts = _check_texts(row)
    assert any(ARTIFACT_DIGEST_SQL in text for text in texts), (
        f"{row.__tablename__}.{column} не имеет CHECK на формат digest"
    )


def test_digest_sql_pattern_rejects_bad_values() -> None:
    """Само регулярное выражение доменного типа: только sha256 + 64 hex."""

    import re

    pattern = re.compile(ARTIFACT_DIGEST_SQL)
    assert pattern.fullmatch(DIGEST)
    assert pattern.fullmatch("sha256:" + "A" * 64) is None  # верхний регистр запрещён
    assert pattern.fullmatch("md5:" + "a" * 64) is None
    assert pattern.fullmatch("sha256:" + "a" * 63) is None
    assert pattern.fullmatch("sha256:" + "a" * 65) is None


# --------------------------------------------------------------------------- уникальности


def test_capability_grant_uniqueness() -> None:
    """Уникальность ``(company_id, plugin_instance_id)`` + индексы company/plugin."""

    assert ("company_id", "plugin_instance_id") in {
        tuple(sorted(item)) for item in _unique_tuples(CapabilityGrantRow)
    }
    names = set(_indexes(CapabilityGrantRow))
    assert "ix_plugin_capability_grants_company" in names
    assert "ix_plugin_capability_grants_plugin" in names


def test_plugin_artifact_uniqueness() -> None:
    """Уникальность ``(plugin_id, version)`` и ``oci_digest``."""

    unique = _unique_tuples(PluginArtifactRow)
    assert ("plugin_id", "version") in unique
    assert ("oci_digest",) in unique


def test_plugin_instance_company_plugin_uniqueness() -> None:
    """Один экземпляр плагина на компанию (company-scoped установка)."""

    assert ("company_id", "plugin_id") in {
        tuple(sorted(item)) for item in _unique_tuples(PluginInstanceRow)
    }


def test_artifact_current_is_partial_unique() -> None:
    """``is_current`` + partial UNIQUE(plugin_id) WHERE is_current = true."""

    col = PluginArtifactRow.__table__.c.is_current
    assert not col.nullable
    assert str(col.server_default.arg) == "false"
    index = _indexes(PluginArtifactRow)["uq_plugin_artifact_current"]
    assert index.unique is True
    assert tuple(column.name for column in index.columns) == ("plugin_id",)
    assert str(index.dialect_options["postgresql"]["where"]) == "is_current = true"


def test_plugin_event_delivery_uniqueness() -> None:
    """Идемпотентность консьюмера: UNIQUE(subscription_id, source_event_id)."""

    assert ("source_event_id", "subscription_id") in {
        tuple(sorted(item)) for item in _unique_tuples(PluginEventDeliveryRow)
    }


def test_feature_flag_is_company_scoped_single_row() -> None:
    """Одна строка на компанию, список (не объект), default ``[]``."""

    table = PluginFeatureFlagRow.__table__
    assert [column.name for column in table.primary_key.columns] == ["company_id"]
    assert not table.c.enabled_modules.nullable
    assert str(table.c.enabled_modules.server_default.arg) == "[]"
    assert any("jsonb_typeof" in text for text in _check_texts(PluginFeatureFlagRow))


# --------------------------------------------------------------------------- CHECK-ограничения (fail-closed)


def test_capability_grant_status_only_active_or_revoked() -> None:
    """``CapabilityGrantRow.status`` ДОПУСКАЕТ только active/revoked."""

    assert set(GRANT_STATUSES) == {"active", "revoked"}
    checks = " ".join(_check_texts(CapabilityGrantRow))
    assert "status IN" in checks
    assert "'active'" in checks and "'revoked'" in checks
    # Никакого третьего статуса в CHECK.
    for extra in ("expired", "suspended", "pending"):
        assert f"'{extra}'" not in checks


def test_instance_and_artifact_status_checks() -> None:
    assert set(INSTANCE_STATUSES) == {
        "installed", "active", "suspended", "revoked", "quarantined",
    }
    assert set(ARTIFACT_STATUSES) == {"published", "quarantined", "revoked"}
    assert "desired_replicas IN (0, 1)" in " ".join(_check_texts(PluginInstanceRow))
    assert "manifest <> app_version_projection" in " ".join(
        _check_texts(PluginArtifactRow)
    )


def test_audit_company_occurred_index() -> None:
    """Индекс ``(company_id, occurred_at)`` из ТЗ A10."""

    index = _indexes(PluginAuditEventRow)["ix_plugin_audit_events_company_occurred"]
    assert tuple(column.name for column in index.columns) == (
        "company_id",
        "occurred_at",
    )


# --------------------------------------------------------------------------- JSONB-типы


@pytest.mark.parametrize(
    ("row", "columns"),
    [
        (
            CapabilityGrantRow,
            ("capabilities", "egress_policy", "quota"),
        ),
        (PluginInstanceRow, ("active_job_ids",)),
        (PluginArtifactRow, ("manifest", "app_version_projection")),
        (PluginFeatureFlagRow, ("enabled_modules",)),
    ],
)
def test_jsonb_columns(row: type, columns: tuple[str, ...]) -> None:
    for name in columns:
        assert isinstance(row.__table__.c[name].type, JSONB)


# --------------------------------------------------------------------------- проекция AppVersion (§5.1)


_CANONICAL_MANIFEST: dict[str, object] = {
    "plugin_id": "mod_equipment",
    "version": "1.2.0",
    "protocol": "plugin-rpc/1",
    "frontend": {"entry": "index.html", "base_path": "/"},
    "backend": {"port": 8080, "health": "/health"},
    "capabilities": ["cache", "objects", "events"],
    "migrations": {"engine": "liquibase", "path": "migrations/liquibase/changelog.xml"},
    "egress_ref": "grant:co_123",
    "public_pages": [],
    "webhooks": ["/webhook/external-system"],
    "min_platform_version": "1.0.0",
}


def test_app_version_projection_differs_from_canonical_manifest() -> None:
    """Проекция НЕ равна каноническому manifest: другой protocol/appId/ключи."""

    projection = build_app_version_projection(_CANONICAL_MANIFEST)
    assert projection != _CANONICAL_MANIFEST
    assert projection["protocol"] == "1.0"
    assert _CANONICAL_MANIFEST["protocol"] == "plugin-rpc/1"
    assert projection["appId"] == "mod-equipment"  # подчёркивание нормализовано
    assert set(projection) != set(_CANONICAL_MANIFEST)
    assert "plugin_manifest" in projection
    assert projection["plugin_manifest"] == _CANONICAL_MANIFEST


def test_projection_is_stable_and_does_not_mutate_manifest() -> None:
    """Проекция детерминирована и не портит подписанный исходник."""

    original = dict(_CANONICAL_MANIFEST)
    first = build_app_version_projection(_CANONICAL_MANIFEST)
    second = build_app_version_projection(_CANONICAL_MANIFEST)
    assert first == second
    assert _CANONICAL_MANIFEST == original


def test_build_projection_empty_manifest_still_differs() -> None:
    """Даже пустой manifest даёт отличную от него обёртку (CHECK в БД не сработает)."""

    projection = build_app_version_projection({})
    assert projection != {}
    assert projection["protocol"] == "1.0"
    assert projection["requestedCapabilities"] == []

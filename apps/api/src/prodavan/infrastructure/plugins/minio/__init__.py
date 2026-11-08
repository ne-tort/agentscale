"""Сборка объектной capability плагинов на MinIO (внедрение портов задачи 06).

Фабрика ``build_plugin_object_store`` — ЕДИНСТВЕННАЯ точка, где контракты
``application/plugins/objects`` связываются с инфраструктурными адаптерами.
Плагин и сервис задачи 06 не знают о boto3/MinIO: они видят только порты.

Схема (v4 §5.4, §4.5)::

    MinioPluginConfig (env, fail-closed)
        ├── MinioBucketAdmin  → BucketAdminPort  (tenant-<company>, МЕРДЖ политик)
        ├── MinioSts          → StsPort          (по умолчанию НЕ доступен!)
        ├── MinioPresigner    → PresignPort      (scoped, TTL 60..3600)
        └── MinioAuditAdapter → AuditPort        (plugin_object_access, redaction)
                    ↓
        PluginObjectStore (задача 06): grant / issue_credentials / presign /
                           list / usage / set_quota / revoke / uninstall

ВАЖНО — ОТКРЫТЫЙ БЛОКЕР v4 §12.8 / план §5.1: официальная документация MinIO
STS ``AssumeRole`` / session policy / ``StringLike`` по ``s3:prefix`` для образа
``RELEASE.2024-10-02T17-50-41Z`` в этой сессии НЕДОСТУПНА. Поэтому:

  * ``PLUGIN_MINIO_STS_ENABLED`` по умолчанию ``false``;
  * ``MinioSts`` создаётся с ``sts_available=False`` и при любом вызове
    ``issue_credentials`` поднимает ``StsUnavailable``;
  * **НИКОГДА** не выдаются root-креды или постоянные креды service user'а
    (``ScopedCredentials`` от имени admin-ключей запрещены);
  * единственный рабочий путь выдачи доступа — **scoped presigned URL** на
    короткий TTL и жёсткий префикс ``module-<plugin>/``.

Включать ``PLUGIN_MINIO_STS_ENABLED=true`` можно ТОЛЬКО после подтверждения
официальной документацией MinIO для конкретного образа (и integration-проверки
критерия §11.19: creds плагина не видят чужой bucket/префикс).

Дополнительно не подтверждено: метод MinIO Admin API для **жёсткой** квоты
(``SetBucketQuota`` / ``mc admin bucket quota --hard``). boto3 S3-клиент таких
операций не имеет, поэтому ``set_quota`` делегируется инжектируемому
``QuotaAdminPort``; без него — fail-closed ``BucketUnavailable``, а НЕ безлимит.

Родитель (F11) обязан:
  * привязать env из Secret ``agentscale-minio-plugin-admin``;
  * добавить ``plugin-buckets-init.yaml`` + ConfigMap политики в
    ``infra/k3s/base/platform/kustomization.yaml``;
  * предоставить append-only ``AuditSinkPort`` (БД/журнал) и ``QuotaAdminPort``.
    Без sink аудит отказывает (``strict=True`` в сервисе → deny), что и есть
    корректное fail-closed поведение, а не «залогируем потом».
"""

from __future__ import annotations

import time
from typing import Any

from prodavan.application.plugins.objects.service import PluginObjectStore
from prodavan.infrastructure.plugins.minio.adapters import (
    InMemoryPluginAuditSink,
    MinioAuditAdapter,
    MinioBucketAdmin,
    MinioPresigner,
    MinioSts,
    QuotaAdminPort,
    UnavailableQuotaAdmin,
)
from prodavan.infrastructure.plugins.minio.config import MinioPluginConfig

#: Квота по умолчанию (1 GiB), как в ``PluginObjectStore`` задачи 06.
DEFAULT_QUOTA_BYTES = 1024 * 1024 * 1024


def build_plugin_object_store(
    config: MinioPluginConfig,
    *,
    audit_sink: Any | None = None,
    quota_admin: Any | None = None,
    s3_client: Any | None = None,
    sts_client: Any | None = None,
    quota_bytes: int = DEFAULT_QUOTA_BYTES,
    clock: Any = time,
    allow_noop_audit: bool = False,
) -> PluginObjectStore:
    """Собрать ``PluginObjectStore`` задачи 06 из MinIO-адаптеров.

    Parameters
    ----------
    config:
        Обязательная fail-closed конфигурация (``MinioPluginConfig.from_env()``).
        Без admin-кредов построение невозможно — фолбэка на root нет.
    audit_sink:
        Append-only приёмник с ``append(event)`` (синхронным или async).
        Если не передан и ``allow_noop_audit=False`` (по умолчанию) —
        ``MinioAuditAdapter`` будет поднимать ``AuditUnavailable``, т.е. любая
        позитивная мутация/выдача прав ОТКЛОНЯЕТСЯ. Это намеренно: тихий
        in-memory лог в проде создал бы иллюзию аудита. Для юнит-тестов можно
        передать ``InMemoryPluginAuditSink()`` или ``allow_noop_audit=True``.
    quota_admin:
        Реализация ``QuotaAdminPort`` (mc/Admin API). Без неё —
        ``UnavailableQuotaAdmin`` и fail-closed отказ ``set_quota``/``grant``.
    s3_client / sts_client:
        Инжекция клиентов (тесты, или уже настроенный boto3-клиент родителя).
    quota_bytes:
        Жёсткая квота бакета; положительная, иначе сервис задачи 06 откажет.
    allow_noop_audit:
        ТОЛЬКО для тестов/локальной отладки: подставляет in-memory sink.
        В проде оставлять ``False`` (fail-closed).

    Returns
    -------
    PluginObjectStore
        Сервис с подключёнными портами. ``sts`` создаётся ВСЕГДА, но при
        ``config.sts_enabled=False`` он немедленно отвечает ``StsUnavailable``
        с явной причиной (документация не подтверждена), а не тихо отсутствует.
    """
    if not isinstance(config, MinioPluginConfig):
        raise TypeError(
            "build_plugin_object_store требует MinioPluginConfig, "
            "получите его через MinioPluginConfig.from_env()"
        )

    sink = audit_sink
    if sink is None and allow_noop_audit:
        sink = InMemoryPluginAuditSink()
    quotas: Any = quota_admin if quota_admin is not None else UnavailableQuotaAdmin()

    bucket_admin = MinioBucketAdmin(
        config=config,
        client=s3_client,
        quota_admin=quotas,
        owns_bucket=True,
        clock=clock,
    )
    presigner = MinioPresigner(config=config, client=s3_client)
    sts = MinioSts(
        config=config,
        sts_client=sts_client,
        sts_available=bool(config.sts_enabled),
        clock=clock,
    )
    audit = MinioAuditAdapter(sink=sink, clock=clock)

    return PluginObjectStore(
        buckets=bucket_admin,
        sts=sts,
        presigner=presigner,
        audit=audit,
        quota_bytes=quota_bytes,
        clock=clock,
    )


__all__ = [
    "DEFAULT_QUOTA_BYTES",
    "MinioPluginConfig",
    "MinioBucketAdmin",
    "MinioSts",
    "MinioPresigner",
    "MinioAuditAdapter",
    "InMemoryPluginAuditSink",
    "QuotaAdminPort",
    "UnavailableQuotaAdmin",
    "build_plugin_object_store",
]

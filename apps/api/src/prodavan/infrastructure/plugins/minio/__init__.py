"""Сборка объектной capability плагинов на MinIO (внедрение портов задачи 06).

Фабрика ``build_plugin_object_store`` — ЕДИНСТВЕННАЯ точка, где контракты
``application/plugins/objects`` связываются с инфраструктурными адаптерами.
Плагин и сервис задачи 06 не знают о boto3/MinIO: они видят только порты.

Схема (v4 §5.4, §4.5)::

    MinioPluginConfig (env, fail-closed)
        ├── MinioBucketAdmin  → BucketAdminPort  (admin-ключ, tenant-*, МЕРДЖ)
        ├── MinioSts          → StsPort          (opt-in, по умолчанию НЕ доступен)
        ├── MinioPresigner    → PresignPort      (ОТДЕЛЬНЫЙ ключ подписанта!)
        └── MinioAuditAdapter → AuditPort        (plugin_object_access, redaction)
                    ↓
        PluginObjectStore (задача 06): grant / issue_credentials / presign /
                           list / usage / set_quota / revoke / uninstall

ВАЖНО — ВЕРДИКТ РОДИТЕЛЯ (сеть недоступна, официальная документация MinIO STS
``AssumeRole`` / session policy / ``StringLike`` по ``s3:prefix`` для образа
``RELEASE.2024-10-02T17-50-41Z`` НЕ ПОДТВЕРЖДЕНА, v4 §12.8 / план §5.1):

  * ``PLUGIN_MINIO_STS_ENABLED`` по умолчанию ``false``; **PresignPort —
    ОСНОВНОЙ и ОБЯЗАТЕЛЬНЫЙ в дефолтном режиме**, STS только opt-in после
    ручной проверки на Verify Dev;
  * ``StsPort`` / ``ScopedCredentials`` СОХРАНЕНЫ (не удаляются), помечены
    opt-in за флагом; ``issue_credentials`` НЕ экспонируется в
    ``/capabilities/v1/objects/*`` по умолчанию (это зона родителя);
  * при любом вызове ``issue_credentials`` без флага — ``StsUnavailable``;
    **НИКОГДА** не выдаются root-креды или постоянные креды service user'а;
  * **ОТДЕЛЬНЫЙ подписывающий principal** ``plugin-object-presigner`` с узкой
    политикой только на ``tenant-*/module-*``. as-built
    ``minio-api-policy.json`` даёт ``agentscale-api`` весь
    ``arn:aws:s3:::agentscale/*``; подпись от такого ключа унаследовала бы
    слишком широкие права, и «политика на префикс» осталась бы на бумаге;
  * **Отзыв (к чужому ``service.py`` задачи 06 НЕ прикасаемся):** его
    ``revoke()`` пишет «STS-сессии не продлеваются» — в presign-режиме это
    неточно. Выданные URL живут до ``X-Amz-Expires`` ≤ 3600 с и НЕ
    отзываются мгновенно. Отзыв = запрет НОВЫХ URL + деактивация
    ``CapabilityGrant``; мгновенный отзыв — РОТАЦИЯ ключа подписанта;
  * **Presigned URL и ``X-Amz-Signature`` НЕ логируются** (в аудите только
    ``key_digest``). In-cluster ``agentscale-minio:9000`` не резолвится
    снаружи → нужен ``proxy=1`` ИЛИ подпись СРАЗУ на внешний host через
    ``PLUGIN_MINIO_PUBLIC_ENDPOINT``; перепись Host после подписи
    инвалидирует SigV4.

Дополнительно не подтверждено: метод MinIO Admin API для **жёсткой** квоты
(``SetBucketQuota`` / ``mc admin bucket quota --hard``). boto3 S3-клиент таких
операций не имеет, поэтому ``set_quota`` делегируется инжектируемому
``QuotaAdminPort``; без него — fail-closed ``BucketUnavailable``, а НЕ безлимит.

Родитель (F11) уже добавил ``plugin-buckets-init.yaml`` +
``agentscale-minio-plugin-policy`` в ``base/platform/kustomization.yaml`` и
env ``agentscale-minio-plugin-admin`` в API Deployment — имена сверены. Осталось:
  * привязать admin/presigner credentials только через PLUGIN_MINIO_*_REF;
    plaintext env переменные отклоняются конфигурацией;
  * предоставить append-only ``AuditSinkPort`` и ``QuotaAdminPort``.
    Без sink аудит отказывает (``strict=True`` в сервисе → deny) — корректное
    fail-closed поведение, а не «залогируем потом».
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
    presign_client: Any | None = None,
    sts_client: Any | None = None,
    quota_bytes: int = DEFAULT_QUOTA_BYTES,
    clock: Any = time,
    allow_noop_audit: bool = False,
    expected_prefix: str | None = None,
) -> PluginObjectStore:
    """Собрать ``PluginObjectStore`` задачи 06 из MinIO-адаптеров.

    Parameters
    ----------
    config:
        Обязательная fail-closed конфигурация через SecretStore refs.
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
    s3_client / presign_client / sts_client:
        Инжекция клиентов (тесты, или уже настроенные boto3-клиенты родителя).
        ``presign_client`` ОТДЕЛЕН от ``s3_client`` намеренно: подпись идёт от
        другого principal. Если не передан — строится из
        ``config.presign_access_key`` / ``config.presign_secret_key`` /
        ``config.presign_endpoint``, а НЕ из admin-ключей.
    quota_bytes:
        Жёсткая квота бакета; положительная, иначе сервис задачи 06 откажет.
    allow_noop_audit:
        ТОЛЬКО для тестов/локальной отладки: подставляет in-memory sink.
        В проде оставлять ``False`` (fail-closed).
    expected_prefix:
        Канонический ``module-<plugin>/``, который адаптеру разрешено
        подписывать (например ``module-hello_plugin/``). Задаётся вызывающим,
        который знает пару (company, plugin). ``None`` = прямая проверка
        структуры ключа без привязки к конкретному плагину.

    Returns
    -------
    PluginObjectStore
        Сервис с подключёнными портами. ``presigner`` ОБЯЗАТЕЛЕН и всегда
        создаётся (дефолтный режим = scoped presigned). ``sts`` тоже создаётся
        ВСЕГДА (контракт задачи 06 не удаляем), но при
        ``config.sts_enabled=False`` немедленно отвечает ``StsUnavailable``
        с явной причиной (документация не подтверждена), а не тихо отсутствует.
    """
    if not isinstance(config, MinioPluginConfig):
        raise TypeError(
            "build_plugin_object_store требует MinioPluginConfig, "
            "получите его через MinioPluginConfig.from_secret_refs()"
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
    presigner = MinioPresigner(
        config=config, client=presign_client,
        # Явный «свой» префикс: ``PresignPort`` не знает plugin_id, а компания
        # может иметь несколько плагинов. Без привязки адаптер счёл бы ЛЮБОЙ
        # ``module-*`` своим и подписал бы чужой ``module-other/secret``.
        # Вызывающий задаёт «свой» префикс по паре (company, plugin).
        expected_prefix=expected_prefix,
    )
    sts = MinioSts(
        config=config,
        sts_client=sts_client,
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

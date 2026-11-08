"""Пин безопасного резолвера MinIO BucketAdmin в F11-композиции.

Урок раунда 2: массовая правка unwired-портов дважды ломала `create_app()`,
при этом полный pytest на fakes регрессию НЕ ловил. Здесь проверяется именно
выделенный резолвер `_minio_bucket_admin()`:

* без env MinIO он НЕ бросает и возвращает ``None`` (вызывающий оставит
  fail-closed ``_UnwiredPort``, имя попадёт в ``plugin_unwired_ports``);
* с валидным env возвращает РЕАЛЬНЫЙ ``MinioBucketAdmin``, а не заглушку;
* конфиг берётся ровно из ``PLUGIN_MINIO_*`` — фолбэка на root/S3_* нет.
"""

from __future__ import annotations

from typing import Any

from prodavan.application.plugins.wiring import install

_MINIO_ENV = {
    "PLUGIN_MINIO_ENDPOINT": "http://agentscale-minio:9000",
    "PLUGIN_MINIO_ADMIN_ACCESS_KEY": "plugin-object-admin",
    "PLUGIN_MINIO_ADMIN_SECRET_KEY": "plugin-object-admin-secret",
    "PLUGIN_MINIO_PRESIGN_ACCESS_KEY": "plugin-object-presigner",
    "PLUGIN_MINIO_PRESIGN_SECRET_KEY": "plugin-object-presigner-secret",
    "PLUGIN_MINIO_STS_ENABLED": "false",
    "PLUGIN_MINIO_REGION": "us-east-1",
}


def test_minio_bucket_admin_is_fail_closed_without_env(monkeypatch: Any) -> None:
    """Отсутствие конфигурации ≠ падение startup и ≠ тихий allow."""
    for key in _MINIO_ENV:
        monkeypatch.delenv(key, raising=False)

    assert install._minio_bucket_admin() is None


def test_minio_bucket_admin_is_real_with_valid_env(monkeypatch: Any) -> None:
    """В кластерной конфигурации порт привязан к реальному адаптеру."""
    for key, value in _MINIO_ENV.items():
        monkeypatch.setenv(key, value)
    from prodavan.infrastructure.plugins.minio.adapters import MinioBucketAdmin

    buckets = install._minio_bucket_admin()

    assert isinstance(buckets, MinioBucketAdmin)
    assert buckets.owns_bucket is True
    assert buckets.config is not None
    assert buckets.config.admin_access_key == "plugin-object-admin"
    assert buckets.config.sts_enabled is False

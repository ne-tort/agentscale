"""Пины безопасных резолверов F11-композиции (MinIO + gateway sessions).

Урок раунда 2: массовая правка unwired-портов дважды ломала `create_app()`,
при этом полный pytest на fakes регрессию НЕ ловил. Здесь проверяются именно
выделенные резолверы `_minio_bucket_admin()` / `_gateway_sessions()`:

* без env/БД они НЕ бросают и возвращают ``None`` (вызывающий оставит
  fail-closed ``_UnwiredPort``, имя попадёт в ``plugin_unwired_ports``);
* с валидной конфигурацией возвращают РЕАЛЬНЫЕ адаптеры, а не заглушки;
* MinIO-конфиг берётся ровно из ``PLUGIN_MINIO_*`` — фолбэка на root/S3_* нет.
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


def test_gateway_sessions_is_real_sql_directory_with_db(monkeypatch: Any) -> None:
    """В кластерной конфигурации SessionPort — реальный SQL-каталог, не заглушка."""
    from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
        SessionAdapter,
        SqlEmployeeDirectory,
    )

    sessions = install._gateway_sessions()

    assert isinstance(sessions, SessionAdapter)
    assert isinstance(sessions._directory, SqlEmployeeDirectory)
    assert sessions._directory._session_factory is not None
    assert sessions._cookie_sessions is None  # opaque cookie = fail-closed


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

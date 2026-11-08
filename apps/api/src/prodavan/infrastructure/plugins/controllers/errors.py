"""Ошибки контроллера синхронизации controller-managed данных (fail-closed).

Главное правило: ``sync()`` НЕ возвращает «частичный успех». Если patch или
reload любого из трёх артефактов упал, вызывающий получает ``SyncError`` с
указанием КОНКРЕТНОГО шага, а не ``SyncReceipt`` с пометкой «2 из 3 успешно».

Redaction (§4.5, §11 критерий 11): в ``detail`` / ``__str__`` / аудит НЕ должны
попадать SCRAM-секреты, plaintext-пароли, DSN, содержимое ``userlist.txt`` или
текст исключения адаптера (в нём может быть строка подключения к PgBouncer/
PostgreSQL). Поэтому ``SyncError`` принимает только безопасный ``step`` и
``reason``; ``cause`` сохраняется как ``__cause__`` для отладки, но НЕ попадает
в строковое представление.
"""

from __future__ import annotations

from typing import Any

from prodavan.domain.errors import AppError

# Стабильные machine-readable коды (problem+json, аудит, алертинг).
SYNC_FAILED_CODE = "PLUGIN_INFRA_SYNC_FAILED"
RENDER_FAILED_CODE = "PLUGIN_INFRA_RENDER_FAILED"

# Шаги синхронизации. Порядок ЗАФИКСИРОВАН в controller.py: tenant-PG →
# PgBouncer → Envoy → audit. Имя шага попадает в SyncError и в аудит.
STEP_TENANT_HBA_PATCH = "tenant_hba_patch"
STEP_TENANT_PG_RELOAD = "tenant_pg_pg_reload_conf"
STEP_USERLIST_PATCH = "pgbouncer_userlist_patch"
STEP_PGBOUNCER_RELOAD = "pgbouncer_reload"
STEP_ENVOY_PATCH = "envoy_config_patch"
STEP_ENVOY_RELOAD = "envoy_hot_reload"
STEP_AUDIT = "audit"

ALL_STEPS: frozenset[str] = frozenset(
    {
        STEP_TENANT_HBA_PATCH,
        STEP_TENANT_PG_RELOAD,
        STEP_USERLIST_PATCH,
        STEP_PGBOUNCER_RELOAD,
        STEP_ENVOY_PATCH,
        STEP_ENVOY_RELOAD,
        STEP_AUDIT,
    }
)


class SyncError(AppError):
    """Ошибка любого шага ``PluginInfraController.sync()``.

    ``step`` — один из ``ALL_STEPS``; ``reason`` — короткий стабильный код
    (``adapter_missing`` / ``adapter_failed`` / ``store_failed`` /
    ``render_failed`` / ``audit_failed``). Исходное исключение адаптера
    рекомендуется передавать через ``raise SyncError(...) from exc``: так
    ``__cause__`` сохранится для трассировки, но НЕ утечёт в логи/аудит.

    НИКОГДА не подставляйте в ``detail`` значения ``/data``: ``userlist.txt``
    содержит SCRAM-секреты, а ``envoy.yaml`` — allowlist (не секрет, но тоже
    не должен копироваться в problem+json).
    """

    def __init__(
        self,
        *,
        step: str,
        reason: str = "adapter_failed",
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if step not in ALL_STEPS:
            # Даже ошибку об ошибке не оставляем без явного контракта.
            raise ValueError(f"unknown sync step: {step!r}")
        self.step = step
        self.reason = reason
        safe_detail = detail or f"синхронизация прервана на шаге {step}: {reason}"
        super().__init__(
            code=SYNC_FAILED_CODE,
            title="Plugin Infra Sync Failed",
            status=503,
            detail=safe_detail,
            extra=extra,
        )


class RenderError(SyncError):
    """Ошибка чистого рендеринга (битые grant'ы, невалидный SCRAM-секрет).

    Наследник ``SyncError`` осознанно: ``pytest.raises(SyncError)`` ловит и
    ошибку рендеринга, и ошибку адаптера. Свой ``code`` подменяется на
    ``RENDER_FAILED_CODE``, чтобы в аудите было видно, что проблема во входных
    данных, а не в k8s/PgBouncer/Envoy.
    """

    def __init__(self, *, reason: str, detail: str | None = None) -> None:
        super().__init__(
            step=STEP_TENANT_HBA_PATCH,  # рендер идёт до первого patch
            reason=reason,
            detail=detail or f"рендеринг артефактов отклонён: {reason}",
        )
        self.code = RENDER_FAILED_CODE
        self.title = "Plugin Infra Render Failed"


__all__ = [
    "SYNC_FAILED_CODE",
    "RENDER_FAILED_CODE",
    "STEP_TENANT_HBA_PATCH",
    "STEP_TENANT_PG_RELOAD",
    "STEP_USERLIST_PATCH",
    "STEP_PGBOUNCER_RELOAD",
    "STEP_ENVOY_PATCH",
    "STEP_ENVOY_RELOAD",
    "STEP_AUDIT",
    "ALL_STEPS",
    "SyncError",
    "RenderError",
]

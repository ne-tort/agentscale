"""Регрессии ensure/rotate/revoke/uninstall tenant DB."""

from __future__ import annotations

import pytest

from prodavan.application.plugins.tenantdb import (
    ADMIN_UNAVAILABLE,
    ROTATION_REQUIRED,
    TenantDbError,
    plugin_role_name,
    plugin_schema_name,
    tenant_db_name,
)

from .conftest import make_service

COMPANY = "co_rotation"
PLUGIN = "mod_equipment"
ROLE = plugin_role_name(COMPANY, PLUGIN)
DB = tenant_db_name(COMPANY)
SCHEMA = plugin_schema_name(PLUGIN)


async def test_ensure_missing_secret_requires_explicit_rotation_without_alter_password() -> None:
    service, admin, secrets, _audit = make_service()
    await service.ensure_plugin(COMPANY, PLUGIN)
    secrets.values.clear()
    password_ddl = [t for t in admin.texts(prefix="ALTER ROLE") if "PASSWORD" in t]

    with pytest.raises(TenantDbError) as caught:
        await service.ensure_plugin(COMPANY, PLUGIN)

    assert caught.value.code == ROTATION_REQUIRED
    assert [t for t in admin.texts(prefix="ALTER ROLE") if "PASSWORD" in t] == password_ddl
    assert secrets.rotates == []


async def test_ensure_mismatched_secret_requires_rotation_and_never_rewrites_it() -> None:
    service, _admin, secrets, _audit = make_service()
    await service.ensure_plugin(COMPANY, PLUGIN)
    key = next(iter(secrets.values))
    original = secrets.values[key]
    secrets.values[key] = original.replace(f"://{ROLE}:", "://other_role:")

    with pytest.raises(TenantDbError) as caught:
        await service.ensure_plugin(COMPANY, PLUGIN)

    assert caught.value.code == ROTATION_REQUIRED
    assert secrets.values[key].startswith("postgresql://other_role:")
    assert secrets.rotates == []


async def test_explicit_rotate_changes_dsn_then_replay_preserves_it() -> None:
    service, admin, secrets, _audit = make_service()
    first = await service.ensure_plugin(COMPANY, PLUGIN)
    rotated = await service.rotate_plugin(COMPANY, PLUGIN)
    password_ddl = [t for t in admin.texts(prefix="ALTER ROLE") if "PASSWORD" in t]
    replay = await service.ensure_plugin(COMPANY, PLUGIN)

    assert rotated.rotated is True
    assert rotated.secret_ref != first.secret_ref
    assert replay.secret_ref == rotated.secret_ref
    assert len(secrets.rotates) == 1
    assert [t for t in admin.texts(prefix="ALTER ROLE") if "PASSWORD" in t] == password_ddl


async def test_repeated_revoke_still_terminates_sessions_and_cleans_secret() -> None:
    service, admin, secrets, _audit = make_service()
    await service.ensure_plugin(COMPANY, PLUGIN)
    await service.revoke_plugin(COMPANY, PLUGIN)
    await service.revoke_plugin(COMPANY, PLUGIN)

    assert secrets.values == {}
    assert admin.count("REVOKE CONNECT") == 2
    terminate = [t for t in admin.texts() if "pg_terminate_backend" in t and "usename" in t]
    assert len(terminate) == 2


async def test_uninstall_retry_after_secret_cleanup_failure_finishes_without_role() -> None:
    service, admin, secrets, _audit = make_service()
    await service.ensure_plugin(COMPANY, PLUGIN)
    original_delete = secrets.delete
    fail_once = True

    async def delete_with_failure(*, secret_ref: str) -> None:
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("secret backend unavailable")
        await original_delete(secret_ref=secret_ref)

    secrets.delete = delete_with_failure  # type: ignore[method-assign]
    with pytest.raises(TenantDbError) as caught:
        await service.uninstall_plugin(COMPANY, PLUGIN)
    assert caught.value.code == ADMIN_UNAVAILABLE
    assert ROLE not in admin.roles
    assert SCHEMA not in admin.schemas[DB]

    await service.uninstall_plugin(COMPANY, PLUGIN)
    assert secrets.values == {}

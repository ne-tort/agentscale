"""Seed builders for the legacy gateway port tests.

Rows are written through the **real ORM classes**, so every assertion proves the
adapters agree with the registered models instead of with an invented schema.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.infrastructure.persistence.models.dynamic_apps import (
    AppDefinitionRow,
    AppGrantRow,
    AppInstanceRow,
    AppVersionRow,
)

from .sqlite_mirror import (
    APP_ID,
    CAPABILITY_GRANT,
    DEFINITION_A,
    DEFINITION_B,
    DIGEST_A,
    DIGEST_B,
    GRANT_A,
    GRANT_B,
    INSTANCE_A,
    INSTANCE_B,
    MANIFEST,
    MANIFEST_VERSION,
    TENANT_A,
    TENANT_B,
    VERSION_A,
    VERSION_B,
)

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
"""A fixed instant; the ports' clock is driven from it, never from the wall clock."""


def manifest(*, app_id: str = APP_ID, version: str = MANIFEST_VERSION) -> dict[str, Any]:
    """A copy of the accepted manifest, re-labelled for another app/version."""

    payload = {
        "protocol": "1.0",
        "appId": app_id,
        "version": version,
        "displayName": "Demo plugin",
        "requestedCapabilities": [{"name": "storage.kv", "reason": "persist drafts"}],
        "entrypoints": {
            "plugin": {
                "methods": [
                    {
                        "method": "kv.put",
                        "capability": "storage.kv",
                        "operation": "write",
                        "resource": "drafts",
                        "domain": "storage",
                    }
                ]
            }
        },
    }
    return payload


def definition(
    *,
    row_id: str = DEFINITION_A,
    tenant_id: str = TENANT_A,
    app_id: str = APP_ID,
    scope_kind: str = "tenant",
    scope_id: str | None = None,
    status: str = "published",
    current_version_id: str | None = VERSION_A,
) -> AppDefinitionRow:
    return AppDefinitionRow(
        id=row_id,
        tenant_id=tenant_id,
        app_id=app_id,
        display_name="Demo plugin",
        scope_kind=scope_kind,
        scope_id=scope_id if scope_id is not None else tenant_id,
        status=status,
        current_version_id=current_version_id,
        created_at=NOW,
        updated_at=NOW,
    )


def version(
    *,
    row_id: str = VERSION_A,
    definition_id: str = DEFINITION_A,
    manifest_version: str = MANIFEST_VERSION,
    digest: str = DIGEST_A,
    status: str = "published",
    payload: dict[str, Any] | None = None,
    requested: list[dict[str, Any]] | None = None,
) -> AppVersionRow:
    body = payload if payload is not None else manifest(version=manifest_version)
    return AppVersionRow(
        id=row_id,
        app_definition_id=definition_id,
        manifest_version=manifest_version,
        artifact_digest=digest,
        manifest=body,
        requested_capabilities=(
            requested
            if requested is not None
            else list(body.get("requestedCapabilities") or [])
        ),
        status=status,
        created_at=NOW,
        published_at=NOW if status == "published" else None,
    )


def grant(
    *,
    row_id: str = GRANT_A,
    definition_id: str = DEFINITION_A,
    version_id: str = VERSION_A,
    scope_kind: str = "tenant",
    scope_id: str = TENANT_A,
    entries: list[dict[str, Any]] | None = None,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> AppGrantRow:
    return AppGrantRow(
        id=row_id,
        app_definition_id=definition_id,
        app_version_id=version_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        grants=entries if entries is not None else [dict(CAPABILITY_GRANT)],
        issued_at=issued_at or NOW - timedelta(days=1),
        expires_at=expires_at,
    )


def instance(
    *,
    row_id: str = INSTANCE_A,
    definition_id: str = DEFINITION_A,
    version_id: str = VERSION_A,
    scope_kind: str = "tenant",
    scope_id: str = TENANT_A,
    status: str = "published",
) -> AppInstanceRow:
    return AppInstanceRow(
        id=row_id,
        app_definition_id=definition_id,
        app_version_id=version_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


async def seed_baseline(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str = TENANT_A,
    definition_id: str = DEFINITION_A,
    version_id: str = VERSION_A,
    instance_id: str = INSTANCE_A,
    grant_id: str = GRANT_A,
    digest: str = DIGEST_A,
) -> None:
    """One published definition/version/instance with one unexpired capability grant."""

    async with session_factory() as session:
        session.add(
            definition(
                row_id=definition_id,
                tenant_id=tenant_id,
                status="published",
                current_version_id=version_id,
            )
        )
        await session.flush()
        session.add(version(row_id=version_id, definition_id=definition_id, digest=digest))
        await session.flush()
        session.add(
            grant(
                row_id=grant_id,
                definition_id=definition_id,
                version_id=version_id,
                scope_id=tenant_id,
            )
        )
        session.add(
            instance(
                row_id=instance_id,
                definition_id=definition_id,
                version_id=version_id,
                scope_id=tenant_id,
            )
        )
        await session.commit()


async def seed_second_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A complete, otherwise identical installation owned by the *other* tenant."""

    await seed_baseline(
        session_factory,
        tenant_id=TENANT_B,
        definition_id=DEFINITION_B,
        version_id=VERSION_B,
        instance_id=INSTANCE_B,
        grant_id=GRANT_B,
        digest=DIGEST_B,
    )


__all__ = [
    "NOW",
    "definition",
    "grant",
    "instance",
    "manifest",
    "seed_baseline",
    "seed_second_tenant",
    "version",
]

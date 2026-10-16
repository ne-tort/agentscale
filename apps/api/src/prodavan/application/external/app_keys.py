"""External Chat API app keys — machine credentials for /api/v1/external.

A key acts as a cabinet employee (auth-as): company + optional cabinet scope +
scopes. The secret is shown once at creation; the row stores only a sha256 hash.
"""

from __future__ import annotations

import hashlib
import logging
import secrets as _secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.external_api import ApiAppKeyRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "ak_"
_SCOPES_DEFAULT = ["chat:read", "chat:write", "catalog:read"]


@dataclass(frozen=True)
class ExternalAppContext:
    """Resolved identity of an app key: the employee it acts as."""

    key_id: str
    company_id: str
    cabinet_id: str | None
    employee_id: str
    scopes: frozenset[str]


class ApiAppKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        label: str,
        company_id: str,
        employee_id: str,
        cabinet_id: str | None = None,
        scopes: list[str] | None = None,
        expires_at: datetime | None = None,
        created_by: str | None = None,
    ) -> tuple[ApiAppKeyRow, str]:
        """Create a key; returns (row, full_token). Token shown once."""
        label = label.strip()
        if not label:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="label required")
        secret = _secrets.token_urlsafe(32)
        token = f"{TOKEN_PREFIX}{secret}"
        row = ApiAppKeyRow(
            label=label,
            company_id=company_id,
            cabinet_id=cabinet_id or None,
            employee_id=employee_id,
            token_prefix=token[:11],
            secret_hash=_hash(token),
            scopes=list(scopes or _SCOPES_DEFAULT),
            status="active",
            expires_at=expires_at,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row, token

    async def resolve(self, token: str) -> ExternalAppContext:
        """Resolve a Bearer app key to its employee context."""
        if not token or not token.startswith(TOKEN_PREFIX):
            raise AppError(code="UNAUTHORIZED", title="Unauthorized", status=401, detail="invalid app key")
        prefix = token[:11]
        q = await self._session.execute(
            select(ApiAppKeyRow).where(ApiAppKeyRow.token_prefix == prefix)
        )
        row = q.scalars().first()
        if row is None or row.secret_hash != _hash(token):
            raise AppError(code="UNAUTHORIZED", title="Unauthorized", status=401, detail="invalid app key")
        if row.status != "active":
            raise AppError(code="UNAUTHORIZED", title="Unauthorized", status=401, detail="app key revoked")
        if row.expires_at is not None and row.expires_at < datetime.now(UTC):
            raise AppError(code="UNAUTHORIZED", title="Unauthorized", status=401, detail="app key expired")
        # коснёмся last_used_at без блокировки запроса
        row.last_used_at = datetime.now(UTC)
        await self._session.flush()
        return ExternalAppContext(
            key_id=row.id,
            company_id=row.company_id,
            cabinet_id=row.cabinet_id,
            employee_id=row.employee_id,
            scopes=frozenset(row.scopes or []),
        )

    async def list_for_company(self, company_id: str) -> list[ApiAppKeyRow]:
        q = await self._session.execute(
            select(ApiAppKeyRow)
            .where(ApiAppKeyRow.company_id == company_id)
            .order_by(ApiAppKeyRow.created_at.desc())
        )
        return list(q.scalars().all())

    async def revoke(self, key_id: str, *, company_id: str) -> bool:
        q = await self._session.execute(
            select(ApiAppKeyRow).where(
                ApiAppKeyRow.id == key_id, ApiAppKeyRow.company_id == company_id
            )
        )
        row = q.scalars().first()
        if row is None:
            return False
        row.status = "revoked"
        await self._session.flush()
        return True


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

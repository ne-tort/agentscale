"""FastAPI dependencies."""

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.auth.jwt import TokenError, decode_access_token
from prodavan.infrastructure.persistence.database import get_db_session

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    cabinet_ids: list[uuid.UUID]


async def get_session() -> AsyncSession:
    async for session in get_db_session():
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Missing bearer token"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": str(exc)},
        ) from exc

    return CurrentUser(
        user_id=uuid.UUID(payload["sub"]),
        tenant_id=uuid.UUID(payload["tenant_id"]),
        cabinet_ids=[uuid.UUID(cid) for cid in payload.get("cabinet_ids", [])],
    )

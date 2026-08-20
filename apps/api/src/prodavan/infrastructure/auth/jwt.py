"""JWT token creation and validation."""

import uuid
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from prodavan.config.settings import settings


class TokenError(Exception):
    pass


def create_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    cabinet_ids: list[uuid.UUID],
    role: str = "user",
    active_cabinet_id: uuid.UUID | None = None,
    active_project_id: str | None = None,
) -> str:
    expire = datetime.now(UTC) + timedelta(seconds=settings.access_token_ttl_seconds)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "cabinet_ids": [str(cid) for cid in cabinet_ids],
        "role": role,
        "exp": expire,
        "type": "access",
    }
    if active_cabinet_id is not None:
        payload["active_cabinet_id"] = str(active_cabinet_id)
    if active_project_id is not None:
        payload["active_project_id"] = active_project_id
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise TokenError("Invalid token") from exc
    if payload.get("type") != "access":
        raise TokenError("Invalid token type")
    return payload

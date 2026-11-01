"""Request bodies for workspace filesystem routes."""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel, Field

from prodavan.domain.errors import AppError

_WORKSPACE_UPLOAD_MAX_BYTES = 10_485_760


async def read_workspace_upload(request: Request) -> bytes:
    """Read an upload while enforcing the workspace limit before buffering more."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _WORKSPACE_UPLOAD_MAX_BYTES:
            raise AppError(
                code="PAYLOAD_TOO_LARGE",
                title="Payload Too Large",
                status=413,
                detail=f"file exceeds the {_WORKSPACE_UPLOAD_MAX_BYTES} byte upload limit",
                extra={"max_bytes": _WORKSPACE_UPLOAD_MAX_BYTES, "size": size},
            )
        chunks.append(chunk)
    return b"".join(chunks)


class WorkspaceMoveBody(BaseModel):
    model_config = {"extra": "forbid"}

    src: str = Field(min_length=1, max_length=500)
    dst: str = Field(min_length=1, max_length=500)


class WorkspaceCopyBody(BaseModel):
    model_config = {"extra": "forbid"}

    src: str = Field(min_length=1, max_length=500)
    dst: str = Field(min_length=1, max_length=500)

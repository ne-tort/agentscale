"""Prompt use cases (M03)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.services.cabinet_service import CabinetError, get_cabinet
from prodavan.infrastructure.storage.prompt_storage import (
    PromptPathError,
    build_prompt_tree,
    create_version_snapshot,
    get_version_manifest,
    list_version_manifests,
    load_version_state,
    prompts_root,
    read_prompt_file,
    rollback_to_version,
    write_prompt_file,
)


class PromptError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


async def ensure_prompts_access(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    active_cabinet_id: uuid.UUID,
) -> None:
    if cabinet_id != active_cabinet_id:
        raise PromptError("CABINET_MISMATCH", "Cabinet id mismatch", 403)
    try:
        await get_cabinet(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            cabinet_id=cabinet_id,
        )
    except CabinetError as exc:
        raise PromptError(exc.code, exc.message, exc.status) from exc


def _map_storage_error(exc: PromptPathError) -> PromptError:
    code = str(exc)
    status = 404
    if code in {"PATH_FORBIDDEN", "FILE_TOO_LARGE"}:
        status = 422
    elif code == "ETAG_MISMATCH":
        status = 409
    elif code in {"VERSION_CORRUPT"}:
        status = 500
    return PromptError(code, code, status)


def get_prompt_tree(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> dict:
    root = prompts_root(tenant_id, cabinet_id)
    state = load_version_state(root)
    return {
        "root": "prompts/",
        "tree": build_prompt_tree(root),
        "current_version": state.get("current_version_id"),
    }


def get_prompt_file(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, path: str) -> dict:
    try:
        return read_prompt_file(tenant_id, cabinet_id, path)
    except PromptPathError as exc:
        raise _map_storage_error(exc) from exc


def save_prompt_file(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    path: str,
    content: str,
    *,
    expected_etag: str | None,
) -> dict:
    try:
        return write_prompt_file(
            tenant_id,
            cabinet_id,
            path,
            content,
            expected_etag=expected_etag,
        )
    except PromptPathError as exc:
        raise _map_storage_error(exc) from exc


def create_prompt_version(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    *,
    label: str | None,
    user_id: uuid.UUID,
) -> dict:
    return create_version_snapshot(
        tenant_id,
        cabinet_id,
        label=label,
        created_by=user_id,
        parent_version_id=load_version_state(prompts_root(tenant_id, cabinet_id)).get(
            "current_version_id"
        ),
    )


def list_prompt_versions(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> list[dict]:
    return list_version_manifests(tenant_id, cabinet_id)


def get_prompt_version(
    tenant_id: uuid.UUID, cabinet_id: uuid.UUID, version_id: str
) -> dict:
    try:
        return get_version_manifest(tenant_id, cabinet_id, version_id)
    except PromptPathError as exc:
        raise _map_storage_error(exc) from exc


def rollback_prompt_version(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    version_id: str,
    *,
    user_id: uuid.UUID,
    create_backup: bool,
) -> dict:
    try:
        return rollback_to_version(
            tenant_id,
            cabinet_id,
            version_id,
            created_by=user_id,
            create_backup=create_backup,
        )
    except PromptPathError as exc:
        raise _map_storage_error(exc) from exc

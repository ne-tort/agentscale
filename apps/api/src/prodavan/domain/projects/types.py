"""Projects runtime domain types (L07)."""

from __future__ import annotations

import re
import uuid
from enum import StrEnum


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DELETED = "deleted"


class TriggerStatus(StrEnum):
    QUEUED = "queued"
    DONE = "done"
    FAILED = "failed"


PROJECT_TRIGGER_KINDS = frozenset(
    {
        "chat.message",
        "chat.regenerate",
        "project.prepare",
        "system.schedule",
        "webhook.http",
    }
)

# Platform fallback when company policy row missing (see L04 DEFAULT_MAX_ATTACHMENT_MB).
ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024

ATTACHMENT_ALLOWED_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".pdf",
        ".xlsx",
        ".xls",
        ".csv",
        ".txt",
        ".md",
        ".zip",
    }
)


def attachment_extension(filename: str) -> str:
    dot = filename.rfind(".")
    if dot <= 0:
        return ""
    return filename[dot:].lower()


def is_allowed_attachment_filename(filename: str) -> bool:
    ext = attachment_extension(filename.strip())
    return ext in ATTACHMENT_ALLOWED_EXTENSIONS


def new_project_id() -> str:
    return f"proj_{uuid.uuid4().hex[:16]}"


def slugify_name(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return base[:48] or "project"


def workspace_key_for(project_id: str) -> str:
    return project_id.removeprefix("proj_")


def container_ref_for(workspace_key: str) -> str:
    return f"local-ws:{workspace_key}"

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
        "telegram.message",
    }
)

# Company / employee / project lifecycle — NOT project_triggers (see triggers.md).
PLATFORM_EVENT_TYPES = frozenset(
    {
        "project.created",
        "project.paused",
        "project.resumed",
        "project.deleted",
        "company.suspended",
        "company.reactivated",
        "employee.disabled",
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

# Magic prefixes that must never appear in chat uploads (lightweight content policy).
_FORBIDDEN_MAGIC = (
    b"MZ",  # PE / DOS
    b"\x7fELF",  # ELF
    b"\xca\xfe\xba\xbe",  # Mach-O fat
    b"\xcf\xfa\xed\xfe",  # Mach-O 64
    b"\xce\xfa\xed\xfe",  # Mach-O 32
)


def attachment_extension(filename: str) -> str:
    dot = filename.rfind(".")
    if dot <= 0:
        return ""
    return filename[dot:].lower()


def is_allowed_attachment_filename(filename: str) -> bool:
    ext = attachment_extension(filename.strip())
    return ext in ATTACHMENT_ALLOWED_EXTENSIONS


def is_forbidden_attachment_content(raw: bytes) -> bool:
    """Return True if bytes look like an executable (not a full virus scanner)."""
    if not raw:
        return False
    head = raw[:8]
    return any(head.startswith(magic) for magic in _FORBIDDEN_MAGIC)


def new_project_id() -> str:
    return f"proj_{uuid.uuid4().hex[:16]}"


def slugify_name(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return base[:48] or "project"


def workspace_key_for(project_id: str) -> str:
    return project_id.removeprefix("proj_")


def container_ref_for(workspace_key: str) -> str:
    return f"local-ws:{workspace_key}"

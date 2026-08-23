"""Projects runtime domain types (L07)."""

from __future__ import annotations

import mimetypes
import re
import uuid
from datetime import datetime, timedelta
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

# Triggers allowed while company subscription is expired (no agent runtime).
SUBSCRIPTION_EXEMPT_TRIGGER_KINDS = frozenset({"project.prepare"})

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

# Sniff common safe types when client omits/guesses wrong content_type.
_CONTENT_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
)


def sniff_attachment_content_type(raw: bytes, *, filename: str = "", fallback: str | None = None) -> str:
    """Best-effort content type from magic bytes, then filename, then fallback."""
    head = raw[:16] if raw else b""
    for magic, ctype in _CONTENT_MAGIC:
        if head.startswith(magic):
            return ctype
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed
    if fallback and fallback.strip():
        return fallback.strip()
    return "application/octet-stream"


def project_is_idle(
    *,
    last_activity_at: datetime | None,
    now: datetime,
    idle_pause_after_hours: int,
) -> bool:
    """True when last activity is older than the idle threshold (hours > 0)."""
    if idle_pause_after_hours <= 0 or last_activity_at is None:
        return False
    activity = last_activity_at
    if activity.tzinfo is None and now.tzinfo is not None:
        activity = activity.replace(tzinfo=now.tzinfo)
    return activity + timedelta(hours=idle_pause_after_hours) <= now


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

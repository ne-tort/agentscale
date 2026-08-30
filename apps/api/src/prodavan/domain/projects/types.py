"""Projects runtime domain types (L07)."""

from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from datetime import datetime, timedelta
from enum import StrEnum


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"
    DELETED = "deleted"


class ProjectVisibilityMode(StrEnum):
    CABINET_SHARED = "cabinet_shared"
    RESTRICTED = "restricted"


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

# Triggers allowed while project is paused (workspace prepare only).
PAUSE_EXEMPT_TRIGGER_KINDS = frozenset({"project.prepare"})

# Company / employee / project lifecycle — NOT project_triggers (see triggers.md).
PLATFORM_EVENT_TYPES = frozenset(
    {
        "project.created",
        "project.started",
        "project.paused",
        "project.resumed",
        "project.failed",
        "project.recovered",
        "project.completed",
        "project.deleted",
        "project.restored",
        "project.purged",
        "project.visibility.changed",
        "pod.provisioned",
        "pod.started",
        "pod.paused",
        "pod.resumed",
        "pod.terminated",
        "pod.failed",
        "pod.hydrated",
        "pod.reconciled",
        "company.suspended",
        "company.reactivated",
        "company.deleted",
        "company.restored",
        "company.purged",
        "employee.disabled",
        "employee.enabled",
        "employee.soft_deleted",
        "employee.restored",
        "cabinet.soft_deleted",
        "cabinet.restored",
        "cabinet.purged",
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
        ".json",
        ".zip",
    }
)

# Magic prefixes that must never appear in chat uploads (lightweight content policy / AV-lite).
# Not a virus scanner — blocks obvious executables and script entrypoints.
_FORBIDDEN_MAGIC = (
    b"MZ",  # PE / DOS
    b"\x7fELF",  # ELF
    b"\xca\xfe\xba\xbe",  # Mach-O fat / Java class
    b"\xcf\xfa\xed\xfe",  # Mach-O 64
    b"\xce\xfa\xed\xfe",  # Mach-O 32
    b"\0asm",  # WebAssembly
    b"#!",  # shell/script shebang (bypass via .txt/.md)
    b"<?php",  # PHP
    b"<%",  # ASP / JSP-ish
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
    # Office Open XML (xlsx/docx/…) shares ZIP magic — prefer extension when present.
    if head.startswith(b"PK\x03\x04") and filename:
        ext = attachment_extension(filename)
        office = {
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        if ext in office:
            return office[ext]
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
    """Return True if bytes look like an executable or script entrypoint (not a full AV)."""
    if not raw:
        return False
    # Strip UTF-8 BOM so shebang/PHP still match.
    head = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    sample = head[:16]
    return any(sample.startswith(magic) for magic in _FORBIDDEN_MAGIC)


def new_project_id() -> str:
    return f"proj_{uuid.uuid4().hex[:16]}"


_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def _transliterate_slug_source(name: str) -> str:
    lowered = name.strip().lower()
    return lowered.translate(_CYRILLIC_TO_LATIN)


def slugify_name(name: str) -> str:
    source = _transliterate_slug_source(name)
    base = re.sub(r"[^a-z0-9]+", "-", source).strip("-")
    if not base:
        digest = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()[:8]
        base = f"project-{digest}"
    return base[:48]


def workspace_key_for(project_id: str) -> str:
    return project_id.removeprefix("proj_")


def container_ref_for(workspace_key: str) -> str:
    """Canonical workspace container ref (object-store-backed).

    Legacy rows may still store ``local-ws:{key}`` — accept both via
    :func:`parse_container_ref` / :func:`workspace_key_from_container_ref`.
    """
    return f"object-ws:{workspace_key}"


def parse_container_ref(ref: str) -> tuple[str, str]:
    """Return ``(scheme, workspace_key)`` for ``object-ws:`` / ``local-ws:``."""
    raw = (ref or "").strip()
    for scheme in ("object-ws:", "local-ws:"):
        if raw.startswith(scheme):
            key = raw.removeprefix(scheme).strip()
            if not key:
                raise ValueError(f"empty workspace key in container_ref: {ref!r}")
            return scheme.rstrip(":"), key
    raise ValueError(f"unsupported container_ref scheme: {raw[:32]!r}")


def workspace_key_from_container_ref(ref: str) -> str:
    return parse_container_ref(ref)[1]

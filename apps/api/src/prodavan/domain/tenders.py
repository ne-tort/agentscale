"""Tender domain constants shared by metadata, API, importer and MCP.

The tender table itself lives in module JSONB rows (mod_equipment). These
constants are the single source of truth for enum values so a new status does
not silently break intake/validation (previously duplicated in three places).
"""

from __future__ import annotations

from datetime import UTC, datetime

# --- Work status (человеческий workflow) ---------------------------------
WORK_NEW = "new"
WORK_IN_PROGRESS = "in_progress"
WORK_PROCESSED = "processed"
WORK_INVALID = "invalid"
WORK_ON_HOLD = "on_hold"

WORK_STATUSES: tuple[str, ...] = (
    WORK_NEW,
    WORK_IN_PROGRESS,
    WORK_PROCESSED,
    WORK_INVALID,
    WORK_ON_HOLD,
)

WORK_STATUS_LABELS: dict[str, str] = {
    WORK_NEW: "Новый",
    WORK_IN_PROGRESS: "Взят в работу",
    WORK_PROCESSED: "Обработан",
    WORK_INVALID: "Невалиден",
    WORK_ON_HOLD: "На паузе",
}

# --- Lifecycle (вычисляется по сроку/статусу площадки) --------------------
LIFECYCLE_ACTIVE = "active"
LIFECYCLE_INACTIVE = "inactive"

LIFECYCLE_STATUSES: tuple[str, ...] = (LIFECYCLE_ACTIVE, LIFECYCLE_INACTIVE)
LIFECYCLE_STATUS_LABELS: dict[str, str] = {
    LIFECYCLE_ACTIVE: "Активный",
    LIFECYCLE_INACTIVE: "Неактивный",
}

# Legacy `status` values kept for compatibility with old rows/MCP clients.
LEGACY_STATUSES: tuple[str, ...] = (
    "draft",
    "active",
    "submitted",
    "won",
    "lost",
    "cancelled",
    "irrelevant",
)

_LEGACY_TO_WORK: dict[str, str] = {
    "draft": WORK_NEW,
    "active": WORK_IN_PROGRESS,
    "submitted": WORK_IN_PROGRESS,
    "won": WORK_PROCESSED,
    "lost": WORK_PROCESSED,
    "cancelled": WORK_INVALID,
    "irrelevant": WORK_INVALID,
}


def normalize_work_status(value: str | None) -> str:
    """Map legacy status to a work status; unknown values → new."""
    raw = (value or "").strip().lower()
    if raw in WORK_STATUSES:
        return raw
    return _LEGACY_TO_WORK.get(raw, WORK_NEW)


def lifecycle_from_deadline(deadline_at: str | datetime | None, *, now: datetime | None = None) -> str:
    """Active while the submission deadline has not passed."""
    at = now or datetime.now(UTC)
    if not deadline_at:
        return LIFECYCLE_ACTIVE
    dt: datetime | None
    if isinstance(deadline_at, datetime):
        dt = deadline_at
    else:
        raw = str(deadline_at).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return LIFECYCLE_ACTIVE
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return LIFECYCLE_ACTIVE if dt > at else LIFECYCLE_INACTIVE


def work_status_from_legacy(value: str | None) -> str:
    return normalize_work_status(value)

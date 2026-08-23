"""Cabinet Runtime domain types (L06)."""

from __future__ import annotations

from enum import StrEnum


class CabinetStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class StorageKind(StrEnum):
    PHYSICAL = "physical"
    JSON_DOCUMENT = "json_document"


class ColumnType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    BOOL = "bool"
    DATETIME = "datetime"
    JSON = "json"
    ENUM = "enum"
    REF = "ref"
    FILE_REF = "file_ref"


COLUMN_TYPES: frozenset[str] = frozenset(t.value for t in ColumnType)

# Base system tabs seeded on create (default-cabinets.md)
BASE_SYSTEM_TABS: tuple[tuple[str, int, str], ...] = (
    ("Projects", 10, "projects"),
    ("Chat", 20, "chat"),
    ("Context", 30, "context"),
    ("Tables", 40, "tables"),
    ("Tools", 50, "tools"),
)


def schema_name_for_instance(instance_id: str) -> str:
    """Map cab_* id → cab_inst_* PG schema (validated identifier)."""
    suffix = instance_id.removeprefix("cab_")
    if not suffix or len(suffix) > 32 or not suffix.replace("_", "").isalnum():
        raise ValueError("invalid cabinet instance id")
    name = f"cab_inst_{suffix}"
    if not name.replace("_", "").isalnum():
        raise ValueError("invalid schema name")
    return name.lower()

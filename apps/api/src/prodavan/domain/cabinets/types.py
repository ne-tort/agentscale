"""Cabinet Runtime domain types."""

from __future__ import annotations

from enum import StrEnum


class CabinetStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


def schema_name_for_instance(instance_id: str) -> str:
    """Map cab_* id → cab_inst_* PG schema (validated identifier)."""
    suffix = instance_id.removeprefix("cab_")
    if not suffix or len(suffix) > 32 or not suffix.replace("_", "").isalnum():
        raise ValueError("invalid cabinet instance id")
    name = f"cab_inst_{suffix}"
    if not name.replace("_", "").isalnum():
        raise ValueError("invalid schema name")
    return name.lower()

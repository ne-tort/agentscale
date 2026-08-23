"""Official starter bundle catalog entries (L04 / default-cabinets.md)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StarterBundleEntry:
    id: str
    name: str
    description: str
    official: bool = True


STARTER_BUNDLE_CATALOG: tuple[StarterBundleEntry, ...] = (
    StarterBundleEntry(
        id="equipment-procurement",
        name="Equipment procurement",
        description="Seed tables and views for spec/search/KP workflows (Commerce-style procurement).",
    ),
)

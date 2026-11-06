"""Persistence boundary for scoped dynamic-app definition/version resolution."""

from __future__ import annotations

from typing import Protocol, Sequence

from prodavan.domain.dynamic_apps import AppDefinition, AppVersion


class DefinitionVersionRepository(Protocol):
    """Read-only port; implementations must not infer tenant or version ownership."""

    async def list_definitions(self, *, tenant_id: str, app_id: str) -> Sequence[AppDefinition]:
        """Return all rows matching the authenticated tenant/app anchor."""
        ...

    async def list_versions(
        self, *, app_definition_id: str, manifest_version: str
    ) -> Sequence[AppVersion]:
        """Return candidates scoped to one definition and manifest version."""
        ...

    async def list_definition_versions(self, *, app_definition_id: str) -> Sequence[AppVersion]:
        """Return the complete immutable history for consistency checks."""
        ...


__all__ = ["DefinitionVersionRepository"]

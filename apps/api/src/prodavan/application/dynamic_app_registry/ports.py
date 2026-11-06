"""Repository ports for the dynamic app registry application service."""

from __future__ import annotations

from typing import Protocol, Sequence

from prodavan.domain.dynamic_apps import AppDefinition, AppGrant, AppVersion


class DynamicAppRegistryRepository(Protocol):
    async def get_definition(self, *, definition_id: str) -> AppDefinition | None: ...
    async def get_version(self, *, definition_id: str, version_id: str) -> AppVersion | None: ...
    async def list_versions(self, *, definition_id: str) -> Sequence[AppVersion]: ...
    async def append_draft(self, *, definition: AppDefinition, version: AppVersion, actor: str) -> None: ...
    async def transition_and_project(
        self,
        *,
        definition: AppDefinition,
        version: AppVersion,
        status: str,
        current_version_id: str | None,
        expected_current_version_id: str | None,
        expected_artifact_digest: str | None,
        grants: Sequence[AppGrant],
        actor: str,
    ) -> None: ...


class RegistryQuarantinePort(Protocol):
    async def append(self, evidence: object) -> None: ...


__all__ = ["DynamicAppRegistryRepository", "RegistryQuarantinePort"]

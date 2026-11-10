"""Минимальные порты чтения/изменения ModuleSource и его reservation."""

from __future__ import annotations

from typing import Protocol

from .types import (
    ModuleSource,
    ModuleSourceProvisioningReservation,
    ModuleSourceReservationResult,
    ModuleSourceStatus,
)


class ModuleSourceRepositoryPort(Protocol):
    """Read/runtime port; publication consumers need no provisioning methods."""

    async def get_by_id(self, source_id: str) -> ModuleSource | None: ...

    async def get_by_company_plugin(
        self, *, company_id: str, plugin_id: str
    ) -> ModuleSource | None: ...

    async def create(self, source: ModuleSource) -> ModuleSource: ...

    async def set_status(
        self, source_id: str, status: ModuleSourceStatus
    ) -> ModuleSource | None: ...

    async def bind_scope(self, source: ModuleSource) -> ModuleSource: ...

    async def delete(self, source_id: str) -> bool: ...


class ModuleSourceProvisioningRepositoryPort(ModuleSourceRepositoryPort, Protocol):
    """Provisioning-only port implemented by the same SQL repository."""

    async def get_by_provider_repo(
        self, *, provider: str, repo_id: int
    ) -> ModuleSource | None: ...

    async def reserve_provisioning(
        self, reservation: ModuleSourceProvisioningReservation
    ) -> ModuleSourceReservationResult:
        """Commit an atomic INSERT ON CONFLICT DO NOTHING before provider HTTP."""
        ...

    async def retry_provisioning(
        self,
        reservation: ModuleSourceProvisioningReservation,
        *,
        attempt_id: str,
    ) -> ModuleSourceReservationResult:
        """Atomically fence and acquire one idle failed create attempt."""
        ...

    async def release_provisioning_attempt(
        self, reservation_id: str, *, attempt_id: str
    ) -> None:
        """Make a failed create retryable without releasing another worker."""
        ...

    async def complete_provisioning(self, source_id: str) -> ModuleSource | None:
        """Atomically mark source and its reservation ready after protection."""
        ...

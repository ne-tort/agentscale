"""Read-only content boundary for exact immutable M04 app artifacts."""

from __future__ import annotations

from typing import Protocol


class ArtifactContentPort(Protocol):
    """Read bytes by a proof-bound identity; implementations must not write."""

    async def resolve_artifact_uri(
        self, *, tenant_id: str, app_definition_id: str, app_version_id: str,
    ) -> str | None:
        """Resolve URI from the exact persisted version row, never from a caller."""
        ...

    async def read_artifact(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        artifact_uri: str,
        max_bytes: int,
    ) -> bytes | None:
        """Return exact immutable content, or ``None`` when that object is absent.

        Storage errors other than a confirmed missing object must raise so the
        caller leaves its checkpoint unchanged and can retry after recovery.
        The port is an application boundary only; no adapter is bound here.
        """
        ...


__all__ = ["ArtifactContentPort"]
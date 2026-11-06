"""Application adapter connecting M04 scope service to trusted repositories."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from prodavan.application.scope_registry.contracts import (
    PositiveWriteDecision,
    RlsContext,
    ScopeAnchors,
    ScopeAssertion,
    ScopeRegistryEntry,
)
from prodavan.application.scope_registry.service import ScopeRegistryService

from .protocols import TrustedScopeRegistryRepository


class ScopeRegistryServiceAdapter:
    """Stable application-facing facade over ``ScopeRegistryService``.

    Repository methods are intentionally not exposed as a bypass.  All positive
    reads/writes go through the existing service gates, which enforce the typed
    company (tenant), cabinet, and project anchors and defer plugin instances.
    """

    def __init__(self, repository: TrustedScopeRegistryRepository) -> None:
        self._service = ScopeRegistryService(repository)

    async def set_rls_context(self, context: RlsContext) -> None:
        await self._service.set_rls_context(context)

    async def clear_rls_context(self) -> None:
        await self._service.clear_rls_context()

    @asynccontextmanager
    async def rls_context(self, context: RlsContext) -> AsyncIterator[None]:
        """Set transaction-local context and clear it on every exit path."""
        await self.set_rls_context(context)
        try:
            yield
        finally:
            await self.clear_rls_context()

    async def resolve_active(self, anchors: ScopeAnchors) -> ScopeRegistryEntry | None:
        return await self._service.resolve_active(anchors)

    async def authorize_positive_write(
        self,
        *,
        anchors: ScopeAnchors,
        context: RlsContext | None,
        assertion: ScopeAssertion | None = None,
    ) -> PositiveWriteDecision:
        return await self._service.authorize_positive_write(
            anchors=anchors,
            context=context,
            assertion=assertion,
        )

    async def register_active_scope(
        self,
        *,
        entry: ScopeRegistryEntry,
        context: RlsContext | None,
        assertion: ScopeAssertion | None = None,
    ) -> PositiveWriteDecision:
        return await self._service.register_active_scope(
            entry=entry,
            context=context,
            assertion=assertion,
        )


__all__ = ["ScopeRegistryServiceAdapter"]

"""Ports owned by the M04 typed scope application boundary."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    ParentCompatibilityProof,
    QuarantineRecord,
    RlsContext,
    ScopeAnchors,
    ScopeAssertion,
    ScopeRegistryEntry,
)


class ScopeRegistryReader(Protocol):
    async def get_scope(self, anchors: ScopeAnchors) -> ScopeRegistryEntry | None: ...


class ParentCompatibilityPort(Protocol):
    async def prove(self, anchors: ScopeAnchors) -> ParentCompatibilityProof: ...


class QuarantinePort(Protocol):
    async def record(self, record: QuarantineRecord) -> None: ...


class RlsContextPort(Protocol):
    async def set_transaction_context(self, context: RlsContext) -> None: ...

    async def clear_transaction_context(self) -> None: ...


class ScopeRegistryWritePort(Protocol):
    async def put_scope(self, entry: ScopeRegistryEntry) -> ScopeRegistryEntry: ...


class ScopeRegistryPort(
    ScopeRegistryReader,
    ParentCompatibilityPort,
    QuarantinePort,
    RlsContextPort,
    ScopeRegistryWritePort,
    Protocol,
):
    """Combined port used by the service; adapters remain outside this zone."""


class ScopeAssertionPort(Protocol):
    def assert_matches(self, assertion: ScopeAssertion, anchors: ScopeAnchors) -> None: ...

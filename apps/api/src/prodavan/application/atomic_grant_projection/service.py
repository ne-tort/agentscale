"""Fail-closed application service for the exclusive grant projection command."""
from __future__ import annotations

from .contracts import (
    AtomicGrantProjectionCommand,
    AtomicGrantProjectionResult,
    AtomicProjectionDecision,
    AtomicProjectionReason,
)
from .ports import AtomicGrantProjectionRepository


class AtomicGrantProjectionService:
    """Submit exactly one complete command to the repository-owned transaction.

    This service intentionally has no positive-write, quarantine, audit, or
    promotion methods.  A caller must provide the complete command and the
    repository decides the one transaction outcome.
    """

    def __init__(self, repository: AtomicGrantProjectionRepository) -> None:
        self._repository = repository

    async def execute(self, command: AtomicGrantProjectionCommand | None) -> AtomicGrantProjectionResult:
        if command is None:
            raise ValueError("complete atomic grant projection command is required")
        self._validate(command)
        return await self._repository.execute(command)

    @staticmethod
    def _validate(command: AtomicGrantProjectionCommand) -> None:
        if command.decision is AtomicProjectionDecision.ALLOW and command.promotion_evidence is None:
            raise ValueError(AtomicProjectionReason.MISSING_PARTITION_EVIDENCE.value)
        if command.decision is AtomicProjectionDecision.QUARANTINE and command.quarantine_evidence is None:
            raise ValueError(AtomicProjectionReason.MISSING_CONTEXT.value)
        if not command.m04.tenant_id or not command.m05.plugin_instance_id or not command.partition.tenant_id:
            raise ValueError(AtomicProjectionReason.MISSING_CONTEXT.value)


__all__ = ["AtomicGrantProjectionService"]

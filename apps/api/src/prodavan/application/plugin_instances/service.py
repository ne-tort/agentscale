"""Fail-closed M05 plugin instance and runtime fencing service."""

from __future__ import annotations

from .contracts import (
    LeaseCasDecision,
    LeaseCasRequest,
    PluginInstanceDecisionReason,
    PluginInstanceRecord,
    PluginInstanceWriteRequest,
    PositiveWriteDecision,
    WriterCasRequest,
)
from .ports import PluginInstanceQuarantinePort, PluginInstanceRepository


class PluginInstanceFencingService:
    """Authorize plugin writes only against an exact installation snapshot."""

    def __init__(
        self,
        repository: PluginInstanceRepository,
        quarantine: PluginInstanceQuarantinePort | None = None,
    ) -> None:
        self._repository = repository
        self._quarantine = quarantine

    async def authorize_positive_write(self, request: PluginInstanceWriteRequest) -> PositiveWriteDecision:
        record = await self._repository.get_exact(request.identity)
        if record is None:
            return self._deny(PluginInstanceDecisionReason.NOT_FOUND)

        reason = self._check(record, request)
        if reason is not None:
            if reason in {
                PluginInstanceDecisionReason.IDENTITY_MISMATCH,
                PluginInstanceDecisionReason.DEFINITION_MISMATCH,
                PluginInstanceDecisionReason.VERSION_MISMATCH,
                PluginInstanceDecisionReason.TENANT_MISMATCH,
                PluginInstanceDecisionReason.SCOPE_MISMATCH,
                PluginInstanceDecisionReason.DIGEST_MISMATCH,
                PluginInstanceDecisionReason.SUBJECT_MISMATCH,
                PluginInstanceDecisionReason.SESSION_MISMATCH,
                PluginInstanceDecisionReason.RUNTIME_GENERATION_STALE,
                PluginInstanceDecisionReason.WRITER_GENERATION_STALE,
                PluginInstanceDecisionReason.QUARANTINED,
            }:
                await self._record_quarantine(record, reason)
            return self._deny(reason)
        return PositiveWriteDecision(True, PluginInstanceDecisionReason.ALLOWED, record)

    async def acquire_lease(self, request: LeaseCasRequest) -> LeaseCasDecision:
        """Accept a lease CAS only when its returned snapshot is exact."""
        decision = await self._repository.acquire_lease(request)
        if not decision.acquired or decision.record is None:
            return LeaseCasDecision(False, decision.reason)
        reason = self._check_lease_result(decision.record, request)
        if reason is not None:
            await self._record_quarantine(decision.record, reason)
            return LeaseCasDecision(False, PluginInstanceDecisionReason.LEASE_CAS_CONFLICT)
        return decision

    async def advance_writer(self, request: WriterCasRequest) -> PositiveWriteDecision:
        """Advance writer generation only through exact owner/token CAS."""
        decision = await self._repository.advance_writer(request)
        if not decision.changed or decision.record is None:
            return self._deny(decision.reason)
        reason = self._check_writer_result(decision.record, request)
        if reason is not None:
            await self._record_quarantine(decision.record, reason)
            return self._deny(PluginInstanceDecisionReason.WRITER_CAS_CONFLICT)
        return PositiveWriteDecision(True, PluginInstanceDecisionReason.ALLOWED, decision.record)

    @staticmethod
    def _check_lease_result(record: PluginInstanceRecord, request: LeaseCasRequest) -> PluginInstanceDecisionReason | None:
        if record.identity != request.identity:
            return PluginInstanceDecisionReason.IDENTITY_MISMATCH
        if record.subject_id != request.subject_id:
            return PluginInstanceDecisionReason.SUBJECT_MISMATCH
        if record.session_id != request.session_id:
            return PluginInstanceDecisionReason.SESSION_MISMATCH
        if record.fencing != request.expected:
            return PluginInstanceDecisionReason.RUNTIME_GENERATION_STALE
        if record.lease_owner != request.lease_owner:
            return PluginInstanceDecisionReason.LEASE_OWNER_MISMATCH
        if record.lease_id != request.lease_id:
            return PluginInstanceDecisionReason.LEASE_ID_MISMATCH
        if record.lease_until != request.lease_until:
            return PluginInstanceDecisionReason.LEASE_CAS_CONFLICT
        return None

    @staticmethod
    def _check_writer_result(record: PluginInstanceRecord, request: WriterCasRequest) -> PluginInstanceDecisionReason | None:
        if record.identity != request.identity:
            return PluginInstanceDecisionReason.IDENTITY_MISMATCH
        if record.subject_id != request.subject_id:
            return PluginInstanceDecisionReason.SUBJECT_MISMATCH
        if record.session_id != request.session_id:
            return PluginInstanceDecisionReason.SESSION_MISMATCH
        if record.lease_owner != request.lease_owner:
            return PluginInstanceDecisionReason.LEASE_OWNER_MISMATCH
        if record.lease_id != request.lease_id or record.lease_until != request.lease_until:
            return PluginInstanceDecisionReason.LEASE_ID_MISMATCH
        if record.fencing.runtime_generation != request.expected.runtime_generation:
            return PluginInstanceDecisionReason.RUNTIME_GENERATION_STALE
        if record.fencing.writer_generation != request.expected.writer_generation + 1:
            return PluginInstanceDecisionReason.WRITER_GENERATION_STALE
        return None

    async def _record_quarantine(self, record: PluginInstanceRecord, reason: PluginInstanceDecisionReason) -> None:
        if self._quarantine is not None:
            await self._quarantine.record_quarantine(record, reason=reason.value)

    @staticmethod
    def _check(record: PluginInstanceRecord, request: PluginInstanceWriteRequest) -> PluginInstanceDecisionReason | None:
        expected = request.identity
        actual = record.identity
        if actual.plugin_instance_id != expected.plugin_instance_id:
            return PluginInstanceDecisionReason.IDENTITY_MISMATCH
        if actual.definition_id != expected.definition_id:
            return PluginInstanceDecisionReason.DEFINITION_MISMATCH
        if actual.version_id != expected.version_id:
            return PluginInstanceDecisionReason.VERSION_MISMATCH
        if actual.tenant_id != expected.tenant_id:
            return PluginInstanceDecisionReason.TENANT_MISMATCH
        if (actual.scope_kind, actual.scope_id) != (expected.scope_kind, expected.scope_id):
            return PluginInstanceDecisionReason.SCOPE_MISMATCH
        if actual.artifact_digest != expected.artifact_digest:
            return PluginInstanceDecisionReason.DIGEST_MISMATCH
        if record.subject_id != request.subject_id:
            return PluginInstanceDecisionReason.SUBJECT_MISMATCH
        if record.session_id != request.session_id:
            return PluginInstanceDecisionReason.SESSION_MISMATCH
        if record.state.value == "archived":
            return PluginInstanceDecisionReason.ARCHIVED
        if record.state.value == "quarantined":
            return PluginInstanceDecisionReason.QUARANTINED
        if record.fencing.runtime_generation != request.fencing.runtime_generation:
            return PluginInstanceDecisionReason.RUNTIME_GENERATION_STALE
        if record.fencing.writer_generation != request.fencing.writer_generation:
            return PluginInstanceDecisionReason.WRITER_GENERATION_STALE
        if not record.lease_id or not record.lease_owner or record.lease_until is None:
            return PluginInstanceDecisionReason.LEASE_MISSING
        if record.lease_until <= request.now:
            return PluginInstanceDecisionReason.LEASE_EXPIRED
        if record.lease_owner != request.lease_owner:
            return PluginInstanceDecisionReason.LEASE_OWNER_MISMATCH
        if record.lease_id != request.lease_id:
            return PluginInstanceDecisionReason.LEASE_ID_MISMATCH
        return None

    @staticmethod
    def _deny(reason: PluginInstanceDecisionReason) -> PositiveWriteDecision:
        return PositiveWriteDecision(False, reason)


__all__ = ["PluginInstanceFencingService"]

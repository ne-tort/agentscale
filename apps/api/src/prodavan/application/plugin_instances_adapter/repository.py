"""Trusted M05 repository adapter over runtime-instance rows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from prodavan.application.plugin_instances import (
    FencingToken,
    LeaseCasDecision,
    LeaseCasRequest,
    OwnerCasDecision,
    PluginInstanceDecisionReason,
    PluginInstanceIdentity,
    PluginInstanceRecord,
    PluginInstanceState,
    WriterCasRequest,
)

from .ports import (
    PluginInstanceAdapterQuarantinePort,
    RuntimeInstanceCasPort,
    RuntimeInstanceReadPort,
    RuntimeRow,
)


_MISSING = object()


def _value(row: RuntimeRow, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, _MISSING)
    return getattr(row, name, _MISSING)


def _required(row: RuntimeRow, name: str) -> Any:
    value = _value(row, name)
    if value is _MISSING or value is None:
        raise ValueError(f"runtime row field {name} is missing")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"runtime row field {name} is blank")
    return value


def _required_text(row: RuntimeRow, name: str) -> str:
    value = _required(row, name)
    if not isinstance(value, str):
        raise ValueError(f"runtime row field {name} must be text")
    return value


def _required_alias(row: RuntimeRow, name: str, alias: str) -> Any:
    value = _value(row, name)
    alternate = _value(row, alias)
    if value is _MISSING:
        return _required(row, alias)
    if alternate is not _MISSING and alternate != value:
        raise ValueError(f"runtime row fields {name} and {alias} disagree")
    return _required(row, name)


def _required_text_alias(row: RuntimeRow, name: str, alias: str) -> str:
    value = _required_alias(row, name, alias)
    if not isinstance(value, str):
        raise ValueError(f"runtime row fields {name}/{alias} must be text")
    return value


def _state(value: Any) -> PluginInstanceState:
    if isinstance(value, PluginInstanceState):
        return value
    return PluginInstanceState(str(value))


def _map_record(row: RuntimeRow) -> PluginInstanceRecord:
    """Map only a complete row; constructor validation is part of the gate."""
    return PluginInstanceRecord(
        identity=PluginInstanceIdentity(
            plugin_instance_id=_required_text(row, "plugin_instance_id"),
            definition_id=_required_text_alias(row, "definition_id", "app_definition_id"),
            version_id=_required_text_alias(row, "version_id", "app_version_id"),
            tenant_id=_required_text(row, "tenant_id"),
            scope_kind=_required_text(row, "scope_kind"),
            scope_id=_required_text(row, "scope_id"),
            artifact_digest=_required_text(row, "artifact_digest"),
        ),
        subject_id=_required_text(row, "subject_id"),
        session_id=_required_text(row, "session_id"),
        state=_state(_required(row, "state")),
        fencing=FencingToken(
            int(_required(row, "runtime_generation")),
            int(_required(row, "writer_generation")),
        ),
        lease_owner=_value(row, "lease_owner") if _value(row, "lease_owner") is not _MISSING else None,
        lease_id=_value(row, "lease_id") if _value(row, "lease_id") is not _MISSING else None,
        lease_until=_value(row, "lease_until") if _value(row, "lease_until") is not _MISSING else None,
    )


class TrustedPluginInstanceRepository:
    """Translate runtime rows into M05 records and keep malformed rows deny-only."""

    def __init__(
        self,
        reader: RuntimeInstanceReadPort,
        cas: RuntimeInstanceCasPort,
        quarantine: PluginInstanceAdapterQuarantinePort | None = None,
    ) -> None:
        self._reader = reader
        self._cas = cas
        self._quarantine = quarantine

    async def _map_or_quarantine(self, row: RuntimeRow, *, reason: str) -> PluginInstanceRecord | None:
        try:
            return _map_record(row)
        except (AttributeError, TypeError, ValueError, KeyError, OverflowError) as exc:
            if self._quarantine is not None:
                await self._quarantine.quarantine(row, reason=f"{reason}:{exc}")
            return None

    async def get_exact(self, identity: PluginInstanceIdentity) -> PluginInstanceRecord | None:
        row = await self._reader.read_exact(identity)
        if row is None:
            return None
        record = await self._map_or_quarantine(row, reason="incomplete_runtime_instance")
        if record is None:
            return None
        if record.identity != identity:
            if self._quarantine is not None:
                await self._quarantine.quarantine(row, reason="runtime_instance_identity_mismatch")
            return None
        return record

    async def acquire_lease(self, request: LeaseCasRequest) -> LeaseCasDecision:
        row = await self._cas.acquire_lease(request)
        if row is None:
            return LeaseCasDecision(False, PluginInstanceDecisionReason.LEASE_CAS_CONFLICT)
        record = await self._map_or_quarantine(row, reason="incomplete_lease_cas_result")
        if record is None:
            return LeaseCasDecision(False, PluginInstanceDecisionReason.LEASE_CAS_CONFLICT)
        if (
            record.identity.plugin_instance_id != request.plugin_instance_id
            or record.lease_owner != request.owner_id
            or record.lease_id != request.lease_id
            or record.fencing != request.expected
            or record.lease_until is None
            or record.lease_until != request.lease_until
        ):
            if self._quarantine is not None:
                await self._quarantine.quarantine(row, reason="lease_cas_result_mismatch")
            return LeaseCasDecision(False, PluginInstanceDecisionReason.LEASE_CAS_CONFLICT)
        return LeaseCasDecision(True, PluginInstanceDecisionReason.ALLOWED, record)

    async def advance_writer(self, request: WriterCasRequest) -> OwnerCasDecision:
        row = await self._cas.advance_writer(request)
        if row is None:
            return OwnerCasDecision(False, PluginInstanceDecisionReason.WRITER_CAS_CONFLICT)
        record = await self._map_or_quarantine(row, reason="incomplete_writer_cas_result")
        if record is None:
            return OwnerCasDecision(False, PluginInstanceDecisionReason.WRITER_CAS_CONFLICT)
        if (
            record.identity.plugin_instance_id != request.plugin_instance_id
            or record.lease_owner != request.owner_id
            or record.fencing.runtime_generation != request.expected.runtime_generation
            or record.fencing.writer_generation != request.expected.writer_generation + 1
        ):
            if self._quarantine is not None:
                await self._quarantine.quarantine(row, reason="writer_cas_result_mismatch")
            return OwnerCasDecision(False, PluginInstanceDecisionReason.WRITER_CAS_CONFLICT)
        return OwnerCasDecision(True, PluginInstanceDecisionReason.ALLOWED, record)


__all__ = ["TrustedPluginInstanceRepository"]

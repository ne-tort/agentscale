"""Fail-closed, persistence-independent plugin-instance registry adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import (
    PluginInstanceRegistryIdentity,
    PluginInstanceRegistryOutcome,
    PluginInstanceRegistryQuery,
    PluginInstanceRegistryReason,
    PluginInstanceRegistryRecord,
    PluginInstanceRegistryResolution,
    PluginInstanceRegistryState,
)
from .ports import PluginInstanceRegistryQuarantinePort, PluginInstanceRegistrySourcePort, RegistryRow

_MISSING = object()


def _value(row: RegistryRow, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, _MISSING)
    return getattr(row, name, _MISSING)


def _required(row: RegistryRow, name: str) -> Any:
    value = _value(row, name)
    if value is _MISSING or value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"registry row field {name} is missing or blank")
    return value


def _text(row: RegistryRow, name: str) -> str:
    value = _required(row, name)
    if not isinstance(value, str):
        raise TypeError(f"registry row field {name} must be text")
    return value


def _alias_text(row: RegistryRow, name: str, alias: str) -> str:
    value = _value(row, name)
    alternate = _value(row, alias)
    if value is _MISSING:
        return _text(row, alias)
    if alternate is not _MISSING and alternate != value:
        raise ValueError(f"registry row fields {name} and {alias} disagree")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"registry row field {name} must be text")
    return value


def _map_record(row: RegistryRow) -> PluginInstanceRegistryRecord:
    state = _required(row, "state")
    if not isinstance(state, PluginInstanceRegistryState):
        state = PluginInstanceRegistryState(str(state))
    return PluginInstanceRegistryRecord(
        identity=PluginInstanceRegistryIdentity(
            plugin_instance_id=_text(row, "plugin_instance_id"),
            definition_id=_alias_text(row, "definition_id", "app_definition_id"),
            version_id=_alias_text(row, "version_id", "app_version_id"),
            tenant_id=_text(row, "tenant_id"),
            scope_kind=_text(row, "scope_kind"),
            scope_id=_text(row, "scope_id"),
            artifact_digest=_text(row, "artifact_digest"),
        ),
        subject_id=_text(row, "subject_id"),
        session_id=_text(row, "session_id"),
        state=state,
        runtime_generation=int(_required(row, "runtime_generation")),
        writer_generation=int(_required(row, "writer_generation")),
    )


class PluginInstanceRegistryService:
    """Resolve an exact registry snapshot without any positive write capability."""

    def __init__(
        self,
        source: PluginInstanceRegistrySourcePort,
        quarantine: PluginInstanceRegistryQuarantinePort | None = None,
    ) -> None:
        self._source = source
        self._quarantine = quarantine

    async def resolve(self, query: PluginInstanceRegistryQuery) -> PluginInstanceRegistryResolution:
        row = await self._source.read_exact(query.identity)
        if row is None:
            return self._negative(PluginInstanceRegistryOutcome.MISSING, PluginInstanceRegistryReason.NOT_FOUND)
        try:
            record = _map_record(row)
        except (AttributeError, TypeError, ValueError, KeyError, OverflowError) as exc:
            await self._quarantine_row(row, f"{PluginInstanceRegistryReason.INVALID_SNAPSHOT.value}:{exc}")
            return self._negative(PluginInstanceRegistryOutcome.QUARANTINED, PluginInstanceRegistryReason.INVALID_SNAPSHOT)
        if record.identity != query.identity:
            await self._quarantine_row(row, PluginInstanceRegistryReason.IDENTITY_MISMATCH.value)
            return self._negative(PluginInstanceRegistryOutcome.QUARANTINED, PluginInstanceRegistryReason.IDENTITY_MISMATCH)
        reason = self._context_mismatch(record, query)
        if reason is not None:
            await self._quarantine_row(row, reason.value)
            return self._negative(PluginInstanceRegistryOutcome.QUARANTINED, reason)
        if record.state is PluginInstanceRegistryState.ARCHIVED:
            return self._negative(PluginInstanceRegistryOutcome.ARCHIVED, PluginInstanceRegistryReason.ARCHIVED)
        if record.state is PluginInstanceRegistryState.QUARANTINED:
            return self._negative(PluginInstanceRegistryOutcome.QUARANTINED, PluginInstanceRegistryReason.QUARANTINED)
        return PluginInstanceRegistryResolution(PluginInstanceRegistryOutcome.RESOLVED, record=record)

    async def _quarantine_row(self, row: RegistryRow, reason: str) -> None:
        if self._quarantine is not None:
            await self._quarantine.quarantine(row, reason=reason)

    @staticmethod
    def _context_mismatch(record: PluginInstanceRegistryRecord, query: PluginInstanceRegistryQuery) -> PluginInstanceRegistryReason | None:
        if query.subject_id is not None and record.subject_id != query.subject_id:
            return PluginInstanceRegistryReason.SUBJECT_MISMATCH
        if query.session_id is not None and record.session_id != query.session_id:
            return PluginInstanceRegistryReason.SESSION_MISMATCH
        if query.runtime_generation is not None and record.runtime_generation != query.runtime_generation:
            return PluginInstanceRegistryReason.RUNTIME_GENERATION_MISMATCH
        if query.writer_generation is not None and record.writer_generation != query.writer_generation:
            return PluginInstanceRegistryReason.WRITER_GENERATION_MISMATCH
        return None

    @staticmethod
    def _negative(outcome: PluginInstanceRegistryOutcome, reason: PluginInstanceRegistryReason) -> PluginInstanceRegistryResolution:
        return PluginInstanceRegistryResolution(outcome=outcome, reason=reason)


__all__ = ["PluginInstanceRegistryService"]

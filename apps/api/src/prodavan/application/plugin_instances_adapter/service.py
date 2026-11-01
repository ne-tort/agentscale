"""M05 service adapter exposing trusted repository operations."""

from __future__ import annotations

from prodavan.application.plugin_instances import (
    LeaseCasDecision,
    LeaseCasRequest,
    PluginInstanceFencingService,
    PluginInstanceRecord,
    PluginInstanceWriteRequest,
    PositiveWriteDecision,
    WriterCasRequest,
)

from .ports import PluginInstanceAdapterQuarantinePort, RuntimeInstanceCasPort, RuntimeInstanceReadPort, RuntimeRow
from .repository import TrustedPluginInstanceRepository


class _RecordQuarantine:
    def __init__(self, sink: PluginInstanceAdapterQuarantinePort) -> None:
        self._sink = sink

    async def record_quarantine(self, record: PluginInstanceRecord, *, reason: str) -> None:
        await self._sink.quarantine(record, reason=reason)


class TrustedPluginInstanceService:
    """Use the M05 application service with a trusted runtime-row repository."""

    def __init__(
        self,
        reader: RuntimeInstanceReadPort,
        cas: RuntimeInstanceCasPort,
        quarantine: PluginInstanceAdapterQuarantinePort | None = None,
    ) -> None:
        repository = TrustedPluginInstanceRepository(reader, cas, quarantine)
        service_quarantine = _RecordQuarantine(quarantine) if quarantine is not None else None
        self._service = PluginInstanceFencingService(repository, service_quarantine)

    async def authorize_positive_write(self, request: PluginInstanceWriteRequest) -> PositiveWriteDecision:
        return await self._service.authorize_positive_write(request)

    async def acquire_lease(self, request: LeaseCasRequest) -> LeaseCasDecision:
        return await self._service.acquire_lease(request)

    async def advance_writer(self, request: WriterCasRequest) -> PositiveWriteDecision:
        return await self._service.advance_writer(request)


__all__ = ["TrustedPluginInstanceService", "RuntimeRow"]

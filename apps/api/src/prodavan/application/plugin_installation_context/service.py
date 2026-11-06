"""Fail-closed cross-domain installation admission service."""

from __future__ import annotations

from .contracts import (
    InstallationAdmissionDecision,
    InstallationAdmissionReason,
    InstallationAdmissionRequest,
    M05CasContext,
    M06GrantHandoff,
)
from .ports import (
    InstallationContextConsistencyPort,
    InstallationContextReadPort,
    M05RuntimeAdmissionPort,
    M06GrantAdmissionPort,
)


class PluginInstallationAdmissionService:
    """Resolve anchors once, admit M05 CAS, then hand off to M06."""

    def __init__(
        self,
        context: InstallationContextReadPort,
        consistency: InstallationContextConsistencyPort,
        runtime: M05RuntimeAdmissionPort,
        grants: M06GrantAdmissionPort | None = None,
    ) -> None:
        self._context = context
        self._consistency = consistency
        self._runtime = runtime
        self._grants = grants

    async def admit(self, request: InstallationAdmissionRequest) -> InstallationAdmissionDecision:
        supplied = request.context
        if not self._assertions_match(request):
            return self._deny(InstallationAdmissionReason.ANCHOR_MISMATCH)
        resolved = await self._context.resolve(
            plugin_instance_id=supplied.plugin_instance_id,
            session_id=supplied.session_id,
        )
        if resolved is None:
            return self._deny(InstallationAdmissionReason.INSTALLATION_NOT_FOUND)
        if resolved != supplied:
            return self._deny(InstallationAdmissionReason.ANCHOR_MISMATCH)
        if not await self._consistency.is_active(resolved):
            return self._deny(InstallationAdmissionReason.INSTALLATION_INACTIVE)
        m05 = M05CasContext(resolved)
        if not await self._runtime.admit(m05):
            return self._deny(InstallationAdmissionReason.RUNTIME_FENCE_DENIED)
        m06 = M06GrantHandoff(resolved, runtime_admitted=True)
        if self._grants is not None:
            await self._grants.handoff(m06)
        return InstallationAdmissionDecision(True, InstallationAdmissionReason.ALLOWED, m05=m05, m06=m06)

    @staticmethod
    def _assertions_match(request: InstallationAdmissionRequest) -> bool:
        context = request.context
        assertions = (
            (request.asserted_tenant_id, context.tenant_id),
            (request.asserted_cabinet_id, context.cabinet_id),
            (request.asserted_project_id, context.project_id),
            (request.asserted_plugin_instance_id, context.plugin_instance_id),
        )
        return all(asserted is None or asserted == actual for asserted, actual in assertions)

    @staticmethod
    def _deny(reason: InstallationAdmissionReason) -> InstallationAdmissionDecision:
        return InstallationAdmissionDecision(False, reason)


__all__ = ["PluginInstallationAdmissionService"]

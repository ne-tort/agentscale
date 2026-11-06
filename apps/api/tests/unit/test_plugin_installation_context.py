from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.plugin_installation_context import (
    InstallationAdmissionReason,
    InstallationAdmissionRequest,
    M05CasContext,
    M06GrantHandoff,
    PluginInstallationAdmissionService,
    PluginInstallationContext,
)


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def context(**overrides: object) -> PluginInstallationContext:
    values: dict[str, object] = {
        "tenant_id": "tenant-1",
        "cabinet_id": "cabinet-1",
        "project_id": "project-1",
        "scope_kind": "project",
        "scope_id": "project-1",
        "plugin_instance_id": "instance-1",
        "definition_id": "definition-1",
        "version_id": "version-1",
        "artifact_digest": "sha256:" + "a" * 64,
        "subject_id": "subject-1",
        "session_id": "session-1",
        "runtime_generation": 4,
        "writer_generation": 9,
        "lease_owner": "worker-1",
        "lease_id": "lease-1",
        "observed_at": NOW,
    }
    values.update(overrides)
    return PluginInstallationContext(**values)


class ContextReader:
    def __init__(self, resolved: PluginInstallationContext | None) -> None:
        self.resolved = resolved

    async def resolve(self, *, plugin_instance_id: str, session_id: str) -> PluginInstallationContext | None:
        return self.resolved


class Consistency:
    def __init__(self, active: bool = True) -> None:
        self.active = active

    async def is_active(self, value: PluginInstallationContext) -> bool:
        return self.active


class Runtime:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.seen: list[M05CasContext] = []

    async def admit(self, value: M05CasContext) -> bool:
        self.seen.append(value)
        return self.allowed


class Grants:
    def __init__(self) -> None:
        self.seen: list[M06GrantHandoff] = []

    async def handoff(self, value: M06GrantHandoff) -> None:
        self.seen.append(value)


@pytest.mark.asyncio
async def test_admission_passes_exact_context_to_m05_and_m06() -> None:
    installation = context()
    runtime = Runtime()
    grants = Grants()
    service = PluginInstallationAdmissionService(
        ContextReader(installation), Consistency(), runtime, grants
    )

    result = await service.admit(InstallationAdmissionRequest(
        installation,
        asserted_tenant_id="tenant-1",
        asserted_project_id="project-1",
        asserted_plugin_instance_id="instance-1",
    ))

    assert result.admitted
    assert result.reason is InstallationAdmissionReason.ALLOWED
    assert result.m05 is not None and result.m05.installation is installation
    assert result.m06 is not None and result.m06.installation is installation
    assert runtime.seen == [result.m05]
    assert grants.seen == [result.m06]


@pytest.mark.asyncio
async def test_client_anchor_mismatch_denies_before_resolution() -> None:
    runtime = Runtime()
    service = PluginInstallationAdmissionService(ContextReader(context()), Consistency(), runtime)

    result = await service.admit(InstallationAdmissionRequest(context(), asserted_tenant_id="other"))

    assert not result.admitted
    assert result.reason is InstallationAdmissionReason.ANCHOR_MISMATCH
    assert runtime.seen == []


@pytest.mark.asyncio
async def test_resolved_anchor_mismatch_denies_without_handoff() -> None:
    supplied = context()
    resolved = context(version_id="version-2")
    runtime = Runtime()
    service = PluginInstallationAdmissionService(ContextReader(resolved), Consistency(), runtime)

    result = await service.admit(InstallationAdmissionRequest(supplied))

    assert not result.admitted
    assert result.reason is InstallationAdmissionReason.ANCHOR_MISMATCH
    assert runtime.seen == []


@pytest.mark.asyncio
async def test_inactive_or_stale_runtime_denies_without_m06() -> None:
    installation = context()
    grants = Grants()
    inactive = PluginInstallationAdmissionService(
        ContextReader(installation), Consistency(False), Runtime(), grants
    )
    result = await inactive.admit(InstallationAdmissionRequest(installation))
    assert result.reason is InstallationAdmissionReason.INSTALLATION_INACTIVE
    assert grants.seen == []

    fenced = PluginInstallationAdmissionService(
        ContextReader(installation), Consistency(), Runtime(False), grants
    )
    result = await fenced.admit(InstallationAdmissionRequest(installation))
    assert result.reason is InstallationAdmissionReason.RUNTIME_FENCE_DENIED
    assert grants.seen == []


def test_m06_handoff_requires_m05_admission() -> None:
    with pytest.raises(ValueError, match="successful M05"):
        M06GrantHandoff(context(), runtime_admitted=False)


def test_context_rejects_inconsistent_typed_anchors() -> None:
    with pytest.raises(ValueError, match="tenant scope"):
        context(scope_kind="tenant", scope_id="tenant-1", cabinet_id="cabinet-1")
    with pytest.raises(ValueError, match="cabinet scope"):
        context(scope_kind="cabinet", scope_id="cabinet-1", project_id="project-1")
    with pytest.raises(ValueError, match="project scope"):
        context(scope_id="cabinet-1")
    with pytest.raises(ValueError, match="plugin_instance scope"):
        context(scope_kind="plugin_instance", scope_id="project-1")
    with pytest.raises(ValueError, match="project anchor"):
        context(
            scope_kind="plugin_instance",
            scope_id="instance-1",
            cabinet_id=None,
            project_id="project-1",
        )


@pytest.mark.asyncio
async def test_active_check_precedes_m05_and_m05_precedes_m06() -> None:
    installation = context()
    events: list[str] = []

    class OrderedConsistency:
        async def is_active(self, value: PluginInstallationContext) -> bool:
            events.append("active")
            return True

    class OrderedRuntime(Runtime):
        async def admit(self, value: M05CasContext) -> bool:
            events.append("m05")
            return await super().admit(value)

    class OrderedGrants(Grants):
        async def handoff(self, value: M06GrantHandoff) -> None:
            events.append("m06")
            await super().handoff(value)

    service = PluginInstallationAdmissionService(
        ContextReader(installation), OrderedConsistency(), OrderedRuntime(), OrderedGrants()
    )
    result = await service.admit(InstallationAdmissionRequest(installation))

    assert result.admitted
    assert events == ["active", "m05", "m06"]

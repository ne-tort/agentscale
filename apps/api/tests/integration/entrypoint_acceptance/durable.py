"""Production SQL repositories, claims mint, distributed update lock and revocation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from prodavan.application.plugins.adapters.identity_adapter import IdentityMintAdapter
from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressMode,
    EgressPolicy,
    GrantStatus,
    MintRequest,
    PluginInstanceRecord,
    PluginInstanceStatus,
    QuotaSet,
)
from prodavan.application.plugins.wiring.adapters import WIRED_CAPABILITIES
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
)
from prodavan.infrastructure.persistence.models.plugin_platform import (
    ModuleSourceRow,
    PluginFeatureFlagRow,
)
from prodavan.infrastructure.persistence.models.projects import (
    ProjectEmployeeAssignmentRow,
    ProjectRow,
)
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    SqlCapabilityGrantRepository,
)
from prodavan.infrastructure.persistence.repositories.plugins.instances import (
    SqlPluginInstanceRepository,
)
from prodavan.infrastructure.persistence.repositories.plugins.revocation import (
    DbGrantRevocation,
)
from prodavan.infrastructure.persistence.repositories.plugins.update_lock import (
    SqlPluginUpdateLock,
)

from .artifact import VerifiedTestArtifact
from .config import AcceptanceConfig


@dataclass(frozen=True, slots=True)
class DurableState:
    grant: CapabilityGrantRecord
    foreign_grant: CapabilityGrantRecord
    token: str
    foreign_token: str


def _instance(
    *,
    config: AcceptanceConfig,
    artifact: VerifiedTestArtifact,
    company_id: str,
    instance_id: str,
    bucket: str,
    now: datetime,
) -> PluginInstanceRecord:
    return PluginInstanceRecord(
        plugin_instance_id=instance_id,
        company_id=company_id,
        plugin_id=artifact.record.plugin_id,
        manifest_version=artifact.record.version,
        artifact_digest=artifact.record.digest,
        status=PluginInstanceStatus.ACTIVE,
        tenant_db_name=f"tenant_{company_id}",
        bucket_name=bucket,
        created_at=now,
        updated_at=now,
    )


def _grant(
    *,
    config: AcceptanceConfig,
    instance: PluginInstanceRecord,
    grant_id: str,
    now: datetime,
) -> CapabilityGrantRecord:
    return CapabilityGrantRecord(
        grant_id=grant_id,
        company_id=instance.company_id,
        plugin_id=instance.plugin_id,
        plugin_instance_id=instance.plugin_instance_id,
        manifest_version=instance.manifest_version,
        artifact_digest=instance.artifact_digest,
        capabilities=WIRED_CAPABILITIES,
        egress_policy=EgressPolicy(
            mode=EgressMode.WEBHOOKS_ONLY,
            allowed_hosts=("egress.acceptance.test",),
            allowed_paths=("/v1/egress",),
        ),
        quotas=QuotaSet(),
        status=GrantStatus.ACTIVE,
        created_at=now,
    )


async def seed_durable_state(
    *,
    config: AcceptanceConfig,
    artifact: VerifiedTestArtifact,
    session_factory: Any,
    bucket: str,
    foreign_bucket: str,
    claims_secret: str,
) -> DurableState:
    if set(artifact.record.manifest.get("capabilities", ())) != set(WIRED_CAPABILITIES):
        raise RuntimeError("verified test artifact must declare exactly all nine real capabilities")
    instances = SqlPluginInstanceRepository(session_factory=session_factory)
    grants = SqlCapabilityGrantRepository(session_factory=session_factory)
    now = datetime.now(UTC)
    primary = await instances.create(instance=_instance(
        config=config,
        artifact=artifact,
        company_id=config.company_id,
        instance_id=config.plugin_instance_id,
        bucket=bucket,
        now=now,
    ))
    foreign = await instances.create(instance=_instance(
        config=config,
        artifact=artifact,
        company_id=config.foreign_company_id,
        instance_id=config.foreign_plugin_instance_id,
        bucket=foreign_bucket,
        now=now,
    ))
    grant = await grants.create(grant=_grant(
        config=config, instance=primary, grant_id=config.grant_id, now=now
    ))
    foreign_grant = await grants.create(grant=_grant(
        config=config, instance=foreign, grant_id=config.foreign_grant_id, now=now
    ))
    mint = IdentityMintAdapter(secret=claims_secret)
    primary_issued = await mint.mint(request=MintRequest(
        company_id=grant.company_id,
        plugin_id=grant.plugin_id,
        plugin_instance_id=grant.plugin_instance_id,
        manifest_version=grant.manifest_version,
        artifact_digest=grant.artifact_digest,
        grant_id=grant.grant_id,
        scopes=grant.capabilities,
        quota_ref=f"{grant.company_id}/plan_default",
        project_id=config.project_id,
        ttl_seconds=300,
    ))
    foreign_issued = await mint.mint(request=MintRequest(
        company_id=foreign_grant.company_id,
        plugin_id=foreign_grant.plugin_id,
        plugin_instance_id=foreign_grant.plugin_instance_id,
        manifest_version=foreign_grant.manifest_version,
        artifact_digest=foreign_grant.artifact_digest,
        grant_id=foreign_grant.grant_id,
        scopes=foreign_grant.capabilities,
        quota_ref=f"{foreign_grant.company_id}/plan_default",
        project_id=config.project_id,
        ttl_seconds=300,
    ))
    return DurableState(
        grant=grant,
        foreign_grant=foreign_grant,
        token=primary_issued.token,
        foreign_token=foreign_issued.token,
    )


@dataclass(frozen=True, slots=True)
class BootstrapIdentity:
    employee_id: str
    keycloak_sub: str
    cabinet_id: str
    project_id: str


async def seed_bootstrap_identity(
    *,
    config: AcceptanceConfig,
    session_factory: Any,
) -> BootstrapIdentity:
    """Seed real parent/membership rows, never a fabricated InstallationScope."""
    employee_id = "emp_entrypoint"
    keycloak_sub = "entrypoint-employee"
    async with session_factory() as session:
        session.add(CompanyRow(
            id=config.company_id,
            name="Entrypoint Acceptance Company",
            login_slug="entrypoint-company",
            status="active",
        ))
        session.add(CompanyRow(
            id=config.foreign_company_id,
            name="Entrypoint Foreign Company",
            login_slug="entrypoint-foreign-company",
            status="active",
        ))
        session.add(EmployeeRow(
            id=employee_id,
            keycloak_sub=keycloak_sub,
            login="employee@entrypoint.local",
            email="employee@entrypoint.test",
            status="active",
        ))
        session.add(MembershipRow(
            id="mem_entrypoint",
            company_id=config.company_id,
            employee_id=employee_id,
            role="member",
        ))
        session.add(CabinetInstanceRow(
            id=config.cabinet_id,
            name="Entrypoint Cabinet",
            schema_name="entrypoint_cabinet",
            owner_employee_id=employee_id,
            company_id=config.company_id,
            owner_company_id=config.company_id,
            status="active",
        ))
        session.add(CabinetCompanyGrantRow(
            id="ccg_entrypoint",
            cabinet_id=config.cabinet_id,
            company_id=config.company_id,
            status="active",
        ))
        session.add(CabinetEmployeeAssignmentRow(
            id="cea_entrypoint",
            cabinet_id=config.cabinet_id,
            employee_id=employee_id,
            role="operator",
            status="active",
        ))
        session.add(ProjectRow(
            id=config.project_id,
            company_id=config.company_id,
            cabinet_id=config.cabinet_id,
            owner_employee_id=employee_id,
            name="Entrypoint Project",
            slug="entrypoint-project",
            status="active",
            workspace_key="entrypoint-workspace",
            container_ref="entrypoint-container",
        ))
        session.add(ProjectEmployeeAssignmentRow(
            id="pea_entrypoint",
            project_id=config.project_id,
            cabinet_id=config.cabinet_id,
            employee_id=employee_id,
            status="active",
        ))
        session.add(PluginFeatureFlagRow(
            company_id=config.company_id,
            enabled_modules=[config.plugin_id],
        ))
        session.add(ModuleSourceRow(
            id=config.source_id,
            company_id=config.company_id,
            plugin_id=config.plugin_id,
            cabinet_id=config.cabinet_id,
            project_id=config.project_id,
            provider="github_app",
            repo_id=91009009,
            full_name=config.source_repository,
            private=True,
            default_branch="main",
            installation_credential_ref="secret://entrypoint/github-app",
            trusted_workflow_ref=(
                "disposable/entrypoint/.github/workflows/sign.yml@" + config.commit_sha
            ),
            status="ready",
        ))
        await session.commit()
    return BootstrapIdentity(
        employee_id=employee_id,
        keycloak_sub=keycloak_sub,
        cabinet_id=config.cabinet_id,
        project_id=config.project_id,
    )


async def revoke_primary(
    *,
    config: AcceptanceConfig,
    artifact: VerifiedTestArtifact,
    state: DurableState,
    session_factory: Any,
) -> None:
    """Serialize revoke through the real PostgreSQL owner-leased update lock."""
    lock = SqlPluginUpdateLock(session_factory=session_factory)
    lease = await lock.acquire(
        company_id=state.grant.company_id,
        plugin_id=state.grant.plugin_id,
        plugin_instance_id=state.grant.plugin_instance_id,
        expected_manifest_version=artifact.record.version,
        expected_artifact_digest=artifact.record.digest,
    )
    assert lease is not None, "primary revoke update lock must be free"
    try:
        revocation = DbGrantRevocation(
            SqlCapabilityGrantRepository(session_factory=session_factory)
        )
        assert await revocation.revoke(state.grant.grant_id, reason="entrypoint acceptance revoke")
        updated = await SqlPluginInstanceRepository(session_factory=session_factory).update_status(
            plugin_instance_id=state.grant.plugin_instance_id,
            status=PluginInstanceStatus.REVOKED,
        )
        assert updated.status is PluginInstanceStatus.REVOKED
        assert not await revocation.is_active(state.grant.grant_id)
    finally:
        await lock.release(lease)

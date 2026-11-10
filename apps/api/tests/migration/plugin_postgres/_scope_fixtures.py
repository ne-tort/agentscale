"""Real ORM seeds for source-backed installation-scope/bootstrap acceptance."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord
from prodavan.domain.plugins.helpers import plugin_instance_id_for
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow, MembershipRow
from prodavan.infrastructure.persistence.models.plugin_platform import (
    CapabilityGrantRow,
    ModuleSourceRow,
    PluginFeatureFlagRow,
    PluginInstanceRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow

from ._fixtures import PLUGIN_ID, SOURCE_ID, make_record, store_for

COMPANY_ID = "co_scope"
CABINET_ID = "cab_scope"
PROJECT_ID = "prj_scope"
EMPLOYEE_ID = "emp_scope"


@dataclass(frozen=True, slots=True)
class ScopeSeed:
    employee: EmployeeRow
    plugin_instance_id: str
    record: PluginArtifactRecord


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def bootstrap_manifest() -> dict[str, object]:
    return {
        "plugin_id": PLUGIN_ID,
        "version": "1.0.0",
        "protocol": "plugin-rpc/1",
        "capabilities": ["equipment.read"],
        "frontend": {"entry": "index.js", "base_path": "/"},
        "public_pages": ["/"],
        "rpc_operations": [],
    }


async def seed_ready_bootstrap(engine: AsyncEngine) -> ScopeSeed:
    factory = session_factory(engine)
    instance_id = plugin_instance_id_for(COMPANY_ID, PLUGIN_ID)
    employee = EmployeeRow(
        id=EMPLOYEE_ID,
        keycloak_sub="scope-employee",
        login="employee@scope.local",
        email="employee@scope.test",
        status="active",
    )
    async with factory() as session:
        session.add(CompanyRow(
            id=COMPANY_ID,
            name="Scope Acceptance Company",
            login_slug="scope-company",
            status="active",
        ))
        session.add(employee)
        session.add(MembershipRow(
            id="mem_scope",
            company_id=COMPANY_ID,
            employee_id=EMPLOYEE_ID,
            role="member",
        ))
        # Harness fix: flush in FK-safe phases.  The unit of work emitted the
        # relationship-less CabinetInstanceRow INSERT before Company/Employee,
        # violating cabinet_instances_owner_employee_id_fkey; explicit phases
        # persist parents first without changing the final committed state.
        await session.flush()
        session.add(CabinetInstanceRow(
            id=CABINET_ID,
            name="Scope Cabinet",
            schema_name="scope_cabinet",
            owner_employee_id=EMPLOYEE_ID,
            company_id=COMPANY_ID,
            owner_company_id=COMPANY_ID,
            status="active",
        ))
        await session.flush()  # cabinet anchors persist before the project
        session.add(ProjectRow(
            id=PROJECT_ID,
            company_id=COMPANY_ID,
            cabinet_id=CABINET_ID,
            owner_employee_id=EMPLOYEE_ID,
            name="Scope Project",
            slug="scope-project",
            status="active",
            workspace_key="scope-workspace",
            container_ref="scope-container",
        ))
        await session.flush()  # project anchors persist before source/flag rows
        session.add(PluginFeatureFlagRow(
            company_id=COMPANY_ID,
            enabled_modules=[PLUGIN_ID],
        ))
        session.add(ModuleSourceRow(
            id=SOURCE_ID,
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            cabinet_id=CABINET_ID,
            project_id=PROJECT_ID,
            provider="github_app",
            repo_id=900001,
            full_name="agentscale/plugins",
            private=True,
            default_branch="main",
            installation_credential_ref="secret_ref://ci/plugin",
            trusted_workflow_ref=(
                "agentscale/plugins/.github/workflows/publish.yml@" + "a" * 40
            ),
            status="ready",
        ))
        await session.commit()

    record = make_record(manifest_override=bootstrap_manifest())
    store = store_for(factory)
    await store.publish_async(record, evidence=record.verification, actor="scope-ci")

    async with factory() as session:
        session.add(PluginInstanceRow(
            id=instance_id,
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            manifest_version=record.version,
            artifact_digest=record.digest,
            status="active",
            tenant_db_name="tenant_co_scope",
            bucket_name="scope-bucket",
        ))
        await session.flush()  # instance row persists before the grant binding
        session.add(CapabilityGrantRow(
            id="grant_scope",
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            plugin_instance_id=instance_id,
            manifest_version=record.version,
            artifact_digest=record.digest,
            capabilities=["equipment.read"],
            egress_policy={},
            quota={},
            status="active",
            created_by="scope-acceptance",
        ))
        await session.commit()
    return ScopeSeed(employee=employee, plugin_instance_id=instance_id, record=record)


__all__ = [
    "CABINET_ID",
    "COMPANY_ID",
    "EMPLOYEE_ID",
    "PROJECT_ID",
    "ScopeSeed",
    "bootstrap_manifest",
    "seed_ready_bootstrap",
    "session_factory",
]

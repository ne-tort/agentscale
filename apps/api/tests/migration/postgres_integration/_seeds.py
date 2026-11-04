"""Seed rows for the PostgreSQL integration suite (MIG-INT-PG-01).

Every fixture the schema chain needs is spelled out here instead of being
copied into individual tests, because the migration wave introduced several
deep foreign-key chains (tenant anchor -> scope registry -> M04.e definition ->
version) and a copy would drift.

Two rules are applied throughout:

* rows are inserted with **explicit column lists over ``text()`` SQL**, not
  through the ORM, so the suite exercises the constraints that the migrations
  create rather than ORM-side validation and ``before_update`` hooks;
* every JSONB parameter is sent as ``json.dumps(...)`` and cast in SQL
  (``CAST(:param AS jsonb)``).  ``text()`` carries no column types, so a raw
  Python list would reach ``asyncpg`` as a PostgreSQL array and fail on a
  ``jsonb`` column.

Tenant identities are kept separate on purpose: ``scope_owner_tenant_anchors``
allows exactly one anchor per tenant (``uq_scope_owner_tenant_identity``), so a
tenant whose scope registry row is ``active`` and one whose row is
``quarantined`` cannot be the same tenant.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from prodavan.application.agent_control_plane import ControlPlaneLinkage, DurableAgentControlPlane
from prodavan.domain.identity import ROLE_COMPANY_ADMIN

from ._support import sha256_digest, sha256_of_text

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
"""Fixed control-plane clock so event times are stable across reruns."""

QUEUE_NAME = "agent-control-plane"

#: Tables the agent control plane writes; used for "no duplicates" assertions.
CONTROL_PLANE_TABLES = (
    "agent_runs",
    "agent_control_plane_run_scopes",
    "agent_control_plane_aggregates",
    "agent_control_plane_events",
    "agent_control_plane_outbox_events",
    "agent_control_plane_enqueue_intents",
    "agent_control_plane_commands",
)


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


def _jsonb(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# --------------------------------------------------------------------------- #
# run scope: the authorization chain `_valid_scope` walks
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SeedScope:
    """A tenant/project/cabinet/employee tuple that passes ``_valid_scope``."""

    tenant_id: str
    employee_id: str
    membership_id: str
    cabinet_id: str
    project_id: str


async def seed_scope(harness) -> SeedScope:
    """Create one fully authorized tenant/project/cabinet/employee tuple.

    ``_valid_scope`` requires an active company, an active project of that
    company, an active employee with a membership, an active cabinet, and
    either a cabinet assignment or company-wide cabinet access.  The cabinet is
    therefore created with ``company_grant_scope = 'all'`` and
    ``owner_scope = 'platform'`` so no assignment row is needed.
    """
    suffix = _suffix()
    scope = SeedScope(
        tenant_id=f"co_pgint{suffix}",
        employee_id=f"emp_pgint{suffix}",
        membership_id=f"mem_pgint{suffix}",
        cabinet_id=f"cab_pgint{suffix}",
        project_id=f"prj_pgint{suffix}",
    )
    async with harness.engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO companies (id, name, login_slug, status) VALUES (:id, :name, :slug, 'active')"),
            {"id": scope.tenant_id, "name": f"PG Int {suffix}", "slug": f"pgint{suffix}"},
        )
        await conn.execute(
            text(
                "INSERT INTO employees (id, login, email, status) "
                "VALUES (:id, :login, :email, 'active')"
            ),
            {
                "id": scope.employee_id,
                "login": f"pgint{suffix}@pgint.local",
                "email": f"pgint{suffix}@pgint.local",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO memberships (id, company_id, employee_id, role) "
                "VALUES (:id, :company, :employee, :role)"
            ),
            {
                "id": scope.membership_id,
                "company": scope.tenant_id,
                "employee": scope.employee_id,
                "role": ROLE_COMPANY_ADMIN,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO cabinet_instances "
                "(id, name, schema_name, owner_scope, company_grant_scope, status) "
                "VALUES (:id, :name, :schema, 'platform', 'all', 'active')"
            ),
            {
                "id": scope.cabinet_id,
                "name": f"PG Int Cabinet {suffix}",
                "schema": f"pgint_cab_{suffix}",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO projects "
                "(id, company_id, cabinet_id, name, slug, status, visibility_mode, "
                " workspace_key, container_ref) "
                "VALUES (:id, :company, :cabinet, :name, :slug, 'active', "
                " 'cabinet_shared', :workspace, :container)"
            ),
            {
                "id": scope.project_id,
                "company": scope.tenant_id,
                "cabinet": scope.cabinet_id,
                "name": f"PG Int Project {suffix}",
                "slug": f"pgint-{suffix}",
                "workspace": f"pgint_ws_{suffix}",
                "container": f"pgint_container_{suffix}",
            },
        )
    return scope


def linkage(
    scope: SeedScope,
    *,
    trace_id: str = "trace-pgint",
) -> ControlPlaneLinkage:
    """Control-plane linkage for a seeded scope (no agent session on purpose).

    ``session_id`` stays ``None``: ``_valid_scope`` then skips the session
    lookup, which keeps these fixtures focused on the concurrency contract
    instead of the session lifecycle.
    """
    return ControlPlaneLinkage(
        tenant_id=scope.tenant_id,
        actor_id=scope.employee_id,
        project_id=scope.project_id,
        session_id=None,
        trace_id=trace_id,
    )


def control_plane(harness, scope: SeedScope) -> DurableAgentControlPlane:
    """The real durable control plane wired to this schema's repository."""
    return DurableAgentControlPlane(
        transaction=harness.repository(),
        tenant_id=scope.tenant_id,
        queue_name=QUEUE_NAME,
        clock=lambda: NOW,
    )


# --------------------------------------------------------------------------- #
# agent runs and leases
# --------------------------------------------------------------------------- #
async def seed_run(
    harness,
    scope: SeedScope,
    *,
    run_id: str,
    status: str = "queued",
) -> str:
    """Insert one ``agent_runs`` row (the lease table's FK parent)."""
    async with harness.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, project_id, session_id, requested_by_employee_id, idempotency_key, "
                " status, input_payload) "
                "VALUES (:id, :project, NULL, NULL, :key, :status, CAST(:payload AS jsonb))"
            ),
            {
                "id": run_id,
                "project": scope.project_id,
                "key": f"seed-{run_id}",
                "status": status,
                "payload": _jsonb({}),
            },
        )
    return run_id


async def seed_aggregate_cursor(
    harness,
    *,
    run_id: str,
    status: str = "queued",
    sequence: int = 1,
) -> None:
    """Insert the run's aggregate cursor.

    ``claim_candidate_statement`` joins this table and requires
    ``aggregate.status == run.status`` plus ``last_sequence >= 1``, so a run
    without a cursor is deliberately never claimable.  Seeding it explicitly is
    therefore part of "this run is claimable", not an incidental detail.
    """
    await harness.execute(
        "INSERT INTO agent_control_plane_aggregates "
        "(aggregate_type, aggregate_id, last_sequence, status) "
        "VALUES ('agent_run', :run, :sequence, :status)",
        {"run": run_id, "sequence": sequence, "status": status},
    )


async def seed_claimable_run(
    harness,
    scope: SeedScope,
    *,
    run_id: str,
    status: str = "queued",
    sequence: int = 1,
    lease: dict[str, Any] | None = None,
) -> str:
    """A run that a worker may claim: run row, aggregate cursor and lease row.

    ``lease`` overrides ``seed_lease`` keyword arguments, which is how the tests
    build "expired lease" and "retry scheduled in the future" states without
    reaching for a second helper.
    """
    await seed_run(harness, scope, run_id=run_id, status=status)
    await seed_aggregate_cursor(harness, run_id=run_id, status=status, sequence=sequence)
    await seed_lease(harness, scope, run_id=run_id, **(lease or {}))
    return run_id


async def seed_lease(
    harness,
    scope: SeedScope,
    *,
    run_id: str,
    lease_owner: str | None = None,
    lease_token: str | None = None,
    lease_until: datetime | None = None,
    retry_at: datetime | None = None,
    lease_generation: int = 0,
    attempt: int = 1,
) -> None:
    """Insert one ``agent_run_leases`` row.

    The all-or-nothing lease triple is enforced by the row itself
    (``ck_agent_run_lease_token_pair`` / ``ck_agent_run_lease_until_pair``), so
    the caller must pass owner, token and deadline together.
    """
    async with harness.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_run_leases "
                "(run_id, tenant_id, project_id, lease_owner, lease_token, lease_until, "
                " lease_generation, attempt, retry_at) "
                "VALUES (:run, :tenant, :project, :owner, :token, :until, :generation, "
                " :attempt, :retry_at)"
            ),
            {
                "run": run_id,
                "tenant": scope.tenant_id,
                "project": scope.project_id,
                "owner": lease_owner,
                "token": lease_token,
                "until": lease_until,
                "generation": lease_generation,
                "attempt": attempt,
                "retry_at": retry_at,
            },
        )


# --------------------------------------------------------------------------- #
# M04.e definition/version parents (shared by M05 and M06 fixtures)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DefinitionParents:
    """Tenant, definition and version rows a grant or an instance hangs off."""

    tenant_id: str
    app_id: str
    definition_id: str
    version_id: str
    artifact_digest: str
    owner_state: str


async def seed_definition_parents(
    harness,
    *,
    owner_state: str,
    tenant_id: str | None = None,
) -> DefinitionParents:
    """Create company, tenant anchor, tenant-scope definition and one version.

    ``owner_state`` selects the anchor flavour: ``active`` for the canonical
    grant chain, ``quarantined`` for the staged M05 cycle (a plugin-instance
    registry row is required to stay quarantined by
    ``ck_scope_registry_plugin_deferred``).
    """
    suffix = _suffix()
    tenant = tenant_id or f"co_pgint{suffix}"
    app_id = f"pgint-app-{suffix}"
    definition_id = f"def_pgint{suffix}"
    version_id = f"ver_pgint{suffix}"
    artifact_digest = sha256_of_text(f"artifact-{suffix}")
    artifact_hex = artifact_digest.removeprefix("sha256:")
    manifest = {
        "protocol": "1.0",
        "version": "1.0.0",
        "appId": app_id,
        "displayName": f"PG Int App {suffix}",
        "entrypoints": {"plugin": {"artifact": {"sha256": artifact_hex}}},
        "requestedCapabilities": [],
    }
    async with harness.engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO companies (id, name, login_slug, status) VALUES (:id, :name, :slug, 'active')"),
            {"id": tenant, "name": f"PG Int {suffix}", "slug": f"pgint{suffix}"},
        )
        await conn.execute(
            text(
                "INSERT INTO scope_owner_tenant_anchors "
                "(id, tenant_id, tenant_type, owner_state, source_kind, source_id, "
                " schema_version, payload_digest) "
                "VALUES (:id, :tenant, 'company', :state, :source_kind, :source_id, "
                " '1.0', :digest)"
            ),
            {
                "id": f"scope_anchor_{suffix}",
                "tenant": tenant,
                "state": owner_state,
                "source_kind": "pgint_fixture",
                "source_id": suffix,
                "digest": sha256_digest({"anchor": suffix}),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO m04e_dynamic_app_definitions "
                "(id, tenant_id, app_id, display_name, scope_kind, scope_id, lifecycle, "
                " source_snapshot_id, source_digest) "
                "VALUES (:id, :tenant, :app, :name, 'tenant', :scope_id, 'draft', "
                " :snapshot, :digest)"
            ),
            {
                "id": definition_id,
                "tenant": tenant,
                "app": app_id,
                "name": f"PG Int Definition {suffix}",
                "scope_id": tenant,
                "snapshot": f"snapshot-{suffix}",
                "digest": sha256_digest({"definition": suffix}),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO m04e_dynamic_app_versions "
                "(id, definition_id, tenant_id, app_id, manifest_version, artifact_digest, "
                " manifest, requested_capabilities, lifecycle, source_snapshot_id, source_digest) "
                "VALUES (:id, :definition, :tenant, :app, '1.0.0', :artifact, "
                " CAST(:manifest AS jsonb), CAST(:capabilities AS jsonb), 'draft', "
                " :snapshot, :digest)"
            ),
            {
                "id": version_id,
                "definition": definition_id,
                "tenant": tenant,
                "app": app_id,
                "artifact": artifact_digest,
                "manifest": _jsonb(manifest),
                "capabilities": _jsonb([]),
                "snapshot": f"snapshot-{suffix}",
                "digest": sha256_digest({"version": suffix}),
            },
        )
    return DefinitionParents(
        tenant_id=tenant,
        app_id=app_id,
        definition_id=definition_id,
        version_id=version_id,
        artifact_digest=artifact_digest,
        owner_state=owner_state,
    )


# --------------------------------------------------------------------------- #
# scope registry rows
# --------------------------------------------------------------------------- #
async def seed_tenant_scope_registry(harness, parents: DefinitionParents) -> str:
    """A ``scope_kind = 'tenant'`` registry row (target of the grant FK)."""
    registry_id = f"scope_reg_{_suffix()}"
    async with harness.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO scope_registry "
                "(id, scope_kind, scope_id, tenant_id, tenant_type, cabinet_id, project_id, "
                " project_status, plugin_instance_id, owner_state, source_kind, source_id, "
                " schema_version, payload_digest) "
                "VALUES (:id, 'tenant', :tenant, :tenant, 'company', NULL, NULL, NULL, NULL, "
                " :state, :source_kind, :source_id, '1.0', :digest)"
            ),
            {
                "id": registry_id,
                "tenant": parents.tenant_id,
                "state": parents.owner_state,
                "source_kind": "pgint_fixture",
                "source_id": registry_id,
                "digest": sha256_digest({"registry": registry_id}),
            },
        )
    return registry_id


def plugin_scope_registry_values(
    parents: DefinitionParents,
    *,
    registry_id: str,
    instance_id: str,
) -> dict[str, Any]:
    """Column values for the *plugin-instance* side of the M05 FK cycle.

    Only the tenant link is exercised (``cabinet_id``/``project_id`` stay NULL),
    so exactly one reciprocal pair is under test:
    ``m05_plugin_instances.fk_m05_instance_registry_tenant_link`` and
    ``scope_registry.fk_m05_registry_instance_tenant_link``.
    """
    return {
        "id": registry_id,
        "scope_kind": "plugin_instance",
        "scope_id": instance_id,
        "tenant_id": parents.tenant_id,
        "tenant_type": "company",
        "cabinet_id": None,
        "project_id": None,
        "project_status": None,
        "plugin_instance_id": instance_id,
        "owner_state": "quarantined",
        "source_kind": "pgint_fixture",
        "source_id": instance_id,
        "schema_version": "1.0",
        "payload_digest": sha256_digest({"registry": registry_id, "instance": instance_id}),
        "checkpoint": None,
    }


def plugin_instance_values(
    parents: DefinitionParents,
    *,
    instance_id: str,
    registry_id: str,
) -> dict[str, Any]:
    """Column values for the ``m05_plugin_instances`` side of the M05 FK cycle."""
    return {
        "id": instance_id,
        "plugin_instance_id": instance_id,
        "definition_id": parents.definition_id,
        "version_id": parents.version_id,
        "tenant_id": parents.tenant_id,
        "scope_kind": "plugin_instance",
        "scope_id": instance_id,
        "cabinet_id": None,
        "project_id": None,
        "scope_registry_id": registry_id,
        "artifact_digest": parents.artifact_digest,
        "subject_id": None,
        "session_id": None,
        "lifecycle": "quarantined",
        "runtime_generation": 0,
        "writer_generation": 0,
        "source_kind": "pgint_fixture",
        "source_id": instance_id,
        "source_schema_version": "1.0",
        "source_digest": sha256_digest({"instance": instance_id}),
        "checkpoint": None,
    }


#: Insert statement for one M05 instance row (parameter names match the values dict).
INSERT_PLUGIN_INSTANCE = text(
    """
    INSERT INTO m05_plugin_instances
        (id, plugin_instance_id, definition_id, version_id, tenant_id, scope_kind,
         scope_id, cabinet_id, project_id, scope_registry_id, artifact_digest,
         subject_id, session_id, lifecycle, runtime_generation, writer_generation,
         source_kind, source_id, source_schema_version, source_digest, checkpoint)
    VALUES
        (:id, :plugin_instance_id, :definition_id, :version_id, :tenant_id, :scope_kind,
         :scope_id, :cabinet_id, :project_id, :scope_registry_id, :artifact_digest,
         :subject_id, :session_id, :lifecycle, :runtime_generation, :writer_generation,
         :source_kind, :source_id, :source_schema_version, :source_digest, :checkpoint)
    """
)

#: Insert statement for one scope-registry row (plugin-instance flavour).
INSERT_PLUGIN_SCOPE_REGISTRY = text(
    """
    INSERT INTO scope_registry
        (id, scope_kind, scope_id, tenant_id, tenant_type, cabinet_id, project_id,
         project_status, plugin_instance_id, owner_state, source_kind, source_id,
         schema_version, payload_digest, checkpoint)
    VALUES
        (:id, :scope_kind, :scope_id, :tenant_id, :tenant_type, :cabinet_id, :project_id,
         :project_status, :plugin_instance_id, :owner_state, :source_kind, :source_id,
         :schema_version, :payload_digest, :checkpoint)
    """
)


# --------------------------------------------------------------------------- #
# canonical grants
# --------------------------------------------------------------------------- #
GRANT_OPERATIONS = ["read"]
GRANT_RESOURCES = ["equipment:*"]
GRANT_DOMAINS = ["app.example"]
GRANT_CONSTRAINTS: dict[str, Any] = {}


def grant_values(
    parents: DefinitionParents,
    *,
    registry_id: str,
    idempotency_key: str,
    subject_id: str = "emp_pgint_grant",
) -> dict[str, Any]:
    """A complete, individually valid ``dynamic_app_capability_grants`` row.

    Only ``idempotency_key`` (and the derived ``id``) differ between calls, so
    two calls describe the *same* authorization identity and collide on the
    partial unique index ``uq_cap_grant_active_identity``.  Going through a
    different ``uq_cap_grant_idempotency`` key keeps the two failure modes
    distinguishable.
    """
    payload = {
        "capability": "equipment.read",
        "operations": GRANT_OPERATIONS,
        "resources": GRANT_RESOURCES,
        "domains": GRANT_DOMAINS,
        "constraints": GRANT_CONSTRAINTS,
        "scope": {"kind": "tenant", "id": parents.tenant_id},
        "subject": {"kind": "user", "id": subject_id},
    }
    return {
        "id": f"cgrant_{uuid.uuid4().hex[:24]}",
        "app_definition_id": parents.definition_id,
        "app_id": parents.app_id,
        "app_version_id": parents.version_id,
        "app_artifact_digest": parents.artifact_digest,
        "tenant_id": parents.tenant_id,
        "cabinet_id": None,
        "project_id": None,
        "scope_registry_id": registry_id,
        "scope_kind": "tenant",
        "scope_id": parents.tenant_id,
        "capability_key": "equipment.read",
        "subject_kind": "user",
        "subject_id": subject_id,
        "plugin_instance_id": None,
        "session_id": None,
        "operations": _jsonb(GRANT_OPERATIONS),
        "resources": _jsonb(GRANT_RESOURCES),
        "domains": _jsonb(GRANT_DOMAINS),
        "constraints": _jsonb(GRANT_CONSTRAINTS),
        "operations_digest": sha256_digest(GRANT_OPERATIONS),
        "resources_digest": sha256_digest(GRANT_RESOURCES),
        "domains_digest": sha256_digest(GRANT_DOMAINS),
        "constraints_digest": sha256_digest(GRANT_CONSTRAINTS),
        "issued_by": "pgint-fixture",
        "revoked_by": None,
        "revoke_reason": None,
        "grant_generation": 1,
        "writer_generation": 1,
        "idempotency_key": idempotency_key,
        "source_kind": "canonical",
        "source_id": None,
        "schema_version": "1.0",
        "payload_digest": sha256_digest(payload),
        "audit_metadata": _jsonb({}),
    }


INSERT_GRANT = text(
    """
    INSERT INTO dynamic_app_capability_grants
        (id, app_definition_id, app_id, app_version_id, app_artifact_digest, tenant_id,
         cabinet_id, project_id, scope_registry_id, scope_kind, scope_id, capability_key,
         subject_kind, subject_id, plugin_instance_id, session_id, operations, resources,
         domains, constraints, operations_digest, resources_digest, domains_digest,
         constraints_digest, issued_by, revoked_by, revoke_reason, grant_generation,
         writer_generation, idempotency_key, source_kind, source_id, schema_version,
         payload_digest, audit_metadata)
    VALUES
        (:id, :app_definition_id, :app_id, :app_version_id, :app_artifact_digest, :tenant_id,
         :cabinet_id, :project_id, :scope_registry_id, :scope_kind, :scope_id, :capability_key,
         :subject_kind, :subject_id, :plugin_instance_id, :session_id,
         CAST(:operations AS jsonb), CAST(:resources AS jsonb), CAST(:domains AS jsonb),
         CAST(:constraints AS jsonb), :operations_digest, :resources_digest, :domains_digest,
         :constraints_digest, :issued_by, :revoked_by, :revoke_reason, :grant_generation,
         :writer_generation, :idempotency_key, :source_kind, :source_id, :schema_version,
         :payload_digest, CAST(:audit_metadata AS jsonb))
    """
)


__all__ = [
    "CONTROL_PLANE_TABLES",
    "DefinitionParents",
    "GRANT_CONSTRAINTS",
    "GRANT_DOMAINS",
    "GRANT_OPERATIONS",
    "GRANT_RESOURCES",
    "INSERT_GRANT",
    "INSERT_PLUGIN_INSTANCE",
    "INSERT_PLUGIN_SCOPE_REGISTRY",
    "NOW",
    "QUEUE_NAME",
    "SeedScope",
    "control_plane",
    "grant_values",
    "linkage",
    "plugin_instance_values",
    "plugin_scope_registry_values",
    "seed_definition_parents",
    "seed_aggregate_cursor",
    "seed_claimable_run",
    "seed_lease",
    "seed_run",
    "seed_scope",
    "seed_tenant_scope_registry",
]

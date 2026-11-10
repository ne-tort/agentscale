from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from prodavan.api.internal.plugin_artifacts import PLUGIN_CI_PUBLICATION_SERVICE_STATE
from prodavan.api.v1.plugin_admin import (
    CapabilitiesBody,
    GrantBody,
    PublicationApprovalBody,
    QuotaSetBody,
    approve_plugin_publication,
    grant_plugin,
    list_plugins,
)
from prodavan.application.plugins.admin.contracts import (
    CapabilitySet,
    GrantStatus,
    PluginInstanceRecord,
    PluginInstanceStatus,
    PluginRuntimeStatus,
    QuotaSet,
)
from prodavan.application.plugins.admin.errors import PluginAdminError
from prodavan.application.plugins.admin.ports import webhook_secret_key
from prodavan.application.plugins.bootstrap.service import PluginBootstrapService
from prodavan.application.plugins.capabilities.adapters.secrets import (
    validate_secret_delivery_ref,
)
from prodavan.application.plugins.events.dispatch import HmacPluginEventSigner
from prodavan.application.plugins.identity.mint import peek_plugin_token
from prodavan.application.plugins.installation_scope.repository import (
    SqlInstallationScopeRepository,
)
from prodavan.application.plugins.jobs.tasks import ManifestJobTasks
from prodavan.domain.errors import AppError
from prodavan.domain.plugins.helpers import plugin_instance_id_for
from prodavan.domain.plugins.validation import validate_manifest
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
)
from prodavan.infrastructure.persistence.models.installation_scope import (
    PluginInstallationScopeRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.persistence.models.plugin_platform import (
    CapabilityGrantRow,
    PluginArtifactRow,
)
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    SqlCapabilityGrantRepository,
)
from prodavan.infrastructure.plugins.runtime.deployment_admin import (
    ARTIFACT_ANNOTATION,
    BACKEND_ANNOTATION,
    COMPANY_LABEL,
    INSTANCE_LABEL,
    MANAGED_BY,
    MANAGED_BY_VALUE,
    PLUGIN_LABEL,
)
from prodavan.infrastructure.plugins.runtime.k8s_scale import plugin_resource_name
from prodavan.infrastructure.plugins.runtime.secret_projection import (
    DELIVERY_SECRET_PREFIX,
    DSN_REF_ANNOTATION,
    SECRET_KEYS,
)

from .production_boundaries import (
    BACKEND_DIGEST,
    BACKEND_REF,
    CAPABILITIES,
    COMMIT_SHA,
    COMPANY_ID,
    NOW,
    PLUGIN_ID,
    QUOTAS,
    VERSION,
    publication_fixture,
    ready_source,
    register_publication,
)
from .test_production_composition import composition_world

pytestmark = pytest.mark.integration


def _capabilities() -> CapabilitySet:
    return CapabilitySet(capabilities=CAPABILITIES, quotas=QuotaSet(**QUOTAS))


def _capabilities_body() -> CapabilitiesBody:
    return CapabilitiesBody(
        capabilities=CAPABILITIES,
        quotas=QuotaSetBody(**QUOTAS),
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]


@pytest.mark.asyncio
async def test_first_publication_grant_failure_success_replay_and_production_dtos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with composition_world(monkeypatch, source=True, ci=True, platform=True) as world:
        app = world.app
        cabinet_id = "cab_acceptance"
        project_id = "pr_acceptance"
        source = replace(
            ready_source(), cabinet_id=cabinet_id, project_id=project_id
        )
        fixture = publication_fixture(source, public=True)
        register_publication(fixture, world.ghcr)
        ci = getattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
        admin = app.state.plugin_admin_service
        ports = app.state.plugin_admin_ports
        artifact_store = ci._registry._store
        grant_store = ports.grant_store
        assert isinstance(ports.installation_scopes, SqlInstallationScopeRepository)
        assert (
            ports.installation_scopes._session_factory
            is world.relational.session_factory
        )
        assert admin._ports.installation_scopes is ports.installation_scopes

        # The cold composition reached real signed events and production-created
        # ManifestJobTasks; below are behavioral saga assertions, not startup skips.
        assert isinstance(app.state.plugin_event_dispatcher._signer, HmacPluginEventSigner)
        jobs = app.state.plugin_adapter_registry.resolve("jobs")
        assert isinstance(jobs._allowed_tasks, ManifestJobTasks)
        assert jobs._allowed_tasks.specs
        assert world.plugin_worker.wired is True
        assert world.job_declaration is not None
        assert world.job_declaration.plugin_id != PLUGIN_ID

        # First-ever target state: no hello_plugin artifact/grant is seeded. The
        # independent job declaration has no grant; source uses its real repository.
        assert await artifact_store.by_digest_async(fixture.bundle_digest) is None
        assert await grant_store.get(company_id=COMPANY_ID, plugin_id=PLUGIN_ID) is None
        async with world.relational.session_factory() as session:
            session.add(
                CompanyRow(
                    id=COMPANY_ID,
                    name="Acceptance Company",
                    login_slug="acceptance-company",
                    status="active",
                )
            )
            # No ORM relationship orders these independent fixture mappers.
            await session.flush()
            session.add(
                CabinetInstanceRow(
                    id=cabinet_id,
                    name="Acceptance Cabinet",
                    schema_name="cab_acceptance",
                    company_id=COMPANY_ID,
                    status="active",
                )
            )
            await session.flush()
            session.add(
                ProjectRow(
                    id=project_id,
                    company_id=COMPANY_ID,
                    cabinet_id=cabinet_id,
                    name="Acceptance Project",
                    slug="acceptance-project",
                    status="active",
                    workspace_key="ws-acceptance",
                    container_ref="acceptance-project",
                )
            )
            await session.commit()
        stored_source = await ci._sources.create(source)
        assert stored_source.status is source.status
        assert (await ci._sources.get_by_id(source.id)) == source

        approval_receipt = await admin.approve_publication(
            actor_id="platform-admin-acceptance",
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            manifest_version=VERSION,
            artifact_digest=fixture.bundle_digest,
            capabilities=_capabilities(),
        )
        assert approval_receipt.created is True
        assert approval_receipt.replayed is False
        approval = approval_receipt.approval
        assert approval.consumed_at is None
        assert approval.quotas == QuotaSet(**QUOTAS)
        assert approval.capabilities == CAPABILITIES
        assert await artifact_store.by_digest_async(fixture.bundle_digest) is None
        assert await grant_store.get(company_id=COMPANY_ID, plugin_id=PLUGIN_ID) is None

        # Production Pydantic mapping is exercised independently of the service call.
        approval_dto = await approve_plugin_publication(
            PublicationApprovalBody(
                company_id=COMPANY_ID,
                plugin_id=PLUGIN_ID,
                manifest_version=VERSION,
                artifact_digest=fixture.bundle_digest,
                capabilities=_capabilities_body(),
            ),
            SimpleNamespace(app=app),
            "platform-admin-acceptance",
        )
        assert approval_dto.created is False
        assert approval_dto.replayed is True
        assert approval_dto.approval_id == approval.approval_id
        assert approval_dto.quotas.model_dump() == QUOTAS

        published = await ci.publish(
            fixture.command,
            oidc_token=world.jwks.token(source),
        )
        assert published.status == "PUBLISHED", (
            f"PUBLICATION COMPOSITION BLOCKER: {published.code}"
        )
        artifact = await artifact_store.by_digest_async(fixture.bundle_digest)
        assert artifact is not None
        assert artifact.published is True
        assert artifact.publication is not None
        assert artifact.publication.source_id == source.id
        assert artifact.publication.commit_sha == COMMIT_SHA
        assert artifact.publication.backend_digest == BACKEND_DIGEST
        assert artifact.publication.backend_image_ref == BACKEND_REF
        assert artifact.publication.run_url == fixture.command.run_url
        assert artifact.verification is not None
        assert artifact.verification.seal is None
        assert ci._registry._verifier._seal is app.state.plugin_artifact_verifier._seal
        assert artifact.verification.artifact_digest == fixture.bundle_digest
        assert artifact.verification.artifact_digest != BACKEND_DIGEST
        assert world.ghcr.authorized_requests >= 2

        # Start the real lifecycle-owned asyncpg adapter; only pool responses
        # are stateful test doubles.
        await world.asyncpg.open()
        assert await world.asyncpg.health()

        # A failed K8s create happens after real secret projection and must not
        # produce an ACTIVE grant or consume the approval.
        world.k8s.fail_next_deployment_create = True
        with pytest.raises(PluginAdminError):
            await admin.grant(
                company_id=COMPANY_ID,
                plugin_id=PLUGIN_ID,
                manifest_version=VERSION,
                artifact_digest=fixture.bundle_digest,
                capabilities=_capabilities(),
            )
        assert await grant_store.get(company_id=COMPANY_ID, plugin_id=PLUGIN_ID) is None
        failed_instance = await ports.instance_store.get(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
        )
        assert failed_instance is not None
        assert failed_instance.status is PluginInstanceStatus.INSTALLED
        persisted_approval = await ports.publication_approvals.get(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            manifest_version=VERSION,
            artifact_digest=fixture.bundle_digest,
        )
        assert persisted_approval is not None
        assert persisted_approval.consumed_at is None
        projected_name = plugin_resource_name(PLUGIN_ID, failed_instance.plugin_instance_id)
        assert world.k8s.secret(projected_name)["kind"] == "Secret"
        assert ("deployments", projected_name) not in world.k8s.store.objects

        receipt = await admin.grant(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            manifest_version=VERSION,
            artifact_digest=fixture.bundle_digest,
            capabilities=_capabilities(),
        )
        assert receipt.replayed is False
        assert receipt.plugin_instance_id == failed_instance.plugin_instance_id
        assert receipt.grant.status is GrantStatus.ACTIVE
        assert receipt.grant.quotas == QuotaSet(**QUOTAS)
        assert receipt.grant.artifact_digest == fixture.bundle_digest
        assert receipt.grant.webhook_secret_ref is not None
        assert receipt.database.dsn_ref == (
            f"secret://plugins/tenantdb/{COMPANY_ID}/TENANT_DB_DSN"
        )
        assert receipt.schema.dsn_ref == (
            f"file://ai_keys/plugins/tenantdb/{COMPANY_ID}/{PLUGIN_ID}/TENANT_DB_DSN.secret"
        )
        assert receipt.schema.dsn_ref != receipt.database.dsn_ref

        # Exact durable row, not a repository snapshot or an ACTIVE seed.
        fresh_grants = SqlCapabilityGrantRepository(
            session_factory=world.relational.session_factory,
        )
        durable = await fresh_grants.get_by_grant_id(grant_id=receipt.grant.grant_id)
        assert durable is not None
        assert durable.is_active is True
        assert durable.plugin_instance_id == receipt.plugin_instance_id
        assert durable.quotas == QuotaSet(**QUOTAS)
        assert durable.webhook_secret_ref == receipt.grant.webhook_secret_ref
        async with world.relational.session_factory() as session:
            row = await session.get(CapabilityGrantRow, receipt.grant.grant_id)
            assert row is not None
            assert row.status == "active"
            assert row.quota == QUOTAS
            assert row.webhook_secret_ref == receipt.grant.webhook_secret_ref

        # SecretProjection + DeploymentAdmin: exact data keys, labels, source and
        # backend binding. Values are compared locally, never printed or logged.
        secret = world.k8s.secret(projected_name)
        metadata = secret["metadata"]
        expected_labels = {
            MANAGED_BY: MANAGED_BY_VALUE,
            COMPANY_LABEL: _hash(COMPANY_ID),
            PLUGIN_LABEL: _hash(PLUGIN_ID),
            INSTANCE_LABEL: _hash(
                f"{COMPANY_ID}\0{PLUGIN_ID}\0{receipt.plugin_instance_id}"
            ),
        }
        assert metadata["labels"] == expected_labels
        assert set(secret["data"]) == set(SECRET_KEYS) == {
            "GATEWAY_URL",
            "PLUGIN_ID",
            "TENANT_DB_DSN",
            "WEBHOOK_SECRET",
        }
        assert metadata["annotations"][DSN_REF_ANNOTATION] == hashlib.sha256(
            receipt.schema.dsn_ref.encode("utf-8")
        ).hexdigest()
        decoded = {
            key: base64.b64decode(value).decode("utf-8")
            for key, value in secret["data"].items()
        }
        assert decoded["PLUGIN_ID"] == PLUGIN_ID
        assert decoded["GATEWAY_URL"].startswith("http://plugin-gateway.")
        assert decoded["TENANT_DB_DSN"].startswith("postgresql://")
        webhook_key = webhook_secret_key(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            plugin_instance_id=receipt.plugin_instance_id,
        )
        stored_webhook = await world.vault.get(key=webhook_key)
        assert stored_webhook is not None
        assert stored_webhook.secret_ref == receipt.grant.webhook_secret_ref
        assert decoded["WEBHOOK_SECRET"] == stored_webhook.value

        deployment = world.k8s.deployment(projected_name)
        pod_spec = deployment["spec"]["template"]["spec"]
        container = pod_spec["containers"][0]
        assert deployment["metadata"]["annotations"][ARTIFACT_ANNOTATION] == fixture.bundle_digest
        assert deployment["metadata"]["annotations"][BACKEND_ANNOTATION] == BACKEND_DIGEST
        assert container["image"] == BACKEND_REF
        assert container["image"] != fixture.bundle_ref
        assert deployment["spec"]["replicas"] == 0
        assert pod_spec["automountServiceAccountToken"] is False
        env = container["env"]
        assert {item["name"] for item in env} == set(SECRET_KEYS) | {
            "HOME",
            "TMPDIR",
            "PLUGIN_ARTIFACT_DIGEST",
        }
        for item in env:
            if item["name"] in SECRET_KEYS:
                reference = item["valueFrom"]["secretKeyRef"]
                assert reference == {"name": projected_name, "key": item["name"]}
                assert "value" not in item
            elif item["name"] in {"HOME", "TMPDIR"}:
                assert item == {"name": item["name"], "value": "/tmp"}
            else:
                assert item == {
                    "name": "PLUGIN_ARTIFACT_DIGEST",
                    "valueFrom": {
                        "fieldRef": {
                            "fieldPath": f"metadata.annotations['{ARTIFACT_ANNOTATION}']"
                        }
                    },
                }
        runtime = await ports.activation.runtime_status(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
        )
        assert runtime is PluginRuntimeStatus.IDLE
        assert world.s3.buckets[f"tenant-{COMPANY_ID}"]["quota_bytes"] > 0

        # ACTIVE-path accepts only the exact durable ref, never a foreign one.
        resolver = ports.activation.deployments.secret_projection._webhook_resolver
        with pytest.raises(LookupError):
            await resolver.resolve(
                company_id=COMPANY_ID,
                plugin_id=PLUGIN_ID,
                plugin_instance_id=receipt.plugin_instance_id,
                secret_ref="file://ai_keys/plugins/webhooks/foreign/WEBHOOK_SECRET.secret",
            )

        # Replay must reuse grant id and both secret refs, with no DSN/webhook
        # rotation and no second ACTIVE grant. It still re-ensures Deployment.
        replay = await admin.grant(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            manifest_version=VERSION,
            artifact_digest=fixture.bundle_digest,
            capabilities=_capabilities(),
        )
        assert replay.replayed is True
        assert replay.grant.grant_id == receipt.grant.grant_id
        assert replay.grant.webhook_secret_ref == receipt.grant.webhook_secret_ref
        assert replay.schema.dsn_ref == receipt.schema.dsn_ref
        assert replay.claims.jti != receipt.claims.jti
        consumed = await ports.publication_approvals.get(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            manifest_version=VERSION,
            artifact_digest=fixture.bundle_digest,
        )
        assert consumed is not None
        assert consumed.consumed_at is not None
        assert await fresh_grants.get_by_grant_id(grant_id=receipt.grant.grant_id) == durable

        grant_dto = await grant_plugin(
            GrantBody(
                company_id=COMPANY_ID,
                plugin_id=PLUGIN_ID,
                manifest_version=VERSION,
                artifact_digest=fixture.bundle_digest,
                capabilities=_capabilities_body(),
            ),
            SimpleNamespace(app=app),
            "platform-admin-acceptance",
        )
        assert grant_dto.replayed is True
        assert grant_dto.grant_id == receipt.grant.grant_id
        assert grant_dto.grant_status is GrantStatus.ACTIVE
        assert grant_dto.egress_in_token is False
        assert grant_dto.quotas_in_token is False
        token_payload = peek_plugin_token(grant_dto.claims.token)
        assert token_payload is not None
        assert "egress" not in token_payload
        assert "quotas" not in token_payload
        assert token_payload["artifact_digest"] == fixture.bundle_digest
        assert token_payload["grant_id"] == receipt.grant.grant_id

        enabled = await admin.set_enabled_modules(
            company_id=COMPANY_ID,
            plugin_ids=(PLUGIN_ID,),
        )
        assert enabled.enabled_modules == (PLUGIN_ID,)
        list_dto = await list_plugins(
            SimpleNamespace(app=app),
            "platform-admin-acceptance",
            COMPANY_ID,
        )
        assert list_dto.enabled_modules == (PLUGIN_ID,)
        assert len(list_dto.items) == 1
        item = list_dto.items[0]
        assert item.runtime_status is PluginRuntimeStatus.IDLE
        assert item.enabled is True
        assert item.quotas is not None
        assert item.quotas.model_dump() == QUOTAS

        # Bootstrap reads the same durable artifact/grant rows; scope absence
        # blocks private DTO, while public DTO remains available without anchors.
        import prodavan.application.plugins.bootstrap.service as bootstrap_module

        monkeypatch.setattr(
            bootstrap_module.settings,
            "plugin_static_origin",
            "https://plugins.example.test",
        )
        monkeypatch.setattr(
            bootstrap_module.settings,
            "plugin_shell_origin",
            "https://agentscale.example.test",
        )
        async with world.relational.session_factory() as session:
            session.add(
                EmployeeRow(
                    id="emp_acceptance",
                    login="employee@acceptance.local",
                    email="employee@example.test",
                    status="active",
                )
            )
            session.add(
                MembershipRow(
                    id="mem_acceptance",
                    company_id=COMPANY_ID,
                    employee_id="emp_acceptance",
                    role="member",
                )
            )
            await session.commit()
            employee = await session.get(EmployeeRow, "emp_acceptance")
            assert employee is not None

        bootstrap = PluginBootstrapService(world.relational.session_factory)
        private = await bootstrap.private(
            employee=employee,
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            route_path="/private",
        )
        assert private.scope.company_id == COMPANY_ID
        assert private.scope.cabinet_id == cabinet_id
        assert private.scope.project_id == project_id
        assert private.artifact_digest == fixture.bundle_digest

        public = await bootstrap.public(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            route_path="/",
        )
        assert public.route_path == "/"
        assert public.public_pages == ["/"]
        assert len(public.rpc_operations) == 1
        operation = public.rpc_operations[0]
        assert operation.capability_id == "hello.greetings"
        assert operation.capability_version == "1.0"
        assert operation.http_method == "GET"
        assert operation.backend_path == "/api/greetings/list"
        assert operation.public is True
        assert public.scope.company_id == COMPANY_ID
        assert public.scope.cabinet_id is None
        assert public.scope.project_id is None
        public_dump = public.model_dump()
        for forbidden in ("token", "dsn", "webhook_secret_ref", "admin_receipt"):
            assert forbidden not in public_dump

        # Missing binding blocks only private bootstrap; public remains company-only.
        async with world.relational.session_factory() as session:
            binding_row = await session.get(
                PluginInstallationScopeRow, receipt.plugin_instance_id
            )
            assert binding_row is not None
            await session.delete(binding_row)
            await session.commit()
        with pytest.raises(AppError) as missing_scope:
            await bootstrap.private(
                employee=employee,
                company_id=COMPANY_ID,
                plugin_id=PLUGIN_ID,
                route_path="/private",
            )
        assert missing_scope.value.code == "PLUGIN_BOOTSTRAP_SCOPE_UNAVAILABLE"
        public_without_binding = await bootstrap.public(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            route_path="/",
        )
        assert public_without_binding.scope.cabinet_id is None
        assert public_without_binding.scope.project_id is None
        # Recreate the exact row before testing cross-tenant access; querying a
        # deleted binding would correctly return None rather than a mismatch.
        await ports.installation_scopes.ensure_from_source(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            plugin_instance_id=receipt.plugin_instance_id,
        )
        with pytest.raises(AppError) as mismatch:
            await ports.installation_scopes.get(
                company_id="co_other",
                plugin_instance_id=receipt.plugin_instance_id,
            )
        assert mismatch.value.code == "PLUGIN_BOOTSTRAP_SCOPE_MISMATCH"
        with pytest.raises(AppError) as legacy_denied:
            await ports.installation_scopes.ensure_from_source(
                company_id=COMPANY_ID,
                plugin_id=PLUGIN_ID,
                plugin_instance_id="legacy-random-instance",
            )
        assert legacy_denied.value.code == "PLUGIN_INSTALLATION_SCOPE_UNAVAILABLE"

        # Trusted deterministic source recreates only the exact binding; revoke and
        # re-grant retain it without a second id provider or tenant default.
        restored = await ports.installation_scopes.ensure_from_source(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            plugin_instance_id=receipt.plugin_instance_id,
        )
        assert restored.company_id == COMPANY_ID
        assert restored.cabinet_id == cabinet_id
        assert restored.project_id == project_id
        revoked = await admin.revoke(company_id=COMPANY_ID, plugin_id=PLUGIN_ID)
        assert revoked.status is GrantStatus.REVOKED
        after_revoke = await ports.installation_scopes.get(
            company_id=COMPANY_ID,
            plugin_instance_id=receipt.plugin_instance_id,
        )
        assert after_revoke == restored
        # The consumed approval proves the previously approved installation of
        # this exact artifact. Re-grant never resets it or substitutes an update.
        regranted = await admin.grant(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            manifest_version=VERSION,
            artifact_digest=fixture.bundle_digest,
            capabilities=_capabilities(),
        )
        assert regranted.grant.status is GrantStatus.ACTIVE
        assert regranted.grant.manifest_version == VERSION
        assert regranted.grant.artifact_digest == fixture.bundle_digest
        after_regrant = await ports.installation_scopes.get(
            company_id=COMPANY_ID,
            plugin_instance_id=receipt.plugin_instance_id,
        )
        assert after_regrant == restored


@pytest.mark.asyncio
async def test_signed_prepublication_scopes_match_initial_instance_and_secret_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with composition_world(monkeypatch, source=True, ci=True, platform=True) as world:
        source = replace(
            ready_source(),
            cabinet_id="cab_prepublication",
            project_id="pr_prepublication",
        )
        expected_instance_id = plugin_instance_id_for(COMPANY_ID, PLUGIN_ID)
        assert source.plugin_instance_id == expected_instance_id
        pod_scope = {
            "plugin_instance_id": expected_instance_id,
            "project_id": "project_prepublication",
            "job_id": "job_prepublication",
        }
        secret_ref = (
            "vault://ai_keys/plugins/capabilities/secrets/"
            f"{COMPANY_ID}/{PLUGIN_ID}/{expected_instance_id}/api-key"
        )
        fixture = publication_fixture(
            source,
            manifest_patch={
                "pods": {"allowlist": [pod_scope]},
                "secret_delivery": {
                    "mode": "k8s_secret",
                    "allowlist": [secret_ref],
                },
            },
        )

        # The author builds and validates the exact signed scopes before the
        # publication service or initial grant has produced an installation.
        typed_manifest = validate_manifest(json.loads(fixture.canonical_bytes))
        assert typed_manifest.pods is not None
        assert typed_manifest.pods.allowlist[0].plugin_instance_id == expected_instance_id
        assert typed_manifest.pods.allowlist[0].project_id == pod_scope["project_id"]
        assert typed_manifest.pods.allowlist[0].job_id == pod_scope["job_id"]
        assert typed_manifest.secret_delivery is not None
        assert typed_manifest.secret_delivery.allowlist == (secret_ref,)
        validate_secret_delivery_ref(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            plugin_instance_id=expected_instance_id,
            secret_ref=secret_ref,
        )

        register_publication(fixture, world.ghcr)
        secret_value = "prepublication-integration-test-only"
        world.vault.seed(secret_ref, secret_value)
        ci = getattr(world.app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
        admin = world.app.state.plugin_admin_service
        async with world.relational.session_factory() as session:
            session.add(
                CompanyRow(
                    id=COMPANY_ID,
                    name="Prepublication Company",
                    login_slug="prepublication-company",
                    status="active",
                )
            )
            await session.flush()
            session.add(
                CabinetInstanceRow(
                    id="cab_prepublication",
                    name="Prepublication Cabinet",
                    schema_name="cab_prepublication",
                    company_id=COMPANY_ID,
                    status="active",
                )
            )
            await session.flush()
            session.add(
                ProjectRow(
                    id="pr_prepublication",
                    company_id=COMPANY_ID,
                    cabinet_id="cab_prepublication",
                    name="Prepublication Project",
                    slug="prepublication-project",
                    status="active",
                    workspace_key="ws-prepublication",
                    container_ref="prepublication-project",
                )
            )
            await session.commit()
        await ci._sources.create(source)
        approval = await admin.approve_publication(
            actor_id="platform-admin-prepublication-scope",
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            manifest_version=VERSION,
            artifact_digest=fixture.bundle_digest,
            capabilities=_capabilities(),
        )
        assert approval.created is True

        published = await ci.publish(
            fixture.command,
            oidc_token=world.jwks.token(source),
        )
        assert published.status == "PUBLISHED", published.code
        artifact = await ci._registry._store.by_digest_async(fixture.bundle_digest)
        assert artifact is not None
        assert artifact.manifest["pods"]["allowlist"] == [pod_scope]
        assert artifact.manifest["secret_delivery"]["allowlist"] == [secret_ref]

        await world.asyncpg.open()
        receipt = await admin.grant(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            manifest_version=VERSION,
            artifact_digest=fixture.bundle_digest,
            capabilities=_capabilities(),
        )
        assert receipt.replayed is False
        assert receipt.plugin_instance_id == expected_instance_id
        assert receipt.grant.plugin_instance_id == expected_instance_id

        # The real pre-delivery path accepted the precomputed ref and projected
        # its value under the same installation; replay changes neither id nor pin.
        projected_name = plugin_resource_name(
            PLUGIN_ID,
            receipt.plugin_instance_id,
            prefix=f"plugin-{DELIVERY_SECRET_PREFIX}",
        )
        projected = world.k8s.secret(projected_name)
        delivery_key = "SECRET_" + hashlib.sha256(
            secret_ref.encode("utf-8")
        ).hexdigest().upper()
        assert base64.b64decode(
            projected["data"][delivery_key]
        ).decode("utf-8") == secret_value
        replay = await admin.grant(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            manifest_version=VERSION,
            artifact_digest=fixture.bundle_digest,
            capabilities=_capabilities(),
        )
        assert replay.replayed is True
        assert replay.plugin_instance_id == expected_instance_id
        assert replay.grant.grant_id == receipt.grant.grant_id


@pytest.mark.asyncio
async def test_grant_without_exact_prior_approval_never_publishes_or_activates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with composition_world(monkeypatch, source=True, ci=True, platform=True) as world:
        source = ready_source()
        fixture = publication_fixture(source)
        register_publication(fixture, world.ghcr)
        ci = getattr(world.app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
        await ci._sources.create(source)
        result = await ci.publish(fixture.command, oidc_token=world.jwks.token(source))
        assert result.status == "QUARANTINED"
        assert result.code == "APPROVAL_REQUIRED"
        assert await ci._registry._store.by_digest_async(fixture.bundle_digest) is None
        assert await app_grant_absent(world)


@pytest.mark.asyncio
async def test_pre_active_webhook_resolver_rejects_missing_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with composition_world(monkeypatch, source=True, ci=True, platform=True) as world:
        fixture = publication_fixture(ready_source())
        ports = world.app.state.plugin_admin_ports
        async with world.relational.session_factory() as session:
            session.add(
                CompanyRow(
                    id=COMPANY_ID,
                    name="Acceptance Company",
                    login_slug="acceptance-company",
                    status="active",
                )
            )
            # The instance FK requires a real artifact anchor, but this negative
            # path must still have neither a publication approval nor a grant.
            session.add(
                PluginArtifactRow(
                    id="part_missing_approval",
                    plugin_id=PLUGIN_ID,
                    version=VERSION,
                    oci_digest=fixture.bundle_digest,
                    manifest={"plugin_id": PLUGIN_ID, "version": VERSION},
                    app_version_projection={
                        "appId": PLUGIN_ID.replace("_", "-"),
                        "version": VERSION,
                    },
                    status="published",
                    published_at=NOW,
                )
            )
            await session.commit()
        instance = await ports.instance_store.create(
            instance=PluginInstanceRecord(
                plugin_instance_id="inst_missing_approval",
                company_id=COMPANY_ID,
                plugin_id=PLUGIN_ID,
                manifest_version=VERSION,
                artifact_digest=fixture.bundle_digest,
                status=PluginInstanceStatus.INSTALLED,
                tenant_db_name=f"tenant_{COMPANY_ID}",
                bucket_name=f"tenant-{COMPANY_ID}",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        key = webhook_secret_key(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            plugin_instance_id=instance.plugin_instance_id,
        )
        secret_ref = await world.vault.put(key=key, value="pre-active-secret-test-only")
        resolver = ports.activation.deployments.secret_projection._webhook_resolver
        with pytest.raises(LookupError):
            await resolver.resolve(
                company_id=COMPANY_ID,
                plugin_id=PLUGIN_ID,
                plugin_instance_id=instance.plugin_instance_id,
                secret_ref=secret_ref,
            )


async def app_grant_absent(world) -> bool:
    return await world.app.state.plugin_admin_ports.grant_store.get(
        company_id=COMPANY_ID,
        plugin_id=PLUGIN_ID,
    ) is None

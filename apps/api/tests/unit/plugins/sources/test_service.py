"""Unit tests for ModuleSourceService; intentionally not run during freeze."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from types import SimpleNamespace
from typing import Any, cast

import pytest

from prodavan.application.plugins.sources.repository import (
    ModuleSourceConflictError as RepositoryConflictError,
)
from prodavan.application.plugins.sources.service import (
    ModuleSourceConflictError,
    ModuleSourceLockRegistry,
    ModuleSourceNotFoundError,
    ModuleSourceProvisioningConfig,
    ModuleSourceProvisioningInProgressError,
    ModuleSourceService,
    ModuleSourceUnavailableError,
    ModuleSourceValidationError,
)
from prodavan.application.plugins.sources.types import (
    ModuleSource,
    ModuleSourceProvisioningReservation,
    ModuleSourceReservationResult,
    ModuleSourceReservationStatus,
    ModuleSourceStatus,
    ModuleSourceTrustConfig,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.plugins.helpers import plugin_instance_id_for
from prodavan.infrastructure.plugins.sources.errors import GitHubAPIError, GitHubConflictError
from prodavan.infrastructure.plugins.sources.models import (
    BranchProtectionPolicy,
    CreatedRepository,
    ManagedOrganization,
    RepositoryMetadata,
    RepositoryTemplate,
)

from ..domain._fixtures import valid_manifest_dict

COMPANY = "co_123"
PLUGIN = "mod_equipment"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
WORKFLOW = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40
CREDENTIAL_REF = "vault://github_app/co_123/mod_equipment/installation"


def _manifest(**changes: Any) -> dict[str, Any]:
    manifest = valid_manifest_dict()
    manifest.update(
        plugin_id=PLUGIN,
        egress_ref=f"grant:{COMPANY}/{PLUGIN}/inst_source",
    )
    manifest.update(changes)
    return manifest


def _secret_ref(instance_id: str, name: str) -> str:
    return (
        "vault://ai_keys/plugins/capabilities/secrets/"
        f"{COMPANY}/{PLUGIN}/{instance_id}/{name}"
    )


def _valid_instance_scopes() -> dict[str, Any]:
    instance_id = plugin_instance_id_for(COMPANY, PLUGIN)
    return {
        "pods": {
            "allowlist": [
                {
                    "plugin_instance_id": instance_id,
                    "project_id": "project_a",
                    "job_id": "job_a",
                },
                {
                    "plugin_instance_id": instance_id,
                    "project_id": "project_b",
                    "job_id": "job_b",
                },
            ]
        },
        "secret_delivery": {
            "mode": "k8s_secret",
            "allowlist": [
                _secret_ref(instance_id, "api-key"),
                _secret_ref(instance_id, "other-key"),
            ],
        },
    }


class FakeEntitlement:
    def __init__(self) -> None:
        self.denied: AppError | None = None
        self.calls: list[str] = []

    async def require_company_actor(
        self,
        principal: Principal,
        company_id: str,
        *,
        employee: object,
    ) -> object:
        self.calls.append(company_id)
        if self.denied is not None:
            raise self.denied
        return SimpleNamespace(id=company_id)


class FakeProjectAccess:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.denied: AppError | None = None
        self.company_id = COMPANY
        self.cabinet_id = "cab_123"

    async def require_access(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: Any,
        write: bool = False,
        allow_paused: bool = False,
        allow_deleted: bool = False,
        skip_visibility: bool = False,
    ) -> object:
        self.calls.append((self.cabinet_id, project_id, "principal"))
        if self.denied is not None:
            raise self.denied
        return SimpleNamespace(
            id=project_id,
            company_id=self.company_id,
            cabinet_id=self.cabinet_id,
        )


class FakeProvider:
    def __init__(
        self, config: ModuleSourceProvisioningConfig, events: list[str]
    ) -> None:
        self._config = config
        self._events = events
        self.fail_create: Exception | None = None
        self.fail_protect: Exception | None = None
        self.fail_metadata: Exception | None = None
        self.repo_id = 7_654_321
        self.create_requests: list[object] = []
        self.protect_calls: list[tuple[RepositoryMetadata, BranchProtectionPolicy]] = []
        self.repositories: dict[str, RepositoryMetadata] = {}

    async def create_private_repository(self, request: object) -> CreatedRepository:
        self._events.append("create")
        self.create_requests.append(request)
        if self.fail_create is not None:
            raise self.fail_create
        organization = cast(Any, request).organization
        name = cast(Any, request).name
        created = CreatedRepository(
            repo_id=self.repo_id,
            full_name=f"{organization}/{name}",
            default_branch=self._config.default_branch,
            private=True,
            template=self._config.organization.template,
        )
        self.repositories[created.full_name] = created
        return created

    async def protect_default_branch(
        self, repository: RepositoryMetadata, policy: BranchProtectionPolicy
    ) -> None:
        self._events.append("protect")
        self.protect_calls.append((repository, policy))
        if self.fail_protect is not None:
            raise self.fail_protect

    async def get_repository_metadata(self, full_name: str) -> RepositoryMetadata:
        self._events.append("metadata")
        if self.fail_metadata is not None:
            raise self.fail_metadata
        try:
            return self.repositories[full_name]
        except KeyError:
            raise GitHubAPIError("get_repository_metadata", 404) from None

    async def aclose(self) -> None:
        return None


class FakeRepository:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.sources: dict[str, ModuleSource] = {}
        self.reservations: dict[str, ModuleSourceProvisioningReservation] = {}

    async def get_by_id(self, source_id: str) -> ModuleSource | None:
        return self.sources.get(source_id)

    async def get_by_company_plugin(
        self, *, company_id: str, plugin_id: str
    ) -> ModuleSource | None:
        return next(
            (
                source
                for source in self.sources.values()
                if source.company_id == company_id and source.plugin_id == plugin_id
            ),
            None,
        )

    async def get_by_provider_repo(
        self, *, provider: str, repo_id: int
    ) -> ModuleSource | None:
        return next(
            (
                source
                for source in self.sources.values()
                if source.provider == provider and source.repo_id == repo_id
            ),
            None,
        )

    async def reserve_provisioning(
        self, reservation: ModuleSourceProvisioningReservation
    ) -> ModuleSourceReservationResult:
        existing = next(
            (
                candidate
                for candidate in self.reservations.values()
                if (
                    candidate.company_id == reservation.company_id
                    and candidate.plugin_id == reservation.plugin_id
                )
                or (
                    candidate.provider == reservation.provider
                    and candidate.full_name == reservation.full_name
                )
            ),
            None,
        )
        if existing is not None:
            self._events.append("reserve:existing")
            return ModuleSourceReservationResult(
                reservation=existing, acquired=False
            )
        self._events.append("reserve:acquired")
        self.reservations[reservation.id] = reservation
        return ModuleSourceReservationResult(reservation=reservation, acquired=True)

    async def retry_provisioning(
        self,
        reservation: ModuleSourceProvisioningReservation,
        *,
        attempt_id: str,
    ) -> ModuleSourceReservationResult:
        current = self.reservations[reservation.id]
        acquired = (
            current.attempt_id == reservation.attempt_id
            and current.status is ModuleSourceReservationStatus.PROVISIONING
            and not current.create_in_progress
        )
        if acquired:
            current = replace(
                current,
                attempt_id=attempt_id,
                create_in_progress=True,
                updated_at=NOW + timedelta(minutes=1),
            )
            self.reservations[current.id] = current
        self._events.append("retry:acquired" if acquired else "retry:existing")
        return ModuleSourceReservationResult(
            reservation=current, acquired=acquired
        )

    async def release_provisioning_attempt(
        self, reservation_id: str, *, attempt_id: str
    ) -> None:
        current = self.reservations[reservation_id]
        if (
            current.attempt_id != attempt_id
            or current.status is not ModuleSourceReservationStatus.PROVISIONING
            or not current.create_in_progress
        ):
            return
        self._events.append("release")
        self.reservations[reservation_id] = replace(
            current,
            create_in_progress=False,
            updated_at=NOW + timedelta(minutes=1),
        )

    async def create(self, source: ModuleSource) -> ModuleSource:
        self._events.append(f"save:{source.status.value}")
        if await self.get_by_company_plugin(
            company_id=source.company_id, plugin_id=source.plugin_id
        ) or await self.get_by_provider_repo(
            provider=source.provider, repo_id=source.repo_id
        ):
            raise RepositoryConflictError("unique source identity")
        self.sources[source.id] = source
        return source

    async def bind_scope(self, source: ModuleSource) -> ModuleSource:
        self._events.append(
            f"scope:{source.cabinet_id}/{source.project_id}"
        )
        current = self.sources[source.id]
        if (
            current.cabinet_id is not None
            and (
                current.cabinet_id != source.cabinet_id
                or current.project_id != source.project_id
            )
        ):
            raise RepositoryConflictError("scope immutable")
        self.sources[source.id] = replace(
            source, updated_at=NOW + timedelta(minutes=1)
        )
        return self.sources[source.id]

    async def set_status(
        self, source_id: str, status: ModuleSourceStatus
    ) -> ModuleSource | None:
        self._events.append(f"status:{status.value}")
        source = self.sources.get(source_id)
        if source is None:
            return None
        updated = replace(source, status=status, updated_at=NOW + timedelta(minutes=1))
        self.sources[source_id] = updated
        return updated

    async def complete_provisioning(self, source_id: str) -> ModuleSource | None:
        self._events.append("complete:ready")
        source = self.sources.get(source_id)
        if source is None:
            return None
        updated = replace(
            source,
            status=ModuleSourceStatus.READY,
            updated_at=NOW + timedelta(minutes=1),
        )
        self.sources[source_id] = updated
        reservation = next(
            (
                candidate
                for candidate in self.reservations.values()
                if candidate.company_id == source.company_id
                and candidate.plugin_id == source.plugin_id
            ),
            None,
        )
        if reservation is not None:
            self.reservations[reservation.id] = replace(
                reservation,
                status=ModuleSourceReservationStatus.READY,
                create_in_progress=False,
                updated_at=NOW + timedelta(minutes=1),
            )
        return updated

    async def delete(self, source_id: str) -> bool:
        return self.sources.pop(source_id, None) is not None


class Harness:
    def __init__(
        self,
        *,
        workflow: str = WORKFLOW,
        credential_ref: str = CREDENTIAL_REF,
    ) -> None:
        self.events: list[str] = []
        template = RepositoryTemplate(
            owner="managed-plugins",
            name="private-plugin-template",
            agents_revision="a" * 40,
            security_revision="b" * 40,
        )
        self.config = ModuleSourceProvisioningConfig(
            organization=ManagedOrganization(
                name="managed-plugins", installation_id=42, template=template
            ),
            trust=ModuleSourceTrustConfig(
                installation_credential_ref=credential_ref,
                trusted_workflow_ref=workflow,
            ),
            branch_protection=BranchProtectionPolicy(
                required_status_checks=("plugin/verify", "plugin/supply-chain")
            ),
        )
        self.provider = FakeProvider(self.config, self.events)
        self.repository = FakeRepository(self.events)
        self.entitlement = FakeEntitlement()
        self.project_access = FakeProjectAccess()
        ids = count(1)
        self.service = ModuleSourceService(
            provider=self.provider,
            repository=self.repository,
            entitlement=self.entitlement,
            project_access=self.project_access,
            config=self.config,
            clock=lambda: NOW,
            id_factory=lambda: f"psrc_{next(ids):032x}",
            lock_registry=ModuleSourceLockRegistry(),
        )


async def _create(
    harness: Harness,
    *,
    company_id: str = COMPANY,
    plugin_id: str = PLUGIN,
    manifest: dict[str, Any] | None = None,
):
    return await harness.service.create_from_template(
        company_id=company_id,
        plugin_id=plugin_id,
        manifest=_manifest() if manifest is None else manifest,
        principal=cast(Principal, object()),
        employee=None,
    )


async def test_bind_scope_records_exact_scope_and_replays_immutable() -> None:
    harness = Harness()
    await _create(harness)

    first = await harness.service.bind_scope(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        cabinet_id="cab_123",
        project_id="pr_123",
        principal=cast(Principal, object()),
        employee=None,
    )
    assert first is not None
    assert first.cabinet_id == "cab_123"
    assert first.project_id == "pr_123"
    assert first.updated_at == NOW + timedelta(minutes=1)
    assert harness.project_access.calls == [("cab_123", "pr_123", "principal")]

    replay = await harness.service.bind_scope(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        cabinet_id="cab_123",
        project_id="pr_123",
        principal=cast(Principal, object()),
        employee=None,
    )
    assert replay is not None
    assert replay.cabinet_id == "cab_123"
    assert replay.project_id == "pr_123"
    assert replay.updated_at == NOW + timedelta(minutes=1)

    with pytest.raises(ModuleSourceConflictError) as mismatch:
        await harness.service.bind_scope(
            company_id=COMPANY,
            plugin_id=PLUGIN,
            cabinet_id="cab_123",
            project_id="pr_other",
            principal=cast(Principal, object()),
            employee=None,
        )
    assert mismatch.value.status == 409
    assert mismatch.value.code == "MODULE_SOURCE_SCOPE_CONFLICT"


async def test_bind_scope_rejects_cross_tenant_project_and_preserves_legacy_null() -> None:
    harness = Harness()
    await _create(harness)
    harness.project_access.company_id = "co_other"

    with pytest.raises(ModuleSourceConflictError) as caught:
        await harness.service.bind_scope(
            company_id=COMPANY,
            plugin_id=PLUGIN,
            cabinet_id="cab_123",
            project_id="pr_123",
            principal=cast(Principal, object()),
            employee=None,
        )
    assert caught.value.status == 409
    assert caught.value.code == "MODULE_SOURCE_SCOPE_MISMATCH"
    source = await harness.repository.get_by_company_plugin(
        company_id=COMPANY, plugin_id=PLUGIN
    )
    assert source is not None
    assert source.cabinet_id is None
    assert source.project_id is None


async def test_provisioning_source_cannot_receive_scope() -> None:
    harness = Harness()
    created = await _create(harness)
    await harness.repository.set_status(
        created.source.id, ModuleSourceStatus.PROVISIONING
    )
    with pytest.raises(ModuleSourceConflictError) as caught:
        await harness.service.bind_scope(
            company_id=COMPANY,
            plugin_id=PLUGIN,
            cabinet_id="cab_123",
            project_id="pr_123",
            principal=cast(Principal, object()),
            employee=None,
        )
    assert caught.value.code == "MODULE_SOURCE_NOT_READY"
    assert harness.project_access.calls == []


def _stored_source(
    *,
    source_id: str = "psrc_existing",
    company_id: str = COMPANY,
    plugin_id: str = PLUGIN,
    repo_id: int = 7_654_321,
    full_name: str = "managed-plugins/existing",
    status: ModuleSourceStatus = ModuleSourceStatus.READY,
) -> ModuleSource:
    return ModuleSource(
        id=source_id,
        company_id=company_id,
        plugin_id=plugin_id,
        repo_id=repo_id,
        full_name=full_name,
        private=True,
        default_branch="main",
        installation_credential_ref=CREDENTIAL_REF,
        trusted_workflow_ref=WORKFLOW,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


async def _seed_reservation(
    harness: Harness, **changes: Any
) -> ModuleSourceProvisioningReservation:
    reservation = harness.service._new_reservation(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        full_name=(
            "managed-plugins/"
            + harness.service._repository_name(company_id=COMPANY, plugin_id=PLUGIN)
        ),
    )
    if changes:
        reservation = replace(reservation, **changes)
    reserved = await harness.repository.reserve_provisioning(reservation)
    assert reserved.acquired is True
    harness.events.clear()
    return reserved.reservation


def _confirm_provider(
    harness: Harness,
    reservation: ModuleSourceProvisioningReservation,
    **changes: Any,
) -> RepositoryMetadata:
    metadata = RepositoryMetadata(
        repo_id=harness.provider.repo_id,
        full_name=reservation.full_name,
        default_branch=reservation.default_branch,
        private=True,
    )
    if changes:
        metadata = replace(metadata, **changes)
    harness.provider.repositories[metadata.full_name] = metadata
    return metadata


async def test_create_save_protect_complete_ready_exact_call_order() -> None:
    harness = Harness()
    result = await _create(harness)

    assert result.created is True
    assert result.source.status is ModuleSourceStatus.READY
    assert result.source.private is True
    assert result.source.trusted_workflow_ref == WORKFLOW
    assert harness.events == [
        "reserve:acquired",
        "create",
        "save:provisioning",
        "protect",
        "complete:ready",
    ]
    request = cast(Any, harness.provider.create_requests[0])
    assert request.organization == "managed-plugins"
    assert request.name == result.source.full_name.split("/", 1)[1]
    assert harness.provider.protect_calls[0][1] == harness.config.branch_protection


async def test_valid_deterministic_instance_scopes_pass_before_provisioning() -> None:
    harness = Harness()
    result = await _create(
        harness,
        manifest=_manifest(**_valid_instance_scopes()),
    )

    assert result.created is True
    assert result.source.status is ModuleSourceStatus.READY
    assert result.source.plugin_instance_id == plugin_instance_id_for(COMPANY, PLUGIN)
    assert len(harness.provider.create_requests) == 1


async def test_absent_instance_scopes_remain_valid_and_grant_no_access() -> None:
    harness = Harness()
    manifest = _manifest()
    assert "pods" not in manifest
    assert "secret_delivery" not in manifest

    result = await _create(harness, manifest=manifest)

    assert result.created is True
    assert result.source.status is ModuleSourceStatus.READY
    assert len(harness.provider.create_requests) == 1


@pytest.mark.parametrize("invalid_scope", ["pods", "secret_delivery"])
async def test_foreign_deterministic_instance_scope_is_rejected_before_mutation(
    invalid_scope: str,
) -> None:
    harness = Harness()
    scopes = _valid_instance_scopes()
    foreign_instance = plugin_instance_id_for("co_other", PLUGIN)
    if invalid_scope == "pods":
        # A valid first entry proves that every allowlist item is checked.
        scopes["pods"]["allowlist"][1]["plugin_instance_id"] = foreign_instance
    else:
        scopes["secret_delivery"]["allowlist"][1] = _secret_ref(
            foreign_instance,
            "other-key",
        )

    with pytest.raises(ModuleSourceValidationError) as caught:
        await _create(harness, manifest=_manifest(**scopes))

    assert caught.value.status == 422
    assert caught.value.code == "MODULE_SOURCE_INSTANCE_SCOPE_MISMATCH"
    assert harness.events == []
    assert harness.provider.create_requests == []
    assert harness.provider.protect_calls == []
    assert harness.repository.reservations == {}
    assert harness.repository.sources == {}


async def test_ready_repeat_returns_same_source_without_second_repository() -> None:
    harness = Harness()
    first = await _create(harness)
    second = await _create(harness)

    assert second.created is False
    assert second.source is first.source
    assert harness.events == [
        "reserve:acquired",
        "create",
        "save:provisioning",
        "protect",
        "complete:ready",
    ]
    assert len(harness.provider.create_requests) == 1


async def test_provider_create_failure_is_sanitized_and_never_ready() -> None:
    harness = Harness()
    harness.provider.fail_create = RuntimeError("ghs_secret_create_token")

    with pytest.raises(ModuleSourceUnavailableError) as caught:
        await _create(harness)

    assert caught.value.status == 503
    assert caught.value.code == "MODULE_SOURCE_REPOSITORY_CREATE_FAILED"
    assert "ghs_secret_create_token" not in str(caught.value)
    assert harness.events == ["reserve:acquired", "create", "release"]
    assert not harness.repository.sources
    reservation = next(iter(harness.repository.reservations.values()))
    assert reservation.status is ModuleSourceReservationStatus.PROVISIONING
    assert reservation.create_in_progress is False


async def test_provider_conflict_without_metadata_stays_retryable() -> None:
    harness = Harness()
    harness.provider.fail_create = GitHubConflictError("create_private_repository")

    with pytest.raises(ModuleSourceConflictError) as caught:
        await _create(harness)

    assert caught.value.status == 409
    assert caught.value.code == "MODULE_SOURCE_REPOSITORY_CONFLICT"
    assert not harness.repository.sources
    reservation = next(iter(harness.repository.reservations.values()))
    assert reservation.status is ModuleSourceReservationStatus.PROVISIONING
    assert reservation.create_in_progress is False
    assert harness.events == [
        "reserve:acquired",
        "create",
        "metadata",
        "release",
    ]


async def test_protect_failure_records_provisioning_then_resume_without_new_repo() -> None:
    harness = Harness()
    harness.provider.fail_protect = RuntimeError("ghs_secret_protect_token")

    with pytest.raises(ModuleSourceUnavailableError) as caught:
        await _create(harness)

    assert caught.value.status == 503
    assert caught.value.code == "MODULE_SOURCE_PROTECTION_FAILED"
    assert "ghs_secret_protect_token" not in str(caught.value)
    assert harness.events == [
        "reserve:acquired",
        "create",
        "save:provisioning",
        "protect",
    ]
    stored = next(iter(harness.repository.sources.values()))
    assert stored.status is ModuleSourceStatus.PROVISIONING
    reservation = next(iter(harness.repository.reservations.values()))
    assert reservation.status is ModuleSourceReservationStatus.PROVISIONING

    harness.provider.fail_protect = None
    resumed = await _create(harness)

    assert resumed.created is False
    assert resumed.source.id == stored.id
    assert resumed.source.status is ModuleSourceStatus.READY
    assert harness.events == [
        "reserve:acquired",
        "create",
        "save:provisioning",
        "protect",
        "metadata",
        "protect",
        "complete:ready",
    ]
    assert len(harness.provider.create_requests) == 1


async def test_entitlement_denied_precedes_manifest_and_provider_work() -> None:
    harness = Harness()
    harness.entitlement.denied = AppError(
        code="FORBIDDEN", title="Forbidden", status=403, detail="denied"
    )

    with pytest.raises(AppError) as caught:
        await _create(harness, manifest={"plugin_id": "not-even-a-manifest"})

    assert caught.value.status == 403
    assert harness.events == []


@pytest.mark.parametrize(
    ("workflow", "credential_ref"),
    [
        ("ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@main", CREDENTIAL_REF),
        (WORKFLOW, "ghp_inline_secret_value"),
    ],
)
async def test_invalid_workflow_or_raw_secret_config_fails_before_provider(
    workflow: str, credential_ref: str
) -> None:
    harness = Harness(workflow=workflow, credential_ref=credential_ref)

    with pytest.raises(ModuleSourceUnavailableError) as caught:
        await _create(harness)

    assert caught.value.status == 503
    assert caught.value.code == "MODULE_SOURCE_CONFIG_INVALID"
    assert harness.events == []


async def test_manifest_plugin_mismatch_is_422_without_provider_work() -> None:
    harness = Harness()
    with pytest.raises(ModuleSourceValidationError) as caught:
        await _create(harness, manifest=_manifest(plugin_id="another_plugin"))

    assert caught.value.status == 422
    assert caught.value.code == "MODULE_SOURCE_PLUGIN_MISMATCH"
    assert harness.events == []


async def test_unknown_manifest_field_is_strict_422() -> None:
    harness = Harness()
    with pytest.raises(ModuleSourceValidationError) as caught:
        await _create(harness, manifest=_manifest(secret_token="must-not-be-accepted"))

    assert caught.value.status == 422
    assert caught.value.code == "MODULE_SOURCE_MANIFEST_INVALID"
    assert "must-not-be-accepted" not in str(caught.value)
    assert harness.events == []


async def test_existing_different_immutable_binding_is_typed_conflict() -> None:
    harness = Harness()
    await harness.repository.create(_stored_source(full_name="managed-plugins/different"))
    harness.events.clear()

    with pytest.raises(ModuleSourceConflictError) as caught:
        await _create(harness)

    assert caught.value.status == 409
    assert caught.value.code == "MODULE_SOURCE_IMMUTABLE_BINDING_CONFLICT"
    assert harness.events == []


async def test_provider_repo_id_bound_to_another_identity_conflicts_before_protect() -> None:
    harness = Harness()
    await harness.repository.create(
        _stored_source(
            source_id="psrc_other",
            company_id="co_other",
            plugin_id="other_plugin",
            full_name="managed-plugins/other",
        )
    )
    harness.events.clear()

    with pytest.raises(ModuleSourceConflictError) as caught:
        await _create(harness)

    assert caught.value.status == 409
    assert caught.value.code == "MODULE_SOURCE_IMMUTABLE_BINDING_CONFLICT"
    assert harness.events == ["reserve:acquired", "create"]
    assert len(harness.repository.sources) == 1


async def test_get_status_is_company_scoped_and_typed_not_found() -> None:
    harness = Harness()
    created = await _create(harness)
    found = await harness.service.get_status(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        principal=cast(Principal, object()),
        employee=None,
    )
    assert found is created.source

    with pytest.raises(ModuleSourceNotFoundError) as caught:
        await harness.service.get_status(
            company_id=COMPANY,
            plugin_id="missing_plugin",
            principal=cast(Principal, object()),
            employee=None,
        )
    assert caught.value.status == 404


async def test_second_worker_with_active_create_gets_typed_in_progress() -> None:
    harness = Harness()
    await _seed_reservation(harness)

    with pytest.raises(ModuleSourceProvisioningInProgressError) as caught:
        await _create(harness)

    assert caught.value.status == 409
    assert caught.value.code == "MODULE_SOURCE_PROVISIONING_IN_PROGRESS"
    assert harness.provider.create_requests == []
    assert harness.provider.protect_calls == []
    assert not harness.repository.sources
    assert harness.events == ["reserve:existing", "metadata"]


async def test_acquired_false_reconciles_crash_after_provider_success_without_create() -> None:
    harness = Harness()
    reservation = await _seed_reservation(harness)
    confirmed = _confirm_provider(harness, reservation)

    resumed = await _create(harness)

    assert resumed.created is False
    assert resumed.source.status is ModuleSourceStatus.READY
    assert resumed.source.repo_id == confirmed.repo_id
    assert resumed.source.full_name == reservation.full_name
    assert harness.provider.create_requests == []
    assert len(harness.provider.protect_calls) == 1
    assert harness.events == [
        "reserve:existing",
        "metadata",
        "save:provisioning",
        "protect",
        "complete:ready",
    ]
    stored_reservation = harness.repository.reservations[reservation.id]
    assert stored_reservation.status is ModuleSourceReservationStatus.READY
    assert stored_reservation.create_in_progress is False


async def test_failed_create_reservation_retries_with_one_fenced_attempt() -> None:
    harness = Harness()
    harness.provider.fail_create = RuntimeError("temporary create failure")
    with pytest.raises(ModuleSourceUnavailableError):
        await _create(harness)
    original = next(iter(harness.repository.reservations.values()))
    assert original.create_in_progress is False

    harness.provider.fail_create = None
    harness.events.clear()
    resumed = await _create(harness)

    assert resumed.source.status is ModuleSourceStatus.READY
    assert len(harness.provider.create_requests) == 2
    assert harness.events == [
        "reserve:existing",
        "metadata",
        "retry:acquired",
        "create",
        "save:provisioning",
        "protect",
        "complete:ready",
    ]
    current = harness.repository.reservations[original.id]
    assert current.attempt_id != original.attempt_id
    assert current.status is ModuleSourceReservationStatus.READY


async def test_unique_save_conflict_reconciles_existing_source_without_orphan() -> None:
    harness = Harness()

    async def conflicting_create(source: ModuleSource) -> ModuleSource:
        concurrent = replace(source, id="psrc_concurrent")
        harness.repository.sources[concurrent.id] = concurrent
        harness.events.append("save:conflict")
        raise RepositoryConflictError("concurrent unique insert")

    cast(Any, harness.repository).create = conflicting_create
    result = await _create(harness)

    assert result.source.status is ModuleSourceStatus.READY
    assert result.source.id == "psrc_concurrent"
    assert len(harness.provider.create_requests) == 1
    assert len(harness.provider.protect_calls) == 1
    assert len(harness.repository.sources) == 1
    reservation = next(iter(harness.repository.reservations.values()))
    assert reservation.status is ModuleSourceReservationStatus.READY


async def test_resume_does_not_protect_or_ready_without_confirmed_metadata() -> None:
    harness = Harness()
    expected = harness.service._new_reservation(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        full_name=(
            "managed-plugins/"
            + harness.service._repository_name(company_id=COMPANY, plugin_id=PLUGIN)
        ),
    )
    stored = _stored_source(
        full_name=expected.full_name,
        status=ModuleSourceStatus.PROVISIONING,
    )
    await harness.repository.create(stored)
    harness.events.clear()

    with pytest.raises(ModuleSourceUnavailableError) as caught:
        await _create(harness)

    assert caught.value.code == "MODULE_SOURCE_PROVIDER_METADATA_NOT_FOUND"
    assert harness.provider.create_requests == []
    assert harness.provider.protect_calls == []
    assert harness.repository.sources[stored.id].status is ModuleSourceStatus.PROVISIONING
    assert harness.events == ["metadata"]


@pytest.mark.parametrize(
    "changes",
    [
        {"full_name": "managed-plugins/different"},
        {"default_branch": "develop"},
        {
            "trusted_workflow_ref": (
                "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@"
                + "c" * 40
            )
        },
    ],
)
async def test_existing_reservation_with_different_binding_is_409(
    changes: dict[str, Any],
) -> None:
    harness = Harness()
    await _seed_reservation(harness, **changes)

    with pytest.raises(ModuleSourceConflictError) as caught:
        await _create(harness)

    assert caught.value.status == 409
    assert caught.value.code == "MODULE_SOURCE_IMMUTABLE_BINDING_CONFLICT"
    assert harness.provider.create_requests == []
    assert harness.provider.protect_calls == []
    assert harness.events == ["reserve:existing"]


async def test_retry_cas_lets_only_one_idle_worker_acquire_create() -> None:
    harness = Harness()
    reservation = await _seed_reservation(harness)
    released = replace(reservation, create_in_progress=False)
    harness.repository.reservations[reservation.id] = released

    first = await harness.repository.retry_provisioning(
        released, attempt_id="attempt-first"
    )
    second = await harness.repository.retry_provisioning(
        released, attempt_id="attempt-second"
    )

    assert first.acquired is True
    assert second.acquired is False
    assert second.reservation.attempt_id == "attempt-first"

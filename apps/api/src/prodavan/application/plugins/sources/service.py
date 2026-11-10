"""Company-scoped ModuleSource provisioning orchestration.

The service owns only source metadata. It does not create grants, publish
artifacts, deploy modules, or expose a second runtime registry. Trusted
organization/template, branch policy, workflow and credential references are
composition configuration; callers provide only identity and a strict manifest.
"""

from __future__ import annotations

import asyncio
import hashlib
import weakref
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from prodavan.application.plugins.capabilities.adapters.secrets import (
    validate_secret_delivery_ref,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.plugins.scopes import validate_plugin_instance_scopes
from prodavan.domain.plugins.types import PLUGIN_ID_PATTERN
from prodavan.domain.plugins.validation import PluginValidationError, validate_manifest
from prodavan.infrastructure.plugins.sources.errors import GitHubAPIError, GitHubConflictError
from prodavan.infrastructure.plugins.sources.models import (
    BranchProtectionPolicy,
    CreatedRepository,
    CreatePrivateRepositoryRequest,
    ManagedOrganization,
    RepositoryMetadata,
    validate_branch,
)
from prodavan.infrastructure.plugins.sources.ports import GitHubRepositorySourcePort

from .ports import ModuleSourceProvisioningRepositoryPort
from .repository import ModuleSourceConflictError as RepositoryConflictError
from .types import (
    ModuleSource,
    ModuleSourceProvisioningReservation,
    ModuleSourceReservationResult,
    ModuleSourceReservationStatus,
    ModuleSourceStatus,
    ModuleSourceTrustConfig,
)

if TYPE_CHECKING:
    from prodavan.infrastructure.persistence.models.identity import EmployeeRow


class ModuleSourceServiceError(AppError):
    """Base sanitized application error returned by source orchestration."""


class ModuleSourceValidationError(ModuleSourceServiceError):
    def __init__(self, *, code: str = "MODULE_SOURCE_INVALID", detail: str) -> None:
        super().__init__(code=code, title="Module source rejected", status=422, detail=detail)


class ModuleSourceConflictError(ModuleSourceServiceError):
    def __init__(self, *, code: str = "MODULE_SOURCE_CONFLICT", detail: str) -> None:
        super().__init__(code=code, title="Module source conflict", status=409, detail=detail)


class ModuleSourceProvisioningInProgressError(ModuleSourceServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="MODULE_SOURCE_PROVISIONING_IN_PROGRESS",
            title="Module source provisioning in progress",
            status=409,
            detail="another worker owns repository creation; retry without creating a new repository",
        )


class ModuleSourceNotFoundError(ModuleSourceServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="MODULE_SOURCE_NOT_FOUND",
            title="Module source not found",
            status=404,
            detail="No module source exists for this company and plugin",
        )


class ModuleSourceUnavailableError(ModuleSourceServiceError):
    def __init__(self, *, code: str, detail: str) -> None:
        super().__init__(code=code, title="Module source unavailable", status=503, detail=detail)


class CompanyIdentity(Protocol):
    id: str


class ModuleSourceProjectAccessPort(Protocol):
    async def require_access(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        write: bool = False,
        allow_paused: bool = False,
        allow_deleted: bool = False,
        skip_visibility: bool = False,
    ) -> Any: ...


class ModuleSourceEntitlementPort(Protocol):
    """Structural port implemented by EntitlementService."""

    async def require_company_actor(
        self,
        principal: Principal,
        company_id: str,
        *,
        employee: EmployeeRow | None,
    ) -> CompanyIdentity: ...


@dataclass(frozen=True, slots=True)
class ModuleSourceProvisioningConfig:
    """Trusted, non-request provisioning configuration."""

    organization: ManagedOrganization
    trust: ModuleSourceTrustConfig
    branch_protection: BranchProtectionPolicy
    default_branch: str = "main"

    def __post_init__(self) -> None:
        if not isinstance(self.organization, ManagedOrganization):
            raise TypeError("organization must be managed source configuration")
        if not isinstance(self.trust, ModuleSourceTrustConfig):
            raise TypeError("trust must be ModuleSourceTrustConfig")
        if not isinstance(self.branch_protection, BranchProtectionPolicy):
            raise TypeError("branch_protection must be BranchProtectionPolicy")


@dataclass(frozen=True, slots=True)
class ModuleSourceProvisionResult:
    source: ModuleSource
    created: bool


class ModuleSourceLockRegistry:
    """Serialize same-identity provisioning inside one API process.

    The database uniqueness constraints remain the cross-process fail-closed
    boundary. Weak values avoid retaining one lock per company/plugin forever.
    """

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
        async with lock:
            yield


_SHARED_LOCKS = ModuleSourceLockRegistry()


class ModuleSourceService:
    """Provision and resume exactly one private source per company/plugin."""

    def __init__(
        self,
        *,
        provider: GitHubRepositorySourcePort,
        repository: ModuleSourceProvisioningRepositoryPort,
        entitlement: ModuleSourceEntitlementPort,
        config: ModuleSourceProvisioningConfig,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        lock_registry: ModuleSourceLockRegistry | None = None,
        project_access: ModuleSourceProjectAccessPort | None = None,
    ) -> None:
        if not isinstance(config, ModuleSourceProvisioningConfig):
            raise TypeError("config must be ModuleSourceProvisioningConfig")
        self._provider = provider
        self._repository = repository
        self._entitlement = entitlement
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"psrc_{uuid4().hex}")
        self._locks = lock_registry or _SHARED_LOCKS
        self._project_access = project_access

    async def create_from_template(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest: Mapping[str, Any],
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> ModuleSourceProvisionResult:
        """Reserve, create/reconcile, protect, then atomically mark READY.

        The SQL reservation is committed before provider HTTP. Only its acquired
        worker may call create; other workers either reconcile confirmed provider
        metadata or return a typed in-progress error, never a premature READY.
        """

        resolved_company_id = await self._authorize(
            company_id=company_id, principal=principal, employee=employee
        )
        self._validate_trust()
        self._validate_plugin_id(plugin_id)
        try:
            parsed_manifest = validate_manifest(dict(manifest))
        except (PluginValidationError, TypeError, ValueError) as exc:
            raise ModuleSourceValidationError(
                code="MODULE_SOURCE_MANIFEST_INVALID",
                detail="manifest failed strict plugin schema validation",
            ) from exc
        if parsed_manifest.plugin_id != plugin_id:
            raise ModuleSourceValidationError(
                code="MODULE_SOURCE_PLUGIN_MISMATCH",
                detail="manifest.plugin_id must exactly match the routed plugin_id",
            )
        try:
            validate_plugin_instance_scopes(
                parsed_manifest,
                company_id=resolved_company_id,
                plugin_id=plugin_id,
                secret_ref_validator=validate_secret_delivery_ref,
            )
        except (TypeError, ValueError) as exc:
            raise ModuleSourceValidationError(
                code="MODULE_SOURCE_INSTANCE_SCOPE_MISMATCH",
                detail=(
                    "manifest pods/secret_delivery scopes must match the "
                    "deterministic plugin instance"
                ),
            ) from exc

        repository_name = self._repository_name(
            company_id=resolved_company_id, plugin_id=plugin_id
        )
        expected_full_name = f"{self._config.organization.name}/{repository_name}"
        expected_default_branch = self._config.default_branch
        lock_key = f"{resolved_company_id}\x00{plugin_id}"
        async with self._locks.hold(lock_key):
            existing = await self._get_company_plugin(
                company_id=resolved_company_id, plugin_id=plugin_id
            )
            if existing is not None:
                return await self._resume_or_return(
                    existing,
                    company_id=resolved_company_id,
                    plugin_id=plugin_id,
                    expected_full_name=expected_full_name,
                    expected_default_branch=expected_default_branch,
                    expected_repo_id=None,
                )

            proposed = self._new_reservation(
                company_id=resolved_company_id,
                plugin_id=plugin_id,
                full_name=expected_full_name,
            )
            reserved = await self._reserve_provisioning(proposed)
            reservation = reserved.reservation
            self._validate_reservation(
                reservation,
                company_id=resolved_company_id,
                plugin_id=plugin_id,
                expected_full_name=expected_full_name,
                expected_default_branch=expected_default_branch,
            )

            # A source may have been committed between the first read and the
            # reservation insert. It wins over provider creation in every case.
            existing = await self._get_company_plugin(
                company_id=resolved_company_id, plugin_id=plugin_id
            )
            if existing is not None:
                return await self._resume_or_return(
                    existing,
                    company_id=resolved_company_id,
                    plugin_id=plugin_id,
                    expected_full_name=expected_full_name,
                    expected_default_branch=expected_default_branch,
                    expected_repo_id=None,
                )
            if reservation.status is ModuleSourceReservationStatus.DISABLED:
                raise ModuleSourceConflictError(
                    code="MODULE_SOURCE_DISABLED",
                    detail="disabled provisioning reservation cannot be resumed",
                )
            if reserved.acquired:
                return await self._create_reserved_repository(
                    reservation,
                    repository_name=repository_name,
                    company_id=resolved_company_id,
                    plugin_id=plugin_id,
                    expected_full_name=expected_full_name,
                    expected_default_branch=expected_default_branch,
                )

            # acquired=false is never permission to create. First reconcile a
            # repository whose create response or database write was lost.
            metadata = await self._get_provider_metadata(expected_full_name)
            if metadata is not None:
                return await self._persist_and_protect(
                    metadata,
                    company_id=resolved_company_id,
                    plugin_id=plugin_id,
                    expected_full_name=expected_full_name,
                    expected_default_branch=expected_default_branch,
                    created=False,
                )
            if reservation.status is ModuleSourceReservationStatus.READY:
                raise ModuleSourceUnavailableError(
                    code="MODULE_SOURCE_RESERVATION_INCONSISTENT",
                    detail="ready reservation has neither source nor provider metadata",
                )
            if reservation.create_in_progress:
                raise ModuleSourceProvisioningInProgressError()

            # A previous provider call returned a failure and released only its
            # own fenced attempt. A single retry caller may now acquire it.
            retry = await self._retry_provisioning(reservation)
            if retry.acquired:
                return await self._create_reserved_repository(
                    retry.reservation,
                    repository_name=repository_name,
                    company_id=resolved_company_id,
                    plugin_id=plugin_id,
                    expected_full_name=expected_full_name,
                    expected_default_branch=expected_default_branch,
                )
            raise ModuleSourceProvisioningInProgressError()

    async def get_status(
        self,
        *,
        company_id: str,
        plugin_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> ModuleSource:
        resolved_company_id = await self._authorize(
            company_id=company_id, principal=principal, employee=employee
        )
        self._validate_plugin_id(plugin_id)
        source = await self._get_company_plugin(
            company_id=resolved_company_id, plugin_id=plugin_id
        )
        if source is None:
            raise ModuleSourceNotFoundError()
        return source

    async def bind_scope(
        self,
        *,
        company_id: str,
        plugin_id: str,
        cabinet_id: str,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> ModuleSource:
        """Bind source scope after company membership and project access checks."""
        resolved_company_id = await self._authorize(
            company_id=company_id, principal=principal, employee=employee
        )
        self._validate_plugin_id(plugin_id)
        for name, value in (("cabinet_id", cabinet_id), ("project_id", project_id)):
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
                or len(value) > 40
            ):
                raise ModuleSourceValidationError(
                    code="MODULE_SOURCE_SCOPE_INVALID",
                    detail=f"{name} must be an exact non-empty identifier",
                )
        source = await self._get_company_plugin(
            company_id=resolved_company_id, plugin_id=plugin_id
        )
        if source is None:
            raise ModuleSourceNotFoundError()
        if source.status is not ModuleSourceStatus.READY:
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_NOT_READY",
                detail="only a READY source can receive an installation scope binding",
            )
        if self._project_access is None:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_SCOPE_UNAVAILABLE",
                detail="project access validation is not wired",
            )
        project = await self._project_access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=False,
            allow_paused=False,
            allow_deleted=False,
            skip_visibility=False,
        )
        if (
            project.id != project_id
            or project.company_id != resolved_company_id
            or project.cabinet_id != cabinet_id
        ):
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_SCOPE_MISMATCH",
                detail="ProjectRow does not match the requested tenant/cabinet scope",
            )
        try:
            return await self._repository.bind_scope(
                replace(source, cabinet_id=cabinet_id, project_id=project_id)
            )
        except RepositoryConflictError as exc:
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_SCOPE_CONFLICT",
                detail="existing source or reservation has a different immutable scope",
            ) from exc
        except Exception as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_SCOPE_UNAVAILABLE",
                detail="source scope binding could not be persisted",
            ) from exc

    async def _authorize(
        self,
        *,
        company_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> str:
        try:
            company = await self._entitlement.require_company_actor(
                principal, company_id, employee=employee
            )
        except AppError:
            raise
        except Exception as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_ENTITLEMENT_UNAVAILABLE",
                detail="company entitlement check is unavailable",
            ) from exc
        resolved = getattr(company, "id", None)
        if not isinstance(resolved, str) or not resolved or resolved != company_id:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="resolved company scope does not match the requested source",
            )
        return resolved

    def _validate_trust(self) -> None:
        try:
            self._config.trust.validate()
            validate_branch(self._config.default_branch)
        except (TypeError, ValueError) as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_CONFIG_INVALID",
                detail="managed workflow or installation credential configuration is invalid",
            ) from exc

    @staticmethod
    def _validate_plugin_id(plugin_id: str) -> None:
        if (
            not isinstance(plugin_id, str)
            or len(plugin_id) > 160
            or PLUGIN_ID_PATTERN.fullmatch(plugin_id) is None
        ):
            raise ModuleSourceValidationError(
                code="MODULE_SOURCE_PLUGIN_INVALID",
                detail="plugin_id must use the canonical plugin id grammar",
            )

    @staticmethod
    def _repository_name(*, company_id: str, plugin_id: str) -> str:
        digest = hashlib.sha256(f"{company_id}\x00{plugin_id}".encode("utf-8")).hexdigest()[:16]
        stem = f"plugin-{plugin_id}"[:79].rstrip("._-")
        return f"{stem}-{digest}"

    def _validate_created_repository(
        self,
        created: CreatedRepository,
        *,
        expected_full_name: str,
        expected_default_branch: str,
    ) -> None:
        if (
            not isinstance(created, CreatedRepository)
            or created.full_name != expected_full_name
            or created.default_branch != expected_default_branch
            or created.private is not True
            or created.template != self._config.organization.template
        ):
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_PROVIDER_INVALID_RESPONSE",
                detail="provider returned unconfirmed repository metadata or template provenance",
            )

    @staticmethod
    def _validate_repository_metadata(
        metadata: RepositoryMetadata,
        *,
        expected_full_name: str,
        expected_default_branch: str,
        expected_repo_id: int | None,
    ) -> None:
        if (
            not isinstance(metadata, RepositoryMetadata)
            or metadata.full_name != expected_full_name
            or metadata.default_branch != expected_default_branch
            or metadata.private is not True
            or (expected_repo_id is not None and metadata.repo_id != expected_repo_id)
        ):
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_PROVIDER_BINDING_CONFLICT",
                detail="confirmed provider metadata does not match the reserved source binding",
            )

    def _validate_reservation(
        self,
        reservation: ModuleSourceProvisioningReservation,
        *,
        company_id: str,
        plugin_id: str,
        expected_full_name: str,
        expected_default_branch: str,
    ) -> None:
        if (
            reservation.company_id != company_id
            or reservation.plugin_id != plugin_id
            or reservation.provider != "github_app"
            or reservation.full_name != expected_full_name
            or reservation.default_branch != expected_default_branch
            or reservation.private is not True
            or reservation.installation_credential_ref
            != self._config.trust.installation_credential_ref
            or reservation.trusted_workflow_ref != self._config.trust.trusted_workflow_ref
        ):
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_IMMUTABLE_BINDING_CONFLICT",
                detail="existing reservation has a different repository or trust binding",
            )

    def _new_reservation(
        self, *, company_id: str, plugin_id: str, full_name: str
    ) -> ModuleSourceProvisioningReservation:
        now = self._clock()
        return ModuleSourceProvisioningReservation(
            id=self._id_factory(),
            company_id=company_id,
            plugin_id=plugin_id,
            full_name=full_name,
            private=True,
            default_branch=self._config.default_branch,
            installation_credential_ref=self._config.trust.installation_credential_ref,
            trusted_workflow_ref=self._config.trust.trusted_workflow_ref,
            attempt_id=uuid4().hex,
            create_in_progress=True,
            status=ModuleSourceReservationStatus.PROVISIONING,
            created_at=now,
            updated_at=now,
        )

    async def _reserve_provisioning(
        self, reservation: ModuleSourceProvisioningReservation
    ) -> ModuleSourceReservationResult:
        try:
            return await self._repository.reserve_provisioning(reservation)
        except RepositoryConflictError as exc:
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_RESERVATION_CONFLICT",
                detail="provisioning reservation identities are inconsistent",
            ) from exc
        except Exception as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_RESERVATION_FAILED",
                detail="distributed provisioning reservation is unavailable; provider was not called",
            ) from exc

    async def _retry_provisioning(
        self, reservation: ModuleSourceProvisioningReservation
    ) -> ModuleSourceReservationResult:
        try:
            return await self._repository.retry_provisioning(
                reservation, attempt_id=uuid4().hex
            )
        except RepositoryConflictError as exc:
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_RESERVATION_CONFLICT",
                detail="provisioning reservation changed inconsistently during retry",
            ) from exc
        except Exception as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_RESERVATION_FAILED",
                detail="provisioning retry reservation is unavailable; provider was not called",
            ) from exc

    async def _release_create_attempt(
        self, reservation: ModuleSourceProvisioningReservation
    ) -> None:
        try:
            await self._repository.release_provisioning_attempt(
                reservation.id, attempt_id=reservation.attempt_id
            )
        except Exception as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_FAILURE_RECORDING_FAILED",
                detail="provider failed and its retryable reservation state could not be recorded",
            ) from exc

    async def _create_reserved_repository(
        self,
        reservation: ModuleSourceProvisioningReservation,
        *,
        repository_name: str,
        company_id: str,
        plugin_id: str,
        expected_full_name: str,
        expected_default_branch: str,
    ) -> ModuleSourceProvisionResult:
        request = CreatePrivateRepositoryRequest(
            organization=self._config.organization.name,
            name=repository_name,
            description=f"Module source for {company_id}/{plugin_id}",
        )
        try:
            created = await self._provider.create_private_repository(request)
        except GitHubConflictError as exc:
            # An earlier response may have been lost. Reconcile the same reserved
            # name instead of creating again or leaving an unbound provider repo.
            metadata = await self._get_provider_metadata(expected_full_name)
            if metadata is not None:
                return await self._persist_and_protect(
                    metadata,
                    company_id=company_id,
                    plugin_id=plugin_id,
                    expected_full_name=expected_full_name,
                    expected_default_branch=expected_default_branch,
                    created=False,
                )
            await self._release_create_attempt(reservation)
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_REPOSITORY_CONFLICT",
                detail="managed repository creation conflicted with existing provider state",
            ) from exc
        except Exception as exc:
            await self._release_create_attempt(reservation)
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_REPOSITORY_CREATE_FAILED",
                detail="managed repository creation failed; reservation remains retryable",
            ) from exc

        self._validate_created_repository(
            created,
            expected_full_name=expected_full_name,
            expected_default_branch=expected_default_branch,
        )
        # CreatedRepository is adapter-confirmed provider metadata including
        # template provenance. Persist its actual numeric id before protection.
        return await self._persist_and_protect(
            created,
            company_id=company_id,
            plugin_id=plugin_id,
            expected_full_name=expected_full_name,
            expected_default_branch=expected_default_branch,
            created=True,
        )

    async def _persist_and_protect(
        self,
        metadata: RepositoryMetadata,
        *,
        company_id: str,
        plugin_id: str,
        expected_full_name: str,
        expected_default_branch: str,
        created: bool,
    ) -> ModuleSourceProvisionResult:
        self._validate_repository_metadata(
            metadata,
            expected_full_name=expected_full_name,
            expected_default_branch=expected_default_branch,
            expected_repo_id=None,
        )
        bound = await self._get_provider_repo(metadata.repo_id)
        if bound is not None:
            resumed = await self._resume_or_return(
                bound,
                company_id=company_id,
                plugin_id=plugin_id,
                expected_full_name=expected_full_name,
                expected_default_branch=expected_default_branch,
                expected_repo_id=metadata.repo_id,
            )
            return ModuleSourceProvisionResult(
                source=resumed.source, created=created
            )

        source = self._new_source(
            company_id=company_id,
            plugin_id=plugin_id,
            repository=metadata,
            status=ModuleSourceStatus.PROVISIONING,
        )
        try:
            saved = await self._repository.create(source)
        except RepositoryConflictError as exc:
            existing = await self._get_company_plugin(
                company_id=company_id, plugin_id=plugin_id
            )
            if existing is None:
                existing = await self._get_provider_repo(metadata.repo_id)
            if existing is None:
                raise ModuleSourceConflictError(
                    code="MODULE_SOURCE_IDENTITY_CONFLICT",
                    detail="company/plugin or provider/repository identity is already bound",
                ) from exc
            resumed = await self._resume_or_return(
                existing,
                company_id=company_id,
                plugin_id=plugin_id,
                expected_full_name=expected_full_name,
                expected_default_branch=expected_default_branch,
                expected_repo_id=metadata.repo_id,
            )
            return ModuleSourceProvisionResult(
                source=resumed.source, created=created
            )
        except Exception as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_SAVE_FAILED",
                detail="confirmed repository metadata could not be saved; source is not ready",
            ) from exc
        return await self._protect_and_complete(
            saved, metadata, created=created
        )

    async def _protect_and_complete(
        self,
        source: ModuleSource,
        metadata: RepositoryMetadata,
        *,
        created: bool,
    ) -> ModuleSourceProvisionResult:
        try:
            await self._provider.protect_default_branch(
                metadata, self._config.branch_protection
            )
        except Exception as exc:
            if isinstance(exc, GitHubConflictError):
                raise ModuleSourceConflictError(
                    code="MODULE_SOURCE_PROTECTION_CONFLICT",
                    detail="branch protection conflicted; source remains provisioning",
                ) from exc
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_PROTECTION_FAILED",
                detail="branch protection failed; source remains provisioning",
            ) from exc
        try:
            updated = await self._repository.complete_provisioning(source.id)
        except RepositoryConflictError as exc:
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_COMPLETION_CONFLICT",
                detail="protected source or its reservation was disabled",
            ) from exc
        except Exception as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_STATUS_UPDATE_FAILED",
                detail="branch protection succeeded but ready state could not be saved",
            ) from exc
        if updated is None or updated.status is not ModuleSourceStatus.READY:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_STATUS_UPDATE_FAILED",
                detail="source completion did not produce a ready state",
            )
        return ModuleSourceProvisionResult(source=updated, created=created)

    def _new_source(
        self,
        *,
        company_id: str,
        plugin_id: str,
        repository: RepositoryMetadata,
        status: ModuleSourceStatus,
    ) -> ModuleSource:
        now = self._clock()
        return ModuleSource(
            id=self._id_factory(),
            company_id=company_id,
            plugin_id=plugin_id,
            repo_id=repository.repo_id,
            full_name=repository.full_name,
            private=repository.private,
            default_branch=repository.default_branch,
            installation_credential_ref=self._config.trust.installation_credential_ref,
            trusted_workflow_ref=self._config.trust.trusted_workflow_ref,
            status=status,
            created_at=now,
            updated_at=now,
        )

    async def _resume_or_return(
        self,
        source: ModuleSource,
        *,
        company_id: str,
        plugin_id: str,
        expected_full_name: str,
        expected_default_branch: str,
        expected_repo_id: int | None,
    ) -> ModuleSourceProvisionResult:
        if (
            source.company_id != company_id
            or source.plugin_id != plugin_id
            or source.provider != "github_app"
            or source.full_name != expected_full_name
            or source.default_branch != expected_default_branch
            or (expected_repo_id is not None and source.repo_id != expected_repo_id)
            or source.private is not True
            or source.installation_credential_ref
            != self._config.trust.installation_credential_ref
            or source.trusted_workflow_ref != self._config.trust.trusted_workflow_ref
        ):
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_IMMUTABLE_BINDING_CONFLICT",
                detail="existing source has a different immutable repository or trust binding",
            )
        if source.status is ModuleSourceStatus.READY:
            return ModuleSourceProvisionResult(source=source, created=False)
        if source.status is ModuleSourceStatus.DISABLED:
            raise ModuleSourceConflictError(
                code="MODULE_SOURCE_DISABLED",
                detail="disabled source cannot be resumed through template creation",
            )

        # Stored metadata alone never authorizes protection. Resume confirms the
        # same immutable numeric id and branch/privacy directly at the provider.
        metadata = await self._get_provider_metadata(source.full_name)
        if metadata is None:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_PROVIDER_METADATA_NOT_FOUND",
                detail="provider repository is not confirmed; source remains provisioning",
            )
        self._validate_repository_metadata(
            metadata,
            expected_full_name=expected_full_name,
            expected_default_branch=expected_default_branch,
            expected_repo_id=source.repo_id,
        )
        return await self._protect_and_complete(source, metadata, created=False)

    async def _get_company_plugin(
        self, *, company_id: str, plugin_id: str
    ) -> ModuleSource | None:
        try:
            return await self._repository.get_by_company_plugin(
                company_id=company_id, plugin_id=plugin_id
            )
        except Exception as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_READ_FAILED",
                detail="module source metadata is unavailable",
            ) from exc

    async def _get_provider_metadata(
        self, full_name: str
    ) -> RepositoryMetadata | None:
        try:
            return await self._provider.get_repository_metadata(full_name)
        except GitHubAPIError as exc:
            if exc.status_code == 404:
                return None
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_PROVIDER_METADATA_FAILED",
                detail="provider repository metadata is unavailable; no state was marked ready",
            ) from exc
        except Exception as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_PROVIDER_METADATA_FAILED",
                detail="provider repository metadata is unavailable; no state was marked ready",
            ) from exc

    async def _get_provider_repo(self, repo_id: int) -> ModuleSource | None:
        try:
            return await self._repository.get_by_provider_repo(
                provider="github_app", repo_id=repo_id
            )
        except Exception as exc:
            raise ModuleSourceUnavailableError(
                code="MODULE_SOURCE_READ_FAILED",
                detail="module source repository identity could not be checked",
            ) from exc


__all__ = [
    "ModuleSourceConflictError",
    "ModuleSourceEntitlementPort",
    "ModuleSourceLockRegistry",
    "ModuleSourceNotFoundError",
    "ModuleSourceProjectAccessPort",
    "ModuleSourceProvisionResult",
    "ModuleSourceProvisioningConfig",
    "ModuleSourceProvisioningInProgressError",
    "ModuleSourceService",
    "ModuleSourceServiceError",
    "ModuleSourceUnavailableError",
    "ModuleSourceValidationError",
]

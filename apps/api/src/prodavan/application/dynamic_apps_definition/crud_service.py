"""Fail-closed CRUD application service for dynamic app definitions/versions/instances.

Every public method follows the same shape:

1. resolve the server-side :class:`ActorIdentity` (tenant is never taken from the body);
2. read the existing idempotency/audit record for ``(tenant, operation, key)``;
3. **replay** an exact repeat (same request digest) or reject a changed one with
   ``IDEMPOTENCY_REPLAY`` — never overwrite;
4. validate lifecycle/scope/ownership rules before touching persistence;
5. write all rows plus exactly one redacted audit record, then commit once.

Any raised :class:`AppError` before ``commit`` leaves the transaction untouched.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Mapping

from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppGrant,
    AppInstance,
    AppLifecycleStatus,
    AppScope,
    AppScopeKind,
    AppVersion,
    CapabilityDecision,
    CapabilityGrant,
    CapabilityName,
    RequestedCapability,
    RollbackMetadata,
)
from prodavan.domain.errors import AppError

from .crud_contracts import (
    ArchiveDefinitionRequest,
    CreateDefinitionRequest,
    CreateVersionRequest,
    DynamicAppAuditEvent,
    DynamicAppErrorCode,
    DynamicAppOperation,
    InstallInstanceRequest,
    IssueGrantRequest,
    MutationContext,
    MutationReceipt,
    PublishVersionRequest,
    RevokeGrantRequest,
    digest_of,
)
from .crud_ports import (
    AuditConflict,
    AuditRecord,
    DynamicAppAuditPort,
    DynamicAppCrudRepository,
    DynamicAppScopeAuthority,
)
from .lifecycle import (
    api_lifecycle,
    archived_persisted_status,
    require_archivable_definition,
    require_installable,
    require_accepts_new_version,
    require_publishable_version,
    require_published_version,
)

_APP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_PAGE = 200


def _error(code: DynamicAppErrorCode, status: int, detail: str) -> AppError:
    return AppError(code=code.value, title=_TITLES.get(code, code.value), status=status, detail=detail)


_TITLES = {
    DynamicAppErrorCode.TENANT_CONTEXT_REQUIRED: "Tenant context required",
    DynamicAppErrorCode.TENANT_MISMATCH: "Tenant mismatch",
    DynamicAppErrorCode.IDEMPOTENCY_KEY_REQUIRED: "Idempotency key required",
    DynamicAppErrorCode.IDEMPOTENCY_REPLAY: "Idempotency replay with different body",
    DynamicAppErrorCode.FORBIDDEN: "Forbidden",
    DynamicAppErrorCode.NOT_FOUND: "Not Found",
    DynamicAppErrorCode.VALIDATION_FAILED: "Validation Error",
    DynamicAppErrorCode.APP_ID_TAKEN: "App id already exists",
    DynamicAppErrorCode.VERSION_EXISTS: "Version already exists",
    DynamicAppErrorCode.VERSION_ALREADY_PUBLISHED: "Version already published",
    DynamicAppErrorCode.VERSION_NOT_PUBLISHED: "Version not published",
    DynamicAppErrorCode.INVALID_LIFECYCLE_TRANSITION: "Invalid lifecycle transition",
    DynamicAppErrorCode.ALREADY_ARCHIVED: "Already archived",
    DynamicAppErrorCode.APP_ARCHIVED: "Application not installable",
    DynamicAppErrorCode.APP_NOT_PUBLISHED: "Application not published",
    DynamicAppErrorCode.STATE_CHANGED: "Concurrent state change",
    DynamicAppErrorCode.INSTANCE_EXISTS: "Instance already installed",
    DynamicAppErrorCode.CAPABILITY_NOT_REQUESTED: "Capability not requested",
    DynamicAppErrorCode.GRANT_ALREADY_REVOKED: "Grant already revoked",
    DynamicAppErrorCode.GRANT_REVOKED: "Grant revoked",
    DynamicAppErrorCode.SCOPE_DENIED: "Scope denied",
    DynamicAppErrorCode.INVALID_SCOPE: "Invalid scope",
}


def _not_found(detail: str) -> AppError:
    # Cross-tenant reads and writes must be indistinguishable from "absent".
    return _error(DynamicAppErrorCode.NOT_FOUND, 404, detail)


def _definition_view(definition: AppDefinition) -> dict[str, Any]:
    return {
        "id": definition.id,
        "type": "dynamic_app_definition",
        "tenant_id": definition.tenant_id,
        "app_id": definition.app_id,
        "display_name": definition.display_name,
        "description": definition.description,
        "scope": {"kind": definition.scope.kind.value, "id": definition.scope.id},
        "lifecycle": api_lifecycle(definition.status),
        "current_version_id": definition.current_version_id,
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
    }


def _version_view(version: AppVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "type": "dynamic_app_version",
        "app_definition_id": version.app_definition_id,
        "manifest_version": version.manifest_version,
        "artifact_digest": version.artifact_digest,
        "lifecycle": api_lifecycle(version.status),
        "requested_capabilities": [
            {
                "name": item.name.value,
                "reason": item.reason,
                "constraints": dict(item.constraints) if item.constraints else None,
                "optional": item.optional,
            }
            for item in version.requested_capabilities
        ],
        "created_at": version.created_at.isoformat(),
        "published_at": version.published_at.isoformat() if version.published_at else None,
    }


def _instance_view(instance: AppInstance) -> dict[str, Any]:
    return {
        "id": instance.id,
        "type": "dynamic_app_instance",
        "app_definition_id": instance.app_definition_id,
        "app_version_id": instance.app_version_id,
        "scope": {"kind": instance.scope.kind.value, "id": instance.scope.id},
        "lifecycle": api_lifecycle(instance.status),
        "runtime_ref": instance.runtime_ref,
        "created_at": instance.created_at.isoformat(),
        "updated_at": instance.updated_at.isoformat(),
    }


def _grant_view(grant: AppGrant) -> dict[str, Any]:
    return {
        "id": grant.id,
        "type": "dynamic_app_grant",
        "app_definition_id": grant.app_definition_id,
        "app_version_id": grant.app_version_id,
        "scope": {"kind": grant.scope.kind.value, "id": grant.scope.id},
        "capabilities": [
            {
                "name": item.name.value,
                "decision": item.decision.value,
                "constraints": dict(item.constraints) if item.constraints else None,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
            }
            for item in grant.grants
        ],
        "issued_at": grant.issued_at.isoformat(),
        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
    }


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """Normalise datetimes so a naive client value is rejected, not silently coerced."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise _error(
            DynamicAppErrorCode.VALIDATION_FAILED,
            422,
            "timestamps must be timezone-aware",
        )
    return value.astimezone(UTC)


class DynamicAppCrudService:
    """Owner of every dynamic-app mutation reachable through the v1 HTTP surface."""

    def __init__(
        self,
        repository: DynamicAppCrudRepository,
        audit: DynamicAppAuditPort,
        *,
        scope_authority: DynamicAppScopeAuthority,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._scope_authority = scope_authority

    # ---------------------------------------------------------------- definitions --

    async def create_definition(
        self, *, context: MutationContext, request: CreateDefinitionRequest
    ) -> MutationReceipt:
        self._require_identity(context)
        if not _APP_ID_PATTERN.fullmatch(request.app_id):
            raise _error(
                DynamicAppErrorCode.VALIDATION_FAILED,
                422,
                "app_id must match ^[a-z][a-z0-9-]{2,63}$",
            )
        if request.description is not None and len(request.description) > 2_000:
            raise _error(DynamicAppErrorCode.VALIDATION_FAILED, 422, "description too long")
        payload = {
            "app_id": request.app_id,
            "display_name": request.display_name,
            "description": request.description,
        }
        request_digest = digest_of(payload)

        replay = await self._replay(context, request_digest)
        if replay is not None:
            return replay

        existing = await self._repository.get_definition_by_app_id(
            tenant_id=context.identity.tenant_id, app_id=request.app_id
        )
        if existing is not None:
            raise _error(
                DynamicAppErrorCode.APP_ID_TAKEN,
                409,
                f"app_id {request.app_id} already exists in this tenant",
            )

        now = _now()
        definition = AppDefinition(
            id=f"app_{_short_id(payload, now)}",
            tenant_id=context.identity.tenant_id,
            app_id=request.app_id,
            display_name=request.display_name,
            description=request.description,
            # A definition created through the tenant API is tenant-scoped by construction;
            # cabinet/project scoping is expressed by instances, not by the definition.
            scope=AppScope(kind=AppScopeKind.TENANT, id=context.identity.tenant_id),
            status=AppLifecycleStatus.DRAFT,
            current_version_id=None,
            created_at=now,
            updated_at=now,
        )
        await self._repository.add_definition(definition)
        view = _definition_view(definition)
        await self._append_audit(
            context,
            request_digest=request_digest,
            event_type=DynamicAppAuditEvent.DEFINITION_CREATED,
            result=view,
        )
        await self._commit()
        return MutationReceipt(data=view, trace_id=context.trace_id)

    async def list_definitions(
        self,
        *,
        identity_tenant_id: str,
        limit: int = 50,
        after_id: str | None = None,
        lifecycle_status: AppLifecycleStatus | None = None,
    ) -> dict[str, Any]:
        if not identity_tenant_id.strip():
            raise _error(
                DynamicAppErrorCode.TENANT_CONTEXT_REQUIRED,
                400,
                "resolved tenant context is required",
            )
        page = await self._repository.list_definitions(
            tenant_id=identity_tenant_id,
            limit=_page_size(limit) + 1,
            after_id=after_id,
            lifecycle=lifecycle_status,
        )
        return _page([_definition_view(item) for item in page], limit)

    async def get_definition(self, *, identity_tenant_id: str, app_id: str) -> dict[str, Any]:
        definition = await self._require_definition(
            tenant_id=identity_tenant_id, app_id=app_id
        )
        versions = await self._repository.list_versions(
            definition_id=definition.id, limit=50
        )
        payload = _definition_view(definition)
        payload["versions"] = [_version_view(item) for item in versions]
        return payload

    async def archive_definition(
        self, *, context: MutationContext, request: ArchiveDefinitionRequest
    ) -> MutationReceipt:
        self._require_identity(context)
        request_digest = digest_of(
            {"definition_id": request.definition_id, "reason": request.reason}
        )
        replay = await self._replay(context, request_digest)
        if replay is not None:
            return replay

        definition = await self._require_definition(
            tenant_id=context.identity.tenant_id, definition_id=request.definition_id
        )
        require_archivable_definition(definition.status)

        target = archived_persisted_status()
        moved = await self._repository.transition_definition(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            expected_status=definition.status,
            status=target,
            current_version_id=definition.current_version_id,
        )
        if not moved:
            raise _error(
                DynamicAppErrorCode.STATE_CHANGED,
                409,
                "definition changed concurrently; retry with a fresh read",
            )
        archived = replace(definition, status=target, updated_at=_now())
        view = _definition_view(archived)
        await self._append_audit(
            context,
            request_digest=request_digest,
            event_type=DynamicAppAuditEvent.DEFINITION_ARCHIVED,
            result=view,
            reason=request.reason,
        )
        await self._commit()
        return MutationReceipt(data=view, trace_id=context.trace_id)

    # ------------------------------------------------------------------- versions --

    async def create_version(
        self,
        *,
        context: MutationContext,
        definition_id: str,
        request: CreateVersionRequest,
    ) -> MutationReceipt:
        """Append an immutable draft version.

        ``create_version`` is *not* a separate idempotency operation: a repeated upload is
        identified by the unique ``(definition, manifest_version)`` / ``(definition,
        digest)`` keys and answered with ``VERSION_EXISTS`` instead of a silent second row.
        """
        self._require_identity(context)
        manifest = dict(request.manifest)
        validated = _validate_manifest(manifest, artifact_digest=request.artifact_digest)
        request_digest = digest_of(
            {
                "definition_id": definition_id,
                "manifest": manifest,
                "artifact_digest": request.artifact_digest,
                "artifact_uri": request.artifact_uri,
            }
        )
        replay = await self._replay(context, request_digest)
        if replay is not None:
            return replay

        definition = await self._require_definition(
            tenant_id=context.identity.tenant_id, definition_id=definition_id
        )
        require_accepts_new_version(definition.status)
        if validated.app_id != definition.app_id:
            raise _error(
                DynamicAppErrorCode.VALIDATION_FAILED,
                422,
                "manifest appId must equal the definition app_id",
            )

        same_version = await self._repository.get_version_by_manifest_version(
            definition_id=definition.id, manifest_version=validated.manifest_version
        )
        if same_version is not None:
            raise _error(
                DynamicAppErrorCode.VERSION_EXISTS,
                409,
                "manifest version already exists for this definition",
            )

        now = _now()
        version = AppVersion(
            id=f"aver_{_short_id(request_digest, now)}",
            app_definition_id=definition.id,
            manifest_version=validated.manifest_version,
            artifact_digest=request.artifact_digest,
            manifest=manifest,
            requested_capabilities=validated.requested_capabilities,
            status=AppLifecycleStatus.DRAFT,
            rollback=RollbackMetadata(),
            created_at=now,
            published_at=None,
        )
        await self._repository.add_version(version)
        view = _version_view(version)
        await self._append_audit(
            context,
            request_digest=request_digest,
            event_type=DynamicAppAuditEvent.VERSION_CREATED,
            result=view,
            markers={"artifact_digest": version.artifact_digest},
        )
        await self._commit()
        return MutationReceipt(data=view, trace_id=context.trace_id)

    async def publish_version(
        self, *, context: MutationContext, request: PublishVersionRequest
    ) -> MutationReceipt:
        self._require_identity(context)
        request_digest = digest_of(
            {
                "definition_id": request.definition_id,
                "version_id": request.version_id,
                "expected_current_version_id": request.expected_current_version_id,
            }
        )
        replay = await self._replay(context, request_digest)
        if replay is not None:
            return replay

        definition, version = await self._require_pair(
            tenant_id=context.identity.tenant_id,
            definition_id=request.definition_id,
            version_id=request.version_id,
        )
        require_accepts_new_version(definition.status)
        require_publishable_version(version.status)
        if (
            request.expected_current_version_id is not None
            and definition.current_version_id != request.expected_current_version_id
        ):
            raise _error(
                DynamicAppErrorCode.STATE_CHANGED,
                409,
                "definition current version changed; retry with a fresh read",
            )
        # Re-validate the stored snapshot before it becomes serving: a row written by an
        # older/looser path must not be promotable if its manifest no longer matches the
        # immutable artifact digest.
        validated = _validate_manifest(
            dict(version.manifest), artifact_digest=version.artifact_digest
        )
        if validated.manifest_version != version.manifest_version:
            raise _error(
                DynamicAppErrorCode.VALIDATION_FAILED,
                422,
                "stored manifest version disagrees with the version row",
            )

        published_at = _now()
        moved = await self._repository.transition_version(
            definition_id=definition.id,
            version_id=version.id,
            expected_status=AppLifecycleStatus.DRAFT,
            status=AppLifecycleStatus.PUBLISHED,
            published_at=published_at,
        )
        if not moved:
            raise _error(
                DynamicAppErrorCode.STATE_CHANGED,
                409,
                "version changed concurrently; retry with a fresh read",
            )
        moved_definition = await self._repository.transition_definition(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            expected_status=definition.status,
            status=AppLifecycleStatus.PUBLISHED,
            current_version_id=version.id,
        )
        if not moved_definition:
            await self._repository.rollback()
            raise _error(
                DynamicAppErrorCode.STATE_CHANGED,
                409,
                "definition changed concurrently; retry with a fresh read",
            )

        published = replace(
            version, status=AppLifecycleStatus.PUBLISHED, published_at=published_at
        )
        serving = replace(
            definition,
            status=AppLifecycleStatus.PUBLISHED,
            current_version_id=version.id,
            updated_at=published_at,
        )
        view = {
            "definition": _definition_view(serving),
            "version": _version_view(published),
        }
        await self._append_audit(
            context,
            request_digest=request_digest,
            event_type=DynamicAppAuditEvent.VERSION_PUBLISHED,
            result=view,
            markers={
                "version_id": version.id,
                "artifact_digest": version.artifact_digest,
            },
        )
        await self._commit()
        return MutationReceipt(data=view, trace_id=context.trace_id)

    # ------------------------------------------------------------------ instances --

    async def install_instance(
        self, *, context: MutationContext, request: InstallInstanceRequest
    ) -> MutationReceipt:
        self._require_identity(context)
        request_digest = digest_of(
            {
                "definition_id": request.definition_id,
                "scope_kind": request.scope_kind.value,
                "scope_id": request.scope_id,
                "version_id": request.version_id,
                "runtime_ref": request.runtime_ref,
            }
        )
        replay = await self._replay(context, request_digest)
        if replay is not None:
            return replay

        definition = await self._require_definition(
            tenant_id=context.identity.tenant_id, definition_id=request.definition_id
        )
        # Archive check runs *before* scope resolution so an archived app never leaks
        # whether a foreign cabinet/project exists.
        require_installable(definition.status)

        version = await self._resolve_install_version(
            definition=definition, version_id=request.version_id
        )
        cabinet_id = await self._resolve_scope(
            tenant_id=definition.tenant_id,
            scope_kind=request.scope_kind,
            scope_id=request.scope_id,
        )
        existing = await self._repository.get_instance_for_scope(
            definition_id=definition.id,
            scope_kind=request.scope_kind,
            scope_id=request.scope_id,
        )
        if existing is not None:
            raise _error(
                DynamicAppErrorCode.INSTANCE_EXISTS,
                409,
                "an instance for this scope already exists; archive it or install elsewhere",
            )

        now = _now()
        instance = AppInstance(
            id=f"ains_{_short_id(request_digest, now)}",
            app_definition_id=definition.id,
            app_version_id=version.id,
            scope=AppScope(kind=request.scope_kind, id=request.scope_id),
            status=AppLifecycleStatus.PUBLISHED,
            rollback=RollbackMetadata(),
            runtime_ref=request.runtime_ref,
            created_at=now,
            updated_at=now,
        )
        await self._repository.add_instance(instance)
        view = _instance_view(instance)
        await self._append_audit(
            context,
            request_digest=request_digest,
            event_type=DynamicAppAuditEvent.INSTANCE_INSTALLED,
            result=view,
            cabinet_id=cabinet_id,
            project_id=request.scope_id if request.scope_kind is AppScopeKind.PROJECT else None,
            markers={"version_id": version.id},
        )
        await self._commit()
        return MutationReceipt(data=view, trace_id=context.trace_id)

    async def list_instances(
        self, *, identity_tenant_id: str, definition_id: str, limit: int = 50
    ) -> dict[str, Any]:
        definition = await self._require_definition(
            tenant_id=identity_tenant_id, definition_id=definition_id
        )
        rows = await self._repository.list_instances(
            definition_id=definition.id, limit=_page_size(limit)
        )
        return {"items": [_instance_view(item) for item in rows], "has_more": False}

    # --------------------------------------------------------------------- grants --

    async def issue_grant(
        self, *, context: MutationContext, request: IssueGrantRequest
    ) -> MutationReceipt:
        self._require_identity(context)
        request_digest = digest_of(
            {
                "definition_id": request.definition_id,
                "version_id": request.version_id,
                "scope_kind": request.scope_kind.value,
                "scope_id": request.scope_id,
                "capabilities": [
                    {
                        "name": item.name,
                        "decision": item.decision.value,
                        "constraints": dict(item.constraints) if item.constraints else None,
                        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                    }
                    for item in request.capabilities
                ],
                "expires_at": request.expires_at.isoformat() if request.expires_at else None,
            }
        )
        replay = await self._replay(context, request_digest)
        if replay is not None:
            return replay

        definition, version = await self._require_pair(
            tenant_id=context.identity.tenant_id,
            definition_id=request.definition_id,
            version_id=request.version_id,
        )
        require_installable(definition.status)
        require_published_version(version.status)
        cabinet_id = await self._resolve_scope(
            tenant_id=definition.tenant_id,
            scope_kind=request.scope_kind,
            scope_id=request.scope_id,
        )

        requested = {item.name.value: item for item in version.requested_capabilities}
        capabilities = self._validate_capabilities(
            request=request, requested=requested
        )

        existing = await self._repository.get_grant(
            app_version_id=version.id,
            scope_kind=request.scope_kind,
            scope_id=request.scope_id,
        )
        now = _now()
        if existing is not None:
            # One row per (version, scope): a new grant must not silently replace a
            # previously issued decision set. Additive re-issue is allowed only for
            # capabilities that are not already present.
            current_names = {item.name.value for item in existing.grants}
            if current_names & {item.name.value for item in capabilities}:
                raise _error(
                    DynamicAppErrorCode.STATE_CHANGED,
                    409,
                    "grant for this version/scope already contains the requested capability",
                )
            merged = AppGrant(
                id=existing.id,
                app_definition_id=existing.app_definition_id,
                app_version_id=existing.app_version_id,
                scope=existing.scope,
                grants=existing.grants + capabilities,
                issued_at=existing.issued_at,
                expires_at=request.expires_at or existing.expires_at,
            )
            await self._repository.replace_grant(merged)
            grant = merged
        else:
            grant = AppGrant(
                id=f"agr_{_short_id(request_digest, now)}",
                app_definition_id=definition.id,
                app_version_id=version.id,
                scope=AppScope(kind=request.scope_kind, id=request.scope_id),
                grants=capabilities,
                issued_at=now,
                expires_at=request.expires_at,
            )
            await self._repository.add_grant(grant)

        view = _grant_view(grant)
        await self._append_audit(
            context,
            request_digest=request_digest,
            event_type=DynamicAppAuditEvent.GRANT_ISSUED,
            result=view,
            cabinet_id=cabinet_id,
            project_id=request.scope_id if request.scope_kind is AppScopeKind.PROJECT else None,
            markers={"version_id": version.id, "grant_id": grant.id},
        )
        await self._commit()
        return MutationReceipt(data=view, trace_id=context.trace_id)

    async def revoke_grant(
        self, *, context: MutationContext, request: RevokeGrantRequest
    ) -> MutationReceipt:
        self._require_identity(context)
        if not request.reason.strip():
            raise _error(
                DynamicAppErrorCode.VALIDATION_FAILED, 422, "revoke reason is required"
            )
        request_digest = digest_of(
            {
                "definition_id": request.definition_id,
                "version_id": request.version_id,
                "scope_kind": request.scope_kind.value,
                "scope_id": request.scope_id,
                "capability": request.capability,
                "reason": request.reason,
            }
        )
        replay = await self._replay(context, request_digest)
        if replay is not None:
            return replay

        # ``dynamic_app_grants`` has no revocation columns. Revoke is represented by
        # flipping the capability decision to ``denied`` (evidence is retained) plus a
        # durable audit marker, so a repeat is detected without a destructive delete.
        definition, version = await self._require_pair(
            tenant_id=context.identity.tenant_id,
            definition_id=request.definition_id,
            version_id=request.version_id,
        )
        cabinet_id = await self._resolve_scope(
            tenant_id=definition.tenant_id,
            scope_kind=request.scope_kind,
            scope_id=request.scope_id,
        )
        name = _capability_name(request.capability)
        already_revoked = await self._audit.has_marker(
            tenant_id=context.identity.tenant_id,
            marker_kind="grant_revoked",
            marker_values={
                "version_id": version.id,
                "scope_kind": request.scope_kind.value,
                "scope_id": request.scope_id,
                "capability": name.value,
            },
        )
        if already_revoked:
            raise _error(
                DynamicAppErrorCode.GRANT_ALREADY_REVOKED,
                409,
                "capability already revoked for this version and scope",
            )

        grant = await self._repository.get_grant(
            app_version_id=version.id,
            scope_kind=request.scope_kind,
            scope_id=request.scope_id,
        )
        if grant is None:
            raise _not_found("grant not found for this version and scope")

        retained: list[CapabilityGrant] = []
        found = False
        for item in grant.grants:
            if item.name is name:
                found = True
                if item.decision is CapabilityDecision.DENIED:
                    raise _error(
                        DynamicAppErrorCode.GRANT_ALREADY_REVOKED,
                        409,
                        "capability is already denied",
                    )
                # Deny is retained as evidence; the row is never deleted.
                retained.append(
                    item.model_copy(
                        update={
                            "decision": CapabilityDecision.DENIED,
                            "expires_at": _now(),
                        }
                    )
                )
            else:
                retained.append(item)
        if not found:
            raise _not_found("capability was never granted for this version and scope")

        updated = AppGrant(
            id=grant.id,
            app_definition_id=grant.app_definition_id,
            app_version_id=grant.app_version_id,
            scope=grant.scope,
            grants=tuple(retained),
            issued_at=grant.issued_at,
            expires_at=grant.expires_at,
        )
        await self._repository.replace_grant(updated)
        view = _grant_view(updated)
        view["revoked_capability"] = name.value
        await self._append_audit(
            context,
            request_digest=request_digest,
            event_type=DynamicAppAuditEvent.GRANT_REVOKED,
            result=view,
            cabinet_id=cabinet_id,
            project_id=request.scope_id if request.scope_kind is AppScopeKind.PROJECT else None,
            reason=request.reason,
            markers={
                "version_id": version.id,
                "grant_id": grant.id,
                "capability": name.value,
                "scope_kind": request.scope_kind.value,
                "scope_id": request.scope_id,
                "revoked": "true",
            },
        )
        await self._commit()
        return MutationReceipt(data=view, trace_id=context.trace_id)

    async def list_grants(
        self, *, identity_tenant_id: str, definition_id: str, version_id: str
    ) -> dict[str, Any]:
        definition, version = await self._require_pair(
            tenant_id=identity_tenant_id,
            definition_id=definition_id,
            version_id=version_id,
        )
        rows = await self._repository.list_grants(
            definition_id=definition.id, version_id=version.id
        )
        return {"items": [_grant_view(item) for item in rows]}

    # ------------------------------------------------------------------- internals --

    def _require_identity(self, context: MutationContext) -> None:
        if not context.identity.tenant_id.strip():
            raise _error(
                DynamicAppErrorCode.TENANT_CONTEXT_REQUIRED,
                400,
                "tenant must be resolved server-side from the principal",
            )
        if not context.idempotency_key.strip():
            raise _error(
                DynamicAppErrorCode.IDEMPOTENCY_KEY_REQUIRED,
                400,
                "Idempotency-Key header is required for mutating operations",
            )

    async def _replay(
        self, context: MutationContext, request_digest: str
    ) -> MutationReceipt | None:
        """Exact replay returns the original receipt; a changed digest is a conflict."""
        record = await self._audit.find(
            tenant_id=context.identity.tenant_id, scope_key=context.scope
        )
        if record is None:
            return None
        if record.request_digest != request_digest:
            raise _error(
                DynamicAppErrorCode.IDEMPOTENCY_REPLAY,
                409,
                "Idempotency-Key was already used with a different request body",
            )
        return MutationReceipt(
            data=dict(record.result),
            trace_id=record.trace_id,
            replayed=True,
        )

    async def _append_audit(
        self,
        context: MutationContext,
        *,
        request_digest: str,
        event_type: str,
        result: Mapping[str, Any],
        cabinet_id: str | None = None,
        project_id: str | None = None,
        reason: str | None = None,
        markers: Mapping[str, str] | None = None,
    ) -> None:
        record = AuditRecord(
            event_type=event_type,
            tenant_id=context.identity.tenant_id,
            actor_id=context.identity.actor_id,
            scope_key=context.scope,
            operation=context.operation.value,
            request_digest=request_digest,
            # Store only the redacted public projection, never the raw manifest or body.
            result=_redact(result),
            trace_id=context.trace_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            reason=reason,
            markers=dict(markers or {}),
        )
        try:
            await self._audit.append(record)
        except AuditConflict:
            await self._repository.rollback()
            raise _error(
                DynamicAppErrorCode.IDEMPOTENCY_REPLAY,
                409,
                "a concurrent request already used this Idempotency-Key",
            ) from None

    async def _commit(self) -> None:
        try:
            await self._repository.flush()
        except Exception:
            # Nothing is committed, but the session must be left usable and clean.
            await self._repository.rollback()
            raise
        await self._repository.commit()

    async def _require_definition(
        self, *, tenant_id: str, app_id: str | None = None, definition_id: str | None = None
    ) -> AppDefinition:
        if not tenant_id.strip():
            raise _error(
                DynamicAppErrorCode.TENANT_CONTEXT_REQUIRED,
                400,
                "resolved tenant context is required",
            )
        if definition_id is not None:
            definition = await self._repository.get_definition(
                tenant_id=tenant_id, definition_id=definition_id
            )
        else:
            assert app_id is not None
            definition = await self._repository.get_definition_by_app_id(
                tenant_id=tenant_id, app_id=app_id
            )
        # A row owned by another tenant is indistinguishable from a missing row.
        if definition is None or definition.tenant_id != tenant_id:
            raise _not_found("application not found")
        return definition

    async def _require_pair(
        self, *, tenant_id: str, definition_id: str, version_id: str
    ) -> tuple[AppDefinition, AppVersion]:
        definition = await self._require_definition(
            tenant_id=tenant_id, definition_id=definition_id
        )
        version = await self._repository.get_version(
            definition_id=definition.id, version_id=version_id
        )
        # The composite (version, definition) identity is re-checked in the application
        # layer as well as by the composite FK, so a cross-definition id can never pass.
        if version is None or version.app_definition_id != definition.id:
            raise _not_found("version not found for this application")
        return definition, version

    async def _resolve_install_version(
        self, *, definition: AppDefinition, version_id: str | None
    ) -> AppVersion:
        if version_id is None:
            if definition.current_version_id is None:
                raise _error(
                    DynamicAppErrorCode.VERSION_NOT_PUBLISHED,
                    409,
                    "definition has no published current version to install",
                )
            version_id = definition.current_version_id
        version = await self._repository.get_version(
            definition_id=definition.id, version_id=version_id
        )
        if version is None or version.app_definition_id != definition.id:
            raise _not_found("version not found for this application")
        require_published_version(version.status)
        if definition.current_version_id != version.id:
            # Installing a superseded version would silently resurrect an older snapshot.
            raise _error(
                DynamicAppErrorCode.VERSION_NOT_PUBLISHED,
                409,
                "installs must reference the definition current version",
            )
        return version

    async def _resolve_scope(
        self, *, tenant_id: str, scope_kind: AppScopeKind, scope_id: str
    ) -> str:
        if scope_kind is AppScopeKind.TENANT:
            raise _error(
                DynamicAppErrorCode.INVALID_SCOPE,
                422,
                "cabinet or project scope is required",
            )
        try:
            return await self._scope_authority.require(
                tenant_id=tenant_id, scope_kind=scope_kind, scope_id=scope_id
            )
        except AppError as exc:
            # Ownership failures stay 404 so a caller cannot probe foreign scopes.
            if exc.status in {403, 404}:
                raise _not_found("scope not found in this tenant") from None
            raise

    def _validate_capabilities(
        self, *, request: IssueGrantRequest, requested: Mapping[str, RequestedCapability]
    ) -> tuple[CapabilityGrant, ...]:
        result: list[CapabilityGrant] = []
        for item in request.capabilities:
            name = _capability_name(item.name)
            if name.value not in requested:
                # A grant may only cover what the immutable manifest requested.
                raise _error(
                    DynamicAppErrorCode.CAPABILITY_NOT_REQUESTED,
                    409,
                    f"capability {name.value} was not requested by this version",
                )
            expires_at = _aware(item.expires_at) or _aware(request.expires_at)
            if expires_at is not None and expires_at <= _now():
                raise _error(
                    DynamicAppErrorCode.VALIDATION_FAILED,
                    422,
                    "grant expiry must be in the future",
                )
            result.append(
                CapabilityGrant(
                    name=name,
                    decision=item.decision,
                    constraints=dict(item.constraints) if item.constraints else None,
                    expires_at=expires_at,
                    granted_by=GrantActor.ADMINISTRATOR,
                )
            )
        return tuple(result)


class _ValidatedManifest:
    __slots__ = ("app_id", "manifest_version", "requested_capabilities")

    def __init__(
        self,
        *,
        app_id: str,
        manifest_version: str,
        requested_capabilities: tuple[RequestedCapability, ...],
    ) -> None:
        self.app_id = app_id
        self.manifest_version = manifest_version
        self.requested_capabilities = requested_capabilities


def _validate_manifest(
    manifest: Mapping[str, Any], *, artifact_digest: str
) -> _ValidatedManifest:
    """Validate the v1 manifest shape before any row is written.

    Mirrors the persisted CHECK constraints (protocol, required keys, digest format,
    requested-capabilities equality) so a bad upload fails as ``422`` instead of a raw
    database integrity error.
    """
    if not _DIGEST_PATTERN.fullmatch(artifact_digest):
        raise _error(
            DynamicAppErrorCode.VALIDATION_FAILED,
            422,
            "artifact_digest must be sha256:<64 lowercase hex>",
        )
    if manifest.get("protocol") != "1.0":
        raise _error(
            DynamicAppErrorCode.VALIDATION_FAILED, 422, "manifest protocol must be '1.0'"
        )
    app_id = manifest.get("appId")
    if not isinstance(app_id, str) or not _APP_ID_PATTERN.fullmatch(app_id):
        raise _error(
            DynamicAppErrorCode.VALIDATION_FAILED, 422, "manifest appId is invalid"
        )
    display_name = manifest.get("displayName")
    if not isinstance(display_name, str) or not display_name.strip():
        raise _error(
            DynamicAppErrorCode.VALIDATION_FAILED, 422, "manifest displayName is required"
        )
    manifest_version = manifest.get("version")
    if not isinstance(manifest_version, str) or not _SEMVER_PATTERN.fullmatch(
        manifest_version
    ):
        raise _error(
            DynamicAppErrorCode.VALIDATION_FAILED,
            422,
            "manifest version must be semantic version",
        )
    entrypoints = manifest.get("entrypoints")
    if not isinstance(entrypoints, Mapping) or not entrypoints:
        raise _error(
            DynamicAppErrorCode.VALIDATION_FAILED,
            422,
            "manifest entrypoints must be a non-empty object",
        )
    plugin = entrypoints.get("plugin")
    if isinstance(plugin, Mapping):
        artifact = plugin.get("artifact")
        if isinstance(artifact, Mapping) and artifact.get("sha256") is not None:
            if artifact["sha256"] != artifact_digest.removeprefix("sha256:"):
                raise _error(
                    DynamicAppErrorCode.VALIDATION_FAILED,
                    422,
                    "manifest plugin artifact sha256 must equal artifact_digest",
                )
    raw_requested = manifest.get("requestedCapabilities")
    if not isinstance(raw_requested, list):
        raise _error(
            DynamicAppErrorCode.VALIDATION_FAILED,
            422,
            "manifest requestedCapabilities must be an array",
        )
    requested: list[RequestedCapability] = []
    for entry in raw_requested:
        if not isinstance(entry, Mapping):
            raise _error(
                DynamicAppErrorCode.VALIDATION_FAILED,
                422,
                "requested capability entries must be objects",
            )
        try:
            requested.append(
                RequestedCapability(
                    name=CapabilityName(entry.get("name")),
                    reason=entry.get("reason"),
                    constraints=entry.get("constraints"),
                    optional=bool(entry.get("optional", False)),
                )
            )
        except (ValueError, TypeError) as exc:
            raise _error(
                DynamicAppErrorCode.VALIDATION_FAILED,
                422,
                f"invalid requested capability: {exc}",
            ) from None
    names = [item.name.value for item in requested]
    if len(set(names)) != len(names):
        raise _error(
            DynamicAppErrorCode.VALIDATION_FAILED,
            422,
            "requested capabilities must be unique",
        )
    return _ValidatedManifest(
        app_id=app_id,
        manifest_version=manifest_version,
        requested_capabilities=tuple(requested),
    )


def _capability_name(value: str) -> CapabilityName:
    try:
        return CapabilityName(value)
    except ValueError:
        raise _error(
            DynamicAppErrorCode.VALIDATION_FAILED,
            422,
            f"unknown capability: {value}",
        ) from None


def _short_id(seed: object, now: datetime) -> str:
    return digest_of({"seed": seed, "at": now.isoformat()})[7:23]


def _page_size(limit: int) -> int:
    return max(1, min(int(limit), _MAX_PAGE))


def _page(items: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    size = _page_size(limit)
    has_more = len(items) > size
    page = items[:size]
    return {
        "items": page,
        "has_more": has_more,
        "next_cursor": page[-1]["id"] if has_more and page else None,
    }


def _redact(result: Mapping[str, Any]) -> dict[str, Any]:
    """Drop anything that could carry secrets or user content from audit payloads."""
    if "manifest" in result:
        redacted = {key: value for key, value in result.items() if key != "manifest"}
        return redacted
    return dict(result)


__all__ = ["DynamicAppCrudService"]

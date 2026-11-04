"""Fail-closed lifecycle application service for dynamic app registry records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar

from pydantic import BaseModel

from prodavan.domain.dynamic_apps import AppLifecycleStatus

from .contracts import (
    DraftCommand,
    LifecycleCommand,
    PublishCommand,
    QuarantineEvidence,
    RegistryOutcome,
    RegistryReason,
    RegistryResult,
    RollbackCommand,
)
from .ports import DynamicAppRegistryRepository, RegistryQuarantinePort

_ContractT = TypeVar("_ContractT", bound=BaseModel)


def _evolve(model: _ContractT, **changes: object) -> _ContractT:
    """Return a transitioned copy of a frozen domain contract.

    ``AppDefinition``/``AppVersion`` are frozen **pydantic** contracts, not
    dataclasses: ``dataclasses.replace`` raises ``TypeError`` on them, so every
    lifecycle transition died before reaching the repository port.

    The copy is built by re-validating through ``model_validate`` instead of
    ``model_copy``, because ``model_copy(update=...)`` skips validators. A
    transition that would produce an invalid contract (``published`` without a
    current version, rollback metadata with only one side of the pair) must
    fail loudly here rather than be written to the registry and projected to
    grant consumers.
    """
    return type(model).model_validate({**model.model_dump(), **changes})


class DynamicAppRegistryService:
    """Apply lifecycle transitions without depending on DB, ORM, or migration APIs."""

    def __init__(
        self,
        repository: DynamicAppRegistryRepository,
        *,
        quarantine: RegistryQuarantinePort | None = None,
    ) -> None:
        self._repository = repository
        self._quarantine = quarantine

    async def draft(self, command: DraftCommand) -> RegistryResult:
        if command.expected_artifact_digest is not None and command.expected_artifact_digest != command.version.artifact_digest:
            return await self._quarantine_result(command.version.id, RegistryReason.ARTIFACT_DIGEST_CONFLICT, command.version.artifact_digest)
        if command.version.app_definition_id != command.definition.id:
            return await self._quarantine_result(command.version.id, RegistryReason.VERSION_DEFINITION_MISMATCH, command.version.artifact_digest)
        if command.definition.status is not AppLifecycleStatus.DRAFT or command.version.status is not AppLifecycleStatus.DRAFT:
            return self._negative(RegistryOutcome.CONFLICT, RegistryReason.INVALID_TRANSITION)
        await self._repository.append_draft(definition=command.definition, version=command.version, actor=command.actor)
        return RegistryResult(outcome=RegistryOutcome.APPLIED, definition=command.definition, version=command.version)

    async def publish(self, command: PublishCommand) -> RegistryResult:
        definition, version, result = await self._load_pair(command.definition_id, command.version_id)
        if result is not None:
            return result
        assert definition is not None and version is not None
        if version.app_definition_id != definition.id:
            return await self._quarantine_result(version.id, RegistryReason.VERSION_DEFINITION_MISMATCH, version.artifact_digest)
        if version.manifest.get("appId") != definition.app_id or version.manifest.get("version") != version.manifest_version:
            return await self._quarantine_result(version.id, RegistryReason.VERSION_DEFINITION_MISMATCH, version.artifact_digest)
        if command.expected_artifact_digest is not None and command.expected_artifact_digest != version.artifact_digest:
            return await self._quarantine_result(version.id, RegistryReason.ARTIFACT_DIGEST_CONFLICT, version.artifact_digest)
        if command.expected_current_version_id is not None and definition.current_version_id != command.expected_current_version_id:
            return self._negative(RegistryOutcome.CONFLICT, RegistryReason.CURRENT_VERSION_CONFLICT)
        if version.status is not AppLifecycleStatus.DRAFT:
            return self._negative(RegistryOutcome.CONFLICT, RegistryReason.VERSION_ALREADY_PUBLISHED)
        published_at = datetime.now(UTC)
        version = _evolve(version, status=AppLifecycleStatus.PUBLISHED, published_at=published_at)
        definition = _evolve(definition, status=AppLifecycleStatus.PUBLISHED, current_version_id=version.id, updated_at=published_at)
        if any(
            grant.app_definition_id != definition.id
            or grant.app_version_id != version.id
            or grant.scope.id != definition.scope.id
            or grant.scope.kind != definition.scope.kind
            for grant in command.grants
        ):
            return await self._quarantine_result(version.id, RegistryReason.UNSUPPORTED_GRANT, version.artifact_digest)
        try:
            await self._repository.transition_and_project(
                definition=definition,
                version=version,
                status=version.status.value,
                current_version_id=version.id,
                expected_current_version_id=command.expected_current_version_id,
                expected_artifact_digest=command.expected_artifact_digest,
                grants=command.grants,
                actor=command.actor,
            )
        except Exception:
            return await self._quarantine_result(version.id, RegistryReason.GRANT_PROJECTION_CONFLICT, version.artifact_digest)
        return RegistryResult(outcome=RegistryOutcome.APPLIED, definition=definition, version=version, projected_grants=command.grants)

    async def disable(self, command: LifecycleCommand) -> RegistryResult:
        return await self._transition(command, AppLifecycleStatus.DISABLED)

    async def revoke(self, command: LifecycleCommand) -> RegistryResult:
        return await self._transition(command, AppLifecycleStatus.REVOKED)

    async def rollback(self, command: RollbackCommand) -> RegistryResult:
        definition, target, result = await self._load_pair(command.definition_id, command.target_version_id)
        if result is not None:
            return result
        assert definition is not None and target is not None
        if target.status is not AppLifecycleStatus.PUBLISHED or definition.current_version_id == target.id:
            return self._negative(RegistryOutcome.CONFLICT, RegistryReason.ROLLBACK_TARGET_INVALID)
        if command.expected_current_version_id is not None and definition.current_version_id != command.expected_current_version_id:
            return self._negative(RegistryOutcome.CONFLICT, RegistryReason.CURRENT_VERSION_CONFLICT)
        if command.expected_artifact_digest is not None and command.expected_artifact_digest != target.artifact_digest:
            return await self._quarantine_result(target.id, RegistryReason.ARTIFACT_DIGEST_CONFLICT, target.artifact_digest)
        now = datetime.now(UTC)
        target = _evolve(
            target,
            rollback=_evolve(
                target.rollback,
                from_version_id=definition.current_version_id,
                to_version_id=target.id,
                reason=command.reason,
                occurred_at=now,
            ),
        )
        definition = _evolve(definition, status=AppLifecycleStatus.PUBLISHED, current_version_id=target.id, updated_at=now)
        try:
            await self._repository.transition_and_project(
                definition=definition,
                version=target,
                status=target.status.value,
                current_version_id=target.id,
                expected_current_version_id=command.expected_current_version_id,
                expected_artifact_digest=command.expected_artifact_digest,
                grants=(),
                actor=command.actor,
            )
        except Exception:
            return await self._quarantine_result(target.id, RegistryReason.ROLLBACK_TARGET_INVALID, target.artifact_digest)
        return RegistryResult(outcome=RegistryOutcome.APPLIED, definition=definition, version=target)

    async def _transition(self, command: LifecycleCommand, status: AppLifecycleStatus) -> RegistryResult:
        definition, version, result = await self._load_pair(command.definition_id, command.version_id)
        if result is not None:
            return result
        assert definition is not None and version is not None
        if command.expected_current_version_id is not None and definition.current_version_id != command.expected_current_version_id:
            return self._negative(RegistryOutcome.CONFLICT, RegistryReason.CURRENT_VERSION_CONFLICT)
        allowed_sources = (
            {AppLifecycleStatus.PUBLISHED}
            if status is AppLifecycleStatus.DISABLED
            else {AppLifecycleStatus.PUBLISHED, AppLifecycleStatus.DISABLED}
        )
        if version.status not in allowed_sources:
            return self._negative(RegistryOutcome.CONFLICT, RegistryReason.INVALID_TRANSITION)
        # Leaving ``published`` ends the current publication window: the domain
        # contract admits ``published_at`` only while the version is published
        # (``AppVersion.validate_publication``), so a disable/revoke transition
        # must clear it instead of carrying the old timestamp into a non-serving
        # state. The publication history itself is not lost: the lifecycle audit
        # row and ``created_at`` remain the evidence.
        version = _evolve(
            version,
            status=status,
            published_at=None if status is not AppLifecycleStatus.PUBLISHED else version.published_at,
        )
        now = datetime.now(UTC)
        # A historical version transition must not change the app's current pointer.
        definition_status = status if definition.current_version_id == version.id else definition.status
        definition = _evolve(definition, status=definition_status, updated_at=now)
        try:
            await self._repository.transition_and_project(
                definition=definition,
                version=version,
                status=status.value,
                current_version_id=definition.current_version_id,
                expected_current_version_id=command.expected_current_version_id,
                expected_artifact_digest=command.expected_artifact_digest,
                grants=(),
                actor=command.actor,
            )
        except Exception:
            return await self._quarantine_result(version.id, RegistryReason.INVALID_TRANSITION, version.artifact_digest)
        return RegistryResult(outcome=RegistryOutcome.APPLIED, definition=definition, version=version)

    async def _load_pair(self, definition_id: str, version_id: str):
        definition = await self._repository.get_definition(definition_id=definition_id)
        if definition is None:
            return None, None, self._negative(RegistryOutcome.MISSING, RegistryReason.DEFINITION_MISSING)
        version = await self._repository.get_version(definition_id=definition_id, version_id=version_id)
        if version is None:
            return definition, None, self._negative(RegistryOutcome.MISSING, RegistryReason.VERSION_MISSING)
        return definition, version, None

    async def _quarantine_result(self, source_id: str, reason: RegistryReason, digest: str) -> RegistryResult:
        evidence = QuarantineEvidence(reason=reason, source_kind="dynamic_app_registry", source_id=source_id, source_digest=digest, observed_at=datetime.now(UTC))
        if self._quarantine is not None:
            await self._quarantine.append(evidence)
        return RegistryResult(outcome=RegistryOutcome.QUARANTINED, reason=reason, quarantine=evidence)

    @staticmethod
    def _negative(outcome: RegistryOutcome, reason: RegistryReason) -> RegistryResult:
        return RegistryResult(outcome=outcome, reason=reason)


__all__ = ["DynamicAppRegistryService"]

"""Fail-closed definition/version resolution service for M04.e."""

from __future__ import annotations

from prodavan.domain.dynamic_apps import AppLifecycleStatus

from .contracts import (
    DefinitionResolution,
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
    DefinitionVersionQuery,
    DefinitionVersionRef,
)
from .ports import DefinitionVersionRepository


class DynamicAppDefinitionService:
    """Resolve one immutable version only after all owner/history gates pass."""

    def __init__(self, repository: DefinitionVersionRepository) -> None:
        self._repository = repository

    async def resolve(self, query: DefinitionVersionQuery) -> DefinitionResolution:
        definitions = tuple(await self._repository.list_definitions(tenant_id=query.tenant_id, app_id=query.app_id))
        if not definitions:
            return self._negative(DefinitionResolutionOutcome.MISSING, DefinitionResolutionReason.MISSING_DEFINITION)
        if len(definitions) != 1:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_DEFINITION_HISTORY)

        definition = definitions[0]
        if definition.tenant_id != query.tenant_id:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.CROSS_TENANT_DEFINITION)
        if definition.app_id != query.app_id:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.CROSS_APP_DEFINITION)
        if definition.scope.kind.value != "tenant" or definition.scope.id != definition.tenant_id:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_DEFINITION_HISTORY)
        if definition.status is AppLifecycleStatus.PUBLISHED and definition.current_version_id is None:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_DEFINITION_HISTORY)

        versions = tuple(
            await self._repository.list_versions(
                app_definition_id=definition.id, manifest_version=query.manifest_version
            )
        )
        if not versions:
            return self._negative(DefinitionResolutionOutcome.MISSING, DefinitionResolutionReason.MISSING_VERSION)
        if len(versions) != 1:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.AMBIGUOUS_VERSION)

        version = versions[0]
        if version.app_definition_id != definition.id:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.CROSS_DEFINITION_VERSION)
        if version.manifest.get("appId") != definition.app_id or version.manifest.get("version") != version.manifest_version:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        if query.expected_artifact_digest is not None and version.artifact_digest != query.expected_artifact_digest:
            return self._negative(DefinitionResolutionOutcome.CONFLICT, DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT)
        if version.status is AppLifecycleStatus.PUBLISHED:
            if version.published_at is None:
                return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_VERSION_HISTORY)
            if definition.status is not AppLifecycleStatus.PUBLISHED or definition.current_version_id != version.id:
                return self._negative(DefinitionResolutionOutcome.CONFLICT, DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH)
        elif definition.status is AppLifecycleStatus.PUBLISHED:
            return self._negative(DefinitionResolutionOutcome.CONFLICT, DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH)
        elif version.status not in {AppLifecycleStatus.DRAFT, AppLifecycleStatus.DISABLED, AppLifecycleStatus.REVOKED}:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.DISALLOWED_VERSION_STATUS)

        history = tuple(await self._repository.list_definition_versions(app_definition_id=definition.id))
        if not history:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        if any(item.app_definition_id != definition.id for item in history):
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.CROSS_DEFINITION_VERSION)
        matching_history = tuple(item for item in history if item.id == version.id)
        if len(matching_history) != 1:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        if any(
            item.manifest.get("appId") != definition.app_id
            or item.manifest.get("version") != item.manifest_version
            or (item.status is AppLifecycleStatus.PUBLISHED and item.published_at is None)
            or (item.status is not AppLifecycleStatus.PUBLISHED and item.published_at is not None)
            for item in history
        ):
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        if len({item.manifest_version for item in history}) != len(history) or len({item.artifact_digest for item in history}) != len(history):
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        selected_history = matching_history[0]
        if (
            selected_history.manifest_version != version.manifest_version
            or selected_history.artifact_digest != version.artifact_digest
            or selected_history.status is not version.status
            or selected_history.published_at != version.published_at
        ):
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_VERSION_HISTORY)

        return DefinitionResolution(
            outcome=DefinitionResolutionOutcome.RESOLVED,
            definition=definition,
            version=version,
            reference=DefinitionVersionRef(
                tenant_id=definition.tenant_id,
                app_id=definition.app_id,
                app_definition_id=definition.id,
                app_version_id=version.id,
                manifest_version=version.manifest_version,
                artifact_digest=version.artifact_digest,
            ),
        )

    @staticmethod
    def _negative(outcome: DefinitionResolutionOutcome, reason: DefinitionResolutionReason) -> DefinitionResolution:
        return DefinitionResolution(outcome=outcome, reason=reason)


__all__ = ["DynamicAppDefinitionService"]

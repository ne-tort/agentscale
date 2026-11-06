"""Application-only orchestration for the module capability migration slice.

The pipeline prepares typed definition/version candidates and routes durable
writes through ports. It does not read/write legacy module rows, expose an API,
or invoke runtime/DSL interpreters.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .contracts import (
    CapabilityDefinition,
    CapabilityVersion,
    DefinitionLifecycle,
    DefinitionNormalizationResult,
    NormalizationResult,
    OwnerScope,
    PublicationGateEvidence,
    VersionLifecycle,
)
from .dependencies import DependencyValidation, validate_module_dependencies
from .lifecycle import ModuleCapabilityLifecycleService, PublicationResult
from .normalization import normalize_legacy_module


class ModuleCapabilityDraftPort(Protocol):
    """Durable owner for drafts, quarantine, and immutable snapshots."""

    async def persist_draft(self, *, definition: CapabilityDefinition, version: CapabilityVersion) -> None:
        ...

    async def append_quarantine(
        self,
        *,
        module_id: str,
        definition_id: str,
        source_digest: str,
        reasons: Sequence[str],
        correlation_id: str,
    ) -> None:
        ...


@dataclass(frozen=True, slots=True)
class ModuleCapabilityPreparation:
    definition: CapabilityDefinition | None
    version: CapabilityVersion | None
    dependency_validation: DependencyValidation | None = None
    reasons: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.definition is not None and self.version is not None and not self.reasons and bool(self.dependency_validation and self.dependency_validation.valid)


class ModuleCapabilityPipeline:
    """Normalize and stage one version; publication remains gate-controlled."""

    def __init__(
        self,
        *,
        draft_port: ModuleCapabilityDraftPort,
        lifecycle: ModuleCapabilityLifecycleService,
    ) -> None:
        self._draft_port = draft_port
        self._lifecycle = lifecycle

    def normalize_definition(
        self,
        *,
        definition_id: str,
        module_id: str,
        name: str,
        owner_scope: OwnerScope | None,
        lifecycle: str = "draft",
    ) -> DefinitionNormalizationResult:
        try:
            definition = CapabilityDefinition(
                definition_id=definition_id,
                module_id=module_id,
                name=name,
                lifecycle=DefinitionLifecycle(lifecycle),
                owner_scope=owner_scope,
            )
            return DefinitionNormalizationResult(definition, ())
        except (TypeError, ValueError) as exc:
            from .contracts import NormalizationIssue
            return DefinitionNormalizationResult(None, (NormalizationIssue("invalid_definition", "definition", str(exc)),))

    def normalize_version(self, **kwargs: object) -> NormalizationResult:
        return normalize_legacy_module(**kwargs)  # type: ignore[arg-type]

    def validate_dependencies(
        self,
        *,
        version: CapabilityVersion,
        available: Mapping[str, CapabilityVersion],
    ) -> DependencyValidation:
        return validate_module_dependencies(candidate=version, available=available)

    async def stage(
        self,
        *,
        definition: CapabilityDefinition,
        version: CapabilityVersion,
        available_versions: Mapping[str, CapabilityVersion],
        correlation_id: str,
    ) -> ModuleCapabilityPreparation:
        if definition.owner_scope is None:
            reasons = ("durable_owner_scope_missing",)
            await self._draft_port.append_quarantine(
                module_id=definition.module_id,
                definition_id=definition.definition_id,
                source_digest=version.content_digest,
                reasons=reasons,
                correlation_id=correlation_id,
            )
            return ModuleCapabilityPreparation(definition, None, reasons=reasons)
        if version.lifecycle is not VersionLifecycle.DRAFT or definition.lifecycle is DefinitionLifecycle.DEPRECATED:
            reasons = ("only_active_definition_draft_version_can_be_staged",)
            await self._draft_port.append_quarantine(
                module_id=version.module_id,
                definition_id=definition.definition_id,
                source_digest=version.content_digest,
                reasons=reasons,
                correlation_id=correlation_id,
            )
            return ModuleCapabilityPreparation(definition, None, reasons=reasons)
        if version.definition_id != definition.definition_id or version.module_id != definition.module_id:
            reasons = ("definition_version_identity_mismatch",)
            await self._draft_port.append_quarantine(
                module_id=version.module_id,
                definition_id=definition.definition_id,
                source_digest=version.content_digest,
                reasons=reasons,
                correlation_id=correlation_id,
            )
            return ModuleCapabilityPreparation(definition, None, reasons=reasons)
        dependency_validation = validate_module_dependencies(candidate=version, available=available_versions)
        if not dependency_validation.valid:
            reasons = tuple(issue.code for issue in dependency_validation.issues)
            await self._draft_port.append_quarantine(
                module_id=version.module_id,
                definition_id=definition.definition_id,
                source_digest=version.content_digest,
                reasons=reasons,
                correlation_id=correlation_id,
            )
            return ModuleCapabilityPreparation(definition, None, dependency_validation, reasons)
        await self._draft_port.persist_draft(definition=definition, version=version)
        return ModuleCapabilityPreparation(definition, version, dependency_validation)

    async def normalize_and_stage(
        self,
        *,
        definition: CapabilityDefinition,
        source_digest: str,
        normalization_inputs: Mapping[str, object],
        available_versions: Mapping[str, CapabilityVersion],
        correlation_id: str,
    ) -> ModuleCapabilityPreparation:
        inputs = dict(normalization_inputs)
        if inputs.get("source_digest") != source_digest:
            reasons = ("source_digest_input_mismatch",)
            await self._draft_port.append_quarantine(
                module_id=definition.module_id,
                definition_id=definition.definition_id,
                source_digest=source_digest,
                reasons=reasons,
                correlation_id=correlation_id,
            )
            return ModuleCapabilityPreparation(definition, None, reasons=reasons)
        result = normalize_legacy_module(**inputs)  # type: ignore[arg-type]
        if result.candidate is None:
            reasons = tuple(f"{issue.code}:{issue.path}" for issue in result.issues)
            await self._draft_port.append_quarantine(
                module_id=definition.module_id,
                definition_id=definition.definition_id,
                source_digest=source_digest,
                reasons=reasons,
                correlation_id=correlation_id,
            )
            return ModuleCapabilityPreparation(definition, None, reasons=reasons)
        return await self.stage(
            definition=definition,
            version=result.candidate,
            available_versions=available_versions,
            correlation_id=correlation_id,
        )

    async def publish(
        self,
        *,
        definition: CapabilityDefinition,
        version: CapabilityVersion,
        evidence: PublicationGateEvidence,
    ) -> PublicationResult:
        return await self._lifecycle.publish(definition=definition, version=version, evidence=evidence)


__all__ = ["ModuleCapabilityDraftPort", "ModuleCapabilityPipeline", "ModuleCapabilityPreparation"]

"""Acceptance checks for the typed module capability pipeline; not executed."""
from __future__ import annotations

import asyncio

from .acceptance_fixtures import definition, version
from prodavan.application.migration.module_capabilities.lifecycle import ModuleCapabilityLifecycleService, PublicationCommit
from prodavan.application.migration.module_capabilities.pipeline import ModuleCapabilityPipeline


class DraftStore:
    def __init__(self) -> None:
        self.drafts = []
        self.quarantines = []

    async def persist_draft(self, *, definition, version) -> None:
        self.drafts.append((definition, version))

    async def append_quarantine(self, **payload) -> None:
        self.quarantines.append(payload)


class Gate:
    async def verify_publication_gate(self, evidence) -> bool:
        return False


class Repository:
    async def append_published_snapshot(self, *, snapshot, transitions):
        return PublicationCommit(True)

    async def append_transition(self, *, transition):
        from prodavan.application.migration.module_capabilities.lifecycle import TransitionCommit
        return TransitionCommit(True)


def run(coro):
    return asyncio.run(coro)


def test_stage_persists_draft_only_after_dependency_gate() -> None:
    store = DraftStore()
    pipeline = ModuleCapabilityPipeline(
        draft_port=store,
        lifecycle=ModuleCapabilityLifecycleService(repository=Repository(), gate_verifier=Gate()),
    )
    result = run(pipeline.stage(
        definition=definition(),
        version=version(),
        available_versions={},
        correlation_id="stage-correlation",
    ))
    assert result.accepted
    assert len(store.drafts) == 1
    assert not store.quarantines


def test_stage_quarantines_identity_mismatch_without_draft_write() -> None:
    store = DraftStore()
    pipeline = ModuleCapabilityPipeline(
        draft_port=store,
        lifecycle=ModuleCapabilityLifecycleService(repository=Repository(), gate_verifier=Gate()),
    )
    result = run(pipeline.stage(
        definition=definition(),
        version=version(module_id="other_module"),
        available_versions={},
        correlation_id="stage-correlation",
    ))
    assert not result.accepted
    assert result.reasons == ("definition_version_identity_mismatch",)
    assert not store.drafts
    assert store.quarantines[0]["reasons"] == ["definition_version_identity_mismatch"]


def test_stage_quarantines_missing_dependency_and_never_falls_back_to_legacy() -> None:
    store = DraftStore()
    pipeline = ModuleCapabilityPipeline(
        draft_port=store,
        lifecycle=ModuleCapabilityLifecycleService(repository=Repository(), gate_verifier=Gate()),
    )
    result = run(pipeline.stage(
        definition=definition(),
        version=version(dependencies=("missing_module",)),
        available_versions={},
        correlation_id="stage-correlation",
    ))
    assert not result.accepted
    assert "missing_dependency" in result.reasons
    assert not store.drafts
    assert store.quarantines

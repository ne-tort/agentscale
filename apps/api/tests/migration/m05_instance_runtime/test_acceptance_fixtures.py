"""Executable acceptance fixtures for MIG-M05-02; intentionally not run during freeze."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from prodavan.application.migration.m05_instance_runtime.runtime import (
    FailClosedResolvePluginInstance,
    ImportCheckpoint,
    ImportOutcome,
    InstanceImportCandidate,
    InstanceImportService,
    RuntimeAuditRecord,
    RuntimeSnapshot,
    WriterCasResult,
    advance_runtime_generation,
    advance_writer_generation,
)


class Reader:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def read_page(self, *, after_source_id, limit):
        self.calls += 1
        return self.rows[:limit]


class Store:
    def __init__(self, prior=None):
        self.prior = prior
        self.commits = []

    async def find(self, source_kind, source_id, schema_version):
        return self.prior

    async def commit_record_and_checkpoint(self, record):
        self.commits.append(record)


class Audit:
    def __init__(self):
        self.records = []

    async def append(self, record):
        self.records.append(record)


class Resolver:
    def __init__(self):
        self.calls = 0

    async def resolve(self, **query):
        self.calls += 1
        return {"accepted": True}


class Lifecycle:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def compare_and_set_writer(self, **kwargs):
        self.calls.append(("writer", kwargs))
        return self.result

    async def compare_and_set_runtime(self, **kwargs):
        self.calls.append(("runtime", kwargs))
        return self.result


@pytest.mark.asyncio
async def test_dependency_block_does_not_read_candidates():
    reader, store = Reader([InstanceImportCandidate("legacy", "a", "1", {"x": 1})]), Store()
    result = await InstanceImportService(reader, store).run_page(ImportCheckpoint("p"), correlation_id="c")
    assert result[0].outcome is ImportOutcome.BLOCKED
    assert reader.calls == 0 and store.commits == []


@pytest.mark.asyncio
async def test_same_digest_is_replay_without_second_commit():
    candidate = InstanceImportCandidate("legacy", "a", "1", {"x": 1})
    prior = type("Prior", (), {"source_digest": candidate.digest, "checkpoint": ImportCheckpoint("p", "a", 1)})()
    store = Store(prior)
    result = await InstanceImportService(Reader([candidate]), store, dependencies_ready=True).run_page(
        ImportCheckpoint("p"), correlation_id="c"
    )
    assert result[0].outcome is ImportOutcome.REPLAY
    assert store.commits == []


@pytest.mark.asyncio
async def test_changed_digest_conflicts_without_overwrite():
    candidate = InstanceImportCandidate("legacy", "a", "1", {"x": 2})
    prior = type("Prior", (), {"source_digest": "sha256:" + "0" * 64, "checkpoint": ImportCheckpoint("p", "a", 1)})()
    store = Store(prior)
    result = await InstanceImportService(Reader([candidate]), store, dependencies_ready=True).run_page(
        ImportCheckpoint("p"), correlation_id="c"
    )
    assert result[0].outcome is ImportOutcome.CONFLICT
    assert result[0].reason_code == "source_digest_changed"
    assert len(store.commits) == 1 and store.commits[0].outcome is ImportOutcome.CONFLICT


@pytest.mark.asyncio
async def test_resolver_default_denies_and_emits_redacted_audit():
    audit, delegate = Audit(), Resolver()
    result = await FailClosedResolvePluginInstance(delegate, audit).resolve(
        correlation_id="opaque-correlation", plugin_instance_id="sensitive-id"
    )
    assert result is None and delegate.calls == 0
    assert audit.records == [RuntimeAuditRecord(
        "plugin_instance.resolve", "denied", "dependency_or_query_gate_missing", "opaque-correlation"
    )]


@pytest.mark.asyncio
async def test_writer_generation_cas_denies_stale_runtime_worker():
    lifecycle = Lifecycle(WriterCasResult(True, RuntimeSnapshot("pi", "active", 8, 4), "ok"))
    result = await advance_writer_generation(lifecycle, plugin_instance_id="pi",
                                            expected_runtime_generation=7, expected_writer_generation=3)
    assert not result.changed and result.reason_code == "stale_worker_denied"
    assert lifecycle.calls[0][1]["next_writer_generation"] == 4


@pytest.mark.asyncio
async def test_runtime_generation_must_advance_exactly_one():
    lifecycle = Lifecycle(WriterCasResult(True, RuntimeSnapshot("pi", "active", 9, 0), "ok"))
    result = await advance_runtime_generation(lifecycle, plugin_instance_id="pi", expected_runtime_generation=7)
    assert not result.changed and result.reason_code == "stale_runtime_denied"
    assert lifecycle.calls[0][1]["next_runtime_generation"] == 8

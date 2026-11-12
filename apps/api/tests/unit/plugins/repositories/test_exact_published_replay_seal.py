"""Exact PUBLISHED replay regressions for the SQL artifact registry store.

Retracted-finding follow-up to B3: SqlArtifactRegistryStore._publish_atomic
already returns the stored row for an exact published replay
(artifacts.py:732-743) without _values/UPDATE/lifecycle writes, and the
persisted projection reconstructs verification evidence with seal=None
(the process-local verifier seal is never persisted).

These tests pin that contract at the SQL-adapter level using the existing
stateful no-database session doubles: an exact replay (the operation retried by
both the unconsumed repeat path and any direct store retry) must issue only
SELECT statements, add no artifact/lifecycle rows, commit nothing and leave
created_at/published_at/lifecycle occurred_at/current CAS untouched, while the
returned record carries the full seal=None persisted projection consumed by the
service-level _exact_published_replay guard.

Written during the migration verification freeze; intentionally not executed.
"""
from __future__ import annotations

from typing import Any

import pytest
from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome

from .conftest import NOW
from .test_artifacts import _artifact_row, _verified_record
from .test_atomic_publication import _Database, _Factory, _store

pytestmark = pytest.mark.asyncio


def _only_selects(session: Any) -> None:
    """No INSERT (session.add) and no UPDATE/DELETE statements at all."""

    assert session.added == []
    assert session.commits == 0
    statements = [str(statement).lstrip().upper() for statement in session.statements]
    assert statements, "replay must at least re-read the stored row"
    assert all(sql.startswith("SELECT") for sql in statements), statements


def _committed_snapshot(factory: _Factory) -> dict[str, Any]:
    row = factory.database.rows[0]
    return {
        "id": row.id,
        "created_at": row.created_at,
        "published_at": row.published_at,
        "is_current": row.is_current,
        "lifecycle_version": row.lifecycle_version,
        "status": row.status,
        "oci_digest": row.oci_digest,
        "lifecycle": [
            (item.operation_token, item.occurred_at, item.actor, item.detail, item.operation)
            for item in factory.database.lifecycle_rows
        ],
    }


def _assert_seal_none_projection(replayed: Any, record: Any) -> None:
    """Full persisted projection returned by an exact replay/read-back."""

    original = record.verification
    evidence = replayed.verification
    assert evidence is not None
    # The process-local verifier seal cannot and must not be persisted.
    assert evidence.seal is None
    assert evidence.image_ref == original.image_ref
    assert evidence.artifact_digest == original.artifact_digest
    assert evidence.manifest_fingerprint == original.manifest_fingerprint
    assert evidence.identity == original.identity
    assert evidence.issuer == original.issuer
    assert evidence.sbom_verified is True
    assert evidence.provenance_verified is True
    assert evidence.scan_verified is True
    assert evidence.csp_verified is True
    assert evidence.observed_at == original.observed_at
    assert evidence.granted_capabilities == original.granted_capabilities
    # Prerequisites of the service-level _exact_published_replay guard.
    assert replayed.published is True
    assert replayed.outcome is RegistryOutcome.APPLIED
    assert replayed.quarantine is None
    assert replayed.publication == record.publication
    assert replayed.manifest_fingerprint == record.manifest_fingerprint
    assert replayed.digest == record.digest
    assert replayed.image_ref == record.image_ref


async def test_exact_replay_is_read_only_and_returns_seal_none_projection() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()

    await store.publish_async(record, evidence=record.verification, actor="test")
    assert len(factory.database.rows) == 1
    assert len(factory.database.lifecycle_rows) == 1
    assert len(factory.sessions) == 1
    committed = _committed_snapshot(factory)

    # Both an unconsumed exact repeat and any later direct store retry are the
    # same durable replay; an advanced clock proves nothing is re-timestamped.
    store.now = NOW.replace(year=NOW.year + 1)
    for attempt in (1, 2):
        replayed = await store.publish_async(record, evidence=record.verification, actor="test")

        session = factory.sessions[-1]
        assert len(factory.sessions) == 1 + attempt
        _only_selects(session)
        assert replayed.published_at == NOW
        assert replayed.created_at == NOW
        _assert_seal_none_projection(replayed, record)
        # Artifact row, current CAS and the single durable lifecycle fact are
        # byte-identical to the first publication: no second write happened.
        assert len(factory.database.rows) == 1
        assert _committed_snapshot(factory) == committed
        assert replayed.lifecycle_version == committed["lifecycle_version"]


async def test_get_async_readback_restores_seal_none_projection_without_writes() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()
    await store.publish_async(record, evidence=record.verification, actor="test")
    committed = _committed_snapshot(factory)

    # This is the exact read used by PluginArtifactRegistry.get_async and thus
    # by the service-level consumed-approval replay guard: natively async,
    # read-only, and reconstructing evidence with seal=None.
    stored = await store.get_async(record.plugin_id, record.version)
    assert stored is not None
    _only_selects(factory.sessions[-1])
    _assert_seal_none_projection(stored, record)
    assert stored.published_at == NOW
    assert stored.created_at == NOW

    by_digest = await store.by_digest_async(record.digest)
    assert by_digest is not None
    _only_selects(factory.sessions[-1])
    _assert_seal_none_projection(by_digest, record)
    assert _committed_snapshot(factory) == committed


async def test_legacy_published_row_without_lifecycle_replay_is_read_only() -> None:
    # A published row predating the lifecycle table: no durable operation token
    # exists, so the no-op must come from the existing-published early return,
    # never from reactivation, UPDATE or a fresh lifecycle insert.
    legacy = _artifact_row()
    factory = _Factory(_Database([legacy]))
    store = _store(factory)
    record = _verified_record()

    replayed = await store.publish_async(record, evidence=record.verification, actor="test")

    _only_selects(factory.sessions[-1])
    assert factory.database.lifecycle_rows == []
    row = factory.database.rows[0]
    assert row.is_current is False
    assert row.lifecycle_version == 7
    assert row.created_at == NOW
    assert row.published_at == NOW
    _assert_seal_none_projection(replayed, record)
    assert replayed.published_at == NOW
    assert replayed.lifecycle_version == 7

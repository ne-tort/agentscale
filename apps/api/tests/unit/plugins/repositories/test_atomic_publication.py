"""Stateful, no-database acceptance doubles for atomic artifact publication.

The tests are intentionally written during the verification freeze. The session
models committed/working rows, CAS updates, commit merging and rollback evidence;
no live database or external service is used.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from prodavan.application.dynamic_app_registry.contracts import (
    RollbackCommand,
    RollbackConflictError,
    rollback_command_fingerprint,
)
from prodavan.application.plugins.artifacts.verification import manifest_fingerprint
from prodavan.infrastructure.persistence.repositories.plugins._schema import (
    PluginRepositoryError,
)
from prodavan.infrastructure.persistence.models.artifact_lifecycle import (
    PluginArtifactLifecycleRow,
)
from prodavan.infrastructure.persistence.repositories.plugins.artifacts import (
    ConcurrentPublishError,
    SqlArtifactRegistryStore,
)

from .conftest import DIGEST, NOW, OTHER_DIGEST, FakeResult
from .test_artifacts import _artifact_row, _evidence, _verified_record

pytestmark = pytest.mark.asyncio


def _clone(row: Any) -> Any:
    values = {
        key: deepcopy(value)
        for key, value in vars(row).items()
        if not key.startswith("_")
    }
    return type(row)(**values)


def _clones(rows: list[Any]) -> list[Any]:
    return [_clone(row) for row in rows]


class _Database:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = _clones(rows or [])
        self.lifecycle_rows: list[Any] = []


class _Session:
    """Small stateful AsyncSession double with transactional working rows."""

    def __init__(self, database: _Database, fail_stage: str | None = None) -> None:
        self.database = database
        self._rows = _clones(database.rows)
        self._snapshot = _clones(self._rows)
        self.lifecycle_added: list[Any] = []
        self.fail_stage = fail_stage
        self.hide_existing = False
        self.request: Any = None
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    @staticmethod
    def _params(statement: Any) -> list[Any]:
        try:
            return list(statement.compile().params.values())
        except Exception:
            return []

    @staticmethod
    def _value(value: Any) -> Any:
        return getattr(value, "value", value)

    def _column_param(self, statement: Any, column: str) -> Any:
        try:
            params = statement.compile().params
        except Exception:
            return None
        for key, value in params.items():
            if key == column or key.startswith(column + "_"):
                return self._value(value)
        return None

    def _values(self, statement: Any) -> dict[str, Any]:
        return {
            getattr(key, "key", str(key)): self._value(value)
            for key, value in getattr(statement, "_values", {}).items()
        }

    def _plugin(self, statement: Any) -> str | None:
        if self.request is not None:
            return self.request.plugin_id
        plugin = self._column_param(statement, "plugin_id")
        if plugin is not None:
            return str(plugin)
        params = self._params(statement)
        for row in self._rows:
            if row.plugin_id in params:
                return row.plugin_id
        return None

    def _version(self, statement: Any) -> str | None:
        if self.request is not None:
            return self.request.version
        version = self._column_param(statement, "version")
        if version is not None:
            return str(version)
        params = self._params(statement)
        for row in self._rows:
            if row.version in params:
                return row.version
        return None

    def _matching(self, statement: Any) -> list[Any]:
        plugin = self._plugin(statement)
        version = self._version(statement)
        return [
            row for row in self._rows
            if (plugin is None or row.plugin_id == plugin)
            and (version is None or row.version == version)
        ]

    def _select(self, statement: Any) -> FakeResult:
        if self.hide_existing:
            return FakeResult([], rowcount=0)
        where = str(getattr(statement, "whereclause", ""))
        params = self._params(statement)
        if "operation_token" in where:
            lifecycle_rows = self.database.lifecycle_rows + self.lifecycle_added
            artifact_id = next(
                (
                    value for value in params
                    if isinstance(value, str)
                    and any(row.artifact_id == value for row in lifecycle_rows)
                ),
                None,
            )
            token = next(
                (value for value in params if isinstance(value, str) and ":" in value),
                None,
            )
            rows = [
                row for row in lifecycle_rows
                if row.artifact_id == artifact_id and row.operation_token == token
            ]
            return FakeResult(rows, rowcount=len(rows))
        if "oci_digest" in where:
            digest = next(
                (value for value in params if isinstance(value, str) and value.startswith("sha256:")),
                self.request.digest if self.request is not None else None,
            )
            rows = [row for row in self._rows if row.oci_digest == digest]
        elif "is_current" in where:
            plugin = self._plugin(statement)
            rows = [
                row for row in self._rows
                if row.is_current is True and (plugin is None or row.plugin_id == plugin)
            ]
        else:
            rows = self._matching(statement)
        return FakeResult(rows, rowcount=len(rows))

    def _update(self, statement: Any) -> FakeResult:
        values = self._values(statement)
        if "is_current" in values:
            plugin = self._plugin(statement)
            if values["is_current"] is False:
                rows = [
                    row for row in self._rows
                    if row.is_current is True
                    and (plugin is None or row.plugin_id == plugin)
                ]
                generations = [
                    value for value in self._params(statement)
                    if isinstance(value, int) and not isinstance(value, bool)
                ]
                if generations:
                    expected_generation = generations[-1]
                    rows = [
                        row for row in rows
                        if row.lifecycle_version == expected_generation
                    ]
                for row in rows:
                    row.is_current = False
                return FakeResult(rows, rowcount=len(rows))
            if self.fail_stage == "current":
                raise RuntimeError("injected current CAS failure")
            rows = self._matching(statement)
            if len(rows) != 1:
                return FakeResult([], rowcount=0)
            row = rows[0]
            next_version = values.get("lifecycle_version")
            if isinstance(next_version, int) and row.lifecycle_version != next_version - 1:
                return FakeResult([], rowcount=0)
            row.is_current = True
            if isinstance(next_version, int):
                row.lifecycle_version = next_version
            return FakeResult([row], rowcount=1)

        if self.fail_stage == "artifact":
            raise RuntimeError("injected artifact update failure")
        rows = self._matching(statement)
        for row in rows:
            for key, value in values.items():
                setattr(row, key, deepcopy(value))
        return FakeResult(rows, rowcount=len(rows))

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> FakeResult:
        self.statements.append(statement)
        sql = str(statement).lstrip().upper()
        if sql.startswith("SELECT"):
            return self._select(statement)
        return self._update(statement)

    def add(self, instance: Any) -> None:
        if self.fail_stage == "insert" and not isinstance(instance, PluginArtifactLifecycleRow):
            raise RuntimeError("injected insert failure")
        if getattr(instance, "id", None) is None:
            prefix = "pal_" if isinstance(instance, PluginArtifactLifecycleRow) else "part_"
            instance.id = f"{prefix}{id(instance):x}"
        self.added.append(instance)
        if isinstance(instance, PluginArtifactLifecycleRow):
            self.lifecycle_added.append(instance)
            return
        self.request = instance
        self._rows.append(instance)

    async def flush(self) -> None:
        if self.fail_stage == "insert":
            raise RuntimeError("injected flush failure")
        if self.fail_stage == "duplicate_insert":
            raise IntegrityError("INSERT", {}, Exception("concurrent first insert"))
        seen: set[tuple[str, str]] = set()
        digests: set[str] = set()
        for row in self._rows:
            key = (row.plugin_id, row.version)
            if key in seen or row.oci_digest in digests:
                raise IntegrityError("INSERT", {}, Exception("duplicate artifact"))
            seen.add(key)
            digests.add(row.oci_digest)

    async def commit(self) -> None:
        if self.fail_stage == "commit":
            raise RuntimeError("injected commit failure")
        for row in self._rows:
            for existing in self.database.rows:
                if existing.id != row.id and (
                    (existing.plugin_id, existing.version) == (row.plugin_id, row.version)
                    or existing.oci_digest == row.oci_digest
                ):
                    raise IntegrityError("COMMIT", {}, Exception("concurrent duplicate"))
        for row in self.lifecycle_added:
            if any(
                existing.artifact_id == row.artifact_id
                and existing.operation_token == row.operation_token
                for existing in self.database.lifecycle_rows
            ):
                raise IntegrityError("LIFECYCLE", {}, Exception("duplicate operation token"))
        current_plugins = {row.plugin_id for row in self._rows if row.is_current is True}
        for row in self.database.rows:
            if row.plugin_id in current_plugins:
                row.is_current = False
        for row in self._rows:
            for index, existing in enumerate(self.database.rows):
                if existing.id == row.id:
                    self.database.rows[index] = _clone(row)
                    break
            else:
                self.database.rows.append(_clone(row))
        for row in self.lifecycle_added:
            self.database.lifecycle_rows.append(_clone(row))
        self._snapshot = _clones(self._rows)
        self.commits += 1

    async def rollback(self) -> None:
        self._rows = _clones(self._snapshot)
        self.lifecycle_added.clear()
        self.rollbacks += 1

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


class _Factory:
    def __init__(self, database: _Database) -> None:
        self.database = database
        self.fail_stage: str | None = None
        self.fail_stage_once: str | None = None
        self.hide_existing_once = False
        self.sessions: list[_Session] = []

    def __call__(self) -> _Session:
        session = _Session(self.database, self.fail_stage)
        if self.fail_stage_once is not None:
            session.fail_stage = self.fail_stage_once
            self.fail_stage_once = None
        if self.hide_existing_once:
            session.hide_existing = True
            self.hide_existing_once = False
        self.sessions.append(session)
        return session


class _Store(SqlArtifactRegistryStore):
    def __init__(self, factory: _Factory) -> None:
        self.now = NOW
        super().__init__(session_factory=factory, clock=lambda: self.now)
        self.fail_lifecycle = False
        self.fail_lifecycle_actors: set[str] = set()
        self.pause_actor: str | None = None
        self.pause_entered: asyncio.Event | None = None
        self.pause_release: asyncio.Event | None = None
        self.pause_consumed = False

    async def _append_lifecycle_in_session(
        self,
        session: Any,
        *,
        artifact_id: str | None,
        operation: str,
        actor: str,
        detail: str,
        operation_token: str,
        command_fingerprint: str | None = None,
    ) -> None:
        await super()._append_lifecycle_in_session(
            session,
            artifact_id=artifact_id,
            operation=operation,
            actor=actor,
            detail=detail,
            operation_token=operation_token,
            command_fingerprint=command_fingerprint,
        )
        if (
            actor == self.pause_actor
            and not self.pause_consumed
            and self.pause_entered is not None
            and self.pause_release is not None
        ):
            self.pause_consumed = True
            self.pause_entered.set()
            await self.pause_release.wait()
        if self.fail_lifecycle or actor in self.fail_lifecycle_actors:
            raise RuntimeError("injected lifecycle failure")


def _store(factory: _Factory) -> _Store:
    return _Store(factory)


def _versioned_record(version: str, digest: str) -> Any:
    base = _verified_record()
    manifest = {"plugin_id": base.plugin_id, "version": version}
    bare = replace(
        base,
        version=version,
        digest=digest,
        image_ref="ghcr.io/example@" + digest,
        manifest=manifest,
        manifest_fingerprint=manifest_fingerprint(manifest),
        verification=None,
    )
    return replace(bare, verification=_evidence(bare))


async def test_atomic_publish_has_one_commit_and_exact_replay_is_noop() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()

    await store.publish_async(record, evidence=record.verification, actor="test")
    assert len(factory.database.rows) == 1
    assert factory.database.rows[0].status == "published"
    assert factory.database.rows[0].is_current is True
    assert len(factory.sessions) == 1
    assert factory.sessions[-1].commits == 1
    assert len(factory.database.lifecycle_rows) == 1
    assert factory.database.lifecycle_rows[0].operation == "publish"
    assert factory.database.lifecycle_rows[0].artifact_id == factory.database.rows[0].id
    assert factory.database.lifecycle_rows[0].operation_token
    lifecycle = factory.database.lifecycle_rows[0]
    operation_token = lifecycle.operation_token
    occurred_at = lifecycle.occurred_at

    # A later retry of the logical operation must not replace its durable
    # identity or timestamp with a freshly generated lifecycle fact.
    store.now = NOW.replace(year=NOW.year + 1)
    await store.publish_async(record, evidence=record.verification, actor="test")
    assert len(factory.database.rows) == 1
    assert factory.sessions[-1].commits == 0
    assert len(factory.database.lifecycle_rows) == 1
    assert factory.database.lifecycle_rows[0].operation_token == operation_token
    assert factory.database.lifecycle_rows[0].occurred_at == occurred_at


@pytest.mark.parametrize("stage", ("insert", "current", "lifecycle", "commit"))
async def test_insert_current_and_lifecycle_failures_restore_previous_state(stage: str) -> None:
    previous = _artifact_row(
        id="part_previous",
        version="0.9.0",
        oci_digest=OTHER_DIGEST,
        is_current=True,
    )
    committed = PluginArtifactLifecycleRow(
        id="pal_committed",
        artifact_id=previous.id,
        operation="publish",
        actor="committed",
        detail="previously committed publication",
        occurred_at=NOW.replace(year=NOW.year - 1),
        operation_token="committed-operation",
    )
    factory = _Factory(_Database([previous]))
    factory.database.lifecycle_rows.append(_clone(committed))
    factory.fail_stage = stage if stage != "lifecycle" else None
    store = _store(factory)
    store.fail_lifecycle = stage == "lifecycle"
    record = _verified_record()

    with pytest.raises(PluginRepositoryError):
        await store.publish_async(record, evidence=record.verification, actor="test")

    assert factory.sessions[-1].commits == 0
    assert factory.sessions[-1].rollbacks == 1
    assert len(factory.database.rows) == 1
    assert factory.database.rows[0].id == "part_previous"
    assert factory.database.rows[0].is_current is True
    assert all(row.oci_digest != DIGEST for row in factory.database.rows)
    assert len(factory.database.lifecycle_rows) == 1
    persisted = factory.database.lifecycle_rows[0]
    assert persisted.id == committed.id
    assert persisted.operation_token == committed.operation_token
    assert persisted.occurred_at == committed.occurred_at


async def test_atomic_promote_updates_row_and_current_in_one_commit() -> None:
    draft = _artifact_row(
        status="quarantined",
        published_at=None,
        is_current=False,
        verification_evidence={},
        app_version_projection={"protocol": "1.0"},
        source_id=None,
        commit_sha=None,
        backend_digest=None,
        backend_image_ref=None,
        signer_identity=None,
        issuer=None,
        run_url=None,
    )
    factory = _Factory(_Database([draft]))
    store = _store(factory)
    record = _verified_record()

    await store.promote_async(record, evidence=record.verification, actor="test")

    assert factory.sessions[-1].commits == 1
    assert len(factory.database.rows) == 1
    assert factory.database.rows[0].status == "published"
    assert factory.database.rows[0].is_current is True
    assert factory.database.rows[0].verification_evidence
    assert len(factory.database.lifecycle_rows) == 1
    assert factory.database.lifecycle_rows[0].operation == "promote"


async def test_concurrent_publish_leaves_exactly_one_current_row() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    first = _versioned_record("1.0.0", DIGEST)
    second = _versioned_record("1.1.0", OTHER_DIGEST)

    await asyncio.gather(
        store.publish_async(first, evidence=first.verification, actor="first"),
        store.publish_async(second, evidence=second.verification, actor="second"),
    )

    assert len(factory.database.rows) == 2
    assert sum(row.is_current is True for row in factory.database.rows) == 1
    assert sum(session.commits for session in factory.sessions) == 2
    assert len(factory.database.lifecycle_rows) == 2


@pytest.mark.parametrize("failure_first", (False, True))
async def test_successful_race_entry_survives_other_operation_failure(
    failure_first: bool,
) -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    store.fail_lifecycle_actors.add("failure")
    successful = _versioned_record("1.0.0", DIGEST)
    failed = _versioned_record("1.1.0", OTHER_DIGEST)
    store.pause_actor = "failure" if failure_first else "success"
    store.pause_entered = asyncio.Event()
    store.pause_release = asyncio.Event()

    async def publish_success() -> Any:
        return await store.publish_async(
            successful, evidence=successful.verification, actor="success"
        )

    async def publish_failure() -> Any:
        return await store.publish_async(
            failed, evidence=failed.verification, actor="failure"
        )

    first = publish_failure if failure_first else publish_success
    second = publish_success if failure_first else publish_failure
    first_task = asyncio.create_task(first())
    await store.pause_entered.wait()
    if failure_first:
        await second()
    else:
        with pytest.raises(PluginRepositoryError):
            await second()
    store.pause_release.set()
    if failure_first:
        with pytest.raises(PluginRepositoryError):
            await first_task
    else:
        await first_task

    assert len(factory.database.rows) == 1
    assert factory.database.rows[0].oci_digest == DIGEST
    assert factory.database.rows[0].is_current is True
    assert len(factory.database.lifecycle_rows) == 1
    assert factory.database.lifecycle_rows[0].actor == "success"
    assert factory.database.lifecycle_rows[0].artifact_id == factory.database.rows[0].id


async def test_failed_retry_reuses_token_and_existing_occurred_at() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()
    store.fail_lifecycle = True

    with pytest.raises(PluginRepositoryError):
        await store.publish_async(record, evidence=record.verification, actor="retry")
    failed_session = factory.sessions[-1]
    failed_lifecycle = next(
        row for row in failed_session.added if isinstance(row, PluginArtifactLifecycleRow)
    )
    failed_token = failed_lifecycle.operation_token
    assert failed_token.startswith("publish:part_")

    store.fail_lifecycle = False
    await store.publish_async(record, evidence=record.verification, actor="retry")
    persisted = factory.database.lifecycle_rows[0]
    assert persisted.operation_token == failed_token
    first_occurred_at = persisted.occurred_at

    await store.publish_async(record, evidence=record.verification, actor="retry")
    assert len(factory.database.rows) == 1
    assert len(factory.database.lifecycle_rows) == 1
    assert factory.database.lifecycle_rows[0].occurred_at == first_occurred_at
    assert factory.sessions[-1].commits == 0


async def test_lifecycle_semantic_mismatch_is_typed_conflict() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()
    await store.publish_async(record, evidence=record.verification, actor="original")
    occurred_at = factory.database.lifecycle_rows[0].occurred_at

    with pytest.raises(ConcurrentPublishError):
        await store.publish_async(record, evidence=record.verification, actor="different")

    assert factory.sessions[-1].commits == 0
    assert factory.database.lifecycle_rows[0].occurred_at == occurred_at
    assert len(factory.database.lifecycle_rows) == 1
    assert factory.database.rows[0].is_current is True


async def test_concurrent_exact_replay_keeps_one_artifact_and_lifecycle_row() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()

    await asyncio.gather(
        store.publish_async(record, evidence=record.verification, actor="same"),
        store.publish_async(record, evidence=record.verification, actor="same"),
        return_exceptions=True,
    )

    assert len(factory.database.rows) == 1
    assert sum(row.is_current is True for row in factory.database.rows) == 1
    assert len(factory.database.lifecycle_rows) == 1


async def test_unique_first_insert_rereads_exact_winner_as_replay() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()
    await store.publish_async(record, evidence=record.verification, actor="same")
    factory.hide_existing_once = True
    factory.fail_stage_once = "duplicate_insert"

    replayed = await store.publish_async(record, evidence=record.verification, actor="same")

    assert replayed.digest == record.digest
    assert len(factory.database.rows) == 1
    assert len(factory.database.lifecycle_rows) == 1
    assert factory.sessions[-1].commits == 0


async def test_unique_first_insert_replay_canonicalizes_capability_order() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    evidence = replace(
        _verified_record().verification,
        granted_capabilities=("cache", "objects"),
    )
    record = replace(_verified_record(), verification=evidence)
    await store.publish_async(record, evidence=record.verification, actor="same")
    reordered = replace(
        record,
        verification=replace(
            record.verification,
            granted_capabilities=("objects", "cache"),
        ),
    )
    factory.hide_existing_once = True
    factory.fail_stage_once = "duplicate_insert"

    replayed = await store.publish_async(
        reordered, evidence=reordered.verification, actor="same"
    )

    assert replayed.verification.granted_capabilities == ("cache", "objects")
    assert len(factory.database.rows) == 1
    assert len(factory.database.lifecycle_rows) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"granted_capabilities": ("different.capability",)},
        {"observed_at": NOW.replace(year=NOW.year + 1)},
    ],
)
async def test_unique_first_insert_rejects_changed_verification_evidence(
    changes: dict[str, Any],
) -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()
    await store.publish_async(record, evidence=record.verification, actor="same")
    changed = replace(
        record,
        verification=replace(record.verification, **changes),
    )
    factory.hide_existing_once = True
    factory.fail_stage_once = "duplicate_insert"

    with pytest.raises(ConcurrentPublishError):
        await store.publish_async(changed, evidence=changed.verification, actor="same")

    assert len(factory.database.rows) == 1
    assert sum(row.is_current is True for row in factory.database.rows) == 1
    assert len(factory.database.lifecycle_rows) == 1
    assert factory.sessions[-1].commits == 0


@pytest.mark.parametrize(
    "gate",
    ["sbom_verified", "provenance_verified", "scan_verified", "csp_verified"],
)
async def test_recovery_denies_changed_persisted_security_gate(gate: str) -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()
    await store.publish_async(record, evidence=record.verification, actor="same")
    changed = replace(
        record,
        verification=replace(record.verification, **{gate: False}),
    )

    replay = await store._recover_publication_replay(
        record=changed,
        actor="same",
        detail=f"publish {record.image_ref}",
        promote=False,
    )

    assert replay is None
    assert len(factory.database.rows) == 1
    assert sum(row.is_current is True for row in factory.database.rows) == 1
    assert len(factory.database.lifecycle_rows) == 1


async def test_unique_first_insert_rejects_different_payload() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()
    await store.publish_async(record, evidence=record.verification, actor="same")
    changed_manifest = {"plugin_id": record.plugin_id, "version": record.version, "changed": True}
    bare = replace(
        record,
        manifest=changed_manifest,
        manifest_fingerprint=manifest_fingerprint(changed_manifest),
        verification=None,
    )
    changed = replace(bare, verification=_evidence(bare))
    factory.hide_existing_once = True
    factory.fail_stage_once = "duplicate_insert"

    with pytest.raises(ConcurrentPublishError):
        await store.publish_async(changed, evidence=changed.verification, actor="same")

    assert len(factory.database.rows) == 1
    assert factory.database.rows[0].manifest == record.manifest
    assert len(factory.database.lifecycle_rows) == 1


async def test_unique_first_insert_rejects_current_version_conflict() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    first = _versioned_record("1.0.0", DIGEST)
    second = _versioned_record("1.1.0", OTHER_DIGEST)
    await store.publish_async(first, evidence=first.verification, actor="first")
    await store.publish_async(second, evidence=second.verification, actor="second")
    factory.hide_existing_once = True
    factory.fail_stage_once = "duplicate_insert"

    with pytest.raises(ConcurrentPublishError):
        await store.publish_async(first, evidence=first.verification, actor="first")

    current = [row for row in factory.database.rows if row.is_current]
    assert len(current) == 1
    assert current[0].version == "1.1.0"
    assert len(factory.database.lifecycle_rows) == 2


async def test_exact_replay_does_not_reactivate_legacy_row() -> None:
    legacy = _artifact_row(
        is_current=False,
        verification_evidence={},
        app_version_projection={"protocol": "1.0"},
        source_id=None,
        commit_sha=None,
        backend_digest=None,
        backend_image_ref=None,
        signer_identity=None,
        issuer=None,
        run_url=None,
    )
    factory = _Factory(_Database([legacy]))
    store = _store(factory)
    record = _verified_record()

    await store.publish_async(record, evidence=record.verification, actor="test")

    assert factory.sessions[-1].commits == 0
    assert factory.database.rows[0].is_current is False
    assert factory.database.lifecycle_rows == []


async def test_atomic_store_conflict_is_typed_and_does_not_mutate_rows() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    record = _verified_record()
    await store.publish_async(record, evidence=record.verification, actor="test")
    changed_manifest = {"plugin_id": record.plugin_id, "version": record.version, "changed": True}
    changed = replace(
        record,
        manifest=changed_manifest,
        manifest_fingerprint=manifest_fingerprint(changed_manifest),
    )

    with pytest.raises(ConcurrentPublishError):
        await store.publish_async(changed, evidence=record.verification, actor="test")

    assert len(factory.database.rows) == 1
    assert factory.database.rows[0].is_current is True
    assert len(factory.database.lifecycle_rows) == 1


async def test_sql_rollback_is_atomic_durable_and_replays_stored_result() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    first = _versioned_record("1.0.0", DIGEST)
    second = _versioned_record("1.1.0", OTHER_DIGEST)
    await store.publish_async(first, evidence=first.verification, actor="first")
    await store.publish_async(second, evidence=second.verification, actor="second")
    target = await store.by_digest_async(first.digest)
    assert target is not None
    command = RollbackCommand(
        definition_id=first.plugin_id,
        target_version_id=first.version,
        actor="admin",
        reason="regression",
        expected_current_version_id=second.version,
        expected_artifact_digest=first.digest,
    )

    rolled_back = await store.rollback_async(
        target=target,
        command=command,
        actor="admin",
        detail="rollback mod_equipment@1.0.0",
    )

    assert rolled_back.created_at == target.created_at
    current = [row for row in factory.database.rows if row.is_current]
    assert len(current) == 1 and current[0].version == first.version
    assert [row.operation for row in factory.database.lifecycle_rows] == [
        "publish", "publish", "rollback"
    ]
    rollback_session = factory.sessions[-1]
    assert rollback_session.commits == 1
    occurred_at = factory.database.lifecycle_rows[-1].occurred_at

    await store.rollback_async(
        target=target,
        command=command,
        actor="admin",
        detail="rollback mod_equipment@1.0.0",
    )
    assert len(factory.database.lifecycle_rows) == 3
    assert factory.database.lifecycle_rows[-1].occurred_at == occurred_at
    assert factory.sessions[-1].commits == 0


async def test_rollback_lifecycle_failure_restores_previous_current() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    first = _versioned_record("1.0.0", DIGEST)
    second = _versioned_record("1.1.0", OTHER_DIGEST)
    await store.publish_async(first, evidence=first.verification, actor="first")
    await store.publish_async(second, evidence=second.verification, actor="second")
    target = await store.by_digest_async(first.digest)
    assert target is not None
    store.fail_lifecycle_actors.add("admin")
    command = RollbackCommand(
        definition_id=first.plugin_id,
        target_version_id=first.version,
        actor="admin",
        reason="regression",
        expected_current_version_id=second.version,
        expected_artifact_digest=first.digest,
    )

    with pytest.raises(PluginRepositoryError):
        await store.rollback_async(
            target=target,
            command=command,
            actor="admin",
            detail="rollback mod_equipment@1.0.0",
        )

    current = [row for row in factory.database.rows if row.is_current]
    assert len(current) == 1 and current[0].version == second.version
    assert len(factory.database.lifecycle_rows) == 2
    assert factory.sessions[-1].rollbacks == 1


@pytest.mark.asyncio
async def test_rollback_command_fingerprint_is_deterministic_and_redacted() -> None:
    command = RollbackCommand(
        definition_id="mod_x",
        target_version_id="1.0.0",
        actor="admin",
        reason="secret incident reason",
        expected_current_version_id="1.1.0",
        expected_artifact_digest=DIGEST,
        expected_current_lifecycle_version=1,
    )
    fingerprint = rollback_command_fingerprint(command)
    again = rollback_command_fingerprint(replace(command, expected_current_lifecycle_version=1))

    assert fingerprint == again
    assert len(fingerprint) == 64
    assert command.reason not in fingerprint
    assert command.actor not in fingerprint
    assert fingerprint != rollback_command_fingerprint(replace(command, reason="other reason"))
    assert fingerprint != rollback_command_fingerprint(replace(command, actor="other"))


async def test_sql_rollback_rejects_reason_actor_and_missing_fingerprint() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    first = _versioned_record("1.0.0", DIGEST)
    second = _versioned_record("1.1.0", OTHER_DIGEST)
    await store.publish_async(first, evidence=first.verification, actor="first")
    await store.publish_async(second, evidence=second.verification, actor="second")
    target = await store.by_digest_async(first.digest)
    assert target is not None
    initial = RollbackCommand(
        definition_id=first.plugin_id,
        target_version_id=first.version,
        actor="admin",
        reason="initial reason",
        expected_current_version_id=second.version,
        expected_artifact_digest=first.digest,
        expected_current_lifecycle_version=1,
    )
    await store.rollback_async(
        target=target, command=initial, actor="admin", detail="rollback initial"
    )
    before = factory.database.lifecycle_rows[-1]
    assert before.command_fingerprint
    assert initial.reason not in before.command_fingerprint

    with pytest.raises(ConcurrentPublishError):
        await store.rollback_async(
            target=target,
            command=replace(initial, reason="changed reason"),
            actor="admin",
            detail="rollback initial",
        )
    with pytest.raises(ConcurrentPublishError):
        await store.rollback_async(
            target=target,
            command=replace(initial, actor="other"),
            actor="admin",
            detail="rollback initial",
        )

    legacy = factory.database.lifecycle_rows[-1]
    legacy.command_fingerprint = None
    with pytest.raises(ConcurrentPublishError, match="fingerprint"):
        await store.rollback_async(
            target=target,
            command=initial,
            actor="admin",
            detail="rollback initial",
        )

    assert legacy.command_fingerprint is None
    assert len(factory.database.lifecycle_rows) == 3
    assert factory.database.lifecycle_rows[-1] is before
    assert before.occurred_at == factory.database.lifecycle_rows[-1].occurred_at
    current = [row for row in factory.database.rows if row.is_current]
    assert len(current) == 1 and current[0].version == first.version


async def test_sync_sql_rollback_delegates_to_atomic_async_uow() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    first = _versioned_record("1.0.0", DIGEST)
    second = _versioned_record("1.1.0", OTHER_DIGEST)
    await store.publish_async(first, evidence=first.verification, actor="first")
    await store.publish_async(second, evidence=second.verification, actor="second")
    target = await store.by_digest_async(first.digest)
    assert target is not None
    calls: list[dict[str, Any]] = []

    async def fake_async(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return target

    store._run = lambda coro: coro  # type: ignore[method-assign]
    store.rollback_async = fake_async  # type: ignore[method-assign]
    command = RollbackCommand(
        definition_id=first.plugin_id,
        target_version_id=first.version,
        actor="admin",
        reason="delegation",
        expected_current_version_id=second.version,
        expected_artifact_digest=first.digest,
        expected_current_lifecycle_version=1,
    )
    pending = store.rollback(
        target=target,
        command=command,
        actor="admin",
        detail="rollback delegated",
    )
    stored = await pending
    assert stored == target
    assert len(calls) == 1
    assert calls[0] == {
        "target": target,
        "command": command,
        "actor": "admin",
        "detail": "rollback delegated",
    }


async def test_rollback_cycle_uses_new_generation_and_adds_second_b_to_a() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    first = _versioned_record("1.0.0", DIGEST)
    second = _versioned_record("1.1.0", OTHER_DIGEST)
    # Seed A as current without a lifecycle fact. The full A→B→A→B→A path has
    # four durable facts: B publication, two distinct B→A facts, and A→B.
    await store.add_async(first, evidence=first.verification)
    await store.set_current_async(first.plugin_id, first.version)
    await store.publish_async(second, evidence=second.verification, actor="second")

    target_a = await store.by_digest_async(first.digest)
    assert target_a is not None
    await store.rollback_async(
        target=target_a,
        command=RollbackCommand(
            definition_id=first.plugin_id,
            target_version_id=first.version,
            actor="admin",
            reason="cycle first",
            expected_current_version_id=second.version,
            expected_artifact_digest=first.digest,
            expected_current_lifecycle_version=1,
        ),
        actor="admin",
        detail="rollback to A first",
    )
    target_b = await store.by_digest_async(second.digest)
    assert target_b is not None
    await store.rollback_async(
        target=target_b,
        command=RollbackCommand(
            definition_id=second.plugin_id,
            target_version_id=second.version,
            actor="admin",
            reason="cycle second",
            expected_current_version_id=first.version,
            expected_artifact_digest=second.digest,
            expected_current_lifecycle_version=2,
        ),
        actor="admin",
        detail="rollback to B",
    )
    first_b_to_a = factory.database.lifecycle_rows[1]
    first_occurred_at = first_b_to_a.occurred_at

    store.now = NOW.replace(year=NOW.year + 1)
    target_a_again = await store.by_digest_async(first.digest)
    assert target_a_again is not None
    second_b_to_a_command = RollbackCommand(
        definition_id=first.plugin_id,
        target_version_id=first.version,
        actor="admin",
        reason="cycle third",
        expected_current_version_id=second.version,
        expected_artifact_digest=first.digest,
        expected_current_lifecycle_version=2,
    )
    returned = await store.rollback_async(
        target=target_a_again,
        command=second_b_to_a_command,
        actor="admin",
        detail="rollback to A on the new B generation",
    )

    assert [row.operation for row in factory.database.lifecycle_rows] == [
        "publish", "rollback", "rollback", "rollback"
    ]
    b_to_a_rows = factory.database.lifecycle_rows[1::2]
    assert [row.detail for row in b_to_a_rows] == [
        "rollback to A first",
        "rollback to A on the new B generation",
    ]
    assert b_to_a_rows[0].operation_token != b_to_a_rows[1].operation_token
    assert len(factory.database.lifecycle_rows) == 4
    assert returned == replace(target_a_again, lifecycle_version=3)
    assert returned.created_at == target_a_again.created_at
    assert returned.published_at == target_a_again.published_at
    assert returned.lifecycle_version == 3

    replayed = await store.rollback_async(
        target=target_a_again,
        command=second_b_to_a_command,
        actor="admin",
        detail="rollback to A on the new B generation",
    )
    assert replayed == returned
    assert replayed.created_at == returned.created_at
    assert replayed.published_at == returned.published_at
    assert factory.database.lifecycle_rows[3].occurred_at > first_occurred_at
    assert len(factory.database.lifecycle_rows) == 4
    second_b_to_a_occurred_at = factory.database.lifecycle_rows[3].occurred_at

    assert replayed == returned
    assert replayed.created_at == returned.created_at
    assert replayed.published_at == returned.published_at
    assert factory.database.lifecycle_rows[3].occurred_at == second_b_to_a_occurred_at
    assert len(factory.database.lifecycle_rows) == 4
    current = [row for row in factory.database.rows if row.is_current]
    assert len(current) == 1 and current[0].version == first.version
    assert current[0].lifecycle_version == 3


async def test_concurrent_same_rollback_replays_one_durable_fact() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    first = _versioned_record("1.0.0", DIGEST)
    second = _versioned_record("1.1.0", OTHER_DIGEST)
    await store.publish_async(first, evidence=first.verification, actor="first")
    await store.publish_async(second, evidence=second.verification, actor="second")
    target = await store.by_digest_async(first.digest)
    assert target is not None
    command = RollbackCommand(
        definition_id=first.plugin_id,
        target_version_id=first.version,
        actor="admin",
        reason="same generation",
        expected_current_version_id=second.version,
        expected_artifact_digest=first.digest,
        expected_current_lifecycle_version=1,
    )
    store.pause_actor = "admin"
    store.pause_entered = asyncio.Event()
    store.pause_release = asyncio.Event()

    first_task = asyncio.create_task(store.rollback_async(
        target=target, command=command, actor="admin", detail="rollback same"
    ))
    await store.pause_entered.wait()
    second = await store.rollback_async(
        target=target, command=command, actor="admin", detail="rollback same"
    )
    store.pause_release.set()
    first_result = await first_task

    assert first_result == second
    assert first_result.digest == first.digest
    assert first_result.lifecycle_version == second.lifecycle_version
    assert len(factory.database.lifecycle_rows) == 3
    assert sum(row.operation == "rollback" for row in factory.database.lifecycle_rows) == 1
    current = [row for row in factory.database.rows if row.is_current]
    assert len(current) == 1 and current[0].version == first.version


async def test_rollback_wrong_source_generation_is_cas_conflict() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    first = _versioned_record("1.0.0", DIGEST)
    second = _versioned_record("1.1.0", OTHER_DIGEST)
    await store.publish_async(first, evidence=first.verification, actor="first")
    await store.publish_async(second, evidence=second.verification, actor="second")
    target = await store.by_digest_async(first.digest)
    assert target is not None

    with pytest.raises(ConcurrentPublishError):
        await store.rollback_async(
            target=target,
            command=RollbackCommand(
                definition_id=first.plugin_id,
                target_version_id=first.version,
                actor="admin",
                reason="wrong generation",
                expected_current_version_id=second.version,
                expected_artifact_digest=first.digest,
                expected_current_lifecycle_version=999,
            ),
            actor="admin",
            detail="rollback wrong generation",
        )

    current = [row for row in factory.database.rows if row.is_current]
    assert len(current) == 1 and current[0].version == second.version
    assert len(factory.database.lifecycle_rows) == 2
    assert sum(session.rollbacks for session in factory.sessions) == 1


async def test_rollback_wrong_current_version_is_cas_conflict() -> None:
    factory = _Factory(_Database())
    store = _store(factory)
    first = _versioned_record("1.0.0", DIGEST)
    second = _versioned_record("1.1.0", OTHER_DIGEST)
    await store.publish_async(first, evidence=first.verification, actor="first")
    await store.publish_async(second, evidence=second.verification, actor="second")
    target = await store.by_digest_async(first.digest)
    assert target is not None

    with pytest.raises(ConcurrentPublishError):
        await store.rollback_async(
            target=target,
            command=RollbackCommand(
                definition_id=first.plugin_id,
                target_version_id=first.version,
                actor="admin",
                reason="wrong current",
                expected_current_version_id="9.9.9",
                expected_artifact_digest=first.digest,
                expected_current_lifecycle_version=1,
            ),
            actor="admin",
            detail="rollback wrong current",
        )

    current = [row for row in factory.database.rows if row.is_current]
    assert len(current) == 1 and current[0].version == second.version
    assert len(factory.database.lifecycle_rows) == 2


async def test_revoked_legacy_row_cannot_be_promoted() -> None:
    revoked = _artifact_row(status="revoked", is_current=False)
    factory = _Factory(_Database([revoked]))
    store = _store(factory)
    record = _verified_record()

    with pytest.raises(ConcurrentPublishError):
        await store.promote_async(record, evidence=record.verification, actor="test")

    assert factory.database.rows[0].status == "revoked"
    assert factory.database.rows[0].is_current is False
    assert factory.sessions[-1].commits == 0
    assert factory.database.lifecycle_rows == []

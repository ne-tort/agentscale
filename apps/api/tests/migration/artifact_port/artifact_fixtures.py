"""Fixtures for the durable M04E artifact content port.

Two kinds of doubles live here, and the difference matters.

**SQL doubles.**  ``RecordingSessionFactory`` hands out a session whose ``execute``
answers from a small in-memory catalogue keyed by statement shape.  It is deliberately
*not* an SQL engine: the point of the unit tests is the port's decision table (which
refusal, in which order, before which read), and a real engine would blur that with the
engine's own behaviour.  The live-PostgreSQL tests in ``test_live_postgres.py`` cover the
statements themselves.

**Byte-store doubles.**  ``FakeByteStore`` is a dict-backed ``FileStorePort`` subset that
records every key it is asked for, so a test can assert *that nothing was read* -- which is
the whole claim behind "a mismatch is refused before the transfer".  ``CollapsingByteStore``
reproduces the S3 backend's documented behaviour (every failure becomes
``FileNotFoundError``), because that is the ambiguity this port has to refuse.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

TENANT = "tenant-artifact"
OTHER_TENANT = "tenant-foreign"
DEFINITION = "appdef_0001"
VERSION = "aver_0001"

ARTIFACT_BYTES = b'{"protocol":"1.0","appId":"demo-app"}'
ARTIFACT_DIGEST = "sha256:" + hashlib.sha256(ARTIFACT_BYTES).hexdigest()
ARTIFACT_KEY = "blobs/artifacts/0001"
ARTIFACT_URI = f"object://{ARTIFACT_KEY}"

CONTENT_ASSET = "cast_0001"
CONTENT_BLOB = "cbv_0001"
CONTENT_KEY = "blobs/0001"
CONTENT_URI = f"content://{CONTENT_ASSET}"

OTHER_DIGEST = "sha256:" + "f" * 64


def digest_of(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# SQL doubles
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows: tuple[Mapping[str, Any], ...] = ()) -> None:
        self._rows = tuple(rows)

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[Mapping[str, Any]]:
        return list(self._rows)

    def one(self) -> Mapping[str, Any]:
        assert len(self._rows) == 1, f"expected exactly one row, got {len(self._rows)}"
        return self._rows[0]


@dataclass
class Catalogue:
    """The rows the fake session answers with, per relation."""

    legacy_versions: tuple[Mapping[str, Any], ...] = ()
    target_versions: tuple[Mapping[str, Any], ...] = ()
    content_assets: tuple[Mapping[str, Any], ...] = ()
    content_blobs: tuple[Mapping[str, Any], ...] = ()
    #: Rows returned for the transaction-local readback; ``None`` keeps the honest answer.
    tenant_readback: str | None = None
    role_readback: str = "true"


class _Transaction:
    """``session.begin()`` as a real session exposes it: an *async context manager*.

    It is deliberately not a coroutine: SQLAlchemy's ``AsyncSession.begin()`` returns an
    object supporting ``async with``, and a double that returned a coroutine would refuse
    exactly the call shape the production code uses.
    """

    async def __aenter__(self) -> _Transaction:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class FakeSession:
    """Answers the four SELECTs of the port from a :class:`Catalogue`."""

    def __init__(self, catalogue: Catalogue) -> None:
        self._catalogue = catalogue
        self.statements: list[str] = []
        self.context: dict[str, Any] | None = None
        self.transactions = 0

    async def execute(self, statement: Any, parameters: Mapping[str, Any] | None = None):
        sql = str(statement)
        self.statements.append(sql)
        values = dict(parameters or {})
        if "set_config('app.tenant_id'" in sql:
            self.context = values
            return _Result(({"tenant_id": values.get("tenant_id")},))
        if "current_setting('app.tenant_id'" in sql:
            return _Result(({
                "tenant_id": (
                    self._catalogue.tenant_readback
                    if self._catalogue.tenant_readback is not None
                    else (self.context or {}).get("tenant_id")
                ),
                "migration_role": self._catalogue.role_readback,
            },))
        if "FROM dynamic_app_versions" in sql:
            return _Result(self._catalogue.legacy_versions)
        if "FROM m04e_dynamic_app_versions" in sql:
            return _Result(self._catalogue.target_versions)
        if "FROM content_assets" in sql:
            return _Result(self._catalogue.content_assets)
        if "FROM content_blob_versions" in sql:
            return _Result(self._catalogue.content_blobs)
        raise AssertionError(f"unexpected statement: {sql[:80]}")

    def begin(self) -> _Transaction:
        self.transactions += 1
        return _Transaction()

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class RecordingSessionFactory:
    """A session factory that counts its sessions and answers from one catalogue."""

    def __init__(self, catalogue: Catalogue | None = None) -> None:
        self.catalogue = catalogue or Catalogue()
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession(self.catalogue)
        self.sessions.append(session)
        return session

    @property
    def calls(self) -> int:
        return len(self.sessions)

    @property
    def statements(self) -> list[str]:
        return [statement for session in self.sessions for statement in session.statements]


# ---------------------------------------------------------------------------
# Byte-store doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeByteStore:
    """A dict-backed store that records every read; ``backend_name`` drives the contract."""

    objects: dict[str, bytes] = field(default_factory=dict)
    backend_name: str = "local"
    reads: list[str] = field(default_factory=list)
    heads: list[str] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)
    fail_with: Exception | None = None

    def get_bytes(self, key: str) -> bytes:
        self.reads.append(key)
        if self.fail_with is not None:
            raise self.fail_with
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def head(self, key: str):
        from prodavan.infrastructure.files.port import ObjectHead

        self.heads.append(key)
        if key not in self.objects:
            raise FileNotFoundError(key)
        return ObjectHead(
            size=self.sizes.get(key, len(self.objects[key])),
            etag=None,
            content_type=None,
            metadata={},
        )


class AsyncByteStore(FakeByteStore):
    """The same store with the async signature ``FileStoreManager`` exposes."""

    async def get_bytes(self, key: str) -> bytes:  # type: ignore[override]
        self.reads.append(key)
        if self.fail_with is not None:
            raise self.fail_with
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    async def head(self, key: str):  # type: ignore[override]
        return FakeByteStore.head(self, key)


@dataclass
class CollapsingByteStore(FakeByteStore):
    """An S3-shaped store: every failure, including a real one, is ``FileNotFoundError``.

    ``infrastructure/files/minio_store.py`` raises ``FileNotFoundError(key)`` for *any*
    boto exception, so on that backend a miss cannot be told from an outage.  This class is
    itself a ``@dataclass`` on purpose: a bare annotation in a non-dataclass subclass is
    inert, ``FakeByteStore.__init__`` would keep writing ``local``, and the double would
    quietly stop modelling the backend this test exists for.
    """

    backend_name: str = "s3"

    def get_bytes(self, key: str) -> bytes:
        self.reads.append(key)
        if self.fail_with is not None:
            raise FileNotFoundError(key) from self.fail_with
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]


# ---------------------------------------------------------------------------
# Catalogue builders
# ---------------------------------------------------------------------------


def legacy_version(**overrides: Any) -> Mapping[str, Any]:
    row: dict[str, Any] = {
        "version_id": VERSION,
        "definition_id": DEFINITION,
        "tenant_id": TENANT,
        "artifact_digest": ARTIFACT_DIGEST,
        "artifact_uri": ARTIFACT_URI,
    }
    row.update(overrides)
    return row


def target_version(**overrides: Any) -> Mapping[str, Any]:
    row: dict[str, Any] = {
        "version_id": VERSION,
        "definition_id": DEFINITION,
        "tenant_id": TENANT,
        "artifact_digest": ARTIFACT_DIGEST,
        "artifact_uri": ARTIFACT_URI,
    }
    row.update(overrides)
    return row


def content_asset(**overrides: Any) -> Mapping[str, Any]:
    row: dict[str, Any] = {
        "id": CONTENT_ASSET,
        "owner_company_id": TENANT,
        "owner_scope": "company",
        "visibility": "company",
    }
    row.update(overrides)
    return row


def content_blob(**overrides: Any) -> Mapping[str, Any]:
    row: dict[str, Any] = {
        "id": CONTENT_BLOB,
        "asset_id": CONTENT_ASSET,
        "version": 1,
        "storage_key": CONTENT_KEY,
        "size": len(ARTIFACT_BYTES),
        "sha256": ARTIFACT_DIGEST.removeprefix("sha256:"),
    }
    row.update(overrides)
    return row


def catalogue(**overrides: Any) -> Catalogue:
    values: dict[str, Any] = {
        "legacy_versions": (legacy_version(),),
        "target_versions": (),
        "content_assets": (),
        "content_blobs": (),
    }
    values.update(overrides)
    return Catalogue(**values)


__all__ = [
    "ARTIFACT_BYTES",
    "ARTIFACT_DIGEST",
    "ARTIFACT_KEY",
    "ARTIFACT_URI",
    "AsyncByteStore",
    "CONTENT_ASSET",
    "CONTENT_BLOB",
    "CONTENT_KEY",
    "CONTENT_URI",
    "Catalogue",
    "CollapsingByteStore",
    "DEFINITION",
    "FakeByteStore",
    "FakeSession",
    "OTHER_DIGEST",
    "OTHER_TENANT",
    "RecordingSessionFactory",
    "TENANT",
    "VERSION",
    "catalogue",
    "content_asset",
    "content_blob",
    "digest_of",
    "legacy_version",
    "target_version",
]

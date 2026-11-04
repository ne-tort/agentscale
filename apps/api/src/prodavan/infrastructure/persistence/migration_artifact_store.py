"""Durable ``ArtifactContentPort`` over the persisted M04 version rows and the object store.

Task ``MIG-ARTIFACT-PORT-01``.

Why this module exists
======================

``application/migration/m04e_definition_version/artifact.py`` declares the
:class:`~prodavan.application.migration.m04e_definition_version.artifact.ArtifactContentPort`
boundary and states, in its own docstring, that "no adapter is bound here".  The M04E
backfill refuses to run a page without it (``artifact_content_port_missing``), and the
operator assembly root reports the consequence as
``partition_artifact_content_port_unavailable`` -- i.e. no partition can be imported.
This module is that adapter.

It is deliberately the only thing ``application/**`` delegates to for artifact bytes:
the port is an *application boundary*, so the SQL, the object-store key and the
content-table lookup all live here, in ``infrastructure``.

Where the bytes actually come from
==================================

A version row pins exactly two things about its artifact: ``artifact_uri`` (an opaque
reference) and ``artifact_digest`` (``sha256:<64 hex>``).  Neither is a storage key, and
:mod:`prodavan.infrastructure.persistence.migration_definition_version.content_adapter`
is right to refuse to *derive* one: there is no foreign key from a version row to
``content_assets``/``content_blob_versions``, so parsing a URI into a bucket key would be
an invented mapping.

This adapter therefore never derives a storage key.  It *resolves* the persisted
``artifact_uri`` through schemes the repository already defines, and every hop is a
lookup that carries its own tenant proof:

===========================  ==========================================================
scheme                        resolution (each one proven, never parsed)
===========================  ==========================================================
``object://<key>``            ``FileStorePort`` by key -- the canonical product blob ref
                              (``core/infra/object_keys.parse_storage_ref``)
``file://<key>``              ``FileStorePort`` by key -- the legacy local ref, same
                              parser, same store
``content://<asset_id>``      ``content_assets`` by **primary key**, then the newest
                              ``content_blob_versions.storage_key`` of that asset.  The
                              asset row must be tenant-owned: ``owner_company_id`` must
                              equal the partition tenant
anything else                 typed refusal ``artifact_uri_scheme_unsupported`` naming
                              what a reviewed mapping would have to add
===========================  ==========================================================

The ``content://`` form is not an invention of this module: ``object_keys.content_asset_ref``
defines it as ``f"content://{asset_id}"`` and ``parse_content_asset_ref`` reads it back, and
both ``application/projects/attachment_service.py`` and ``attachment_delivery.py`` already
resolve project attachments through exactly this pair.  What this module adds is the
tenant proof the attachment path does not need, and the digest proof neither of them has.

Immutability is proven by hashing, not by trusting the store
============================================================

``read_artifact_verified`` returns the bytes **and** the digest it recomputed, and refuses
when that digest differs from the ``artifact_digest`` persisted on the exact version row.
The object store is never believed: a mutated object, a re-pointed ``storage_key``, or a
substituted asset all produce ``artifact_content_digest_mismatch`` rather than a silently
different artifact identity.  When the content row carries its own sealed ``sha256``
(``content_blob_versions.sha256``) it is compared *before* the bytes are read, so a wrong
asset is refused without paying for the transfer.

Tenant scope is fail-closed on four independent layers
======================================================

1. **Identity.** The tuple ``(tenant_id, app_definition_id, app_version_id)`` must name
   exactly one persisted row.  Zero rows is a refusal (``artifact_version_identity_missing``),
   never an empty answer: an empty answer would be indistinguishable from "this tenant has
   no such version", which is what a foreign tenant would like to hear.  Two rows is
   ``artifact_version_identity_ambiguous``.
2. **Transaction.** Every read runs in one transaction whose ``app.tenant_id`` is set
   transaction-locally (``set_config(..., true)``) and read back; a mismatch refuses rather
   than continuing.  The M04E tables are ``FORCE ROW LEVEL SECURITY`` with restrictive
   tenant policies, so a read without a bound tenant returns **zero rows** -- which layer 1
   would otherwise report as "absent".  Binding first is what keeps that from happening.
3. **SQL predicate.** Every statement also carries an explicit ``tenant_id = :tenant_id``
   (legacy ``dynamic_app_versions`` has no tenant column, so it is bounded through
   ``dynamic_app_definitions.tenant_id``).  A policy regression cannot widen the read.
4. **Content ownership.** A ``content://`` asset must be owned by the partition tenant.
   An asset with ``owner_company_id IS NULL`` is platform content: readable in principle,
   but not tenant-immutable evidence for *this* partition, so it is refused
   (``artifact_asset_tenant_unproven``) instead of being treated as owned.

Which row is authoritative
==========================

The M04E backfill binds a *legacy* version (``dynamic_app_versions``) and proves it against
the canonical row (``m04e_dynamic_app_versions``), so both relations are consulted:

* exactly one row in either relation -> that row is authoritative;
* a row in both -> they must agree on ``artifact_uri`` and ``artifact_digest``, else
  ``artifact_version_identity_conflict``;
* neither -> ``artifact_version_identity_missing``.

Missing content versus a failing backend
========================================

The port contract says ``read_artifact`` returns ``None`` only when the object is
*confirmed* absent, and that every other storage error must raise so the caller keeps its
checkpoint.  ``MinioFileStore.get_bytes`` collapses every S3 exception into
``FileNotFoundError``, so on the S3 backend "not found" is *not* a confirmed absence.  The
adapter therefore distinguishes the two by backend:

* the local backend (and any store that does not declare a collapsing backend) reports
  absence definitively, so ``read_artifact`` returns ``None``;
* the ``s3`` backend cannot, so a ``FileNotFoundError`` there is refused as
  ``artifact_store_missing_unconfirmed`` and the caller retries after recovery instead of
  quarantining a row on the strength of a transient network error.

``missing_is_confirmed`` overrides the derivation for a store that fixes the ambiguity.

Nothing here logs, hashes or stores a payload, a URI body or a secret
====================================================================

Log lines and refusal messages carry the reason code, the partition tuple, the version id
and the digest only.  The URI is reduced to its **scheme** before it is mentioned, because
the remainder is a storage key (i.e. a capability).
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.core.infra.object_keys import parse_content_asset_ref, parse_storage_ref

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Relations read by this adapter (all registered; nothing is created here)
# ---------------------------------------------------------------------------

LEGACY_DEFINITION_TABLE = "dynamic_app_definitions"
LEGACY_VERSION_TABLE = "dynamic_app_versions"
TARGET_VERSION_TABLE = "m04e_dynamic_app_versions"
CONTENT_ASSET_TABLE = "content_assets"
CONTENT_BLOB_VERSION_TABLE = "content_blob_versions"

#: Byte sources, in the order a resolved URI is attributed.  Only used for evidence.
BYTE_SOURCE_OBJECT_STORE = "object_store"
BYTE_SOURCE_CONTENT_ASSET = "content_asset"

#: ``^sha256:[0-9a-f]{64}$`` -- the only digest form the contract accepts.
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

#: The same ceiling the composition root states for an M04E artifact (64 MiB).
DEFAULT_MAX_OBJECT_BYTES = 64 * 1024 * 1024

#: Backends whose ``get_bytes`` collapses infrastructure errors into ``FileNotFoundError``
#: (``infrastructure/files/minio_store.py``), so a miss is not a confirmed absence there.
_COLLAPSING_BACKENDS = frozenset({"s3"})

#: Roles recorded transaction-locally, matching ``application/migration/m04e_ports.py``.
CONTEXT_ROLE = "m04e_artifact_read"

APPLY_CONTEXT_SQL = text(
    "SELECT "
    "set_config('app.tenant_id', :tenant_id, true) AS tenant_id, "
    "set_config('app.user_id', :user_id, true) AS user_id, "
    "set_config('app.role', :role, true) AS role, "
    "set_config('app.migration_role', 'true', true) AS migration_role"
)

READBACK_CONTEXT_SQL = text(
    "SELECT "
    "current_setting('app.tenant_id', true) AS tenant_id, "
    "current_setting('app.migration_role', true) AS migration_role"
)

#: Legacy version bounded through its definition, because ``dynamic_app_versions`` has no
#: tenant column of its own.
SELECT_LEGACY_VERSION_SQL = text(
    "SELECT v.id AS version_id, v.app_definition_id AS definition_id, "
    "v.artifact_digest, v.artifact_uri, d.tenant_id "
    f"FROM {LEGACY_VERSION_TABLE} v "
    f"JOIN {LEGACY_DEFINITION_TABLE} d ON d.id = v.app_definition_id "
    "WHERE d.tenant_id = :tenant_id AND v.app_definition_id = :definition_id "
    "AND v.id = :version_id LIMIT 2"
)

SELECT_TARGET_VERSION_SQL = text(
    "SELECT id AS version_id, definition_id, tenant_id, artifact_digest, artifact_uri "
    f"FROM {TARGET_VERSION_TABLE} "
    "WHERE tenant_id = :tenant_id AND definition_id = :definition_id "
    "AND id = :version_id LIMIT 2"
)

SELECT_CONTENT_ASSET_SQL = text(
    "SELECT id, owner_company_id, owner_scope, visibility "
    f"FROM {CONTENT_ASSET_TABLE} WHERE id = :asset_id LIMIT 2"
)

SELECT_CONTENT_BLOB_SQL = text(
    "SELECT id, asset_id, version, storage_key, size, sha256 "
    f"FROM {CONTENT_BLOB_VERSION_TABLE} "
    "WHERE asset_id = :asset_id ORDER BY version DESC LIMIT 1"
)


# ---------------------------------------------------------------------------
# Refusals: typed, named, redacted
# ---------------------------------------------------------------------------


class ArtifactRefusalCode(StrEnum):
    """Closed vocabulary of every way this port can refuse to answer.

    ``artifact_content_absent`` is the one code that ``read_artifact`` translates into the
    protocol's ``None``; every other code raises, because none of them is a statement about
    the object being missing.
    """

    # -- the question itself ------------------------------------------------
    MAX_BYTES_INVALID = "artifact_max_bytes_invalid"
    IDENTITY_INVALID = "artifact_identity_invalid"
    TENANT_CONTEXT_UNBOUND = "artifact_tenant_context_unbound"
    # -- the persisted row --------------------------------------------------
    IDENTITY_MISSING = "artifact_version_identity_missing"
    IDENTITY_AMBIGUOUS = "artifact_version_identity_ambiguous"
    IDENTITY_CONFLICT = "artifact_version_identity_conflict"
    DIGEST_MISSING = "artifact_expected_digest_missing"
    # -- the reference -------------------------------------------------------
    URI_MISSING = "artifact_uri_missing"
    URI_MISMATCH = "artifact_uri_mismatch"
    SCHEME_UNSUPPORTED = "artifact_uri_scheme_unsupported"
    URI_UNRESOLVED = "artifact_uri_unresolved"
    # -- the asset behind a ``content://`` reference -------------------------
    ASSET_ABSENT = "artifact_asset_absent"
    ASSET_TENANT_UNPROVEN = "artifact_asset_tenant_unproven"
    ASSET_TENANT_MISMATCH = "artifact_asset_tenant_mismatch"
    ASSET_DIGEST_MISMATCH = "artifact_asset_digest_mismatch"
    ASSET_BLOB_ABSENT = "artifact_asset_blob_absent"
    # -- the bytes -----------------------------------------------------------
    CONTENT_ABSENT = "artifact_content_absent"
    CONTENT_EXCEEDS_LIMIT = "artifact_content_exceeds_limit"
    CONTENT_DIGEST_MISMATCH = "artifact_content_digest_mismatch"
    STORE_MISSING_UNCONFIRMED = "artifact_store_missing_unconfirmed"
    STORE_READ_FAILED = "artifact_store_read_failed"


@dataclass(frozen=True, slots=True)
class ArtifactRefusal:
    """One named, redacted reason an artifact read could not produce a proof.

    Falsy by construction, so ``if refusal:`` reads as "there is a proof" and a call site
    cannot mistake a refusal for success.
    """

    code: ArtifactRefusalCode
    detail: str
    needed: str
    tenant_id: str | None = None
    app_definition_id: str | None = None
    app_version_id: str | None = None

    def __bool__(self) -> bool:
        return False

    def evidence_text(self) -> str:
        """Redacted single line: a reason code, a container tuple, never a URI or bytes."""

        partition = ""
        if self.tenant_id is not None and self.app_definition_id is not None:
            partition = f" partition=({self.tenant_id},{self.app_definition_id})"
        return f"{self.code.value}{partition}: {self.detail}"


class ArtifactContentRefused(RuntimeError):
    """A typed refusal raised by the port.  Never converted into a silent ``None``."""

    def __init__(self, refusal: ArtifactRefusal) -> None:
        self.refusal = refusal
        super().__init__(f"{refusal.evidence_text()} needed: {refusal.needed}")

    @property
    def code(self) -> ArtifactRefusalCode:
        return self.refusal.code

    @property
    def needed(self) -> str:
        return self.refusal.needed


@dataclass(frozen=True, slots=True)
class ArtifactRead:
    """Verified immutable content plus the identity and digest that were proven."""

    content: bytes
    digest: str
    artifact_uri: str
    tenant_id: str
    app_definition_id: str
    app_version_id: str
    byte_source: str

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class ArtifactVersionIdentity:
    """The persisted artifact identity of one version row."""

    tenant_id: str
    app_definition_id: str
    app_version_id: str
    artifact_digest: str
    artifact_uri: str | None
    relation: str


# ---------------------------------------------------------------------------
# The byte store this adapter needs (a subset of ``FileStorePort``)
# ---------------------------------------------------------------------------


@runtime_checkable
class ByteStore(Protocol):
    """The slice of ``FileStorePort`` this adapter uses; sync or async is fine."""

    def get_bytes(self, key: str) -> Any: ...


def _refuse(
    code: ArtifactRefusalCode,
    detail: str,
    needed: str,
    *,
    tenant_id: str | None = None,
    app_definition_id: str | None = None,
    app_version_id: str | None = None,
) -> ArtifactContentRefused:
    return ArtifactContentRefused(ArtifactRefusal(
        code=code,
        detail=detail,
        needed=needed,
        tenant_id=tenant_id,
        app_definition_id=app_definition_id,
        app_version_id=app_version_id,
    ))


def _uri_scheme(uri: str) -> str:
    """The scheme of a reference, safe to log; the remainder is a storage key."""

    scheme, separator, _rest = str(uri).partition("://")
    return f"{scheme}://" if separator else "<opaque>"


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and bool(_DIGEST.fullmatch(value))


async def _maybe_await(value: Any) -> Any:
    """Await a coroutine result and pass a plain value through.

    ``FileStoreManager`` (async) and ``LocalFileStore``/``MinioFileStore`` (sync) are both
    accepted, because a host may hand this adapter either one.
    """

    if inspect.isawaitable(value):
        return await value
    return value


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class SqlArtifactContentStore:
    """``ArtifactContentPort`` over the persisted version rows and the object store.

    One call owns one PostgreSQL transaction over the injected ``async_sessionmaker`` and
    nothing is cached between calls, so a pooled connection cannot carry one partition's
    tenant into the next read.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        file_store: ByteStore | None = None,
        max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
        missing_is_confirmed: bool | None = None,
    ) -> None:
        if session_factory is None:
            raise ValueError("artifact content store requires an async_sessionmaker")
        if (
            not isinstance(max_object_bytes, int)
            or isinstance(max_object_bytes, bool)
            or max_object_bytes < 1
        ):
            raise ValueError("max_object_bytes must be a positive integer")
        self._session_factory = session_factory
        self._file_store = file_store
        self._max_object_bytes = max_object_bytes
        self._missing_is_confirmed = missing_is_confirmed

    # -- properties -------------------------------------------------------

    @property
    def max_object_bytes(self) -> int:
        return self._max_object_bytes

    @property
    def missing_is_confirmed(self) -> bool:
        """Whether a store miss on this backend is a *confirmed* absence.

        Derived from the store's declared backend unless the host states it: only the
        collapsing ``s3`` backend cannot tell "no such object" from "backend failed".
        """

        if self._missing_is_confirmed is not None:
            return self._missing_is_confirmed
        store = self._store()
        backend = getattr(store, "backend_name", None)
        if isinstance(backend, str):
            return backend.strip().lower() not in _COLLAPSING_BACKENDS
        return True

    # -- the port ---------------------------------------------------------

    async def resolve_artifact_uri(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
    ) -> str | None:
        """Return the URI persisted on the exact version row -- never a caller's value.

        ``None`` means "this tenant's version row carries no artifact reference".  An
        ambiguous or self-contradictory row is refused rather than answered, because a
        caller cannot tell ``None`` from "the row disagrees with itself".
        """

        identity = await self._identity(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
        )
        if identity is None:
            return None
        return identity.artifact_uri

    async def read_artifact(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        artifact_uri: str,
        max_bytes: int,
    ) -> bytes | None:
        """Return verified immutable bytes, or ``None`` only when content is confirmed absent.

        The bytes are the ones this port already proved against the persisted
        ``artifact_digest``; a caller that wants the digest and its provenance should call
        :meth:`read_artifact_verified` instead.
        """

        try:
            verified = await self.read_artifact_verified(
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
                artifact_uri=artifact_uri,
                max_bytes=max_bytes,
            )
        except ArtifactContentRefused as refused:
            if refused.code is ArtifactRefusalCode.CONTENT_ABSENT:
                return None
            raise
        return verified.content

    async def read_artifact_verified(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        artifact_uri: str,
        max_bytes: int,
    ) -> ArtifactRead:
        """Read, hash and prove one artifact; refuse anything that is not a proof.

        This is the method the task contract describes: bytes **and** digest, with the
        digest checked against the value persisted on the immutable version row.  Every
        negative outcome -- including a confirmed absence -- raises
        :class:`ArtifactContentRefused` with a reason code, so nothing is silent.
        """

        self._require_arguments(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
            artifact_uri=artifact_uri,
            max_bytes=max_bytes,
        )
        located = await self._identity_bound(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
        )
        identity = located.identity
        if identity.artifact_uri != artifact_uri:
            # The caller's reference is not the persisted one.  Refuse instead of reading
            # whatever the caller named: a caller-selected URI is not an artifact identity.
            raise _refuse(
                ArtifactRefusalCode.URI_MISMATCH,
                "the supplied artifact reference is not the reference persisted on the "
                "exact version row",
                "call resolve_artifact_uri for this identity and pass its result back",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        expected_digest = identity.artifact_digest

        limit = min(max_bytes, self._max_object_bytes)
        content, byte_source = await self._content(
            located=located,
            artifact_uri=artifact_uri,
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
            expected_digest=expected_digest,
            limit=limit,
        )
        actual_digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if actual_digest != expected_digest:
            # The store returned bytes that are not this artifact.  Trusting the store here
            # would be exactly the immutability hole this adapter exists to close.
            logger.warning(
                "m04e artifact digest mismatch tenant=%s definition=%s version=%s",
                tenant_id, app_definition_id, app_version_id,
            )
            raise _refuse(
                ArtifactRefusalCode.CONTENT_DIGEST_MISMATCH,
                f"retrieved bytes hash to {actual_digest}, not the persisted artifact digest",
                "review the artifact lineage; do not replace an immutable artifact in place",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        return ArtifactRead(
            content=content,
            digest=actual_digest,
            artifact_uri=artifact_uri,
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
            byte_source=byte_source,
        )

    # -- argument checks ---------------------------------------------------

    @staticmethod
    def _require_arguments(
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        artifact_uri: str,
        max_bytes: int,
    ) -> None:
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
            raise _refuse(
                ArtifactRefusalCode.MAX_BYTES_INVALID,
                "max_bytes must be a positive integer",
                "a bounded read limit in bytes",
            )
        for name, value in (
            ("tenant_id", tenant_id),
            ("app_definition_id", app_definition_id),
            ("app_version_id", app_version_id),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise _refuse(
                    ArtifactRefusalCode.IDENTITY_INVALID,
                    f"{name} must be a non-empty, unpadded identifier",
                    "the exact persisted (tenant, definition, version) tuple",
                )
        if not isinstance(artifact_uri, str) or not artifact_uri.strip():
            raise _refuse(
                ArtifactRefusalCode.URI_MISSING,
                "an empty artifact reference cannot name an object",
                "the reference persisted on the exact version row",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )

    # -- identity ---------------------------------------------------------

    async def _identity_bound(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
    ) -> _LocatedIdentity:
        """Resolve the identity inside a transaction whose tenant context is proven first.

        The ``content://`` location is proven in the *same* transaction, so a row that
        exists but does not prove ownership is refused before any byte is read.
        """

        parameters = {
            "tenant_id": tenant_id,
            "definition_id": app_definition_id,
            "version_id": app_version_id,
        }
        async with self._session_factory() as session:
            async with session.begin():
                await self._bind_tenant(session, tenant_id)
                legacy = await self._rows(session, SELECT_LEGACY_VERSION_SQL, parameters)
                target = await self._rows(session, SELECT_TARGET_VERSION_SQL, parameters)
                identity = self._merge(
                    legacy, target,
                    tenant_id=tenant_id,
                    app_definition_id=app_definition_id,
                    app_version_id=app_version_id,
                )
                if identity.artifact_uri is None:
                    raise _refuse(
                        ArtifactRefusalCode.URI_MISSING,
                        "the persisted version row carries no artifact reference",
                        "a persisted artifact_uri on the exact version row",
                        tenant_id=tenant_id,
                        app_definition_id=app_definition_id,
                        app_version_id=app_version_id,
                    )
                asset, blob = await self._content_location(
                    session,
                    artifact_uri=identity.artifact_uri,
                    tenant_id=tenant_id,
                    app_definition_id=app_definition_id,
                    app_version_id=app_version_id,
                    expected_digest=identity.artifact_digest,
                )
        return _LocatedIdentity(identity=identity, asset=asset, blob=blob)

    async def _identity(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
    ) -> ArtifactVersionIdentity | None:
        """``resolve_artifact_uri``'s read: absent and ambiguous are different answers."""

        for name, value in (
            ("tenant_id", tenant_id),
            ("app_definition_id", app_definition_id),
            ("app_version_id", app_version_id),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise _refuse(
                    ArtifactRefusalCode.IDENTITY_INVALID,
                    f"{name} must be a non-empty, unpadded identifier",
                    "the exact persisted (tenant, definition, version) tuple",
                )
        parameters = {
            "tenant_id": tenant_id,
            "definition_id": app_definition_id,
            "version_id": app_version_id,
        }
        async with self._session_factory() as session:
            async with session.begin():
                await self._bind_tenant(session, tenant_id)
                legacy = await self._rows(session, SELECT_LEGACY_VERSION_SQL, parameters)
                target = await self._rows(session, SELECT_TARGET_VERSION_SQL, parameters)
        if not legacy and not target:
            return None
        return self._merge(
            legacy,
            target,
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
        )

    def _merge(
        self,
        legacy: Sequence[Mapping[str, Any]],
        target: Sequence[Mapping[str, Any]],
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
    ) -> ArtifactVersionIdentity:
        """Combine the legacy and canonical rows into one proven identity."""

        rows: list[tuple[str, Mapping[str, Any]]] = [
            (LEGACY_VERSION_TABLE, row) for row in legacy
        ] + [(TARGET_VERSION_TABLE, row) for row in target]
        if not rows:
            raise _refuse(
                ArtifactRefusalCode.IDENTITY_MISSING,
                "no persisted version of this tenant matches the (definition, version) tuple",
                "the exact persisted M04 version row the backfill is binding",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        if len(rows) > 1 and len({str(row.get("version_id")) for _name, row in rows}) > 1:
            raise _refuse(
                ArtifactRefusalCode.IDENTITY_AMBIGUOUS,
                "the identity tuple resolves to more than one persisted version",
                "a unique (definition, version) key on both relations",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        if len(rows) > 2:
            raise _refuse(
                ArtifactRefusalCode.IDENTITY_AMBIGUOUS,
                "one relation returned more than one row for the exact identity",
                "a unique (definition, version) key on both relations",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )

        digests: set[str] = set()
        uris: set[str | None] = set()
        for _name, row in rows:
            if str(row.get("tenant_id")) != tenant_id:
                # Defence in depth: the SQL predicate and the bound tenant already bound
                # this, but a widened read must never become another tenant's artifact.
                raise _refuse(
                    ArtifactRefusalCode.IDENTITY_MISSING,
                    "the persisted row belongs to another tenant than the requested one",
                    "the exact persisted M04 version row of this tenant",
                    tenant_id=tenant_id,
                    app_definition_id=app_definition_id,
                    app_version_id=app_version_id,
                )
            if str(row.get("definition_id")) != app_definition_id:
                raise _refuse(
                    ArtifactRefusalCode.IDENTITY_MISSING,
                    "the persisted row belongs to another definition than the requested one",
                    "the exact persisted M04 version row of this definition",
                    tenant_id=tenant_id,
                    app_definition_id=app_definition_id,
                    app_version_id=app_version_id,
                )
            digests.add(str(row.get("artifact_digest")))
            uris.add(row.get("artifact_uri"))

        if len(digests) != 1 or len(uris) != 1:
            raise _refuse(
                ArtifactRefusalCode.IDENTITY_CONFLICT,
                "the legacy and canonical rows disagree on the artifact identity",
                "one artifact reference and digest for both relations before reading bytes",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        digest = next(iter(digests))
        if not _is_digest(digest):
            raise _refuse(
                ArtifactRefusalCode.DIGEST_MISSING,
                "the persisted artifact digest is not a sha256 digest",
                "a persisted artifact_digest matching ^sha256:[0-9a-f]{64}$",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        uri = next(iter(uris))
        return ArtifactVersionIdentity(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
            artifact_digest=digest,
            artifact_uri=uri if isinstance(uri, str) and uri.strip() else None,
            relation="+".join(name for name, _row in rows),
        )

    # -- content location (a ``content://`` reference) ---------------------

    async def _content_location(
        self,
        session: AsyncSession,
        *,
        artifact_uri: str,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        expected_digest: str,
    ) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
        """Prove a ``content://`` reference really names this tenant's artifact.

        Returns ``(None, None)`` for the direct object-store schemes, which need no
        database hop.  Any row that exists but does not prove ownership is refused here,
        so the byte read is only ever attempted against a proven location.
        """

        if not artifact_uri.startswith("content://"):
            self._object_key(artifact_uri, tenant_id=tenant_id,
                             app_definition_id=app_definition_id,
                             app_version_id=app_version_id)
            return None, None

        try:
            asset_id = parse_content_asset_ref(artifact_uri)
        except ValueError as exc:
            raise _refuse(
                ArtifactRefusalCode.URI_UNRESOLVED,
                "the content reference does not carry an asset identifier",
                "content://<content_assets.id> or a reviewed URI-to-blob mapping",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            ) from exc

        assets = await self._rows(session, SELECT_CONTENT_ASSET_SQL, {"asset_id": asset_id})
        if len(assets) != 1:
            raise _refuse(
                ArtifactRefusalCode.ASSET_ABSENT,
                "the referenced content asset does not exist or is not unique",
                "the exact content_assets row this version artifact was stored as",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        asset = assets[0]
        owner = asset.get("owner_company_id")
        if owner is None:
            # Platform content is readable in principle but is not tenant-immutable
            # evidence for this partition, so it must not stand in for one.
            raise _refuse(
                ArtifactRefusalCode.ASSET_TENANT_UNPROVEN,
                "the referenced content asset has no owning tenant",
                "a tenant-owned content asset, or a reviewed platform-artifact policy",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        if str(owner) != tenant_id:
            raise _refuse(
                ArtifactRefusalCode.ASSET_TENANT_MISMATCH,
                "the referenced content asset is owned by another tenant",
                "the exact content_assets row owned by this partition tenant",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )

        blobs = await self._rows(session, SELECT_CONTENT_BLOB_SQL, {"asset_id": asset_id})
        if len(blobs) != 1 or not str(blobs[0].get("storage_key") or "").strip():
            raise _refuse(
                ArtifactRefusalCode.ASSET_BLOB_ABSENT,
                "the referenced content asset has no readable blob version",
                "a finalized content_blob_versions row for this asset",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        blob = blobs[0]
        sealed = blob.get("sha256")
        if isinstance(sealed, str) and sealed.strip():
            if sealed.strip().lower() != expected_digest.removeprefix("sha256:"):
                # The asset is not this artifact.  Refusing before the transfer keeps a
                # wrong asset from being reported as "mutated bytes".
                raise _refuse(
                    ArtifactRefusalCode.ASSET_DIGEST_MISMATCH,
                    "the content asset's sealed digest is not the persisted artifact digest",
                    "the content asset that was actually sealed for this version",
                    tenant_id=tenant_id,
                    app_definition_id=app_definition_id,
                    app_version_id=app_version_id,
                )
        return asset, blob

    # -- bytes ------------------------------------------------------------

    def _object_key(
        self,
        artifact_uri: str,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
    ) -> str:
        """Map an ``object://``/``file://`` reference onto a store key, or refuse."""

        try:
            return parse_storage_ref(artifact_uri)
        except ValueError as exc:
            raise _refuse(
                ArtifactRefusalCode.SCHEME_UNSUPPORTED,
                f"the artifact reference uses the {_uri_scheme(artifact_uri)} scheme, which "
                "has no reviewed byte mapping",
                "object://<key>, file://<key> or content://<content_assets.id>; any other "
                "scheme needs a reviewed URI-to-blob mapping",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            ) from exc

    async def _content(
        self,
        *,
        located: _LocatedIdentity,
        artifact_uri: str,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        expected_digest: str,
        limit: int,
    ) -> tuple[bytes, str]:
        """Resolve the reference to a store key and read it within ``limit`` bytes.

        The location was already proven in the identity transaction, so this method only
        turns a proven location into a key: no second lookup, no second interpretation of
        the URI.
        """

        if artifact_uri.startswith("content://"):
            blob = located.blob
            if blob is None or not str(blob.get("storage_key") or "").strip():
                raise _refuse(
                    ArtifactRefusalCode.ASSET_BLOB_ABSENT,
                    "the referenced content asset has no readable blob version",
                    "a finalized content_blob_versions row for this asset",
                    tenant_id=tenant_id,
                    app_definition_id=app_definition_id,
                    app_version_id=app_version_id,
                )
            key = str(blob["storage_key"])
            byte_source = BYTE_SOURCE_CONTENT_ASSET
        else:
            key = self._object_key(
                artifact_uri,
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
            byte_source = BYTE_SOURCE_OBJECT_STORE
        return await self._read_key(
            key,
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
            limit=limit,
        ), byte_source

    async def _read_key(
        self,
        key: str,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        limit: int,
    ) -> bytes:
        """Read one object, enforcing ``limit`` before and after the transfer."""

        store = self._store()
        declared = await self._declared_size(store, key)
        if declared is not None and declared > limit:
            raise _refuse(
                ArtifactRefusalCode.CONTENT_EXCEEDS_LIMIT,
                "the stored object is larger than the configured read bound",
                "an artifact within the configured byte bound, or a reviewed larger bound",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        try:
            payload = await _maybe_await(store.get_bytes(key))
        except FileNotFoundError as exc:
            if self.missing_is_confirmed:
                raise _refuse(
                    ArtifactRefusalCode.CONTENT_ABSENT,
                    "the object this reference names is not present in the object store",
                    "the immutable artifact object, restored from backup if it was lost",
                    tenant_id=tenant_id,
                    app_definition_id=app_definition_id,
                    app_version_id=app_version_id,
                ) from exc
            # The S3 backend reports a transient failure as ``FileNotFoundError``; a
            # quarantine recorded on that basis would be a false finding.
            raise _refuse(
                ArtifactRefusalCode.STORE_MISSING_UNCONFIRMED,
                "the object store backend cannot distinguish a missing object from a "
                "backend failure",
                "a backend whose read contract separates a confirmed miss from an "
                "infrastructure error",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            ) from exc
        except OSError as exc:
            raise _refuse(
                ArtifactRefusalCode.STORE_READ_FAILED,
                "the object store failed while reading the artifact",
                "a healthy object store; retry from the unchanged checkpoint",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            ) from exc
        if not isinstance(payload, bytes):
            raise _refuse(
                ArtifactRefusalCode.STORE_READ_FAILED,
                "the object store returned something other than bytes",
                "a byte-oriented object store backend",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        if len(payload) > limit:
            raise _refuse(
                ArtifactRefusalCode.CONTENT_EXCEEDS_LIMIT,
                "the stored object is larger than the configured read bound",
                "an artifact within the configured byte bound, or a reviewed larger bound",
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
            )
        return payload

    @staticmethod
    async def _declared_size(store: ByteStore, key: str) -> int | None:
        """The object's size when the store can report it without transferring bytes."""

        head = getattr(store, "head", None)
        if head is None:
            return None
        try:
            described = await _maybe_await(head(key))
        except FileNotFoundError:
            # Size is a guard, not a proof: an unreadable head lets the read itself decide,
            # and the post-read bound still applies.
            return None
        except OSError:
            return None
        size = getattr(described, "size", None)
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
            return size
        return None

    def _store(self) -> ByteStore:
        """The object store, resolved lazily so assembly never starts a resource."""

        if self._file_store is not None:
            return self._file_store
        from prodavan.infrastructure.files.manager import ensure_file_store

        return ensure_file_store()

    # -- helpers ----------------------------------------------------------

    @staticmethod
    async def _bind_tenant(session: AsyncSession, tenant_id: str) -> None:
        """Apply and prove the transaction-local tenant context before any table read."""

        await session.execute(APPLY_CONTEXT_SQL, {
            "tenant_id": tenant_id,
            "user_id": "migration-operator",
            "role": CONTEXT_ROLE,
        })
        readback = (await session.execute(READBACK_CONTEXT_SQL)).mappings().one()
        if (
            str(readback.get("tenant_id") or "") != tenant_id
            or str(readback.get("migration_role") or "") != "true"
        ):
            # An unbound tenant would make the FORCE RLS version tables return zero rows,
            # which layer 1 would report as "absent" -- a false finding about a row that
            # may well exist.
            raise _refuse(
                ArtifactRefusalCode.TENANT_CONTEXT_UNBOUND,
                "the transaction-local tenant setting does not match the requested tenant",
                "a transaction that can bind app.tenant_id transaction-locally",
                tenant_id=tenant_id,
            )

    @staticmethod
    async def _rows(
        session: AsyncSession, statement: Any, parameters: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple((await session.execute(statement, dict(parameters))).mappings().all())


@dataclass(frozen=True, slots=True)
class _LocatedIdentity:
    """Internal carrier so the identity transaction can hand back its own location proof."""

    identity: ArtifactVersionIdentity
    asset: Mapping[str, Any] | None
    blob: Mapping[str, Any] | None


def build_m04e_artifact_content_store(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None,
    file_store: ByteStore | None = None,
    max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    missing_is_confirmed: bool | None = None,
) -> SqlArtifactContentStore | None:
    """Construct the port for a partition, or ``None`` when it cannot exist yet.

    ``None`` is the honest answer for "no session factory", i.e. the same absence the
    planner already reports as ``partition_session_factory_missing``; every other input is
    a scalar with a reviewed default, so nothing else can make construction impossible.
    """

    if session_factory is None:
        return None
    return SqlArtifactContentStore(
        session_factory=session_factory,
        file_store=file_store,
        max_object_bytes=max_object_bytes,
        missing_is_confirmed=missing_is_confirmed,
    )


__all__ = [
    "APPLY_CONTEXT_SQL",
    "ArtifactContentRefused",
    "ArtifactRead",
    "ArtifactRefusal",
    "ArtifactRefusalCode",
    "ArtifactVersionIdentity",
    "BYTE_SOURCE_CONTENT_ASSET",
    "BYTE_SOURCE_OBJECT_STORE",
    "ByteStore",
    "CONTENT_ASSET_TABLE",
    "CONTENT_BLOB_VERSION_TABLE",
    "DEFAULT_MAX_OBJECT_BYTES",
    "LEGACY_DEFINITION_TABLE",
    "LEGACY_VERSION_TABLE",
    "READBACK_CONTEXT_SQL",
    "SELECT_CONTENT_ASSET_SQL",
    "SELECT_CONTENT_BLOB_SQL",
    "SELECT_LEGACY_VERSION_SQL",
    "SELECT_TARGET_VERSION_SQL",
    "SqlArtifactContentStore",
    "TARGET_VERSION_TABLE",
    "build_m04e_artifact_content_store",
]

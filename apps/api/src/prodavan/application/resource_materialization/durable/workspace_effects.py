"""MIG-RES-EFFECT-01 — Pod/workspace implementation of the resource effect port.

Before this module the durable apply path had every other collaborator (gate,
attempt ledger, head CAS, snapshots, redacted audit) but no effect boundary, so
``DurableMaterializationExecutor`` refused every **populated** plan with
``BLOCKED resource_effect_port_missing`` (see ``durable/effects.py``, whose
docstring states that a missing port must never degrade to a no-op success).
This module supplies that boundary over the *existing* Pod workspace runtime: it
owns no filesystem transport of its own.

What it is
==========

:class:`PodWorkspaceOperationEffectPort` implements both ``ResourceOperationEffectPort``
(``apply_operation`` / ``compensate_operation``) and the ``RollbackEffectPort``
signature (``apply_rollback``, a typed refusal — see below).

* writes go through ``PodWorkspacePort.write_bytes`` (the same port
  ``PodWorkspaceService`` uses), addressed by a ``runtime_ref`` resolved from the
  project's live runtime row;
* the runtime must be **running** before a single byte is written: the resolver
  applies the same predicates as ``agent.runtime_guard.require_running_pod_runtime``
  (a stub runtime, a non-``running`` ``observed_state``, an empty ref or a
  ``object-ws:`` ref are all refusals), because that guard itself needs a
  ``Principal``/``EmployeeRow`` that a ledger-driven effect does not have;
* ``prompt`` payloads are written as their UTF-8 text, ``file`` payloads as their
  inline text, their inline base64 body or the immutable asset bytes referenced by
  the payload's ``file_ref``/``asset_ref`` (read through the existing
  ``FileStoreManager``, never a new store);
* ``env``, ``mcp`` and ``scope`` have no workspace write location in this codebase
  (env files and MCP/scope state are owned by the Pod runtime materialization
  zone, not by a resource apply), so they answer a **named refusal**
  ``resource_effect_<kind>_unsupported`` instead of pretending to have applied.

Fence semantics
===============

The ledger owns the fence. This adapter never mints, renews or interprets a
fence; it only proves it is allowed to act under the one it was handed:

* every receipt echoes the exact ``fence_token`` and ``fence_generation`` it
  received and carries ``operation_digest = resource_operation_digest(operation)``,
  so ``OperationEffectReceipt.matches`` is the ledger's own comparison;
* ``fence.resource_key`` must be the key derived for ``project_id``; a fence minted
  for another resource is refused (``resource_effect_fence_resource_mismatch``), so
  an effect cannot be re-aimed at a project it was not claimed for;
* a refusal is a receipt with ``succeeded=False`` and a redacted reason code — the
  executor turns it into a redacted diagnostic and, for a required operation, a
  ``FAILED`` attempt with compensation.

Idempotency
===========

``apply_operation`` remembers, per (project, resource key, generation, fence token,
operation digest), the receipt it produced. A repeat under the *same* fence returns
that receipt unchanged and performs no second write. The memory is bounded: an
evicted entry only loses the ability to answer a replay (a repeat then re-writes
the same bytes, which is idempotent by content) or to compensate (which then
refuses instead of deleting — see below). Cross-process repetition is the ledger's
job: the idempotency key is resolved before the CAS, so a second worker never
reaches this adapter with the same plan.

Compensation
============

``compensate_operation`` deletes a file this adapter wrote **under the same fence**
and records that compensation, so repeating it is an idempotent success
(``resource_effect_compensated``) rather than a second delete. If the file is
already absent it reports ``resource_effect_compensated_absent`` (still
``succeeded``). Two cases fail closed rather than guess:

* no write receipt for this exact fence (process restart, evicted entry) —
  ``resource_effect_compensation_unrecorded``: it will not delete a path it cannot
  prove it wrote;
* the target exists but is a directory — ``resource_effect_compensation_conflict``.

A *new* successful write under the same key clears the compensation marker, so the
file that reappeared can be compensated again in a later attempt.

Rollback is a refusal, not a stub
=================================

``apply_rollback`` cannot restore a previous generation: no revision snapshot of
workspace bytes is persisted anywhere, and this module must not "restore" by
re-walking resource versions (that would claim a generation the ledger never
proved). It therefore returns a receipt whose ``reason_code`` is
``resource_effect_rollback_unsupported`` and whose ``operation_digest`` is
*deliberately not* the digest the executor expects for a rollback
(``canonical_digest({"rollback_of_generation": target_generation})``). That
mismatch is the fail-closed part: the executor's rollback branch only tests
``receipt.matches(...)`` and ignores ``succeeded``, so a matching receipt with
``succeeded=False`` would be accepted as a completed rollback. Reporting a
non-matching digest makes the ledger answer
``FAILED rollback_effect_unknown`` — exactly the documented "unknown outcome" path.
The missing ``succeeded`` check is an executor-side defect (outside this task's file
zone) recorded in the acceptance artifact.

Observability
=============

Logs carry the operation kind, the reason code, the fence generation and a byte
count only. Never the target path, never the payload, never an exception message
(an exception message may embed a path or content). Secret material in a payload
is rejected earlier by ``resources.api.commands.validate_payload``; this adapter
adds no second path for it.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.materialization_adapter import ResourceOperation
from prodavan.application.pod_service.ports.workspace import PodWorkspacePort, WorkspaceEntry
from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.application.resource_materialization.durable.contracts import (
    resource_key_for_project,
)
from prodavan.application.resource_materialization.durable.effects import (
    OperationEffectReceipt,
    OperationFence,
    resource_operation_digest,
)
from prodavan.application.migration.runtime_materialization.state_machine import (
    canonical_digest,
)
from prodavan.domain.errors import AppError
from prodavan.domain.materialization import OperationKind

logger = logging.getLogger(__name__)

#: Same ceiling as ``PodWorkspaceService.write_content`` (10 MiB): a resource
#: payload that the workspace API would itself refuse must not be attempted here.
_MAX_EFFECT_BYTES = 10 * 1024 * 1024
#: ``PodWorkspaceService.create_directory`` bounds a workspace path at 500 chars.
_MAX_TARGET_LENGTH = 500
#: Bounded replay memory; see the idempotency section of the module docstring.
_DEFAULT_APPLIED_LIMIT = 2048

#: Kinds with a real workspace write location today.
_WRITABLE_KINDS = frozenset({OperationKind.PROMPT, OperationKind.FILE})
_TEXT_KEYS = ("body_md", "body", "content", "text")
_ASSET_KEYS = ("asset_ref", "file_ref", "asset")
_CONTROL_CHARS = frozenset("\x00\r\n\t\v\f")
_ABSOLUTE_TARGETS = ("/", "\\")

# Reason codes. All match ``durable.contracts._CODE``
# (``^[a-z][a-z0-9_.-]{0,63}$``) and name no credential material, so they survive
# ``AttemptSnapshot``/``RedactedDiagnostic`` validation and can be logged or
# persisted verbatim.
REASON_APPLIED = "applied"
REASON_PROJECT_SCOPE_INVALID = "resource_effect_project_scope_invalid"
REASON_FENCE_RESOURCE_MISMATCH = "resource_effect_fence_resource_mismatch"
REASON_RUNTIME_RESOLVER_MISSING = "resource_effect_runtime_resolver_missing"
REASON_RUNTIME_UNAVAILABLE = "resource_effect_runtime_unavailable"
REASON_RUNTIME_NOT_RUNNING = "resource_effect_runtime_not_running"
REASON_TARGET_MISSING = "resource_effect_target_missing"
REASON_TARGET_ABSOLUTE = "resource_effect_target_absolute"
REASON_TARGET_UNSAFE = "resource_effect_target_unsafe"
REASON_TARGET_TOO_LONG = "resource_effect_target_too_long"
REASON_PROMPT_PAYLOAD_MISSING = "resource_effect_prompt_payload_missing"
REASON_PROMPT_PAYLOAD_UNSUPPORTED = "resource_effect_prompt_payload_unsupported"
REASON_FILE_PAYLOAD_UNSUPPORTED = "resource_effect_file_payload_unsupported"
REASON_ASSET_UNREADABLE = "resource_effect_asset_unreadable"
REASON_PAYLOAD_TOO_LARGE = "resource_effect_payload_too_large"
REASON_WRITE_UNKNOWN = "resource_effect_write_unknown"
REASON_COMPENSATED = "resource_effect_compensated"
REASON_COMPENSATED_ABSENT = "resource_effect_compensated_absent"
REASON_COMPENSATION_UNRECORDED = "resource_effect_compensation_unrecorded"
REASON_COMPENSATION_CONFLICT = "resource_effect_compensation_conflict"
REASON_COMPENSATION_UNKNOWN = "resource_effect_compensation_unknown"
REASON_ROLLBACK_UNSUPPORTED = "resource_effect_rollback_unsupported"


def unsupported_reason_code(kind: OperationKind | str) -> str:
    """Named refusal for an operation kind with no workspace write location."""
    value = kind.value if isinstance(kind, OperationKind) else str(kind)
    return f"resource_effect_{value}_unsupported"


class ResourceEffectRefused(Exception):
    """Typed refusal.

    ``str(exc)`` is the reason code and nothing else, so an accidental log of the
    exception cannot leak a payload or a path. ``effect_unknown`` distinguishes
    "nothing was written" (``False``) from "something may have been written"
    (``True``), which the executor treats as fatal even for optional operations.
    """

    def __init__(self, reason_code: str, *, effect_unknown: bool = False) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.effect_unknown = effect_unknown


# --------------------------------------------------------------------------- #
# Ports used by the adapter
# --------------------------------------------------------------------------- #


class ProjectRuntimeRefResolver(Protocol):
    """Resolve the live ``runtime_ref`` of a project, or ``None`` if it cannot."""

    async def runtime_ref(self, *, project_id: str) -> str | None: ...


class AssetBytesReader(Protocol):
    """Read immutable asset bytes by their object-store key."""

    async def read(self, *, storage_key: str) -> bytes | None: ...


def running_runtime_ref(runtime: Mapping[str, Any] | None) -> str | None:
    """Extract a writable runtime ref, applying ``runtime_guard``'s predicates.

    Mirrors ``application.agent.runtime_guard.require_running_pod_runtime`` minus the
    principal/employee access check, which a ledger-driven effect cannot supply:
    a stub runtime, an ``observed_state`` other than ``running``, an empty ref and a
    non-k8s ``object-ws:`` ref are all refusals (``None``).
    """
    if not runtime:
        return None
    if runtime.get("stub"):
        return None
    if runtime.get("observed_state") != "running":
        return None
    ref = str(runtime.get("k8s_pod_name") or runtime.get("runtime_ref") or "").strip()
    if not ref or ref.startswith("object-ws:"):
        return None
    return ref


@dataclass(frozen=True, slots=True)
class SqlAlchemyProjectRuntimeRefResolver:
    """Production resolver: the project's runtime row, in its own short session."""

    session_factory: async_sessionmaker[AsyncSession]

    async def runtime_ref(self, *, project_id: str) -> str | None:
        from prodavan.application.pod_service.query import PodQuery

        async with self.session_factory() as session:
            runtime = await PodQuery(session).runtime_view(project_id)
        return running_runtime_ref(runtime)


class FileStoreAssetBytesReader:
    """Asset bytes from the existing ``FileStoreManager`` (no new object store)."""

    async def read(self, *, storage_key: str) -> bytes | None:
        key = storage_key.strip()
        if not key or key.startswith(_ABSOLUTE_TARGETS) or ".." in key.split("/"):
            raise ValueError("asset storage key must be a relative object key")
        from prodavan.infrastructure.files.manager import ensure_file_store

        def _load() -> bytes | None:
            try:
                return ensure_file_store().get_bytes_sync(key)
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_load)


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #


def workspace_relative_path(target: str) -> str:
    """Return the canonical relative workspace path of ``target`` or raise.

    The safety contract is deliberately stricter than "the Pod normalizes it":

    * empty/blank targets, absolute targets (POSIX ``/`` or a Windows drive letter)
      and NUL/control characters are refused;
    * any empty segment (``a//b``, ``a/``), ``.`` or ``..`` segment is refused on the
      **raw** string, before ``PurePosixPath`` could collapse it away;
    * percent-encoded input is refused whenever decoding changes the path (so
      ``%2e%2e%2f`` cannot smuggle a traversal past the raw checks);
    * the result is bounded by the workspace API's own 500-character path limit.

    The canonical form is then computed by ``normalize_workspace_path`` — the same
    function the workspace API and the resource planner use — so this module never
    introduces a second path dialect.
    """
    if not isinstance(target, str) or not target.strip():
        raise ResourceEffectRefused(REASON_TARGET_MISSING)
    raw = target.strip().replace("\\", "/")
    if any(ch in _CONTROL_CHARS for ch in raw):
        raise ResourceEffectRefused(REASON_TARGET_UNSAFE)
    if raw.startswith(_ABSOLUTE_TARGETS) or (len(raw) > 1 and raw[1] == ":"):
        raise ResourceEffectRefused(REASON_TARGET_ABSOLUTE)
    segments = raw.split("/")
    if any(segment == "" for segment in segments):
        raise ResourceEffectRefused(REASON_TARGET_UNSAFE)
    if any(segment in (".", "..") for segment in segments):
        raise ResourceEffectRefused(REASON_TARGET_UNSAFE)
    if len(raw) > _MAX_TARGET_LENGTH:
        raise ResourceEffectRefused(REASON_TARGET_TOO_LONG)
    try:
        canonical = normalize_workspace_path(raw)
    except AppError as exc:
        raise ResourceEffectRefused(REASON_TARGET_UNSAFE) from exc
    if canonical != raw:
        # Percent decoding (or any other rewrite) changed the path: refuse it rather
        # than write to a target the planner never named.
        raise ResourceEffectRefused(REASON_TARGET_UNSAFE)
    return canonical


# --------------------------------------------------------------------------- #
# Payload decoding
# --------------------------------------------------------------------------- #


def _inline_text(payload: Mapping[str, Any]) -> str | None:
    for key in _TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _asset_storage_key(payload: Mapping[str, Any]) -> str | None:
    for key in _ASSET_KEYS:
        ref = payload.get(key)
        if isinstance(ref, Mapping):
            value = ref.get("storage_key")
            if isinstance(value, str) and value.strip():
                return value
    value = payload.get("storage_key")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _declares_asset(payload: Mapping[str, Any]) -> bool:
    """True when the payload names an asset identity at all (even unresolvable)."""
    return any(key in payload for key in _ASSET_KEYS) or "asset_id" in payload


def _kind_value(operation: ResourceOperation) -> str:
    kind = operation.kind
    return kind.value if isinstance(kind, OperationKind) else str(kind)


# --------------------------------------------------------------------------- #
# The port
# --------------------------------------------------------------------------- #


class PodWorkspaceOperationEffectPort:
    """Resource effect boundary over the live Pod workspace (no in-memory stub)."""

    #: This adapter cannot restore a previous generation; see ``apply_rollback``.
    rollback_supported = False

    def __init__(
        self,
        *,
        workspace: PodWorkspacePort | None = None,
        runtime: ProjectRuntimeRefResolver | None = None,
        assets: AssetBytesReader | None = None,
        applied_limit: int = _DEFAULT_APPLIED_LIMIT,
    ) -> None:
        if applied_limit < 1:
            raise ValueError("applied_limit must be positive")
        self._workspace = workspace
        self._runtime = runtime
        self._assets = assets
        self._applied_limit = applied_limit
        # (project_id, resource_key, generation, fence_token, operation_digest) ->
        # the receipt this adapter produced under that exact fence.
        self._applied: dict[tuple[str, str, int, str, str], OperationEffectReceipt] = {}
        # Keys this adapter already compensated, so a repeat stays an idempotent
        # success instead of becoming an "unrecorded" refusal.
        self._compensated: dict[tuple[str, str, int, str, str], None] = {}

    # ------------------------------------------------------------- protocol ---

    async def apply_operation(
        self, *, project_id: str, operation: ResourceOperation, fence: OperationFence
    ) -> OperationEffectReceipt:
        """Write one operation under ``fence``; a repeat under it is a no-op."""
        digest = resource_operation_digest(operation)
        key = self._receipt_key(project_id=project_id, fence=fence, digest=digest)
        remembered = self._applied.get(key)
        if remembered is not None:
            logger.info(
                "resource_effect.replayed kind=%s generation=%d",
                _kind_value(operation),
                fence.generation,
            )
            return remembered
        try:
            # Kind support is decided before anything else so an unsupported
            # operation always answers the named ``resource_effect_<kind>_unsupported``
            # code rather than an incidental path complaint.
            if operation.kind not in _WRITABLE_KINDS:
                raise ResourceEffectRefused(unsupported_reason_code(operation.kind))
            # The fence must belong to this project's resource set: an effect can
            # never be re-aimed at a resource it was not claimed for.
            self._require_fence(project_id=project_id, fence=fence)
            relative = workspace_relative_path(operation.target)
            data = await self._payload_bytes(kind=operation.kind, payload=operation.payload)
            runtime_ref = await self._require_runtime_ref(project_id)
            await self._workspace_port().write_bytes(
                runtime_ref=runtime_ref, path=relative, data=data
            )
        except ResourceEffectRefused as refusal:
            logger.info(
                "resource_effect.refused kind=%s code=%s generation=%d",
                _kind_value(operation),
                refusal.reason_code,
                fence.generation,
            )
            return self._receipt(
                digest=digest,
                fence=fence,
                succeeded=False,
                reason_code=refusal.reason_code,
                effect_unknown=refusal.effect_unknown,
            )
        except Exception as exc:  # noqa: BLE001 - any transport failure is unknown
            logger.warning(
                "resource_effect.write_unknown kind=%s error=%s generation=%d",
                _kind_value(operation),
                type(exc).__name__,
                fence.generation,
            )
            return self._receipt(
                digest=digest,
                fence=fence,
                succeeded=False,
                reason_code=REASON_WRITE_UNKNOWN,
                effect_unknown=True,
            )
        receipt = self._receipt(
            digest=digest, fence=fence, succeeded=True, reason_code=REASON_APPLIED
        )
        self._remember(key, receipt)
        logger.info(
            "resource_effect.applied kind=%s bytes=%d generation=%d",
            _kind_value(operation),
            len(data),
            fence.generation,
        )
        return receipt

    async def compensate_operation(
        self, *, project_id: str, operation: ResourceOperation, fence: OperationFence
    ) -> OperationEffectReceipt:
        """Idempotently delete what this adapter wrote under the same fence.

        A repeat of an already-compensated operation answers ``REASON_COMPENSATED``
        (still ``succeeded``) without touching the workspace, and a target that is
        already absent answers ``REASON_COMPENSATED_ABSENT``: both restore the
        same "the file this generation wrote is not there" state. The adapter never
        deletes a path it has no receipt for (``REASON_COMPENSATION_UNRECORDED``) and
        never removes a directory (``REASON_COMPENSATION_CONFLICT``).
        """
        digest = resource_operation_digest(operation)
        key = self._receipt_key(project_id=project_id, fence=fence, digest=digest)
        try:
            if operation.kind not in _WRITABLE_KINDS:
                raise ResourceEffectRefused(unsupported_reason_code(operation.kind))
            self._require_fence(project_id=project_id, fence=fence)
            relative = workspace_relative_path(operation.target)
            if key in self._compensated:
                # Already compensated for by an earlier call in this process.
                logger.info(
                    "resource_effect.compensated kind=%s state=already generation=%d",
                    _kind_value(operation),
                    fence.generation,
                )
                return self._receipt(
                    digest=digest,
                    fence=fence,
                    succeeded=True,
                    reason_code=REASON_COMPENSATED,
                )
            if key not in self._applied:
                raise ResourceEffectRefused(REASON_COMPENSATION_UNRECORDED)
            runtime_ref = await self._require_runtime_ref(project_id)
            entry = await self._stat(runtime_ref=runtime_ref, path=relative)
            if entry is None:
                self._forget_applied(key)
                self._remember_compensated(key)
                logger.info(
                    "resource_effect.compensated kind=%s state=absent generation=%d",
                    _kind_value(operation),
                    fence.generation,
                )
                return self._receipt(
                    digest=digest,
                    fence=fence,
                    succeeded=True,
                    reason_code=REASON_COMPENSATED_ABSENT,
                )
            if entry.kind != "file":
                raise ResourceEffectRefused(REASON_COMPENSATION_CONFLICT)
            try:
                await self._workspace_port().delete(runtime_ref=runtime_ref, path=relative)
            except Exception as exc:  # noqa: BLE001 - delete may have partially run
                logger.warning(
                    "resource_effect.compensation_unknown kind=%s error=%s generation=%d",
                    _kind_value(operation),
                    type(exc).__name__,
                    fence.generation,
                )
                return self._receipt(
                    digest=digest,
                    fence=fence,
                    succeeded=False,
                    reason_code=REASON_COMPENSATION_UNKNOWN,
                    effect_unknown=True,
                )
        except ResourceEffectRefused as refusal:
            logger.info(
                "resource_effect.compensation_refused kind=%s code=%s generation=%d",
                _kind_value(operation),
                refusal.reason_code,
                fence.generation,
            )
            return self._receipt(
                digest=digest,
                fence=fence,
                succeeded=False,
                reason_code=refusal.reason_code,
                effect_unknown=refusal.effect_unknown,
            )
        except Exception as exc:  # noqa: BLE001 - stat/transport failure is unknown
            logger.warning(
                "resource_effect.compensation_unknown kind=%s error=%s generation=%d",
                _kind_value(operation),
                type(exc).__name__,
                fence.generation,
            )
            return self._receipt(
                digest=digest,
                fence=fence,
                succeeded=False,
                reason_code=REASON_COMPENSATION_UNKNOWN,
                effect_unknown=True,
            )
        self._forget_applied(key)
        self._remember_compensated(key)
        logger.info(
            "resource_effect.compensated kind=%s state=deleted generation=%d",
            _kind_value(operation),
            fence.generation,
        )
        return self._receipt(
            digest=digest, fence=fence, succeeded=True, reason_code=REASON_COMPENSATED
        )

    async def apply_rollback(
        self,
        *,
        project_id: str,
        target_generation: int,
        target_revision_id: str,
        fence: OperationFence,
    ) -> OperationEffectReceipt:
        """Typed refusal: this adapter has no way to restore a generation.

        No workspace-byte snapshot is persisted for a revision, so "restore
        generation N" cannot be performed without inventing state. The returned
        receipt is deliberately *not* the receipt the executor expects for a
        rollback (its ``operation_digest`` is not
        ``canonical_digest({"rollback_of_generation": target_generation})``), because
        the executor's rollback branch accepts a matching receipt regardless of
        ``succeeded``; a mismatch is what makes the ledger fail closed with
        ``rollback_effect_unknown`` rather than record a rollback that never ran.

        ``effect_unknown=False``: nothing was attempted, and the caller knows it.
        """
        logger.info(
            "resource_effect.rollback_refused target_generation=%d generation=%d",
            target_generation,
            fence.generation,
        )
        return OperationEffectReceipt(
            operation_digest=_rollback_refusal_digest(target_generation),
            fence_token=fence.fence_token,
            fence_generation=fence.generation,
            succeeded=False,
            reason_code=REASON_ROLLBACK_UNSUPPORTED,
            effect_unknown=False,
        )

    # -------------------------------------------------------------- internals ---

    def _workspace_port(self) -> PodWorkspacePort:
        if self._workspace is None:
            from prodavan.application.pod_service.factory import build_pod_workspace

            # Built once, then reused: the factory returns the process-wide adapter
            # (k8s exec / agent-runtime HTTP / sandbox HTTP) according to settings.
            self._workspace = build_pod_workspace()
        return self._workspace

    def _assets_reader(self) -> AssetBytesReader:
        if self._assets is None:
            self._assets = FileStoreAssetBytesReader()
        return self._assets

    def _require_fence(self, *, project_id: str, fence: OperationFence) -> None:
        try:
            expected = resource_key_for_project(project_id)
        except ValueError as exc:
            raise ResourceEffectRefused(REASON_PROJECT_SCOPE_INVALID) from exc
        if fence.resource_key != expected:
            raise ResourceEffectRefused(REASON_FENCE_RESOURCE_MISMATCH)

    async def _require_runtime_ref(self, project_id: str) -> str:
        resolver = self._runtime
        if resolver is None:
            raise ResourceEffectRefused(REASON_RUNTIME_RESOLVER_MISSING)
        try:
            ref = await resolver.runtime_ref(project_id=project_id)
        except Exception as exc:  # noqa: BLE001 - an unreadable runtime is a refusal
            logger.warning(
                "resource_effect.runtime_lookup_failed error=%s", type(exc).__name__
            )
            raise ResourceEffectRefused(REASON_RUNTIME_UNAVAILABLE) from exc
        if not isinstance(ref, str) or not ref.strip():
            raise ResourceEffectRefused(REASON_RUNTIME_NOT_RUNNING)
        return ref.strip()

    async def _payload_bytes(
        self, *, kind: OperationKind | str, payload: Mapping[str, Any]
    ) -> bytes:
        if kind not in _WRITABLE_KINDS:
            raise ResourceEffectRefused(unsupported_reason_code(kind))
        text = _inline_text(payload)
        if text is not None:
            data = text.encode("utf-8")
        elif kind == OperationKind.FILE:
            data = await self._file_bytes(payload)
        elif (
            payload.get("prompt_fragments") is not None
            or payload.get("files") is not None
        ):
            # A composed prompt is an assembly problem this adapter must not guess.
            raise ResourceEffectRefused(REASON_PROMPT_PAYLOAD_UNSUPPORTED)
        else:
            raise ResourceEffectRefused(REASON_PROMPT_PAYLOAD_MISSING)
        if len(data) > _MAX_EFFECT_BYTES:
            raise ResourceEffectRefused(REASON_PAYLOAD_TOO_LARGE)
        return data

    async def _file_bytes(self, payload: Mapping[str, Any]) -> bytes:
        storage_key = _asset_storage_key(payload)
        if storage_key is not None:
            try:
                data = await self._assets_reader().read(storage_key=storage_key)
            except Exception as exc:  # noqa: BLE001 - unreadable asset is a refusal
                logger.warning(
                    "resource_effect.asset_read_failed error=%s", type(exc).__name__
                )
                raise ResourceEffectRefused(REASON_ASSET_UNREADABLE) from exc
            if not isinstance(data, (bytes, bytearray)):
                raise ResourceEffectRefused(REASON_ASSET_UNREADABLE)
            return bytes(data)
        encoded = payload.get("data_base64")
        if isinstance(encoded, str) and encoded.strip():
            try:
                return base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ResourceEffectRefused(REASON_FILE_PAYLOAD_UNSUPPORTED) from exc
        if _declares_asset(payload):
            # An asset the payload names but does not locate cannot be materialized;
            # refusing keeps the ledger from recording a file it never wrote.
            raise ResourceEffectRefused(REASON_ASSET_UNREADABLE)
        raise ResourceEffectRefused(REASON_FILE_PAYLOAD_UNSUPPORTED)

    async def _stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry | None:
        try:
            return await self._workspace_port().stat(runtime_ref=runtime_ref, path=path)
        except KeyError:
            return None
        except AppError as exc:
            if exc.code == "WORKSPACE_NOT_FOUND":
                return None
            raise

    def _receipt_key(
        self, *, project_id: str, fence: OperationFence, digest: str
    ) -> tuple[str, str, int, str, str]:
        return (
            project_id,
            fence.resource_key,
            fence.generation,
            fence.fence_token,
            digest,
        )

    def _remember(
        self, key: tuple[str, str, int, str, str], receipt: OperationEffectReceipt
    ) -> None:
        if len(self._applied) >= self._applied_limit:
            # Eviction only forgets a receipt; it never turns a repeat into a silent
            # skip or lets compensation delete an unproven path.
            self._applied.pop(next(iter(self._applied)), None)
        self._applied[key] = receipt
        # A fresh write under this key invalidates an earlier compensation marker:
        # the file exists again and a later compensation must delete it.
        self._compensated.pop(key, None)

    def _forget_applied(self, key: tuple[str, str, int, str, str]) -> None:
        self._applied.pop(key, None)

    def _remember_compensated(self, key: tuple[str, str, int, str, str]) -> None:
        if len(self._compensated) >= self._applied_limit:
            self._compensated.pop(next(iter(self._compensated)), None)
        self._compensated[key] = None

    @staticmethod
    def _receipt(
        *,
        digest: str,
        fence: OperationFence,
        succeeded: bool,
        reason_code: str,
        effect_unknown: bool = False,
    ) -> OperationEffectReceipt:
        return OperationEffectReceipt(
            operation_digest=digest,
            fence_token=fence.fence_token,
            fence_generation=fence.generation,
            succeeded=succeeded,
            reason_code=reason_code,
            effect_unknown=effect_unknown,
        )


def _rollback_refusal_digest(target_generation: int) -> str:
    """Digest of a refused rollback — deliberately not the executor's expectation."""
    return canonical_digest({"resource_effect_rollback_refused": target_generation})


def build_pod_workspace_effect_port(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    workspace: PodWorkspacePort | None = None,
    assets: AssetBytesReader | None = None,
) -> PodWorkspaceOperationEffectPort:
    """Production composition: live workspace + project runtime resolver.

    The returned object is safe to pass as ``DurableMaterializationExecutor(effects=...)``.
    Wiring it as ``rollback_effects=`` also fails closed (every rollback answers
    ``rollback_effect_unknown``), but that is a refusal, not a capability.
    """
    return PodWorkspaceOperationEffectPort(
        workspace=workspace,
        runtime=SqlAlchemyProjectRuntimeRefResolver(session_factory=session_factory),
        assets=assets,
    )


__all__ = [
    "AssetBytesReader",
    "FileStoreAssetBytesReader",
    "PodWorkspaceOperationEffectPort",
    "ProjectRuntimeRefResolver",
    "REASON_APPLIED",
    "REASON_ASSET_UNREADABLE",
    "REASON_COMPENSATED",
    "REASON_COMPENSATED_ABSENT",
    "REASON_COMPENSATION_CONFLICT",
    "REASON_COMPENSATION_UNKNOWN",
    "REASON_COMPENSATION_UNRECORDED",
    "REASON_FENCE_RESOURCE_MISMATCH",
    "REASON_FILE_PAYLOAD_UNSUPPORTED",
    "REASON_PAYLOAD_TOO_LARGE",
    "REASON_PROJECT_SCOPE_INVALID",
    "REASON_PROMPT_PAYLOAD_MISSING",
    "REASON_PROMPT_PAYLOAD_UNSUPPORTED",
    "REASON_ROLLBACK_UNSUPPORTED",
    "REASON_RUNTIME_NOT_RUNNING",
    "REASON_RUNTIME_RESOLVER_MISSING",
    "REASON_RUNTIME_UNAVAILABLE",
    "REASON_TARGET_ABSOLUTE",
    "REASON_TARGET_MISSING",
    "REASON_TARGET_TOO_LONG",
    "REASON_TARGET_UNSAFE",
    "REASON_WRITE_UNKNOWN",
    "ResourceEffectRefused",
    "SqlAlchemyProjectRuntimeRefResolver",
    "build_pod_workspace_effect_port",
    "running_runtime_ref",
    "unsupported_reason_code",
    "workspace_relative_path",
]

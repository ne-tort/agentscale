"""Test doubles for MIG-RES-EFFECT-01 — **test tree only**.

The production path never falls back to these: ``PodWorkspaceOperationEffectPort``
refuses with a named code when its workspace port or runtime resolver is missing
(``resource_effect_runtime_resolver_missing``), and it never constructs a fake Pod.

* :class:`RecordingWorkspace` — an in-memory ``PodWorkspacePort`` that records every
  call, so a test can assert *what was written where* and, just as importantly, that
  a refused operation wrote nothing at all.
* :class:`StaticRuntimeResolver` — a ``ProjectRuntimeRefResolver`` double; a ``None``
  ref models "the Pod is not running", ``raise_error`` models an unreadable runtime
  store.
* :class:`StaticAssets` — an ``AssetBytesReader`` double over immutable blobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from prodavan.application.pod_service.ports.workspace import WorkspaceEntry
from prodavan.domain.errors import AppError

DEFAULT_RUNTIME_REF = "pod-effect-1"


def workspace_not_found(path: str) -> AppError:
    return AppError(
        code="WORKSPACE_NOT_FOUND",
        title="Not Found",
        status=404,
        detail=f"workspace entry not found: {path}",
    )


@dataclass
class RecordingWorkspace:
    """In-memory workspace that records writes/deletes and can fail on demand."""

    files: dict[str, bytes] = field(default_factory=dict)
    directories: set[str] = field(default_factory=set)
    writes: list[tuple[str, str, bytes]] = field(default_factory=list)
    deletes: list[tuple[str, str]] = field(default_factory=list)
    reads: list[tuple[str, str]] = field(default_factory=list)
    fail_writes: bool = False
    fail_deletes: bool = False
    fail_after_write: bool = False

    # -- helpers used by tests ------------------------------------------------

    def put_file(self, path: str, data: bytes) -> None:
        self.files[path] = data

    def put_directory(self, path: str) -> None:
        self.directories.add(path)

    @property
    def written_paths(self) -> list[str]:
        return [path for _runtime, path, _data in self.writes]

    # -- PodWorkspacePort -----------------------------------------------------

    async def list_entries(self, *, runtime_ref: str, path: str) -> list[WorkspaceEntry]:
        prefix = f"{path}/" if path else ""
        names = {
            key[len(prefix) :].split("/")[0]
            for key in [*self.files, *self.directories]
            if key.startswith(prefix)
        }
        entries = []
        for name in sorted(names):
            full = f"{prefix}{name}"
            entries.append(await self.stat(runtime_ref=runtime_ref, path=full))
        return entries

    async def create_directory(self, *, runtime_ref: str, path: str) -> None:
        _ = runtime_ref
        if path in self.files:
            raise workspace_not_found(path)
        self.directories.add(path)

    async def read_bytes(self, *, runtime_ref: str, path: str, max_bytes: int) -> bytes:
        self.reads.append((runtime_ref, path))
        data = self.files.get(path)
        if data is None:
            raise workspace_not_found(path)
        return data[:max_bytes]

    async def stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry:
        _ = runtime_ref
        if path in self.files:
            return WorkspaceEntry(
                name=path.rsplit("/", 1)[-1],
                path=path,
                kind="file",
                size=len(self.files[path]),
                modified_at=None,
            )
        if path in self.directories:
            return WorkspaceEntry(
                name=path.rsplit("/", 1)[-1],
                path=path,
                kind="dir",
                size=None,
                modified_at=None,
            )
        raise workspace_not_found(path)

    async def delete(self, *, runtime_ref: str, path: str) -> None:
        self.deletes.append((runtime_ref, path))
        if self.fail_deletes:
            raise RuntimeError("delete transport failed")
        if path in self.files:
            self.files.pop(path)
            return
        if path in self.directories:
            self.directories.discard(path)
            return
        raise workspace_not_found(path)

    async def move(self, *, runtime_ref: str, src: str, dst: str) -> None:
        _ = runtime_ref
        data = self.files.pop(src, None)
        if data is None:
            raise workspace_not_found(src)
        self.files[dst] = data

    async def copy(self, *, runtime_ref: str, src: str, dst: str) -> None:
        _ = runtime_ref
        data = self.files.get(src)
        if data is None:
            raise workspace_not_found(src)
        self.files[dst] = data

    async def write_bytes(self, *, runtime_ref: str, path: str, data: bytes) -> None:
        self.writes.append((runtime_ref, path, data))
        if self.fail_writes:
            raise RuntimeError("write transport failed with a secret=do-not-leak")
        self.files[path] = data
        if self.fail_after_write:
            raise RuntimeError("write answered after the bytes landed")


@dataclass
class StaticRuntimeResolver:
    """``ProjectRuntimeRefResolver`` double (``ref=None`` ⇒ Pod not running)."""

    ref: str | None = DEFAULT_RUNTIME_REF
    calls: list[str] = field(default_factory=list)
    raise_error: bool = False

    async def runtime_ref(self, *, project_id: str) -> str | None:
        self.calls.append(project_id)
        if self.raise_error:
            raise RuntimeError("runtime store unavailable")
        return self.ref


@dataclass
class StaticAssets:
    """``AssetBytesReader`` double over immutable blobs."""

    blobs: dict[str, bytes] = field(default_factory=dict)
    fail: bool = False
    keys: list[str] = field(default_factory=list)

    async def read(self, *, storage_key: str) -> bytes | None:
        self.keys.append(storage_key)
        if self.fail:
            raise RuntimeError("object store unavailable")
        return self.blobs.get(storage_key)

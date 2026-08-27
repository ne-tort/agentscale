"""Local filesystem FileStorePort (dev/tests)."""

from __future__ import annotations

import shutil
from pathlib import Path

from prodavan.infrastructure.files.port import FileStorePort, ObjectHead


class LocalFileStore(FileStorePort):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        safe = key.lstrip("/").replace("\\", "/")
        if ".." in Path(safe).parts:
            raise ValueError(f"unsafe object key: {key}")
        return self._root / safe

    def _prefix_base(self, prefix: str) -> Path:
        safe = prefix.lstrip("/").replace("\\", "/").rstrip("/")
        if not safe or ".." in Path(safe).parts:
            raise ValueError(f"unsafe object prefix: {prefix}")
        return self._root / safe

    def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def head(self, key: str) -> ObjectHead:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        stat = path.stat()
        return ObjectHead(size=stat.st_size, etag=None, content_type=None, metadata={})

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_prefix(self, prefix: str, *, limit: int = 1000) -> list[str]:
        base = self._prefix_base(prefix)
        if not base.exists():
            return []
        out: list[str] = []
        cap = max(1, int(limit))
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            out.append(path.relative_to(self._root).as_posix())
            if len(out) >= cap:
                break
        return out

    def delete_prefix(self, prefix: str) -> int:
        base = self._prefix_base(prefix)
        if base.is_dir():
            count = sum(1 for p in base.rglob("*") if p.is_file())
            shutil.rmtree(base)
            return count
        if base.is_file():
            base.unlink()
            return 1
        return 0

    def prefix_size(self, prefix: str) -> int:
        base = self._prefix_base(prefix)
        if not base.exists():
            return 0
        total = 0
        for path in base.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def list_child_prefixes(self, prefix: str, *, limit: int = 1000) -> list[str]:
        safe = prefix.lstrip("/").replace("\\", "/").rstrip("/")
        if not safe or ".." in Path(safe).parts:
            raise ValueError(f"unsafe object prefix: {prefix}")
        base = self._root / safe
        if not base.is_dir():
            return []
        out: list[str] = []
        cap = max(1, int(limit))
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            out.append(f"{safe}/{child.name}/")
            if len(out) >= cap:
                break
        return out

    def presign_get(self, key: str, *, ttl_seconds: int = 900) -> str:
        return f"file://{self._path(key)}"

    def presign_put(
        self,
        key: str,
        *,
        ttl_seconds: int = 900,
        content_type: str | None = None,
    ) -> str:
        return f"file://{self._path(key)}"

    def health(self) -> bool:
        self._root.mkdir(parents=True, exist_ok=True)
        return True

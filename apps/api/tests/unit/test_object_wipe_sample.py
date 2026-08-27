"""Unit — object wipe leftover sample for ops."""

from __future__ import annotations

from prodavan.infrastructure.files.manager import FileStoreManager


class _FakeBackend:
    def __init__(self) -> None:
        self.deleted_prefixes: list[str] = []

    def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        return None

    def get_bytes(self, key: str) -> bytes:
        raise FileNotFoundError(key)

    def delete(self, key: str) -> bool:
        return False

    def head(self, key: str):
        raise FileNotFoundError(key)

    def exists(self, key: str) -> bool:
        return False

    def list_prefix(self, prefix: str, *, limit: int = 1000) -> list[str]:
        return [f"{prefix}leftover.bin"][:limit]

    def delete_prefix(self, prefix: str) -> int:
        self.deleted_prefixes.append(prefix)
        return 3

    def prefix_size(self, prefix: str) -> int:
        return 0

    def list_child_prefixes(self, prefix: str, *, limit: int = 1000) -> list[str]:
        return []

    def presign_get(self, key: str, *, ttl_seconds: int = 900) -> str:
        return f"file://{key}"

    def presign_put(self, key: str, *, ttl_seconds: int = 900, content_type: str | None = None) -> str:
        return f"file://{key}"

    def health(self) -> bool:
        return True


def test_delete_prefix_verified_includes_remaining_sample() -> None:
    store = FileStoreManager.__new__(FileStoreManager)
    store._primary = _FakeBackend()  # type: ignore[attr-defined]
    store._local = _FakeBackend()  # type: ignore[attr-defined]
    store._mirror_local = False  # type: ignore[attr-defined]
    store._backend_name = "local"  # type: ignore[attr-defined]

    result = store.delete_prefix_verified_sync("projects/abc/")
    assert result["ok"] is False
    assert result["deleted"] == 3
    assert result["remaining"] == 1
    assert result["remaining_sample"] == ["projects/abc/leftover.bin"]

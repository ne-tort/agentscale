"""Unit — object wipe leftover sample for ops."""

from __future__ import annotations

from prodavan.core.infra.object_storage_manager import ObjectStorageManager


class _FakeBackend:
    def __init__(self) -> None:
        self.deleted_prefixes: list[str] = []

    def delete_prefix(self, prefix: str) -> int:
        self.deleted_prefixes.append(prefix)
        return 3

    def list_prefix(self, prefix: str, limit: int = 1000) -> list[str]:
        return [f"{prefix}leftover.bin"][:limit]

    def prefix_size(self, prefix: str) -> int:
        return 0


def test_delete_prefix_verified_includes_remaining_sample() -> None:
    store = ObjectStorageManager.__new__(ObjectStorageManager)
    store._primary = _FakeBackend()  # type: ignore[attr-defined]
    store._local = _FakeBackend()  # type: ignore[attr-defined]
    store._mirror_local = False  # type: ignore[attr-defined]
    store._backend_name = "local"  # type: ignore[attr-defined]

    result = store.delete_prefix_verified_sync("projects/abc/")
    assert result["ok"] is False
    assert result["deleted"] == 3
    assert result["remaining"] == 1
    assert result["remaining_sample"] == ["projects/abc/leftover.bin"]

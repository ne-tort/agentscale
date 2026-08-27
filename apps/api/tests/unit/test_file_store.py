"""Unit tests for FileStorePort + keys."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from prodavan.infrastructure.files.keys import new_blob_key
from prodavan.infrastructure.files.local_store import LocalFileStore


def test_new_blob_key_opaque() -> None:
    k1 = new_blob_key()
    k2 = new_blob_key()
    assert k1.startswith("blobs/")
    assert k1 != k2


def test_local_file_store_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalFileStore(Path(tmp))
        key = new_blob_key()
        store.put_bytes(key, b"hello", content_type="text/plain")
        assert store.exists(key)
        assert store.get_bytes(key) == b"hello"
        head = store.head(key)
        assert head.size == 5
        assert store.delete(key)
        assert not store.exists(key)


def test_local_file_store_unsafe_key() -> None:
    store = LocalFileStore(Path(tempfile.mkdtemp()))
    with pytest.raises(ValueError):
        store.put_bytes("../escape", b"x")

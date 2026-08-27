"""File Service — thin S3/local blob port (no ACL/aliases)."""

from prodavan.infrastructure.files.manager import FileStoreManager, ensure_file_store, get_file_store

__all__ = ["FileStoreManager", "ensure_file_store", "get_file_store"]

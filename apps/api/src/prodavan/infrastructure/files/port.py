"""FileStorePort — unified blob storage (S3/local + presign + prefix ops)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ObjectHead:
    size: int
    etag: str | None
    content_type: str | None
    metadata: dict[str, str]


class FileStorePort(ABC):
    @abstractmethod
    def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> bool: ...

    @abstractmethod
    def head(self, key: str) -> ObjectHead: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list_prefix(self, prefix: str, *, limit: int = 1000) -> list[str]: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int: ...

    @abstractmethod
    def prefix_size(self, prefix: str) -> int: ...

    @abstractmethod
    def list_child_prefixes(self, prefix: str, *, limit: int = 1000) -> list[str]: ...

    @abstractmethod
    def presign_get(self, key: str, *, ttl_seconds: int = 900) -> str: ...

    @abstractmethod
    def presign_put(
        self,
        key: str,
        *,
        ttl_seconds: int = 900,
        content_type: str | None = None,
    ) -> str: ...

    @abstractmethod
    def health(self) -> bool: ...

    def close(self) -> None:
        return None

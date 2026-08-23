"""Object storage backends — local FS (transitional) + S3/MinIO."""

from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ObjectStoreBackend(ABC):
    @abstractmethod
    def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> bool: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    def delete_prefix(self, prefix: str) -> int:
        """Delete all keys under prefix. Returns number of objects removed."""
        raise NotImplementedError

    def health(self) -> bool:
        return True

    def close(self) -> None:
        return None


class LocalFsObjectStore(ObjectStoreBackend):
    """Keys map to ``{root}/{key}``. Default until MinIO is mandatory in all envs."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        safe = key.lstrip("/").replace("\\", "/")
        if ".." in Path(safe).parts:
            raise ValueError(f"unsafe object key: {key}")
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

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete_prefix(self, prefix: str) -> int:
        safe = prefix.lstrip("/").replace("\\", "/").rstrip("/")
        if not safe or ".." in Path(safe).parts:
            raise ValueError(f"unsafe object prefix: {prefix}")
        base = self._root / safe
        if base.is_dir():
            count = sum(1 for p in base.rglob("*") if p.is_file())
            shutil.rmtree(base)
            return count
        if base.is_file():
            base.unlink()
            return 1
        return 0


class S3ObjectStore(ObjectStoreBackend):
    """S3-compatible store (MinIO / AWS). Uses boto3 (sync)."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        import boto3
        from botocore.client import Config

        self._bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

    def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        extra: dict[str, str] = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)

    def get_bytes(self, key: str) -> bytes:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            raise FileNotFoundError(key) from exc
        return resp["Body"].read()

    def delete(self, key: str) -> bool:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            logger.exception("s3 delete failed: %s", key)
            return False

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False

    def delete_prefix(self, prefix: str) -> int:
        safe = prefix.lstrip("/")
        if not safe or ".." in Path(safe).parts:
            raise ValueError(f"unsafe object prefix: {prefix}")
        deleted = 0
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self._bucket, Prefix=safe):
                objs = [{"Key": o["Key"]} for o in page.get("Contents") or []]
                if not objs:
                    continue
                for i in range(0, len(objs), 1000):
                    chunk = objs[i : i + 1000]
                    self._client.delete_objects(
                        Bucket=self._bucket,
                        Delete={"Objects": chunk, "Quiet": True},
                    )
                    deleted += len(chunk)
        except Exception:
            logger.exception("s3 delete_prefix failed: %s", safe)
        return deleted

    def health(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return True
        except Exception:
            return False

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception:
            logger.info("s3: creating bucket %s", self._bucket)
            self._client.create_bucket(Bucket=self._bucket)

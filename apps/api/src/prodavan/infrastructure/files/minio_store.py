"""S3/MinIO FileStorePort with presigned URLs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from prodavan.infrastructure.files.port import FileStorePort, ObjectHead

logger = logging.getLogger(__name__)


class MinioFileStore(FileStorePort):
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

    def _safe_prefix(self, prefix: str) -> str:
        safe = prefix.lstrip("/").replace("\\", "/")
        if not safe or ".." in Path(safe).parts:
            raise ValueError(f"unsafe object prefix: {prefix}")
        return safe

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

    def head(self, key: str) -> ObjectHead:
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            raise FileNotFoundError(key) from exc
        meta = {k.removeprefix("x-amz-meta-"): str(v) for k, v in resp.get("Metadata", {}).items()}
        return ObjectHead(
            size=int(resp.get("ContentLength") or 0),
            etag=str(resp.get("ETag") or "").strip('"') or None,
            content_type=resp.get("ContentType"),
            metadata=meta,
        )

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False

    def list_prefix(self, prefix: str, *, limit: int = 1000) -> list[str]:
        safe = self._safe_prefix(prefix)
        out: list[str] = []
        cap = max(1, int(limit))
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self._bucket, Prefix=safe):
                for obj in page.get("Contents") or []:
                    k = obj.get("Key")
                    if k:
                        out.append(str(k))
                    if len(out) >= cap:
                        return out
        except Exception:
            logger.exception("s3 list_prefix failed: %s", safe)
        return out

    def delete_prefix(self, prefix: str) -> int:
        safe = self._safe_prefix(prefix)
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

    def prefix_size(self, prefix: str) -> int:
        safe = self._safe_prefix(prefix)
        total = 0
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self._bucket, Prefix=safe):
                for obj in page.get("Contents") or []:
                    total += int(obj.get("Size") or 0)
        except Exception:
            logger.exception("s3 prefix_size failed: %s", safe)
        return total

    def list_child_prefixes(self, prefix: str, *, limit: int = 1000) -> list[str]:
        safe = self._safe_prefix(prefix)
        if not safe.endswith("/"):
            safe = f"{safe}/"
        out: list[str] = []
        cap = max(1, int(limit))
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self._bucket, Prefix=safe, Delimiter="/"):
                for common in page.get("CommonPrefixes") or []:
                    pref = common.get("Prefix")
                    if pref:
                        out.append(str(pref))
                    if len(out) >= cap:
                        return out
        except Exception:
            logger.exception("s3 list_child_prefixes failed: %s", safe)
        return out

    def presign_get(self, key: str, *, ttl_seconds: int = 900) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=max(60, int(ttl_seconds)),
        )

    def presign_put(
        self,
        key: str,
        *,
        ttl_seconds: int = 900,
        content_type: str | None = None,
    ) -> str:
        params: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        return self._client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=max(60, int(ttl_seconds)),
        )

    def health(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return True
        except Exception:
            return False

    def close(self) -> None:
        return None

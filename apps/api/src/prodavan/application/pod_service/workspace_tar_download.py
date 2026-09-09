"""Build workspace tar from object-store last-good (API-mediated hydrate)."""

from __future__ import annotations

import io
import logging
import tarfile
from typing import Any

from prodavan.infrastructure.files.manager import ensure_file_store

logger = logging.getLogger(__name__)

# Soft cap for in-memory archive (hydrate via API).
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_OBJECTS = 20_000


def download_workspace_tar(*, workspace_key: str, store: Any | None = None) -> bytes:
    """Pack ``projects/{key}/workspace/`` into an uncompressed ustar stream."""
    mgr = store or ensure_file_store()
    prefix = f"projects/{workspace_key}/workspace/"
    keys = mgr.list_prefix_sync(prefix, limit=_MAX_OBJECTS)
    buf = io.BytesIO()
    total = 0
    packed = 0
    with tarfile.open(fileobj=buf, mode="w") as archive:
        for key in keys:
            if key.endswith("/"):
                continue
            rel = key[len(prefix) :]
            if not rel or ".." in rel.split("/") or rel.startswith("/"):
                continue
            try:
                data = mgr.get_bytes_sync(key)
            except FileNotFoundError:
                continue
            total += len(data)
            if total > _MAX_ARCHIVE_BYTES:
                raise RuntimeError(
                    f"workspace archive exceeds {_MAX_ARCHIVE_BYTES} bytes "
                    f"(workspace_key={workspace_key})"
                )
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            packed += 1
    logger.info(
        "workspace archive built workspace_key=%s objects=%s bytes=%s",
        workspace_key,
        packed,
        total,
    )
    return buf.getvalue()

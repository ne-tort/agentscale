"""Upload tar stream members into object-store workspace prefix (last-good)."""

from __future__ import annotations

import io
import logging
import mimetypes
import tarfile
from typing import Any

from prodavan.application.pod_service.ports.dehydrate import DehydrateResult
from prodavan.application.pod_service.workspace_dehydrate_rules import is_excluded_rel, max_file_bytes
from prodavan.core.infra.object_keys import workspace_object_key
from prodavan.infrastructure.files.manager import ensure_file_store

logger = logging.getLogger(__name__)


def upload_workspace_tar(
    *,
    workspace_key: str,
    tar_bytes: bytes,
    store: Any | None = None,
) -> DehydrateResult:
    """Extract gzip/ustar archive rooted at /workspace and overwrite MinIO last-good."""
    mgr = store or ensure_file_store()
    uploaded = 0
    skipped = 0
    kept_keys: set[str] = set()
    cap = max_file_bytes()

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            rel = member.name.replace("\\", "/").lstrip("./")
            if not rel or is_excluded_rel(rel):
                skipped += 1
                continue
            if member.size > cap:
                logger.warning(
                    "dehydrate skip oversized file workspace_key=%s rel=%s size=%s",
                    workspace_key,
                    rel,
                    member.size,
                )
                skipped += 1
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                skipped += 1
                continue
            data = extracted.read()
            key = workspace_object_key(workspace_key=workspace_key, relative_path=rel)
            content_type, _ = mimetypes.guess_type(rel)
            mgr.put_bytes_sync(key, data, content_type=content_type or "application/octet-stream")
            kept_keys.add(key)
            uploaded += 1

    prefix = f"projects/{workspace_key}/workspace/"
    existing = mgr.list_prefix_sync(prefix, limit=50_000)
    deleted = 0
    for key in existing:
        if key.endswith("/"):
            continue
        if key not in kept_keys:
            if mgr.delete_sync(key):
                deleted += 1

    logger.info(
        "dehydrate uploaded=%s deleted=%s skipped=%s workspace_key=%s",
        uploaded,
        deleted,
        skipped,
        workspace_key,
    )
    return DehydrateResult(uploaded=uploaded, deleted=deleted, skipped=skipped)

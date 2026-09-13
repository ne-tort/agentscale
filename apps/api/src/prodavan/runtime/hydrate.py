"""Init-container workspace hydrate entrypoint (runs inside sandbox Pod)."""

from __future__ import annotations

import io
import logging
import os
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


def _workspace_owner_ids() -> tuple[int, int]:
    """Uid/gid for agent-runtime (Dockerfile USER node → 1000)."""
    uid = int(os.environ.get("WORKSPACE_UID", "1000"))
    gid = int(os.environ.get("WORKSPACE_GID", str(uid)))
    return uid, gid


def fix_workspace_ownership(target: Path) -> None:
    """Make hydrated tree writable by agent-runtime (hydrate init runs as root)."""
    if not hasattr(os, "chown"):
        return
    uid, gid = _workspace_owner_ids()
    try:
        os.chown(target, uid, gid)
    except OSError as exc:
        logger.warning("hydrate chown root failed target=%s err=%s", target, exc)
    for root, _dirs, files in os.walk(target):
        try:
            os.chown(root, uid, gid)
        except OSError as exc:
            logger.warning("hydrate chown dir failed path=%s err=%s", root, exc)
        for name in files:
            path = Path(root) / name
            try:
                os.chown(path, uid, gid)
            except OSError as exc:
                logger.warning("hydrate chown file failed path=%s err=%s", path, exc)


def _sync_from_api(*, target: Path) -> None:
    """Download workspace tar from prodavan-api pod surface (no MinIO in sandbox)."""
    base = (os.environ.get("PRODAVAN_API_BASE_URL") or "").rstrip("/")
    token = (os.environ.get("PRODAVAN_AUTH_TOKEN") or "").strip()
    pod_id = (os.environ.get("PRODAVAN_POD_ID") or "").strip()
    if not base or not token or not pod_id:
        raise RuntimeError("PRODAVAN_API_BASE_URL, PRODAVAN_AUTH_TOKEN, PRODAVAN_POD_ID required")

    url = f"{base}/internal/pods/{pod_id}/workspace-archive"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/x-tar"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"hydrate API HTTP {exc.code}: {body}") from exc

    target.mkdir(parents=True, exist_ok=True)
    if not data:
        for sub in ("inbox", "runs"):
            (target / sub).mkdir(parents=True, exist_ok=True)
        logger.info("hydrate API empty archive → stub dirs target=%s", target)
        return

    from prodavan.infrastructure.projects.tar_paths import tar_member_relpath

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            rel = tar_member_relpath(member.name)
            if not rel:
                continue
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            dest.write_bytes(extracted.read())
    logger.info("hydrate API complete pod_id=%s target=%s bytes=%s", pod_id, target, len(data))


def _sync_from_minio(*, workspace_key: str, target: Path) -> None:
    """Legacy path — kept for local/dev without API hydrate; not used in k3s GitOps."""
    import boto3
    from botocore.client import Config

    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    access_key = os.environ.get("MINIO_ACCESS_KEY", "")
    secret_key = os.environ.get("MINIO_SECRET_KEY", "")
    bucket = os.environ.get("MINIO_BUCKET", "prodavan")
    if not endpoint or not access_key or not secret_key:
        raise RuntimeError("MINIO_* env required for hydrate")

    prefix = f"projects/{workspace_key}/workspace/"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name=os.environ.get("MINIO_REGION", "us-east-1"),
    )
    target.mkdir(parents=True, exist_ok=True)
    paginator = client.get_paginator("list_objects_v2")
    found = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            key = obj["Key"]
            if key.endswith("/"):
                continue
            rel = key[len(prefix) :]
            if not rel or rel.startswith(".."):
                continue
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(dest))
            found += 1
    logger.info("hydrate MinIO complete workspace_key=%s objects=%s target=%s", workspace_key, found, target)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    workspace_key = os.environ.get("WORKSPACE_KEY", "").strip()
    target = Path(os.environ.get("HYDRATE_TARGET", "/workspace"))
    if not workspace_key:
        raise SystemExit("WORKSPACE_KEY is required")
    target.mkdir(parents=True, exist_ok=True)

    api_base = (os.environ.get("PRODAVAN_API_BASE_URL") or "").strip()
    api_token = (os.environ.get("PRODAVAN_AUTH_TOKEN") or "").strip()
    pod_id = (os.environ.get("PRODAVAN_POD_ID") or "").strip()
    if api_base and api_token and pod_id:
        _sync_from_api(target=target)
    elif os.environ.get("MINIO_ENDPOINT"):
        _sync_from_minio(workspace_key=workspace_key, target=target)
    else:
        for sub in ("inbox", "runs"):
            (target / sub).mkdir(parents=True, exist_ok=True)
        logger.info("hydrate stub workspace_key=%s target=%s", workspace_key, target)

    fix_workspace_ownership(target)
    print(f"ok hydrate {workspace_key} -> {target}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("hydrate failed")
        print(f"hydrate error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

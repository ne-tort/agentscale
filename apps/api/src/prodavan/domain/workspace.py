"""Workspace key helpers."""

from __future__ import annotations

import re
import uuid

WORKSPACE_KEY_RE = re.compile(r"^cab:([^:]+):([^:]+):(.+)$")


def build_workspace_key(*, tenant_slug: str, cabinet_id: uuid.UUID, project_id: str) -> str:
    return f"cab:{tenant_slug}:{cabinet_id}:{project_id}"


def parse_workspace_key(key: str) -> tuple[str, str, str]:
    match = WORKSPACE_KEY_RE.match(key)
    if match is None:
        raise ValueError("invalid workspace_key")
    return match.group(1), match.group(2), match.group(3)

"""Opaque blob storage keys for Content Service."""

from __future__ import annotations

import uuid


def new_blob_key() -> str:
    """Return a unique opaque key under blobs/."""
    return f"blobs/{uuid.uuid4().hex}"

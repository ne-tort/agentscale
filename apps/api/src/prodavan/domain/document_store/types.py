"""Document Store domain types — namespaced non-relational documents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
COLLECTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Namespaces that may omit company_id (platform/system SoT).
PLATFORM_NAMESPACES = frozenset({"platform", "system", "document_store"})

MAX_FIND_LIMIT = 200
DEFAULT_FIND_LIMIT = 50

METRIC_DOCUMENT_WRITES = "document_writes"
METRIC_DOCUMENT_READS = "document_reads"
METRIC_DOCUMENT_DELETES = "document_deletes"


@dataclass(slots=True)
class IndexSpec:
    keys: list[tuple[str, int]]
    name: str | None = None
    unique: bool = False


@dataclass(slots=True)
class WriteResult:
    matched: int
    modified: int
    upserted_id: str | None = None


@dataclass(slots=True)
class FindResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0


def physical_collection(namespace: str, collection: str) -> str:
    return f"{namespace}.{collection}"


def validate_namespace(namespace: str) -> str:
    ns = (namespace or "").strip()
    if not NAMESPACE_RE.match(ns):
        raise ValueError(f"invalid document_store namespace: {namespace!r}")
    return ns


def validate_collection(collection: str) -> str:
    col = (collection or "").strip()
    if not COLLECTION_RE.match(col):
        raise ValueError(f"invalid document_store collection: {collection!r}")
    return col

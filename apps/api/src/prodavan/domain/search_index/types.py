"""Search Index domain types — namespaced full-text / document indexes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
INDEX_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DOC_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,512}$")

# Namespaces that may omit company_id (platform/system SoT).
PLATFORM_NAMESPACES = frozenset({"platform", "system", "search_index"})

MAX_SEARCH_SIZE = 200
DEFAULT_SEARCH_SIZE = 50
MAX_BULK_BATCH = 500
MAX_MAPPING_FIELDS = 200
MAX_INDEXES_PER_COMPANY = 50

METRIC_SEARCH_WRITES = "search_writes"
METRIC_SEARCH_DELETES = "search_deletes"
METRIC_SEARCH_QUERIES = "search_queries"
METRIC_SEARCH_INDEXES_ENSURED = "search_indexes_ensured"


@dataclass(slots=True)
class IndexMappingSpec:
    """OpenSearch-compatible mappings/settings fragment."""

    mappings: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IndexResult:
    index: str
    created: bool
    acknowledged: bool = True


@dataclass(slots=True)
class IndexDocumentResult:
    doc_id: str
    result: str  # created | updated | noop | deleted
    version: int | None = None


@dataclass(slots=True)
class BulkIndexItem:
    doc_id: str
    document: dict[str, Any]
    result: str = "indexed"
    error: str | None = None


@dataclass(slots=True)
class BulkIndexResult:
    items: list[BulkIndexItem] = field(default_factory=list)
    indexed: int = 0
    errors: int = 0


@dataclass(slots=True)
class SearchHit:
    doc_id: str
    score: float | None
    source: dict[str, Any]
    # sort values of the hit (for search_after cursor paging)
    sort: list[Any] | None = None


@dataclass(slots=True)
class SearchResult:
    hits: list[SearchHit] = field(default_factory=list)
    total: int = 0
    took_ms: int | None = None


def physical_index(namespace: str, index: str) -> str:
    return f"{namespace}__{index}"


def validate_namespace(namespace: str) -> str:
    ns = (namespace or "").strip()
    if not NAMESPACE_RE.match(ns):
        raise ValueError(f"invalid search_index namespace: {namespace!r}")
    return ns


def validate_index(index: str) -> str:
    idx = (index or "").strip()
    if not INDEX_RE.match(idx):
        raise ValueError(f"invalid search_index index: {index!r}")
    return idx


def validate_doc_id(doc_id: str) -> str:
    did = (doc_id or "").strip()
    if not DOC_ID_RE.match(did):
        raise ValueError(f"invalid search_index doc_id: {doc_id!r}")
    return did


def count_mapping_fields(mappings: dict[str, Any]) -> int:
    props = mappings.get("properties") if isinstance(mappings, dict) else None
    if isinstance(props, dict):
        return len(props)
    return 0

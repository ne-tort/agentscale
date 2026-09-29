"""Documents BC domain types — formats, MIME map, metric names (DOCUM)."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

# Input formats the module accepts (extension without dot, lowercase).
SUPPORTED_SOURCE_FORMATS = frozenset(
    {
        "doc", "docx", "odt", "rtf",
        "xls", "xlsx", "ods", "csv",
        "pdf", "txt", "md", "html",
    }
)

# Formats `documents.convert` can produce. Office→office routes other than
# pdf run through the local in-proc adapter; office→pdf runs Gotenberg.
CONVERTIBLE_TARGETS = frozenset(
    {"pdf", "docx", "odt", "rtf", "txt", "xlsx", "ods", "csv", "html"}
)

METRIC_DOCUMENTS_CONVERSIONS = "documents_conversions"
METRIC_DOCUMENTS_CREATIONS = "documents_creations"
METRIC_DOCUMENTS_TEMPLATE_FILLS = "documents_template_fills"
METRIC_DOCUMENTS_READS = "documents_reads"

FORMAT_MIME: dict[str, str] = {
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "odt": "application/vnd.oasis.opendocument.text",
    "rtf": "application/rtf",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "csv": "text/csv",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
}

# Default filename stem for `documents.create` when the spec carries none.
DEFAULT_FILENAME_STEM = "document"


def _normalize_format(fmt: str) -> str:
    raw = (fmt or "").strip().lower().lstrip(".")
    return raw


def mime_for_format(fmt: str) -> str:
    """MIME type for a supported format; falls back to octet-stream."""
    return FORMAT_MIME.get(_normalize_format(fmt), "application/octet-stream")


def detect_format(filename: str) -> str:
    """Source format from the filename extension (no dot, lowercase)."""
    name = (filename or "").strip()
    # tolerate both POSIX and Windows separators / query-ish suffixes
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = PurePosixPath(base).suffix or PureWindowsPath(base).suffix
    return _normalize_format(suffix)


def validate_source_format(fmt: str) -> str:
    """Validate a source format; ValueError when unsupported."""
    fmt = _normalize_format(fmt)
    if fmt not in SUPPORTED_SOURCE_FORMATS:
        raise ValueError(f"unsupported source format: {fmt!r}")
    return fmt


def validate_target_format(fmt: str) -> str:
    """Validate a conversion target; ValueError when not convertible."""
    fmt = _normalize_format(fmt)
    if fmt not in CONVERTIBLE_TARGETS:
        raise ValueError(f"unsupported target format: {fmt!r}")
    return fmt


def replace_extension(filename: str, target_format: str) -> str:
    """Swap the filename extension for ``target_format`` (keeps the stem)."""
    name = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    stem = PurePosixPath(name).stem or DEFAULT_FILENAME_STEM
    target = _normalize_format(target_format)
    return f"{stem}.{target}"


def file_ref(source: Any) -> dict[str, Any]:
    """Coerce a caller-supplied FileRef into a plain dict (or raise TypeError)."""
    if isinstance(source, dict):
        return dict(source)
    raise TypeError(f"file_ref must be a dict, got {type(source).__name__}")

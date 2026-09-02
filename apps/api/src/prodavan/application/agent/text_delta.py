"""Normalize cumulative SDK text deltas into incremental append chunks."""

from __future__ import annotations


def _overlap_append(previous: str, chunk: str) -> tuple[str, str]:
    """Append chunk to previous, merging suffix/prefix overlap when present."""
    max_k = min(len(previous), len(chunk))
    for k in range(max_k, 1, -1):
        if previous[-k:] == chunk[:k]:
            incremental = chunk[k:]
            return incremental, previous + incremental
    return chunk, previous + chunk


def normalize_text_delta(previous: str, chunk: str) -> tuple[str, str]:
    """Return ``(incremental, cumulative)`` for assistant/thinking stream chunks."""
    if not chunk:
        return "", previous
    if chunk.startswith(previous):
        return chunk[len(previous) :], chunk
    if previous.startswith(chunk):
        return "", previous
    return _overlap_append(previous, chunk)

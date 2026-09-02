"""Normalize cumulative SDK text deltas into incremental append chunks."""

from __future__ import annotations


def normalize_text_delta(previous: str, chunk: str) -> tuple[str, str]:
    """Return ``(incremental, cumulative)`` for assistant/thinking stream chunks."""
    if not chunk:
        return "", previous
    if chunk.startswith(previous):
        return chunk[len(previous) :], chunk
    if previous.startswith(chunk):
        return "", previous
    return chunk, previous + chunk

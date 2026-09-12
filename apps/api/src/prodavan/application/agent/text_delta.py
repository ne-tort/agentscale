"""Normalize stream text deltas into incremental append chunks.

Wire contract after this helper: ``incremental`` is always safe to append.

Handles two producer styles:

1. **Cumulative** (Cursor-style): each chunk is the full text so far → emit suffix.
2. **Incremental** (OpenClaw / Ollama / OpenAI): each chunk is new tokens → append as-is.

Deliberately does **not** merge arbitrary suffix/prefix overlaps: that heuristic
swallows syllables on incremental streams (``"конф"+"конфигурацию"`` → ``"игурацию"``).
"""

from __future__ import annotations


def normalize_text_delta(previous: str, chunk: str) -> tuple[str, str]:
    """Return ``(incremental, cumulative)`` for assistant/thinking stream chunks."""
    if not chunk:
        return "", previous
    # Cumulative: chunk is previous + new suffix (or exact replay).
    if chunk.startswith(previous):
        return chunk[len(previous) :], chunk
    # Regression / shorter replay of the same cumulative buffer.
    if previous.startswith(chunk):
        return "", previous
    # Incremental token(s): append verbatim.
    return chunk, previous + chunk

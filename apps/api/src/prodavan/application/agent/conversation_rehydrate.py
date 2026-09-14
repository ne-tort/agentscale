"""Re-inject Postgres chat history into pod runtime after workspace hydrate.

UI transcript SoT is Postgres. Pod local conversation state (OpenClaw / Cursor
checkpoint under emptyDir) is wiped when sync bumps ``hydrate_generation`` and
recreates the pod. Bridge send only forwards the new user message, so without
this prefix the model starts cold while the Flutter UI still shows the old chat.
"""

from __future__ import annotations

from typing import Any

HYDRATE_GEN_KEY = "prodavan_hydrate_generation"

_MAX_CHARS = 14_000
_MAX_TURNS = 24
_MAX_TURN_CHARS = 1_800


def needs_conversation_rehydrate(
    adapter_state: dict[str, Any] | None,
    *,
    current_generation: int | None,
) -> bool:
    if current_generation is None:
        return False
    if not isinstance(adapter_state, dict):
        return True
    last = adapter_state.get(HYDRATE_GEN_KEY)
    if last is None:
        return True
    try:
        return int(last) != int(current_generation)
    except (TypeError, ValueError):
        return True


def stamp_hydrate_generation(
    adapter_state: dict[str, Any] | None,
    generation: int,
) -> dict[str, Any]:
    out = dict(adapter_state) if isinstance(adapter_state, dict) else {}
    out[HYDRATE_GEN_KEY] = int(generation)
    return out


def format_rehydrate_bridge_message(
    *,
    history: list[dict[str, Any]],
    new_message: str,
    max_chars: int = _MAX_CHARS,
) -> str | None:
    """Return bridge-only prompt with prior user/assistant turns, or None if empty."""
    turns: list[str] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if len(text) > _MAX_TURN_CHARS:
            text = text[: _MAX_TURN_CHARS - 1] + "…"
        turns.append(f"{role.upper()}: {text}")
    if not turns:
        return None

    turns = turns[-_MAX_TURNS:]
    header = (
        "[Platform context] Workspace was updated and the pod runtime restarted. "
        "The UI chat history below is authoritative — continue the same dialog. "
        "Do not claim there is no prior conversation."
    )
    body = "\n\n".join(turns)
    suffix = f"USER (new message):\n{new_message}"
    assembled = f"{header}\n\n{body}\n\n{suffix}"
    if len(assembled) <= max_chars:
        return assembled
    # Keep header + newest turns + new message.
    budget = max_chars - len(header) - len(suffix) - 8
    clipped = body[-budget:] if budget > 0 else ""
    return f"{header}\n\n…\n{clipped}\n\n{suffix}"

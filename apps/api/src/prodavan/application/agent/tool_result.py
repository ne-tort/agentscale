"""Разбор tool_result-события: текст результата и признак ошибки.

Флаг ошибки живёт в разных местах в зависимости от адаптера рантайма:

- codex / cursor / claude-sdk нормалайзеры кладут его в корень данных —
  ``data["is_error"]`` (см. ``openclaw-sdk/packages/events/src/normalizers``);
- openclaw-путь (``adapter: platform_openclaw``, он же боевой на dev/prod)
  отдаёт MCP-конверт как есть, и флаг лежит ВНУТРИ него —
  ``data["output"]["isError"]``.

Поэтому читать нужно оба места: проекция чата иначе не помечала упавшие вызовы,
а детектор потерянного bind'а в ``openclaw_bridge`` на боевом адаптере не
срабатывал вовсе.
"""

from __future__ import annotations

from typing import Any


def tool_result_text(output: Any) -> str:
    """Текст tool_result: MCP-конверт (``{content: [{text}]}``) или голая строка."""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        content = output.get("content")
        if isinstance(content, list):
            parts = [
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            ]
            return "\n".join(p for p in parts if p)
        return str(output.get("text") or "")
    return ""


def tool_result_is_error(data: dict[str, Any]) -> bool:
    """True, если tool_result помечен как ошибку (в любом из вариантов записи)."""
    if not isinstance(data, dict):
        return False
    for key in ("is_error", "isError"):
        if key in data:
            return bool(data.get(key))
    output = data.get("output")
    if isinstance(output, dict) and "isError" in output:
        return bool(output.get("isError"))
    return False

"""Канонические path/query и подписываемое представление webhook v2.

Raw query нигде не разбирается и не пересобирается: порядок, дубликаты, ``+`` и
percent-encoding остаются именно такими, как их прислал клиент. Это одновременно
сохраняет proxy-контракт и делает HMAC проверяемым без неоднозначной нормализации.
"""

from __future__ import annotations


def canonical_backend_path(path: str) -> str:
    """Вернуть backend path с одним leading slash, сохранив trailing slash.

    Query/fragment передаются отдельно. Dot-segments, backslash и управляющие
    символы запрещены, чтобы подписанный путь нельзя было интерпретировать иначе
    на downstream HTTP-сервере.
    """
    if not isinstance(path, str) or not path:
        raise ValueError("backend path must be a non-empty string")
    normalized = "/" + path.lstrip("/")
    if normalized.startswith("//"):
        raise ValueError("backend path must not start with '//'")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in normalized):
        raise ValueError("backend path contains control characters")
    if any(ch in normalized for ch in ("\\", "?", "#")):
        raise ValueError("backend path contains a forbidden character")
    if any(segment in {".", ".."} for segment in normalized.split("/")):
        raise ValueError("backend path contains a dot segment")
    return normalized


def canonical_raw_query(raw_query: bytes) -> bytes:
    """Проверить и вернуть исходные query bytes без какой-либо трансформации."""
    if not isinstance(raw_query, bytes):
        raise TypeError("raw query must be bytes")
    # HTTP request-target обязан кодировать не-ASCII/пробелы. Отклоняем их, а не
    # молча нормализуем: иначе HMAC и фактический upstream target разойдутся.
    if any(byte < 0x21 or byte > 0x7E or byte == ord("#") for byte in raw_query):
        raise ValueError("raw query must contain printable ASCII without spaces or '#'")
    return raw_query


def webhook_external_path(*, plugin_id: str, company_id: str, path: str) -> str:
    """Путь внешнего webhook URL, привязывающий подпись v2 к tenant scope."""
    backend_path = canonical_backend_path(path)
    for name, value in (("plugin_id", plugin_id), ("company_id", company_id)):
        if not isinstance(value, str) or not value or any(
            ch in value for ch in "/\\?#"
        ) or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError(f"{name} is not a safe path segment")
    return f"/webhook/{plugin_id}/{company_id}{backend_path}"


def webhook_v2_signing_bytes(
    *,
    timestamp: str,
    delivery_id: str,
    event: str,
    method: str,
    canonical_path: str,
    raw_query: bytes,
    raw_body: bytes,
) -> bytes:
    """Versioned canonical string + неизменённое raw body.

    Формат (все текстовые поля UTF-8, query — исходные ASCII bytes)::

        prodavan.webhook.v2\n
        <timestamp>\n<delivery>\n<event>\n<METHOD>\n
        <canonical-path>[?<raw-query>]\n<raw-body>

    Body идёт последним и не декодируется. Текстовые поля не могут содержать
    управляющие символы/newline, поэтому представление однозначно.
    """
    if not isinstance(raw_body, bytes):
        raise TypeError("raw body must be bytes")
    fields: list[bytes] = []
    for name, value in (
        ("timestamp", timestamp),
        ("delivery_id", delivery_id),
        ("event", event),
        ("method", method.upper()),
        ("canonical_path", canonical_path),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be non-empty")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError(f"{name} contains control characters")
        fields.append(value.encode("utf-8"))
    path_bytes = fields[-1]
    query = canonical_raw_query(raw_query)
    if query:
        path_bytes += b"?" + query
    return b"prodavan.webhook.v2\n" + b"\n".join((*fields[:-1], path_bytes, raw_body))


__all__ = [
    "canonical_backend_path",
    "canonical_raw_query",
    "webhook_external_path",
    "webhook_v2_signing_bytes",
]

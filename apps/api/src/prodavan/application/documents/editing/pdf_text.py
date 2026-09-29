"""Best-effort PDF plain-text extraction (stdlib zlib, DOCUM).

Read document PDFs are almost always Flate-compressed; this helper walks the
stream objects, inflates them and pulls literal strings out of ``Tj`` / ``TJ``
text operators. It is *best-effort*: no font CMap handling, no encrypted PDFs
— failures degrade to an empty string rather than an error (the read endpoint
still reports ``kind=pdf``).
"""

from __future__ import annotations

import re
import zlib

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_LITERAL_RE = re.compile(rb"\(((?:\\.|[^\\()])*)\)")
_TEXT_OP_RE = re.compile(rb"\((?:\\.|[^\\()])*\)\s*Tj|\[[^\]]*\]\s*TJ")
_HEX_RE = re.compile(rb"<([0-9A-Fa-f\s]+)>\s*Tj")

_ESCAPES = {
    b"n": b"\n",
    b"r": b"\r",
    b"t": b"\t",
    b"b": b"\b",
    b"f": b"\f",
    b"(": b"(",
    b")": b")",
    b"\\": b"\\",
}


def _unescape(raw: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i : i + 1]
        if ch == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1 : i + 2]
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt]
                i += 2
                continue
            if nxt.isdigit():
                # octal escape \ddd
                j = i + 1
                digits = b""
                while j < len(raw) and len(digits) < 3 and raw[j : j + 1].isdigit():
                    digits += raw[j : j + 1]
                    j += 1
                try:
                    out.append(int(digits, 8) & 0xFF)
                except ValueError:
                    pass
                i = j
                continue
            i += 2
            continue
        out += ch
        i += 1
    return bytes(out)


def _decode_pdf_bytes(raw: bytes) -> str:
    """PDFDocEncoding is close enough to latin-1 for extracted text."""
    try:
        text = raw.decode("latin-1")
    except Exception:
        return ""
    # Zero/oversized strings are spacing artifacts — keep whitespace sane.
    return text


def _extract_from_content(content: bytes) -> list[str]:
    if not content:
        return []
    parts: list[str] = []
    for match in _TEXT_OP_RE.finditer(content):
        chunk = match.group(0)
        literals = _LITERAL_RE.findall(chunk)
        if not literals:
            # TJ arrays may carry hex strings; approximate with hex Tj scan
            hexes = _HEX_RE.findall(chunk)
            for hx in hexes:
                try:
                    raw = bytes.fromhex(re.sub(rb"\s", b"", hx).decode("ascii"))
                    parts.append(_decode_pdf_bytes(raw))
                except Exception:
                    continue
            continue
        for lit in literals:
            parts.append(_decode_pdf_bytes(_unescape(lit)))
    return parts


def pdf_extract_text(data: bytes, *, max_streams: int = 400) -> str:
    """Extract visible text lines from an uncompressed/Flate PDF."""
    if not data:
        return ""
    if b"/Encrypt" in data[:4096]:
        return ""
    chunks: list[str] = []
    streams = _STREAM_RE.findall(data)
    for raw in streams[:max_streams]:
        content: bytes | None = None
        try:
            content = zlib.decompress(raw)
        except zlib.error:
            content = raw  # plain (uncompressed) stream
        if content is None:
            continue
        if b"BT" not in content and b"Tj" not in content:
            continue  # font/image/other object — skip
        chunks.extend(_extract_from_content(content))
    text = "\n".join(chunks)
    # collapse runs of blank space introduced by positioning operators
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

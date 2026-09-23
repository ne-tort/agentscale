"""Extract plain text from text-like attachment formats for chat delivery.

Supported (no third-party deps; pure stdlib zipfile + xml.etree):
- Plain text: .txt .md .json .yaml .yml .log .sql .py .js .ts .sh .go .rs
  .java .c .cpp .h .hpp .rb .php .css .xml .html .htm .toml .ini .conf .env
- Office: .docx (WordprocessingML), .odt (ODF text), .pptx (PresentationML)

For binary/unknown formats the caller should place the file in the container
rather than inline it. ``is_text_extractable_filename`` is the gate used by
attachment delivery to decide whether to attempt text extraction.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# Extensions we can reliably turn into prompt-inline text.
TEXT_EXTRACTABLE_EXTENSIONS = frozenset(
    {
        ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".log", ".sql",
        ".py", ".js", ".jsx", ".ts", ".tsx", ".sh", ".bash", ".go", ".rs",
        ".java", ".c", ".cpp", ".cc", ".h", ".hpp", ".rb", ".php", ".css",
        ".xml", ".html", ".htm", ".toml", ".ini", ".cfg", ".conf", ".env",
        ".csv", ".tsv",
    }
)

OFFICE_TEXT_EXTENSIONS = frozenset({".docx", ".odt", ".pptx"})

# Bytes at which we stop inlining as prompt text — the agent prompt is not a
# document store. Larger text is written to the workspace and referenced.
MAX_INLINE_TEXT_BYTES = 48 * 1024

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def is_text_extractable_filename(filename: str) -> bool:
    ext = Path(filename or "").suffix.lower()
    return ext in TEXT_EXTRACTABLE_EXTENSIONS or ext in OFFICE_TEXT_EXTENSIONS


def _decode_text_bytes(data: bytes) -> str:
    """Decode with common encodings, stripping BOM."""
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp866", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    """Crude HTML→text: drop tags, unescape common entities, collapse blanks."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    parts: list[str] = []
    for para in root.iter(f"{{{_W_NS}}}p"):
        chunks = [t.text or "" for t in para.iter(f"{{{_W_NS}}}t")]
        line = "".join(chunks).strip()
        if line:
            parts.append(line)
    return "\n\n".join(parts).strip()


def _odt_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("content.xml")
    root = ET.fromstring(xml)
    parts: list[str] = []
    for el in root.iter(f"{{{_TEXT_NS}}}p"):
        chunks = [node.text or "" for node in el.iter() if node.text]
        line = "".join(chunks).strip()
        if line:
            parts.append(line)
    return "\n\n".join(parts).strip()


def _pptx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        slide_names = sorted(
            n for n in zf.namelist()
            if n.startswith("ppt/slides/slide") and n.endswith(".xml")
        )
        parts: list[str] = []
        for name in slide_names:
            root = ET.fromstring(zf.read(name))
            chunks = [t.text or "" for t in root.iter(f"{{{_A_NS}}}t")]
            line = "\n".join(c for c in chunks if c.strip()).strip()
            if line:
                parts.append(line)
    return "\n\n---\n\n".join(parts).strip()


def extract_text(data: bytes, *, filename: str) -> str | None:
    """Return extracted plain text, or None if the format is not parseable.

    Never raises — a parse failure means the caller should place the raw file
    in the container instead of inlining it.
    """
    ext = Path(filename or "").suffix.lower()
    try:
        if ext in OFFICE_TEXT_EXTENSIONS:
            if ext == ".docx":
                return _docx_text(data)
            if ext == ".odt":
                return _odt_text(data)
            if ext == ".pptx":
                return _pptx_text(data)
        if ext in {".html", ".htm"}:
            return _strip_html(_decode_text_bytes(data))
        if ext in TEXT_EXTRACTABLE_EXTENSIONS:
            return _decode_text_bytes(data).strip()
    except Exception:
        return None
    return None
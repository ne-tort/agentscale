"""Unit tests — text extraction + attachment delivery compose."""

from __future__ import annotations

from prodavan.application.content.text_extract import (
    extract_text,
    is_text_extractable_filename,
)
from prodavan.application.projects.attachment_delivery import (
    DeliveredAttachment,
    compose_agent_message,
    compose_display_text,
)


def test_extract_plain_text() -> None:
    assert extract_text(b"hello world", filename="note.txt") == "hello world"


def test_extract_markdown_strips_bom() -> None:
    raw = b"\xef\xbb\xbf# Title\nbody"
    assert extract_text(raw, filename="readme.md") == "# Title\nbody"


def test_extract_html_drops_tags() -> None:
    raw = b"<html><body><h1>Title</h1><p>Hello &amp; bye</p></body></html>"
    text = extract_text(raw, filename="page.html")
    assert text is not None
    assert "Title" in text
    assert "Hello & bye" in text
    assert "<" not in text


def test_extract_docx_text() -> None:
    # Minimal .docx with one paragraph "Hello docx".
    import io
    import zipfile

    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document_xml = (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<w:document xmlns:w="{ns}"><w:body>'
        f'<w:p><w:r><w:t>Hello docx</w:t></w:r></w:p>'
        f'</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
    raw = buf.getvalue()
    assert extract_text(raw, filename="doc.docx") == "Hello docx"


def test_is_text_extractable() -> None:
    assert is_text_extractable_filename("a.txt")
    assert is_text_extractable_filename("a.docx")
    assert is_text_extractable_filename("a.odt")
    assert is_text_extractable_filename("a.html")
    assert not is_text_extractable_filename("a.exe")
    assert not is_text_extractable_filename("a.png")


def test_compose_inline_text_block() -> None:
    items = [
        DeliveredAttachment(
            filename="note.txt",
            storage_ref="object://x",
            kind="inline_text",
            workspace_path=None,
            row_count=None,
            records=None,
            text="hello body",
            note="парсинг в текст, встроено в сообщение",
        )
    ]
    agent = compose_agent_message(user_text="обработай", items=items)
    assert "Вложенные данные: note.txt" in agent
    assert "```\nhello body\n```" in agent
    assert "обработай" in agent
    assert agent.rstrip().endswith("Поступи с ним, согласно инструкциям.")


def test_compose_attachment_only_triggers_agent() -> None:
    items = [
        DeliveredAttachment(
            filename="report.pdf",
            storage_ref="object://y",
            kind="workspace_file",
            workspace_path="inbox/report.pdf",
            row_count=None,
            records=None,
            text=None,
            note="файл помещён в контейнер; путь /workspace/inbox/report.pdf",
        )
    ]
    agent = compose_agent_message(user_text="", items=items)
    assert "/workspace/inbox/report.pdf" in agent
    assert "Поступи с ним, согласно инструкциям." in agent
    display = compose_display_text(user_text="", items=items)
    assert display == "Вложение: report.pdf"
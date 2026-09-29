"""docxtpl Jinja-style DOCX template rendering (DOCUM)."""

from __future__ import annotations

import io
from typing import Any

from prodavan.domain.errors import AppError


def render_docx_template(template_bytes: bytes, context: dict[str, Any]) -> bytes:
    """Render ``{{ jinja }}`` placeholders in a DOCX template with ``context``.

    The template stays a valid docx after render — docxtpl patches the OOXML
    runs in place. Mapping values must be str/int/float/bool (docxtpl also
    supports richer objects; JSON-ish scalars are the supported subset here).
    """
    if not template_bytes:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="empty docx template",
        )
    if not isinstance(context, dict):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="template context must be a mapping",
        )
    try:
        from docxtpl import DocxTemplate

        tpl = DocxTemplate(io.BytesIO(template_bytes))
        tpl.render(dict(context))
        out = io.BytesIO()
        tpl.save(out)
        return out.getvalue()
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"docx template render failed: {exc}",
        ) from exc

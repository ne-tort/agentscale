"""Gotenberg HTTP adapter — LibreOffice office→PDF (DOCUM).

Gotenberg 8 `/forms/libreoffice/convert` always answers with a PDF (the
endpoint has no target-format parameter). The Documents module therefore
routes *only* `→ pdf` through this adapter; every other office→office
route runs in the local in-proc adapter (see ``local_converter.py``).
"""

from __future__ import annotations

import logging

import httpx

from prodavan.application.documents.ports.converter import ConvertedDocument
from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)

_GOTENBERG_PDF_PATH = "/forms/libreoffice/convert"
_GOTENBERG_HEALTH_PATH = "/health"


class GotenbergConverter:
    """Thin REST client for the Gotenberg LibreOffice route (office→pdf only)."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = (base_url or "").strip().rstrip("/")
        self._timeout = float(timeout_sec) if timeout_sec and timeout_sec > 0 else 120.0
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=10.0),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def ping(self) -> bool:
        if not self._base:
            return False
        try:
            resp = await self._client.get(f"{self._base}{_GOTENBERG_HEALTH_PATH}")
            return resp.status_code < 500
        except Exception:
            return False

    async def convert(self, data: bytes, filename: str, target_format: str) -> ConvertedDocument:
        target = (target_format or "").strip().lower()
        if target != "pdf":
            # Gotenberg cannot produce non-pdf output — see module docstring.
            raise AppError(
                code="CONVERSION_UNSUPPORTED",
                title="Conversion Unsupported",
                status=422,
                detail=f"gotenberg converter only targets pdf, got {target!r}",
            )
        if not self._base:
            raise AppError(
                code="CONVERSION_UNAVAILABLE",
                title="Conversion Unavailable",
                status=422,
                detail="gotenberg converter is not configured",
            )
        if not data:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="empty document payload",
            )
        # The multipart filename must keep the source extension: Gotenberg hands
        # the file to LibreOffice which picks the import filter from it.
        files = {"files": (filename or "document", data)}
        try:
            resp = await self._client.post(f"{self._base}{_GOTENBERG_PDF_PATH}", files=files)
        except httpx.TimeoutException as exc:
            raise AppError(
                code="CONVERSION_TIMEOUT",
                title="Conversion Timeout",
                status=504,
                detail="gotenberg did not convert the document in time",
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                code="CONVERSION_UNAVAILABLE",
                title="Conversion Unavailable",
                status=502,
                detail=f"cannot reach gotenberg: {exc.__class__.__name__}",
            ) from exc
        if resp.status_code >= 400:
            logger.warning(
                "gotenberg convert failed status=%s bytes_in=%s", resp.status_code, len(data)
            )
            raise AppError(
                code="CONVERSION_FAILED",
                title="Conversion Failed",
                status=502,
                detail=f"gotenberg returned HTTP {resp.status_code}",
            )
        out = resp.content or b""
        if not out:
            raise AppError(
                code="CONVERSION_FAILED",
                title="Conversion Failed",
                status=502,
                detail="gotenberg returned an empty document",
            )
        # Gotenberg names the result after the source with a .pdf extension;
        # the service layer re-derives the canonical output filename anyway.
        out_name = resp.headers.get("Content-Disposition", "")
        if "filename=" in out_name:
            out_name = out_name.split("filename=", 1)[1].strip().strip('"')
        return ConvertedDocument(data=out, mime="application/pdf", filename=out_name or filename)

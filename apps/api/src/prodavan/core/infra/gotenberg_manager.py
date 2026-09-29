"""GotenbergManager — httpx client + LifespanResource (Documents converter)."""

from __future__ import annotations

import logging

from prodavan.application.documents.adapters.gotenberg_converter import GotenbergConverter
from prodavan.application.documents.ports.converter import DocumentConverterPort
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

_manager: GotenbergManager | None = None


def get_gotenberg_manager() -> GotenbergManager | None:
    return _manager


def set_gotenberg_manager(manager: GotenbergManager | None) -> None:
    global _manager
    _manager = manager


class GotenbergManager(LifespanResource):
    """Run the Gotenberg (LibreOffice) HTTP converter when configured.

    Empty ``url`` disables the manager — the application starts normally and
    DocumentsService falls back to the local in-proc converter. A failed ping
    only aborts startup when ``required=True``.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        timeout_sec: float = 120.0,
        required: bool = False,
    ) -> None:
        self._url = (url or "").strip() or None
        self._timeout = float(timeout_sec) if timeout_sec and timeout_sec > 0 else 120.0
        self._required = required
        self._enabled_flag = self._url is not None
        self._converter: GotenbergConverter | None = None

    @property
    def name(self) -> str:
        return "gotenberg"

    @property
    def enabled(self) -> bool:
        return self._enabled_flag and self._converter is not None

    @property
    def url(self) -> str | None:
        return self._url

    @property
    def converter(self) -> DocumentConverterPort | None:
        return self._converter

    async def startup(self) -> None:
        set_gotenberg_manager(self)
        if not self._enabled_flag:
            logger.info("gotenberg: disabled (GOTENBERG_URL empty) — local converter fallback")
            return
        self._converter = GotenbergConverter(
            base_url=self._url or "",
            timeout_sec=self._timeout,
        )
        try:
            ok = await self._converter.ping()
            if not ok:
                raise RuntimeError("gotenberg ping returned false")
            logger.info("gotenberg: connected url=%s", self._url)
        except Exception:
            logger.exception("gotenberg: ping failed on startup")
            await self._close_converter()
            if self._required:
                if get_gotenberg_manager() is self:
                    set_gotenberg_manager(None)
                raise
            logger.warning("gotenberg: disabled after failed ping — local converter fallback")

    async def shutdown(self) -> None:
        await self._close_converter()
        if get_gotenberg_manager() is self:
            set_gotenberg_manager(None)

    async def _close_converter(self) -> None:
        if self._converter is not None:
            try:
                await self._converter.aclose()
            except Exception:
                logger.exception("gotenberg: close failed")
            self._converter = None

    async def health(self) -> bool | None:
        if not self._enabled_flag or self._converter is None:
            return None
        try:
            return await self._converter.ping()
        except Exception:
            return False

    async def ping(self) -> bool:
        result = await self.health()
        return bool(result)

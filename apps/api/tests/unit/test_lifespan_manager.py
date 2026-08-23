"""Unit tests — P0 LifespanManager register / order."""

from __future__ import annotations

import pytest

from prodavan.core.lifespan.manager import LifespanManager
from prodavan.core.lifespan.resource import LifespanResource


class _Probe(LifespanResource):
    def __init__(self, name: str, log: list[str]) -> None:
        self._name = name
        self._log = log
        self.healthy = True

    @property
    def name(self) -> str:
        return self._name

    async def startup(self) -> None:
        self._log.append(f"up:{self._name}")

    async def shutdown(self) -> None:
        self._log.append(f"down:{self._name}")

    async def health(self) -> bool | None:
        return self.healthy


@pytest.mark.asyncio
async def test_lifespan_startup_shutdown_order() -> None:
    log: list[str] = []
    mgr = LifespanManager()
    mgr.register(_Probe("a", log)).register(_Probe("b", log))

    async with mgr.lifespan(None):  # type: ignore[arg-type]
        assert log == ["up:a", "up:b"]
        report = await mgr.health_report()
        assert report == {"a": True, "b": True}

    assert log == ["up:a", "up:b", "down:b", "down:a"]


def test_duplicate_register_rejected() -> None:
    mgr = LifespanManager()
    log: list[str] = []
    mgr.register(_Probe("a", log))
    with pytest.raises(ValueError, match="already registered"):
        mgr.register(_Probe("a", log))

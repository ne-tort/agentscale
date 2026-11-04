"""Lifespan wiring for the canonical web chat SSE runtime.

The SSE route (``api/v1/web_stream.py``) reads its composition from
``app.state`` and refuses with ``503 web_chat_stream_facade_unwired`` while that
state is absent. This resource is the only place in the real process that
installs it:

* flag ``WEB_STREAM_RUNTIME_ENABLED`` (default **off**): with the flag off no
  runtime is installed, so the route keeps the exact pre-wiring refusal;
* ``startup`` builds :class:`WebStreamRuntime` from real dependencies
  (:mod:`prodavan.application.web_facade_adapter.runtime_factory`) and calls
  ``install_web_stream_runtime(app, runtime)``;
* ``shutdown`` removes the installed runtime again, so a stopped app holds no
  reference to the database-backed resolver, run index or facade.

The resource never raises on startup: an API that cannot serve one route must
still boot (and answer 503 on that route), not crash-loop. What it does keep is
a *named* blocker — one per genuinely missing part — instead of degrading the
whole runtime to a stub.

``LifespanResource.startup()`` takes no arguments and therefore cannot see the
ASGI app, while ``LifespanManager.lifespan(app)`` is the only place the app is
visible. :class:`WebStreamLifespanManager` closes that gap by handing the app to
every registered resource that exposes ``bind_app``, before startup runs. A
plain ``LifespanManager`` still boots the process; the route then stays
fail-closed with ``web_stream_app_handle_unbound`` recorded here.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from prodavan.application.web_facade_adapter.runtime_factory import (
    build_web_stream_runtime,
    runtime_blockers,
)
from prodavan.core.lifespan.manager import LifespanManager
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

RESOURCE_NAME = "web_stream_runtime"

#: Mirrors ``prodavan.api.v1.web_stream.RUNTIME_STATE_ATTR``; the module is
#: imported lazily so ``core`` does not depend on the API layer at import time.
RUNTIME_STATE_ATTR = "web_stream_runtime"

#: Named startup blockers (also used as the resource's health reason).
BLOCKER_APP_UNBOUND = "web_stream_app_handle_unbound"
BLOCKER_COMPOSITION_FAILED = "web_stream_runtime_composition_failed"


def _web_stream_module() -> Any:
    from prodavan.api.v1 import web_stream

    return web_stream


def web_stream_runtime_resource_from_settings() -> WebStreamResource:
    """Resource built from the process settings (registered in ``core/wiring``)."""
    from prodavan.config.settings import settings

    return WebStreamResource(enabled=bool(settings.web_stream_runtime_enabled))


class WebStreamResource(LifespanResource):
    """Build the transport runtime once and install it on the app.

    ``runtime_factory`` is injectable so a test can prove the install/shutdown
    contract without a database; production passes nothing and gets the real
    composition.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        runtime_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._runtime_factory = runtime_factory
        self._app: Any = None
        self._runtime: Any = None
        self._installed = False
        self._blockers: tuple[str, ...] = ()
        self._startup_blocker: str | None = None

    # -- LifespanResource --------------------------------------------------

    @property
    def name(self) -> str:
        return RESOURCE_NAME

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def blockers(self) -> tuple[str, ...]:
        """Named blockers for parts the composed runtime cannot serve."""
        return self._blockers

    @property
    def startup_blocker(self) -> str | None:
        """Why the runtime is not installed at all, when it is not."""
        return self._startup_blocker

    @property
    def runtime(self) -> Any:
        """Installed runtime, or ``None``; cleared on shutdown."""
        return self._runtime

    def bind_app(self, app: Any) -> None:
        """Receive the ASGI app from :class:`WebStreamLifespanManager`."""
        if app is self._app:
            return
        if self._installed:
            # A different app object must never inherit this runtime's state.
            self._uninstall()
        self._app = app

    async def startup(self) -> None:
        if not self._enabled:
            # Never leave a runtime installed while the flag is off: the route
            # must answer the same 503 web_chat_stream_facade_unwired as before.
            self._uninstall()
            logger.info(
                "web stream runtime: disabled (WEB_STREAM_RUNTIME_ENABLED=false); "
                "route stays fail-closed web_chat_stream_facade_unwired"
            )
            return

        self._uninstall()
        if self._app is None:
            self._startup_blocker = BLOCKER_APP_UNBOUND
            logger.warning(
                "web stream runtime: enabled but no app handle was bound; "
                "route stays fail-closed (%s)",
                BLOCKER_APP_UNBOUND,
            )
            return

        try:
            runtime = (
                self._runtime_factory() if self._runtime_factory is not None else build_web_stream_runtime()
            )
            _web_stream_module().install_web_stream_runtime(self._app, runtime)
        except Exception:  # noqa: BLE001 - startup must not crash-loop the API
            self._startup_blocker = BLOCKER_COMPOSITION_FAILED
            self._runtime = None
            self._blockers = ()
            self._installed = False
            logger.exception(
                "web stream runtime: composition failed; route stays fail-closed (%s)",
                BLOCKER_COMPOSITION_FAILED,
            )
            return

        self._runtime = runtime
        self._blockers = tuple(runtime_blockers(runtime))
        self._startup_blocker = None
        self._installed = True
        if self._blockers:
            # Installed on purpose: a partially composed runtime still refuses
            # per part with a named blocker, which is strictly more useful than
            # the generic "not wired" refusal of the whole route.
            logger.warning(
                "web stream runtime: installed with blockers %s",
                ",".join(self._blockers),
            )
        else:
            logger.info("web stream runtime: installed (sessions, run index, chat stream)")

    async def shutdown(self) -> None:
        self._uninstall()
        self._runtime = None
        self._app = None
        self._blockers = ()
        self._startup_blocker = None

    async def health(self) -> bool | None:
        if not self._enabled:
            return None
        return bool(self._installed and self._runtime is not None)

    # -- internals ---------------------------------------------------------

    def _uninstall(self) -> None:
        """Drop the installed state, if this resource installed it.

        The attribute is removed rather than set to ``None`` so the route takes
        the exact same ``_require_runtime`` branch it takes with no runtime at
        all, and so ``get_web_stream_runtime`` keeps returning ``None``.
        """
        app, runtime, installed = self._app, self._runtime, self._installed
        self._installed = False
        if not installed or app is None or runtime is None:
            return
        state = getattr(app, "state", None)
        if state is None:
            return
        if getattr(state, RUNTIME_STATE_ATTR, None) is not runtime:
            # Somebody else owns that state now; never delete their runtime.
            return
        try:
            delattr(state, RUNTIME_STATE_ATTR)
        except AttributeError:  # pragma: no cover - concurrent uninstall
            logger.debug("web stream runtime: state already cleared")


class WebStreamLifespanManager(LifespanManager):
    """``LifespanManager`` that hands the ASGI app to app-bound resources.

    Startup/shutdown order, error handling and health reporting stay exactly the
    base class's; only the app reference is forwarded, because ``startup()`` has
    no other way to reach ``app.state``.
    """

    @asynccontextmanager
    async def lifespan(self, app: Any) -> AsyncIterator[None]:
        self.bind_app(app)
        async with super().lifespan(app):
            yield

    def bind_app(self, app: Any) -> None:
        for resource in self.resources:
            binder = getattr(resource, "bind_app", None)
            if callable(binder):
                binder(app)


__all__ = [
    "BLOCKER_APP_UNBOUND",
    "BLOCKER_COMPOSITION_FAILED",
    "RESOURCE_NAME",
    "RUNTIME_STATE_ATTR",
    "WebStreamLifespanManager",
    "WebStreamResource",
    "web_stream_runtime_resource_from_settings",
]

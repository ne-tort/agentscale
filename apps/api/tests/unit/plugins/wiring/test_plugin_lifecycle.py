"""Plugin lifecycle through the real custom LifespanManager (no Starlette events).

F1/F2 regression coverage: every plugin startup/shutdown is registered through
LifespanManager.register, which runs on the pinned Starlette (>=1.6, where
add_event_handler/lifespan_context no longer exist) behind a custom
FastAPI(lifespan=...). No fake add_event_handler anywhere.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prodavan.application.plugins.wiring.install import (
    CAPABILITY_LIFECYCLE_STATE_KEY,
    DIGEST_PAYLOAD_STORE_STATE_KEY,
    RUNTIME_SHUTDOWN_STATE_KEY,
    PluginCompositionError,
    _Composer,
    _TenantDbAdminLifecycle,
    _register_capability_shutdown,
    _register_digest_payload_shutdown,
    _register_minio_sts_lifecycle,
    _register_runtime_shutdown,
    _register_tenant_db_admin_lifecycle,
)
from prodavan.core.lifespan.manager import LifespanManager


class _Closable:
    def __init__(self, log: list[str], tag: str) -> None:
        self._log = log
        self._tag = tag
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        self._log.append("close:" + self._tag)


class _Admin:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.open_calls = 0
        self.close_calls = 0

    async def open(self) -> None:
        self.open_calls += 1
        self._log.append("open:tenant")

    async def close(self) -> None:
        self.close_calls += 1
        self._log.append("close:tenant")


class _Sts:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.invalidated = 0

    async def probe(self, *, force: bool) -> None:
        self._log.append("probe:sts")

    def invalidate(self) -> None:
        self.invalidated += 1
        self._log.append("invalidate:sts")


def _app_with_manager(manager: LifespanManager) -> Any:
    return SimpleNamespace(state=SimpleNamespace(lifespan_manager=manager))


async def test_shutdown_order_is_reverse_registration_through_manager() -> None:
    manager = LifespanManager()
    log: list[str] = []
    app = _app_with_manager(manager)
    composer = _Composer(app)
    store = _Closable(log, "payload")
    runtime = _Closable(log, "runtime")
    platform = _Closable(log, "platform")
    admin = _Admin(log)
    sts = _Sts(log)

    setattr(app.state, DIGEST_PAYLOAD_STORE_STATE_KEY, store)
    _register_digest_payload_shutdown(app, store, composer)
    _register_runtime_shutdown(app, runtime, composer)
    _register_tenant_db_admin_lifecycle(app, _TenantDbAdminLifecycle(admin), composer)
    _register_minio_sts_lifecycle(app, sts, composer)
    _register_capability_shutdown(app, platform, composer)

    await manager.startup_all()
    # Registration performs no IO; startup runs in registration order.
    assert log == ["open:tenant", "probe:sts"]
    assert getattr(app.state, CAPABILITY_LIFECYCLE_STATE_KEY) is True

    await manager.shutdown_all()
    # Reverse registration order, incl. runtime BEFORE its payload store.
    assert log == [
        "open:tenant",
        "probe:sts",
        "close:platform",
        "invalidate:sts",
        "close:tenant",
        "close:runtime",
        "close:payload",
    ]
    assert store.close_calls == 1
    assert runtime.close_calls == 1
    assert platform.close_calls == 1
    assert admin.close_calls == 1


async def test_repeated_registration_keeps_singleton_identity() -> None:
    manager = LifespanManager()
    app = _app_with_manager(manager)
    composer = _Composer(app)
    first = _Closable([], "first")
    other = _Closable([], "other")
    _register_runtime_shutdown(app, first, composer)
    _register_runtime_shutdown(app, first, composer)
    _register_runtime_shutdown(app, first, composer, overrides={"lifespan_manager": manager})
    assert len(manager.resources) == 1
    assert manager.get("plugin-runtime").owner is first

    # Same app, different runtime: fail-closed on the state-flag contract.
    with pytest.raises(PluginCompositionError) as caught:
        _register_runtime_shutdown(app, other, composer)
    assert caught.value.step == "runtime_lifecycle"
    assert len(manager.resources) == 1

    # Fresh app sharing one manager: the manager itself guards singleton identity.
    other_app = _app_with_manager(manager)
    other_composer = _Composer(other_app)
    with pytest.raises(PluginCompositionError) as conflict:
        _register_runtime_shutdown(other_app, other, other_composer)
    assert conflict.value.step == "runtime_lifecycle"
    assert "already registered" in str(conflict.value)
    assert len(manager.resources) == 1


async def test_missing_manager_fails_closed_without_starlette_router() -> None:
    # No app.router and no add_event_handler exist on the pinned framework.
    app = SimpleNamespace(state=SimpleNamespace())
    composer = _Composer(app)
    store = _Closable([], "payload")
    setattr(app.state, DIGEST_PAYLOAD_STORE_STATE_KEY, store)
    checks = (
        ("runtime_lifecycle", lambda: _register_runtime_shutdown(app, _Closable([], "r"), composer)),
        ("payload_lifecycle", lambda: _register_digest_payload_shutdown(app, store, composer)),
        ("capability_lifecycle", lambda: _register_capability_shutdown(app, _Closable([], "p"), composer)),
        (
            "tenant_db_admin_lifecycle",
            lambda: _register_tenant_db_admin_lifecycle(app, _TenantDbAdminLifecycle(_Admin([])), composer),
        ),
        ("minio_sts_probe", lambda: _register_minio_sts_lifecycle(app, _Sts([]), composer)),
    )
    for step, register in checks:
        with pytest.raises(PluginCompositionError) as caught:
            register()
        assert caught.value.step == step
        assert "LifespanManager" in str(caught.value)
    assert not hasattr(app.state, CAPABILITY_LIFECYCLE_STATE_KEY)


async def test_rollback_unregisters_resources_and_restores_state() -> None:
    manager = LifespanManager()
    app = _app_with_manager(manager)
    composer = _Composer(app)
    runtime = _Closable([], "runtime")
    _register_runtime_shutdown(app, runtime, composer)
    assert manager.get("plugin-runtime") is not None
    assert getattr(app.state, RUNTIME_SHUTDOWN_STATE_KEY) is True

    composer.rollback()
    assert manager.get("plugin-runtime") is None
    assert not hasattr(app.state, RUNTIME_SHUTDOWN_STATE_KEY)
    assert not hasattr(app.state, "plugin_runtime_ports")


async def test_overrides_manager_discovered_without_app_state_manager() -> None:
    # Standalone gateway shape: no app.state.lifespan_manager by contract.
    manager = LifespanManager()
    app = SimpleNamespace(state=SimpleNamespace())
    composer = _Composer(app)
    runtime = _Closable([], "runtime")
    _register_runtime_shutdown(app, runtime, composer, overrides={"lifespan_manager": manager})
    assert manager.get("plugin-runtime") is not None
    assert getattr(app.state, RUNTIME_SHUTDOWN_STATE_KEY) is True


def test_fastapi_custom_lifespan_runs_plugin_resources_on_pinned_starlette() -> None:
    manager = LifespanManager()
    log: list[str] = []
    app = FastAPI(lifespan=manager.as_fastapi_lifespan())
    # Pinned Starlette removed the legacy event API (FastAPI's APIRouter keeps
    # a deprecated shim that never runs on a custom lifespan — unused here).
    from starlette.applications import Starlette
    from starlette.routing import Router

    assert not hasattr(Starlette, "add_event_handler")
    assert not hasattr(Router, "add_event_handler")
    assert not hasattr(Router, "lifespan_context")
    # main.py composition root publishes the same per-app manager on app.state.
    app.state.lifespan_manager = manager

    store = _Closable(log, "payload")
    runtime = _Closable(log, "runtime")
    composer = _Composer(app)
    setattr(app.state, DIGEST_PAYLOAD_STORE_STATE_KEY, store)
    _register_digest_payload_shutdown(app, store, composer)
    _register_runtime_shutdown(app, runtime, composer)

    with TestClient(app):
        # Shutdown-only resources do no startup IO through the custom lifespan.
        assert log == []
        assert getattr(app.state, RUNTIME_SHUTDOWN_STATE_KEY) is True
    assert log == ["close:runtime", "close:payload"]
    assert runtime.close_calls == 1
    assert store.close_calls == 1

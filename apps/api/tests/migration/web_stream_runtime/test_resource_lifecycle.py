"""MIG-WEB-RUNTIME-01 acceptance: lifespan install/shutdown and route behaviour.

Three states are covered end to end through the real route, the real lifespan
manager and the real ``install_web_stream_runtime`` hook:

* flag off            → nothing installed, route answers the original
                        ``503 web_chat_stream_facade_unwired``;
* flag on, deps ready → runtime installed, ``get_web_stream_runtime`` returns
                        the object and the route streams wire frames;
* deps partly ready   → the refusal names the one missing part;
* shutdown            → the installed state is gone, nothing is left behind.

Authored under the migration verification freeze; deliberately not executed.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from prodavan.api.v1 import web_stream
from prodavan.application.web_facade_adapter.runtime_factory import (
    BLOCKER_CHAT_STREAM,
    BLOCKER_RUN_INDEX,
    BLOCKER_RUN_INDEX_EVIDENCE,
    BLOCKER_SESSIONS,
    CODE_RUN_INDEX_EVIDENCE,
    CODE_SESSION_RESOLVER,
    DurableRunIndexProvider,
    UnavailableChatSessionResolver,
)
from prodavan.application.web_facade_adapter.streams import (
    UnavailableChatStreamFacade,
    UnavailableRunIndexProvider,
)
from prodavan.core.infra.web_stream_resource import (
    BLOCKER_APP_UNBOUND,
    BLOCKER_COMPOSITION_FAILED,
    RESOURCE_NAME,
    WebStreamLifespanManager,
    WebStreamResource,
)

from .acceptance_fixtures import (
    PROJECT,
    RUN,
    SESSION,
    STREAM_BODY,
    STREAM_URL,
    FakeAsyncSession,
    FakeChatStreamFacade,
    FakeIndexProvider,
    FakeRunIndex,
    FakeSessionFactory,
    build_app,
    composed_runtime,
    manager_with,
    request_for,
    resource,
    runtime_state,
)


async def _stream(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(STREAM_URL, json=STREAM_BODY)


# ---------------------------------------------------------------------------
# Flag off — the pre-wiring behaviour must survive unchanged
# ---------------------------------------------------------------------------


async def test_disabled_flag_installs_nothing_and_route_stays_unwired() -> None:
    app = build_app()
    manager = manager_with(resource(enabled=False))

    async with manager.lifespan(app):
        assert runtime_state(app) is None
        assert web_stream.get_web_stream_runtime(request_for(app)) is None
        response = await _stream(app)

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "WEB_CHAT_STREAM_FACADE_UNAVAILABLE"
    assert body["blocker"] == "web_chat_stream_facade_unwired"
    assert runtime_state(app) is None


async def test_disabled_flag_never_calls_the_runtime_factory() -> None:
    built: list[int] = []

    def factory():
        built.append(1)
        return composed_runtime()

    app = build_app()
    manager = manager_with(resource(enabled=False, runtime_factory=factory))
    async with manager.lifespan(app):
        pass

    assert built == []
    assert runtime_state(app) is None


async def test_disabled_flag_health_is_not_applicable() -> None:
    res = resource(enabled=False)
    app = build_app()
    async with manager_with(res).lifespan(app):
        assert await res.health() is None


# ---------------------------------------------------------------------------
# Flag on, dependencies present
# ---------------------------------------------------------------------------


async def test_enabled_flag_installs_the_runtime_and_route_streams() -> None:
    runtime = composed_runtime()
    app = build_app()
    manager = manager_with(resource(enabled=True, runtime_factory=lambda: runtime))

    async with manager.lifespan(app):
        installed = web_stream.get_web_stream_runtime(request_for(app))
        assert installed is runtime
        assert runtime_state(app) is runtime

        response = await _stream(app)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # The wire frame is produced only through the trusted run index.
    assert "event: run_started" in response.text
    assert f'"run_id":"{RUN}"' in response.text
    # Startup and shutdown are symmetrical.
    assert runtime_state(app) is None


async def test_enabled_install_is_visible_during_the_whole_lifespan() -> None:
    app = build_app()
    runtime = composed_runtime()
    res = resource(enabled=True, runtime_factory=lambda: runtime)
    manager = manager_with(res)

    async with manager.lifespan(app):
        assert res.runtime is runtime
        assert res.blockers == ()
        assert res.startup_blocker is None
        assert await res.health() is True

    assert res.runtime is None
    assert res.startup_blocker is None


async def test_shutdown_clears_state_so_the_route_refuses_again() -> None:
    app = build_app()
    manager = manager_with(resource(enabled=True, runtime_factory=composed_runtime))

    async with manager.lifespan(app):
        assert runtime_state(app) is not None
    response_after_stop = await _stream(app)

    assert response_after_stop.status_code == 503
    assert response_after_stop.json()["blocker"] == "web_chat_stream_facade_unwired"


async def test_shutdown_does_not_delete_a_runtime_it_does_not_own() -> None:
    """A foreign runtime on the same attribute is never removed by our shutdown."""
    app = build_app()
    foreign = composed_runtime()
    manager = manager_with(resource(enabled=True, runtime_factory=composed_runtime))

    async with manager.lifespan(app):
        # Simulate another composition taking over the state.
        web_stream.install_web_stream_runtime(app, foreign)

    assert runtime_state(app) is foreign


# ---------------------------------------------------------------------------
# Flag on, dependencies partially present → named blocker, not "unavailable"
# ---------------------------------------------------------------------------


async def test_missing_session_resolver_names_that_part() -> None:
    runtime = composed_runtime(sessions=UnavailableChatSessionResolver())
    app = build_app()
    manager = manager_with(resource(enabled=True, runtime_factory=lambda: runtime))

    async with manager.lifespan(app):
        response = await _stream(app)

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == CODE_SESSION_RESOLVER
    assert body["blocker"] == BLOCKER_SESSIONS


async def test_missing_run_index_names_that_part() -> None:
    runtime = composed_runtime(index_provider=UnavailableRunIndexProvider())
    app = build_app()
    manager = manager_with(resource(enabled=True, runtime_factory=lambda: runtime))

    async with manager.lifespan(app):
        response = await _stream(app)

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "WEB_CHAT_STREAM_RUN_INDEX_UNAVAILABLE"
    assert body["blocker"] == BLOCKER_RUN_INDEX


async def test_run_index_without_durable_evidence_names_that_part() -> None:
    """No agent run for the session is a named evidence blocker, not an empty index."""
    provider = DurableRunIndexProvider(FakeSessionFactory(FakeAsyncSession(runs=[], pairs=[])))
    runtime = composed_runtime(index_provider=provider)
    app = build_app()
    manager = manager_with(resource(enabled=True, runtime_factory=lambda: runtime))

    async with manager.lifespan(app):
        response = await _stream(app)

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == CODE_RUN_INDEX_EVIDENCE
    assert body["blocker"] == BLOCKER_RUN_INDEX_EVIDENCE


async def test_missing_chat_stream_facade_names_that_part() -> None:
    runtime = composed_runtime(chat_stream=UnavailableChatStreamFacade())
    app = build_app()
    manager = manager_with(resource(enabled=True, runtime_factory=lambda: runtime))

    async with manager.lifespan(app):
        response = await _stream(app)

    assert response.status_code == 503
    body = response.json()
    # The chat-stream part keeps its own long-standing code and blocker.
    assert body["code"] == "WEB_CHAT_STREAM_FACADE_UNAVAILABLE"
    assert body["blocker"] == BLOCKER_CHAT_STREAM


async def test_partial_runtime_is_installed_and_reports_its_blockers() -> None:
    """A partial runtime is installed on purpose: it names the part that failed."""
    runtime = composed_runtime(index_provider=UnavailableRunIndexProvider())
    res = resource(enabled=True, runtime_factory=lambda: runtime)
    app = build_app()

    async with manager_with(res).lifespan(app):
        assert runtime_state(app) is runtime
        assert res.blockers == (BLOCKER_RUN_INDEX,)
        assert await res.health() is True


# ---------------------------------------------------------------------------
# Resource-level failure modes
# ---------------------------------------------------------------------------


async def test_unbound_app_is_a_named_startup_blocker() -> None:
    """Without the app handle the route stays fail-closed, and the reason is named."""
    res = resource(enabled=True, runtime_factory=composed_runtime)
    await res.startup()  # no bind_app: a plain LifespanManager would do this

    assert res.startup_blocker == BLOCKER_APP_UNBOUND
    assert res.runtime is None
    assert await res.health() is False
    await res.shutdown()
    assert res.startup_blocker is None


async def test_composition_failure_does_not_crash_startup() -> None:
    def boom():
        raise RuntimeError("session factory exploded")

    app = build_app()
    res = resource(enabled=True, runtime_factory=boom)
    manager = manager_with(res)

    async with manager.lifespan(app):
        # The API must boot; only this route stays unavailable.
        assert res.startup_blocker == BLOCKER_COMPOSITION_FAILED
        assert runtime_state(app) is None
        response = await _stream(app)

    assert response.status_code == 503
    assert response.json()["blocker"] == "web_chat_stream_facade_unwired"


async def test_resource_name_is_stable_and_unique_in_the_manager() -> None:
    res = resource(enabled=False)
    assert res.name == RESOURCE_NAME

    manager = WebStreamLifespanManager()
    manager.register(res)
    with pytest.raises(ValueError):
        manager.register(resource(enabled=False))


async def test_manager_binds_the_app_before_startup() -> None:
    seen: list[object] = []
    app = build_app()

    class Bound(WebStreamResource):
        async def startup(self) -> None:
            seen.append(self._app)  # noqa: SLF001 - asserts the binding order
            await super().startup()

    res = Bound(enabled=False)
    async with WebStreamLifespanManager().register(res).lifespan(app):
        pass

    assert seen == [app]


async def test_restart_reinstalls_cleanly() -> None:
    app = build_app()
    built: list[object] = []

    def factory():
        runtime = composed_runtime()
        built.append(runtime)
        return runtime

    res = resource(enabled=True, runtime_factory=factory)
    manager = manager_with(res)

    async with manager.lifespan(app):
        first = runtime_state(app)
    assert runtime_state(app) is None

    async with manager.lifespan(app):
        second = runtime_state(app)
    assert first is not second
    assert len(built) == 2


async def test_run_index_bound_to_the_routed_scope_is_used() -> None:
    """The provider is called with the routed project/session, not a claim."""
    provider = FakeIndexProvider(FakeRunIndex())
    runtime = composed_runtime(index_provider=provider)
    app = build_app()

    async with manager_with(resource(enabled=True, runtime_factory=lambda: runtime)).lifespan(app):
        await _stream(app)

    assert provider.scopes
    scope = provider.scopes[0]
    assert (scope.project_id, scope.session_id) == (PROJECT, SESSION)


async def test_stream_facade_receives_the_authorized_command() -> None:
    facade = FakeChatStreamFacade()
    runtime = composed_runtime(chat_stream=facade)
    app = build_app()

    async with manager_with(resource(enabled=True, runtime_factory=lambda: runtime)).lifespan(app):
        await _stream(app)

    assert facade.calls
    context, command = facade.calls[0]
    assert (command.project_id, command.session_id) == (PROJECT, SESSION)
    assert command.prompt == "hello"
    assert command.last_event_id is None
    # The facade context carries the server-resolved session, never a claim.
    assert context.session_id == SESSION


# ---------------------------------------------------------------------------
# Settings flag
# ---------------------------------------------------------------------------


def test_state_attribute_matches_the_route_constant() -> None:
    """The resource clears exactly the attribute the route reads."""
    from prodavan.core.infra.web_stream_resource import RUNTIME_STATE_ATTR

    assert RUNTIME_STATE_ATTR == web_stream.RUNTIME_STATE_ATTR
    assert RESOURCE_NAME == "web_stream_runtime"


def test_flag_defaults_to_off_and_accepts_the_documented_env_names() -> None:
    """Off by default — the migration must not switch the route on by accident."""
    from prodavan.config.settings import Settings

    field = Settings.model_fields["web_stream_runtime_enabled"]
    assert field.default is False
    choices = tuple(getattr(field.validation_alias, "choices", ()))
    assert choices == (
        "WEB_STREAM_RUNTIME_ENABLED",
        "WEB_CHAT_STREAM_RUNTIME_ENABLED",
    )

"""Path/method parity: every request the web client can build has a live route.

A missing path is the worst class of divergence because it surfaces as a 404 only
at runtime, in a browser, against a deployed API. vitest mocks ``fetch``, so it can
only assert the string the client *would* have sent — never that the server answers
it. This module closes that gap in one direction: the client's declared routes are
matched against ``app.openapi()``.
"""

from __future__ import annotations

import pytest

from .server_contract import API_PREFIX, operations, path_parameters, route_matches
from .web_contract import (
    CONTENT_OPS_SERVICE,
    DYNAMIC_CLIENT,
    content_ops_calls,
    dynamic_route_bindings,
)


def _mounted(path: str) -> str:
    """The client declares paths relative to the API mount (``/companies/...``)."""
    return path if path.startswith(API_PREFIX) else API_PREFIX + path


def _matching_routes(method: str, path: str) -> list[tuple[str, str]]:
    mounted = _mounted(path)
    return [
        (route_method, route_path)
        for (route_method, route_path) in operations()
        if route_method == method and route_matches(mounted, route_path)
    ]


# ------------------------------------------------------------------ content-ops


@pytest.mark.parametrize(
    "web_call", content_ops_calls(), ids=lambda call: f"{call.call.method} {call.template}"
)
def test_content_ops_call_resolves_to_a_registered_route(web_call) -> None:
    """Every ``/content/...`` literal in the content-ops adapter has a route."""
    routes = _matching_routes(web_call.call.method, web_call.call.path)
    assert routes, (
        f"{CONTENT_OPS_SERVICE.name} calls {web_call.call.method} {web_call.call.path}, "
        "which is not registered on the content router"
    )


def test_content_ops_does_not_invent_parameters() -> None:
    """A path placeholder the client fills must exist as a path parameter."""
    for web_call in content_ops_calls():
        (_, route_path), *_ = _matching_routes(web_call.call.method, web_call.call.path)
        server_parameters = path_parameters(route_path)
        requested = _placeholder_count(web_call.call.path)
        assert requested == len(server_parameters), (
            f"{web_call.call.method} {web_call.call.path} fills {requested} placeholder(s) "
            f"but the server route declares {server_parameters}"
        )


def _placeholder_count(path: str) -> int:
    return path.count("{p}")


# --------------------------------------------------------------------- dynamic


@pytest.mark.parametrize(
    "binding", dynamic_route_bindings(), ids=lambda binding: binding.method
)
def test_dynamic_gateway_method_resolves_to_a_registered_route(binding) -> None:
    """Each declared gateway method maps onto a real dynamic-apps operation."""
    routes = _matching_routes(binding.http, binding.path)
    assert routes, (
        f"{DYNAMIC_CLIENT.name} declares {binding.method} as {binding.http} {binding.path}, "
        "which is not registered on the dynamic-apps router"
    )
    assert len(routes) == 1, f"{binding.method} is ambiguous: {routes}"


@pytest.mark.parametrize(
    "binding", dynamic_route_bindings(), ids=lambda binding: binding.method
)
def test_dynamic_path_identifier_is_the_server_path_parameter(binding) -> None:
    """The identifier the client interpolates is the parameter the server expects.

    This is the check that would have caught a client sending a definition *row* id
    where the route declares ``{app_id}``, or a caller-supplied cabinet that the
    route does not take at all. ``company_id`` is excluded because the shell
    resolves the tenant and injects it for every call, so no transport method owns
    that segment.
    """
    (_, route_path), *_ = _matching_routes(binding.http, binding.path)
    owned = {name for name in path_parameters(route_path) if name != "company_id"}
    declared = {_snake_case(name) for name in binding.path_identifiers}
    assert declared == owned, (
        f"{binding.method} declares path identifier(s) {sorted(declared)} but the route "
        f"{route_path} declares {sorted(owned)}"
    )


def _snake_case(name: str) -> str:
    return "".join(f"_{char.lower()}" if char.isupper() else char for char in name)


@pytest.mark.parametrize(
    "binding", dynamic_route_bindings(), ids=lambda binding: binding.method
)
def test_dynamic_query_names_are_served(binding) -> None:
    """Every query key the client sends is accepted by that operation."""
    (method, route_path), *_ = _matching_routes(binding.http, binding.path)
    served = {
        parameter["name"]
        for parameter in operations()[(method, route_path)].get("parameters", [])
        if parameter["in"] in {"query", "header"}
    }
    unknown = sorted(set(binding.query) - served)
    assert not unknown, (
        f"{binding.method} sends query {unknown} which {method} {route_path} does not "
        f"accept (served: {sorted(served)})"
    )


def test_dynamic_client_reads_only_registered_dynamic_routes() -> None:
    """The dynamic screens never rely on a route outside the dynamic-apps family."""
    families = {path.split("/dynamic-apps")[0] for _, path in operations() if "/dynamic-apps" in path}
    assert families, "dynamic-apps routes are not registered"
    for binding in dynamic_route_bindings():
        assert "/dynamic-apps" in binding.path, binding

"""MIG-AUTH-AUDIT-01 — the audit's own detector, tested against both answers.

A green contract run means "nothing was found". That is only good news if the
detector could have found something. This module builds the two situations the
audit exists to catch, and the two it must not misreport, on synthetic inputs
that carry no production data:

**must be detected**

* a route registered without an authentication dependency;
* a handler that reads ``company_id`` from a body model and never validates it;
* a handler whose validation sits one receiver hop away — the shape that a naive
  "does the word ``require`` appear" check misses.

**must not be misreported**

* a handler that authenticates through a sub-dependency chain;
* a handler that validates through ``SomeService(...).method(...)``;
* a handler that validates through ``self._helper()`` inside a method.

The guard search is exercised on a temporary source tree rather than on
``src/prodavan``, so the assertions describe the analyzer's behaviour and not
today's state of the product.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

from .route_inventory import (
    SourceIndex,
    dependency_names,
    is_authenticated,
    request_parameter_classes,
    route_inventory,
)

# --------------------------------------------------------------------------- #
# synthetic applications: authentication is either declared or it is not
# --------------------------------------------------------------------------- #


async def get_principal() -> str:
    return "principal-1"


async def require_platform_admin(principal: str = Depends(get_principal)) -> str:
    return principal


class _Body(BaseModel):
    company_id: str
    name: str = "x"


def _build_app() -> FastAPI:
    """A two-route app: one open, one protected — the smallest interesting surface.

    The dependency names are the production ones on purpose: the recogniser is a
    closed family (``route_inventory.AUTHENTICATION_DEPENDENCIES``), so a fixture
    with invented names would test that a made-up name is not recognised, not
    that a real guard is.
    """

    app = FastAPI()
    router = APIRouter()

    @router.get("/open/thing")
    async def open_thing() -> dict[str, str]:
        return {"ok": "yes"}

    @router.get("/secure/thing")
    async def secure_thing(_: str = Depends(get_principal)) -> dict[str, str]:
        return {"ok": "yes"}

    @router.get("/secure/derived")
    async def secure_derived(_: str = Depends(require_platform_admin)) -> dict[str, str]:
        return {"ok": "yes"}

    @router.post("/secure/with-tenant")
    async def secure_with_tenant(
        body: _Body, _: str = Depends(get_principal)
    ) -> dict[str, str]:
        return {"company": body.company_id}

    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture(scope="module")
def synthetic_app() -> FastAPI:
    return _build_app()


def test_a_route_without_a_principal_is_reported_unauthenticated(synthetic_app) -> None:
    records = {record.key: record for record in route_inventory(synthetic_app)}
    # The framework's own /docs, /redoc, /openapi.json come along; they are the
    # reason ``RouteRecord.framework`` exists, so they are filtered here rather
    # than asserted away.
    authored = {key for key, record in records.items() if not record.framework}
    assert authored == {
        "GET /api/v1/open/thing",
        "GET /api/v1/secure/thing",
        "GET /api/v1/secure/derived",
        "POST /api/v1/secure/with-tenant",
    }
    open_route = records["GET /api/v1/open/thing"]
    assert is_authenticated(dependency_names(open_route.dependant)) is False


def test_a_route_with_a_principal_is_reported_authenticated(synthetic_app) -> None:
    records = {record.key: record for record in route_inventory(synthetic_app)}
    assert is_authenticated(dependency_names(records["GET /api/v1/secure/thing"].dependant))
    # …including when the principal arrives only through a dependency's own
    # ``Depends`` chain: the walk must descend, not look at the top level.
    assert is_authenticated(dependency_names(records["GET /api/v1/secure/derived"].dependant))


def test_a_body_tenant_selector_is_visible_on_the_route(synthetic_app) -> None:
    """The selector has to be *found* before it can be judged."""

    records = {record.key: record for record in route_inventory(synthetic_app)}
    route = records["POST /api/v1/secure/with-tenant"]
    params = request_parameter_classes(route)
    assert ("body", "company_id") in params.body_fields
    assert "company_id" not in params.path


# --------------------------------------------------------------------------- #
# synthetic source tree: the guard search must find the real shapes
# --------------------------------------------------------------------------- #

_GUARDED_SERVICE = '''
"""A service that does check the tenant, two ways."""

from prodavan.application.identity.service import EntitlementService


class GuardedService:
    def __init__(self, session):
        self._session = session

    async def _assert_company(self, *, principal, company_id):
        await EntitlementService(self._session).require_company_actor(principal, company_id)

    async def via_helper(self, *, principal, company_id):
        """Guarded through ``self.``: the shape a text search misses."""
        await self._assert_company(principal=principal, company_id=company_id)
        return {"company": company_id}

    async def via_receiver(self, *, principal, company_id, employee=None):
        from prodavan.application.identity.service import EntitlementService

        await EntitlementService(self._session).require_company_actor(
            principal, company_id, employee=employee
        )
        return {"company": company_id}
'''

_UNGUARDED_SERVICE = '''
"""A service that reads the company and trusts it."""


class UnguardedService:
    def __init__(self, session):
        self._session = session

    async def read(self, *, company_id):
        return await self._session.execute(
            f"select * from things where company_id = '{company_id}'"
        )

    async def read_with_identity_but_no_guard(self, *, principal, company_id):
        visible = await self.list_visible(company_id)
        return visible

    async def list_visible(self, company_id):
        return []
'''

_LOCAL_BINDING = '''
"""A handler that builds the service into a local and calls it."""

from prodavan.application.fixture.guarded import GuardedService


async def handler(principal, company_id, session):
    svc = GuardedService(session)
    return await svc.via_helper(principal=principal, company_id=company_id)
'''

_IMPORT_HOP = '''
"""A guard reached only through a function imported from another module."""

from prodavan.application.fixture.runtime_guard import require_running_thing


async def handler(project_id, principal, employee):
    await require_running_thing(project_id, principal=principal, employee=employee)
    return {}
'''

_REEXPORTED_PACKAGE = '''
"""Package __init__ that forwards a class from a submodule (``_LAZY`` style)."""

_LAZY = {"GuardedService": (".guarded", "GuardedService")}


def __getattr__(name):
    raise AttributeError(name)
'''

_PACKAGE_CONSUMER = '''
"""A handler that imports the class through the package facade."""

from prodavan.application.fixture import GuardedService


async def handler(principal, company_id, session):
    return await GuardedService(session).via_receiver(
        principal=principal, company_id=company_id
    )
'''


@pytest.fixture(scope="module")
def fixture_tree(tmp_path_factory) -> Path:
    """A minimal ``prodavan`` tree exercising each resolution shape."""

    root = tmp_path_factory.mktemp("auth_audit_fixture") / "prodavan"
    files = {
        "application/fixture/__init__.py": _REEXPORTED_PACKAGE,
        "application/fixture/guarded.py": _GUARDED_SERVICE,
        "application/fixture/unguarded.py": _UNGUARDED_SERVICE,
        "application/fixture/local_binding.py": _LOCAL_BINDING,
        "application/fixture/import_hop.py": _IMPORT_HOP,
        "application/fixture/package_consumer.py": _PACKAGE_CONSUMER,
        "application/fixture/runtime_guard.py": (
            "async def require_running_thing(project_id, *, principal, employee):\n"
            "    from prodavan.application.identity.service import EntitlementService\n"
            "    await EntitlementService(None).require_company_actor(principal, project_id)\n"
            "    return project_id\n"
        ),
        "application/identity/service.py": (
            "class EntitlementService:\n"
            "    def __init__(self, session):\n"
            "        self._session = session\n"
            "    async def require_company_actor(self, principal, company_id, *, employee=None):\n"
            "        return company_id\n"
        ),
    }
    for relative, body in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def index(fixture_tree: Path) -> SourceIndex:
    return SourceIndex(fixture_tree)


def _record(module: str, name: str):
    from .route_inventory import RouteRecord

    return RouteRecord(
        path=f"/fixture/{name}",
        method="POST",
        name=name,
        module=module,
        endpoint_name=name,
    )


def test_synthetic_tree_is_indexed(fixture_tree) -> None:
    """Guard: if the fixture root were mis-resolved every test below would pass
    for the wrong reason (an empty index reports no guard at all)."""

    expected = {
        "application/fixture/__init__.py",
        "application/fixture/guarded.py",
        "application/fixture/unguarded.py",
        "application/fixture/local_binding.py",
        "application/fixture/import_hop.py",
        "application/fixture/package_consumer.py",
        "application/fixture/runtime_guard.py",
        "application/identity/service.py",
    }
    present = {
        str(path.relative_to(fixture_tree)).replace("\\", "/")
        for path in fixture_tree.rglob("*.py")
    }
    assert present == expected


def test_guard_through_a_self_helper_is_found(index) -> None:
    """``self._helper()`` -> ``require_company_actor``: two hops inside a class."""

    record = _record("prodavan.application.fixture.guarded", "GuardedService.via_helper")
    assert index.guard_reached(record) == "require_company_actor"


def test_guard_through_a_service_receiver_is_found(index) -> None:
    record = _record("prodavan.application.fixture.guarded", "GuardedService.via_receiver")
    assert index.guard_reached(record) == "require_company_actor"


def test_guard_through_a_local_variable_is_found(index) -> None:
    """``svc = GuardedService(session)`` then ``svc.via_helper(...)``."""

    record = _record("prodavan.application.fixture.local_binding", "handler")
    assert index.guard_reached(record) == "require_company_actor"


def test_guard_through_an_imported_function_is_found(index) -> None:
    record = _record("prodavan.application.fixture.import_hop", "handler")
    assert index.guard_reached(record) == "require_company_actor"


def test_guard_through_a_package_reexport_is_found(index) -> None:
    """``from pkg import GuardedService`` with a ``_LAZY`` table behind it."""

    record = _record("prodavan.application.fixture.package_consumer", "handler")
    assert index.guard_reached(record) == "require_company_actor"


def test_an_unguarded_handler_is_not_reported_as_guarded(index) -> None:
    """The dangerous direction: a false "guarded" hides a real hole."""

    record = _record("prodavan.application.fixture.unguarded", "UnguardedService.read")
    assert index.guard_reached(record) is None


def test_identity_passing_is_not_itself_a_guard(index) -> None:
    """A helper that merely receives ``principal`` must not be read as a check.

    ``read_with_identity_but_no_guard`` takes a principal and calls
    ``list_visible`` — nothing raises on a foreign tenant, so the answer must be
    "unguarded" even though the keyword ``principal`` is everywhere.
    """

    record = _record(
        "prodavan.application.fixture.unguarded",
        "UnguardedService.read_with_identity_but_no_guard",
    )
    assert index.guard_reached(record) is None


def test_a_missing_handler_is_not_guarded(index) -> None:
    """An endpoint the index cannot see must never be assumed safe."""

    record = _record("prodavan.application.fixture.nonexistent", "nothing_here")
    assert index.guard_reached(record) is None


def test_source_index_reads_the_real_tree() -> None:
    """The default root resolves to ``apps/api/src/prodavan`` and is populated."""

    from .route_inventory import SRC_ROOT

    real = SourceIndex()
    assert SRC_ROOT.name == "prodavan"
    assert real.function("prodavan.api.deps", "get_principal") is not None
    assert real.function("prodavan.api.deps", "require_platform_admin") is not None

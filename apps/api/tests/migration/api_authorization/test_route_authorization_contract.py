"""MIG-AUTH-AUDIT-01 — every HTTP route is authenticated and tenant-scoped.

The defect class this guards against is invisible to a functional test: those
call the API with a valid token for the right tenant and pass whether or not the
route validates anything. Here the *whole surface* is walked instead, and two
questions are asked of every route:

**is it authenticated?**
    A route without an authentication dependency must appear in
    :data:`registry.PUBLIC_ROUTES` with a justification, and that justification
    must still describe the handler FastAPI actually serves. A new anonymous
    route therefore fails the build, and so does a public route whose handler
    was replaced by something the justification no longer covers.

**does it take the tenant from the server?**
    A ``company_id`` / ``owner_company_id`` / ``X-Cabinet-Id`` selector read from
    the query string, a body model or a header is attacker-controlled. The
    handler (or something it calls with the caller's identity) must validate it
    against the server-side tenant set. Where that does not happen, the route is
    either a reviewed platform-admin route recorded in
    :data:`registry.UNVALIDATED_TENANT_SELECTORS`, or a new finding — and a
    finding fails this test rather than being silently tolerated.

Both tables are pinned in *both* directions: an entry that no longer describes
the code fails too, so a gap cannot be closed and left in the registry, and a
justification cannot outlive the handler it justifies.

Deliberately out of scope, stated so nobody reads absence as coverage:

* whether the *value* a guard compares against is itself trustworthy (that is a
  per-handler question, answered by the handlers' own tests);
* behaviour after authentication — this module never opens a session or a
  request. ``findings.md`` records the gaps the analysis did find.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prodavan.main import create_app

from . import registry
from .route_inventory import (
    TENANT_DEPENDENCIES,
    RouteRecord,
    SourceIndex,
    dependency_names,
    is_authenticated,
    request_parameter_classes,
    route_inventory,
)

#: Query/body/header names by which a caller can select a tenant. The path
#: segment (``{company_id}``) is deliberately *not* in this set: a path selector
#: is the normal REST shape and is expected to be validated, which is what the
#: guard search verifies.
TENANT_SELECTOR_NAMES = frozenset({
    "tenant_id",
    "company_id",
    "owner_company_id",
    "company_ids",
    "tenant_ids",
})

#: Tenant-carrying headers the API accepts (``api.deps``). ``resolve_actor_scope``
#: is the one place allowed to trust them, and only after membership checks.
TENANT_SELECTOR_HEADERS = frozenset({"X-Cabinet-Id", "X-Project-Id"})

PLATFORM_ADMIN_DEPENDENCY = "require_platform_admin"

#: Path parameters that place a route inside a tenant/cabinet/project.
SCOPING_PATH_PARAMETERS = frozenset({"company_id", "tenant_id", "project_id", "cabinet_id"})


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def app():
    """The application the audit reads. ``create_app`` binds routes, not data."""

    return create_app()


@pytest.fixture(scope="module")
def routes(app) -> tuple[RouteRecord, ...]:
    inventory = route_inventory(app)
    assert inventory, "the app exposes no routes — the audit would be vacuous"
    return inventory


@pytest.fixture(scope="module")
def source_index() -> SourceIndex:
    return SourceIndex()


@pytest.fixture(scope="module")
def public_keys() -> frozenset[str]:
    return frozenset(entry.key for entry in registry.PUBLIC_ROUTES)


def _tenant_selectors(record: RouteRecord) -> tuple[str, ...]:
    """Client-controlled tenant selectors this route reads."""

    params = request_parameter_classes(record)
    selectors = [name for name in params.query if name in TENANT_SELECTOR_NAMES]
    selectors += [
        f"body.{field}"
        for _parameter, field in params.body_fields
        if field in TENANT_SELECTOR_NAMES
    ]
    selectors += [name for name in params.header if name in TENANT_SELECTOR_HEADERS]
    # Sorted and de-duplicated: the assertion compares sets, the message reads
    # like a sentence.
    return tuple(sorted(set(selectors)))


def _scoping_path_parameters(record: RouteRecord) -> tuple[str, ...]:
    params = request_parameter_classes(record)
    return tuple(sorted(set(params.path) & SCOPING_PATH_PARAMETERS))


def _is_public(record: RouteRecord, public_keys: frozenset[str]) -> bool:
    return any(entry.covers(record) for entry in registry.PUBLIC_ROUTES)


def _unvalidated_selectors(
    record: RouteRecord, index: SourceIndex
) -> tuple[str, ...]:
    """Selectors used without a reachable guard, on a non-platform route.

    A platform-admin route is excluded on purpose: ``require_platform_admin``
    already proves the caller may act across every tenant, so a second
    per-tenant check is a policy question (recorded in ``findings.md`` F-01 as
    informational), not a tenant-boundary defect.
    """

    dependencies = dependency_names(record.dependant)
    if PLATFORM_ADMIN_DEPENDENCY in dependencies:
        return ()
    if set(dependencies) & TENANT_DEPENDENCIES:
        return ()
    if index.guard_reached(record):
        return ()
    return _tenant_selectors(record)


# --------------------------------------------------------------------------- #
# 1. the inventory itself
# --------------------------------------------------------------------------- #


def test_inventory_covers_the_routers_the_app_mounts(routes) -> None:
    """A walk that silently stops at a wrapper would make every test below vacuous.

    ``_IncludedRouter`` hides the child routes from ``app.routes`` until it is
    asked for its effective contexts, and this FastAPI version does exactly
    that. The assertion is on *scale and breadth*, not on an exact count: the
    point is that the versioned surface (``/api/v1/**``), the bare probe surface
    (``/health*``) and the framework's own routes are all present.
    """

    paths = {record.path for record in routes}
    modules = {record.module for record in routes}
    assert len(routes) > 300, f"only {len(routes)} routes walked; the wrappers were not entered"
    assert "/health/live" in paths
    assert "/api/v1/health/live" in paths
    assert "/openapi.json" in paths
    assert any(path.startswith("/api/v1/projects") for path in paths)
    assert any(path.startswith("/api/v1/admin") for path in paths)
    assert len(modules) > 20, f"only {len(modules)} handler modules reached: {sorted(modules)}"


def test_inventory_has_one_entry_per_method_and_path(routes) -> None:
    keys = [record.key for record in routes]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert duplicates == [], f"duplicate route keys: {duplicates}"


def test_inventory_records_the_authentication_dependencies_it_uses(routes) -> None:
    """The dependency family is what the whole audit keys on; it must be present.

    If a refactor renamed ``get_principal`` the authenticated count would collapse
    to zero and the checks below would either explode or — worse — pass for the
    wrong reason. This pins the floor.
    """

    authenticated = [record for record in routes if is_authenticated(dependency_names(record.dependant))]
    assert len(authenticated) > 300, (
        f"only {len(authenticated)} of {len(routes)} routes report an authentication "
        "dependency; the recogniser no longer matches the code"
    )


# --------------------------------------------------------------------------- #
# 2. authentication: every route, or an explicit justification
# --------------------------------------------------------------------------- #


def test_every_anonymous_route_is_in_the_public_allow_list(routes, public_keys) -> None:
    """The core contract: nothing answers without a principal unless approved."""

    offenders = []
    for record in routes:
        if is_authenticated(dependency_names(record.dependant)):
            continue
        if _is_public(record, public_keys):
            continue
        offenders.append(f"{record.key}  ({record.source})")
    assert offenders == [], (
        "routes answer without an authentication dependency and are not in "
        "registry.PUBLIC_ROUTES:\n  " + "\n  ".join(offenders)
    )


def test_every_public_allow_list_entry_is_still_actually_public(routes, public_keys) -> None:
    """A stale public entry would be a hole nobody notices: the route is guarded,
    the reviewer believes an open route exists, and the next change to it is
    judged against the wrong premise."""

    anonymous = {
        record.key for record in routes
        if not is_authenticated(dependency_names(record.dependant))
    }
    stale = sorted(key for key in public_keys if key not in anonymous)
    assert stale == [], (
        "registry.PUBLIC_ROUTES lists routes that are authenticated (or no longer "
        f"served): {stale}"
    )


@pytest.mark.parametrize("entry", registry.PUBLIC_ROUTES, ids=lambda entry: entry.key)
def test_public_entry_states_why_it_is_safe(entry) -> None:
    """Every public entry carries a justification with a real reason in it.

    The check is deliberately crude — length plus a "why" word — because the
    value of the prose is for a reviewer, while the mechanical part is that no
    entry can be added with an empty or one-word reason.
    """

    assert entry.match in {"exact", "prefix"}, f"{entry.key}: unknown match mode"
    assert len(entry.justification) >= 80, f"{entry.key}: justification is too short to be a reason"
    lowered = entry.justification.lower()
    assert any(
        word in lowered
        for word in ("hmac", "probe", "credential", "static", "schema", "no tenant", "reads no tenant")
    ), f"{entry.key}: justification does not name a mechanism: {entry.justification!r}"


@pytest.mark.parametrize(
    "entry",
    registry.PUBLIC_ROUTES,
    ids=lambda entry: entry.key,
)
def test_public_entry_is_served_by_the_handler_it_describes(entry, routes) -> None:
    """The justification must describe code that is still there."""

    expected = registry.PUBLIC_ROUTE_HANDLERS.get(entry.key)
    assert expected is not None, f"{entry.key}: no handler registered for this public route"
    covered = [record for record in routes if entry.covers(record)]
    assert covered, f"{entry.key}: no route matches this entry"
    actual = {f"{record.module}:{record.endpoint_name}" for record in covered}
    missing = {value for value in actual if not value.startswith(expected)}
    assert missing == set(), (
        f"{entry.key}: served by {sorted(actual)}, but the justification "
        f"describes {expected!r}"
    )


# --------------------------------------------------------------------------- #
# 3. tenant selectors: the server decides the tenant
# --------------------------------------------------------------------------- #


def test_no_unreviewed_tenant_selector_bypasses_a_guard(routes, source_index) -> None:
    """Every non-admin route that takes a tenant from the request validates it."""

    registered = {
        (gap.key, gap.selector) for gap in registry.UNVALIDATED_TENANT_SELECTORS
    }
    offenders: list[str] = []
    for record in routes:
        selectors = _unvalidated_selectors(record, source_index)
        for selector in selectors:
            if (record.key, selector) in registered:
                continue
            offenders.append(
                f"{record.key}  selector={selector}  handler={record.source}  "
                f"indexed_proof={registry.REVIEWED_INDIRECT_TENANT_PROOFS.get(record.key, '-')}"
            )
    assert offenders == [], (
        "routes act on a client-supplied tenant selector with no guard reachable "
        "from the handler, and are not recorded in "
        "registry.UNVALIDATED_TENANT_SELECTORS:\n  " + "\n  ".join(sorted(offenders))
    )


def test_every_recorded_tenant_selector_gap_is_still_open(routes, source_index) -> None:
    """A closed gap must leave the registry.

    Otherwise the table slowly becomes a list of things that used to be wrong,
    and the next real gap hides among them.
    """

    by_key = {record.key: record for record in routes}
    closed: list[str] = []
    for gap in registry.UNVALIDATED_TENANT_SELECTORS:
        record = by_key.get(gap.key)
        if record is None:
            closed.append(f"{gap.key}: route no longer exists")
            continue
        if gap.selector not in _tenant_selectors(record):
            closed.append(f"{gap.key}: no longer takes {gap.selector}")
            continue
        if not _unvalidated_selectors(record, source_index):
            closed.append(f"{gap.key}: now guarded (indexed_proof="
                          f"{source_index.guard_reached(record)!r})")
    assert closed == [], (
        "registry.UNVALIDATED_TENANT_SELECTORS lists gaps that no longer exist; "
        "remove them and the matching section of findings.md:\n  " + "\n  ".join(closed)
    )


@pytest.mark.parametrize(
    "gap",
    registry.UNVALIDATED_TENANT_SELECTORS,
    ids=lambda gap: f"{gap.key}::{gap.selector}",
)
def test_every_gap_names_a_finding(gap) -> None:
    """A gap without a findings entry is a gap nobody owns."""

    findings = _findings_text()
    assert f"## {gap.finding}" in findings, (
        f"{gap.key}: finding {gap.finding} has no section in findings.md"
    )
    assert gap.key in findings, (
        f"{gap.key}: findings.md does not mention this route"
    )


def test_every_scoped_route_registers_its_indirect_proof_or_a_guard(
    routes, source_index, public_keys
) -> None:
    """A route below ``/companies/{company_id}``, ``/projects/{project_id}`` or
    ``/cabinets/{cabinet_id}`` either guards that scope, or says where the guard
    really is.

    This is the check that catches the *new* kind of hole the selector rule
    cannot see: a handler that never names a tenant parameter because the tenant
    arrives through a dependency or an injected runtime.
    """

    offenders: list[str] = []
    for record in routes:
        if not _scoping_path_parameters(record):
            continue
        dependencies = dependency_names(record.dependant)
        if PLATFORM_ADMIN_DEPENDENCY in dependencies:
            continue
        if set(dependencies) & TENANT_DEPENDENCIES:
            continue
        if source_index.guard_reached(record):
            continue
        if record.key in registry.REVIEWED_INDIRECT_TENANT_PROOFS:
            continue
        if record.key in _hmac_ingress_keys():
            continue
        offenders.append(
            f"{record.key}  scoped_by={list(_scoping_path_parameters(record))}  "
            f"handler={record.source}"
        )
    assert offenders == [], (
        "tenant-scoped routes with no guard reachable from the handler and no "
        "registered indirect proof:\n  " + "\n  ".join(sorted(offenders))
    )


def test_reviewed_indirect_proofs_are_still_needed(routes, source_index) -> None:
    """An indirect proof that the index now sees directly is dead weight."""

    by_key = {record.key: record for record in routes}
    obsolete = []
    for key in registry.REVIEWED_INDIRECT_TENANT_PROOFS:
        record = by_key.get(key)
        if record is None:
            obsolete.append(f"{key}: route no longer exists")
            continue
        if source_index.guard_reached(record):
            obsolete.append(
                f"{key}: the guard search now reaches "
                f"{source_index.guard_reached(record)!r} directly"
            )
    assert obsolete == [], (
        "registry.REVIEWED_INDIRECT_TENANT_PROOFS has entries the audit no longer "
        "needs:\n  " + "\n  ".join(obsolete)
    )


def test_platform_admin_surface_is_authenticated_everywhere(routes) -> None:
    """The admin surface is the largest tenant-crossing surface; pin its guard.

    ``/api/v1/admin/**`` is reached by an operator who may act on any company.
    Every route there must require the platform-admin dependency — not merely a
    tenant guard — so an admin route can never be satisfied by a tenant employee
    who happens to be a member of the company in the path.
    """

    offenders = []
    for record in routes:
        if not record.path.startswith("/api/v1/admin/"):
            continue
        dependencies = dependency_names(record.dependant)
        if PLATFORM_ADMIN_DEPENDENCY not in dependencies:
            offenders.append(f"{record.key}  deps={sorted(dependencies)}  ({record.source})")
    assert offenders == [], (
        "admin routes that do not require platform admin:\n  " + "\n  ".join(sorted(offenders))
    )


# --------------------------------------------------------------------------- #
# 4. the surface is covered by something, not just by this file
# --------------------------------------------------------------------------- #


def test_inventory_matches_the_routers_the_app_declares(app, routes) -> None:
    """Cross-check the walk against FastAPI's own view of the app.

    ``app.routes`` is not usable directly (the wrappers hide children), so the
    check uses the app's OpenAPI schema for the *documented* subset: every path
    the schema advertises must be in the inventory with the same methods. A
    wrapper the walker missed, or a route registered behind an opaque mount,
    shows up as a difference here.
    """

    documented = app.openapi()
    inventory = {(record.path, record.method.lower()) for record in routes}
    missing = []
    for path, operations in documented.get("paths", {}).items():
        for method in operations:
            if method.startswith("x-") or method == "parameters":
                continue
            if (path, method.lower()) not in inventory:
                missing.append(f"{method.upper()} {path}")
    assert missing == [], f"routes in the OpenAPI schema but not in the inventory: {missing}"


# --------------------------------------------------------------------------- #
# helpers that read the findings file
# --------------------------------------------------------------------------- #


def _findings_path() -> Path:
    return Path(__file__).with_name("findings.md")


def _findings_text() -> str:
    path = _findings_path()
    assert path.is_file(), f"{path} is missing: findings must be recorded, not implied"
    return path.read_text(encoding="utf-8")


def _hmac_ingress_keys() -> frozenset[str]:
    """The two signed-ingress routes: their ``project_id`` is proven by an HMAC.

    Kept as a function rather than a constant so the set is derived from the
    public registry — an ingress route cannot be public *and* unproven.
    """

    return frozenset(
        entry.key
        for entry in registry.PUBLIC_ROUTES
        if "webhooks/" in entry.path
    )


__all__ = [
    "app",
    "test_every_anonymous_route_is_in_the_public_allow_list",
    "test_every_public_allow_list_entry_is_still_actually_public",
    "test_every_scoped_route_registers_its_indirect_proof_or_a_guard",
    "test_inventory_covers_the_routers_the_app_mounts",
    "test_inventory_matches_the_routers_the_app_declares",
    "test_no_unreviewed_tenant_selector_bypasses_a_guard",
    "test_platform_admin_surface_is_authenticated_everywhere",
]

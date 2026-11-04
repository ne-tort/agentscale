"""Static shape of the HTTP surface: walking, parameter classes, guard reachability.

Three independent analyses live here, each of which the contract test turns into
an assertion:

``route_inventory``
    every routable endpoint of a FastAPI app, with its *effective* path. The
    FastAPI version used by this project wraps each ``include_router`` call in a
    private ``_IncludedRouter`` node that hides the routes until it is asked for
    ``effective_route_contexts()``; a naive walk of ``app.routes`` therefore
    sees two objects and zero endpoints. Both shapes are supported here so the
    audit keeps working whichever one the framework produces.

``request_parameter_classes``
    what a route reads from the request, split into path / query / body / header,
    as FastAPI itself classifies it. The split matters: a tenant id in the *path*
    is a selector the handler is expected to validate against the server
    context, while the same name in the query string or in a body model is the
    shape that leaks across tenants when the handler forgets to validate it.

``guard_reachability``
    whether a handler (or something it calls, transitively) performs an
    authorization check. Calls are followed through method receivers: only
    ``SomeService(...).method(...)`` receivers that resolve to a known class in
    ``src/prodavan`` are entered, and only when the callee actually receives an
    identity object (``principal``/``employee``/``auth``/``bridge``/``claims``).
    Without both restrictions a plain call-graph walk reports every handler as
    guarded, because generic helpers such as ``_envelope`` and ``len`` dominate
    the graph.

Nothing here executes production code beyond importing the app factory and
asking FastAPI how it parsed the signatures.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Directory the analyzer reads source from (``apps/api/src/prodavan``).
SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "prodavan"

#: HTTP verbs a route may declare; ``HEAD`` is implied by ``GET`` and is folded
#: into it so the inventory has one entry per client-visible operation.
_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "HEAD")

#: Framework-internal routes that are not part of the product surface. They are
#: listed as an allow rule rather than hard-coded away so a reader can see them.
FRAMEWORK_MODULES = ("fastapi.", "starlette.")


# --------------------------------------------------------------------------- #
# route inventory
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RouteRecord:
    """One client-visible operation: ``METHOD /path`` as FastAPI will match it."""

    path: str
    method: str
    name: str
    module: str
    endpoint_name: str
    #: ``None`` for the framework's own routes (``/openapi.json`` etc.).
    dependant: Any = None
    endpoint: Any = None
    #: ``True`` when the operation is a framework route rather than a handler
    #: authored in this repository.
    framework: bool = False

    @property
    def key(self) -> str:
        """Stable identity used by the allow-list and by the findings file."""

        return f"{self.method} {self.path}"

    @property
    def source(self) -> str:
        return f"{self.module}:{self.endpoint_name}"


def _iter_routable(routes: Iterable[Any]) -> Iterator[Any]:
    """Yield routable endpoints, descending into the router wrappers.

    FastAPI >= 0.137 represents one ``include_router`` call as a single
    ``_IncludedRouter`` node whose children are only materialised on demand.
    Older versions (and plain Starlette) nest ``APIRouter`` objects with a
    ``routes`` attribute and an optional ``prefix``. Both are handled, and the
    recursion is depth-first so the resulting order is the declaration order.
    """

    for route in routes:
        effective = getattr(route, "effective_route_contexts", None)
        if callable(effective):
            yield from effective()
            continue
        children = getattr(route, "routes", None)
        if children is not None and not hasattr(route, "endpoint"):
            prefix = getattr(route, "prefix", "") or ""
            for child in _iter_routable(children):
                path = getattr(child, "path", None)
                if path is None:
                    yield child
                    continue
                yield _with_prefix(child, prefix)
            continue
        yield route


def _with_prefix(context: Any, prefix: str) -> Any:
    """Return ``context`` as seen under an extra router ``prefix``.

    Only used on the nested-router branch, where the wrapper carries the prefix
    that the children do not know about. ``_EffectiveRouteContext`` already
    reports a fully composed path, so it is never rewritten.
    """

    if not prefix:
        return context
    path = getattr(context, "path", None)
    if path is None:
        return context
    return _PrefixedContext(context, prefix + path)


@dataclass(frozen=True, slots=True)
class _PrefixedContext:
    """Minimal adapter so a prefixed child looks like an effective context."""

    inner: Any
    path: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def route_inventory(app: Any) -> tuple[RouteRecord, ...]:
    """Every ``METHOD /path`` the app serves, deduplicated and sorted."""

    records: dict[str, RouteRecord] = {}
    for context in _iter_routable(app.routes):
        path = getattr(context, "path", None)
        if not path:
            continue  # mounts and websocket-less wrappers carry no HTTP path
        methods = {str(method).upper() for method in (getattr(context, "methods", None) or ())}
        # HEAD is served by GET; folding it keeps one row per client operation.
        if "GET" in methods:
            methods.discard("HEAD")
        endpoint = getattr(context, "endpoint", None)
        dependant = getattr(context, "dependant", None)
        module = getattr(endpoint, "__module__", "") or ""
        framework = endpoint is None or module.startswith(FRAMEWORK_MODULES)
        for method in sorted(methods & set(_HTTP_METHODS)):
            record = RouteRecord(
                path=path,
                method=method,
                name=str(getattr(context, "name", "") or ""),
                module=module,
                endpoint_name=getattr(endpoint, "__name__", "") if endpoint else "",
                dependant=dependant,
                endpoint=endpoint,
                framework=framework,
            )
            records.setdefault(record.key, record)
    return tuple(sorted(records.values(), key=lambda record: (record.path, record.method)))


# --------------------------------------------------------------------------- #
# request parameters
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ParameterClasses:
    """Where a route reads each parameter from, as FastAPI classified it."""

    path: tuple[str, ...] = ()
    query: tuple[str, ...] = ()
    header: tuple[str, ...] = ()
    cookie: tuple[str, ...] = ()
    body: tuple[str, ...] = ()
    #: ``(parameter, field)`` for every field of every Pydantic body model.
    body_fields: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": list(self.path),
            "query": list(self.query),
            "header": list(self.header),
            "cookie": list(self.cookie),
            "body": list(self.body),
            "body_fields": [f"{param}.{name}" for param, name in self.body_fields],
        }


def _names(models: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(getattr(model, "name", "") or "") for model in models)


def _model_fields(parameter: Any) -> tuple[str, ...]:
    """Field names of the Pydantic model bound to a body parameter, if any."""

    annotation = getattr(getattr(parameter, "field_info", None), "annotation", None)
    fields = getattr(annotation, "model_fields", None)
    if fields is None:
        return ()
    return tuple(str(name) for name in fields)


def request_parameter_classes(record: RouteRecord) -> ParameterClasses:
    dependant = record.dependant
    if dependant is None:
        return ParameterClasses()
    body_models = list(getattr(dependant, "body_params", ()) or ())
    body_fields: list[tuple[str, str]] = []
    for parameter in body_models:
        for name in _model_fields(parameter):
            body_fields.append((str(getattr(parameter, "name", "") or ""), name))
    return ParameterClasses(
        path=_names(getattr(dependant, "path_params", ()) or ()),
        query=_names(getattr(dependant, "query_params", ()) or ()),
        header=_names(getattr(dependant, "header_params", ()) or ()),
        cookie=_names(getattr(dependant, "cookie_params", ()) or ()),
        body=_names(body_models),
        body_fields=tuple(body_fields),
    )


# --------------------------------------------------------------------------- #
# dependencies and guards
# --------------------------------------------------------------------------- #

#: Callables that *are* an authorization decision when a route depends on them.
#: The family is closed on purpose: a new sibling has to be added here, which
#: makes "a new kind of authentication" a reviewed edit rather than a silent
#: widening of what counts as protected.
AUTHENTICATION_DEPENDENCIES: frozenset[str] = frozenset({
    # prodavan.api.deps
    "get_principal",
    "get_current_employee",
    "require_platform_admin",
    # prodavan.api.agent_auth
    "get_agent_auth",
    "get_pod_bridge_auth",
    # prodavan.api.v1.external
    "_external_context",
})

#: Dependencies that authenticate *and* hand the handler a resolved tenant.
TENANT_DEPENDENCIES: frozenset[str] = frozenset({
    "resource_tenant",
})

#: Guards a handler may call in its body. Every name is an ``async def`` that
#: raises ``AppError`` on a mismatch; calling one of them is the only thing that
#: turns a client-supplied tenant selector into a validated one.
TENANT_GUARDS: frozenset[str] = frozenset({
    "require_company_actor",
    "ensure_company_principal",
    "require_membership",
    "require_company_admin",
    "require_access",
    "require_project_access",
    "require_bridge_project",
    "require_bridge_pod",
    "resolve_tenant_context",
    "resolve_actor_scope",
    "tenant_or_404_for",
    "tenant_or_404",
    "can_view_project",
    "require_project",
    "require_cabinet",
    # External-app surface: the two module-local guards that compare the
    # project/cabinet against ``_ExternalCtx.app.company_id`` and the app key's
    # cabinet scope before any row is read.
    "_project_visible",
    "_cabinet_visible",
})

#: Names whose presence in a signature means "this callee was given the caller's
#: identity". A callee without one cannot make an authorization decision, so the
#: search does not descend into it — that single rule removes almost all the
#: false positives a plain call graph produces (``_envelope``, ``len``, …).
_IDENTITY_PARAMETERS: frozenset[str] = frozenset({
    "principal",
    "employee",
    "actor",
    "auth",
    "bridge",
    "ctx",
    "identity",
    "claims",
    "current_employee",
})
_IDENTITY_SUFFIXES = ("_principal", "_employee", "_auth", "_actor")


def dependency_names(dependant: Any) -> tuple[str, ...]:
    """Every callable a route depends on, transitively (FastAPI's own order)."""

    names: list[str] = []

    def walk(node: Any) -> None:
        for child in getattr(node, "dependencies", ()) or ():
            call = getattr(child, "call", None)
            if call is not None:
                names.append(getattr(call, "__name__", str(call)))
            walk(child)

    if dependant is not None:
        walk(dependant)
    return tuple(sorted(set(names)))


def is_authenticated(names: Iterable[str]) -> bool:
    return bool(set(names) & AUTHENTICATION_DEPENDENCIES)


# ---- source index ---------------------------------------------------------- #


@dataclass
class _ModuleIndex:
    functions: dict[str, ast.AST] = field(default_factory=dict)
    classes: dict[str, dict[str, ast.AST]] = field(default_factory=dict)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)


class SourceIndex:
    """Parsed ``src/prodavan`` tree, indexed for receiver-aware call following."""

    def __init__(self, root: Path = SRC_ROOT) -> None:
        self.root = root
        self._modules: dict[str, _ModuleIndex] = {}
        for path in sorted(root.rglob("*.py")):
            module = self._module_name(path)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
                continue
            self._modules[module] = self._index(tree)

    def _module_name(self, path: Path) -> str:
        relative = path.relative_to(self.root).with_suffix("")
        parts = list(relative.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(["prodavan", *parts])

    def _index(self, tree: ast.Module) -> _ModuleIndex:
        index = _ModuleIndex()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                index.classes[node.name] = {
                    child.name: child
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index.functions[node.name] = node
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    index.imports[alias.asname or alias.name] = (node.module, alias.name)
        return index

    # -- lookups ------------------------------------------------------- #

    def function(self, module: str, name: str) -> ast.AST | None:
        """A module-level function, or ``"Class.method"`` when the name is qualified."""

        entry = self._modules.get(module)
        if entry is None:
            return None
        cls, dot, method = name.partition(".")
        if dot:
            return entry.classes.get(cls, {}).get(method)
        return entry.functions.get(name)

    def method(self, module: str, cls: str, name: str) -> ast.AST | None:
        entry = self._modules.get(module)
        if entry is None:
            return None
        return entry.classes.get(cls, {}).get(name)

    def resolve_class(self, module: str, name: str | None) -> tuple[str, str] | None:
        """``SomeService`` as written in ``module`` -> ``(defining module, class)``."""

        if not name:
            return None
        entry = self._modules.get(module)
        if entry is not None and name in entry.imports:
            target_module, target_name = entry.imports[name]
            return target_module, target_name
        if entry is not None and name in entry.classes:
            return module, name
        return None

    # -- guard search -------------------------------------------------- #

    def guard_reached(self, record: RouteRecord, *, max_depth: int = 4) -> str | None:
        """Name of the first tenant guard reachable from ``record``'s handler."""

        if not record.framework and record.endpoint_name:
            return self._guard_from(record.module, record.endpoint_name, max_depth, set())
        return None

    def _guard_from(self, module: str, qualname: str, depth: int, seen: set[tuple[str, str]]) -> str | None:
        if depth < 0 or (module, qualname) in seen:
            return None
        seen.add((module, qualname))
        node = self.function(module, qualname)
        if node is None:
            return None
        # ``"Service.method"`` names the class the body runs in, so ``self.other()``
        # can be resolved to ``Service.other`` instead of being dropped.
        owner = qualname.split(".", 1)[0] if "." in qualname else None
        for called, receiver in _callees(node):
            if called in TENANT_GUARDS:
                return called
            target = self._callee_target(module, called, receiver, owner)
            if target is None:
                continue
            target_module, target_name, target_node = target
            if not _takes_identity(target_node):
                # An authorization decision needs the caller's identity; a callee
                # that never receives one is bookkeeping, not a guard.
                continue
            hit = self._guard_from(target_module, target_name, depth - 1, seen)
            if hit:
                return hit
        return None

    def _callee_target(
        self, module: str, called: str, receiver: str | None, owner: str | None
    ) -> tuple[str, str, ast.AST] | None:
        """Where ``called`` is defined, as ``(module, qualified name, node)``.

        A qualified name (``"InstanceService.create_from_base"``) keeps methods
        distinct from same-named module functions, which matters for the cycle
        guard in :meth:`_guard_from`.
        """

        if receiver is not None:
            if receiver == "self" and owner is not None:
                class_module, class_name = module, owner
            else:
                resolved = self.resolve_class(module, receiver)
                if resolved is None:
                    return None
                class_module, class_name = resolved
            method = self.method(class_module, class_name, called)
            if method is None:
                return None
            return class_module, f"{class_name}.{called}", method
        node = self.function(module, called)
        if node is None:
            return None
        return module, called, node


def _callees(node: ast.AST) -> Iterator[tuple[str, str | None]]:
    """``(callee name, receiver class or None)`` for every call inside ``node``.

    The receiver is resolved syntactically and only for the two forms that name
    a class: ``Service(...).method(...)`` and ``Service.method(...)`` with a
    module-level name. Anything else (a local variable, a chained call result)
    yields ``None``, which makes the search fall back to same-module functions —
    conservative in both directions: it never invents a guard and never claims
    one that is not written down.
    """

    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute):
            receiver = func.value
            if isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name):
                yield func.attr, receiver.func.id
            elif isinstance(receiver, ast.Name):
                yield func.attr, receiver.id
            else:
                yield func.attr, None
        elif isinstance(func, ast.Name):
            yield func.id, None


def _parameter_names(node: ast.AST) -> set[str]:
    args = getattr(node, "args", None)
    if args is None:
        return set()
    names = {
        argument.arg
        for argument in (
            *getattr(args, "posonlyargs", ()),
            *args.args,
            *getattr(args, "kwonlyargs", ()),
        )
    }
    return names


def _takes_identity(node: ast.AST) -> bool:
    for name in _parameter_names(node):
        if name in _IDENTITY_PARAMETERS or name.endswith(_IDENTITY_SUFFIXES):
            return True
    return False


__all__ = [
    "AUTHENTICATION_DEPENDENCIES",
    "FRAMEWORK_MODULES",
    "ParameterClasses",
    "RouteRecord",
    "SRC_ROOT",
    "SourceIndex",
    "TENANT_DEPENDENCIES",
    "TENANT_GUARDS",
    "dependency_names",
    "is_authenticated",
    "request_parameter_classes",
    "route_inventory",
]

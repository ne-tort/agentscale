"""Web-side contract extraction from the TypeScript sources.

The browser bundle is not built in this suite, so the client side is read as text.
That is deliberate and bounded: only *names* are extracted (paths, field names,
error codes), never behaviour. Every extractor therefore reports the file it read,
so a failing parity assertion points at a real line rather than at this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from server_contract import API_SRC, WEB_SRC, Call

DYNAMIC_CLIENT = WEB_SRC / "api" / "dynamic.ts"
CONTENT_OPS_SERVICE = WEB_SRC / "features" / "resources" / "content-ops" / "service.ts"
CONTENT_OPS_TYPES = WEB_SRC / "features" / "resources" / "content-ops" / "types.ts"
RESOURCE_CONTRACTS = WEB_SRC / "features" / "resources" / "contracts.ts"
RESOURCE_TYPES = WEB_SRC / "features" / "resources" / "types.ts"
RESOURCE_BLOCKERS = WEB_SRC / "features" / "resources" / "blockers.ts"
RESOURCE_SERVICE = WEB_SRC / "features" / "resources" / "service.ts"
API_CLIENT = WEB_SRC / "api" / "client.ts"
API_TYPES = WEB_SRC / "api" / "types.ts"


def _read(path: Path) -> str:
    if not path.exists():  # pragma: no cover - a moved file is itself the failure
        raise AssertionError(f"web contract source is missing: {path}")
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------------ interfaces


@dataclass(frozen=True, slots=True)
class Interface:
    name: str
    fields: frozenset[str]
    extends: frozenset[str]
    path: Path


_INTERFACE_RE = re.compile(
    r"export\s+interface\s+(?P<name>\w+)(?:\s+extends\s+(?P<extends>[^{]+))?\s*\{(?P<body>.*?)\n\}",
    re.DOTALL,
)
_FIELD_RE = re.compile(r"^\s*readonly\s+(\w+)\??\s*:", re.MULTILINE)


def interfaces(path: Path) -> dict[str, Interface]:
    """Every exported interface in a file, with its own declared field names."""
    text = _read(path)
    found: dict[str, Interface] = {}
    for match in _INTERFACE_RE.finditer(text):
        fields = set(_FIELD_RE.findall(match.group("body")))
        parents = {
            name.strip()
            for name in (match.group("extends") or "").split(",")
            if name.strip()
        }
        found[match.group("name")] = Interface(
            name=match.group("name"),
            fields=frozenset(fields),
            extends=frozenset(parents),
            path=path,
        )
    return found


@lru_cache(maxsize=None)
def _interfaces_cached(path: str) -> dict[str, Interface]:
    return interfaces(Path(path))


def interface(path: Path, name: str) -> Interface:
    found = _interfaces_cached(str(path))
    if name not in found:
        raise AssertionError(f"{name} is not declared in {path}")
    return found[name]


def interface_fields(path: Path, name: str) -> frozenset[str]:
    """Declared fields of an interface *including* everything it extends."""
    found = _interfaces_cached(str(path))
    collected: set[str] = set()
    pending = [name]
    while pending:
        current = pending.pop()
        if current not in found:
            raise AssertionError(f"{current} is not declared in {path}")
        collected |= found[current].fields
        pending.extend(found[current].extends)
    return frozenset(collected)


# ------------------------------------------------------------------ field readers


def _field_alternatives(expression: str) -> tuple[str, ...]:
    """Split ``firstDefined(value.a, value.b)`` / ``value.a ?? value.b`` into names."""
    return tuple(re.findall(r"\b(?:value|raw|envelope)\.(\w+)", expression))


def read_field_aliases(path: Path, function: str) -> dict[str, tuple[str, ...]]:
    """Map each normalized key to every wire name the client will accept for it.

    ``dynamic.ts`` reads snake_case with a camelCase fallback (``firstDefined(value.app_id,
    value.appId)``); the normalizer's own output key is the property on the left of ``:``
    — which may sit on its own line above a multi-line call. Both are collected: the wire
    name must exist on the server, and the normalized name must be what the screens read.
    """
    text = _read(path)
    start = text.find(f"export function {function}")
    if start < 0:
        raise AssertionError(f"{function} is not exported from {path}")
    body = text[start:]
    end = body.find("\n}\n")
    body = body[: end if end > 0 else len(body)]
    # Each ``return { a: ..., b: ..., }`` literal: split on top-level property starts.
    returned = body.find("return {")
    if returned >= 0:
        body = body[returned:]
    aliases: dict[str, tuple[str, ...]] = {}
    for block in re.split(r"\n\s{2,6}(?=[a-zA-Z_]\w*:)", body):
        key = re.match(r"\s*([a-zA-Z_]\w*):", block)
        if not key:
            continue
        names = _field_alternatives(block)
        if names:
            aliases[key.group(1)] = names
    return aliases


# ----------------------------------------------------------------------- endpoints


@dataclass(frozen=True, slots=True)
class RouteBinding:
    """One declared gateway method: how the client reaches it and what it sends."""

    method: str
    http: str
    path: str
    path_identifier: str | None
    query: tuple[str, ...]


_BINDING_BLOCK_RE = re.compile(
    r"export const DYNAMIC_ROUTE_BINDINGS\s*=\s*\[(?P<body>.*?)\n\]\s*as const", re.DOTALL
)
_BINDING_ENTRY_RE = re.compile(r"\{(?P<body>[^{}]*)\}")


def dynamic_route_bindings(path: Path = DYNAMIC_CLIENT) -> list[RouteBinding]:
    """Parse ``DYNAMIC_ROUTE_BINDINGS`` — the client's own wire declaration.

    The client states which HTTP method, path, path identifier and query keys each
    gateway method uses. Declaring it in the client (rather than in this test) keeps
    one source of truth: a screen action that changes shape has to change the
    declaration next to it.
    """
    text = _read(path)
    block = _BINDING_BLOCK_RE.search(text)
    if block is None:
        raise AssertionError(f"{path} does not declare DYNAMIC_ROUTE_BINDINGS")
    bindings: list[RouteBinding] = []
    for entry in _BINDING_ENTRY_RE.finditer(block.group("body")):
        body = entry.group("body")
        method = re.search(r'method:\s*"(\w+)"', body)
        http = re.search(r'http:\s*"(\w+)"', body)
        route = re.search(r'path:\s*"([^"]+)"', body)
        if not (method and http and route):
            continue
        identifier = re.search(r'pathIdentifier:\s*(null|"(\w+)")', body)
        query = re.search(r"query:\s*\[([^\]]*)\]", body)
        bindings.append(
            RouteBinding(
                method=method.group(1),
                http=http.group(1).upper(),
                path=route.group(1),
                path_identifier=(
                    identifier.group(2) if identifier and identifier.group(2) else None
                ),
                query=tuple(re.findall(r'"(\w+)"', query.group(1))) if query else (),
            )
        )
    if not bindings:
        raise AssertionError(f"DYNAMIC_ROUTE_BINDINGS in {path} is empty")
    return bindings


_IDENTIFIER = r"[A-Za-z_$][\w$.]*"


def _normalize_path_template(template: str) -> str:
    """Turn a TypeScript template literal into a server-shaped path template.

    A literal ``/content/assets/${encodeURIComponent(assetId)}/versions`` becomes
    ``/content/assets/{p}/versions``. Query parameters written inside the template
    are dropped: they are covered by the parameter check, not the path check.
    """
    without_query = template.split("?")[0]
    return re.sub(r"\$\{[^}]*\}", "{p}", without_query)


@dataclass(frozen=True, slots=True)
class WebCall:
    call: Call
    template: str


_METHOD_HINT = re.compile(r"method:\s*['\"](\w+)['\"]")
_FETCH_RE = re.compile(r"fetch(?:er)?\(\s*`(?:\$\{)?([A-Za-z_][\w$]*)")


def _mount_prefix() -> str:
    """Recover the URL prefix the content-ops adapter concatenates in front of a literal.

    The service requests ``fetcher(`${baseUrl}${path}`)`` and builds ``baseUrl`` from
    ``options.baseUrl ?? "/api/v1"``, so a literal such as ``/content/assets`` is really
    requested at ``/api/v1/content/assets``. The default is read from the source rather
    than assumed, so a moved default shows up as a parity failure instead of a silent
    pass. A service that builds absolute URLs has no such wrapper and yields ``""``.
    """
    text = _read(CONTENT_OPS_SERVICE)
    mount = _FETCH_RE.search(text)
    if mount is None:
        return ""
    default = re.search(r"options\.baseUrl\s*\?\?\s*\"([^\"]*)\"", text)
    literal = default.group(1) if default else ""
    token = mount.group(1)
    # Only a plain literal initializer replaces the default; a computed one
    # (``configuredBase.endsWith("/") ? … : …``) keeps the documented default.
    plain = re.search(rf"\b{re.escape(token)}\s*(?::\s*[^=]+)?=\s*[\"']([^\"']*)[\"']\s*;", text)
    if plain is not None:
        literal = plain.group(1)
    return literal.rstrip("/")


def content_ops_calls() -> list[WebCall]:
    """Every ``/content/...`` request the content-ops service performs.

    The method is the one declared *after* the path literal, inside the same
    ``init`` object; reads that pass a bare ``{ signal }`` default to ``GET``.
    """
    text = _read(CONTENT_OPS_SERVICE)
    calls: list[WebCall] = []
    for match in re.finditer(r"`(/?content/[^`]*)`", text):
        template = match.group(1)
        following = _METHOD_HINT.search(text[match.start() : match.start() + 400])
        method = following.group(1).upper() if following else "GET"
        base = _mount_prefix()
        calls.append(
            WebCall(
                call=Call(
                    method=method,
                    path=base + _normalize_path_template(template),
                    source=CONTENT_OPS_SERVICE.name,
                ),
                template=template,
            )
        )
    if not calls:
        raise AssertionError(f"no content calls found in {CONTENT_OPS_SERVICE}")
    return calls


# ------------------------------------------------------------------- error handling


def status_keys(path: Path, function: str) -> dict[int, str]:
    """``status -> message`` from a ``Record<number, string>`` message table."""
    text = _read(path)
    start = text.find(f"function {function}")
    if start < 0:
        raise AssertionError(f"{function} is not declared in {path}")
    body = text[start:]
    table = re.search(r"messages[^=]*=\s*\{(?P<body>.*?)\}", body, re.DOTALL)
    if not table:
        raise AssertionError(f"{function} in {path} has no status message table")
    return {
        int(code): message
        for code, message in re.findall(
            r"(\d{3})\s*:\s*\"([^\"]*)\"", table.group("body")
        )
    }


def error_code_literals(path: Path) -> set[str]:
    """Every SCREAMING_SNAKE error code literal the client compares against."""
    return set(re.findall(r"\"([A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+)\"", _read(path)))


def error_code_patterns(path: Path) -> tuple[str, ...]:
    """Regex fragments used to classify a server error code."""
    text = _read(path)
    return tuple(re.findall(r"/\^?\(?([A-Z][A-Z0-9_|]+)\)?\$/", text))


__all__ = [
    "API_CLIENT",
    "API_TYPES",
    "CONTENT_OPS_SERVICE",
    "CONTENT_OPS_TYPES",
    "DYNAMIC_CLIENT",
    "RESOURCE_BLOCKERS",
    "RESOURCE_CONTRACTS",
    "RESOURCE_SERVICE",
    "RESOURCE_TYPES",
    "Interface",
    "RouteBinding",
    "WebCall",
    "content_ops_calls",
    "dynamic_route_bindings",
    "error_code_literals",
    "error_code_patterns",
    "interface",
    "interface_fields",
    "interfaces",
    "read_field_aliases",
    "status_keys",
]

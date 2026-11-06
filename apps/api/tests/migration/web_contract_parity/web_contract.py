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

from .server_contract import API_SRC, WEB_SRC, Call

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


def optional_fields(path: Path, name: str) -> frozenset[str]:
    """Fields declared with ``?``, including those inherited by extension."""
    text = _read(path)
    optional: set[str] = set()
    pending = [name]
    while pending:
        current = pending.pop()
        block = re.search(
            rf"export\s+interface\s+{re.escape(current)}(?:\s+extends\s+([^{{]+))?\s*\{{(.*?)\n\}}",
            text,
            re.DOTALL,
        )
        assert block, f"{current} is not declared in {path}"
        optional |= set(re.findall(r"^\s*readonly\s+(\w+)\?", block.group(2), re.MULTILINE))
        pending.extend(
            part.strip() for part in (block.group(1) or "").split(",") if part.strip()
        )
    return frozenset(optional)


# ------------------------------------------------------------------ field readers


def _field_alternatives(expression: str, body: str = "") -> tuple[str, ...]:
    """Wire names an expression reads, following local aliases.

    ``firstDefined(value.a, value.b)`` and ``value.a ?? value.b`` yield both names.
    With ``body`` supplied the following indirections resolve, which matters because
    the codebase reads a nested object through a local before validating it
    (``const rawScope = value.scope; … rawScope.kind``):

    * a local reader of the wire object (``const capability = readEnvelope(entry, …)``);
    * a local alias of one field (``const rawRoutes = value.routes``);
    * a bare local used as a property value (``id: rowId``, ``grantedAt,``).

    Resolution is transitive up to ``_ALIAS_DEPTH`` steps and never loops on itself.
    """
    roots = ["value", "raw", "envelope"]

    def binding(name: str) -> str:
        return _binding_expression(body, name) if body else ""

    names: list[str] = []
    seen: set[str] = set()

    def collect(text: str, depth: int) -> None:
        if depth > _ALIAS_DEPTH:
            return
        read_roots = list(roots)
        if body:
            read_roots.extend(
                alias
                for alias in re.findall(r"\bconst\s+(\w+)\s*=\s*readEnvelope\(", body)
                if alias not in read_roots
            )
        for name in re.findall(
            r"\b(?:" + "|".join(re.escape(root) for root in read_roots) + r")\.(\w+)", text
        ):
            if name not in seen:
                seen.add(name)
                names.append(name)
        # ``X.attr`` where ``const X = value.Y`` means the wire name is ``Y``.
        for local, field in re.findall(r"\bconst\s+(\w+)\s*=\s*value\.(\w+)", body):
            if re.search(rf"\b{re.escape(local)}\b", text) and field not in seen:
                seen.add(field)
                names.append(field)
        # Bare locals used as values: resolve their own binding recursively.
        for local in re.findall(r"(?::\s*|^\s*)([a-zA-Z_]\w*)\s*[,;]?\s*$", text, re.MULTILINE):
            if local in seen or local in read_roots:
                continue
            seen.add(local)
            inner = binding(local)
            if inner:
                collect(inner, depth + 1)

    collect(expression, 0)
    return tuple(names)


_ALIAS_DEPTH = 4


def _function_body(path: Path, function: str) -> str:
    """Source of one top-level function, from its ``export`` to its closing brace.

    Two traps are handled explicitly:

    * a plain ``find`` would match ``normalizeDefinitionSummary`` when asked for
      ``normalizeDefinition``, so the name is matched as a whole identifier;
    * a TypeScript return-type annotation (``): { scope: … } {``) is itself a balanced
      brace group, so the *body* is the **last** depth-0 brace group of the
      declaration — not the first.
    """
    text = _read(path)
    declared = list(
        re.finditer(r"^(?:export\s+)?function\s+(\w+)\s*\(", text, re.MULTILINE)
    )
    for position, match in enumerate(declared):
        if match.group(1) != function:
            continue
        end = declared[position + 1].start() if position + 1 < len(declared) else len(text)
        return _last_brace_group(text[match.end() : end])
    raise AssertionError(f"{function} is not declared in {path}")


def _last_brace_group(text: str) -> str:
    """Body of the last depth-0 ``{…}`` group in ``text`` (strings are skipped)."""
    groups: list[str] = []
    depth = 0
    opened = -1
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
            continue
        if char == "{":
            if depth == 0:
                opened = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and opened >= 0:
                groups.append(text[opened : index + 1])
                opened = -1
    return groups[-1] if groups else text


def _interpolated_values(body: str) -> set[str]:
    """Local variables a normalizer spreads into its result, resolved one level deep.

    A normalizer that composes a helper (``...normalizeScope(value, field)``,
    ``...scope``) has already read those wire names in the helper; counting them
    here keeps one normalizer from being reported twice.
    """
    names: set[str] = set()
    for expression in re.findall(r"\.\.\.\s*(?:(\w+)|(\w+)\([^)]*\))", body):
        helper = expression[1] or expression[0]
        names.add(helper)
    return names


def read_field_aliases(path: Path, function: str) -> dict[str, tuple[str, ...]]:
    """Map each normalized key to every wire name the client will accept for it.

    ``dynamic.ts`` reads snake_case with a camelCase fallback (``firstDefined(value.app_id,
    value.appId)``); the normalizer's own output key is the property on the left of ``:``.
    Both are collected: the wire name must exist on the server, and the normalized name
    must be what the screens read.

    One rule decides the property list: **the shallowest properties in the function are
    the DTO's own keys**. They own every line indented deeper than themselves. No brace
    or paren counting is involved, so a nested literal (``? { scope: kind } : …``) or a
    one-line spread cannot steal the keys that follow it, and a normalizer whose result
    lives in an inner ``entries.map`` literal still works because its keys are the
    shallowest property lines in that function.

    Two forms add keys without a literal of their own:

    * ``return helper(…)`` / ``...helper(…)`` — the helper's keys are *this* DTO's keys,
      merged in by name;
    * keys written several-to-a-line (``{ scope: kind, projectId: id }``), read from the
      line itself.
    """
    body = _function_body(path, function)
    aliases: dict[str, tuple[str, ...]] = {}

    for key, expression in _properties(body, body):
        names = _field_alternatives(expression, body)
        if names:
            aliases.setdefault(key, names)
    for key, expression in _inline_properties(body, body):
        names = _field_alternatives(expression, body)
        if names:
            aliases.setdefault(key, names)

    for helper in _composed_helpers(path, function, body):
        try:
            nested = read_field_aliases(path, helper)
        except AssertionError:
            continue
        for key, names in nested.items():
            aliases.setdefault(key, names)
    return aliases


def _properties(literal: str, body: str = "") -> list[tuple[str, str]]:
    """``(key, expression)`` for the shallowest property lines, in source order.

    "Shallowest" is what makes this robust: a nested literal and a one-line spread are
    always indented deeper than the keys they sit between, so neither can be mistaken
    for a sibling key. Shorthand properties (``definitionId,``) carry no expression of
    their own; their value is the local binding of the same name, which ``body``
    resolves.
    """
    lines = literal.splitlines()
    candidates: list[tuple[int, str, str]] = []
    index = 0
    while index < len(lines):
        start = re.match(r"^(\s*)(?:readonly\s+)?([a-zA-Z_]\w*)\s*:", lines[index])
        if start:
            indent, key = len(start.group(1)), start.group(2)
            expression = [lines[index]]
            index += 1
            while index < len(lines):
                following = re.match(r"^(\s*)(?:readonly\s+)?([a-zA-Z_]\w*)\s*:", lines[index])
                if following and len(following.group(1)) <= indent:
                    break
                # A shorthand property (``definitionId,``) is a sibling key too, not a
                # continuation of the property above it.
                sibling = re.match(r"^(\s*)([a-zA-Z_]\w*)\s*,\s*$", lines[index])
                if sibling and len(sibling.group(1)) <= indent:
                    break
                expression.append(lines[index])
                index += 1
            candidates.append((indent, key, "\n".join(expression)))
            continue
        shorthand = re.match(r"^(\s*)([a-zA-Z_]\w*)\s*,\s*$", lines[index])
        if shorthand and body:
            binding = _binding_expression(body, shorthand.group(2))
            if binding:
                candidates.append((len(shorthand.group(1)), shorthand.group(2), binding))
        index += 1
    if not candidates:
        return []
    shallowest = min(indent for indent, _, _ in candidates)
    return [(key, expression) for indent, key, expression in candidates if indent == shallowest]


def _binding_expression(body: str, name: str) -> str:
    """The initializer of ``const name = …`` (up to the closing ``;`` at depth 0)."""
    match = re.search(rf"\bconst\s+{re.escape(name)}\s*=\s*", body)
    if match is None:
        return ""
    depth = 0
    for index in range(match.end(), len(body)):
        char = body[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == ";" and depth == 0:
            return body[match.end() : index]
    return body[match.end() :]


def _inline_properties(literal: str, body: str = "") -> list[tuple[str, str]]:
    """``(key, expression)`` for keys that do not start a line.

    Matches ``{ key: …`` (an object literal opened mid-line, which is how a conditional
    spread writes its own keys) and ``key: …`` after a comma inside such a literal. The
    expression runs to the next key on the same line, so ``{ scope: kind, projectId: id }``
    yields both keys with their own values.
    """
    found: list[tuple[str, str]] = []
    for line in literal.splitlines():
        if "{" not in line:
            continue
        starts = list(re.finditer(r"(?:\{|,)\s*([a-zA-Z_]\w*)\s*:", line))
        # Skip the first match when the ``{`` merely opens the returned literal.
        for position, match in enumerate(starts):
            end = starts[position + 1].start() if position + 1 < len(starts) else len(line)
            found.append((match.group(1), line[match.start() : end]))
    return found


def _composed_helpers(path: Path, function: str, body: str) -> set[str]:
    """Local functions whose result *is* this DTO.

    Three shapes qualify, and all of them mean "the object came from that helper":

    * ``return helper(…)`` — the normalizer is a thin alias for the helper
      (``normalizeGrant`` → ``expandGrantRow``);
    * ``...helper(…)`` or ``...variable`` — spread into the returned object
      (``...normalizeScope(value, field)``, or ``...summary`` where ``summary`` is a
      spread of an earlier DTO);
    * ``const x = helper(…)`` where ``x`` is returned or spread
      (``const [first] = expandGrantRow(…)``; ``const summary = normalizeDefinitionSummary(…)``).

    A helper used to normalize a *nested* field (``normalizeRoute(route, …)`` inside
    ``routes.map(…)``) is deliberately excluded: its keys belong to the nested DTO, not
    to the one described here.
    """
    declared = set(re.findall(r"^(?:export\s+)?function\s+(\w+)", _read(path), re.MULTILINE))
    composed = set(_interpolated_values(body))
    for match in re.finditer(r"\breturn\s+(\w+)\(", body):
        composed.add(match.group(1))
    for helper in re.findall(r"\bconst\s*(?:\[[^\]]*\]|\w+)\s*=\s*(\w+)\(", body):
        composed.add(helper)
    # A spread of a variable inherits the helper it was bound to.
    for variable in re.findall(r"\.\.\.\s*(\w+)\b", body):
        binding = re.search(rf"\bconst\s+{re.escape(variable)}\s*=\s*(\w+)\(", body)
        if binding:
            composed.add(binding.group(1))
        else:
            # ``...scope`` where ``scope`` is a helper *call result*: the helper is the
            # one that read those wire names, so its keys are this DTO's keys too.
            composed.add(variable)
    return (composed & declared) - {function}


def _literal_at(body: str, start: int) -> str:
    """Interior of the object literal that starts at the ``{`` on ``start``.

    ``start`` points at the ``return`` keyword; the first ``{`` after it opens the
    literal being returned. The scan is brace-balanced but skips braces inside string
    and template literals, so ``"{}"`` in a fallback expression cannot end it early.
    """
    opened = body.find("{", start)
    if opened < 0:
        return ""
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opened, len(body)):
        char = body[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return body[opened + 1 : index]
    return body[opened + 1 :]


# ----------------------------------------------------------------------- endpoints


@dataclass(frozen=True, slots=True)
class RouteBinding:
    """One declared transport method: how the client reaches it and what it sends."""

    method: str
    http: str
    path: str
    path_identifiers: tuple[str, ...]
    query: tuple[str, ...]


_BINDING_BLOCK_RE = re.compile(
    r"export const DYNAMIC_ROUTE_BINDINGS\s*=\s*\[(?P<body>.*?)\n\]\s*as const", re.DOTALL
)


def dynamic_route_bindings(path: Path = DYNAMIC_CLIENT) -> list[RouteBinding]:
    """Parse ``DYNAMIC_ROUTE_BINDINGS`` — the client's own wire declaration.

    The client states which HTTP method, path, path identifiers and query keys each
    transport method uses. Declaring it in the client (rather than in this test)
    keeps one source of truth: a screen action that changes shape has to change the
    declaration next to it.

    One entry per line is parsed deliberately: paths contain ``{companyId}``, so
    counting balanced braces would confuse a path placeholder with an object
    literal.
    """
    text = _read(path)
    block = _BINDING_BLOCK_RE.search(text)
    if block is None:
        raise AssertionError(f"{path} does not declare DYNAMIC_ROUTE_BINDINGS")
    bindings: list[RouteBinding] = []
    for line in block.group("body").splitlines():
        if "method:" not in line:
            continue
        method = re.search(r'method:\s*"(\w+)"', line)
        http = re.search(r'http:\s*"(\w+)"', line)
        route = re.search(r'path:\s*"([^"]+)"', line)
        if not (method and http and route):
            raise AssertionError(f"malformed DYNAMIC_ROUTE_BINDINGS entry: {line.strip()}")
        identifiers = re.search(r"pathIdentifiers:\s*\[([^\]]*)\]", line)
        query = re.search(r"query:\s*\[([^\]]*)\]", line)
        bindings.append(
            RouteBinding(
                method=method.group(1),
                http=http.group(1).upper(),
                path=route.group(1),
                path_identifiers=(
                    tuple(re.findall(r'"(\w+)"', identifiers.group(1))) if identifiers else ()
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
    "optional_fields",
    "read_field_aliases",
    "status_keys",
]

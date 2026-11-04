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
        found[match.group("name")] = Interface(
            name=match.group("name"), fields=frozenset(fields), path=path
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


# ------------------------------------------------------------------ field readers


def _field_alternatives(expression: str) -> tuple[str, ...]:
    """Split ``firstDefined(value.a, value.b)`` / ``value.a ?? value.b`` into names."""
    return tuple(re.findall(r"\b(?:value|raw|envelope)\.(\w+)", expression))


def read_field_aliases(path: Path, function: str) -> dict[str, tuple[str, ...]]:
    """Map each normalized key to every wire name the client will accept for it.

    ``dynamic.ts`` reads snake_case with a camelCase fallback (``firstDefined(value.app_id,
    value.appId)``); the normalizer's own output key is the property on the left of ``:``.
    Both are collected: the wire name must exist on the server, and the normalized
    name must be what the screens read.
    """
    text = _read(path)
    start = text.find(f"export function {function}")
    if start < 0:
        raise AssertionError(f"{function} is not exported from {path}")
    body = text[start:]
    end = body.find("\n}\n")
    body = body[: end if end > 0 else len(body)]
    aliases: dict[str, tuple[str, ...]] = {}
    for line in body.splitlines():
        key = re.match(r"\s*(?:readonly\s+)?(\w+):", line)
        if not key:
            continue
        names = _field_alternatives(line)
        if names:
            aliases[key.group(1)] = names
    return aliases


# ----------------------------------------------------------------------- endpoints


_IDENTIFIER = r"[A-Za-z_$][\w$.]*"


def _normalize_path_template(template: str) -> str:
    """Turn a TypeScript template literal into a server-shaped path template.

    ``\`/content/assets/${encodeURIComponent(assetId)}/versions\``` becomes
    ``/content/assets/{p}/versions``. Query parameters written inside the template
    are dropped: they are covered by the parameter check, not the path check.
    """
    without_query = template.split("?")[0]
    return re.sub(r"\$\{[^}]*\}", "{p}", without_query)


@lru_cache(maxsize=None)
def _client_passthrough() -> dict[str, str]:
    """Method name -> path template for the shared ``ApiClient`` request wrapper.

    The content-ops service mounts its own ``fetch`` wrapper, so its literals are
    already absolute. Several feature adapters instead delegate to the shared
    client (``request(...)``), and a path typed there is still a path the web
    client calls.
    """
    text = _read(API_CLIENT)
    forwarding = re.search(r"dynamicModules\s*:\s*\{(?P<body>.*?)\n\s*\}", text, re.DOTALL)
    return {"body": forwarding.group("body")} if forwarding else {}


@dataclass(frozen=True, slots=True)
class WebCall:
    call: Call
    template: str


_METHOD_HINT = re.compile(r"method:\s*['\"](\w+)['\"]")


def content_ops_calls() -> list[WebCall]:
    """Every ``/content/...`` request the content-ops service performs."""
    text = _read(CONTENT_OPS_SERVICE)
    calls: list[WebCall] = []
    for match in re.finditer(r"`(/?content/[^`]*)`", text):
        template = match.group(1)
        window = text[max(0, match.start() - 600) : match.start()]
        method_match = _METHOD_HINT.findall(window)
        method = method_match[-1].upper() if method_match else "GET"
        calls.append(
            WebCall(
                call=Call(
                    method=method,
                    path=_normalize_path_template(template),
                    source=f"{CONTENT_OPS_SERVICE.name}",
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
    "WebCall",
    "content_ops_calls",
    "error_code_literals",
    "error_code_patterns",
    "interface",
    "interfaces",
    "read_field_aliases",
    "status_keys",
]

"""First-party Pod MCP: module meta + data over Bridge JWT (:8001).

Stdio JSON-RPC (MCP tools/list + tools/call). Env:
  PRODAVAN_API_BASE_URL, PRODAVAN_AUTH_TOKEN, PRODAVAN_PROJECT_ID
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

def _env() -> tuple[str, str, str]:
    api = (os.environ.get("PRODAVAN_API_BASE_URL") or "").rstrip("/")
    token = os.environ.get("PRODAVAN_AUTH_TOKEN") or os.environ.get("BRIDGE_AUTH_TOKEN") or ""
    project_id = os.environ.get("PRODAVAN_PROJECT_ID") or os.environ.get("PROJECT_ID") or ""
    return api, token, project_id


def _session_id(arguments: dict[str, Any] | None = None) -> str | None:
    if arguments:
        raw = arguments.get("session_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    env = (os.environ.get("PRODAVAN_SESSION_ID") or "").strip()
    return env or None


TOOLS: list[dict[str, Any]] = [
    {
        "name": "modules_list",
        "description": "List modules bound to this project (bind_kind, meta/data writable).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "module_meta_list",
        "description": "List meta document slugs for a module instance.",
        "inputSchema": {
            "type": "object",
            "properties": {"module_id": {"type": "string"}},
            "required": ["module_id"],
        },
    },
    {
        "name": "module_meta_get",
        "description": "Get one module meta document body by slug (tables, columns, views, …).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string"},
                "slug": {"type": "string"},
            },
            "required": ["module_id", "slug"],
        },
    },
    {
        "name": "module_meta_put",
        "description": "Replace a module meta document on the project SoT instance (local copy).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string"},
                "slug": {"type": "string"},
                "body": {},
            },
            "required": ["module_id", "slug", "body"],
        },
    },
    {
        "name": "module_data_list",
        "description": "List rows in a module table. Pass session_id for chat-scoped tables.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string"},
                "table_slug": {"type": "string"},
                "session_id": {
                    "type": "string",
                    "description": "Agent session id (required for scope.chats=current tables)",
                },
            },
            "required": ["module_id", "table_slug"],
        },
    },
    {
        "name": "module_data_create",
        "description": "Create a row in a module table. Pass session_id for chat-scoped tables.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string"},
                "table_slug": {"type": "string"},
                "body": {"type": "object"},
                "session_id": {"type": "string"},
            },
            "required": ["module_id", "table_slug", "body"],
        },
    },
    {
        "name": "module_data_update",
        "description": "Update a module data row. Pass session_id for chat-scoped tables.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string"},
                "table_slug": {"type": "string"},
                "row_id": {"type": "string"},
                "body": {"type": "object"},
                "session_id": {"type": "string"},
            },
            "required": ["module_id", "table_slug", "row_id", "body"],
        },
    },
    {
        "name": "module_data_delete",
        "description": "Delete a module data row.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string"},
                "table_slug": {"type": "string"},
                "row_id": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["module_id", "table_slug", "row_id"],
        },
    },
    {
        "name": "module_action_invoke",
        "description": "Invoke a declarative module action (e.g. probe/index).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string"},
                "action_id": {"type": "string"},
                "row_id": {"type": "string"},
            },
            "required": ["module_id", "action_id"],
        },
    },
]


def _http(
    method: str,
    path: str,
    payload: Any | None = None,
    *,
    session_id: str | None = None,
) -> Any:
    api_base, token, _project_id = _env()
    if not api_base or not token or not _project_id:
        raise RuntimeError(
            "PRODAVAN_API_BASE_URL, PRODAVAN_AUTH_TOKEN, and PRODAVAN_PROJECT_ID are required"
        )
    url = f"{api_base}{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    sid = session_id or _session_id()
    if sid:
        headers["X-Prodavan-Session-Id"] = sid
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    _api, _tok, project_id = _env()
    mid = str(arguments.get("module_id") or "")
    sid = _session_id(arguments)
    if name == "modules_list":
        return _http("GET", f"/projects/{project_id}/modules")
    if name == "module_meta_list":
        return _http("GET", f"/projects/{project_id}/modules/{mid}/meta/documents")
    if name == "module_meta_get":
        slug = str(arguments.get("slug") or "")
        return _http("GET", f"/projects/{project_id}/modules/{mid}/meta/documents/{slug}")
    if name == "module_meta_put":
        slug = str(arguments.get("slug") or "")
        return _http(
            "PUT",
            f"/projects/{project_id}/modules/{mid}/meta/documents/{slug}",
            {"body": arguments.get("body")},
        )
    if name == "module_data_list":
        table = str(arguments.get("table_slug") or "")
        return _http(
            "GET",
            f"/projects/{project_id}/modules/{mid}/data/{table}",
            session_id=sid,
        )
    if name == "module_data_create":
        table = str(arguments.get("table_slug") or "")
        return _http(
            "POST",
            f"/projects/{project_id}/modules/{mid}/data/{table}",
            {"body": arguments.get("body") or {}},
            session_id=sid,
        )
    if name == "module_data_update":
        table = str(arguments.get("table_slug") or "")
        row_id = str(arguments.get("row_id") or "")
        return _http(
            "PATCH",
            f"/projects/{project_id}/modules/{mid}/data/{table}/{row_id}",
            {"body": arguments.get("body") or {}},
            session_id=sid,
        )
    if name == "module_data_delete":
        table = str(arguments.get("table_slug") or "")
        row_id = str(arguments.get("row_id") or "")
        return _http(
            "DELETE",
            f"/projects/{project_id}/modules/{mid}/data/{table}/{row_id}",
            session_id=sid,
        )
    if name == "module_action_invoke":
        action_id = str(arguments.get("action_id") or "")
        body: dict[str, Any] = {}
        if arguments.get("row_id"):
            body["row_id"] = arguments["row_id"]
        return _http(
            "POST",
            f"/projects/{project_id}/modules/{mid}/actions/{action_id}/invoke",
            body or None,
            session_id=sid,
        )
    raise RuntimeError(f"unknown tool: {name}")


def _result_text(payload: Any) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}]}


def _handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "prodavan-modules", "version": "1.0.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            out = _call_tool(name, arguments)
            return {"jsonrpc": "2.0", "id": mid, "result": _result_text(out)}
        except Exception as exc:  # noqa: BLE001 — surface to MCP client
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        resp = _handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

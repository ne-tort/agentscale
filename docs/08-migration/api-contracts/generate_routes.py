from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2]
ROOT = DOCS.parent
API = ROOT / "apps/api/src/prodavan/api"
V1 = API / "v1"
OUT = Path(__file__).with_name("routes-current.md")

FAMILY = {
    "health": "ops health", "stub": "stub", "auth": "authentication", "identity": "identity and employees",
    "company_ai_keys": "company AI keys", "company_ai_models": "company AI models",
    "company_containers": "company containers/workspace", "company_modules": "company-owned modules",
    "content": "content assets, versions, aliases and ACL", "documents": "Pod document tools",
    "admin_companies": "platform and company administration", "admin_profile": "platform profile",
    "admin_metrics": "platform metrics", "admin_triggers": "platform trigger operations",
    "admin_platform_events": "platform events", "admin_projects": "platform project operations",
    "admin_containers": "platform containers/workspace", "admin_cabinets": "platform cabinet administration",
    "admin_recycle": "recycle bin", "admin_modules": "platform-owned modules",
    "admin_object_store": "platform object store operations", "admin_document_store": "platform document store operations",
    "admin_search_index": "platform search index operations", "admin_catalogs": "platform reference catalogs",
    "ai_keys": "platform AI keys", "admin_ai_models": "platform AI model catalog",
    "cabinets": "employee/company cabinet scope", "projects": "employee project scope",
    "tenant_infra": "Pod runtime bridge infra", "employee_search": "employee project search",
    "pod_modules": "Pod runtime module bridge", "agent": "agent sessions, chat and events",
    "agent_runs": "durable agent runs, approvals and event timeline",
    "b2b": "B2B tender compatibility", "external": "app-key API, employee webhooks and incoming integrations",
}
PUBLIC_HANDLERS = {
    "auth_config", "auth_health", "auth_login", "auth_refresh", "auth_logout", "auth_broker_start", "auth_broker_callback",
    "health_check", "liveness", "readiness", "stub_info",
}
RAW_REQUEST_HANDLERS = {
    "upload_asset_version", "upload_project_workspace_content", "upload_company_workspace_content", "upload_admin_workspace_content",
    "ingress_signed_webhook", "ingress_signed_telegram",
}
RESPONSE_HANDLERS = {
    "download_asset", "resolve_alias", "download_attachment_content", "download_project_workspace_content",
    "download_company_workspace_content", "download_admin_workspace_content", "objects_get", "download_pod_workspace_archive",
}


def parse(file: Path) -> ast.Module:
    return ast.parse(file.read_text(encoding="utf-8"))


def assigned_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0] if len(node.targets) == 1 else None
    return target.id if isinstance(target, ast.Name) else None


def module_symbols(tree: ast.Module) -> dict[str, str]:
    result = {"SESSION_HEADER": "X-Prodavan-Session-Id", "_IDEMPOTENCY_HEADER": "Idempotency-Key"}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            name = assigned_name(node)
            value = node.value
            if name and isinstance(value, ast.Constant) and isinstance(value.value, str):
                result[name] = value.value
    return result


def defaults_for(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, ast.expr]:
    positional = list(fn.args.args)
    keyword_only = list(fn.args.kwonlyargs)
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(fn.args.defaults)) + list(fn.args.defaults)
    defaults += list(fn.args.kw_defaults)
    return {arg.arg: value for arg, value in zip(positional + keyword_only, defaults) if value is not None}


def calls_in(text: str) -> list[ast.Call]:
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return []
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def query_value(node: ast.expr) -> str:
    try:
        value = ast.literal_eval(node)
        return repr(value)
    except (ValueError, TypeError):
        return ast.unparse(node)


def parameters(fn: ast.FunctionDef | ast.AsyncFunctionDef, path: str, symbols: dict[str, str]) -> tuple[str, str, str]:
    path_vars = set(re.findall(r"\{([^}:]+)(?::[^}]+)?\}", path))
    excluded = {"self", "cls", "request", "principal", "session", "employee", "admin", "actor", "ctx", "bridge", "auth", "_", "__", "_admin", "principal_admin", "pod_agent"}
    body: list[str] = []
    query: list[str] = []
    headers: list[str] = []
    defaults = defaults_for(fn)
    for arg in list(fn.args.args) + list(fn.args.kwonlyargs):
        name = arg.arg
        ann = ast.unparse(arg.annotation) if arg.annotation else ""
        default_node = defaults.get(name)
        calls = calls_in(ann) + ([node for node in ast.walk(default_node) if isinstance(node, ast.Call)] if default_node else [])
        if name in excluded or name in path_vars:
            if name == "request" and fn.name in RAW_REQUEST_HANDLERS:
                body.append("raw request bytes")
            continue
        if "UploadFile" in ann or any(isinstance(c.func, ast.Name) and c.func.id == "File" for c in calls):
            body.append("multipart/form-data: file")
            continue
        header_call = next((c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "Header"), None)
        if header_call:
            alias = next((kw.value for kw in header_call.keywords if kw.arg == "alias"), None)
            if alias is not None:
                alias_name = ast.unparse(alias)
                header = symbols.get(alias_name, ast.literal_eval(alias) if isinstance(alias, ast.Constant) else alias_name)
            else:
                header = name.replace("_", "-").title()
            headers.append(header)
            continue
        query_call = next((c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "Query"), None)
        if query_call:
            value = next((kw.value for kw in query_call.keywords if kw.arg == "default"), None)
            if value is None:
                value = next((arg for arg in query_call.args[:1]), None)
            if value is None:
                suffix = " (required)"
            elif isinstance(value, ast.Constant) and value.value is None:
                suffix = ""
            else:
                suffix = f"={query_value(value)}"
            query.append(name + suffix)
            continue
        if any(isinstance(c.func, ast.Name) and c.func.id == "Body" for c in calls):
            body.append(ann or "JSON body")
            continue
        if any(isinstance(c.func, ast.Name) and c.func.id == "File" for c in calls):
            body.append("multipart/form-data: file")
            continue
        if "Depends(" in ann or ann.endswith("Dep"):
            continue
        if "Annotated[" in ann:
            continue
        if name == "body" or (ann and re.search(r"[A-Za-z]+Body(?: \| None)?$", ann)):
            body.append(ann or "JSON body")
            continue
        if ann:
            if default_node:
                query.append(name + "=" + query_value(default_node))
            else:
                query.append(name)
    return ("; ".join(body) or "-", ", ".join(query) or "-", ", ".join(headers) or "-")


def auth_class(fn: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> str:
    if fn.name in PUBLIC_HANDLERS:
        return "public"
    if fn.name in {"ingress_signed_webhook", "ingress_signed_telegram"}:
        return "HMAC X-Prodavan-Signature; rate limited + active project/secret"
    args = [ast.unparse(arg.annotation) for arg in list(fn.args.args) + list(fn.args.kwonlyargs) if arg.annotation]
    combined = " ".join(args) + " " + " ".join(ast.unparse(value) for value in defaults_for(fn).values())
    if "PlatformAdminDep" in combined:
        return "Bearer + platform.admin"
    if "ExternalDep" in combined or "_ExternalCtx" in combined:
        return "Bearer app key; scope + company/cabinet/project ownership"
    if "PodBridgeDep" in combined:
        return "scoped Pod-Bridge JWT; project/pod and route scopes"
    if "AgentAuthDep" in combined or "get_agent_auth" in source:
        return "route-specific OIDC or scoped Pod-Bridge JWT; inspect AgentAuth handler"
    if "PrincipalDep" in combined or "EmployeeDep" in combined or "_EmployeeDep" in combined or "get_current_employee" in combined:
        return "Bearer OIDC principal; role/tenant policy varies"
    if "WorkContextDep" in combined:
        return "Bearer OIDC principal + optional X-Cabinet-Id/X-Project-Id"
    return "public/probe or route-specific signature; see source"


def status_code(fn: ast.FunctionDef | ast.AsyncFunctionDef, decorator: ast.Call) -> str:
    value = next((kw.value for kw in decorator.keywords if kw.arg == "status_code"), None)
    if value is not None:
        return ast.unparse(value)
    if fn.name in {"auth_broker_start", "download_asset", "resolve_alias"}:
        return "200/302 (handler-dependent)"
    if fn.name == "auth_broker_callback":
        return "302 (success; error responses vary)"
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            call = node.value
            callname = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else ""
            if callname in {"Response", "RedirectResponse", "JSONResponse"}:
                result = next((kw.value.value for kw in call.keywords if kw.arg == "status_code" and isinstance(kw.value, ast.Constant)), None)
                if result is not None:
                    return str(result)
    return "200"


def response_type(fn: ast.FunctionDef | ast.AsyncFunctionDef, status: str) -> str:
    ret = ast.unparse(fn.returns) if fn.returns else "dict / JSON (unannotated)"
    if fn.name == "project_chat_stream" or "StreamingResponse" in ret:
        return "SSE: text/event-stream"
    if status == "204":
        return "empty (204)"
    if "302" in status:
        return "redirect/JSON/bytes; handler-dependent"
    if fn.name in RESPONSE_HANDLERS or "Response" in ret:
        if fn.name in {"upload_project_workspace_content", "upload_company_workspace_content", "upload_admin_workspace_content"}:
            return "JSON mutation result"
        return "raw Response (binary/redirect; inspect handler for media/headers)"
    if fn.name == "upload_asset_version":
        return "JSON mutation result"
    return "JSON: " + ret.replace("|", "\\|")


def scan_module(file: Path, router_var: str, api_prefix: str = "/api/v1") -> list[dict[str, str]]:
    tree = parse(file)
    prefix = ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and assigned_name(node) == router_var and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "APIRouter":
            prefix_node = next((kw.value for kw in node.value.keywords if kw.arg == "prefix"), None)
            if isinstance(prefix_node, ast.Constant) and isinstance(prefix_node.value, str):
                prefix = prefix_node.value
    symbols = module_symbols(tree)
    source = file.read_text(encoding="utf-8")
    rows: list[dict[str, str]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in fn.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute) or not isinstance(dec.func.value, ast.Name) or dec.func.value.id != router_var:
                continue
            method = dec.func.attr.upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            route = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else ""
            middle = "/".join(part.strip("/") for part in (prefix, route) if part.strip("/"))
            path = api_prefix + ("/" + middle if middle else "")
            status = status_code(fn, dec)
            body, query, headers = parameters(fn, path, symbols)
            rows.append({
                "method": method, "path": path, "body": body, "query": query, "headers": headers,
                "status": status, "response": response_type(fn, status), "auth": auth_class(fn, source),
                "source": f"`{file.relative_to(ROOT).as_posix()}:{fn.lineno}`", "owner": router_var,
            })
    return rows


router_tree = parse(V1 / "router.py")
mounted: list[tuple[str, str]] = []
for node in router_tree.body:
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        continue
    call = node.value
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "include_router" or not call.args:
        continue
    arg = call.args[0]
    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
        mounted.append((arg.value.id, arg.attr))

routes: list[dict[str, str]] = []
for mod, var in mounted:
    file = V1 / f"{mod}.py"
    if not file.exists():
        continue
    for row in scan_module(file, var):
        row["owner"] = mod if var == "router" else f"{mod}.{var}"
        row["family"] = FAMILY.get(mod, mod)
        routes.append(row)
internal_rows = scan_module(API / "internal/pods.py", "router")
for row in internal_rows:
    row["owner"] = "internal.pods"
    row["family"] = "private Pod credential/workspace bridge"
    row["auth"] = "scoped Pod-Bridge JWT; internal credential/hydrate scope"
routes.extend(internal_rows)

root_health = [("GET", "/health", "health_check"), ("GET", "/health/live", "liveness"), ("GET", "/health/ready", "readiness")]
counts = Counter(row["owner"] for row in routes)
lines = [
    "# Live FastAPI route inventory", "",
    "Статический inventory live decorators + `include_router`, baseline 2026-11-02. Источник регистрации — `apps/api/src/prodavan/main.py`, `api/v1/router.py`; prefix `/api/v1` и реальные `APIRouter(prefix=...)`. Source file:line указан у каждой строки.", "",
    f"- Main app: **{len(routes)} registered `/api/v1` operations**, including **{len(internal_rows)} `/internal/pods/*`** operations mounted in main app under separate Pod-Bridge scopes.",
    f"- Additional root probes: **{len(root_health)}** (`GET /health`, `/health/live`, `/health/ready`); health handlers also mount under `/api/v1`.",
    "- `main_pod.py` is a separate Pod-only listener/application; it remounts a reduced subset and is not a browser origin. See README for boundary notes.",
    "- Registered route collision: both `projects.router` and `pod_modules.router` declare `GET /api/v1/projects/{project_id}/modules`. The public app includes the OIDC project-settings router first; Starlette dispatches the first full path/method match, then its `PrincipalDep` rejects a Pod Bridge JWT instead of reaching the later bridge handler. `main_pod.py` mounts `pod_modules` without `projects.router`, so the Pod-only listener has no duplicate.",
    "- Success status/response reflects declared decorator and handler return. JSON dicts are dynamic unless a response model is shown. Transport-specific bytes/SSE/multipart notes are in README.",
    "", "| Family | Registered operations |", "|---|---:|",
]
for owner, count in sorted(counts.items()):
    family = FAMILY.get(owner.split(".")[0], "private Pod credential/workspace bridge" if owner == "internal.pods" else owner)
    lines.append(f"| {family} (`{owner}`) | {count} |")
lines += ["", "## Registered operations", "", "| Method | Live path | Router owner | Body / media | Query | Required non-body headers | Success / response | Auth boundary | Source |", "|---|---|---|---|---|---|---|---|"]
for row in routes:
    values = [row["method"], row["path"], row["owner"], row["body"], row["query"], row["headers"]]
    values.extend([row["status"] + " / " + row["response"], row["auth"], row["source"]])
    lines.append("| " + " | ".join(v.replace("|", "\\|").replace("\n", " ") for v in values) + " |")
lines += ["", "## Root health registrations", "", "| Method | Path | Response | Source |", "|---|---|---|---|"]
for method, path, handler in root_health:
    file = V1 / "health.py"
    tree = parse(file)
    line = next(n.lineno for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == handler)
    lines.append(f"| {method} | `{path}` | JSON (same handler as `/api/v1{path}`) | `{file.relative_to(ROOT).as_posix()}:{line}` |")
lines += ["", "## Parsing notes", "", "- Source-based static extraction of route decorators and registration calls. Dependency arguments are not request inputs; auth is summarized in its own column.", "- Query defaults are recorded where declared. Pydantic request models are named, not expanded; inspect source schemas for exact fields.", "- Response body envelopes are not inferred for dynamic dict returns. Review handler/service for exact shape."]
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote {OUT} ({len(routes)} /api/v1 operations, {len(root_health)} root probes, {len(mounted)} mounted routers)")
print("counts", dict(sorted(counts.items())))

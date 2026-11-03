// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, matchPath, useLocation, useNavigate } from "react-router-dom";
import { AppRouter, EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS } from "./router";
import { AppProviders } from "./context";
import { AUTH_STORAGE_KEY } from "./auth/lifecycle";
import { getSafeNextPath } from "./auth/redirect";
import { RESOURCE_CONTRACT_BLOCKERS } from "../features/resources";
import { emptyEquipmentWorkspace } from "../features/equipment";
import { filesModuleFixture, promptModuleFixture } from "../features/modules/fixtures";

const projectId = "project-71";
const cabinetId = "cabinet-19";
const sessionId = "session-8";
const employee = { id: "employee-4", display_name: "Ada Lovelace" };
const tokens = { access_token: "acceptance-access", refresh_token: "acceptance-refresh" };
const project = {
  id: projectId,
  cabinet_id: cabinetId,
  company_id: "company-2",
  name: "Signal Lab",
  about: "Inspect workspace artifacts",
  status: "active",
  selected: true,
  new_chat_enabled: true,
  observed_state: "running",
};
const secondProjectId = "project-72";
const secondProject = {
  ...project,
  id: secondProjectId,
  name: "Archive Lab",
  about: "A separate workspace",
  selected: false,
};
const session = {
  id: sessionId,
  project_id: projectId,
  status: "active",
  title: "Deployment review",
  provider: "test-provider",
  model: "test-model",
  created_at: "2026-10-31T09:00:00Z",
};

const resourceModuleRecords = [
  { id: "mod_prompts", module_id: "mod_prompts", name: "Prompts", instance_id: "prompt-instance", bind_kind: "global", child_may_edit: true },
  { id: "mod_files", module_id: "mod_files", name: "Files", instance_id: "files-instance", bind_kind: "local", child_may_edit: true },
  { id: "mod_mcp", module_id: "mod_mcp", name: "MCP", instance_id: "mcp-instance", bind_kind: "global", child_may_edit: false },
];

const initialPromptPathFiles: Record<string, unknown>[] = [{ id: "tone", name: "tone.md", priority: 100, body_md: "Be precise." }];
let promptPathFiles: Record<string, unknown>[] = initialPromptPathFiles.map((file) => ({ ...file }));
let resourceFileRow: { row_id: string; body: Record<string, unknown> } | undefined;

function resourceModuleResponse(url: URL, request: CapturedRequest): Response | undefined {
  if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules` && request.method === "GET") return jsonResponse({ items: resourceModuleRecords });
  for (const [moduleId, fixture] of [["mod_prompts", promptModuleFixture], ["mod_files", filesModuleFixture]] as const) {
    for (const slug of ["tables", "columns", "views", "tabs", "actions"] as const) {
      if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/${moduleId}/meta/documents/${slug}`) {
        const document = fixture.documents[slug];
        if (moduleId === "mod_prompts" && slug === "views") {
          const views = (document as { slug: string; body: Array<{ slug: string; ui_json?: Record<string, unknown> }> }).body.map((view) => view.slug !== "prompt_path_settings" ? view : {
            ...view,
            ui_json: {
              ...view.ui_json,
              fields: [...(Array.isArray(view.ui_json?.fields) ? view.ui_json.fields : []), { column: "files_json", widget: "prompt_files_editor" }],
            },
          });
          return jsonResponse({ slug: "views", body: views });
        }
        return jsonResponse(document);
      }
    }
  }
  if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_mcp/meta/documents/tables`) return jsonResponse({ slug: "tables", body: [{ slug: "mcp_packages", enabled: true, storage_kind: "json_document", scope: { projects: "all", chats: "all" } }] });
  if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_mcp/meta/documents/columns`) return jsonResponse({ slug: "columns", body: [
    { table_slug: "mcp_packages", name: "name", type: "text", required: true }, { table_slug: "mcp_packages", name: "version", type: "text", required: true },
    { table_slug: "mcp_packages", name: "enabled", type: "bool" }, { table_slug: "mcp_packages", name: "file_ref", type: "file_ref" }, { table_slug: "mcp_packages", name: "project_ids", type: "json" },
  ] });
  if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_mcp/meta/documents/views`) return jsonResponse({ slug: "views", body: [
    { slug: "mcp_packages_list", table_slug: "mcp_packages", kind: "collection", ui_json: { version: 1, kind: "collection", title: "MCP", columns: [{ field: "name" }, { field: "version" }], row_tap: { kind: "open_form", view: "mcp_packages_form" }, inline_add: { field: "name", title: "Add MCP package" } } },
    { slug: "mcp_packages_form", table_slug: "mcp_packages", kind: "form", ui_json: { version: 1, kind: "form", title: "MCP package", fields: [{ column: "project_ids", widget: "project_multiselect" }, { column: "name" }, { column: "version" }, { column: "enabled", widget: "switch" }, { column: "file_ref", widget: "file_upload", accept: ".zip" }] } },
  ] });
  if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_mcp/meta/documents/tabs`) return jsonResponse({ slug: "tabs", body: [{ id: "tab_mcp", title: "MCP", view_slug: "mcp_packages_list", enabled: true, nav: { contour: "employee", placement: "management" } }] });
  if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_mcp/meta/documents/actions`) return jsonResponse({ slug: "actions", body: [] });
  if (request.method === "GET" && url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_prompts/data/prompt_profiles`) return jsonResponse({ items: [
    { row_id: "profile_default", body: { name: "Default", is_default: true, project_ids: [] } },
    { row_id: "profile_research", body: { name: "Research", is_default: false, project_ids: [] } },
    { row_id: "profile_other_project", body: { name: "Private profile", is_default: false, project_ids: ["project-other"] } },
  ] });
  if (request.method === "GET" && url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_prompts/data/prompt_paths`) return jsonResponse({ items: [
    { row_id: "path_agents", body: { profile_id: "profile_default", name: "AGENTS.md", path: "", files_json: promptPathFiles } },
    { row_id: "path_research", body: { profile_id: "profile_research", name: "Research", path: "prompts/research", files_json: [] } },
    { row_id: "path_private", body: { profile_id: "profile_default", name: "Private path", path: "private", project_ids: ["project-other"], files_json: [] } },
  ] });
  if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_prompts/data/prompt_paths/path_private` && request.method === "PATCH") return jsonResponse({ code: "NOT_FOUND", title: "Not Found", detail: "row not found" }, 404);
  if (request.method === "GET" && url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_files/data/files`) return jsonResponse({ items: resourceFileRow ? [{ ...resourceFileRow, module_id: "mod_files", instance_id: "files-instance" }] : [] });
  if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_files/data/files` && request.method === "POST") {
    const body = JSON.parse(request.body ?? "{}").body as Record<string, unknown>;
    resourceFileRow = { row_id: "resource-file-1", body };
    return jsonResponse({ ...resourceFileRow, module_id: "mod_files", instance_id: "files-instance" });
  }
  if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_files/data/files/resource-file-1` && request.method === "PATCH") {
    const patch = JSON.parse(request.body ?? "{}").body as Record<string, unknown>;
    resourceFileRow = { row_id: "resource-file-1", body: { ...(resourceFileRow?.body ?? {}), ...patch } };
    return jsonResponse({ ...resourceFileRow, module_id: "mod_files", instance_id: "files-instance" });
  }
  if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_files/data/files/resource-file-1` && request.method === "DELETE") {
    resourceFileRow = undefined;
    return jsonResponse({ deleted: true, row_id: "resource-file-1" });
  }
  if (request.method === "GET" && url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_mcp/data/mcp_packages`) return jsonResponse({ items: [] });
  if (url.pathname === `/api/v1/cabinets/${cabinetId}/modules/mod_files/bound-projects` || url.pathname === `/api/v1/cabinets/${cabinetId}/modules/mod_mcp/bound-projects`) return jsonResponse({ items: [{ project_id: projectId, name: project.name }] });
  if (url.pathname === `/api/v1/cabinets/${cabinetId}/content/upload` && request.method === "POST") return jsonResponse({ file_ref: { asset_id: "uploaded-asset", blob_version_id: "uploaded-version", filename: "guide.txt", size: 9 } });
  if (url.pathname === "/api/v1/content/assets/uploaded-asset/download" && request.method === "GET") return { ...jsonResponse({}, 200), headers: new Headers({ "Content-Type": "text/plain", "Content-Disposition": 'attachment; filename="guide.txt"' }), blob: async () => new Blob(["new guide"], { type: "text/plain" }) } as Response;
  return undefined;
}

interface CapturedRequest {
  method: string;
  path: string;
  body?: string;
  binaryBody?: Blob;
  contentType?: string;
  authorization?: string;
}

function jsonResponse(payload: unknown, status = 200): Response {
  const text = JSON.stringify(payload);
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status >= 200 && status < 300 ? "OK" : "Request failed",
    headers: new Headers({ "Content-Type": "application/json" }),
    json: async () => payload,
    text: async () => text,
    blob: async () => new Blob(),
  } as Response;
}

function backendIdentityResponse() {
  return { sub: "keycloak-subject-4", email: "ada@example.test", roles: [], contours: ["employee"], active: true, company: null, employee: { id: employee.id, status: "active", email: "ada@example.test", contact_email: null, memberships: [{ company_id: "company-2", company_name: "Northwind", role: "member" }] }, work_context: { cabinet_id: cabinetId, project_id: projectId } };
}

// Match only the routes mounted through api/v1/router.py from identity.py, cabinets.py, and projects.py.
const registeredFastApiContracts = [
  { method: "GET", template: "/api/v1/me" },
  { method: "GET", template: "/api/v1/cabinets" },
  { method: "GET", template: "/api/v1/cabinets/{cabinet_id}/projects" },
  { method: "GET", template: "/api/v1/cabinets/{cabinet_id}/me/selection" },
  { method: "PUT", template: "/api/v1/cabinets/{cabinet_id}/me/selection" },
  { method: "GET", template: "/api/v1/cabinets/{cabinet_id}" },
  { method: "GET", template: "/api/v1/cabinets/{cabinet_id}/chats/sidebar" },
  { method: "GET", template: "/api/v1/projects/{project_id}" },
  { method: "GET", template: "/api/v1/projects/{project_id}/container" },
  { method: "GET", template: "/api/v1/projects/{project_id}/container/metrics" },
  { method: "GET", template: "/api/v1/projects/{project_id}/container/workspace/entries" },
  { method: "GET", template: "/api/v1/projects/{project_id}/container/workspace/preview" },
  { method: "GET", template: "/api/v1/projects/{project_id}/container/workspace/content" },
  { method: "PUT", template: "/api/v1/projects/{project_id}/container/workspace/content" },
  { method: "POST", template: "/api/v1/projects/{project_id}/container/workspace/mkdir" },
  { method: "GET", template: "/api/v1/projects/{project_id}/attachments" },
  { method: "GET", template: "/api/v1/projects/{project_id}/ai-keys/available" },
  { method: "GET", template: "/api/v1/projects/{project_id}/models/live" },
] as const;

function matchesFastApiPath(template: string, pathname: string): boolean {
  const segments = template.split("/").map((segment) => segment.startsWith("{") && segment.endsWith("}") ? "[^/]+" : segment);
  return new RegExp(`^${segments.join("/")}$`).test(pathname);
}

function captureRegisteredApi(handler: (url: URL, request: CapturedRequest) => Promise<Response> | Response) {
  return capture((url, request) => {
    const registered = registeredFastApiContracts.some((contract) => contract.method === request.method
      && matchesFastApiPath(contract.template, url.pathname));
    if (!registered) throw new Error(`No registered FastAPI contract for ${request.method} ${url.pathname}`);
    if (request.authorization !== "Bearer acceptance-access") throw new Error(`Request was not authenticated: ${request.method} ${url.pathname}`);
    return handler(url, request);
  });
}

async function waitFor(assertion: () => boolean, message = "Timed out waiting for the acceptance condition"): Promise<void> {
  const deadline = Date.now() + 3000;
  while (!assertion()) {
    if (Date.now() >= deadline) throw new Error(message);
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  }
}

function capture(fetcher: (url: URL, request: CapturedRequest) => Promise<Response> | Response) {
  const requests: CapturedRequest[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), window.location.origin);
    const request = {
      method: init?.method ?? "GET",
      path: `${url.pathname}${url.search}`,
      body: typeof init?.body === "string" ? init.body : undefined,
      binaryBody: init?.body instanceof Blob ? init.body : undefined,
      contentType: new Headers(init?.headers).get("Content-Type") ?? undefined,
      authorization: new Headers(init?.headers).get("Authorization") ?? undefined,
    };
    requests.push(request);
    return await fetcher(url, request);
  }));
  return { requests };
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;
let scrollIntoViewDescriptor: PropertyDescriptor | undefined;
let createObjectURLDescriptor: PropertyDescriptor | undefined;
let revokeObjectURLDescriptor: PropertyDescriptor | undefined;
let anchorClickDescriptor: PropertyDescriptor | undefined;
let downloadedFile: { href: string; filename: string } | undefined;

function AcceptanceNavigationProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return <>
    <output data-testid="acceptance-location">{location.pathname}{location.search}</output>
    <button type="button" aria-label="Acceptance browser back" onClick={() => navigate(-1)}>Browser back</button>
  </>;
}

async function mountApp(initialPath: string, options: { navigationProbe?: boolean; initialEntries?: string[] } = {}): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  scrollIntoViewDescriptor ??= Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView");
  Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<MemoryRouter initialEntries={options.initialEntries ?? [initialPath]}><AppProviders><AppRouter /></AppProviders>{options.navigationProbe && <AcceptanceNavigationProbe />}</MemoryRouter>);
  });
}

async function click(selector: string, until: () => boolean): Promise<void> {
  await act(async () => {
    const target = document.querySelector<HTMLElement>(selector);
    if (!target) throw new Error(`Missing clickable element: ${selector}`);
    target.click();
    await waitFor(until);
  });
}

function pageText(): string {
  return document.body.textContent ?? "";
}

function overviewPayload(url: URL): Response | undefined {
  if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
  if (url.pathname === "/api/v1/cabinets") return jsonResponse({ items: [{ id: cabinetId, name: "Northwind", company_id: "company-2" }] });
  if (url.pathname === `/api/v1/cabinets/${cabinetId}/projects`) return jsonResponse({ items: [project] });
  if (url.pathname === `/api/v1/cabinets/${cabinetId}/me/selection`) return jsonResponse({ cabinet_id: cabinetId, project_id: projectId });
  if (url.pathname === `/api/v1/projects/${projectId}`) return jsonResponse(project);
  if (url.pathname === `/api/v1/cabinets/${cabinetId}`) return jsonResponse({ id: cabinetId, name: "Northwind", company_id: "company-2" });
  return undefined;
}

function runtimePayload(url: URL): Response | undefined {
  if (url.pathname === `/api/v1/projects/${projectId}/container`) return jsonResponse({ project_id: projectId, status: "running", observed_state: "running", runtime: { phase: "running", observed_state: "running", ready: true } });
  if (url.pathname === `/api/v1/projects/${projectId}/container/metrics`) return jsonResponse({ metrics_available: true, latest: { cpu_millicores: 120, memory_bytes: 4096, timestamp: "2026-11-01T12:00:00Z" } });
  if (url.pathname === `/api/v1/projects/${projectId}/container/workspace/entries`) return jsonResponse({ path: url.searchParams.get("path") ?? "", entries: [{ path: "notes.md", name: "notes.md", kind: "file", size: 16, modified_at: "2026-11-01T12:00:00Z" }] });
  if (url.pathname === `/api/v1/projects/${projectId}/container/workspace/preview`) return jsonResponse({ path: "notes.md", name: "notes.md", size: 16, truncated: false, content: "workspace content" });
  if (url.pathname === `/api/v1/projects/${projectId}/attachments`) return jsonResponse({ items: [] });
  if (url.pathname === `/api/v1/projects/${projectId}/ai-keys/available`) return jsonResponse({ items: [] });
  if (url.pathname === `/api/v1/projects/${projectId}/models/live`) return jsonResponse({ models: [], default_model: null, source: "unavailable" });
  if (url.pathname === `/api/v1/cabinets/${cabinetId}/chats/sidebar`) return jsonResponse({
    cabinet_id: cabinetId,
    selected_project_id: projectId,
    new_chat_enabled: true,
    observed_state: "running",
    pinned: [],
    project_ids_in_cabinet: [projectId],
    projects: [{ project_id: projectId, project_name: project.name, new_chat_enabled: true, chats: [{ session_id: sessionId, project_id: projectId, title: session.title, pinned: false, has_draft: false, has_messages: true }] }],
    project_chats: [{ session_id: sessionId, project_id: projectId, title: session.title, pinned: false, has_draft: false, has_messages: true }],
  });
  return undefined;
}

function transcriptPayload(): Response {
  return jsonResponse({ session_id: sessionId, has_more: false, turn_in_progress: false, blocks: [
    { kind: "user", seq: 1, text: "Inspect notes.md", metadata: {} },
    { kind: "approval", seq: 2, id: "approval-write", name: "write_file", reason: "Writing changes the project workspace.", input: { path: "notes.md" }, metadata: { approval_id: "approval-write" } },
  ] });
}

async function setValue(element: HTMLInputElement | HTMLTextAreaElement, value: string): Promise<void> {
  await act(async () => {
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  if (scrollIntoViewDescriptor) Object.defineProperty(Element.prototype, "scrollIntoView", scrollIntoViewDescriptor);
  else Reflect.deleteProperty(Element.prototype, "scrollIntoView");
  scrollIntoViewDescriptor = undefined;
  if (createObjectURLDescriptor) Object.defineProperty(URL, "createObjectURL", createObjectURLDescriptor);
  else Reflect.deleteProperty(URL, "createObjectURL");
  if (revokeObjectURLDescriptor) Object.defineProperty(URL, "revokeObjectURL", revokeObjectURLDescriptor);
  else Reflect.deleteProperty(URL, "revokeObjectURL");
  createObjectURLDescriptor = undefined;
  revokeObjectURLDescriptor = undefined;
  if (anchorClickDescriptor) Object.defineProperty(HTMLAnchorElement.prototype, "click", anchorClickDescriptor);
  else Reflect.deleteProperty(HTMLAnchorElement.prototype, "click");
  anchorClickDescriptor = undefined;
  downloadedFile = undefined;
  resourceFileRow = undefined;
  promptPathFiles = initialPromptPathFiles.map((file) => ({ ...file }));
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("employee workspace integration acceptance", () => {
  it("preserves direct project, chat-session, files, runtime, operations and module URLs", () => {
    expect(EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS).toEqual([
      "runtime/*", "chat", "chat/:sessionId", "files/*", "runs", "approvals", "resources/*",
      "content", "equipment", "automations", "models", "settings", "modules/:moduleId/*",
    ]);
    expect(matchPath("/projects/:projectId", `/projects/${projectId}`)?.params.projectId).toBe(projectId);
    const projectUrls = [
      `/projects/${projectId}/runtime`, `/projects/${projectId}/chat`, `/projects/${projectId}/chat/session-8`,
      `/projects/${projectId}/files/reports/q1.csv`, `/projects/${projectId}/runs`, `/projects/${projectId}/approvals`,
      `/projects/${projectId}/resources`, `/projects/${projectId}/content`, `/projects/${projectId}/equipment`,
      `/projects/${projectId}/automations`, `/projects/${projectId}/models`, `/projects/${projectId}/settings`,
      `/projects/${projectId}/modules/inventory/stock-list`,
    ];
    expect(projectUrls).toHaveLength(EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS.length);
    expect(matchPath("/projects/:projectId/chat/:sessionId", projectUrls[2])?.params).toEqual({ projectId, sessionId });
    expect(matchPath("/projects/:projectId/runtime/*", projectUrls[0])).toBeTruthy();
    expect(matchPath("/projects/:projectId/files/*", projectUrls[3])).toBeTruthy();
    expect(matchPath("/projects/:projectId/modules/:moduleId/*", projectUrls[12])).toBeTruthy();
    expect(projectUrls.slice(4, 12).every((url) => matchPath("/projects/:projectId/*", url))).toBe(true);
    expect(matchPath("/cabinets/:cabinetId", "/cabinets/cabinet-19")?.params.cabinetId).toBe(cabinetId);
    expect(matchPath("/company/*", "/company/modules")).toBeTruthy();
    expect(matchPath("/company/*", "/company/content")).toBeTruthy();
    expect(matchPath("/admin/*", "/admin/modules")).toBeTruthy();
    expect(matchPath("/admin/*", "/admin")).toBeTruthy();
  });

  it("redirects an anonymous deep link to login, then restores it using the backend /me contract", async () => {
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/auth/login" && request.method === "POST") return jsonResponse(tokens);
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      if (url.pathname === `/api/v1/projects/${projectId}`) return jsonResponse(project);
      if (url.pathname === `/api/v1/cabinets/${cabinetId}`) return jsonResponse({ id: cabinetId, name: "Northwind", company_id: "company-2" });
      if (url.pathname === `/api/v1/cabinets/${cabinetId}/chats/sidebar`) return jsonResponse({
        cabinet_id: cabinetId, selected_project_id: projectId, new_chat_enabled: true, observed_state: "running", pinned: [], project_ids_in_cabinet: [projectId],
        projects: [{ project_id: projectId, project_name: project.name, new_chat_enabled: true, chats: [{ session_id: sessionId, project_id: projectId, title: session.title, pinned: false, has_draft: false, has_messages: true }] }],
        project_chats: [{ session_id: sessionId, project_id: projectId, title: session.title, pinned: false, has_draft: false, has_messages: true }],
      });
      if (url.pathname === `/api/v1/projects/${projectId}/chat/transcript`) return jsonResponse({ session_id: sessionId, has_more: false, turn_in_progress: false, blocks: [{ kind: "user", seq: 1, text: "Inspect notes.md", metadata: {} }] });
      if (url.pathname === `/api/v1/projects/${projectId}/agent/sessions/${sessionId}/composer-draft`) return jsonResponse({ text: "", has_draft: false });
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });

    await mountApp(`/projects/${projectId}/chat/${sessionId}?model=test-model`);
    await act(async () => { await waitFor(() => pageText().includes("Sign in to agentscale")); });
    expect(requests.some((request) => request.path.startsWith("/api/v1/projects/"))).toBe(false);

    const inputs = document.querySelectorAll<HTMLInputElement>(".login-page input");
    expect(inputs).toHaveLength(2);
    await setValue(inputs[0], "ada@example.test");
    await setValue(inputs[1], "test-password");
    await click(".login-page button", () => document.querySelector(".login-page button")?.textContent === "Sign in");
    await act(async () => { await waitFor(() => pageText().includes("Inspect notes.md")); });

    expect(pageText()).toContain("Chats");
    expect(pageText()).toContain("Inspect notes.md");
    expect(pageText()).toContain("ada@example.test");
    expect(JSON.parse(window.localStorage.getItem(AUTH_STORAGE_KEY) ?? "null")).toEqual(tokens);
    expect(requests.find((request) => request.path === "/api/v1/auth/login")).toMatchObject({
      method: "POST",
      body: JSON.stringify({ username: "ada@example.test", password: "test-password" }),
    });
    const identityRequests = requests.filter((request) => request.path === "/api/v1/me");
    expect(identityRequests).toHaveLength(1);
    expect(identityRequests[0]).toMatchObject({ method: "GET", path: "/api/v1/me", authorization: "Bearer acceptance-access" });
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}` && request.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.some((request) => request.path.startsWith(`/api/v1/projects/${projectId}/chat/transcript?session_id=${sessionId}`)
      && request.authorization === "Bearer acceptance-access")).toBe(true);
  });

  it("shows session restoration loading, then loads projects from the backend /me identity contract", async () => {
    let releaseRestore!: (response: Response) => void;
    const restore = new Promise<Response>((resolve) => { releaseRestore = resolve; });
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return restore;
      const response = overviewPayload(url);
      if (response) return response;
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp("/projects");
    await act(async () => { await waitFor(() => pageText().includes("Restoring your session…")); });
    expect(pageText()).toContain("Restoring your session…");
    expect(requests.some((request) => request.path === "/api/v1/cabinets")).toBe(false);

    await act(async () => {
      releaseRestore(jsonResponse(backendIdentityResponse()));
      await waitFor(() => Boolean(document.querySelector(`a[href='/projects/${projectId}']`)));
    });
    expect(pageText()).toContain("Signal Lab");
    expect(pageText()).toContain("ada@example.test");
    expect(JSON.parse(window.localStorage.getItem(AUTH_STORAGE_KEY) ?? "null")).toEqual(tokens);
    expect(requests.some((request) => request.path === "/api/v1/cabinets")).toBe(true);
    expect(requests.some((request) => request.path === "/api/v1/me" && request.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.some((request) => request.path.startsWith("/api/") && !request.path.startsWith("/api/v1/"))).toBe(false);
  });

  it("loads the project, reads and previews Pod files, then confirms session approval readback", async () => {
    let pendingApprovals = [{ id: "approval-write", name: "write_file", input: { path: "notes.md" } }];
    const { requests } = capture((url, request) => {
      const overview = overviewPayload(url);
      if (overview) return overview;
      const runtime = runtimePayload(url);
      if (runtime) return runtime;
      if (url.pathname === `/api/v1/projects/${projectId}/agent/sessions`) return jsonResponse({ items: [session] });
      if (url.pathname === `/api/v1/projects/${projectId}/agent/sessions/${sessionId}`) return jsonResponse(session);
      if (url.pathname === `/api/v1/projects/${projectId}/agent/sessions/${sessionId}/pending-approvals`) return jsonResponse({ items: pendingApprovals });
      if (url.pathname === `/api/v1/projects/${projectId}/chat/transcript`) return transcriptPayload();
      if (url.pathname === `/api/v1/projects/${projectId}/agent/sessions/${sessionId}/tool-approvals` && request.method === "POST") {
        pendingApprovals = [];
        return jsonResponse({ session_id: sessionId, approval_id: "approval-write", decision: "approve" });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp("/projects");
    await act(async () => { await waitFor(() => Boolean(document.querySelector(`a[href='/projects/${projectId}']`))); });
    expect(pageText()).toContain("Signal Lab");
    expect(requests.some((request) => request.path === `/api/v1/cabinets/${cabinetId}/projects`)).toBe(true);
    expect(requests.some((request) => request.path === `/api/v1/cabinets/${cabinetId}/me/selection`)).toBe(true);
    await click(`a[href='/projects/${projectId}']`, () => Boolean(document.querySelector(".project-runtime-header")));
    expect(document.querySelector(".project-runtime-header")?.textContent).toContain("Signal Lab");
    expect(pageText()).toContain("Наблюдаемое состояние");
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/container`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/container/metrics?window=1h`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });

    await click(`a[href='/projects/${projectId}/files']`, () => Boolean(document.querySelector(".files-entry-main")));
    expect(pageText()).toContain("Live files from the project Pod");
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/entries?path=`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    const fileEntry = [...document.querySelectorAll<HTMLButtonElement>(".files-entry-main")].find((button) => button.textContent?.includes("notes.md"));
    expect(fileEntry).toBeTruthy();
    await act(async () => {
      fileEntry?.click();
      await waitFor(() => document.querySelector<HTMLTextAreaElement>("textarea[aria-label='Editing notes.md']")?.value === "workspace content");
    });
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/preview?path=notes.md`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });

    await click(`a[href='/projects/${projectId}/runs']`, () => pageText().includes("Approvals inbox"));
    await act(async () => { await waitFor(() => pageText().includes("Writing changes the project workspace.")); });
    expect(pageText()).toContain("Session history");
    expect(pageText()).toContain("Deployment review");
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/agent/sessions`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    expect(requests.find((request) => request.path.startsWith(`/api/v1/projects/${projectId}/chat/transcript?session_id=${sessionId}`))).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/agent/sessions/${sessionId}/pending-approvals`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    const approval = [...document.querySelectorAll<HTMLElement>(".run-approval-card")].find((card) => card.querySelector("h4")?.textContent === "write_file");
    expect(approval).toBeTruthy();
    const approveButton = [...approval!.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent?.trim() === "Approve action");
    await act(async () => {
      approveButton?.click();
      await waitFor(() => pageText().includes("Approval decision saved; confirmed absent from pending inbox."));
    });
    expect(document.querySelector(".run-approval-card")).toBeNull();
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/agent/sessions/${sessionId}/tool-approvals`)).toMatchObject({
      method: "POST",
      body: JSON.stringify({ id: "approval-write", decision: "approve" }),
      authorization: "Bearer acceptance-access",
    });
    expect(requests.filter((request) => request.path === `/api/v1/projects/${projectId}/agent/sessions/${sessionId}/pending-approvals`)).toHaveLength(3);
    expect(pendingApprovals).toEqual([]);
    expect(requests.some((request) => request.path.startsWith("/api/") && !request.path.startsWith("/api/v1/"))).toBe(false);
  });

  it("restores auth, selects a project, recovers Files API errors, creates/uploads/reads a deep link, then switches project", async () => {
    const filesystems = new Map<string, { directories: Set<string>; files: Map<string, string> }>([
      [projectId, { directories: new Set(), files: new Map([["notes.md", "workspace content"]]) }],
      [secondProjectId, { directories: new Set(), files: new Map() }],
    ]);
    let selectedProjectId: string | null = null;
    let failNextProjectRootListing = false;
    let releaseIdentity!: (response: Response) => void;
    const identityRestore = new Promise<Response>((resolve) => { releaseIdentity = resolve; });

    const { requests } = captureRegisteredApi((url, request) => {
      const projectForId = (id: string) => id === projectId ? project : id === secondProjectId ? secondProject : undefined;
      const projectRoute = url.pathname.match(/^\/api\/v1\/projects\/([^/]+)(?:\/|$)/);
      const requestedProjectId = projectRoute?.[1];
      const currentProject = requestedProjectId ? projectForId(requestedProjectId) : undefined;
      const fs = requestedProjectId ? filesystems.get(requestedProjectId) : undefined;

      if (url.pathname === "/api/v1/me" && request.method === "GET") return identityRestore;
      if (url.pathname === "/api/v1/cabinets" && request.method === "GET") return jsonResponse({ items: [{ id: cabinetId, name: "Northwind", company_id: "company-2" }] });
      if (url.pathname === `/api/v1/cabinets/${cabinetId}/projects` && request.method === "GET") return jsonResponse({ items: [project, secondProject] });
      if (url.pathname === `/api/v1/cabinets/${cabinetId}/me/selection` && request.method === "GET") return jsonResponse({ cabinet_id: cabinetId, project_id: selectedProjectId });
      if (url.pathname === `/api/v1/cabinets/${cabinetId}/me/selection` && request.method === "PUT") {
        const body = JSON.parse(request.body ?? "{}") as { project_id?: string | null };
        selectedProjectId = body.project_id ?? null;
        return jsonResponse({ cabinet_id: cabinetId, project_id: selectedProjectId });
      }
      if (url.pathname === `/api/v1/cabinets/${cabinetId}` && request.method === "GET") return jsonResponse({ id: cabinetId, name: "Northwind", company_id: "company-2" });
      if (url.pathname === `/api/v1/cabinets/${cabinetId}/chats/sidebar` && request.method === "GET") return jsonResponse({
        cabinet_id: cabinetId,
        selected_project_id: selectedProjectId,
        new_chat_enabled: true,
        observed_state: "running",
        pinned: [],
        project_ids_in_cabinet: [projectId, secondProjectId],
        projects: [project, secondProject].map((item) => ({ project_id: item.id, project_name: item.name, new_chat_enabled: true, chats: [] })),
        project_chats: [],
      });
      if (!currentProject || !fs || !requestedProjectId) throw new Error(`Unexpected project scope: ${request.method} ${request.path}`);
      if (url.pathname === `/api/v1/projects/${requestedProjectId}` && request.method === "GET") return jsonResponse(currentProject);
      if (url.pathname === `/api/v1/projects/${requestedProjectId}/container` && request.method === "GET") {
        return jsonResponse({ project_id: requestedProjectId, status: "running", observed_state: "running", runtime: { phase: "running", observed_state: "running", ready: true } });
      }
      if (url.pathname === `/api/v1/projects/${requestedProjectId}/container/metrics` && request.method === "GET") {
        return jsonResponse({ project_id: requestedProjectId, metrics_available: false, latest: null });
      }
      if (url.pathname === `/api/v1/projects/${requestedProjectId}/attachments` && request.method === "GET") return jsonResponse({ items: [] });
      if (url.pathname === `/api/v1/projects/${requestedProjectId}/ai-keys/available` && request.method === "GET") return jsonResponse({ items: [] });
      if (url.pathname === `/api/v1/projects/${requestedProjectId}/models/live` && request.method === "GET") return jsonResponse({ models: [], default_model: null, source: "catalog" });

      if (url.pathname === `/api/v1/projects/${requestedProjectId}/container/workspace/entries` && request.method === "GET") {
        const path = url.searchParams.get("path") ?? "";
        if (requestedProjectId === projectId && path === "" && failNextProjectRootListing) {
          failNextProjectRootListing = false;
          return jsonResponse({ code: "SERVICE_UNAVAILABLE", title: "Service Unavailable", detail: "Workspace listing temporarily unavailable." }, 503);
        }
        const entries = [
          ...[...fs.directories].filter((directory) => directory.split("/").slice(0, -1).join("/") === path).map((directory) => ({ path: directory, name: directory.split("/").at(-1)!, kind: "dir", size: null, modified_at: null })),
          ...[...fs.files].filter(([filePath]) => filePath.split("/").slice(0, -1).join("/") === path).map(([filePath, content]) => ({ path: filePath, name: filePath.split("/").at(-1)!, kind: "file", size: new Blob([content]).size, modified_at: "2026-11-01T12:00:00Z" })),
        ];
        return jsonResponse({ path, entries });
      }
      if (url.pathname === `/api/v1/projects/${requestedProjectId}/container/workspace/mkdir` && request.method === "POST") {
        const body = JSON.parse(request.body ?? "{}") as { path?: string };
        if (!body.path) throw new Error("mkdir request omitted the required path");
        fs.directories.add(body.path);
        return jsonResponse({ created: true, path: body.path }, 201);
      }
      if (url.pathname === `/api/v1/projects/${requestedProjectId}/container/workspace/content` && request.method === "PUT") {
        const path = url.searchParams.get("path") ?? "";
        const body = request.binaryBody;
        if (!path || !body) throw new Error("workspace upload omitted path or binary body");
        return body.text().then((content) => {
          fs.files.set(path, content);
          return jsonResponse({ written: true, path, size: body.size });
        });
      }
      if (url.pathname === `/api/v1/projects/${requestedProjectId}/container/workspace/content` && request.method === "GET") {
        const path = url.searchParams.get("path") ?? "";
        const content = fs.files.get(path);
        if (content === undefined) return jsonResponse({ code: "WORKSPACE_NOT_FOUND", title: "Not Found", detail: "File not found" }, 404);
        const blob = new Blob([content], { type: "application/octet-stream" });
        return {
          ...jsonResponse({}, 200),
          headers: new Headers({ "Content-Type": "application/octet-stream", "Content-Disposition": `attachment; filename="${path.split("/").at(-1)}"`, "X-Workspace-Path": path, "X-Workspace-Size": String(blob.size) }),
          blob: async () => blob,
        } as Response;
      }
      if (url.pathname === `/api/v1/projects/${requestedProjectId}/container/workspace/preview` && request.method === "GET") {
        const path = url.searchParams.get("path") ?? "";
        const content = fs.files.get(path);
        if (content === undefined) return jsonResponse({ code: "WORKSPACE_NOT_FOUND", title: "Not Found", detail: "File not found" }, 404);
        return jsonResponse({ path, name: path.split("/").at(-1), size: new Blob([content]).size, truncated: false, content });
      }
      throw new Error(`Unexpected registered request: ${request.method} ${request.path}`);
    });

    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));
    await mountApp("/projects", { navigationProbe: true });
    await act(async () => {
      await waitFor(() => pageText().includes("Restoring your session…") && requests.some((request) => request.path === "/api/v1/me"));
    });
    expect(requests.some((request) => request.path === `/api/v1/cabinets/${cabinetId}/projects`)).toBe(false);
    await act(async () => {
      releaseIdentity(jsonResponse(backendIdentityResponse()));
      await waitFor(() => Boolean(document.querySelector(`a[href='/projects/${projectId}']`)));
    });
    expect(pageText()).toContain("Signal Lab");
    expect(requests.filter((request) => request.path === "/api/v1/me")).toHaveLength(1);
    expect(requests.find((request) => request.path === "/api/v1/me")).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    expect(JSON.parse(window.localStorage.getItem(AUTH_STORAGE_KEY) ?? "null")).toEqual(tokens);
    expect(requests.some((request) => request.path === `/api/v1/cabinets/${cabinetId}/projects` && request.authorization === "Bearer acceptance-access")).toBe(true);

    const firstProjectRow = [...document.querySelectorAll<HTMLElement>(".projects-row")].find((row) => row.textContent?.includes("Signal Lab"));
    const firstSelectButton = firstProjectRow?.querySelector<HTMLButtonElement>(".projects-select");
    expect(firstSelectButton?.textContent).toBe("Выбрать");
    const selectionReadbacksBeforeFirstSelect = requests.filter((request) => request.path === `/api/v1/cabinets/${cabinetId}/me/selection` && request.method === "GET").length;
    await act(async () => {
      firstSelectButton?.click();
      await waitFor(() => firstProjectRow?.querySelector(".projects-select")?.textContent === "Текущий проект");
    });
    expect(requests.find((request) => request.path === `/api/v1/cabinets/${cabinetId}/me/selection` && request.method === "PUT")).toMatchObject({
      body: JSON.stringify({ project_id: projectId }),
      authorization: "Bearer acceptance-access",
    });
    expect(requests.filter((request) => request.path === `/api/v1/cabinets/${cabinetId}/me/selection` && request.method === "GET")).toHaveLength(selectionReadbacksBeforeFirstSelect + 1);
    expect(selectedProjectId).toBe(projectId);

    const overviewLink = firstProjectRow?.querySelector<HTMLAnchorElement>(`a[href='/projects/${projectId}']`);
    await act(async () => {
      overviewLink?.click();
      await waitFor(() => Boolean(document.querySelector(`.workspace-shell[data-project-id='${projectId}']`))
        && pageText().includes("Наблюдаемое состояние"));
    });
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/container`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/entries?path=`)).toBe(true);

    await click("a[aria-label='Settings']", () => Boolean(document.querySelector(".project-controls__header")));
    expect(document.querySelector(".project-controls__header h1")?.textContent).toBe("Signal Lab");
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}` && request.method === "GET" && request.authorization === "Bearer acceptance-access")).toBe(true);

    const rootListingCountBeforeError = requests.filter((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/entries?path=` && request.method === "GET").length;
    failNextProjectRootListing = true;
    await click("a[aria-label='Files']", () => Boolean(document.querySelector(".files-availability-error")));
    expect(pageText()).toContain("Workspace listing temporarily unavailable.");
    expect(requests.filter((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/entries?path=` && request.method === "GET")).toHaveLength(rootListingCountBeforeError + 1);
    await click(".files-availability button", () => Boolean(document.querySelector(".files-entry-main")));
    expect(pageText()).toContain("Live files from the project Pod");
    expect(requests.filter((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/entries?path=` && request.method === "GET")).toHaveLength(rootListingCountBeforeError + 2);

    await click("button[aria-label='Create a folder']", () => Boolean(document.querySelector("#files-create-folder-name")));
    await setValue(document.querySelector<HTMLInputElement>("#files-create-folder-name")!, "docs");
    await click(".files-modal-form button[type='submit']", () => pageText().includes("Created docs"));
    const mkdirRequest = requests.find((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/mkdir` && request.method === "POST");
    expect(mkdirRequest).toMatchObject({
      method: "POST",
      body: JSON.stringify({ path: "docs" }),
      contentType: "application/json",
      authorization: "Bearer acceptance-access",
    });
    expect(requests.some((request) => request.path === `/api/v1/projects/${secondProjectId}/container/workspace/mkdir`)).toBe(false);
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/entries?path=` && request.method === "GET" && request.authorization === "Bearer acceptance-access")).toBe(true);
    expect([...document.querySelectorAll<HTMLButtonElement>(".files-entry-main")].some((button) => button.textContent?.includes("docs"))).toBe(true);
    expect(requests.filter((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/entries?path=` && request.method === "GET")).toHaveLength(rootListingCountBeforeError + 4);

    const docsEntry = [...document.querySelectorAll<HTMLButtonElement>(".files-entry-main")].find((button) => button.textContent?.includes("docs"));
    await act(async () => {
      docsEntry?.click();
      await waitFor(() => document.querySelector<HTMLInputElement>("#files-path-input")?.value === "docs"
        && Boolean(document.querySelector("[data-testid='acceptance-location']")?.textContent?.includes("path=docs")));
    });
    const uploadInput = document.querySelector<HTMLInputElement>("input[aria-label='Upload files']");
    expect(uploadInput).toBeTruthy();
    await act(async () => {
      Object.defineProperty(uploadInput, "files", { configurable: true, value: [new File(["acceptance artifact"], "brief.txt", { type: "text/plain" })] });
      uploadInput?.dispatchEvent(new Event("change", { bubbles: true }));
      await waitFor(() => pageText().includes("Uploaded 1 file"));
    });
    const uploadRequest = requests.find((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/content?path=docs%2Fbrief.txt` && request.method === "PUT");
    expect(uploadRequest).toMatchObject({ method: "PUT", contentType: "application/octet-stream", authorization: "Bearer acceptance-access" });
    expect(await uploadRequest?.binaryBody?.text()).toBe("acceptance artifact");
    const fileDownload = requests.find((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/content?path=docs%2Fbrief.txt` && request.method === "GET");
    expect(fileDownload).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    expect(filesystems.get(projectId)?.files.get("docs/brief.txt")).toBe("acceptance artifact");
    expect(pageText()).toContain("Uploaded 1 file");
    expect([...document.querySelectorAll<HTMLButtonElement>(".files-entry-main")].some((button) => button.textContent?.includes("brief.txt"))).toBe(true);
    expect(requests.filter((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/entries?path=docs` && request.method === "GET")).toHaveLength(3);

    const briefEntry = [...document.querySelectorAll<HTMLButtonElement>(".files-entry-main")].find((button) => button.textContent?.includes("brief.txt"));
    await act(async () => {
      briefEntry?.click();
      await waitFor(() => document.querySelector<HTMLTextAreaElement>("textarea[aria-label='Editing brief.txt']")?.value === "acceptance artifact");
    });
    const deepLink = document.querySelector<HTMLOutputElement>("[data-testid='acceptance-location']")?.textContent;
    expect(deepLink).toBe(`/projects/${projectId}/files?path=docs&selected=docs%2Fbrief.txt`);
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/preview?path=docs%2Fbrief.txt`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });

    await act(async () => { root?.unmount(); });
    root = undefined;
    host?.remove();
    host = undefined;
    await mountApp(deepLink!, { navigationProbe: true, initialEntries: ["/projects", deepLink!] });
    await act(async () => {
      await waitFor(() => document.querySelector<HTMLTextAreaElement>("textarea[aria-label='Editing brief.txt']")?.value === "acceptance artifact");
    });
    expect(document.querySelector(".files-entry-row.is-selected")?.textContent).toContain("brief.txt");
    expect(document.querySelector(".workspace-shell")?.getAttribute("data-project-id")).toBe(projectId);

    await click("button[aria-label='Acceptance browser back']", () => Boolean([...document.querySelectorAll(".projects-row")].find((row) => row.textContent?.includes("Archive Lab"))));
    const secondProjectRow = [...document.querySelectorAll<HTMLElement>(".projects-row")].find((row) => row.textContent?.includes("Archive Lab"));
    const secondSelectButton = secondProjectRow?.querySelector<HTMLButtonElement>(".projects-select");
    expect(secondSelectButton?.textContent).toBe("Выбрать");
    const selectionReadbacksBeforeSecondSelect = requests.filter((request) => request.path === `/api/v1/cabinets/${cabinetId}/me/selection` && request.method === "GET").length;
    await act(async () => {
      secondSelectButton?.click();
      await waitFor(() => secondProjectRow?.querySelector(".projects-select")?.textContent === "Текущий проект");
    });
    expect(requests.find((request) => request.path === `/api/v1/cabinets/${cabinetId}/me/selection` && request.method === "PUT" && JSON.parse(request.body ?? "{}").project_id === secondProjectId)).toMatchObject({
      body: JSON.stringify({ project_id: secondProjectId }),
      authorization: "Bearer acceptance-access",
    });
    expect(requests.filter((request) => request.path === `/api/v1/cabinets/${cabinetId}/me/selection` && request.method === "GET")).toHaveLength(selectionReadbacksBeforeSecondSelect + 1);
    expect(selectedProjectId).toBe(secondProjectId);
    const secondOverviewLink = secondProjectRow?.querySelector<HTMLAnchorElement>(`a[href='/projects/${secondProjectId}']`);
    await act(async () => {
      secondOverviewLink?.click();
      await waitFor(() => Boolean(document.querySelector(`.workspace-shell[data-project-id='${secondProjectId}']`))
        && pageText().includes("Наблюдаемое состояние"));
    });
    const secondProjectRequest = requests.find((request) => request.path === `/api/v1/projects/${secondProjectId}`);
    expect(secondProjectRequest).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    const beforeSecondFiles = requests.length;
    await click(`a[href='/projects/${secondProjectId}/files']`, () => Boolean(document.querySelector(".files-workspace"))
      && pageText().includes("This folder is empty"));
    expect(requests.slice(beforeSecondFiles).filter((request) => request.path.includes("/container/workspace/entries"))
      .every((request) => request.path.startsWith(`/api/v1/projects/${secondProjectId}/`))).toBe(true);
    expect(requests.filter((request) => request.path.includes("/container/workspace/mkdir")).map((request) => request.path)).toEqual([
      `/api/v1/projects/${projectId}/container/workspace/mkdir`,
    ]);
    expect(requests.every((request) => request.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.filter((request) => request.path.includes("/container/workspace/"))
      .every((request) => request.path.startsWith(`/api/v1/projects/${projectId}/container/workspace/`)
        || request.path.startsWith(`/api/v1/projects/${secondProjectId}/container/workspace/`))).toBe(true);
  });

  it("renders an empty project cabinet instead of inventing a project workspace", async () => {
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      if (url.pathname === "/api/v1/cabinets") return jsonResponse({ items: [{ id: cabinetId, name: "Northwind" }] });
      if (url.pathname === `/api/v1/cabinets/${cabinetId}/projects`) return jsonResponse({ items: [] });
      if (url.pathname === `/api/v1/cabinets/${cabinetId}/me/selection`) return jsonResponse({ cabinet_id: cabinetId, project_id: null });
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp("/projects");
    await act(async () => { await waitFor(() => pageText().includes("В кабинете пока нет проектов")); });
    expect(pageText()).toContain("Создайте проект, чтобы подготовить отдельную рабочую среду агента.");
    expect(requests.some((request) => request.path === `/api/v1/cabinets/${cabinetId}/projects`)).toBe(true);
    expect(requests.some((request) => request.path.startsWith(`/api/v1/projects/${projectId}`))).toBe(false);
  });

  it("launches a draft project through the overview lifecycle route and confirms refreshed state", async () => {
    let status = "draft";
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      if (url.pathname === "/api/v1/cabinets") return jsonResponse({ items: [{ id: cabinetId, name: "Northwind" }] });
      if (url.pathname === `/api/v1/cabinets/${cabinetId}/projects`) return jsonResponse({ items: [{ ...project, status }] });
      if (url.pathname === `/api/v1/cabinets/${cabinetId}/me/selection`) return jsonResponse({ cabinet_id: cabinetId, project_id: projectId });
      if (url.pathname === `/api/v1/projects/${projectId}/launch` && request.method === "POST") {
        status = "active";
        return jsonResponse({ project_id: projectId, status });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp("/projects");
    await act(async () => { await waitFor(() => [...document.querySelectorAll<HTMLButtonElement>(".projects-action")].some((button) => button.textContent === "Запустить")); });
    const launchButton = [...document.querySelectorAll<HTMLButtonElement>(".projects-action")].find((button) => button.textContent === "Запустить");
    expect(launchButton).toBeTruthy();
    await act(async () => {
      launchButton?.click();
      await waitFor(() => pageText().includes("Действие «запустить» для проекта «Signal Lab» выполнено."));
    });

    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/launch` && request.method === "POST")?.authorization).toBe("Bearer acceptance-access");
    expect(requests.filter((request) => request.path === `/api/v1/cabinets/${cabinetId}/projects`).length).toBeGreaterThanOrEqual(2);
    expect(pageText()).toContain("Активен");
  });

  it("shows an unavailable-project error and a return path when project API lookup fails", async () => {
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      if (url.pathname === `/api/v1/projects/${projectId}`) return jsonResponse({ code: "NOT_FOUND", title: "Not Found", detail: "Project does not exist" }, 404);
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/chat/${sessionId}`);
    await act(async () => { await waitFor(() => pageText().includes("Проект недоступен")); });
    expect(pageText()).toContain("Project does not exist");
    expect(document.querySelector<HTMLAnchorElement>("a[href='/projects']")?.textContent).toBe("Вернуться к проектам");
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}` && request.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.some((request) => request.path.includes("/chat/transcript"))).toBe(false);
  });

  it("shows an empty unbound state and retries a failed project module list", async () => {
    let listAttempts = 0;
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      const overview = overviewPayload(url);
      if (overview) return overview;
      if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules` && request.method === "GET") {
        listAttempts += 1;
        return listAttempts === 1 ? jsonResponse({ detail: "temporarily unavailable" }, 503) : jsonResponse({ items: [] });
      }
      if (url.pathname === "/api/v1/content/assets" && request.method === "GET") return jsonResponse({ items: [] });
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/resources`);
    await act(async () => { await waitFor(() => pageText().includes("Project module bindings unavailable")); });
    expect(document.querySelector(".project-module-resources__feedback--error")?.textContent).toContain("temporarily unavailable");
    await click(".project-module-resources__feedback--error button", () => pageText().includes("No first-party resource modules are bound to this project yet."));
    expect(document.querySelectorAll(".project-module-resources__card")).toHaveLength(3);
    expect([...document.querySelectorAll(".project-module-resources__card .project-module-resources__eyebrow")].map((node) => node.textContent)).toEqual(["Not bound", "Not bound", "Not bound"]);
    expect(requests.filter((request) => request.path === `/api/v1/projects/${projectId}/runtime-modules`).map((request) => request.method)).toEqual(["GET", "GET"]);
    expect(requests.some((request) => request.path.includes("/runtime-modules/mod_") && request.path.includes("/meta/"))).toBe(false);
  });

  it("opens a read-only project-bound MCP module and preserves its empty state", async () => {
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      const overview = overviewPayload(url);
      if (overview) return overview;
      const modules = resourceModuleResponse(url, request);
      if (modules) return modules;
      if (url.pathname === "/api/v1/content/assets" && request.method === "GET") return jsonResponse({ items: [] });
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/resources?module=mod_mcp`);
    await act(async () => { await waitFor(() => pageText().includes("Общие данные доступны только для чтения")); });
    expect(pageText()).toContain("mcp_packages");
    expect(pageText()).toContain("Нет записей");
    expect(document.querySelector(".module-runtime-inline-add")).toBeNull();
    expect(document.querySelector(".module-runtime-button--primary")).toBeNull();
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}/runtime-modules/mod_mcp/meta/documents/tabs` && request.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.some((request) => request.path.startsWith(`/api/v1/projects/${projectId}/runtime-modules/mod_mcp/data/mcp_packages?`) && request.method === "GET" && request.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}/runtime-modules/mod_mcp/data/mcp_packages` && request.method === "POST")).toBe(false);
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}/modules/mod_mcp/profile` && request.method === "PATCH")).toBe(false);
  });

  it("opens only project-bound first-party modules and preserves fail-closed profile/materialization blockers", async () => {
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      const overview = overviewPayload(url);
      if (overview) return overview;
      const modules = resourceModuleResponse(url, request);
      if (modules) return modules;
      if (url.pathname === "/api/v1/content/assets" && request.method === "GET") return jsonResponse({ items: [] });
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/resources`);
    await act(async () => { await waitFor(() => Boolean(document.querySelector("[data-resource-module='mod_prompts']"))); });
    expect(pageText()).toContain(`Project scope: ${projectId}`);
    expect(pageText()).toContain("Bound to project");
    expect(pageText()).toContain("Profile and migration blockers");
    expect(pageText()).toContain("Canonical resource web facade is not confirmed");
    expect(pageText()).toContain("Materialization apply is unavailable");

    await click("[data-resource-module='mod_prompts'] button", () => Boolean(document.querySelector(".module-runtime-nav")));
    expect(pageText()).toContain("Промпты");
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}/runtime-modules` && request.method === "GET" && request.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}/runtime-modules/mod_prompts/meta/documents/views` && request.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.some((request) => request.path.startsWith("/api/v1/projects/") && request.path.includes("/resource-profile"))).toBe(false);
  });

  it("edits nested prompt files through the owner-scoped prompt_paths row route", async () => {
    const savedFiles = [{ id: "tone", name: "tone.md", priority: 100, body_md: "Be precise." }];
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      const overview = overviewPayload(url);
      if (overview) return overview;
      const modules = resourceModuleResponse(url, request);
      if (modules) return modules;
      if (url.pathname === "/api/v1/content/assets" && request.method === "GET") return jsonResponse({ items: [] });
      if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_prompts/data/prompt_paths/path_agents` && request.method === "PATCH") {
        const patch = JSON.parse(request.body ?? "{}").body as { files_json: typeof savedFiles };
        savedFiles.splice(0, savedFiles.length, ...patch.files_json);
        return jsonResponse({ row_id: "path_agents", body: { profile_id: "profile_default", name: "AGENTS.md", path: "", files_json: savedFiles } });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/resources?module=mod_prompts`);
    await act(async () => { await waitFor(() => pageText().includes("Profiles")); });
    expect(pageText()).not.toContain("Private profile");
    const profileRow = [...document.querySelectorAll<HTMLTableRowElement>(".module-runtime-table tbody tr")].find((row) => row.textContent?.includes("Default"));
    expect(profileRow).toBeTruthy();
    await act(async () => { profileRow?.querySelector("button")?.click(); await waitFor(() => [...document.querySelectorAll<HTMLTableRowElement>(".module-runtime-table tbody tr")].some((row) => row.textContent?.includes("AGENTS.md"))); });
    const pathRow = [...document.querySelectorAll<HTMLTableRowElement>(".module-runtime-table tbody tr")].find((row) => row.textContent?.includes("AGENTS.md"));
    expect(pathRow).toBeTruthy();
    expect(pageText()).not.toContain("Private path");
    await act(async () => { pathRow?.querySelector("button")?.click(); await waitFor(() => Boolean(document.querySelector(".project-prompt-files__editor textarea"))); });
    const editor = document.querySelector<HTMLTextAreaElement>(".project-prompt-files__editor textarea");
    expect(editor?.value).toBe("Be precise.");
    await setValue(editor!, "Be precise and concise.");
    await click(".project-prompt-files__editor button[type='submit']", () => pageText().includes("Prompt files saved by the project module API."));

    const save = requests.find((request) => request.path === `/api/v1/projects/${projectId}/runtime-modules/mod_prompts/data/prompt_paths/path_agents` && request.method === "PATCH");
    expect(save).toMatchObject({ authorization: "Bearer acceptance-access", body: JSON.stringify({ body: { files_json: [{ id: "tone", name: "tone.md", priority: 100, body_md: "Be precise and concise." }] } }) });
    expect(savedFiles[0].body_md).toBe("Be precise and concise.");
    expect(requests.some((request) => request.path.includes("/materialization") || request.path.includes("/resource-versions"))).toBe(false);
  });

  it("rejects a stale nested prompt edit when the owner-scoped row changed before save", async () => {
    let simulateConcurrentChange = false;
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      if (url.pathname === `/api/v1/projects/${projectId}/runtime-modules/mod_prompts/data/prompt_paths` && request.method === "GET") {
        if (simulateConcurrentChange) {
          promptPathFiles = [{ ...promptPathFiles[0], body_md: "Concurrent change." }];
          simulateConcurrentChange = false;
        }
        return jsonResponse({ items: [
          { row_id: "path_agents", body: { profile_id: "profile_default", name: "AGENTS.md", path: "", files_json: promptPathFiles } },
          { row_id: "path_research", body: { profile_id: "profile_research", name: "Research", path: "prompts/research", files_json: [] } },
          { row_id: "path_private", body: { profile_id: "profile_default", name: "Private path", path: "private", project_ids: ["project-other"], files_json: [] } },
        ] });
      }
      const overview = overviewPayload(url);
      if (overview) return overview;
      const modules = resourceModuleResponse(url, request);
      if (modules) return modules;
      if (url.pathname === "/api/v1/content/assets" && request.method === "GET") return jsonResponse({ items: [] });
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/resources?module=mod_prompts`);
    await act(async () => { await waitFor(() => pageText().includes("Profiles")); });
    const profileRow = [...document.querySelectorAll<HTMLTableRowElement>(".module-runtime-table tbody tr")].find((item) => item.textContent?.includes("Default"));
    if (!profileRow) throw new Error("The Default profile row was not returned.");
    await act(async () => { profileRow.querySelector("button")?.click(); await waitFor(() => [...document.querySelectorAll<HTMLTableRowElement>(".module-runtime-table tbody tr")].some((item) => item.textContent?.includes("AGENTS.md"))); });
    const pathRow = [...document.querySelectorAll<HTMLTableRowElement>(".module-runtime-table tbody tr")].find((item) => item.textContent?.includes("AGENTS.md"));
    if (!pathRow) throw new Error("The default prompt path row was not returned.");
    await act(async () => { pathRow.querySelector("button")?.click(); await waitFor(() => Boolean(document.querySelector(".project-prompt-files__editor textarea"))); });
    await setValue(document.querySelector<HTMLTextAreaElement>(".project-prompt-files__editor textarea")!, "My local edit.");
    simulateConcurrentChange = true;
    await click(".project-prompt-files__editor button[type='submit']", () => pageText().includes("Prompt files changed since this editor opened."));
    expect(pageText()).toContain("the backend does not provide an atomic version guard");
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}/runtime-modules/mod_prompts/data/prompt_paths/path_agents` && request.method === "PATCH")).toBe(false);
    expect(promptPathFiles[0].body_md).toBe("Concurrent change.");
    expect(document.querySelector(".project-prompt-files__reload")).toBeTruthy();
  });

  it("keeps project Assets authenticated and company-scoped alongside module resources", async () => {

    let projectLookups = 0;
    const uploadedAsset = {
      id: "asset-content-5",
      owner_scope: "company",
      owner_company_id: "company-verified",
      visibility: "company",
      mime: "text/plain",
      title: "handoff.txt",
      tags: [],
      created_by: employee.id,
      latest_version: 1,
      created_at: "2026-11-01T12:00:00Z",
      updated_at: "2026-11-01T12:00:00Z",
    };
    const uploadedVersion = {
      id: "version-content-5",
      asset_id: uploadedAsset.id,
      version: 1,
      size: 12,
      sha256: "a".repeat(64),
      etag: "etag-content-5",
      object_metadata: {},
      created_at: "2026-11-01T12:00:00Z",
    };
    const { requests } = capture((url, request) => {
      if (url.pathname === `/api/v1/projects/${projectId}`) {
        projectLookups += 1;
        return jsonResponse({ ...project, company_id: projectLookups === 1 ? "company-from-parent" : "company-verified" });
      }
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      const modules = resourceModuleResponse(url, request);
      if (modules) return modules;
      if (url.pathname === `/api/v1/cabinets/${cabinetId}`) return jsonResponse({ id: cabinetId, name: "Northwind", company_id: "company-2" });
      if (url.pathname === "/api/v1/content/assets" && request.method === "GET") return jsonResponse({ items: [uploadedAsset] });
      if (url.pathname === "/api/v1/content/assets" && request.method === "POST") return jsonResponse({ ...uploadedAsset, latest_version: null });
      if (url.pathname === `/api/v1/content/assets/${uploadedAsset.id}/versions` && request.method === "POST") {
        return jsonResponse({ version: { ...uploadedVersion, size: 0, sha256: null }, upload_url: "https://signed.invalid/private", method: "PUT" });
      }
      if (url.pathname === `/api/v1/content/assets/${uploadedAsset.id}/versions/${uploadedVersion.id}/upload` && request.method === "PUT") return jsonResponse(uploadedVersion);
      if (url.pathname === `/api/v1/content/assets/${uploadedAsset.id}/download` && request.method === "GET") {
        return { ...jsonResponse({}, 200), headers: new Headers({ "Content-Type": "text/plain", "Content-Disposition": 'attachment; filename="handoff.txt"' }), blob: async () => new Blob(["asset bytes"], { type: "text/plain" }) } as Response;
      }
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/resources`);
    await act(async () => { await waitFor(() => Boolean(document.querySelector(".content-ops__form"))); });
    expect(document.querySelector(".project-module-resources__card h3")?.textContent).toBe("Prompts");
    expect(pageText()).toContain("Profile and migration blockers");
    expect(pageText()).toContain("Canonical resource web facade is not confirmed");
    expect(pageText()).toContain("Materialization apply is unavailable");
    expect(projectLookups).toBe(2);

    const listRequests = requests.filter((item) => item.path === "/api/v1/content/assets?company_id=company-verified&limit=200");
    expect(listRequests.length).toBeGreaterThanOrEqual(1);
    expect(listRequests.every((item) => item.method === "GET" && item.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.some((item) => item.path.includes("company-from-parent"))).toBe(false);
    expect(requests.filter((item) => item.path === `/api/v1/projects/${projectId}`).every((item) => item.method === "GET" && item.authorization === "Bearer acceptance-access")).toBe(true);

    const fileInput = document.querySelector<HTMLInputElement>(".content-ops__file-field input[type='file']");
    expect(fileInput).toBeTruthy();
    const file = new File(["asset bytes"], "handoff.txt", { type: "text/plain" });
    await act(async () => {
      Object.defineProperty(fileInput, "files", { configurable: true, value: [file] });
      fileInput?.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await click(".content-ops__form button[type='submit']", () => pageText().includes("Uploaded v1."));

    const createRequest = requests.find((item) => item.path === "/api/v1/content/assets" && item.method === "POST");
    expect(createRequest).toMatchObject({
      method: "POST",
      authorization: "Bearer acceptance-access",
      body: JSON.stringify({ owner_company_id: "company-verified", visibility: "company", mime: "text/plain", title: "handoff.txt", tags: [] }),
    });
    const beginRequest = requests.find((item) => item.path === `/api/v1/content/assets/${uploadedAsset.id}/versions`);
    expect(beginRequest).toMatchObject({ method: "POST", authorization: "Bearer acceptance-access", body: JSON.stringify({ mime: "text/plain" }) });
    const uploadRequest = requests.find((item) => item.path === `/api/v1/content/assets/${uploadedAsset.id}/versions/${uploadedVersion.id}/upload`);
    expect(uploadRequest).toMatchObject({ method: "PUT", authorization: "Bearer acceptance-access", contentType: "text/plain" });
    expect(await uploadRequest?.binaryBody?.text()).toBe("asset bytes");
    expect(requests.some((item) => item.path.includes("upload_url") || item.path.includes("storage_key"))).toBe(false);

    createObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    revokeObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    anchorClickDescriptor = Object.getOwnPropertyDescriptor(HTMLAnchorElement.prototype, "click");
    const createObjectURL = vi.fn(() => "blob:asset-download");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const anchorClick = vi.fn(function (this: HTMLAnchorElement) { downloadedFile = { href: this.href, filename: this.download }; });
    Object.defineProperty(HTMLAnchorElement.prototype, "click", { configurable: true, value: anchorClick });
    await act(async () => {
      const downloadButton = [...document.querySelectorAll<HTMLButtonElement>(".content-ops__section button")].find((button) => button.textContent?.includes("Download selected version"));
      expect(downloadButton).toBeTruthy();
      downloadButton?.click();
      await waitFor(() => pageText().includes("Downloaded v1."));
    });
    const downloadRequest = requests.find((item) => item.path.startsWith(`/api/v1/content/assets/${uploadedAsset.id}/download?`));
    expect(downloadRequest).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    const downloadUrl = new URL(downloadRequest?.path ?? "", window.location.origin);
    expect(downloadUrl.searchParams.get("blob_version_id")).toBe(uploadedVersion.id);
    expect(downloadUrl.searchParams.get("proxy")).toBe("true");
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(anchorClick).toHaveBeenCalledOnce();
    expect(downloadedFile).toEqual({ href: "blob:asset-download", filename: "handoff.txt" });
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 1010)); });
    expect(requests.some((item) => item.path.startsWith("/api/") && !item.path.startsWith("/api/v1/"))).toBe(false);
  });

  it("uses the first-party Files module for scoped upload, row save and Content FileRef download", async () => {
    resourceFileRow = { row_id: "resource-file-1", body: { name: "Guide", target_path: "docs/guide.txt", file_ref: null, project_ids: [] } };
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      const overview = overviewPayload(url);
      if (overview) return overview;
      const modules = resourceModuleResponse(url, request);
      if (modules) return modules;
      if (url.pathname === "/api/v1/content/assets" && request.method === "GET") return jsonResponse({ items: [] });
      throw new Error(`Unexpected request: ${request.method} ${request.path}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));
    createObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    revokeObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    anchorClickDescriptor = Object.getOwnPropertyDescriptor(HTMLAnchorElement.prototype, "click");
    const createObjectURL = vi.fn(() => "blob:module-file");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const anchorClick = vi.fn(function (this: HTMLAnchorElement) { downloadedFile = { href: this.href, filename: this.download }; });
    Object.defineProperty(HTMLAnchorElement.prototype, "click", { configurable: true, value: anchorClick });

    await mountApp(`/projects/${projectId}/resources?module=mod_files`);
    await act(async () => { await waitFor(() => pageText().includes("Files")); });
    expect(pageText()).toContain("Local bind");
    await act(async () => { await waitFor(() => Boolean(document.querySelector(".module-runtime-table tbody tr"))); });
    const fileRow = [...document.querySelectorAll<HTMLTableRowElement>(".module-runtime-table tbody tr")].find((row) => row.textContent?.includes("Guide"));
    expect(fileRow).toBeTruthy();
    await act(async () => { fileRow?.querySelector("button")?.click(); await waitFor(() => Boolean(document.querySelector(".module-runtime-form"))); });
    await act(async () => { await waitFor(() => document.querySelector<HTMLInputElement>(".module-runtime-project-picker input[type='checkbox']")?.disabled === false); });

    const fileInput = document.querySelector<HTMLInputElement>(".module-runtime-field input[type='file']");
    expect(fileInput).toBeTruthy();
    const file = new File(["new guide"], "guide.txt", { type: "text/plain" });
    await act(async () => {
      Object.defineProperty(fileInput, "files", { configurable: true, value: [file] });
      fileInput?.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => { await waitFor(() => requests.some((request) => request.path === `/api/v1/cabinets/${cabinetId}/content/upload` && request.method === "POST")); });
    expect(requests.find((request) => request.path === `/api/v1/cabinets/${cabinetId}/content/upload`)).toMatchObject({ method: "POST", authorization: "Bearer acceptance-access" });

    await click(".module-runtime-form-actions button[type='submit']", () => requests.some((request) => request.path.endsWith("/runtime-modules/mod_files/data/files/resource-file-1") && request.method === "PATCH"));
    expect(resourceFileRow?.body.file_ref).toMatchObject({ asset_id: "uploaded-asset", blob_version_id: "uploaded-version" });
    expect(requests.some((request) => request.path === `/api/v1/cabinets/${cabinetId}/modules/mod_files/bound-projects` && request.method === "GET")).toBe(true);
    await act(async () => { await waitFor(() => [...document.querySelectorAll<HTMLTableRowElement>(".module-runtime-table tbody tr")].some((row) => row.textContent?.includes("Guide"))); });
    const savedFileRow = [...document.querySelectorAll<HTMLTableRowElement>(".module-runtime-table tbody tr")].find((row) => row.textContent?.includes("Guide"));
    if (!savedFileRow) throw new Error("The saved Files module row is unavailable.");
    await act(async () => { savedFileRow.querySelector("button")?.click(); await waitFor(() => Boolean(document.querySelector(".module-runtime-form .module-runtime-file-ref button"))); });

    const download = document.querySelector<HTMLButtonElement>(".module-runtime-form .module-runtime-file-ref button");
    expect(download).toBeTruthy();
    await act(async () => { download?.click(); await waitFor(() => createObjectURL.mock.calls.length === 1); });
    const assetRequest = requests.find((request) => request.path.startsWith("/api/v1/content/assets/uploaded-asset/download?"));
    const downloadQuery = new URL(assetRequest?.path ?? "", window.location.origin);
    expect(downloadQuery.searchParams.get("blob_version_id")).toBe("uploaded-version");
    expect(downloadQuery.searchParams.get("proxy")).toBe("1");
    expect(assetRequest?.authorization).toBe("Bearer acceptance-access");
    expect(downloadedFile).toEqual({ href: "blob:module-file", filename: "guide.txt" });
    expect(anchorClick).toHaveBeenCalledOnce();
  });

  it("renders Content API list errors without claiming an Assets success", async () => {
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      const overview = overviewPayload(url);
      if (overview) return overview;
      const modules = resourceModuleResponse(url, request);
      if (modules) return modules;
      if (url.pathname === "/api/v1/content/assets" && request.method === "GET") return jsonResponse({ detail: "private backend failure" }, 503);
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/resources`);
    await act(async () => { await waitFor(() => Boolean(document.querySelector(".content-ops__notice--error"))); });

    expect(document.querySelector(".content-ops__notice--error")?.textContent).toContain("The Content Service encountered an error. Try again.");
    expect(pageText()).not.toContain("Uploaded v");
    expect(requests.some((item) => item.path === "/api/v1/content/assets?company_id=company-2&limit=200" && item.method === "GET" && item.authorization === "Bearer acceptance-access")).toBe(true);
    expect(pageText()).not.toContain("private backend failure");
  });

  it("does not request Content API assets when the Project API cannot resolve company scope", async () => {
    let projectLookups = 0;
    const { requests } = capture((url, request) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      if (url.pathname === `/api/v1/projects/${projectId}`) {
        projectLookups += 1;
        return jsonResponse(projectLookups === 1 ? project : { ...project, company_id: undefined });
      }
      const overview = overviewPayload(url);
      if (overview) return overview;
      const modules = resourceModuleResponse(url, request);
      if (modules) return modules;
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/resources`);
    await act(async () => { await waitFor(() => pageText().includes("The Project API did not return a company_id")); });

    expect(document.querySelector("[role='alert']")?.textContent).toContain("Project company scope unavailable");
    expect(projectLookups).toBe(2);
    expect(requests.filter((item) => item.path.startsWith("/api/v1/content/assets"))).toEqual([]);
  });

  it("restores a direct chat session deep link and reads its transcript from the live API route", async () => {
    const { requests } = capture((url) => {
      const response = overviewPayload(url);
      if (response) return response;
      if (url.pathname === `/api/v1/cabinets/${cabinetId}/chats/sidebar`) return runtimePayload(url)!;
      if (url.pathname === `/api/v1/projects/${projectId}/chat/transcript`) return transcriptPayload();
      if (url.pathname === `/api/v1/projects/${projectId}/agent/sessions/${sessionId}/composer-draft`) return jsonResponse({ text: "", has_draft: false });
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/chat/${sessionId}`);
    await act(async () => {
      await waitFor(() => pageText().includes("Writing changes the project workspace."));
    });

    expect(pageText()).toContain("Chats");
    expect(pageText()).toContain("Inspect notes.md");
    expect(pageText()).toContain("Approval required for write_file");
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}`)).toBe(true);
    expect(requests.some((request) => request.path === `/api/v1/cabinets/${cabinetId}`)).toBe(true);
    expect(requests.some((request) => request.path.startsWith(`/api/v1/projects/${projectId}/chat/transcript?session_id=${sessionId}`)
      && request.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.some((request) => request.path.startsWith("/api/") && !request.path.startsWith("/api/v1/"))).toBe(false);
  });

  it("keeps direct workspace navigation loading until project access succeeds", async () => {
    let releaseProject!: (response: Response) => void;
    const pendingProject = new Promise<Response>((resolve) => { releaseProject = resolve; });
    const { requests } = capture((url) => {
      if (url.pathname === "/api/v1/me") return jsonResponse(backendIdentityResponse());
      if (url.pathname === `/api/v1/projects/${projectId}`) return pendingProject;
      const response = overviewPayload(url);
      if (response) return response;
      if (url.pathname === `/api/v1/projects/${projectId}/container/workspace/entries`) return jsonResponse({ path: url.searchParams.get("path") ?? "", entries: [] });
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/files`);
    await act(async () => { await waitFor(() => pageText().includes("Загружаем проект…")); });
    expect(pageText()).toContain("Загружаем проект…");
    expect(pageText()).not.toContain("Live files from the project Pod");

    await act(async () => {
      releaseProject(jsonResponse(project));
      await waitFor(() => pageText().includes("This folder is empty"));
    });
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/entries?path=`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
  });

  it("renders a Pod workspace error from the live entries route", async () => {
    let releaseEntries!: (response: Response) => void;
    const pendingEntries = new Promise<Response>((resolve) => { releaseEntries = resolve; });
    const { requests } = capture((url) => {
      const overview = overviewPayload(url);
      if (overview) return overview;
      if (url.pathname === `/api/v1/projects/${projectId}/container/workspace/entries`) {
        return pendingEntries;
      }
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/files`);
    await act(async () => { await waitFor(() => pageText().includes("Loading workspace…")); });
    expect(pageText()).toContain("Loading workspace…");

    await act(async () => {
      releaseEntries(jsonResponse({ code: "POD_NOT_RUNNING", title: "Conflict", detail: "pod is not running" }, 409));
      await waitFor(() => pageText().includes("The Pod is not running. Start the project Pod to access its files."));
    });

    expect(document.querySelector(".files-availability[role='status']")).toBeTruthy();
    expect(document.querySelector(".files-availability")?.textContent).toContain("Retry");
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/container/workspace/entries?path=`)).toMatchObject({ method: "GET", authorization: "Bearer acceptance-access" });
    expect(requests.some((request) => request.path.startsWith("/api/") && !request.path.startsWith("/api/v1/"))).toBe(false);
  });

  it("shows session history loading followed by the backend error response", async () => {
    let releaseSessions!: (response: Response) => void;
    const pendingSessions = new Promise<Response>((resolve) => { releaseSessions = resolve; });
    const { requests } = capture((url) => {
      const overview = overviewPayload(url);
      if (overview) return overview;
      if (url.pathname === `/api/v1/projects/${projectId}/agent/sessions`) return pendingSessions;
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));

    await mountApp(`/projects/${projectId}/runs`);
    await act(async () => { await waitFor(() => pageText().includes("Loading sessions…")); });
    expect(pageText()).toContain("Loading sessions…");

    await act(async () => {
      releaseSessions(jsonResponse({ code: "SERVICE_UNAVAILABLE", title: "Service Unavailable", detail: "Session history is temporarily unavailable." }, 503));
      await waitFor(() => pageText().includes("Session history is temporarily unavailable."));
    });
    expect(document.querySelector(".run-error[role='alert']")?.textContent).toContain("Session history is temporarily unavailable.");
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}/agent/sessions` && request.authorization === "Bearer acceptance-access")).toBe(true);
    expect(requests.some((request) => request.path.startsWith("/api/") && !request.path.startsWith("/api/v1/"))).toBe(false);
  });

  it("keeps safe next-path handling and explicit resource/equipment contract blockers", () => {
    const next = getSafeNextPath("?next=%2Fprojects%2Fp-1%2Fchat%2Fs-2%3Fmodel%3Dsmall%23latest");
    expect(next).toBe("/projects/p-1/chat/s-2?model=small#latest");
    expect(getSafeNextPath("?next=%2F%2Fevil.example%2Fprojects")).toBeUndefined();
    expect(getSafeNextPath("?next=%2Flogin%3Fnext%3D%2Fprojects")).toBeUndefined();
    expect(RESOURCE_CONTRACT_BLOCKERS.length).toBeGreaterThan(0);
    expect(RESOURCE_CONTRACT_BLOCKERS.some((blocker) => blocker.owner === "api")).toBe(true);
    expect(emptyEquipmentWorkspace().requestLines).toEqual([]);
    expect(emptyEquipmentWorkspace().offers).toEqual([]);
  });
});
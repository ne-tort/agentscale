// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ModuleAdminPage } from "./ModuleAdminPage";
import { createModuleAdminService } from "./service";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const moduleId = "mod-1";
const companyId = "co-1";
const cabinetId = "cab-1";
const projectId = "project-1";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface FakeApi {
  fetcher: typeof fetch;
  requests: Array<{ path: string; method: string; body?: Record<string, unknown> }>;
  setUnauthorized(value: boolean): void;
  setMutationFailure(value: boolean): void;
  setCompanyGrant(value: boolean): void;
  setCatalogEmpty(value: boolean): void;
}

function makeApi(): FakeApi {
  const requests: FakeApi["requests"] = [];
  let unauthorized = false;
  let mutationFailure = false;
  let companyGrant = false;
  let cabinetBound = true;
  let catalogEmpty = false;
  const module = () => ({
    id: moduleId,
    name: "Example module",
    status: "active",
    company_grant_scope: "selected",
    company_ids: companyGrant ? [companyId] : [],
    cabinet_ids: cabinetBound ? [cabinetId] : [],
    writable: true,
    source: "platform_assigned",
    cabinet_bindings_count: cabinetBound ? 1 : 0,
  });

  const fetcher: typeof fetch = async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : undefined;
    requests.push({ path: `${url.pathname}${url.search}`, method, body });
    if (unauthorized && url.pathname === "/api/v1/admin/modules" && method === "GET") {
      return response({ detail: "platform administrator required" }, 403);
    }
    if (url.pathname === "/api/v1/admin/modules" && method === "GET") return response({ items: catalogEmpty ? [] : [module()] });
    if (url.pathname === "/api/v1/admin/companies" && method === "GET") {
      return response({ items: [{ id: companyId, name: "Example company" }] });
    }
    if (url.pathname === `/api/v1/admin/modules/${moduleId}` && method === "GET") return response(module());
    if (url.pathname === `/api/v1/admin/modules/${moduleId}` && method === "PATCH") {
      if (mutationFailure) return response({ detail: "grant update unavailable" }, 503);
      if (Array.isArray(body?.company_ids)) companyGrant = (body.company_ids as string[]).includes(companyId);
      return response(module());
    }
    if (url.pathname === `/api/v1/admin/modules/${moduleId}/meta/documents`) return response({ items: [] });
    if (url.pathname === `/api/v1/admin/modules/${moduleId}/cabinets`) {
      return response({ items: [{ cabinet_id: cabinetId, cabinet_name: "Example cabinet", company_name: "Example company", bound: cabinetBound }] });
    }
    if (url.pathname === `/api/v1/admin/modules/${moduleId}/projects`) {
      return response({ items: [{ project_id: projectId, project_name: "Example project", company_id: companyId, company_name: "Example company", cabinet_id: cabinetId, bind_kind: "global", child_may_edit: false, agent_tokens_used: 2, agent_requests: 1 }] });
    }
    if (url.pathname === `/api/v1/companies/${companyId}/modules` && method === "GET") return response({ items: catalogEmpty ? [] : [module()] });
    if (url.pathname === `/api/v1/companies/${companyId}/modules/${moduleId}` && method === "GET") return response(module());
    if (url.pathname === `/api/v1/companies/${companyId}/modules/${moduleId}` && method === "PATCH") {
      if (mutationFailure) return response({ detail: "cabinet binding unavailable" }, 503);
      if (Array.isArray(body?.cabinet_ids)) cabinetBound = (body.cabinet_ids as string[]).includes(cabinetId);
      return response(module());
    }
    if (url.pathname === `/api/v1/companies/${companyId}/modules/${moduleId}/meta/documents`) return response({ items: [] });
    if (url.pathname === `/api/v1/companies/${companyId}/modules/${moduleId}/cabinets`) {
      return response({ items: [{ cabinet_id: cabinetId, cabinet_name: "Example cabinet", company_id: companyId, company_name: "Example company", bound: cabinetBound }] });
    }
    if (url.pathname === `/api/v1/companies/${companyId}/modules/${moduleId}/projects`) return response({ items: [] });
    if (url.pathname === `/api/v1/cabinets/${cabinetId}/modules`) {
      return response({ items: cabinetBound ? [{ module_id: moduleId, status: "active", bind_kind: "local", child_may_edit: true }] : [] });
    }
    return response({ detail: `unhandled fake route: ${method} ${url.pathname}` }, 404);
  };
  return {
    fetcher,
    requests,
    setUnauthorized: (value) => { unauthorized = value; },
    setMutationFailure: (value) => { mutationFailure = value; },
    setCompanyGrant: (value) => { companyGrant = value; },
    setCatalogEmpty: (value) => { catalogEmpty = value; },
  };
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mount(mode: "admin" | "company", api = makeApi()) {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  const service = createModuleAdminService({ fetcher: api.fetcher });
  await act(async () => root?.render(<MemoryRouter><ModuleAdminPage mode={mode} companyId={mode === "company" ? companyId : undefined} service={service} /></MemoryRouter>));
  return { container: host, api };
}

async function waitFor(condition: () => boolean): Promise<void> {
  const deadline = Date.now() + 3000;
  while (!condition()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for module binding acceptance condition");
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
  }
}

async function clickButton(container: HTMLElement, label: string): Promise<void> {
  const button = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.trim() === label);
  if (!button) throw new Error(`Button not found: ${label}`);
  await act(async () => button.click());
}

async function setInput(input: HTMLInputElement, value: string): Promise<void> {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("module binding owner workflow", () => {
  it("lists available modules and filters the catalog by search", async () => {
    const { container, api } = await mount("company");
    await waitFor(() => container.querySelector("[aria-label='Binding hierarchy']") !== null);
    expect(api.requests.some((request) => request.path === `/api/v1/companies/${companyId}/modules` && request.method === "GET")).toBe(true);
    expect(container.querySelector("[aria-label='Binding hierarchy']")?.textContent).toContain("Platform module");
    expect(container.textContent).toContain("no company-accessible global catalog for ungranted modules");
    // The sentence sits in the middle of a paragraph, so it is lowercase in the DOM;
    // the capitalised form only ever matched a copy of the copy, not the markup.
    expect(container.textContent).toContain("platform grants are managed by platform administrators");
    expect([...container.querySelectorAll("h3")].some((heading) => heading.textContent === "Company grants")).toBe(false);
    const search = container.querySelector<HTMLInputElement>("#module-search");
    expect(search).toBeTruthy();
    await setInput(search!, "no-such-module");
    expect(container.textContent).toContain("No modules match this search.");
    expect(container.querySelector(".module-admin__module-option")).toBeNull();
  });

  it("shows an explicit company empty-catalog state", async () => {
    const api = makeApi();
    api.setCatalogEmpty(true);
    const { container } = await mount("company", api);
    await waitFor(() => container.textContent?.includes("No modules are currently available to this company.") ?? false);
    expect(container.querySelector(".module-admin__details")).toBeNull();
  });

  it("confirms a company grant and verifies it from admin detail readback", async () => {
    const { container, api } = await mount("admin");
    await waitFor(() => container.querySelector(".module-admin__checks input[type=checkbox]") !== null);
    const companyLabel = [...container.querySelectorAll<HTMLLabelElement>(".module-admin__checks label")]
      .find((label) => label.textContent?.includes("Example company"));
    expect(companyLabel).toBeTruthy();
    await act(async () => companyLabel!.querySelector("input")?.click());
    await clickButton(container, "Review grant changes");
    expect(container.querySelector("[role=alertdialog]")?.textContent).toContain("bind 1, revoke 0");
    await clickButton(container, "Confirm");
    await waitFor(() => container.textContent?.includes("Company grants updated and verified.") ?? false);
    expect(api.requests.some((request) => request.path === `/api/v1/admin/modules/${moduleId}` && request.method === "PATCH" && JSON.stringify(request.body) === JSON.stringify({ company_ids: [companyId] }))).toBe(true);
    expect(api.requests.filter((request) => request.path === `/api/v1/admin/modules/${moduleId}` && request.method === "GET").length).toBeGreaterThanOrEqual(2);
  });

  it("confirms grant revocation, warns about cascaded bindings, and verifies empty readback", async () => {
    const api = makeApi();
    api.setCompanyGrant(true);
    const { container } = await mount("admin", api);
    await waitFor(() => [...container.querySelectorAll(".module-admin__checks label")].some((label) => label.textContent?.includes("Example company")));
    const companyLabel = [...container.querySelectorAll<HTMLLabelElement>(".module-admin__checks label")]
      .find((label) => label.textContent?.includes("Example company"));
    expect(companyLabel?.querySelector<HTMLInputElement>("input")?.checked).toBe(true);
    await act(async () => companyLabel?.querySelector("input")?.click());
    await clickButton(container, "Review grant changes");
    expect(container.querySelector("[role=alertdialog]")?.textContent).toContain("bind 0, revoke 1");
    expect(container.querySelector("[role=alertdialog]")?.textContent).toContain("removes this company's cabinet bindings and dependent project bindings");
    await clickButton(container, "Confirm");
    await waitFor(() => container.textContent?.includes("Company grants updated and verified.") ?? false);
    const updatedLabel = [...container.querySelectorAll<HTMLLabelElement>(".module-admin__checks label")]
      .find((label) => label.textContent?.includes("Example company"));
    expect(updatedLabel?.querySelector<HTMLInputElement>("input")?.checked).toBe(false);
  });

  it("confirms a company cabinet removal and checks the unbound API readback", async () => {
    const { container, api } = await mount("company");
    await waitFor(() => [...container.querySelectorAll(".module-admin__checks label")].some((label) => label.textContent?.includes("Example cabinet")));
    const cabinetLabel = [...container.querySelectorAll<HTMLLabelElement>(".module-admin__checks label")]
      .find((label) => label.textContent?.includes("Example cabinet"));
    expect(cabinetLabel?.querySelector<HTMLInputElement>("input")?.checked).toBe(true);
    await act(async () => cabinetLabel?.querySelector("input")?.click());
    await clickButton(container, "Review cabinet changes");
    expect(container.querySelector("[role=alertdialog]")?.textContent).toContain("revoke 1");
    expect(container.querySelector("[role=alertdialog]")?.textContent).toContain("uninstalls the module and revokes its project bindings");
    await clickButton(container, "Confirm");
    await waitFor(() => container.textContent?.includes("Cabinet bindings updated and verified.") ?? false);
    expect(api.requests.some((request) => request.path === `/api/v1/companies/${companyId}/modules/${moduleId}` && request.method === "PATCH" && JSON.stringify(request.body) === JSON.stringify({ cabinet_ids: [] }))).toBe(true);
    expect(api.requests.filter((request) => request.path === `/api/v1/companies/${companyId}/modules/${moduleId}/cabinets`).length).toBeGreaterThanOrEqual(2);
    const updatedCabinetLabel = [...container.querySelectorAll<HTMLLabelElement>(".module-admin__checks label")]
      .find((label) => label.textContent?.includes("Example cabinet"));
    expect(updatedCabinetLabel?.querySelector<HTMLInputElement>("input")?.checked).toBe(false);
  });

  it("surfaces a 403 and retries the catalog without exposing mutation controls", async () => {
    const api = makeApi();
    api.setUnauthorized(true);
    const mounted = await mount("admin", api);
    await waitFor(() => mounted.container.querySelector("[role=alert]") !== null);
    expect(mounted.container.querySelector("[role=alert]")?.textContent).toContain("platform administrator required");
    expect(mounted.container.querySelector(".module-admin__details")).toBeNull();
    expect([...mounted.container.querySelectorAll("button")].some((button) => button.textContent?.includes("Review grant"))).toBe(false);
    expect(mounted.container.querySelector<HTMLButtonElement>(".module-admin__create button")?.disabled).toBe(true);
    api.setUnauthorized(false);
    await clickButton(mounted.container, "Retry");
    await waitFor(() => mounted.container.querySelector(".module-admin__details h2")?.textContent === "Example module");
    expect(api.requests.filter((request) => request.path === "/api/v1/admin/modules" && request.method === "GET").length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the confirmed change dialog open on API failure and offers retry", async () => {
    const api = makeApi();
    api.setMutationFailure(true);
    const { container } = await mount("admin", api);
    await waitFor(() => container.querySelector(".module-admin__checks input[type=checkbox]") !== null);
    const companyLabel = [...container.querySelectorAll<HTMLLabelElement>(".module-admin__checks label")]
      .find((label) => label.textContent?.includes("Example company"));
    await act(async () => companyLabel?.querySelector("input")?.click());
    await clickButton(container, "Review grant changes");
    await clickButton(container, "Confirm");
    await waitFor(() => container.querySelector("[role=alert]")?.textContent?.includes("grant update unavailable") ?? false);
    expect(container.querySelector("[role=alertdialog]")).toBeTruthy();
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Retry")).toBe(true);
    expect(container.textContent).not.toContain("Company grants updated and verified.");
    api.setMutationFailure(false);
    await clickButton(container, "Retry");
    await waitFor(() => container.querySelector(".module-admin__details h2")?.textContent === "Example module" && container.querySelector("[role=alert]") === null);
    expect(container.querySelector("[role=alertdialog]")).toBeTruthy();
    await clickButton(container, "Confirm");
    await waitFor(() => container.textContent?.includes("Company grants updated and verified.") ?? false);
  });

  it("links existing project bindings to project management and labels missing version/status actions", async () => {
    const { container } = await mount("admin");
    await waitFor(() => container.querySelector(".module-admin__details h2")?.textContent === "Example module");
    expect(container.querySelector(`a[href='/projects/${projectId}/settings']`)?.textContent).toBe("Example project");
    expect(container.querySelector("[aria-label='Binding hierarchy']")?.textContent).toContain("Enable/disable and version actions are unavailable");
    expect(container.textContent).toContain("API contract gap");
    expect(container.textContent).toContain("Create or remove project bindings from project management.");
    // Scope the check to the project-bindings section: a catalog row is itself a
    // button whose description reads "<n> cabinet binds", so matching "/bind/"
    // across the whole document flagged descriptive copy, not a mutation control.
    const projectSection = [...container.querySelectorAll(".module-admin__section")]
      .find((section) => section.querySelector("h3")?.textContent === "Project bindings");
    expect(projectSection).toBeDefined();
    expect([...projectSection!.querySelectorAll("button")].some((button) => /bind|revoke|manage/i.test(button.textContent ?? ""))).toBe(false);
  });
});
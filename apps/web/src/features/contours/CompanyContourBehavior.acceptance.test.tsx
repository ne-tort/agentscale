// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import { CompanyContour } from "./CompanyContour";
import { createContourService, type ContourRow } from "./service";
import { POLL_BUDGET_MS } from "../../test-support/poll-budget";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

interface EmployeeApi {
  fetcher: typeof fetch;
  requests: Array<{ path: string; method: string; body?: Record<string, unknown> }>;
  failNextDelete(): void;
  failNextPassword(): void;
  failNextEmployeeList(): void;
  emptyCompanyOne(): void;
  holdCompanyOneList(): () => void;
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function makeApi(): EmployeeApi {
  const requests: EmployeeApi["requests"] = [];
  const employees = new Map<string, ContourRow[]>([
    ["co-1", [{ id: "employee-1", login: "alex", display_name: "Alex", contact_email: "alex@example.test", role: "member", status: "active", deleted_at: null }]],
    ["co-2", [{ id: "employee-2", login: "bea", display_name: "Bea", contact_email: null, role: "member", status: "active", deleted_at: null }]],
  ]);
  let failDelete = false;
  let failPassword = false;
  let failEmployeeList = false;
  let emptyCoOne = false;
  let heldResolve: (() => void) | undefined;
  let holdCoOne = false;
  const fetcher: typeof fetch = async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const path = `${url.pathname}${url.search}`;
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : undefined;
    requests.push({ path, method, body });
    const companyList = path.match(/^\/api\/v1\/companies\/([^/]+)\/employees(?:\?include_deleted=true)?$/);
    if (companyList && method === "GET") {
      const companyId = decodeURIComponent(companyList[1]!);
      if (companyId === "co-1" && !url.search && failEmployeeList) {
        failEmployeeList = false;
        return response({ detail: "employee list unavailable" }, 503);
      }
      if (companyId === "co-1" && holdCoOne) {
        holdCoOne = false;
        await new Promise<void>((resolve) => { heldResolve = resolve; });
      }
      const rows = companyId === "co-1" && emptyCoOne ? [] : (employees.get(companyId) ?? []);
      const deletedView = url.searchParams.get("include_deleted") === "true";
      return response({ items: rows.filter((row) => Boolean(row.deleted_at) === deletedView) });
    }
    if (method === "POST" && /^\/api\/v1\/companies\/[^/]+\/employees$/.test(path)) {
      const companyId = decodeURIComponent(url.pathname.split("/")[4]!);
      const row = { id: `employee-${Date.now()}`, login: body?.login, display_name: body?.display_name, contact_email: body?.contact_email, role: body?.role, status: "invited", deleted_at: null } satisfies ContourRow;
      employees.set(companyId, [...(employees.get(companyId) ?? []), row]);
      return response({ id: row.id, login: row.login, status: row.status, email: row.login }, 201);
    }
    const employeeAction = path.match(/^\/api\/v1\/employees\/([^/]+)(?:\/(disable|enable|restore))?$/);
    if (employeeAction) {
      const employeeId = decodeURIComponent(employeeAction[1]!);
      const action = employeeAction[2] ?? (method === "DELETE" ? "delete" : "");
      if (action === "delete" && failDelete) { failDelete = false; return response({ detail: "delete temporarily unavailable" }, 503); }
      for (const [companyId, rows] of employees) {
        const target = rows.find((row) => row.id === employeeId);
        if (!target) continue;
        if (action === "disable") target.status = "disabled";
        if (action === "enable") target.status = "active";
        if (action === "delete") { target.status = "disabled"; target.deleted_at = "2026-11-03T10:00:00Z"; }
        if (action === "restore") { target.status = "disabled"; target.deleted_at = null; }
        employees.set(companyId, [...rows]);
        return response({ id: employeeId, status: target.status, deleted: Boolean(target.deleted_at), restored: action === "restore" });
      }
      return response({ detail: "Employee not found" }, 404);
    }
    if (path.endsWith("/contact-email") && method === "PATCH") {
      const employeeId = url.pathname.split("/").at(-2)!;
      for (const rows of employees.values()) {
        const row = rows.find((item) => item.id === employeeId);
        if (row) { row.contact_email = body?.contact_email; return response({ id: row.id, contact_email: row.contact_email }); }
      }
    }
    if (path.endsWith("/password") && method === "PUT") {
      if (failPassword) { failPassword = false; return response({ detail: `Password rejected: ${String(body?.password ?? "")}` }, 422); }
      return response({ id: url.pathname.split("/").at(-2), password_set: true });
    }
    return response({ items: [] });
  };
  return {
    fetcher,
    requests,
    failNextDelete: () => { failDelete = true; },
    failNextPassword: () => { failPassword = true; },
    failNextEmployeeList: () => { failEmployeeList = true; },
    emptyCompanyOne: () => { emptyCoOne = true; },
    holdCompanyOneList: () => {
      holdCoOne = true;
      return () => heldResolve?.();
    },
  };
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mount(companyId = "co-1", api = makeApi()) {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  const service = createContourService({ fetcher: api.fetcher });
  await act(async () => root?.render(<SettingsProvider><CompanyContour companyId={companyId} service={service} /></SettingsProvider>));
  return { container: host, api, service };
}

async function waitFor(condition: () => boolean): Promise<void> {
  const deadline = Date.now() + POLL_BUDGET_MS;
  while (!condition()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for company employee acceptance condition");
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

async function setSelect(select: HTMLSelectElement, value: string): Promise<void> {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function inputFor(container: HTMLElement, label: string): HTMLInputElement {
  const field = [...container.querySelectorAll<HTMLLabelElement>("label")].find((item) => item.textContent?.startsWith(label));
  const input = field?.querySelector<HTMLInputElement>("input");
  if (!input) throw new Error(`Input not found for field: ${label}`);
  return input;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("company employee lifecycle acceptance", () => {
  it("shows loading while the company employee list is pending", async () => {
    const api = makeApi();
    const release = api.holdCompanyOneList();
    const { container } = await mount("co-1", api);
    expect(container.textContent).toContain("Загрузка…");
    await act(async () => { release(); });
    await waitFor(() => container.textContent?.includes("Alex") ?? false);
  });

  it("shows an employee-list error with a working retry", async () => {
    const api = makeApi();
    api.failNextEmployeeList();
    const { container } = await mount("co-1", api);
    await waitFor(() => container.querySelector("[role=alert]")?.textContent?.includes("employee list unavailable") ?? false);
    await clickButton(container, "Повторить");
    await waitFor(() => container.textContent?.includes("Alex") ?? false);
  });

  it("shows an employee empty state", async () => {
    const emptyApi = makeApi();
    emptyApi.emptyCompanyOne();
    const emptyMount = await mount("co-1", emptyApi);
    await waitFor(() => emptyMount.container.textContent?.includes("У этой компании нет сотрудников.") ?? false);
    expect(emptyMount.container.querySelector("table")).toBeNull();
  });

  it("creates employees with role/contact email, preserves the active list, and clears the password control", async () => {
    const { container, api } = await mount();
    await waitFor(() => container.textContent?.includes("Alex") ?? false);
    await clickButton(container, "Добавить сотрудника");
    await setInput(inputFor(container, "Логин"), "sam");
    await setInput(inputFor(container, "Пароль"), "do-not-show-this");
    await setInput(inputFor(container, "Имя"), "Sam Example");
    await setSelect(container.querySelector<HTMLSelectElement>("select[aria-label='Роль']")!, "company.admin");
    await setInput(inputFor(container, "Контактный email"), "sam@example.test");
    await clickButton(container, "Создать сотрудника");
    await waitFor(() => container.textContent?.includes("Сотрудник создан и проверен.") ?? false);
    expect(container.textContent).toContain("Alex");
    expect(container.textContent).toContain("Sam Example");
    expect(container.textContent).not.toContain("do-not-show-this");
    const createRequest = api.requests.find((request) => request.method === "POST" && request.path === "/api/v1/companies/co-1/employees");
    expect(createRequest?.body).toEqual({ login: "sam", password: "do-not-show-this", display_name: "Sam Example", role: "company.admin", contact_email: "sam@example.test" });
    await clickButton(container, "Добавить сотрудника");
    expect(inputFor(container, "Пароль").value).toBe("");
  });

  it("confirms disable/delete/restore, keeps a disabled employee in the active table, and retries failures", async () => {
    const api = makeApi();
    const { container } = await mount("co-1", api);
    await waitFor(() => container.textContent?.includes("Alex") ?? false);
    await clickButton(container, "Отключить");
    expect(container.textContent).toContain("Подтвердить отключение");
    await clickButton(container, "Подтвердить отключение");
    await waitFor(() => container.textContent?.includes("Сотрудник отключён и проверен.") ?? false);
    expect(container.textContent).toContain("Alex");
    expect(container.textContent).toContain("Отключён");
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Включить")).toBe(true);
    await clickButton(container, "Включить");
    await waitFor(() => container.textContent?.includes("Сотрудник включён и проверен.") ?? false);
    expect(container.textContent).toContain("Активен");

    api.failNextDelete();
    await clickButton(container, "Удалить");
    expect(container.textContent).toContain("Подтвердить удаление");
    await clickButton(container, "Подтвердить удаление");
    await waitFor(() => container.querySelector("[role=alert]")?.textContent?.includes("delete temporarily unavailable") ?? false);
    expect(container.textContent).toContain("Alex");
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Повторить")).toBe(true);
    await clickButton(container, "Повторить");
    await waitFor(() => container.textContent?.includes("Сотрудник удалён и проверен.") ?? false);
    expect(container.textContent).not.toContain("Alex");

    const listFilter = container.querySelector<HTMLSelectElement>("select");
    expect(listFilter).toBeTruthy();
    await setSelect(listFilter!, "deleted");
    await waitFor(() => container.textContent?.includes("Alex") ?? false);
    expect(container.textContent).toContain("2026-11-03T10:00:00Z");
    await clickButton(container, "Восстановить");
    expect(container.textContent).toContain("Подтвердить восстановление");
    await clickButton(container, "Подтвердить восстановление");
    await waitFor(() => container.textContent?.includes("Сотрудник восстановлен и проверен.") ?? false);
    expect(container.textContent).not.toContain("Alex");
    await setSelect(listFilter!, "active");
    await waitFor(() => container.textContent?.includes("Alex") ?? false);
    expect(container.textContent).toContain("Отключён");
  });

  it("preserves contact/password editing, clears and redacts passwords, and retains retry on failure", async () => {
    const api = makeApi();
    api.failNextPassword();
    const { container } = await mount("co-1", api);
    await waitFor(() => container.textContent?.includes("Alex") ?? false);
    const editButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Изменить контакты и пароль");
    expect(editButton).toBeTruthy();
    await act(async () => editButton!.click());
    await setInput(inputFor(container, "Контактный email"), "new@example.test");
    await setInput(inputFor(container, "Новый пароль"), "another-secret");
    await clickButton(container, "Сохранить сотрудника");
    // The fixture's 422 echoes the submitted password inside `detail` — the exact
    // shape a careless backend produces. Production must therefore surface a
    // failure *and* drop the message: expecting the echoed detail here asserted
    // the opposite of the redaction this test also verifies.
    await waitFor(() => container.querySelector("[role=alert]")?.textContent?.includes("Не удалось выполнить действие с сотрудником.") ?? false);
    expect(container.textContent).not.toContain("another-secret");
    expect(inputFor(container, "Новый пароль").value).toBe("");
    await setInput(inputFor(container, "Новый пароль"), "another-secret");
    await clickButton(container, "Сохранить сотрудника");
    await waitFor(() => container.textContent?.includes("Данные сотрудника сохранены и проверены.") ?? false);
    expect(container.textContent).not.toContain("another-secret");
    expect(api.requests.some((request) => request.method === "PATCH" && request.path.endsWith("/contact-email"))).toBe(true);
    expect(api.requests.some((request) => request.method === "PUT" && request.path.endsWith("/password"))).toBe(true);

    const employeeListGets = api.requests.filter((request) => request.method === "GET" && request.path === "/api/v1/companies/co-1/employees").length;
    expect(employeeListGets).toBeGreaterThan(0);
    const updatedEditButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Изменить контакты и пароль");
    await act(async () => updatedEditButton?.click());
    expect(inputFor(container, "Новый пароль").value).toBe("");
  });

  it("ignores stale employee-list responses after company scope changes", async () => {
    const api = makeApi();
    const release = api.holdCompanyOneList();
    const { container, service } = await mount("co-1", api);
    await act(async () => root?.render(<SettingsProvider><CompanyContour companyId="co-2" service={service} /></SettingsProvider>));
    await waitFor(() => container.textContent?.includes("Bea") ?? false);
    expect(container.textContent).not.toContain("Alex");
    await act(async () => { release(); await new Promise((resolve) => window.setTimeout(resolve, 0)); });
    expect(container.textContent).toContain("Bea");
    expect(container.textContent).not.toContain("Alex");
  });

  it("cancels an employee-ID lifecycle call when the company changes during preflight", async () => {
    const api = makeApi();
    const { container, service } = await mount("co-1", api);
    await waitFor(() => container.textContent?.includes("Alex") ?? false);
    const release = api.holdCompanyOneList();
    await clickButton(container, "Отключить");
    await clickButton(container, "Подтвердить отключение");
    await waitFor(() => api.requests.filter((request) => request.path === "/api/v1/companies/co-1/employees").length >= 2);

    await act(async () => root?.render(<SettingsProvider><CompanyContour companyId="co-2" service={service} /></SettingsProvider>));
    await waitFor(() => container.textContent?.includes("Bea") ?? false);
    await act(async () => { release(); await new Promise((resolve) => window.setTimeout(resolve, 0)); });
    expect(api.requests.some((request) => request.path === "/api/v1/employees/employee-1/disable")).toBe(false);
    expect(container.textContent).toContain("Bea");
    expect(container.textContent).not.toContain("Alex");
  });
});
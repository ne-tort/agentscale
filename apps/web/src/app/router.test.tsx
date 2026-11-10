// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, matchPath, useLocation } from "react-router-dom";
import { SettingsProvider } from "../preferences/SettingsProvider";
import type { ApiClient, Project, ProjectRuntimeModule } from "../api/types";
import { AppRouter, EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS } from "./router";
import { POLL_BUDGET_MS } from "../test-support/poll-budget";

const harness = vi.hoisted(() => ({
  api: undefined as unknown,
  auth: undefined as unknown,
  setProject: vi.fn(),
}));

vi.mock("./context", () => ({
  useApi: () => harness.api,
  useAuth: () => harness.auth,
  useWorkContext: () => ({ setProject: harness.setProject }),
}));

vi.mock("../routes/pages", async () => {
  const { useOutletContext } = await import("react-router-dom");
  const page = (name: string) => (props: Record<string, unknown>) => {
    const context = useOutletContext<Project | undefined>();
    const supplied = props.project as Project | undefined;
    return <div data-testid={`page-${name}`} data-project-id={supplied?.id ?? context?.id ?? ""}>{name}</div>;
  };
  return {
    AdminPage: page("admin"),
    CabinetPage: page("cabinet"),
    CabinetSelectionPage: page("cabinet-selection"),
    CompanyPage: page("company"),
    ContentOperationsPage: page("content"),
    EquipmentPage: page("equipment"),
    LoginPage: page("login"),
    ModuleBindingPage: page("module"),
    NotFoundPage: page("not-found"),
    ProjectChatPage: page("chat"),
    ProjectFilesPage: page("files"),
    ProjectModelsPage: page("models"),
    ProjectOverviewPage: page("settings"),
    ProjectRuntimePage: page("runtime"),
    ProjectsPage: page("projects"),
    ProjectResourcesPage: page("resources"),
    RunsPage: page("runs"),
  };
});

vi.mock("../features/automations", () => ({
  AutomationsPage: () => <div data-testid="page-automations">automations</div>,
}));

const project: Project = {
  id: "project-71",
  cabinet_id: "cabinet-19",
  company_id: "company-2",
  name: "Signal Lab",
  status: "active",
  observed_state: "running",
};
const cabinet = { id: "cabinet-19", name: "Northwind", description: "Research team" };
const moduleRecords: ProjectRuntimeModule[] = [{ id: "mod-files", module_id: "mod-files", name: "Project files" }];
const auth = {
  employee: { id: "employee-4", display_name: "Ada Lovelace", email: "ada@example.test" },
  isLoading: false,
  isAuthenticated: true,
  signOut: vi.fn(),
};

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}{location.search}{location.hash}</output>;
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mount(initialPath: string): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<MemoryRouter initialEntries={[initialPath]}><SettingsProvider><AppRouter /><LocationProbe /></SettingsProvider></MemoryRouter>));
}

async function waitFor(assertion: () => boolean): Promise<void> {
  const deadline = Date.now() + POLL_BUDGET_MS;
  while (!assertion()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for router state");
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
  }
}

async function click(selector: string): Promise<void> {
  await act(async () => {
    const element = host?.querySelector<HTMLElement>(selector);
    if (!element) throw new Error(`Missing element ${selector}`);
    element.click();
  });
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  document.body.replaceChildren();
});

beforeEach(() => {
  harness.setProject.mockClear();
  auth.signOut.mockClear();
  harness.auth = auth;
  harness.api = {
    getProject: vi.fn().mockResolvedValue(project),
    getCabinet: vi.fn().mockResolvedValue(cabinet),
    listProjectRuntimeModules: vi.fn().mockResolvedValue({ items: moduleRecords }),
  } as unknown as ApiClient;
});

describe("AppRouter workspace integration", () => {
  it("keeps direct project and child URLs addressable", () => {
    expect(EMPLOYEE_PROJECT_CHILD_ROUTE_PATHS).toEqual([
      "runtime/*", "chat", "chat/:sessionId", "files/*", "runs", "approvals", "resources/*",
      "content", "equipment", "automations", "models", "settings", "modules/:moduleId/*",
    ]);
    expect(matchPath("/projects/:projectId", "/projects/p-123")?.params.projectId).toBe("p-123");
    expect(matchPath("/projects/:projectId/chat/:sessionId", "/projects/p-123/chat/s-456")?.params).toEqual({ projectId: "p-123", sessionId: "s-456" });
    expect(matchPath("/projects/:projectId/modules/:moduleId/*", "/projects/p-123/modules/m-1/docs/a.txt")?.params).toMatchObject({ projectId: "p-123", moduleId: "m-1" });
  });

  it("requires auth and preserves the complete deep link for login recovery", async () => {
    harness.auth = { ...auth, isAuthenticated: false };
    await mount("/projects/project-71/files/docs/a.txt?path=docs#preview");
    await waitFor(() => host?.querySelector("[data-testid='page-login']") !== null);
    const location = new URLSearchParams(host?.querySelector("[data-testid='location']")?.textContent?.split("?")[1] ?? "").get("next");
    expect(location).toBe("/projects/project-71/files/docs/a.txt?path=docs#preview");
    expect(harness.api && (harness.api as ApiClient).getProject).not.toHaveBeenCalled();
  });

  it("denies private plugin route without session before bootstrap", async () => {
    const getPluginBootstrap = vi.fn();
    const getPublicPluginBootstrap = vi.fn();
    harness.api = { ...harness.api as ApiClient, getPluginBootstrap, getPublicPluginBootstrap };
    harness.auth = { ...auth, isAuthenticated: false, tokens: undefined };
    await mount("/plugins/mod_equipment/co_123/docs/private");
    await waitFor(() => host?.querySelector("[data-testid='page-login']") !== null);
    expect(getPluginBootstrap).not.toHaveBeenCalled();
    expect(getPublicPluginBootstrap).not.toHaveBeenCalled();
  });

  it("serves nested public plugin route without session and never falls back to private/admin bootstrap", async () => {
    const getPluginBootstrap = vi.fn();
    const me = vi.fn();
    const listAdminPlugins = vi.fn();
    const getPublicPluginBootstrap = vi.fn(async (_company: string, _plugin: string, routePath: string) => ({
      plugin_id: "mod_equipment",
      manifest_version: "1.2.0",
      artifact_digest: `sha256:${"a".repeat(64)}`,
      static_origin: "https://plugin-origin.agentscale.dev",
      plugin_shell_origin: "https://agentscale.dev",
      frontend_entry: "shell.html",
      frontend_base_path: "/signed/public",
      route_path: routePath,
      capabilities: ["hello.greetings@1.0"],
      rpc_operations: [{
        capability_id: "hello.greetings",
        capability_version: "1.0",
        operation: "list",
        http_method: "GET",
        backend_path: "/api/greetings/list",
        public: true,
      }],
      public_pages: ["/docs/welcome"],
      scope: { company_id: "co_123" },
      grant_expires_at: null,
    }));
    harness.api = {
      ...harness.api as ApiClient,
      getPluginBootstrap,
      getPublicPluginBootstrap,
      me,
      listAdminPlugins,
    };
    harness.auth = { ...auth, isAuthenticated: false, tokens: undefined };
    await mount("/public/plugins/co_123/mod_equipment/docs/welcome");
    await waitFor(() => host?.querySelector("iframe")?.getAttribute("src")
      === "https://plugin-origin.agentscale.dev/signed/public/shell.html");
    expect(getPublicPluginBootstrap).toHaveBeenCalledWith("co_123", "mod_equipment", "/docs/welcome");
    expect(getPluginBootstrap).not.toHaveBeenCalled();
    expect(me).not.toHaveBeenCalled();
    expect(listAdminPlugins).not.toHaveBeenCalled();
    expect(host?.querySelector("[data-testid='page-login']")).toBeNull();
    expect(host?.querySelector(".plugin-frame")?.getAttribute("data-plugin-public")).toBe("true");
  });

  it("keeps private nested plugin route on authenticated private bootstrap", async () => {
    const getPublicPluginBootstrap = vi.fn();
    const getPluginBootstrap = vi.fn(async (_company: string, _plugin: string, routePath: string) => ({
      plugin_id: "mod_equipment",
      manifest_version: "1.2.0",
      artifact_digest: `sha256:${"a".repeat(64)}`,
      static_origin: "https://plugin-origin.agentscale.dev",
      plugin_shell_origin: "https://agentscale.dev",
      frontend_entry: "index.html",
      frontend_base_path: "/signed/private",
      route_path: routePath,
      capabilities: [],
      rpc_operations: [],
      public_pages: [],
      scope: { company_id: "co_123" },
      grant_expires_at: null,
    }));
    harness.api = { ...harness.api as ApiClient, getPluginBootstrap, getPublicPluginBootstrap };
    harness.auth = {
      ...auth,
      isAuthenticated: true,
      tokens: { access_token: "private-access", refresh_token: "private-refresh" },
    };
    await mount("/plugins/mod_equipment/co_123/docs/private");
    await waitFor(() => host?.querySelector("iframe")?.getAttribute("src")
      === "https://plugin-origin.agentscale.dev/signed/private/index.html");
    expect(getPluginBootstrap).toHaveBeenCalledWith("co_123", "mod_equipment", "/docs/private");
    expect(getPublicPluginBootstrap).not.toHaveBeenCalled();
    expect(host?.querySelector(".plugin-frame")?.getAttribute("data-plugin-public")).toBe("false");
  });

  it("leaves the company application route under the existing application header", async () => {
    await mount("/company/modules");
    await waitFor(() => host?.querySelector("[data-testid='page-company']") !== null);
    expect(host?.querySelector(".employee-app-header nav")).not.toBeNull();
    expect(host?.querySelector(".workspace-shell")).toBeNull();
  });

  it("loads real project, cabinet and employee context and uses the shell sign-out action", async () => {
    await mount("/projects/project-71");
    await waitFor(() => host?.querySelector("[data-testid='page-runtime']") !== null);
    expect(host?.querySelector("[data-testid='page-runtime']")?.getAttribute("data-project-id")).toBe("project-71");
    expect(host?.querySelector(".workspace-shell__context-name")?.textContent).toBe("Northwind");
    expect(host?.querySelector(".workspace-shell__context-description")?.textContent).toBe("Research team");
    expect(host?.querySelector(".workspace-shell__account-trigger")?.getAttribute("aria-label")).toContain("Ada Lovelace");
    expect(harness.setProject).toHaveBeenCalledWith(project);
    expect((harness.api as ApiClient).getProject).toHaveBeenCalledWith("project-71");
    expect((harness.api as ApiClient).getCabinet).toHaveBeenCalledWith("cabinet-19");
    await click(".workspace-shell__account-trigger");
    await click(".workspace-shell__account-menu-item--danger");
    expect(auth.signOut).toHaveBeenCalledOnce();
  });

  it("navigates project views through the shell and retains URL search/hash on direct deep links", async () => {
    await mount("/projects/project-71/files/docs/a.txt?path=docs#preview");
    await waitFor(() => host?.querySelector("[data-testid='page-files']") !== null);
    expect(host?.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-71/files/docs/a.txt?path=docs#preview");
    expect(host?.querySelector(".workspace-shell__nav-link[aria-current='page']")?.getAttribute("href")).toBe("/projects/project-71/files");
    expect(host?.querySelector("[data-testid='page-files']")?.getAttribute("data-project-id")).toBe("project-71");
    await click(".workspace-shell__nav-link[href='/projects/project-71/approvals']");
    await waitFor(() => host?.querySelector(".workspace-shell__nav-link[aria-current='page']")?.getAttribute("href") === "/projects/project-71/approvals");
    expect(host?.querySelector("[data-testid='page-runs']")).not.toBeNull();
    expect(host?.querySelector("[data-testid='page-runs']")?.getAttribute("data-project-id")).toBe("project-71");

    const destinations: Array<[string, string]> = [
      ["/projects/project-71", "runtime"],
      ["/projects/project-71/runtime", "runtime"],
      ["/projects/project-71/chat", "chat"],
      ["/projects/project-71/files", "files"],
      ["/projects/project-71/runs", "runs"],
      ["/projects/project-71/approvals", "runs"],
      ["/projects/project-71/resources", "resources"],
      ["/projects/project-71/content", "content"],
      ["/projects/project-71/equipment", "equipment"],
      ["/projects/project-71/automations", "automations"],
      ["/projects/project-71/models", "models"],
      ["/projects/project-71/settings", "settings"],
    ];
    for (const [href, pageName] of destinations) {
      await click(`.workspace-shell__nav-link[href='${href}']`);
      await waitFor(() => host?.querySelector(".workspace-shell__nav-link[aria-current='page']")?.getAttribute("href") === href);
      expect(host?.querySelector(`[data-testid='page-${pageName}']`)).not.toBeNull();
    }
  });

  it("keeps chat-session deep links intact in the URL and outlet project context", async () => {
    await mount("/projects/project-71/chat/session-8?model=swift#latest");
    await waitFor(() => host?.querySelector("[data-testid='page-chat']") !== null);
    expect(host?.querySelector("[data-testid='page-chat']")?.getAttribute("data-project-id")).toBe("project-71");
    expect(host?.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-71/chat/session-8?model=swift#latest");
    expect(host?.querySelector(".workspace-shell__nav-link[aria-current='page']")?.getAttribute("href")).toBe("/projects/project-71/chat");
  });

  it("shows module context only after exact active project membership is confirmed", async () => {
    let confirmModules!: (response: { items: typeof moduleRecords }) => void;
    (harness.api as ApiClient).listProjectRuntimeModules = vi.fn(() => new Promise<{ items: ProjectRuntimeModule[] }>((resolve) => { confirmModules = resolve; }));
    await mount("/projects/project-71/modules/mod-files/files?path=docs#latest");
    await waitFor(() => host?.textContent?.includes("Проверяем доступность модуля") === true);
    expect(host?.querySelector("[data-testid='page-module']")).toBeNull();
    expect(host?.querySelector(".workspace-shell__context-module")).toBeNull();
    await act(async () => confirmModules({ items: moduleRecords }));
    await waitFor(() => host?.querySelector("[data-testid='page-module']") !== null);
    expect((harness.api as ApiClient).listProjectRuntimeModules).toHaveBeenCalledWith("project-71");
    expect(host?.querySelector(".workspace-shell__context-module")?.textContent).toContain("Project files");
    expect(host?.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-71/modules/mod-files/files?path=docs#latest");
  });

  it("rejects a module absent from the current project's active runtime module list", async () => {
    (harness.api as ApiClient).listProjectRuntimeModules = vi.fn().mockResolvedValue({ items: [{ id: "mod-files", module_id: "mod-files-older", name: "Not the requested module" }] });
    await mount("/projects/project-71/modules/mod-files/files");
    await waitFor(() => host?.textContent?.includes("Модуль не подключён к проекту") === true);
    expect(host?.querySelector("[data-testid='page-module']")).toBeNull();
    expect(host?.querySelector(".workspace-shell__context-module")).toBeNull();
  });

  it("says why a module is refused in the interface's own language", async () => {
    (harness.api as ApiClient).listProjectRuntimeModules = vi.fn().mockResolvedValue({ items: [] });
    window.localStorage.setItem("agentscale.locale", "en");
    try {
      await mount("/projects/project-71/modules/mod-files/files");
      await waitFor(() => host?.textContent?.includes("This module is not connected") === true);
      // The whole refusal, not just the heading: the sentence naming the module
      // and the way out were Russian literals in the route file.
      expect(host?.textContent).toContain("mod-files");
      expect(host?.textContent).toContain("Back to the project");
      expect(host?.textContent).toContain("Sign out");
      expect(host?.textContent).not.toMatch(/[А-Яа-я]/);
    } finally {
      window.localStorage.removeItem("agentscale.locale");
    }
  });

  it("recovers project loading failures with a retry", async () => {    (harness.api as ApiClient).getProject = vi.fn().mockRejectedValueOnce(new Error("project API unavailable")).mockResolvedValue(project);
    await mount("/projects/project-71");
    await waitFor(() => host?.querySelector("[role='alert']") !== null);
    expect(host?.textContent).toContain("project API unavailable");
    await click(".employee-route-state button");
    await waitFor(() => host?.querySelector("[data-testid='page-runtime']") !== null);
    expect((harness.api as ApiClient).getProject).toHaveBeenCalledTimes(2);
  });

  it("does not render project children before project and cabinet context resolve", async () => {
    let resolveProject!: (value: Project) => void;
    (harness.api as ApiClient).getProject = vi.fn(() => new Promise<Project>((resolve) => { resolveProject = resolve; }));
    await mount("/projects/project-71/chat");
    expect(host?.textContent).toContain("Загружаем проект");
    expect(host?.querySelector("[data-testid='page-chat']")).toBeNull();
    await act(async () => resolveProject(project));
    await waitFor(() => host?.querySelector("[data-testid='page-chat']") !== null);
    expect((harness.api as ApiClient).getCabinet).toHaveBeenCalledWith("cabinet-19");
  });

  it("keeps module content blocked on membership API errors and allows verification retry", async () => {
    (harness.api as ApiClient).listProjectRuntimeModules = vi.fn().mockRejectedValueOnce(new Error("module registry unavailable")).mockResolvedValue({ items: moduleRecords });
    await mount("/projects/project-71/modules/mod-files/files");
    await waitFor(() => host?.querySelector("[role='alert']") !== null);
    expect(host?.textContent).toContain("module registry unavailable");
    expect(host?.querySelector("[data-testid='page-module']")).toBeNull();
    await click(".employee-route-state button");
    await waitFor(() => host?.querySelector("[data-testid='page-module']") !== null);
    expect((harness.api as ApiClient).listProjectRuntimeModules).toHaveBeenCalledTimes(2);
  });
});

// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import {
  assertWorkspaceRouteContract,
  createWorkspaceRouteContract,
  getActiveWorkspaceRoute,
  getWorkspaceBreadcrumbs,
  getWorkspaceNavigation,
  resolveWorkspaceRoute,
  WORKSPACE_NAV_ITEMS,
  WORKSPACE_ROUTE_KEYS,
  WorkspaceShell,
  workspaceRouteMatches,
  type WorkspaceRouteContract,
} from "./workspace-shell";

function contractFor(projectId = "project-42") {
  return createWorkspaceRouteContract({ projectId });
}

const shellProps = {
  account: { displayName: "Ada Lovelace", email: "ada@example.test", role: "Platform admin" },
  cabinet: { id: "cabinet-1", name: "Northwind" },
  onSignOut: () => undefined,
  project: { id: "project-42", name: "Signal Lab", status: "running", statusLabel: "Running" },
  routeContract: contractFor(),
} as const;

function LocationControls() {
  const location = useLocation();
  const navigate = useNavigate();
  return <>
    <output data-testid="location">{location.pathname}{location.search}{location.hash}</output>
    <button data-testid="back" type="button" onClick={() => navigate(-1)}>Back</button>
    <button data-testid="forward" type="button" onClick={() => navigate(1)}>Forward</button>
    <button data-testid="open-chat" type="button" onClick={() => navigate("/projects/project-42/chat/session-7?model=fast#latest")}>Open chat deep link</button>
  </>;
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mountShell(initialEntries: string[], initialIndex = initialEntries.length - 1, props = shellProps) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(
    <MemoryRouter initialEntries={initialEntries} initialIndex={initialIndex}>
      <WorkspaceShell {...props}>
        <div data-testid="route-child">Scoped project content</div>
      </WorkspaceShell>
      <LocationControls />
    </MemoryRouter>,
  ));
  return host;
}

async function click(container: HTMLElement, selector: string) {
  await act(async () => {
    const target = container.querySelector<HTMLElement>(selector);
    if (!target) throw new Error(`Missing element ${selector}`);
    target.click();
  });
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  document.body.replaceChildren();
});

describe("workspace shell route contract", () => {
  it("declares every sidebar destination and derives concrete links from it", () => {
    const contract = contractFor();
    const navigation = getWorkspaceNavigation(contract);

    expect(WORKSPACE_ROUTE_KEYS).toHaveLength(13);
    expect(navigation.map((item) => item.key)).toEqual([
      "overview",
      "runtime",
      "chat",
      "files",
      "runs",
      "approvals",
      "resources",
      "content",
      "equipment",
      "automations",
      "models",
      "settings",
    ]);
    expect(navigation.slice(1).every((item) => item.href.startsWith("/projects/project-42/"))).toBe(true);
    expect(Object.entries(contract).every(([, route]) => route !== undefined && route.pattern.startsWith("/projects/:projectId"))).toBe(true);
  });

  it("keeps nested routes active under their owning navigation item", () => {
    const contract = contractFor();

    expect(getActiveWorkspaceRoute("/projects/project-42", contract)).toBe("overview");
    expect(getActiveWorkspaceRoute("/projects/project-42/chat", contract)).toBe("chat");
    expect(getActiveWorkspaceRoute("/projects/project-42/chat/session-7", contract)).toBe("chat");
    expect(getActiveWorkspaceRoute("/projects/project-42/files/artifact-9", contract)).toBe("files");
    expect(getActiveWorkspaceRoute("/projects/project-42/runtime/settings", contract)).toBe("runtime");
    expect(getActiveWorkspaceRoute("/projects/project-42/resources/module/mod-files", contract)).toBe("resources");
    expect(getActiveWorkspaceRoute("/projects/project-42/settings", contract)).toBe("settings");
    expect(getActiveWorkspaceRoute("/projects/another-project/chat", contract)).toBeUndefined();
    expect(getActiveWorkspaceRoute("/projects/project-42/not-confirmed", contract)).toBeUndefined();
    expect(resolveWorkspaceRoute("/projects/another-project/chat", contract)).toEqual({ scope: "other-project", requestedProjectId: "another-project" });
    expect(resolveWorkspaceRoute("/not-a-project", contract)).toEqual({ scope: "outside-project" });
    expect(resolveWorkspaceRoute("/projects/project-42/not-confirmed", contract)).toMatchObject({ scope: "current-project", requestedProjectId: "project-42" });
    expect(resolveWorkspaceRoute("/projects/project-99", contractFor("project-99"), "project-42")).toEqual({
      scope: "project-context-mismatch",
      requestedProjectId: "project-99",
      contextProjectId: "project-42",
    });
  });

  it("covers each confirmed project workflow and rejects unsupported run paths", () => {
    const contract = contractFor();
    const confirmedPaths: Array<[string, string]> = [
      ["/projects/project-42", "overview"],
      ["/projects/project-42/runtime", "runtime"],
      ["/projects/project-42/runtime/settings", "runtime"],
      ["/projects/project-42/chat", "chat"],
      ["/projects/project-42/chat/session-7", "chat"],
      ["/projects/project-42/files/docs/report.csv", "files"],
      ["/projects/project-42/runs?run=run-3", "runs"],
      ["/projects/project-42/approvals", "approvals"],
      ["/projects/project-42/resources?module=mod-files", "resources"],
      ["/projects/project-42/content", "content"],
      ["/projects/project-42/equipment", "equipment"],
      ["/projects/project-42/automations", "automations"],
      ["/projects/project-42/models?session=session-7", "models"],
      ["/projects/project-42/settings", "settings"],
    ];
    for (const [path, route] of confirmedPaths) expect(getActiveWorkspaceRoute(path, contract)).toBe(route);
    expect(getActiveWorkspaceRoute("/projects/project-42/runs/run-3", contract)).toBeUndefined();
    expect(getActiveWorkspaceRoute("/projects/project-42/chat/session-7/transcript", contract)).toBeUndefined();
  });

  it("accepts a host project pattern that already uses a nested wildcard", () => {
    const contract = createWorkspaceRouteContract({
      projectId: "project-42",
      projectPattern: "/projects/:projectId/*",
    });

    expect(contract.files.pattern).toBe("/projects/:projectId/files/*");
    expect(getActiveWorkspaceRoute("/projects/project-42/files/item-1", contract)).toBe("files");
  });

  it("uses the same matching pattern that the link contract declares", () => {
    const contract = contractFor();

    expect(workspaceRouteMatches(contract.runs, "/projects/project-42/runs")).toBe(true);
    expect(workspaceRouteMatches(contract.runs, "/projects/project-42/runs/run-1")).toBe(false);
    expect(workspaceRouteMatches(contract.approvals, "/projects/project-42/approvals")).toBe(true);
    expect(workspaceRouteMatches(contract.runs, "/projects/project-42/resources")).toBe(false);
    expect(getWorkspaceNavigation(contract, "runs").find((item) => item.key === "runs")).toMatchObject({
      href: "/projects/project-42/runs",
      active: true,
    });
  });

  it("keeps context breadcrumbs addressable without inventing a cabinet link", () => {
    const breadcrumbs = getWorkspaceBreadcrumbs({
      cabinetName: "Northwind",
      projectName: "Signal Lab",
      projectId: "signal-lab",
      activeRoute: "files",
      routeContract: contractFor("signal-lab"),
    });

    expect(breadcrumbs).toEqual([
      { label: "Northwind", current: false },
      { label: "Signal Lab · signal-lab", href: "/projects/signal-lab", current: false },
      { label: "Files", current: true },
    ]);
  });

  it("rejects a contract with a missing or non-local destination", () => {
    const invalid = {
      ...contractFor(),
      settings: undefined,
    } as unknown as WorkspaceRouteContract;

    expect(() => assertWorkspaceRouteContract(invalid)).toThrow("settings");
    expect(() => assertWorkspaceRouteContract({
      ...contractFor(),
      files: { href: "https://example.test/files", pattern: "/projects/:projectId/files/*" },
    })).toThrow("files");
    expect(() => assertWorkspaceRouteContract({
      ...contractFor(),
      resources: { href: "//example.test/resources", pattern: "/projects/:projectId/resources/*" },
    })).toThrow("resources");
    expect(() => assertWorkspaceRouteContract({
      ...contractFor(),
      chat: { href: "/projects/project-42/chat", pattern: "/projects/:projectId/files/*" },
    })).toThrow("chat");
    expect(() => assertWorkspaceRouteContract({
      ...contractFor(),
      chat: { href: "/projects/project-42/chat", pattern: "/projects/:projectId/*" },
    })).toThrow("overlapping active routes");
  });

  it("keeps route metadata and navigation item coverage aligned", () => {
    const navigationKeys = new Set(WORKSPACE_NAV_ITEMS.map((item) => item.key));
    expect(navigationKeys.size).toBe(13);
    expect([...navigationKeys]).toEqual(WORKSPACE_ROUTE_KEYS);
    expect(getWorkspaceNavigation(contractFor()).some((item) => item.key === "module")).toBe(false);
  });

  it("offers a module destination only for a confirmed module scope", () => {
    const contract = createWorkspaceRouteContract({ projectId: "project-42", moduleId: "mod-files", moduleLabel: "Files module" });
    expect(contract.module).toMatchObject({ href: "/projects/project-42/modules/mod-files", scopeId: "mod-files" });
    expect(getActiveWorkspaceRoute("/projects/project-42/modules/mod-files/files", contract)).toBe("module");
    expect(getActiveWorkspaceRoute("/projects/project-42/modules/mod-prompts/files", contract)).toBeUndefined();
    expect(resolveWorkspaceRoute("/projects/project-42/modules/mod-prompts/files", contract)).toMatchObject({ scope: "other-module", requestedModuleId: "mod-prompts" });
    expect(getWorkspaceNavigation(contract).find((item) => item.key === "module")).toMatchObject({
      href: "/projects/project-42/modules/mod-files",
      label: "Files module",
    });
    expect(getWorkspaceNavigation(contractFor()).some((item) => item.key === "module")).toBe(false);
  });

  it("renders the typed context, route links, and active state as one shell", () => {
    const markup = renderToString(
      <MemoryRouter initialEntries={["/projects/project-42/files/artifact-9"]}>
        <WorkspaceShell
          account={{ displayName: "Ada Lovelace", email: "ada@example.test", role: "Platform admin" }}
          cabinet={{ id: "cabinet-1", name: "Northwind" }}
          onSignOut={() => undefined}
          project={{ id: "project-42", name: "Signal Lab", status: "running", statusLabel: "Running" }}
          routeContract={contractFor()}
        >
          <div>Files view</div>
        </WorkspaceShell>
      </MemoryRouter>,
    );

    expect(markup).toContain("Northwind");
    expect(markup).toContain("Signal Lab");
    expect(markup).toContain("project-42");
    expect(markup).toContain("Files view");
    expect(markup).toContain('href="/projects/project-42/files"');
    expect(markup).toContain('aria-current="page"');
    expect(markup).toContain('data-active="true"');
    expect(markup).toContain('aria-label="Workspace navigation"');
    expect(markup).toContain('data-route-state="ready"');
  });

  it("updates active navigation from direct deep links and browser back/forward", async () => {
    const container = await mountShell([
      "/projects/project-42/files/reports/q1.csv?path=reports",
      "/projects/project-42/runtime/settings",
    ], 0);
    const activeLink = () => container.querySelector<HTMLAnchorElement>(".workspace-shell__nav-link[aria-current='page']");
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-42/files/reports/q1.csv?path=reports");
    expect(activeLink()?.getAttribute("href")).toBe("/projects/project-42/files");

    await click(container, "[data-testid='open-chat']");
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-42/chat/session-7?model=fast#latest");
    expect(activeLink()?.getAttribute("href")).toBe("/projects/project-42/chat");
    await click(container, "[data-testid='back']");
    expect(activeLink()?.getAttribute("href")).toBe("/projects/project-42/files");
    await click(container, "[data-testid='forward']");
    expect(activeLink()?.getAttribute("href")).toBe("/projects/project-42/chat");
  });

  it("does not render children from a mismatched project URL and offers scoped recovery", async () => {
    const container = await mountShell(["/projects/project-other/chat/session-9"]);
    expect(container.querySelector("[data-route-state='project-mismatch']")).toBeTruthy();
    expect(container.textContent).toContain("This shell is scoped to Signal Lab (project-42)");
    expect(container.textContent).toContain("project-other");
    expect(container.querySelector("[data-testid='route-child']")).toBeNull();
    expect(container.querySelector("a[href='/projects/project-42']")?.textContent).toBe("Open Signal Lab");
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-other/chat/session-9");
  });

  it("rejects selected project context that disagrees with the route", async () => {
    const container = await mountShell(["/projects/project-99"], 0, {
      ...shellProps,
      routeContract: contractFor("project-99"),
    });
    expect(container.querySelector("[data-route-state='project-context-mismatch']")).toBeTruthy();
    expect(container.textContent).toContain("route is scoped to project project-99");
    expect(container.textContent).toContain("selected project context is project-42");
    expect(container.querySelector("[data-testid='route-child']")).toBeNull();
  });

  it("keeps unknown project paths in the URL and shows a 404 recovery state", async () => {
    const container = await mountShell(["/projects/project-42/new-route?keep=1"]);
    expect(container.querySelector("[data-route-state='unsupported']")).toBeTruthy();
    expect(container.textContent).toContain("Unsupported workspace route");
    expect(container.querySelector("[data-testid='route-child']")).toBeNull();
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-42/new-route?keep=1");
  });

  it("keeps narrow navigation usable and closes it after choosing a route", async () => {
    const container = await mountShell(["/projects/project-42"]);
    const trigger = container.querySelector<HTMLButtonElement>(".workspace-shell__mobile-trigger");
    expect(trigger).toBeTruthy();
    await click(container, ".workspace-shell__mobile-trigger");
    expect(container.querySelector(".workspace-shell[data-mobile-open='true']")).toBeTruthy();
    await click(container, ".workspace-shell__nav-link[href='/projects/project-42/approvals']");
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-42/approvals");
    expect(container.querySelector(".workspace-shell[data-mobile-open='false']")).toBeTruthy();
    expect(container.querySelector(".workspace-shell__nav-link[aria-current='page']")?.getAttribute("href")).toBe("/projects/project-42/approvals");
  });

  it("shows the project and module identifiers when a module deep link is active", () => {
    const contract = createWorkspaceRouteContract({ projectId: "project-42", moduleId: "mod-files", moduleLabel: "Files module" });
    const markup = renderToString(
      <MemoryRouter initialEntries={["/projects/project-42/modules/mod-files/files"]}>
        <WorkspaceShell {...shellProps} routeContract={contract}><div>Module view</div></WorkspaceShell>
      </MemoryRouter>,
    );
    expect(markup).toContain("project-42");
    expect(markup).toContain("mod-files");
    expect(markup).toContain("Files module");
    expect(markup).toContain('data-active="true"');
  });

  it("does not render child content when a module URL scope differs", () => {
    const contract = createWorkspaceRouteContract({ projectId: "project-42", moduleId: "mod-files", moduleLabel: "Files module" });
    const markup = renderToString(
      <MemoryRouter initialEntries={["/projects/project-42/modules/mod-prompts/files"]}>
        <WorkspaceShell {...shellProps} routeContract={contract}><div>Wrong module content</div></WorkspaceShell>
      </MemoryRouter>,
    );
    expect(markup).toContain('data-route-state="module-mismatch"');
    expect(markup).toContain("requests module mod-prompts");
    expect(markup).not.toContain("Wrong module content");
  });
});

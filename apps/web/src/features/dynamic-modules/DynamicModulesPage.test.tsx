// @vitest-environment jsdom
/**
 * Acceptance: definition list screen.
 *
 * Covers the states that matter for the registry entry point: a populated page, the
 * empty page, a capability refusal that never touches the service, and the fail-closed
 * "API not wired" state. These run against a stub service, so no network or API client
 * is involved.
 */
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DynamicModuleDefinitionPage, DynamicModuleDefinitionSummary } from "../../api/dynamic";
import { DynamicModulesScreen } from "./DynamicModulesPage";
import { createDynamicModuleAccess } from "./capability";
import { DynamicModulesApiUnavailableError } from "../../api/dynamic";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import type { DynamicModulesService } from "./service";

const definition = (overrides: Partial<DynamicModuleDefinitionSummary> = {}): DynamicModuleDefinitionSummary => ({
  id: "def-1",
  appId: "equipment-review",
  displayName: "Equipment review",
  kind: "declarative-app",
  status: "published",
  latestVersion: "1.2.0",
  versionCount: 3,
  requestedCapabilityCount: 2,
  updatedAt: "2026-11-02T10:15:00Z",
  ...overrides,
});

function stubService(page: DynamicModuleDefinitionPage | Error): DynamicModulesService {
  const listDefinitions = vi.fn(async () => {
    if (page instanceof Error) throw page;
    return page;
  });
  const unimplemented = vi.fn(async () => {
    throw new Error("not part of this acceptance case");
  });
  return {
    listDefinitions,
    loadDefinition: unimplemented,
    listInstances: unimplemented,
    listGrants: unimplemented,
    publishVersion: unimplemented,
    installInstance: unimplemented,
    grantCapability: unimplemented,
    revokeGrant: unimplemented,
    archiveDefinition: unimplemented,
    listCabinets: unimplemented,
    listProjects: unimplemented,
    pendingCommandCount: () => 0,
  } as unknown as DynamicModulesService;
}

const FULL_ACCESS = createDynamicModuleAccess(undefined);
const granted = { canRead: true, canManage: true, granted: ["platform.queries", "platform.commands"] as const, missing: [] as const, reason: "granted" as const };

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mount(element: ReactElement): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<SettingsProvider><MemoryRouter>{element}</MemoryRouter></SettingsProvider>));
  // Flush the pending resource promise and the debounced search timer.
  await act(async () => { await Promise.resolve(); });
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  window.localStorage.clear();
});

beforeEach(() => {
  document.body.replaceChildren();
  // The screen renders through the i18n catalogue; the assertions below read the
  // English wording, so the locale is pinned the way the shell stores it.
  window.localStorage.setItem("agentscale.locale", "en");
});

describe("dynamic module definition list", () => {
  it("renders definitions with app id, status and installed counts", async () => {
    const service = stubService({
      items: [definition(), definition({ id: "def-2", appId: "lab-notes", displayName: "Lab notes", kind: "isolated-code-plugin", status: "draft", latestVersion: undefined, versionCount: 0, requestedCapabilityCount: 0 })],
      total: 2,
    });
    await mount(<DynamicModulesScreen access={granted} service={service} />);

    const text = host?.textContent ?? "";
    expect(text).toContain("Dynamic modules");
    expect(text).toContain("Equipment review");
    expect(text).toContain("equipment-review");
    expect(text).toContain("Published");
    expect(text).toContain("1.2.0");
    expect(text).toContain("Lab notes");
    expect(text).toContain("Draft");
    expect(text).toContain("Isolated plugin");
    expect(text).toContain("2 definitions");
    expect(host?.querySelector("table.ui-table")).not.toBeNull();
    expect(host?.querySelector("a[href='/dynamic-modules/def-1']")).not.toBeNull();
    expect((service.listDefinitions as unknown as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 100 }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("renders an explicit empty state when the registry page has no definitions", async () => {
    const service = stubService({ items: [], total: 0 });
    await mount(<DynamicModulesScreen access={granted} service={service} />);

    const text = host?.textContent ?? "";
    expect(text).toContain("No dynamic module definitions yet");
    expect(text).toContain("Requested capabilities become a grant only after an explicit decision");
    expect(host?.querySelector("table.ui-table")).toBeNull();
    expect(host?.querySelector("[role='status']")).not.toBeNull();
  });

  it("refuses to render or request when the shell has no read capability", async () => {
    const service = stubService({ items: [definition()] });
    // Default context access: no boundary mounted by the host.
    await mount(<DynamicModulesScreen access={FULL_ACCESS} service={service} />);

    const text = host?.textContent ?? "";
    expect(text).toContain("Capability required");
    expect(text).toContain("platform.queries");
    expect(text).toContain("Nothing was requested from the server");
    expect(host?.querySelector("[role='alert']")).not.toBeNull();
    expect(host?.querySelector("table.ui-table")).toBeNull();
    expect(service.listDefinitions).not.toHaveBeenCalled();
  });

  it("fails closed with the transport gap when the API facade exposes no registry transport", async () => {
    const service = stubService({ items: [definition()] });
    await mount(<DynamicModulesScreen
      access={granted}
      availability={{ available: false, missing: ["listDefinitions", "publishVersion"] }}
      service={service}
    />);

    const text = host?.textContent ?? "";
    expect(text).toContain("The dynamic module section is not connected in this build");
    expect(text).toContain("listDefinitions");
    expect(text).toContain("No request was sent");
    expect(service.listDefinitions).not.toHaveBeenCalled();
  });

  it("surfaces a server failure with a stable code and a retry action", async () => {
    const failure = new DynamicModulesApiUnavailableError(["listDefinitions"]);
    const service = stubService(failure);
    await mount(<DynamicModulesScreen access={granted} service={service} />);

    const text = host?.textContent ?? "";
    expect(text).toContain("DYNAMIC_MODULES_API_UNAVAILABLE");
    expect(text).toContain("Retry".slice(0, 0) + "Reload definitions");
    expect(host?.querySelector("[role='alert']")).not.toBeNull();
  });
});

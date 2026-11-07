// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import { CompaniesPage } from "./CompaniesPage";
import { CompanyDetailPage } from "./CompanyDetailPage";
import { DeletedPage } from "./DeletedPage";
import { KeysPage } from "./KeysPage";
import { ModulesPage } from "./ModulesPage";
import { createAdminService, type AdminService } from "./service";
import type { AdminCompany } from "./service";

/** Supplies the outlet context every admin screen reads its service from. */
function OutletFrame({ service, children }: { service: AdminService; children: React.ReactNode }) {
  return (
    <Routes>
      <Route element={<Outlet context={service} />} path="*">
        <Route element={<>{children}</>} path="*" />
      </Route>
    </Routes>
  );
}

const COMPANY: AdminCompany = {
  id: "co_1",
  name: "Acme",
  login: "acme@agentscale.local",
  description: null,
  contactEmail: null,
  phone: null,
  status: "active",
  createdAt: "2026-01-01T00:00:00Z",
  keyIds: ["aik_1"],
  moduleIds: ["mod_1"],
  cabinets: 2,
  employees: 4,
  tokensUsed: 0,
  requests: 0,
  quota: { cabinets: 10, packagesPerCabinet: 20, importMb: 50 },
};

/**
 * A service that answers with nothing.
 *
 * These screens fetch on mount; the test is about what they render for an
 * administrator, not about the transport, which `service.test.ts` covers.
 *
 * They also need the service through the route outlet, so the screens are
 * mounted the way the router mounts them rather than called directly.
 */
function emptyService(overrides: Partial<AdminService> = {}): AdminService {
  const base = createAdminService({
    fetcher: async () =>
      new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  });
  return { ...base, ...overrides };
}

function render(ui: React.ReactElement, service: AdminService = emptyService()): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <SettingsProvider>
        <OutletFrame service={service}>{ui}</OutletFrame>
      </SettingsProvider>
    </MemoryRouter>,
  );
}

/** A service with one company, so a screen renders its populated state. */
function serviceWithCompanies(): AdminService {
  return emptyService({
    listCompanies: async () => [COMPANY],
    listKeys: async () => [],
    listModules: async () => [],
  });
}

/**
 * Mounts a screen and waits for its first fetch to land.
 *
 * `renderToStaticMarkup` only produces the initial render, which for these
 * screens is the loading line — fine for asserting on copy that never changes,
 * useless for asserting on a table that arrives a tick later. The loading and
 * empty states are still checked with the static renderer.
 */
let mounted: { root: Root; host: HTMLDivElement } | undefined;

afterEach(async () => {
  if (mounted) await act(async () => mounted?.root.unmount());
  mounted?.host.remove();
  mounted = undefined;
});

async function mount(ui: React.ReactElement, service: AdminService): Promise<string> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  mounted = { root, host };
  await act(async () => {
    root.render(
      <MemoryRouter>
        <SettingsProvider>
          <OutletFrame service={service}>{ui}</OutletFrame>
        </SettingsProvider>
      </MemoryRouter>,
    );
  });
  // The collection resolves a promise after mount; one more turn lets it paint.
  await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
  return host.innerHTML;
}

describe("administrator screens", () => {
  it("shows the company list without a title block above it", async () => {
    const markup = await mount(<CompaniesPage />, serviceWithCompanies());
    // The tab bar says "Компании"; the screen below starts with the list.
    expect(markup).toContain("Acme");
    expect(markup).not.toContain("admin-section__header");
  });

  it("offers the empty state as an invitation rather than a dead end", async () => {
    // First run has no companies; the screen says what to do next instead of
    // showing an empty table with a search box above it.
    const markup = await mount(<CompaniesPage />, emptyService());
    expect(markup).toContain("Компаний пока нет");
    expect(markup).toContain("Новая компания");
    expect(markup).not.toContain("admin-toolbar");
  });

  it("puts the list's create button on the same line as the search", async () => {
    // "Find a company" and "add a company" are the two things a person does to
    // this list, so they share the toolbar instead of the action sitting in a
    // title block above it.
    const markup = await mount(<CompaniesPage />, serviceWithCompanies());
    const toolbarStart = markup.indexOf("admin-toolbar");
    expect(toolbarStart).toBeGreaterThan(-1);
    const toolbar = markup.slice(toolbarStart, markup.indexOf("</table>"));
    expect(toolbar).toContain("admin-search");
    expect(toolbar).toContain("admin-toolbar__action");
    expect(toolbar).toContain("Новая компания");
    // The action comes after the search box, on the same row.
    expect(toolbar.indexOf("admin-search")).toBeLessThan(toolbar.indexOf("admin-toolbar__action"));
  });
});

describe("admin service contract", () => {
  it("keeps a company's login separate from its identifier", () => {
    // The list shows a login a person can type; `id` is for URLs only.
    expect(COMPANY.login).not.toBe(COMPANY.id);
    expect(COMPANY.login).toContain("@");
  });

  it("exposes exactly the operations an administrator performs", async () => {
    const service = emptyService();
    // No `listProviders`: a key is an endpoint plus a secret, so there is no
    // provider catalog to choose from any more.
    expect(Object.keys(service).sort()).toEqual([
      "createCompany", "createKey", "createModule", "deleteCompany", "deleteKey",
      "deleteModule", "getCompany", "listCompanies", "listDeleted", "listKeys",
      "listModules", "purgeDeleted", "restoreDeleted", "setCompanyQuota",
      "setKeyCompanies", "setModuleCompanies", "setModuleScope",
    ]);
  });
});

describe("screens share one vocabulary", () => {
  it("lets the tab bar name the section instead of repeating it in a heading", async () => {
    // The heading said "Ключи" directly under a tab that said "Ключи". The tab
    // bar is the name; the screen below it is the work.
    for (const [screen, service] of [
      [<KeysPage />, serviceWithCompanies()],
      [<ModulesPage />, emptyService()],
      [<DeletedPage />, emptyService()],
    ] as const) {
      const markup = await mount(screen, service);
      expect(markup).not.toContain("<h2");
    }
  });

  it("shows every module's scope as a control, not as a verdict", async () => {
    // «Все компании» is an ordinary setting any module can have, so every row
    // carries the same two-option control.
    const markup = await mount(<ModulesPage />, emptyService({
      listModules: async () => [
        { id: "mod_management", name: "Управление", status: "active", companyIds: [], allCompanies: true, bindings: 0 },
        { id: "mod_equipment", name: "Подбор техники", status: "active", companyIds: ["co_1"], allCompanies: false, bindings: 2 },
      ],
      listCompanies: async () => [COMPANY],
    }));
    const selects = markup.match(/<select/g)?.length ?? 0;
    expect(selects).toBe(2);
    expect(markup).toContain("Все компании");
    expect(markup).toContain("Выбранные компании");
  });

  it("renders a company detail without leaking its identifier as text", () => {
    // The detail screen reads its service from the outlet and its company from
    // the route, so it is mounted the way the router mounts it.
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/admin/companies/co_1"]}>
        <SettingsProvider>
          <Routes>
            <Route element={<Outlet context={emptyService({
              getCompany: async () => COMPANY,
              listKeys: async () => [],
              listModules: async () => [],
            })} />} path="/admin">
              <Route element={<CompanyDetailPage />} path="companies/:companyId" />
            </Route>
          </Routes>
        </SettingsProvider>
      </MemoryRouter>,
    );
    // The identifier is in the URL and in tooltips; the screen shows the name.
    expect(markup).not.toContain("co_1");
  });
});

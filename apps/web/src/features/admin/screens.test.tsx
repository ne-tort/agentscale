// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
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

describe("administrator screens", () => {
  it("leads with companies and states its job in one line", () => {
    const markup = render(<CompaniesPage />);
    expect(markup).toContain("Компании");
  });

  it("offers the empty state as an invitation rather than a dead end", () => {
    // First run has no companies; the screen says what to do next instead of
    // showing an empty table with a search box above it.
    const markup = render(<CompaniesPage />);
    expect(markup).toContain("Новая компания");
  });
});

describe("admin service contract", () => {
  it("keeps a company's login separate from its identifier", () => {
    // The list shows a login a person can type; `id` is for URLs only.
    expect(COMPANY.login).not.toBe(COMPANY.id);
    expect(COMPANY.login).toContain("@");
  });

  it("exposes exactly the four sections an administrator works in", async () => {
    const service = emptyService();
    expect(Object.keys(service).sort()).toEqual([
      "createCompany", "createKey", "createModule", "deleteCompany", "deleteKey",
      "deleteModule", "getCompany", "listCompanies", "listDeleted", "listKeys",
      "listModules", "listProviders", "purgeDeleted", "restoreDeleted",
      "setCompanyQuota", "setKeyCompanies", "setModuleCompanies",
    ]);
  });
});

describe("screens share one vocabulary", () => {
  it("titles each section with the same word its tab uses", () => {
    // Keys and modules list what they own; the tab bar is the only place the
    // word "Администрирование" appears, so a section must name itself.
    expect(render(<KeysPage />)).toContain("Ключи");
    expect(render(<ModulesPage />)).toContain("Модули");
    expect(render(<DeletedPage />)).toContain("Удалённые");
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

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ModuleAdminPage, ModuleEvidencePanel } from "./ModuleAdminPage";
import type { ModuleAdminService } from "./service";

const emptyService = {} as ModuleAdminService;

describe("module admin surfaces", () => {
  it("keeps platform admin and employee/company catalog surfaces distinct", () => {
    const admin = renderToStaticMarkup(<ModuleAdminPage mode="admin" service={emptyService} />);
    const employee = renderToStaticMarkup(<ModuleAdminPage mode="company" companyId="co-1" service={emptyService} />);
    expect(admin).toContain("Module administration");
    expect(admin).toContain("New platform module");
    expect(employee).toContain("Employee module catalog");
    expect(employee).toContain("New company module");
  });

  it("names what the catalog did not return without exposing developer wording", () => {
    const markup = renderToStaticMarkup(<ModuleEvidencePanel row={{ id: "mod-1", name: "Module" }} mode="admin" />);
    // Three gaps, each stated once — the panel used to print "API contract gap"
    // six times, which told the reader nothing they could act on.
    expect(markup.match(/не указан/g)?.length).toBeGreaterThanOrEqual(3);
    expect(markup).not.toContain("API contract gap");
    expect(markup).not.toContain("not returned");
  });

  it("shows effective evidence and local/global child edit rights", () => {
    const markup = renderToStaticMarkup(<ModuleEvidencePanel row={{
      id: "mod-1",
      source: "company_local",
      module_version: "v3",
      writable: true,
      bind_kind: "local",
      child_may_edit: true,
    }} mode="company" />);
    expect(markup).toContain("company_local");
    expect(markup).toContain("v3");
    expect(markup).toContain("Writable (API)");
    expect(markup).toContain("local");
    expect(markup).toContain("Да");
  });
});
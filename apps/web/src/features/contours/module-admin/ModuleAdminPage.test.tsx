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

  it("renders explicit source/version/rights gaps instead of guessing", () => {
    const markup = renderToStaticMarkup(<ModuleEvidencePanel row={{ id: "mod-1", name: "Module" }} mode="admin" />);
    expect(markup.match(/API contract gap/g)).toHaveLength(6);
    expect(markup).toContain("Effective source");
    expect(markup).toContain("Version");
    expect(markup).toContain("Rights");
  });

  it("shows effective API evidence and local/global child edit rights", () => {
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
    expect(markup).toContain("Yes (API)");
  });
});
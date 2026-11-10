// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SettingsProvider } from "../../../preferences/SettingsProvider";
import { ModuleAdminPage, ModuleEvidencePanel } from "./ModuleAdminPage";
import type { ModuleAdminService } from "./service";

const emptyService = {} as ModuleAdminService;

describe("module admin surfaces", () => {
  it("keeps platform admin and employee/company catalog surfaces distinct", () => {
    const admin = renderToStaticMarkup(<SettingsProvider><ModuleAdminPage mode="admin" service={emptyService} /></SettingsProvider>);
    const employee = renderToStaticMarkup(<SettingsProvider><ModuleAdminPage mode="company" companyId="co-1" service={emptyService} /></SettingsProvider>);
    expect(admin).toContain("Управление модулями");
    expect(admin).toContain("Новый модуль платформы");
    expect(employee).toContain("Модули компании");
    expect(employee).toContain("Новый модуль компании");
  });

  it("names what the catalog did not return without exposing developer wording", () => {
    const markup = renderToStaticMarkup(<SettingsProvider><ModuleEvidencePanel row={{ id: "mod-1", name: "Module" }} mode="admin" /></SettingsProvider>);
    // Three gaps, each stated once — the panel used to print "API contract gap"
    // six times, which told the reader nothing they could act on.
    expect(markup.match(/не указан/g)?.length).toBeGreaterThanOrEqual(3);
    expect(markup).not.toContain("API contract gap");
    expect(markup).not.toContain("not returned");
  });

  it("shows effective evidence and local/global child edit rights", () => {
    const markup = renderToStaticMarkup(<SettingsProvider><ModuleEvidencePanel row={{
      id: "mod-1",
      source: "company_local",
      module_version: "v3",
      writable: true,
      bind_kind: "local",
      child_may_edit: true,
    }} mode="company" /></SettingsProvider>);
    expect(markup).toContain("Модуль компании");
    expect(markup).toContain("v3");
    expect(markup).toContain("Можно менять");
    expect(markup).toContain("Локальная");
    expect(markup).toContain("Да");
  });

  it("shows the plugin-module entry point only with a platform client and exact scope", () => {
    const pluginApi = {
      createPluginSource: async () => ({}) as never,
      bindPluginSourceScope: async () => ({}) as never,
    };
    const withScope = renderToStaticMarkup(<SettingsProvider><ModuleAdminPage
      mode="admin" service={emptyService} companyId="co-1"
      pluginApi={pluginApi} pluginScope={{ cabinetId: "cab-1", projectId: "pr-1" }}
    /></SettingsProvider>);
    expect(withScope).toContain("Новый plugin-модуль");

    // Без платформенного клиента секции быть не должно: форма, которая
    // гарантированно упадёт, хуже её отсутствия.
    const withoutApi = renderToStaticMarkup(<SettingsProvider><ModuleAdminPage
      mode="admin" service={emptyService} companyId="co-1"
    /></SettingsProvider>);
    expect(withoutApi).not.toContain("Новый plugin-модуль");

    // Без точного scope установка невозможна — секции тоже нет.
    const withoutScope = renderToStaticMarkup(<SettingsProvider><ModuleAdminPage
      mode="admin" service={emptyService} companyId="co-1" pluginApi={pluginApi}
    /></SettingsProvider>);
    expect(withoutScope).not.toContain("Новый plugin-модуль");
  });
});

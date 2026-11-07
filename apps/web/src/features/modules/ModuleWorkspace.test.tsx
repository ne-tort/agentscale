// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import { ModuleWorkspace } from "./ModuleWorkspace";
import type { ModuleRuntimeService } from "./contracts";
import { equipmentModuleFixture, filesModuleFixture } from "./fixtures";

/** Copy comes from the settings provider (Russian by default), so every render is wrapped. */
function render(element: Parameters<typeof renderToStaticMarkup>[0]): string {
  return renderToStaticMarkup(<SettingsProvider>{element}</SettingsProvider>);
}

function makeService(): ModuleRuntimeService {
  return {
    listProjectModules: vi.fn(async (_projectId: string, _options?: { signal?: AbortSignal }) => [
      { id: "mod_files", name: "Файлы", bind_kind: "local" as const, child_may_edit: true },
      { id: "mod_equipment", name: "Подбор техники", bind_kind: "global" as const, child_may_edit: false },
    ]),
    loadModule: vi.fn(async (_projectId, moduleId) => {
      const fixture = moduleId === "mod_equipment" ? equipmentModuleFixture : filesModuleFixture;
      const docs = fixture.documents;
      return {
        scope: { projectId: "project-host", cabinetId: "cabinet-host", moduleId, moduleName: fixture.module.name, bindKind: moduleId === "mod_files" ? "local" as const : "global" as const, childMayEdit: moduleId === "mod_files", readOnly: moduleId !== "mod_files" },
        manifest: {
          syntax_version: 1,
          tables: (docs.tables as { body: never[] }).body,
          columns: (docs.columns as { body: never[] }).body,
          views: (docs.views as { body: never[] }).body,
          tabs: (docs.tabs as { body: never[] }).body,
          actions: (docs.actions as { body: never[] }).body,
        } as never,
      };
    }),
    listRows: vi.fn(async () => ({ items: [], total: 0, page: 1, page_size: 20 })),
    createRow: vi.fn(async () => ({ row_id: "new", body: {} })),
    patchRow: vi.fn(async () => ({ row_id: "row", body: {} })),
    deleteRow: vi.fn(async () => undefined),
    invokeAction: vi.fn(async () => ({ kind: "workspace.noop" })),
    uploadContent: vi.fn(async () => ({ asset_id: "asset" })),
    uploadSecret: vi.fn(async () => ({ secret_ref: "file://cabinet_secrets/test" })),
    downloadFile: vi.fn(async () => ({ blob: new Blob(), filename: "export" })),
    listBoundProjects: vi.fn(async () => []),
    setProfile: vi.fn(async () => ({})),
  };
}

describe("ModuleWorkspace navigation", () => {
  it("has an explicit loading boundary during server rendering", () => {
    const markup = render(<ModuleWorkspace projectId="project-host" service={makeService()} />);
    expect(markup).toContain("Загружаем модули проекта");
  });
});

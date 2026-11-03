import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ModuleRuntime } from "./ModuleRuntime";
import type { ModuleManifest, ModuleRuntimeService } from "./contracts";
import { filesModuleFixture } from "./fixtures";

function buildRuntime() {
  const fixture = filesModuleFixture;
  const docs = fixture.documents;
  const manifest: ModuleManifest = {
    syntax_version: 1,
    tables: (docs.tables as { body: ModuleManifest["tables"] }).body,
    columns: (docs.columns as { body: ModuleManifest["columns"] }).body,
    views: (docs.views as { body: ModuleManifest["views"] }).body,
    tabs: (docs.tabs as { body: ModuleManifest["tabs"] }).body,
    actions: (docs.actions as { body: ModuleManifest["actions"] }).body,
  };
  const service: ModuleRuntimeService = {
    listProjectModules: vi.fn(async () => []),
    loadModule: vi.fn(async () => ({ scope: { projectId: fixture.project.id, cabinetId: fixture.project.cabinet_id, moduleId: fixture.module.id, moduleName: fixture.module.name, bindKind: "local", childMayEdit: true, readOnly: false }, manifest })),
    listRows: vi.fn(async (_scope, tableSlug) => tableSlug === "files" ? fixture.page as never : { items: [], total: 0, page: 1, page_size: 20 }),
    createRow: vi.fn(async () => ({ row_id: "new", body: {} })),
    patchRow: vi.fn(async () => ({ row_id: "row", body: {} })),
    deleteRow: vi.fn(async () => undefined),
    invokeAction: vi.fn(async () => ({ kind: "files.export" })),
    uploadContent: vi.fn(async () => ({ asset_id: "asset" })),
    uploadSecret: vi.fn(async () => ({ secret_ref: "file://cabinet_secrets/test" })),
    downloadFile: vi.fn(async () => ({ blob: new Blob(), filename: "file" })),
    listBoundProjects: vi.fn(async () => [{ project_id: "project-beta", name: "Beta" }]),
    setProfile: vi.fn(async () => ({})),
  };
  const initialRuntime = { scope: { projectId: fixture.project.id, cabinetId: fixture.project.cabinet_id, moduleId: fixture.module.id, moduleName: fixture.module.name, bindKind: "local" as const, childMayEdit: true, readOnly: false }, manifest };
  return { fixture, manifest, service, initialRuntime };
}

describe("ModuleRuntime collection acceptance", () => {
  it("exposes the primary create command in the initial collection render", () => {
    const { fixture, service, initialRuntime } = buildRuntime();
    const markup = renderToStaticMarkup(<ModuleRuntime projectId={fixture.project.id} moduleId={fixture.module.id} service={service} initialRuntime={initialRuntime} initialViewSlug="files_primary" />);
    expect(markup).toContain("Новый файл");
  });

  it("shows undeclared and malformed row actions as errors instead of dropping them", () => {
    const { fixture, service, initialRuntime } = buildRuntime();
    const mutated = { ...initialRuntime, manifest: { ...initialRuntime.manifest, views: initialRuntime.manifest.views.map((view) => view.slug === "files_primary" ? { ...view, ui_json: { ...view.ui_json, row_actions: [{ action: "missing_action" }, { kind: "unknown" }] } } : view) } };
    const markup = renderToStaticMarkup(<ModuleRuntime projectId={fixture.project.id} moduleId={fixture.module.id} service={service} initialRuntime={mutated} initialViewSlug="files_primary" />);
    expect(markup).toContain("missing_action");
    expect(markup).toContain("row action must invoke a declared action");
  });
});

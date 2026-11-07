import { describe, expect, it, vi } from "vitest";
import { managementModuleFixture, type ModuleSeedFixture } from "../modules/fixtures";
import { validateManifestDocuments } from "../modules/manifest";
import { createResourceModuleSlots, resourceModuleService, withResourceModuleViews } from "./ProjectModuleResources";
import type { ModuleRuntimeScope, ModuleRuntimeService } from "../modules/contracts";

function manifestFrom(fixture: ModuleSeedFixture = managementModuleFixture) {
  const documents = fixture.documents;
  const result = validateManifestDocuments(documents);
  if (!result.manifest) throw new Error(result.error ?? "Fixture module manifest was invalid.");
  return result.manifest;
}

/** The backend seed declares `prompt_files_editor`; the validator rejects it until the doorway swaps it for a slot. */
function promptManifestWithNestedEditor(fixture: ModuleSeedFixture = managementModuleFixture) {
  const manifest = manifestFrom(fixture);
  const views = manifest.views.map((view) => view.slug !== "prompt_path_settings" ? view : {
    ...view,
    ui_json: {
      ...view.ui_json,
      fields: [...(view.ui_json.fields as unknown[]), { column: "files_json", widget: "prompt_files_editor" }],
    },
  });
  const documents = {
    tables: { body: manifest.tables },
    columns: { body: manifest.columns },
    views: { body: views },
    tabs: { body: manifest.tabs },
    actions: { body: manifest.actions },
  };
  const result = validateManifestDocuments(documents);
  if (!result.manifest) throw new Error(result.error ?? "Nested-editor manifest fixture was invalid.");
  return result.manifest;
}

const scope: ModuleRuntimeScope = { projectId: "project-beta", cabinetId: "cabinet-beta", moduleId: "mod_management", moduleName: "Управление", bindKind: "local", childMayEdit: true, readOnly: false };

describe("project resource module manifest adapters", () => {
  it("adapts only the declared prompt_files_editor and preserves backend prompt row metadata", () => {
    const manifest = promptManifestWithNestedEditor();
    expect(manifest.invalid_views?.prompt_path_settings).toContain("unsupported widget 'prompt_files_editor'");
    const adapted = withResourceModuleViews({ scope, manifest });
    const view = adapted.manifest.views.find((candidate) => candidate.slug === "prompt_path_settings");
    expect(adapted.manifest.invalid_views?.prompt_path_settings).toBeUndefined();
    expect(view?.ui_json.slots).toContainEqual({ name: "resource_prompt_files", placement: "after_content" });
    expect(view?.ui_json.fields).not.toContainEqual({ column: "files_json", widget: "prompt_files_editor" });
    expect(adapted.manifest.columns.find((column) => column.table_slug === "prompt_paths" && column.name === "files_json")).toBeUndefined();
    expect(adapted.scope.projectId).toBe("project-beta");
  });

  it("leaves every unrelated prompt view validation failure intact", () => {
    const manifest = promptManifestWithNestedEditor();
    const invalid = { ...(manifest.invalid_views ?? {}), prompt_path_settings: "view prompt_path_settings uses unsupported property 'unsafe_html'" };
    const adapted = withResourceModuleViews({ scope, manifest: { ...manifest, invalid_views: invalid } });
    expect(adapted.manifest.invalid_views?.prompt_path_settings).toBe(invalid.prompt_path_settings);
    expect(adapted.manifest.views.find((view) => view.slug === "prompt_path_settings")?.ui_json.slots).toBeUndefined();
  });

  it("does not invent fields absent from the backend manifest", () => {
    // No `prompt_files_editor` widget is declared, so the manifest must come back as-is.
    const plain = { scope, manifest: manifestFrom() };
    expect(withResourceModuleViews(plain)).toBe(plain);
  });

  it("keys the nested editor on the prompt_paths detail view, not on the module id", () => {
    const service = {} as ModuleRuntimeService;
    const slots = createResourceModuleSlots(service);
    const manifest = promptManifestWithNestedEditor();
    const detail = manifest.views.find((view) => view.slug === "prompt_path_settings")!;
    const row = managementModuleFixture.profilePage as { items: Array<{ row_id: string; body: Record<string, unknown> }> };

    // The merged module renders it...
    expect(slots.resolve("mod_management", "prompt_path_settings", "resource_prompt_files")).toBeDefined();
    // ...the same renderer refuses an unrelated table that reuses the slot name...
    const foreignTable = { ...detail, table_slug: "mcp_packages" };
    expect(slots.resolve("mod_management", "prompt_path_settings", "resource_prompt_files")!({ scope, manifest, view: foreignTable, row: row.items[0], onRefresh: () => undefined })).toBeNull();
    // ...and it stays silent for every other view or slot.
    expect(slots.resolve("mod_management", "prompts_hub", "resource_prompt_files")).toBeUndefined();
    expect(slots.resolve("mod_management", "prompt_path_settings", "other_slot")).toBeUndefined();
    expect(slots.resolve("mod_equipment", "prompt_path_settings", "resource_prompt_files")).toBeDefined();
  });
});

describe("project resource module table allowlist", () => {
  it("pages every merged Management table client-side and defers unknown tables to the service", async () => {
    const listRows = vi.fn(async (_scope: ModuleRuntimeScope, tableSlug: string) => ({
      items: [{ row_id: `${tableSlug}-1`, session_id: null, body: { name: tableSlug, project_ids: [] } }],
      total: 1,
      page: 1,
      page_size: 200,
    }));
    const decorated = resourceModuleService({ listRows } as unknown as ModuleRuntimeService, ((key: string) => key) as never);

    // Every table the merged module owns keeps the doorway's read path; none was dropped.
    for (const table of ["prompt_profiles", "profile_settings", "prompt_paths", "mcp_packages", "files"]) {
      const page = await decorated.listRows(scope, table, { page: 1, pageSize: 50 });
      expect(page.items).toHaveLength(1);
      // The doorway re-reads with its own page size so project filtering sees every row.
      expect(listRows).toHaveBeenCalledWith(scope, table, expect.objectContaining({ page: 1, pageSize: 200 }), undefined);
      listRows.mockClear();
    }

    // A table outside the allowlist is passed straight through, query untouched.
    await decorated.listRows({ ...scope, moduleId: "mod_equipment" }, "catalogs", { page: 3, pageSize: 10 });
    expect(listRows).toHaveBeenCalledOnce();
    expect(listRows).toHaveBeenCalledWith(expect.objectContaining({ moduleId: "mod_equipment" }), "catalogs", { page: 3, pageSize: 10 }, undefined);
  });
});

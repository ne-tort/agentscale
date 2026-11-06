import { describe, expect, it } from "vitest";
import { filesModuleFixture, promptModuleFixture, type ModuleSeedFixture } from "../modules/fixtures";
import { validateManifestDocuments } from "../modules/manifest";
import { withResourceModuleViews } from "./ProjectModuleResources";

function manifestFrom(fixture: ModuleSeedFixture) {
  const documents = fixture.documents;
  const result = validateManifestDocuments(documents);
  if (!result.manifest) throw new Error(result.error ?? "Fixture module manifest was invalid.");
  return result.manifest;
}

function promptManifestWithNestedEditor(fixture: ModuleSeedFixture = promptModuleFixture) {
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

describe("project resource module manifest adapters", () => {
  it("adapts only the declared prompt_files_editor and preserves backend prompt row metadata", () => {
    const manifest = promptManifestWithNestedEditor();
    expect(manifest.invalid_views?.prompt_path_settings).toContain("unsupported widget 'prompt_files_editor'");
    const adapted = withResourceModuleViews({ scope: { projectId: "project-1", cabinetId: "cabinet-1", moduleId: "mod_prompts", moduleName: "Prompts", bindKind: "global", childMayEdit: true, readOnly: false }, manifest }, "mod_prompts");
    const view = adapted.manifest.views.find((candidate) => candidate.slug === "prompt_path_settings");
    expect(adapted.manifest.invalid_views?.prompt_path_settings).toBeUndefined();
    expect(view?.ui_json.slots).toContainEqual({ name: "resource_prompt_files", placement: "after_content" });
    expect(view?.ui_json.fields).not.toContainEqual({ column: "files_json", widget: "prompt_files_editor" });
    expect(adapted.manifest.columns.find((column) => column.table_slug === "prompt_paths" && column.name === "files_json")).toBeUndefined();
    expect(adapted.scope.projectId).toBe("project-1");
  });

  it("leaves every unrelated prompt view validation failure intact", () => {
    const manifest = promptManifestWithNestedEditor();
    const invalid = { ...(manifest.invalid_views ?? {}), prompt_path_settings: "view prompt_path_settings uses unsupported property 'unsafe_html'" };
    const adapted = withResourceModuleViews({ scope: { projectId: "project-1", cabinetId: "cabinet-1", moduleId: "mod_prompts", moduleName: "Prompts", bindKind: "global", childMayEdit: true, readOnly: false }, manifest: { ...manifest, invalid_views: invalid } }, "mod_prompts");
    expect(adapted.manifest.invalid_views?.prompt_path_settings).toBe(invalid.prompt_path_settings);
    expect(adapted.manifest.views.find((view) => view.slug === "prompt_path_settings")?.ui_json.slots).toBeUndefined();
  });

  it("does not adapt unrelated Modules or invent fields absent from the backend manifest", () => {
    const filesManifest = manifestFrom(filesModuleFixture);
    const files = { scope: { projectId: "project-1", cabinetId: "cabinet-1", moduleId: "mod_files", moduleName: "Files", bindKind: "local" as const, childMayEdit: true, readOnly: false }, manifest: filesManifest };
    expect(withResourceModuleViews(files, "mod_files")).toBe(files);
    const promptManifest = manifestFrom(promptModuleFixture);
    const prompts = { scope: { projectId: "project-1", cabinetId: "cabinet-1", moduleId: "mod_prompts", moduleName: "Prompts", bindKind: "global" as const, childMayEdit: true, readOnly: false }, manifest: promptManifest };
    expect(withResourceModuleViews(prompts, "mod_prompts")).toBe(prompts);
  });
});

import { describe, expect, it, vi } from "vitest";
import type { ModuleManifest, ModuleRuntimeScope, ModuleRuntimeService, ModuleViewDefinition } from "./contracts";
import { aggregateListCount, aggregateSourceTables, listAllModuleRows } from "./hooks";

const scope: ModuleRuntimeScope = { projectId: "project-a", cabinetId: "cabinet-a", moduleId: "module-a", moduleName: "Module", bindKind: "local", childMayEdit: true, readOnly: false };
const manifest: ModuleManifest = {
  syntax_version: 1,
  tables: [{ slug: "profiles" }, { slug: "prompt_paths" }],
  columns: [
    { table_slug: "profiles", name: "name", type: "text" },
    { table_slug: "prompt_paths", name: "profile_id", type: "text" },
    { table_slug: "prompt_paths", name: "files_json", type: "json" },
  ],
  views: [], tabs: [], actions: [],
};
const view: ModuleViewDefinition = { slug: "profiles", table_slug: "profiles", kind: "collection", ui_json: { kind: "collection", columns: [{ field: "prompts_count", source: "aggregate.prompt_paths.files_json", foreign_key: "profile_id", join_field: "row_id" }] } };

describe("module aggregate rows", () => {
  it("discovers declared aggregate source tables", () => {
    expect(aggregateSourceTables(view)).toEqual(["prompt_paths"]);
  });

  it("loads every source page and requests all chat rows when specified", async () => {
    const listRows = vi.fn(async (_scope: ModuleRuntimeScope, _table: string, query?: import("./contracts").ModuleListQuery) => query?.page === 1
      ? { items: [{ row_id: "p1", body: { profile_id: "profile-1", files_json: [{ name: "a" }] } }], total: 2, page: 1, page_size: 1 }
      : { items: [{ row_id: "p2", body: { profile_id: "profile-1", files_json: [{ name: "b" }, { name: "c" }] } }], total: 2, page: 2, page_size: 1 });
    const service = { listRows } as unknown as ModuleRuntimeService;
    const rows = await listAllModuleRows(service, scope, "prompt_paths", new AbortController().signal, true);
    expect(rows).toHaveLength(2);
    expect(listRows).toHaveBeenCalledTimes(2);
    expect(listRows.mock.calls[0][2]).toMatchObject({ page: 1, pageSize: 200, chats: "all" });
  });

  it("sums nested prompt files by the parent row id instead of displaying the missing child field", () => {
    const rowsByTable = { prompt_paths: [
      { row_id: "p1", body: { profile_id: "profile-1", files_json: [{ name: "a" }, { name: "b" }] } },
      { row_id: "p2", body: { profile_id: "profile-1", files_json: [{ name: "c" }] } },
      { row_id: "p3", body: { profile_id: "profile-2", files_json: [{ name: "d" }] } },
    ] } as const;
    const profile = { row_id: "profile-1", body: { name: "Default" } };
    const column = { field: "prompts_count", source: "aggregate.prompt_paths.files_json", foreign_key: "profile_id", join_field: "row_id" };
    const countView = { ...view, ui_json: { kind: "collection", columns: [column] } };
    expect(aggregateListCount(manifest, rowsByTable, countView, profile, column)).toBe(3);
  });
});

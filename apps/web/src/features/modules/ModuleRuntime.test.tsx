// @vitest-environment jsdom
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import { ModuleRuntime } from "./ModuleRuntime";
import type { ModuleManifest, ModuleRuntimeLoadResult, ModuleRuntimeProps, ModuleRuntimeService } from "./contracts";
import { equipmentModuleFixture, filesModuleFixture, type ModuleSeedFixture } from "./fixtures";
import { createModuleSlotRegistry } from "./slots";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

/** Copy comes from the settings provider (Russian by default), so every render is wrapped. */
function withSettings(element: ReactElement): ReactElement {
  return <SettingsProvider>{element}</SettingsProvider>;
}

/**
 * Fixture documents keep their bodies as `unknown` JSON on purpose: they mirror the
 * backend envelope. Reading them back through the manifest contract belongs here, once,
 * instead of a `as never` cast at every call site.
 */
function manifestFor(fixture: ModuleSeedFixture): ModuleManifest {
  return {
    syntax_version: 1,
    tables: (fixture.documents.tables as { body: ModuleManifest["tables"] }).body,
    columns: (fixture.documents.columns as { body: ModuleManifest["columns"] }).body,
    views: (fixture.documents.views as { body: ModuleManifest["views"] }).body,
    tabs: (fixture.documents.tabs as { body: ModuleManifest["tabs"] }).body,
    actions: (fixture.documents.actions as { body: ModuleManifest["actions"] }).body,
  };
}

function serviceFor(fixture = filesModuleFixture): ModuleRuntimeService {
  const rows = new Map<string, unknown>([
    ["files", fixture.page],
    ["prompt_profiles", { items: [{ row_id: "profile_default", body: { name: "Default", is_default: true } }], total: 1, page: 1, page_size: 20 }],
    ["profile_settings", { items: [{ row_id: "settings_default", body: { profile_id: "profile_default", active: true } }], total: 1, page: 1, page_size: 20 }],
    ["prompt_paths", fixture.profilePage],
  ]);
  return {
    listProjectModules: vi.fn(async () => [{ id: fixture.module.id, name: fixture.module.name, bind_kind: fixture.module.id === "mod_files" ? "local" as const : "global" as const, child_may_edit: fixture.module.id !== "mod_equipment" }]),
    loadModule: vi.fn(async () => ({
      scope: {
        projectId: fixture.project.id,
        cabinetId: fixture.project.cabinet_id,
        moduleId: fixture.module.id,
        moduleName: fixture.module.name,
        bindKind: fixture.module.id === "mod_files" ? "local" as const : "global" as const,
        childMayEdit: fixture.module.id !== "mod_equipment",
        readOnly: fixture.module.id === "mod_equipment",
        sessionId: fixture.sessionId,
      },
      manifest: manifestFor(fixture),
    })),
    listRows: vi.fn(async (_scope, tableSlug) => rows.get(tableSlug) as never ?? { items: [], total: 0, page: 1, page_size: 20 }),
    createRow: vi.fn(async (_scope, _table, body) => ({ row_id: "new", body })),
    patchRow: vi.fn(async (_scope, _table, rowId, body) => ({ row_id: rowId, body })),
    deleteRow: vi.fn(async () => undefined),
    invokeAction: vi.fn(async (_scope, actionId) => ({ kind: actionId === "budget_export" ? "equipment.budget_export" : "content.index_opensearch" })),
    uploadContent: vi.fn(async () => ({ asset_id: "asset-upload", filename: "upload.bin" })),
    uploadSecret: vi.fn(async () => ({ secret_ref: "file://cabinet_secrets/test" })),
    downloadFile: vi.fn(async () => ({ blob: new Blob(), filename: "export.xlsx" })),
    listBoundProjects: vi.fn(async () => [{ project_id: fixture.project.id, name: "Primary" }]),
    setProfile: vi.fn(async () => ({ profile_id: "profile_default" })),
  };
}

function initialRuntimeFor(fixture = filesModuleFixture, props: Partial<ModuleRuntimeProps> = {}): ModuleRuntimeLoadResult {
  const sessionId = "sessionId" in props ? props.sessionId : fixture.sessionId;
  return {
    scope: {
      projectId: fixture.project.id,
      cabinetId: fixture.project.cabinet_id,
      moduleId: fixture.module.id,
      moduleName: fixture.module.name,
      bindKind: fixture.module.id === "mod_files" ? "local" as const : "global" as const,
      childMayEdit: fixture.module.id !== "mod_equipment",
      readOnly: fixture.module.id === "mod_equipment",
      ...(sessionId !== undefined ? { sessionId } : {}),
    },
    manifest: manifestFor(fixture),
  };
}

function renderStaticRuntime(fixture = filesModuleFixture, props: Partial<ModuleRuntimeProps> = {}) {
  const service = props.service ?? serviceFor(fixture);
  const initialRuntime = "initialRuntime" in props ? props.initialRuntime : initialRuntimeFor(fixture, props);
  return renderToStaticMarkup(withSettings(<ModuleRuntime projectId={fixture.project.id} moduleId={fixture.module.id} sessionId={fixture.sessionId} service={service} initialRuntime={initialRuntime} {...props} />));
}

const mountedRoots: Root[] = [];

async function mountRuntime(fixture = filesModuleFixture, props: Partial<ModuleRuntimeProps> = {}) {
  const service = props.service ?? serviceFor(fixture);
  const initialRuntime = "initialRuntime" in props ? props.initialRuntime : initialRuntimeFor(fixture, props);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mountedRoots.push(root);
  await act(async () => {
    root.render(withSettings(<ModuleRuntime projectId={fixture.project.id} moduleId={fixture.module.id} sessionId={"sessionId" in props ? props.sessionId : fixture.sessionId} service={service} initialRuntime={initialRuntime} {...props} />));
  });
  return { container, service };
}

afterEach(async () => {
  await act(async () => {
    for (const root of mountedRoots.splice(0)) root.unmount();
  });
  document.body.replaceChildren();
});

describe("ModuleRuntime", () => {
  it("loads module and collection rows in the browser instead of remaining on the SSR loading state", async () => {
    const service = serviceFor();
    const serverMarkup = renderStaticRuntime(filesModuleFixture, { service });
    expect(serverMarkup).toContain("Загружаем данные");
    expect(serverMarkup).not.toContain("guide.pdf");
    expect(service.listRows).not.toHaveBeenCalled();

    const { container } = await mountRuntime(filesModuleFixture, { service, initialRuntime: undefined });
    expect(service.loadModule).toHaveBeenCalledOnce();
    expect(service.listRows).toHaveBeenCalled();
    expect(container.textContent).toContain("guide.pdf");
    expect(container.textContent).toContain("Путь");
    expect(container.textContent).not.toContain("Загружаем данные");
  });

  it("keeps loading until asynchronous module initialization fails, then shows the manifest error", async () => {
    const service = serviceFor();
    let rejectLoad!: (error: Error) => void;
    vi.mocked(service.loadModule).mockImplementationOnce(() => new Promise<Awaited<ReturnType<ModuleRuntimeService["loadModule"]>>>((_resolve, reject) => { rejectLoad = reject; }));
    const { container } = await mountRuntime(filesModuleFixture, { service, initialRuntime: undefined });
    expect(container.textContent).toContain("Загружаем модуль");
    await act(async () => { rejectLoad(new Error("view privileged_html has unsupported kind")); });
    expect(container.textContent).toContain("view privileged_html has unsupported kind");
    expect(container.textContent).not.toContain("Загружаем модуль");
  });

  it("renders a typed collection with current local editability and file refs", async () => {
    const { container } = await mountRuntime();
    expect(container.textContent).toContain("Данные проекта");
    expect(container.textContent).toContain("guide.pdf");
    expect(container.textContent).toContain("Путь");
    expect(container.textContent).toContain("Удалить");
    expect(container.textContent).toContain("Поиск по данным");
    expect(container.textContent).toContain("Дополнительно");
  });

  it("shows the read-only global-binding notice on the route hub without offering CRUD", async () => {
    const { container } = await mountRuntime(equipmentModuleFixture, { initialViewSlug: "equipment_hub" });
    expect(container.textContent).toContain("Общие данные доступны только для чтения");
    expect(container.textContent).toContain("Общие данные доступны только для чтения");
    expect(container.textContent).not.toContain("Удалить");
    expect(container.textContent).not.toContain("Создать");
  });

  it("renders active-chat-required equipment entries only with a chat session", () => {
    const fixture = equipmentModuleFixture;
    const withoutChat = renderStaticRuntime(fixture, { sessionId: undefined, initialViewSlug: "equipment_hub" });
    const withChat = renderStaticRuntime(fixture, { sessionId: fixture.sessionId, initialViewSlug: "equipment_hub" });
    expect(withoutChat).not.toContain("Бюджетирование");
    expect(withChat).toContain("Бюджетирование");
  });

  it("renders a FileRef row value as a download command", async () => {
    const { container, service } = await mountRuntime(filesModuleFixture, { initialViewSlug: "files_primary" });
    const fileButton = [...container.querySelectorAll("button")].find((button) => button.textContent === "guide.pdf");
    expect(fileButton).toBeDefined();
    await act(async () => { fileButton!.click(); });
    expect(service.downloadFile).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ asset_id: "asset-1" }));
  });

  it("routes the primary create command to the form, validates required fields, and confirms the saved row with fresh GET", async () => {
    const { container, service } = await mountRuntime(filesModuleFixture, { initialViewSlug: "files_primary" });
    const createButton = [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("Новый файл"));
    expect(createButton).toBeDefined();
    await act(async () => { createButton!.click(); });
    const form = container.querySelector<HTMLFormElement>("form.module-runtime-form");
    const nameInput = container.querySelector<HTMLInputElement>('input[name="name"]');
    expect(nameInput).not.toBeNull();
    expect(form).not.toBeNull();

    await act(async () => { form!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(service.createRow).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Заполните обязательное поле");

    nameInput!.value = "created.pdf";
    vi.mocked(service.listRows).mockResolvedValue({ items: [{ row_id: "new", body: { name: "created.pdf", target_path: "", project_ids: [] } }], total: 1, page: 1, page_size: 200 });
    await act(async () => { form!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(service.createRow).toHaveBeenCalledWith(expect.objectContaining({ projectId: "project-beta", moduleId: "mod_files", sessionId: "session-beta" }), "files", expect.objectContaining({ name: "created.pdf" }));
    expect(service.listRows).toHaveBeenCalledWith(expect.objectContaining({ projectId: "project-beta", moduleId: "mod_files", sessionId: "session-beta" }), "files", expect.objectContaining({ page: 1, pageSize: 200, chats: "all" }));
    expect(container.textContent).toContain("Запись создана и подтверждена сервером.");
  });

  it("opens an exact collection row in its declared editable form and retains the row body", async () => {
    const { container, service } = await mountRuntime(filesModuleFixture, { initialViewSlug: "files_primary" });
    const openButton = [...container.querySelectorAll("button")].find((button) => button.title === "Открыть запись");
    expect(openButton).toBeDefined();
    await act(async () => { openButton!.click(); });
    expect(container.querySelector<HTMLInputElement>('input[name="name"]')?.value).toBe("guide.pdf");
    expect(container.textContent).toContain("Сохранить");
    expect(service.patchRow).not.toHaveBeenCalled();
    await act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent === "Назад")!.click(); });
    expect(container.textContent).toContain("guide.pdf");
  });

  it("shows an explicit unknown outcome when create readback is absent, mismatched, or unavailable", async () => {
    const { container, service } = await mountRuntime(filesModuleFixture, { initialViewSlug: "files_primary" });
    await act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("Новый файл"))!.click(); });
    const nameInput = container.querySelector<HTMLInputElement>('input[name="name"]')!;
    const form = container.querySelector<HTMLFormElement>("form.module-runtime-form")!;
    nameInput.value = "created.pdf";
    vi.mocked(service.listRows).mockResolvedValue({ items: [{ row_id: "new", body: { name: "different.pdf" } }], total: 1, page: 1, page_size: 200 });
    await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(container.textContent).toContain("Неизвестный результат создания");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("не повторяйте операцию автоматически");

    await act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent === "Назад")!.click(); });
    await act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("Новый файл"))!.click(); });
    const secondInput = container.querySelector<HTMLInputElement>('input[name="name"]')!;
    const secondForm = container.querySelector<HTMLFormElement>("form.module-runtime-form")!;
    secondInput.value = "created.pdf";
    vi.mocked(service.listRows).mockRejectedValueOnce(new Error("GET unavailable"));
    await act(async () => { secondForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(container.textContent).toContain("Неизвестно, создана ли запись");
  });

  it("runs a typed row action with the exact backend row id and collected params; cancel does not invoke it", async () => {
    const service = serviceFor();
    vi.mocked(service.invokeAction).mockResolvedValue({ kind: "files.export", ok: true, status: "completed", result: { exported: true } });
    const base = initialRuntimeFor(filesModuleFixture);
    const manifest: ModuleManifest = {
      ...base.manifest,
      actions: [{ id: "files_export", kind: "files.export", enabled: true, params: { target: "workbook" } }],
      views: base.manifest.views.map((view) => view.slug === "files_primary" ? { ...view, ui_json: { ...view.ui_json, row_actions: [{ kind: "invoke_action", action: "files_export", label: "Export", params_form: [{ name: "format", label: "Format", type: "enum", required: true, values: ["csv", "xlsx"] }, { name: "include_archived", type: "bool" }, { name: "count", type: "number" }] }] } } : view),
    };
    const { container } = await mountRuntime(filesModuleFixture, { service, initialRuntime: { ...base, manifest } as never, initialViewSlug: "files_primary" });
    const actionButton = [...container.querySelectorAll("button")].find((button) => button.textContent === "Export");
    expect(actionButton).toBeDefined();
    await act(async () => { actionButton!.click(); });
    const cancel = [...container.querySelectorAll("button")].find((button) => button.textContent === "Отмена");
    await act(async () => { cancel!.click(); });
    expect(service.invokeAction).not.toHaveBeenCalled();

    await act(async () => { actionButton!.click(); });
    const actionForm = container.querySelector<HTMLFormElement>("form.module-runtime-action-form")!;
    actionForm.querySelector<HTMLSelectElement>('select[name="format"]')!.value = "xlsx";
    actionForm.querySelector<HTMLInputElement>('input[name="count"]')!.value = "3";
    vi.mocked(service.listRows).mockResolvedValue({ items: [{ row_id: "file_row", body: { name: "guide.pdf" } }], total: 1, page: 1, page_size: 200 });
    await act(async () => { actionForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(service.invokeAction).toHaveBeenCalledWith(expect.objectContaining({ moduleId: "mod_files", projectId: "project-beta", sessionId: "session-beta" }), "files_export", { rowId: "file_row", params: { target: "workbook", format: "xlsx", include_archived: false, count: 3 } });
    expect(service.listRows).toHaveBeenCalledWith(expect.objectContaining({ projectId: "project-beta", moduleId: "mod_files", sessionId: "session-beta" }), "files", expect.objectContaining({ page: 1, pageSize: 200, chats: "all" }));
    expect(container.textContent).toContain("Изменения подтверждены сервером.");
    expect(container.textContent).toContain("Дополнительно");
    expect(container.textContent).toContain('"exported":true');
  });

  it("does not invoke a row action with missing required parameters and shows backend errors", async () => {
    const service = serviceFor();
    const base = initialRuntimeFor(filesModuleFixture);
    const manifest: ModuleManifest = {
      ...base.manifest,
      actions: [{ id: "files_export", kind: "files.export", enabled: true }],
      views: base.manifest.views.map((view) => view.slug === "files_primary" ? { ...view, ui_json: { ...view.ui_json, row_actions: [{ kind: "invoke_action", action: "files_export", label: "Export", params_form: [{ name: "format", type: "text", required: true }] }] } } : view),
    };
    const { container } = await mountRuntime(filesModuleFixture, { service, initialRuntime: { ...base, manifest } as never, initialViewSlug: "files_primary" });
    await act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent === "Export")!.click(); });
    const form = container.querySelector<HTMLFormElement>("form.module-runtime-action-form")!;
    await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(service.invokeAction).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Заполните обязательный параметр");

    const input = form.querySelector<HTMLInputElement>('input[name="format"]')!;
    input.value = "csv";
    vi.mocked(service.invokeAction).mockRejectedValueOnce(new Error("backend refused action"));
    await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(container.textContent).toContain("backend refused action");
  });

  it("registers domain extensions through explicit slots without module-specific UI branches", () => {
    const registry = createModuleSlotRegistry();
    const renderer = () => <aside>Equipment catalog extension</aside>;
    const dispose = registry.register("mod_equipment", "catalogs_list", "catalog_picker", renderer);
    expect(registry.resolve("mod_equipment", "catalogs_list", "catalog_picker")).toBe(renderer);
    dispose();
    expect(registry.resolve("mod_equipment", "catalogs_list", "catalog_picker")).toBeUndefined();
  });

  it("speaks English for the runtime's own words without translating the module's manifest copy", async () => {
    window.localStorage.setItem("agentscale.locale", "en");
    try {
      const { container } = await mountRuntime(filesModuleFixture, { initialViewSlug: "files_primary" });
      const text = container.textContent ?? "";

      expect(text).toContain("Search the data");
      expect(text).toContain("Advanced");
      expect(text).toContain("Project data");
      // The fixture's own labels stay as the module author wrote them.
      expect(text).toContain("Новый файл");
    } finally {
      window.localStorage.removeItem("agentscale.locale");
    }
  });
});

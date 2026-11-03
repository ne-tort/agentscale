// @vitest-environment jsdom
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectRuntime } from "./ProjectRuntime";
import type { ProjectRuntimeService, ProjectRuntimeSnapshot } from "./types";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const projectId = "project-42";

function snapshot(overrides: Partial<ProjectRuntimeSnapshot> = {}): ProjectRuntimeSnapshot {
  return {
    project: { id: projectId, cabinet_id: "cabinet-9", name: "Runtime Lab", status: "active" },
    container: { project_id: projectId, observed_state: "running", runtime: { observed_state: "running", ready: true } },
    metrics: null,
    files: { path: "", entries: [] },
    attachments: [],
    keys: [],
    models: { models: [] },
    chats: { cabinet_id: "cabinet-9", projects: [], project_chats: [] },
    ...overrides,
  };
}

function serviceFor(overrides: Partial<ProjectRuntimeService> = {}): ProjectRuntimeService {
  const current = snapshot();
  return {
    load: vi.fn(async () => current),
    getProject: vi.fn(async () => current.project),
    getContainer: vi.fn(async () => current.container),
    getMetrics: vi.fn(async () => ({})),
    listWorkspace: vi.fn(async (_id: string, path = "") => ({ path, entries: [] })),
    previewWorkspace: vi.fn(async (_id: string, path: string) => ({ path, name: path.split("/").at(-1) ?? path, content: "preview" })),
    downloadWorkspace: vi.fn(async () => new Blob(["file"])),
    listAttachments: vi.fn(async () => []),
    listKeys: vi.fn(async () => []),
    listModels: vi.fn(async () => ({ models: [] })),
    listChats: vi.fn(async () => ({ cabinet_id: "cabinet-9", projects: [], project_chats: [] })),
    patchProject: vi.fn(async () => current.project),
    runAction: vi.fn(async () => current.project),
    ...overrides,
  };
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;
let createObjectUrlDescriptor: PropertyDescriptor | undefined;
let revokeObjectUrlDescriptor: PropertyDescriptor | undefined;

async function mount(props: Partial<ComponentProps<typeof ProjectRuntime>> = {}) {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<ProjectRuntime projectId={projectId} onNavigate={vi.fn()} onModelChange={vi.fn()} {...props} />);
  });
  return { container: host, service: props.service };
}

async function waitFor(condition: () => boolean): Promise<void> {
  const deadline = Date.now() + 3000;
  while (!condition()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for ProjectRuntime state");
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
  }
}

function choose(select: HTMLSelectElement | undefined, value: string): void {
  if (!select) return;
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(select, value);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  vi.restoreAllMocks();
  if (createObjectUrlDescriptor) Object.defineProperty(URL, "createObjectURL", createObjectUrlDescriptor);
  else Reflect.deleteProperty(URL, "createObjectURL");
  if (revokeObjectUrlDescriptor) Object.defineProperty(URL, "revokeObjectURL", revokeObjectUrlDescriptor);
  else Reflect.deleteProperty(URL, "revokeObjectURL");
  createObjectUrlDescriptor = undefined;
  revokeObjectUrlDescriptor = undefined;
  document.body.replaceChildren();
});

describe("ProjectRuntime component workflow", () => {
  it("shows loading while the authorized route's project snapshot is being read", async () => {
    let resolveLoad!: (value: ProjectRuntimeSnapshot) => void;
    const load = vi.fn(() => new Promise<ProjectRuntimeSnapshot>((resolve) => { resolveLoad = resolve; }));
    const service = serviceFor({ load });
    const { container } = await mount({ service });

    expect(container.textContent).toContain("Загружаем project runtime");
    expect(load).toHaveBeenCalledWith(projectId);

    await act(async () => { resolveLoad(snapshot()); });
    expect(container.textContent).toContain("Runtime Lab");
    expect(container.textContent).not.toContain("Загружаем project runtime");
  });

  it("shows the project read error and recovers through retry", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("project missing")).mockResolvedValue(snapshot());
    const { container } = await mount({ service: serviceFor({ load }) });
    await waitFor(() => container.textContent?.includes("project missing") ?? false);

    expect(container.textContent).toContain("Project runtime недоступен");
    const retry = [...container.querySelectorAll("button")].find((button) => button.textContent === "Повторить");
    expect(retry).toBeDefined();
    await act(async () => { retry?.click(); });
    await waitFor(() => container.textContent?.includes("Runtime Lab") ?? false);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("discards an initial snapshot from another project and loads the route ID", async () => {
    const foreignSnapshot = snapshot({ project: { ...snapshot().project, id: "other-project", name: "Wrong project" } });
    const load = vi.fn(async (id: string) => snapshot({ project: { ...snapshot().project, id, name: "Route project" } }));
    const { container } = await mount({ service: serviceFor({ load }), initialSnapshot: foreignSnapshot });
    await waitFor(() => container.textContent?.includes("Route project") ?? false);

    expect(load).toHaveBeenCalledWith(projectId);
    expect(container.textContent).not.toContain("Wrong project");
    expect(container.textContent).toContain(`Project / ${projectId}`);
  });

  it("refreshes the readback from the backend", async () => {
    const updated = snapshot({ project: { ...snapshot().project, name: "Runtime Lab refreshed" } });
    const load = vi.fn().mockResolvedValue(updated);
    const { container } = await mount({ service: serviceFor({ load }), initialSnapshot: snapshot() });
    const refresh = [...container.querySelectorAll("button")].find((button) => button.textContent === "Обновить");

    await act(async () => { refresh?.click(); });
    expect(load).toHaveBeenCalledWith(projectId);
    expect(container.textContent).toContain("Runtime Lab refreshed");
  });

  it("shows the backend Pod observation after refreshing a provisioning project", async () => {
    const preparing = snapshot({
      project: { ...snapshot().project, status: "active", launch_phase: "preparing" },
      container: { project_id: projectId, observed_state: "provisioning", runtime: { observed_state: "provisioning", desired_state: "running", ready: false } },
    });
    const running = snapshot({
      container: { project_id: projectId, observed_state: "running", runtime: { observed_state: "running", desired_state: "running", ready: true } },
    });
    const load = vi.fn(async () => running);
    const { container } = await mount({ service: serviceFor({ load }), initialSnapshot: preparing });
    expect(container.textContent).toContain("Provisioning");

    const refresh = [...container.querySelectorAll("button")].find((button) => button.textContent === "Обновить");
    await act(async () => { refresh?.click(); });

    expect(load).toHaveBeenCalledWith(projectId);
    expect(container.textContent).toContain("Работает");
    expect(container.textContent).toContain("Readiness подтверждён");
  });

  it("keeps the last readback visible on refresh error and clears it after recovery", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("runtime read unavailable")).mockResolvedValue(
      snapshot({ project: { ...snapshot().project, name: "Runtime Lab recovered" } }),
    );
    const { container } = await mount({ service: serviceFor({ load }), initialSnapshot: snapshot() });
    const refresh = [...container.querySelectorAll("button")].find((button) => button.textContent === "Обновить");

    await act(async () => { refresh?.click(); });
    expect(container.textContent).toContain("Runtime Lab");
    expect(container.textContent).toContain("runtime read unavailable");
    const retry = [...container.querySelectorAll("button")].find((button) => button.textContent === "Повторить загрузку");
    await act(async () => { retry?.click(); });
    expect(container.textContent).toContain("Runtime Lab recovered");
    expect(container.textContent).not.toContain("runtime read unavailable");
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("requires launch confirmation and reports accepted provisioning until a Pod is running", async () => {
    const draft = snapshot({
      project: { ...snapshot().project, status: "draft", agent_provider: null, resolved_ai_key_id: null },
      container: null,
      files: null,
      keys: [{ id: "key-codex", name: "Codex key", provider: "codex", status: "active", has_secret: true }],
      models: { models: [], default_model: null, source: "catalog" },
    });
    const configured = snapshot({
      project: { ...draft.project, agent_provider: "codex", resolved_ai_key_id: "key-codex" },
      container: null,
      files: null,
      keys: draft.keys,
      models: { models: [], default_model: null, source: "unavailable" },
    });
    const preparing = snapshot({
      project: { ...configured.project, status: "active", launch_phase: "preparing" },
      container: { project_id: projectId, observed_state: "provisioning" },
      files: null,
      keys: draft.keys,
      models: { models: [], source: "unavailable" },
    });
    const runAction = vi.fn(async () => ({ ...configured.project, status: "active", runtime: { observed_state: "provisioning", desired_state: "running", ready: false } }));
    const load = vi.fn().mockResolvedValueOnce(configured).mockResolvedValueOnce(preparing);
    const patchProject = vi.fn(async () => configured.project);
    const service = serviceFor({ runAction, load, patchProject });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    const { container } = await mount({ service, initialSnapshot: draft, initialTab: "settings" });
    const [provider, key] = [...container.querySelectorAll<HTMLSelectElement>("select")];

    await act(async () => { choose(provider, "codex"); });
    await act(async () => { choose(key, "key-codex"); });
    const save = [...container.querySelectorAll("button")].find((button) => button.textContent === "Сохранить настройки");
    await act(async () => { save?.click(); });
    await waitFor(() => container.textContent?.includes("Provider и AI key сохранены и подтверждены backend") ?? false);
    expect(patchProject).toHaveBeenCalledWith(projectId, expect.objectContaining({ agent_provider: "codex", resolved_ai_key_id: "key-codex" }));
    expect(load).toHaveBeenCalledWith(projectId);
    const launch = [...container.querySelectorAll("button")].find((button) => button.textContent === "Запустить проект");

    expect(launch?.disabled).toBe(false);
    await act(async () => { launch?.click(); });
    expect(runAction).not.toHaveBeenCalled();
    await act(async () => { launch?.click(); });
    await waitFor(() => container.textContent?.includes("Pod ещё готовится") ?? false);

    expect(confirm).toHaveBeenCalledWith("Запустить runtime проекта? Backend подготовит workspace и provision контейнера.");
    expect(runAction).toHaveBeenCalledWith(projectId, "launch");
    expect(load).toHaveBeenCalledWith(projectId);
    expect(container.textContent).toContain("Pod ещё готовится");
    expect(container.textContent).not.toContain("Запустить завершено");
    expect(container.textContent).toContain("Live model недоступна до запуска Pod");
  });

  it("keeps launch disabled until provider and active key are explicitly saved", async () => {
    const draft = snapshot({
      project: { ...snapshot().project, status: "draft", agent_provider: null, resolved_ai_key_id: null },
      container: null,
      files: null,
      keys: [{ id: "key-codex", name: "Codex key", provider: "codex", status: "active" }],
      models: { models: [], source: "unavailable" },
    });
    const { container } = await mount({ service: serviceFor(), initialSnapshot: draft, initialTab: "settings" });
    const launch = [...container.querySelectorAll("button")].find((button) => button.textContent === "Запустить проект");

    expect(container.textContent).toContain("черновик создаётся в Projects через реальный POST");
    expect(container.querySelector('a[href="/projects"]')).not.toBeNull();
    expect(launch?.disabled).toBe(true);
  });

  it("requires an explicit live model choice before creating a new chat session", async () => {
    const initial = snapshot({
      project: { ...snapshot().project, agent_provider: "codex", resolved_ai_key_id: "key-codex" },
      container: { project_id: projectId, observed_state: "running", runtime: { observed_state: "running", ready: true } },
      keys: [{ id: "key-codex", name: "Codex key", provider: "codex", status: "active", has_secret: true }],
      models: { models: [{ id: "gpt-5", label: "GPT-5" }], default_model: "gpt-5", source: "live" },
    });
    const onModelChange = vi.fn();
    const { container } = await mount({ service: serviceFor(), initialSnapshot: initial, initialTab: "settings", onModelChange });
    const [, , model] = [...container.querySelectorAll<HTMLSelectElement>("select")];
    const apply = [...container.querySelectorAll("button")].find((button) => button.textContent === "Новый чат с выбранной моделью");

    expect(model?.value).toBe("");
    expect(apply?.disabled).toBe(true);
    await act(async () => { choose(model, "gpt-5"); });
    expect(apply?.disabled).toBe(false);
    await act(async () => { apply?.click(); });

    expect(onModelChange).toHaveBeenCalledWith(projectId, "gpt-5");
    expect(container.textContent).toContain("Создание agent session с моделью gpt-5 передано chat surface");
  });

  it("does not offer launch for an already active project", async () => {
    const { container } = await mount({ service: serviceFor(), initialSnapshot: snapshot() });
    const launch = [...container.querySelectorAll("button")].find((button) => button.textContent === "Запустить");

    expect(launch?.disabled).toBe(true);
  });

  it("navigates runtime tabs, browses folders, previews a file, and downloads it", async () => {
    const initial = snapshot({
      files: { path: "", entries: [{ name: "src", path: "src", kind: "dir" }, { name: "README.md", path: "README.md", kind: "file", size: 4 }] },
    });
    const listWorkspace = vi.fn(async (_id: string, path = "") => path === "src"
      ? { path, entries: [{ name: "main.ts", path: "src/main.ts", kind: "file", size: 4 }] }
      : { path: "", entries: initial.files?.entries ?? [] });
    const previewWorkspace = vi.fn(async (_id: string, path: string) => ({ path, name: "main.ts", content: "code" }));
    const downloadWorkspace = vi.fn(async () => new Blob(["code"]));
    const service = serviceFor({ listWorkspace, previewWorkspace, downloadWorkspace });
    const onNavigate = vi.fn();
    const { container } = await mount({ service, initialSnapshot: initial, onNavigate });
    expect(container.textContent).toContain("Создание папок недоступно");
    const filesTab = [...container.querySelectorAll("nav button")].find((button) => button.textContent?.startsWith("Files"));
    await act(async () => { filesTab?.click(); });

    expect(onNavigate).toHaveBeenCalledWith("files", projectId);
    const folder = [...container.querySelectorAll(".project-runtime-file-row button")].find((button) => button.textContent?.includes("src"));
    await act(async () => { folder?.click(); });
    await waitFor(() => container.textContent?.includes("src/main.ts") ?? false);
    expect(listWorkspace).toHaveBeenCalledWith(projectId, "src");

    const file = [...container.querySelectorAll(".project-runtime-file-row button")].find((button) => button.textContent?.includes("main.ts"));
    await act(async () => { file?.click(); });
    await waitFor(() => container.textContent?.includes("code") ?? false);
    expect(previewWorkspace).toHaveBeenCalledWith(projectId, "src/main.ts");

    const createObjectURL = vi.fn(() => "blob:project-runtime");
    const revokeObjectURL = vi.fn();
    createObjectUrlDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    revokeObjectUrlDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const downloadedLink = { filename: "", href: "" };
    const clickDownload = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      downloadedLink.filename = this.download;
      downloadedLink.href = this.href;
    });
    const download = container.querySelector<HTMLButtonElement>('button[aria-label="Скачать main.ts"]');
    await act(async () => { download?.click(); await Promise.resolve(); });

    expect(downloadWorkspace).toHaveBeenCalledWith(projectId, "src/main.ts");
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(clickDownload).toHaveBeenCalledOnce();
    expect(downloadedLink).toEqual({ filename: "main.ts", href: "blob:project-runtime" });
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:project-runtime");
  });

  it("hides stale files while Pod is unavailable and refreshes status before reopening the live workspace", async () => {
    const paused = snapshot({
      project: { ...snapshot().project, status: "paused" },
      container: { project_id: projectId, observed_state: "paused" },
      files: { path: "", entries: [{ name: "stale.txt", path: "stale.txt", kind: "file" }] },
    });
    const running = snapshot({ files: null });
    const load = vi.fn(async () => running);
    const listWorkspace = vi.fn(async (_id: string, path = "") => ({ path, entries: [{ name: "listed.txt", path: "listed.txt", kind: "file" }] }));
    const { container } = await mount({ service: serviceFor({ load, listWorkspace }), initialSnapshot: paused, initialTab: "files" });

    expect(container.textContent).toContain("Live workspace недоступен");
    expect(container.textContent).not.toContain("stale.txt");
    expect(listWorkspace).not.toHaveBeenCalled();
    const refresh = [...container.querySelectorAll("button")].find((button) => button.textContent === "Обновить статус Pod");
    await act(async () => { refresh?.click(); });

    expect(load).toHaveBeenCalledWith(projectId);
    await waitFor(() => container.textContent?.includes("listed.txt") ?? false);
    expect(container.textContent).not.toContain("stale.txt");
  });

  it("shows workspace errors and retries the live listing", async () => {
    const listWorkspace = vi.fn().mockRejectedValueOnce(new Error("workspace service unavailable"));
    const { container } = await mount({ service: serviceFor({ listWorkspace }), initialSnapshot: snapshot({ files: null }), initialTab: "files" });
    await waitFor(() => container.textContent?.includes("workspace service unavailable") ?? false);
    expect(container.textContent).toContain("Live workspace пока не доступен");
    listWorkspace.mockResolvedValue({ path: "", entries: [{ name: "README.md", path: "README.md", kind: "file" }] });
    const retry = container.querySelector<HTMLButtonElement>(".project-runtime-files-layout .project-runtime-section-heading button");
    await act(async () => { retry?.click(); });
    await waitFor(() => container.textContent?.includes("README.md") ?? false);
  });
});
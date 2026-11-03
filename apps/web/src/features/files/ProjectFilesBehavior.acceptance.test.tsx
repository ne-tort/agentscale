// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { ProjectFilesWorkspace } from "./ProjectFilesWorkspace";
import type { WorkspaceEntry, WorkspaceListing, WorkspaceService, WorkspaceTextPreview } from "./contracts";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const file = (path: string): WorkspaceEntry => ({ name: path.split("/").at(-1) ?? path, path, kind: "file", size: 4, modified_at: null });
const dir = (path: string): WorkspaceEntry => ({ name: path.split("/").at(-1) ?? path, path, kind: "dir", size: null, modified_at: null });
const textPreview = (path: string, content: string): WorkspaceTextPreview => ({ path, name: path.split("/").at(-1) ?? path, size: content.length, truncated: false, content });

function makeService(overrides: Partial<WorkspaceService> = {}): WorkspaceService {
  return {
    listEntries: vi.fn(async (_projectId, path = "") => ({ path, entries: [] })),
    previewText: vi.fn(async (_projectId, path) => textPreview(path, "")),
    download: vi.fn(async (_projectId, path) => ({ blob: new Blob(), filename: path.split("/").at(-1) ?? path, path, size: 0 })),
    upload: vi.fn(async (_projectId, path, body) => ({ written: true, path, size: typeof body === "string" ? body.length : body.size })),
    createDirectory: vi.fn(async (_projectId, path) => ({ created: true, path })),
    deleteEntry: vi.fn(async () => ({ deleted: true })),
    moveEntry: vi.fn(async () => ({ moved: true })),
    copyEntry: vi.fn(async () => ({ copied: true })),
    ...overrides,
  };
}

function ProjectRoute({ service }: { service: WorkspaceService }) {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  return <>
    <button type="button" onClick={() => navigate("/projects/project-2/files")}>Switch project</button>
    <ProjectFilesWorkspace projectId={projectId} service={service} />
  </>;
}

function LocationProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return <>
    <output data-testid="location">{location.pathname}{location.search}</output>
    <button type="button" onClick={() => navigate(-1)}>Browser back</button>
  </>;
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;
let anchorClickDescriptor: PropertyDescriptor | undefined;
let createObjectUrlDescriptor: PropertyDescriptor | undefined;
let revokeObjectUrlDescriptor: PropertyDescriptor | undefined;

async function mount(service: WorkspaceService, url: string) {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(
    <MemoryRouter initialEntries={[url]}>
      <Routes><Route path="/projects/:projectId/files/*" element={<ProjectRoute service={service} />} /></Routes>
      <LocationProbe />
    </MemoryRouter>,
  ));
  return host;
}

async function waitFor(condition: () => boolean): Promise<void> {
  const deadline = Date.now() + 3000;
  while (!condition()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for workspace acceptance condition");
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
  }
}

async function setValue(element: HTMLInputElement | HTMLTextAreaElement, value: string): Promise<void> {
  await act(async () => {
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

async function submitPath(container: HTMLElement, path: string): Promise<void> {
  const input = container.querySelector<HTMLInputElement>("#files-path-input");
  if (!input) throw new Error("Workspace path input is missing");
  await setValue(input, path);
  await act(async () => input.closest("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  if (anchorClickDescriptor) Object.defineProperty(HTMLAnchorElement.prototype, "click", anchorClickDescriptor);
  else Reflect.deleteProperty(HTMLAnchorElement.prototype, "click");
  if (createObjectUrlDescriptor) Object.defineProperty(URL, "createObjectURL", createObjectUrlDescriptor);
  else Reflect.deleteProperty(URL, "createObjectURL");
  if (revokeObjectUrlDescriptor) Object.defineProperty(URL, "revokeObjectURL", revokeObjectUrlDescriptor);
  else Reflect.deleteProperty(URL, "revokeObjectURL");
  anchorClickDescriptor = undefined;
  createObjectUrlDescriptor = undefined;
  revokeObjectUrlDescriptor = undefined;
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("Project Pod files URL and recovery behavior", () => {
  it("opens a selected file from a deep URL and restores it after a reload", async () => {
    const service = makeService({
      listEntries: vi.fn(async (_projectId, path = "") => ({ path, entries: path === "docs" ? [file("docs/notes.txt")] : [dir("docs")] })),
      previewText: vi.fn(async (_projectId, path) => textPreview(path, "persisted text")),
    });
    const url = "/projects/project-1/files/docs/notes.txt";
    let container = await mount(service, url);
    await waitFor(() => container.querySelector<HTMLTextAreaElement>("textarea[aria-label='Editing notes.txt']")?.value === "persisted text");
    expect(container.querySelector<HTMLInputElement>("#files-path-input")?.value).toBe("docs");
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe(url);
    expect(service.previewText).toHaveBeenCalledWith("project-1", "docs/notes.txt", expect.any(Object));

    await act(async () => root?.unmount());
    root = undefined;
    container.remove();
    host = undefined;
    container = await mount(service, url);
    await waitFor(() => container.querySelector<HTMLTextAreaElement>("textarea[aria-label='Editing notes.txt']")?.value === "persisted text");
    expect(container.querySelector(".files-entry-row.is-selected")?.textContent).toContain("notes.txt");
  });

  it("updates the URL and visible directory when navigating by path", async () => {
    const service = makeService({
      listEntries: vi.fn(async (_projectId, path = "") => ({ path, entries: path === "docs" ? [file("docs/readme.md")] : [dir("docs")] })),
    });
    const container = await mount(service, "/projects/project-1/files");
    await waitFor(() => Boolean(container.querySelector(".files-entry-main")));
    await submitPath(container, "docs");
    await waitFor(() => container.querySelector("[data-testid='location']")?.textContent === "/projects/project-1/files?path=docs");
    await waitFor(() => container.querySelector(".files-entry-row")?.textContent?.includes("readme.md") ?? false);
    expect(container.querySelector<HTMLInputElement>("#files-path-input")?.value).toBe("docs");
    const back = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Browser back");
    await act(async () => back?.click());
    await waitFor(() => container.querySelector("[data-testid='location']")?.textContent === "/projects/project-1/files");
    expect(container.querySelector<HTMLInputElement>("#files-path-input")?.value).toBe("");
  });

  it("ignores a late directory response when the user switches paths", async () => {
    let resolveSlow!: (listing: WorkspaceListing) => void;
    let slowSignal: AbortSignal | undefined;
    const slowListing = new Promise<WorkspaceListing>((resolve) => { resolveSlow = resolve; });
    const service = makeService({
      listEntries: vi.fn((_projectId, path = "", options) => {
        if (path === "slow") { slowSignal = options?.signal; return slowListing; }
        return Promise.resolve({ path, entries: path === "quick" ? [file("quick/fresh.txt")] : [] });
      }),
    });
    const container = await mount(service, "/projects/project-1/files?path=slow");
    await waitFor(() => slowSignal !== undefined);
    await submitPath(container, "quick");
    await waitFor(() => container.querySelector(".files-entry-row")?.textContent?.includes("fresh.txt") ?? false);
    await act(async () => { resolveSlow({ path: "slow", entries: [file("slow/stale.txt")] }); });
    expect(slowSignal?.aborted).toBe(true);
    expect(container.textContent).not.toContain("stale.txt");
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-1/files?path=quick");
  });

  it("aborts and discards pending list responses after switching projects", async () => {
    let resolveOld!: (listing: WorkspaceListing) => void;
    let oldSignal: AbortSignal | undefined;
    const oldListing = new Promise<WorkspaceListing>((resolve) => { resolveOld = resolve; });
    const service = makeService({
      listEntries: vi.fn((projectId, path = "", options) => {
        if (projectId === "project-1") { oldSignal = options?.signal; return oldListing; }
        return Promise.resolve({ path, entries: [file("project-2/fresh.txt")] });
      }),
    });
    const container = await mount(service, "/projects/project-1/files");
    await waitFor(() => oldSignal !== undefined);
    const switchButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Switch project");
    await act(async () => switchButton?.click());
    await waitFor(() => container.querySelector(".files-entry-row")?.textContent?.includes("fresh.txt") ?? false);
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => { resolveOld({ path: "", entries: [file("project-1/stale.txt")] }); });
    expect(container.textContent).not.toContain("stale.txt");
    expect(container.querySelector(".files-panel-heading")?.textContent).toContain("project-2");
  });

  it("uploads exact bytes, confirms the upload by readback, and downloads via the service", async () => {
    const files = new Map<string, Blob>();
    const service = makeService({
      listEntries: vi.fn(async (_projectId, path = "") => ({ path, entries: [...files.keys()].map(file) })),
      upload: vi.fn(async (_projectId, path, body) => {
        files.set(path, typeof body === "string" ? new Blob([body]) : body);
        return { written: true, path, size: files.get(path)?.size ?? 0 };
      }),
      download: vi.fn(async (_projectId, path) => {
        const blob = files.get(path) ?? new Blob();
        return { blob, filename: path.split("/").at(-1) ?? path, path, size: blob.size };
      }),
    });
    const container = await mount(service, "/projects/project-1/files");
    await waitFor(() => Boolean(container.querySelector(".files-entry-list")));
    createObjectUrlDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    revokeObjectUrlDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:workspace") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    anchorClickDescriptor = Object.getOwnPropertyDescriptor(HTMLAnchorElement.prototype, "click");
    const clickDownload = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const input = container.querySelector<HTMLInputElement>("input[aria-label='Upload files']");
    expect(input).toBeTruthy();
    Object.defineProperty(input, "files", { configurable: true, value: [new File(["payload"], "upload.txt", { type: "text/plain" })] });
    await act(async () => input?.dispatchEvent(new Event("change", { bubbles: true })));
    await waitFor(() => container.textContent?.includes("Uploaded 1 file") ?? false);
    expect(service.upload).toHaveBeenCalledWith("project-1", "upload.txt", expect.any(File));
    expect(await files.get("upload.txt")?.text()).toBe("payload");
    expect(service.download).toHaveBeenCalledWith("project-1", "upload.txt");
    const download = [...container.querySelectorAll<HTMLButtonElement>(".files-action-button")].find((button) => button.textContent === "Download");
    await act(async () => download?.click());
    await waitFor(() => clickDownload.mock.calls.length === 1);
    expect(service.download).toHaveBeenCalledTimes(2);
  });

  it("confirms a text save only after matching preview readback", async () => {
    let content = "before";
    const service = makeService({
      listEntries: vi.fn(async (_projectId, path = "") => ({ path, entries: [file("notes.txt")] })),
      previewText: vi.fn(async (_projectId, path) => textPreview(path, content)),
      upload: vi.fn(async (_projectId, path, body) => {
        content = typeof body === "string" ? body : await body.text();
        return { written: true, path, size: content.length };
      }),
    });
    const container = await mount(service, "/projects/project-1/files?selected=notes.txt");
    await waitFor(() => container.querySelector<HTMLTextAreaElement>("textarea[aria-label='Editing notes.txt']")?.value === "before");
    const editor = container.querySelector<HTMLTextAreaElement>("textarea[aria-label='Editing notes.txt']");
    expect(editor).toBeTruthy();
    await setValue(editor!, "after");
    const save = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Save changes");
    await act(async () => save?.click());
    await waitFor(() => container.textContent?.includes("Saved notes.txt") ?? false);
    expect(service.upload).toHaveBeenCalledWith("project-1", "notes.txt", "after");
    expect(service.previewText).toHaveBeenCalledTimes(2);
    expect(content).toBe("after");
    expect(editor?.value).toBe("after");
  });

  it("keeps an unsaved editor on URL navigation cancel and discards only after confirmation", async () => {
    const service = makeService({
      listEntries: vi.fn(async (_projectId, path = "") => ({ path, entries: path ? [] : [file("notes.txt")] })),
      previewText: vi.fn(async (_projectId, path) => textPreview(path, "saved")),
    });
    const container = await mount(service, "/projects/project-1/files");
    await waitFor(() => Boolean(container.querySelector(".files-entry-row")));
    const entry = [...container.querySelectorAll<HTMLButtonElement>(".files-entry-main")].find((button) => button.textContent?.includes("notes.txt"));
    await act(async () => entry?.click());
    await waitFor(() => Boolean(container.querySelector("textarea[aria-label='Editing notes.txt']")));
    const editor = container.querySelector<HTMLTextAreaElement>("textarea[aria-label='Editing notes.txt']");
    await setValue(editor!, "unsaved");
    const back = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Browser back");

    await act(async () => back?.click());
    await waitFor(() => container.textContent?.includes("Discard unsaved changes?") ?? false);
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-1/files?selected=notes.txt");
    const cancel = [...container.querySelectorAll<HTMLButtonElement>(".files-modal button")].find((button) => button.textContent === "Cancel");
    await act(async () => cancel?.click());
    expect(editor?.value).toBe("unsaved");

    await act(async () => back?.click());
    await waitFor(() => container.textContent?.includes("Discard unsaved changes?") ?? false);
    const discard = [...container.querySelectorAll<HTMLButtonElement>(".files-modal button")].find((button) => button.textContent === "Discard changes");
    await act(async () => discard?.click());
    await waitFor(() => container.querySelector("[data-testid='location']")?.textContent === "/projects/project-1/files");
    expect(container.querySelector(".files-modal")).toBeNull();
  });

  it("cancels an upload conflict without replacing the existing file", async () => {
    const service = makeService({ listEntries: vi.fn(async (_projectId, path = "") => ({ path, entries: path ? [] : [file("notes.txt")] })) });
    const container = await mount(service, "/projects/project-1/files");
    await waitFor(() => Boolean(container.querySelector(".files-entry-row")));
    const input = container.querySelector<HTMLInputElement>("input[aria-label='Upload files']");
    Object.defineProperty(input, "files", { configurable: true, value: [new File(["replacement"], "notes.txt")] });
    await act(async () => input?.dispatchEvent(new Event("change", { bubbles: true })));
    await waitFor(() => container.textContent?.includes("Replace existing files?") ?? false);
    const cancel = [...container.querySelectorAll<HTMLButtonElement>(".files-modal button")].find((button) => button.textContent === "Cancel");
    await act(async () => cancel?.click());
    expect(service.upload).not.toHaveBeenCalled();
    expect(container.querySelector(".files-modal")).toBeNull();
  });

  it("creates a nested folder only after a successful mkdir response and matching listing readback", async () => {
    const created = new Set<string>();
    const service = makeService({
      listEntries: vi.fn(async (_projectId, path = "") => ({
        path,
        entries: path === "" ? [dir("docs")] : [...created].filter((entry) => entry.startsWith(`${path}/`) && entry.slice(path.length + 1).indexOf("/") < 0).map(dir),
      })),
      createDirectory: vi.fn(async (_projectId, path) => {
        created.add(path);
        return { created: true, path };
      }),
    });
    const container = await mount(service, "/projects/project-1/files?path=docs");
    await waitFor(() => Boolean(container.querySelector(".files-entry-list")));
    await act(async () => container.querySelector<HTMLButtonElement>("button[aria-label='Create a folder']")?.click());
    const input = container.querySelector<HTMLInputElement>("#files-create-folder-name");
    expect(input).toBeTruthy();
    await setValue(input!, "reference");
    await act(async () => input?.closest("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await waitFor(() => container.textContent?.includes("Created reference") ?? false);
    expect(service.createDirectory).toHaveBeenCalledWith("project-1", "docs/reference");
    expect(container.querySelector(".files-entry-row")?.textContent).toContain("reference");
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe("/projects/project-1/files?path=docs");
  });

  it("rejects existing folder and file names before mkdir", async () => {
    const service = makeService({
      listEntries: vi.fn(async (_projectId, path = "") => ({ path, entries: [dir("existing-dir"), file("existing.txt")] })),
    });
    const container = await mount(service, "/projects/project-1/files");
    await waitFor(() => Boolean(container.querySelector(".files-entry-list")));
    await act(async () => container.querySelector<HTMLButtonElement>("button[aria-label='Create a folder']")?.click());
    const input = container.querySelector<HTMLInputElement>("#files-create-folder-name");
    await setValue(input!, "existing-dir");
    await act(async () => input?.closest("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await waitFor(() => container.textContent?.includes("already a folder") ?? false);
    expect(service.createDirectory).not.toHaveBeenCalled();
    await setValue(input!, "existing.txt");
    await act(async () => input?.closest("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await waitFor(() => container.textContent?.includes("existing file") ?? false);
    expect(service.createDirectory).not.toHaveBeenCalled();
  });

  it("rejects a separator-containing folder name", async () => {
    const service = makeService();
    const container = await mount(service, "/projects/project-1/files");
    await waitFor(() => Boolean(container.querySelector(".files-entry-list")));
    await act(async () => container.querySelector<HTMLButtonElement>("button[aria-label='Create a folder']")?.click());
    const input = container.querySelector<HTMLInputElement>("#files-create-folder-name");
    await setValue(input!, "nested/docs");
    await act(async () => input?.closest("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(container.textContent).toContain("without path separators");
    expect(service.createDirectory).not.toHaveBeenCalled();
  });

  it("does not report folder creation when the refreshed listing does not contain it", async () => {
    const service = makeService({
      createDirectory: vi.fn(async (_projectId, path) => ({ created: true, path })),
    });
    const container = await mount(service, "/projects/project-1/files");
    await waitFor(() => Boolean(container.querySelector(".files-entry-list")));
    await act(async () => container.querySelector<HTMLButtonElement>("button[aria-label='Create a folder']")?.click());
    const input = container.querySelector<HTMLInputElement>("#files-create-folder-name");
    await setValue(input!, "not-visible");
    await act(async () => input?.closest("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await waitFor(() => container.textContent?.includes("Workspace mutation was not confirmed") ?? false);
    expect(container.textContent).not.toContain("Created not-visible");
  });
});

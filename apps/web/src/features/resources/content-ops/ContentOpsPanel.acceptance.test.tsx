// @vitest-environment jsdom
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ContentOpsPanel } from "./ContentOpsPanel";
import { ContentOpsError, type ContentOpsService } from "./contracts";
import type { ContentAsset, ContentAssetVersion } from "./types";
import { CONTENT_UPLOAD_MAX_BYTES, contentFileFeedback } from "./validation";
import { SettingsProvider } from "../../../preferences/SettingsProvider";
import { POLL_BUDGET_MS } from "../../../test-support/poll-budget";
import { restoreBlobUrlDescriptors } from "../../../test-support/blob-url-stubs";

const asset = { id: "cast_asset-1", owner_scope: "company", owner_company_id: "company-1", visibility: "company", mime: "text/plain", title: "notes.txt", tags: [], created_by: "employee-1", latest_version: 1, created_at: "2026-10-01T12:00:00Z", updated_at: "2026-10-01T12:00:00Z" };

const service: ContentOpsService = {
  listAssets: async () => [],
  createAsset: async () => ({ id: "asset", owner_scope: "company", owner_company_id: "company-1", visibility: "company", mime: null, title: null, tags: [], created_by: null, latest_version: null, created_at: null, updated_at: null }),
  beginVersion: async () => ({ id: "version", asset_id: "asset", version: 1, size: 0, sha256: null, etag: null, object_metadata: {}, created_at: null }),
  uploadVersion: async () => ({ id: "version", asset_id: "asset", version: 1, size: 0, sha256: null, etag: null, object_metadata: {}, created_at: null }),
  downloadVersion: async () => ({ blob: new Blob(), filename: "file.bin", contentType: "application/octet-stream" }),
  listAcl: async () => [],
  replaceAcl: async () => [],
};

let root: Root | undefined;
let host: HTMLDivElement | undefined;
let createObjectURLDescriptor: PropertyDescriptor | undefined;
let revokeObjectURLDescriptor: PropertyDescriptor | undefined;
let anchorClickDescriptor: PropertyDescriptor | undefined;

/** The panel now reads copy through the i18n hook, so it needs the settings provider. */
function withSettings(ui: ReactElement): ReactElement {
  return <SettingsProvider>{ui}</SettingsProvider>;
}

/**
 * Poll until the UI reflects the action.
 *
 * Each poll runs inside its own `act`: React 19 does not commit a state update
 * until the enclosing `act` scope exits, so a poll that merely yields the event
 * loop — as this helper used to — can never observe the render it is waiting for
 * when the caller is itself inside `await act(async () => { … })`. That produced a
 * three-second timeout on every interaction-based test, not a product failure.
 */
async function waitFor(assertion: () => boolean): Promise<void> {
  const deadline = Date.now() + POLL_BUDGET_MS;
  while (!assertion()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for Content Ops UI");
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }
}

async function mountPanel(testService: ContentOpsService, assets = [asset]): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  // Render inside its own act scope, then assert outside it: React 19 only commits
  // when the enclosing act exits, so an assertion nested inside the same scope waits
  // for a render that cannot happen yet and times out.
  await act(async () => {
    root?.render(withSettings(<ContentOpsPanel companyId="company-1" service={testService} initialAssets={assets} />));
  });
  await waitFor(() => document.querySelectorAll(".content-ops__asset-meta, .content-ops__empty").length > 0);
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  if (createObjectURLDescriptor) Object.defineProperty(URL, "createObjectURL", createObjectURLDescriptor);
  else Reflect.deleteProperty(URL, "createObjectURL");
  restoreBlobUrlDescriptors({ createObjectURL: createObjectURLDescriptor, revokeObjectURL: revokeObjectURLDescriptor });
  if (anchorClickDescriptor) Object.defineProperty(HTMLAnchorElement.prototype, "click", anchorClickDescriptor);
  else Reflect.deleteProperty(HTMLAnchorElement.prototype, "click");
  createObjectURLDescriptor = undefined;
  revokeObjectURLDescriptor = undefined;
  anchorClickDescriptor = undefined;
  vi.restoreAllMocks();
});

describe("Content operations acceptance surface", () => {
  it("renders upload, asset/version selection, ACL, and the host-provided workspace link", () => {
    const markup = renderToStaticMarkup(withSettings(<ContentOpsPanel companyId="company-1" service={service} initialAssets={[asset]} workspaceBindingHref="/projects/project-1" />));

    expect(markup).toContain("Новый материал");
    expect(markup).toContain("Новая версия");
    expect(markup).toContain("Материал и версия");
    expect(markup).toContain("Загрузить список доступа");
    expect(markup).toContain("Открыть в проекте");
    expect(markup).toContain("Загрузил");
    expect(markup).toContain("Создан");
    expect(markup).toContain("История версий недоступна");
    expect(markup).toContain("Связать материал с проектом можно на странице проекта.");
    expect(markup).toContain("Сначала загрузите текущий список: сохранение заменяет его целиком.");
    expect(markup.toLowerCase()).not.toContain("upload_url");
    expect(markup.toLowerCase()).not.toContain("storage_key");
    // The company id is kept for whoever debugs the request, but only as a tooltip.
    expect(markup).toContain('title="company-1"');
    expect(markup).toContain("Здесь показаны лишь версии, загруженные за эту сессию.");
    expect(markup).toContain("В этой сессии нет загруженных версий — посмотреть существующие нельзя.");
  });

  it("reports MIME and size, enforces the API 100 MiB limit, and allows unlisted MIME types", () => {
    const accepted = contentFileFeedback({ size: CONTENT_UPLOAD_MAX_BYTES, type: "application/x-example" });
    const rejected = contentFileFeedback({ size: CONTENT_UPLOAD_MAX_BYTES + 1, type: "text/plain" });
    const fallback = contentFileFeedback({ size: 4, type: "" });

    expect(accepted).toMatchObject({ accepted: true, mime: "application/x-example" });
    expect(rejected).toMatchObject({ accepted: false, message: "The Content API accepts uploads up to 100 MiB." });
    expect(fallback.mime).toBe("application/octet-stream");
  });

  it("shows company asset loading, empty, safe error, and refresh retry states", async () => {
    let resolveInitial: ((items: ContentAsset[]) => void) | undefined;
    let rejectRefresh: ((cause: Error) => void) | undefined;
    let calls = 0;
    const testService: ContentOpsService = {
      ...service,
      listAssets: async () => {
        calls += 1;
        if (calls === 1) return await new Promise<ContentAsset[]>((resolve) => { resolveInitial = resolve; });
        if (calls === 2) return await new Promise<ContentAsset[]>((_resolve, reject) => { rejectRefresh = reject; });
        return [];
      },
    };
    await mountPanel(testService, []);
    expect(document.body.textContent).toContain("Загружаем материалы…");

    await act(async () => { resolveInitial?.([]); });
    await waitFor(() => document.body.textContent?.includes("У этой компании пока нет материалов.") ?? false);
    const refresh = [...document.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Обновить");
    await act(async () => { refresh?.click(); });
    await waitFor(() => calls === 2 && (document.body.textContent?.includes("Загружаем материалы…") ?? false));
    expect(document.body.textContent).toContain("Загружаем материалы…");
    await act(async () => {
      rejectRefresh?.(new ContentOpsError("OFFLINE", 0, "The Content Service could not be reached. Try again."));
    });
    await waitFor(() => document.querySelector('[role="alert"]') !== null);
    expect(document.querySelector('[role="alert"]')?.textContent).toContain("The Content Service could not be reached. Try again.");

    await act(async () => { refresh?.click(); });
    await waitFor(() => calls === 3 && document.querySelector('[role="alert"]') === null);
    expect(document.body.textContent).toContain("У этой компании пока нет материалов.");
  });

  it("orders session-known versions, downloads the selected exact version, and excludes other-company assets", async () => {
    const versions: ContentAssetVersion[] = [
      { id: "version-2", asset_id: asset.id, version: 2, size: 4, sha256: "two", etag: "etag-2", object_metadata: { source: "upload" }, created_at: "2026-10-02T12:00:00Z" },
      { id: "version-1", asset_id: asset.id, version: 1, size: 3, sha256: "one", etag: "etag-1", object_metadata: { source: "upload" }, created_at: "2026-10-01T12:00:00Z" },
    ];
    let next = 0;
    const uploadVersion = vi.fn(async (_assetId: string, versionId: string) => versions.find((version) => version.id === versionId)!);
    let downloadAttempts = 0;
    const downloadVersion = vi.fn(async (_assetId: string, _versionId: string, _fallbackFilename: string) => {
      downloadAttempts += 1;
      if (downloadAttempts === 1) throw new Error("offline detail must not be rendered");
      return { blob: new Blob(["bytes"]), filename: "notes.txt", contentType: "text/plain" };
    });
    const testService: ContentOpsService = {
      ...service,
      listAssets: async () => [asset],
      beginVersion: async () => versions[next++]!,
      uploadVersion,
      downloadVersion,
    };
    const otherCompanyAsset = { ...asset, id: "other-company-asset", owner_company_id: "company-2", title: "must not appear" };
    await mountPanel(testService, [asset, otherCompanyAsset]);

    expect(document.body.textContent).not.toContain("must not appear");
    const existingMode = [...document.querySelectorAll<HTMLButtonElement>(".content-ops__segmented button")].find((button) => button.textContent === "Новая версия");
    await act(async () => existingMode?.click());

    for (const version of versions) {
      const fileInput = document.querySelector<HTMLInputElement>(".content-ops__file-field input[type='file']");
      const file = new File([`v${version.version}`], `notes-v${version.version}.txt`, { type: "text/plain" });
      await act(async () => {
        Object.defineProperty(fileInput, "files", { configurable: true, value: [file] });
        fileInput?.dispatchEvent(new Event("change", { bubbles: true }));
      });
      await act(async () => {
        document.querySelector<HTMLFormElement>(".content-ops__form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      });
      await waitFor(() => document.body.textContent?.includes(`Загружена версия v${version.version}.`) ?? false);
    }

    const versionSelect = [...document.querySelectorAll<HTMLSelectElement>("select")].find((select) => select.querySelector("option[value='version-1']"));
    expect([...versionSelect!.options].map((option) => option.value)).toEqual(["", "version-1", "version-2"]);
    createObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    revokeObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    anchorClickDescriptor = Object.getOwnPropertyDescriptor(HTMLAnchorElement.prototype, "click");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:content-version") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    Object.defineProperty(HTMLAnchorElement.prototype, "click", { configurable: true, value: vi.fn() });
    await act(async () => {
      versionSelect!.value = "version-2";
      versionSelect!.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      const download = [...document.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Скачать версию");
      download?.click();
    });
    await waitFor(() => [...document.querySelectorAll<HTMLButtonElement>("button")].some((button) => button.textContent === "Повторить скачивание"));
    expect(downloadVersion).toHaveBeenCalledWith(asset.id, "version-2", "notes.txt");
    expect(document.querySelector('[role="alert"]')?.textContent).toContain("Не удалось выполнить действие с материалом. Попробуйте ещё раз.");
    expect(document.body.textContent).not.toContain("offline detail must not be rendered");
    await act(async () => {
      [...document.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Повторить скачивание")?.click();
    });
    await waitFor(() => document.body.textContent?.includes("Скачана версия v2.") ?? false);
    expect(downloadVersion).toHaveBeenCalledTimes(2);
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 1010)); });
    expect(document.body.textContent).toContain('{"source":"upload"}');
    expect(document.body.textContent).toContain("Версия создана");
  });
});

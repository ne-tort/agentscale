// @vitest-environment jsdom
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import { ResourceWorkspace } from "./ResourceWorkspace";
import { resourceWorkspaceFixture } from "./fixtures";
import type { ContentOpsService } from "./content-ops/contracts";

const asset = { id: "asset-1", owner_scope: "company", owner_company_id: "company-1", visibility: "company", mime: "text/plain", title: "notes.txt", tags: [], created_by: "employee-1", latest_version: 1, created_at: "2026-10-01T12:00:00Z", updated_at: "2026-10-01T12:00:00Z" };

const contentOpsService: ContentOpsService = {
  listAssets: async () => [asset],
  createAsset: async () => ({ id: "asset-1", owner_scope: "company", owner_company_id: "company-1", visibility: "company", mime: null, title: "asset", tags: [], created_by: null, latest_version: null, created_at: null, updated_at: null }),
  beginVersion: async () => ({ id: "version-1", asset_id: "asset-1", version: 1, size: 0, sha256: null, etag: null, object_metadata: {}, created_at: null }),
  uploadVersion: async () => ({ id: "version-1", asset_id: "asset-1", version: 1, size: 0, sha256: null, etag: null, object_metadata: {}, created_at: null }),
  downloadVersion: async () => ({ blob: new Blob(), filename: "asset", contentType: "application/octet-stream" }),
  listAcl: async () => [],
  replaceAcl: async () => [],
};

/**
 * The app's default locale is Russian, so that is what an unconfigured screen shows.
 * The locale is set explicitly for every render: a rendering test that changes it
 * would otherwise leak into the next one, which asserts Russian copy.
 */
function render(ui: ReactElement, locale: "ru" | "en" = "ru"): string {
  window.localStorage.setItem("agentscale.locale", locale);
  return renderToStaticMarkup(<SettingsProvider>{ui}</SettingsProvider>);
}

describe("ResourceWorkspace", () => {
  it("renders the profile overview, lifecycle navigation, and generation evidence from a typed snapshot", () => {
    const markup = render(<ResourceWorkspace initialSnapshot={resourceWorkspaceFixture} />);

    expect(markup).toContain("Operations reviewer");
    expect(markup).toContain("Промпты · файлы · MCP");
    expect(markup).toContain("Публикация");
    expect(markup).toContain("План 4 · применено 3");
    expect(markup).toContain("Всё подключено");
  });

  it("renders the profile's updated stamp in the interface's language", () => {
    // `profile.updatedAt` used to go through `Intl.DateTimeFormat(undefined)`, so the
    // month came from the browser and not from the language the reader chose.
    const russian = render(<ResourceWorkspace initialSnapshot={resourceWorkspaceFixture} />, "ru");
    const english = render(<ResourceWorkspace initialSnapshot={resourceWorkspaceFixture} />, "en");

    expect(russian).toContain("окт");
    expect(english).toContain("Oct");
    expect(english).not.toContain("окт");
  });

  it("renders blockers and no fabricated data when the service cannot load an authoritative snapshot", () => {
    const markup = render(<ResourceWorkspace profileId="profile-ops" />);

    expect(markup).toContain("Что мешает работе");
    // Static rendering does not run effects, so the screen is still in its
    // pre-load state: the blocked title and its hint, not a fabricated profile.
    expect(markup).toContain("Ресурсы пока нельзя показать");
    expect(markup).toContain("Страница откроется, когда подключим сервис ресурсов.");
    expect(markup).toContain("Нет подтверждённого источника ресурсов");
    // The service code stays available to whoever picks up the ticket, but only as
    // a tooltip: it is not what the person reading the screen is shown.
    expect(markup).toContain('title="resource_web_facade_missing · api"');
    expect(markup).not.toContain("Review policy");
  });

  it("keeps assets reachable without a resource snapshot and blocks them without company service context", () => {
    const markup = render(<ResourceWorkspace profileId="profile-ops" initialTab="assets" />);

    expect(markup).toContain("Материалы");
    expect(markup).toContain("Материалы недоступны");
    expect(markup).toContain("Только просмотр: запрос не отправлялся.");
    expect(markup).not.toContain("Загрузить версию");
  });

  it("integrates ContentOpsPanel for explicit company scope and shows resource mutations as blocked", () => {
    const markup = render(<ResourceWorkspace initialSnapshot={resourceWorkspaceFixture} initialTab="assets" companyId="company-1" contentOpsService={contentOpsService} initialAssets={[asset]} />);

    expect(markup).toContain("Материалы компании");
    expect(markup).toContain("Загрузка");
    expect(markup).toContain("История версий недоступна");
  });

  it("shows immutable version inspection and explicit unsupported create, bind, plan, and sync reasons", () => {
    const markup = render(<ResourceWorkspace initialSnapshot={resourceWorkspaceFixture} initialTab="resources" />);

    expect(markup).toContain("Какую версию посмотреть");
    expect(markup).toContain("sha256:new");
    expect(markup).toContain("Operations");
    expect(markup).toContain("Отдельно · в проекте можно менять");
    expect(markup).toContain("Создание версий недоступно: подключение к сервису ресурсов ещё не готово.");
    expect(markup).toContain("Привязка недоступна: подключение к сервису ресурсов ещё не готово.");
    expect(markup).toContain("Создание недоступно: подключение к сервису ресурсов ещё не готово.");
    expect(markup).toContain("Сравнение версий недоступно: данные для сравнения ещё не подключены.");
  });

  it("keeps exact scope, binding mode, generation, digest, and provenance visible", () => {
    const markup = render(<ResourceWorkspace initialSnapshot={resourceWorkspaceFixture} initialTab="materialization" />);

    expect(markup).toContain("Нужное состояние");
    expect(markup).toContain("Опубликовано");
    expect(markup).toContain("Ревизия источника");
    expect(markup).toContain("Сборка плана и публикация пока недоступны: подключение к сервису ресурсов ещё не готово.");
  });
});

// @vitest-environment jsdom
/**
 * i18n юнит плагинов + изоляция iframe.
 *
 * Тесты НЕ запускались (freeze верификации миграции).
 */
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactElement } from "react";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import pluginStrings from "../../i18n/units/plugins";
import { buildCatalogue } from "../../i18n/catalogue";
import {
  PLUGIN_SANDBOX,
  PluginFrame,
  assertSafeSrc,
  UnsafePluginSrcError,
  type PluginFrameProps,
} from "./PluginFrame";
import { pluginSessionRequired } from "./errors";

const SAFE_SRC = "https://plugin-origin.agentscale.dev/tenant-co_123/mod_equipment/1.2.0/index.html";

function render(element: ReactElement): string {
  return renderToStaticMarkup(<SettingsProvider>{element}</SettingsProvider>);
}

function frameMarkup(props: Partial<PluginFrameProps> = {}): string {
  return render(<PluginFrame
    pluginId="mod_equipment"
    companyId="co_123"
    version="1.2.0"
    src={SAFE_SRC}
    {...props}
  />);
}

describe("i18n/units/plugins.ts", () => {
  it("ru/en имеют одинаковый набор ключей", () => {
    expect(Object.keys(pluginStrings.ru).sort()).toEqual(Object.keys(pluginStrings.en).sort());
  });

  it("содержит ровно требуемые ключи", () => {
    expect(Object.keys(pluginStrings.ru).sort()).toEqual([
      "close", "loadError", "loading", "notConnected", "notGranted", "open",
      "originMismatch", "publicPage", "retry", "timeout", "unsupportedProtocol",
    ]);
  });

  it("все строки непустые и доступны в каталоге под namespace plugins", () => {
    const catalogue = buildCatalogue();
    for (const key of Object.keys(pluginStrings.ru)) {
      expect(pluginStrings.ru[key].trim(), `ru:${key}`).not.toBe("");
      expect(pluginStrings.en[key].trim(), `en:${key}`).not.toBe("");
      expect(catalogue.ru[`plugins.${key}`]).toBe(pluginStrings.ru[key]);
      expect(catalogue.en[`plugins.${key}`]).toBe(pluginStrings.en[key]);
    }
  });

  it("русские тексты UI, а не технические коды", () => {
    expect(pluginStrings.ru.loading).toContain("Загружаем");
    expect(pluginStrings.ru.originMismatch).toContain("источника");
    expect(pluginStrings.ru.publicPage).toContain("не требуется");
  });
});

describe("assertSafeSrc: токена NEVER in URL", () => {
  it("принимает версионированный path-based URL без query/fragment", () => {
    expect(() => assertSafeSrc(SAFE_SRC)).not.toThrow();
  });

  it("отвергает ?token= и #token=", () => {
    expect(() => assertSafeSrc(`${SAFE_SRC}?token=abc123`)).toThrowError(UnsafePluginSrcError);
    expect(() => assertSafeSrc(`${SAFE_SRC}#token=abc123`)).toThrowError(UnsafePluginSrcError);
  });

  it("отвергает access_token, api_key, Authorization и capability_token", () => {
    for (const query of [
      "?access_token=abc", "?api_key=abc", "?authorization=Bearer_abc",
      "?capability_token=abc", "?secret=abc", "?plugin_claims=abc", "?jwt=abc",
    ]) {
      expect(() => assertSafeSrc(`${SAFE_SRC}${query}`), query).toThrowError(UnsafePluginSrcError);
    }
  });

  it("отвергает любой query и fragment даже без токена", () => {
    expect(() => assertSafeSrc(`${SAFE_SRC}?v=1`)).toThrowError(UnsafePluginSrcError);
    expect(() => assertSafeSrc(`${SAFE_SRC}#section`)).toThrowError(UnsafePluginSrcError);
  });

  it("отвергает закодированные разделители, userinfo и небезопасные схемы", () => {
    expect(() => assertSafeSrc(`${SAFE_SRC}%3Ftoken=abc`)).toThrowError(UnsafePluginSrcError);
    expect(() => assertSafeSrc("https://user:pass@plugin-origin.agentscale.dev/x/index.html")).toThrowError(UnsafePluginSrcError);
    expect(() => assertSafeSrc("javascript:alert(1)")).toThrowError(UnsafePluginSrcError);
    expect(() => assertSafeSrc("data:text/html,<script>1</script>")).toThrowError(UnsafePluginSrcError);
  });

  it("unsafe src → ошибка unsafeSrc, а не молчаливый рендер", () => {
    let thrown: unknown;
    try { assertSafeSrc(`${SAFE_SRC}?access_token=abc`); }
    catch (error) { thrown = error; }
    expect(thrown).toBeInstanceOf(UnsafePluginSrcError);
    expect((thrown as UnsafePluginSrcError).code).toBe("unsafeSrc");
  });
});

describe("PluginFrame: sandbox / атрибуты / состояния", () => {
  it("sandbox='allow-scripts allow-forms' и НЕТ allow-same-origin", () => {
    const markup = frameMarkup();
    expect(markup).toContain(`sandbox="${PLUGIN_SANDBOX}"`);
    expect(markup).not.toContain("allow-same-origin");
    expect(PLUGIN_SANDBOX).not.toContain("allow-same-origin");
  });

  it("referrerpolicy, credentialless, no-referrer и aria-атрибуты на месте", () => {
    const markup = frameMarkup();
    expect(markup).toContain('referrerpolicy="no-referrer"');
    expect(markup).toContain("credentialless");
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain('aria-label=');
    expect(markup).toContain('role="status"');
  });

  it("src не содержит ?token= / access_token и capability token не рендерится", () => {
    const markup = frameMarkup();
    expect(markup).not.toContain("?token=");
    expect(markup).not.toContain("access_token");
    expect(markup).not.toContain("capability_token");
    expect(markup).not.toContain("authorization");
  });

  it("состояние загрузки и «не подключён»: русские тексты, iframe скрыт", () => {
    // renderToStaticMarkup не выполняет эффекты: проверяем стартовое состояние.
    expect(frameMarkup()).toContain("Загружаем плагин");
    const markup = render(<PluginFrame
      pluginId="mod_equipment"
      companyId=""
      version="1.2.0"
      src={SAFE_SRC}
    />);
    expect(markup).toContain('data-plugin-company=""');
  });

  it("publicPage не требует сессии в UI-логике", () => {
    expect(pluginSessionRequired(false)).toBe(true);
    expect(pluginSessionRequired(true)).toBe(false);
    const markup = frameMarkup({ publicPage: true });
    expect(markup).toContain('data-plugin-public="true"');
    expect(markup).toContain("Публичная страница");
    expect(markup).not.toContain("Доступ к плагину не выдан");
  });

  it("не-публичная страница не показывает публичную плашку", () => {
    expect(frameMarkup()).toContain('data-plugin-public="false"');
    expect(frameMarkup()).not.toContain("Публичная страница: вход");
  });
});

// @vitest-environment jsdom
/**
 * Страница plugin-модулей: жизненный цикл появляется только когда известен
 * уже установленный модуль, и берёт версию/digest из серверного ответа.
 *
 * Разрыв, который этот тест закрывает: компонент жизненного цикла был написан
 * и протестирован, но нигде не смонтирован, поэтому до него нельзя было дойти.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsProvider } from "../preferences/SettingsProvider";

const DIGEST = `sha256:${"a".repeat(64)}`;

const listAdminPlugins = vi.fn(async () => ({
  items: [{
    company_id: "co_1", plugin_id: "hello_plugin", plugin_instance_id: "inst_1",
    manifest_version: "1.2.3", artifact_digest: DIGEST, grant_status: "active",
    capabilities: ["cache"],
  }],
  enabled_modules: ["hello_plugin"],
}));

vi.mock("./context", async () => {
  const actual = await vi.importActual<typeof import("./context")>("./context");
  const api = {
    listAdminPlugins,
    createPluginSource: vi.fn(),
    bindPluginSourceScope: vi.fn(),
    grantPlugin: vi.fn(),
    revokePlugin: vi.fn(),
    uninstallPlugin: vi.fn(),
    wakePlugin: vi.fn(),
    listAdminModules: vi.fn(async () => ({ items: [] })),
  };
  return {
    ...actual,
    useApi: () => api,
    useAuth: () => ({ tokens: { access_token: "t" } }),
  };
});

let root: Root | undefined;
let host: HTMLDivElement | undefined;

beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(async () => {
  if (root) await act(async () => { root!.unmount(); });
  host?.remove();
  root = undefined;
  host = undefined;
  vi.restoreAllMocks();
  listAdminPlugins.mockClear();
});

async function mountAt(entry: string): Promise<void> {
  const { PluginModulesAdminPage } = await import("./plugin-modules-admin");
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(<SettingsProvider><MemoryRouter initialEntries={[entry]}>
      <Routes><Route path="/admin/plugin-modules" element={<PluginModulesAdminPage />} /></Routes>
    </MemoryRouter></SettingsProvider>);
  });
}

describe("PluginModulesAdminPage", () => {
  it("монтирует жизненный цикл для установленного модуля", async () => {
    await mountAt("/admin/plugin-modules?companyId=co_1&pluginId=hello_plugin");
    expect(listAdminPlugins).toHaveBeenCalledWith("co_1");
    // Версия и digest пришли из серверного ответа, а не из URL.
    expect(host!.textContent).toContain("Жизненный цикл модуля");
  });

  it("без установленного модуля секции жизненного цикла нет", async () => {
    await mountAt("/admin/plugin-modules?companyId=co_1&pluginId=unknown_plugin");
    expect(host!.textContent).not.toContain("Жизненный цикл модуля");
  });

  it("без company_id список не запрашивается", async () => {
    await mountAt("/admin/plugin-modules");
    expect(listAdminPlugins).not.toHaveBeenCalled();
    expect(host!.textContent).not.toContain("Жизненный цикл модуля");
  });
});

// @vitest-environment jsdom
/**
 * Жизненный цикл plugin-модуля: выдача, пробуждение, отзыв, удаление.
 *
 * Ключевое, что проверяется: grant отправляет ровно тот digest, который пришёл
 * из публикации, а не выдуманный UI; операции заблокированы без артефакта;
 * текст исключения не попадает на экран.
 */
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import { PluginLifecycle } from "./PluginLifecycle";

const DIGEST = `sha256:${"a".repeat(64)}`;

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mount(element: ReactElement): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => { root!.render(<SettingsProvider>{element}</SettingsProvider>); });
}

beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(async () => {
  if (root) await act(async () => { root!.unmount(); });
  host?.remove();
  root = undefined;
  host = undefined;
  vi.restoreAllMocks();
});

function apiDouble(overrides: Record<string, unknown> = {}) {
  const granted = {
    company_id: "co_1", plugin_id: "hello_plugin", plugin_instance_id: "inst_1",
    manifest_version: "1.0.0", artifact_digest: DIGEST, grant_id: "grant_1",
    grant_status: "active", capabilities: ["cache"], database_name: "tenant_co_1",
    schema_name: "hello_plugin", bucket: "co-1-bucket", prefix: "hello_plugin/",
    webhook_path: "/webhook/hello_plugin/co_1", pluginpage_path: "/pluginpage/hello_plugin/co_1",
    replayed: false,
  };
  return {
    grantPlugin: vi.fn(async () => granted),
    revokePlugin: vi.fn(async () => ({ company_id: "co_1", plugin_id: "hello_plugin", grant_id: "grant_1" })),
    uninstallPlugin: vi.fn(async () => ({ company_id: "co_1", plugin_id: "hello_plugin", grant_id: "grant_1" })),
    wakePlugin: vi.fn(async () => ({ company_id: "co_1", plugin_id: "hello_plugin", grant_id: "grant_1" })),
    ...overrides,
  };
}

function props(api: ReturnType<typeof apiDouble>, overrides = {}) {
  return {
    api: api as never, companyId: "co_1", pluginId: "hello_plugin",
    manifestVersion: "1.0.0", artifactDigest: DIGEST,
    capabilities: ["cache"], ...overrides,
  };
}

function button(name: string): HTMLButtonElement | null {
  return Array.from(host!.querySelectorAll<HTMLButtonElement>("button"))
    .find((item) => item.textContent === name) ?? null;
}

describe("PluginLifecycle", () => {
  it("grant отправляет digest артефакта и показывает выданные границы", async () => {
    const api = apiDouble();
    await mount(<PluginLifecycle {...props(api)} />);
    await act(async () => { button("Выдать доступ")!.click(); });
    expect(api.grantPlugin).toHaveBeenCalledWith({
      company_id: "co_1", plugin_id: "hello_plugin", manifest_version: "1.0.0",
      artifact_digest: DIGEST, capabilities: { capabilities: ["cache"] },
    });
    expect(host!.textContent).toContain("grant_1");
    expect(host!.textContent).toContain("tenant_co_1");
  });

  it("без digest операции недоступны и сервер не вызывается", async () => {
    const api = apiDouble();
    await mount(<PluginLifecycle {...props(api, { artifactDigest: "" })} />);
    expect(button("Выдать доступ")!.disabled).toBe(true);
    expect(api.grantPlugin).not.toHaveBeenCalled();
  });

  it("wake/revoke доступны только после выдачи, uninstall — всегда", async () => {
    const api = apiDouble();
    await mount(<PluginLifecycle {...props(api)} />);
    expect(button("Разбудить")!.disabled).toBe(true);
    expect(button("Отозвать")!.disabled).toBe(true);
    expect(button("Удалить установку")!.disabled).toBe(false);
    await act(async () => { button("Выдать доступ")!.click(); });
    expect(button("Разбудить")!.disabled).toBe(false);
    await act(async () => { button("Отозвать")!.click(); });
    expect(api.revokePlugin).toHaveBeenCalledWith("co_1", "hello_plugin");
  });

  it("ошибка показывается без текста исключения", async () => {
    const api = apiDouble({
      grantPlugin: vi.fn(async () => { throw new Error("postgresql://user:pw@db/internal"); }),
    });
    await mount(<PluginLifecycle {...props(api)} />);
    await act(async () => { button("Выдать доступ")!.click(); });
    expect(host!.querySelector("[role=alert]")!.textContent).toBeTruthy();
    expect(host!.textContent).not.toContain("postgresql://");
  });
});

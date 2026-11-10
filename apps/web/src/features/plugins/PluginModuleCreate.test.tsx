// @vitest-environment jsdom
/**
 * Форма создания plugin-модуля: серверный источник + точный scope.
 *
 * Проверяем то, что раньше отсутствовало в UI вовсе: единственная точка
 * входа, которая действительно создаёт plugin-модуль, и отказ от неполного
 * scope без обращения к серверу.
 */
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import { PluginModuleCreate, buildSourceManifest } from "./PluginModuleCreate";

const COMPANY = "co_123";
const CABINET = "cab_1";
const PROJECT = "pr_1";

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mount(element: ReactElement): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => { root!.render(<SettingsProvider>{element}</SettingsProvider>); });
}

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(async () => {
  if (root) await act(async () => { root!.unmount(); });
  host?.remove();
  root = undefined;
  host = undefined;
  vi.restoreAllMocks();
});

function apiDouble(overrides: Record<string, unknown> = {}) {
  const created = {
    id: "src_1", company_id: COMPANY, plugin_id: "hello_plugin",
    plugin_instance_id: "inst_x", cabinet_id: CABINET, project_id: PROJECT,
    provider: "github_app" as const, repo_id: 1, full_name: "acme/hello-plugin",
    private: true, default_branch: "main", expected_ref: "refs/heads/main",
    trusted_workflow_ref: "acme/x/.github/workflows/sign.yml@" + "a".repeat(40),
    expected_signer_identity: "https://github.com/ne-tort/agentscale/x",
    status: "ready" as const, created_at: "2026-11-10T00:00:00Z",
    updated_at: "2026-11-10T00:00:00Z",
  };
  return {
    createPluginSource: vi.fn(async () => created),
    bindPluginSourceScope: vi.fn(async () => created),
    ...overrides,
  };
}

describe("buildSourceManifest", () => {
  it("собирает только то, что ввёл автор, и не выдумывает токены", () => {
    const manifest = buildSourceManifest({ pluginId: "hello_plugin", version: "2.0.0", backendPort: 8080 });
    expect(manifest.plugin_id).toBe("hello_plugin");
    expect(manifest.version).toBe("2.0.0");
    expect(manifest.protocol).toBe("plugin-rpc/1");
    // Ни credentials, ни digest, ни подписи в манифесте UI быть не должно.
    expect(JSON.stringify(manifest)).not.toMatch(/token|secret|password|digest|signature/i);
  });
});

describe("PluginModuleCreate", () => {
  it("создаёт источник и сразу привязывает scope", async () => {
    const api = apiDouble();
    await mount(<PluginModuleCreate
      api={api as never} companyId={COMPANY} cabinetId={CABINET} projectId={PROJECT}
    />);
    const input = host!.querySelector<HTMLInputElement>("#plugin-create-id")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "hello_plugin");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      host!.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(api.createPluginSource).toHaveBeenCalledTimes(1);
    expect(api.bindPluginSourceScope).toHaveBeenCalledWith(COMPANY, "hello_plugin", CABINET, PROJECT);
    expect(host!.textContent).toContain("acme/hello-plugin");
  });

  it("без точного scope кнопка заблокирована и сервер не вызывается", async () => {
    const api = apiDouble();
    await mount(<PluginModuleCreate
      api={api as never} companyId={COMPANY} cabinetId="" projectId=""
    />);
    const button = host!.querySelector<HTMLButtonElement>("form button")!;
    expect(button.disabled).toBe(true);
    expect(api.createPluginSource).not.toHaveBeenCalled();
  });

  it("ошибка сервера показывается без текста исключения", async () => {
    const api = apiDouble({
      createPluginSource: vi.fn(async () => { throw new Error("postgresql://user:pw@db/internal"); }),
    });
    await mount(<PluginModuleCreate
      api={api as never} companyId={COMPANY} cabinetId={CABINET} projectId={PROJECT}
    />);
    const input = host!.querySelector<HTMLInputElement>("#plugin-create-id")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "hello_plugin");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      host!.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    const alert = host!.querySelector("[role=alert]")!;
    expect(alert.textContent).toBeTruthy();
    expect(host!.textContent).not.toContain("postgresql://");
  });
});

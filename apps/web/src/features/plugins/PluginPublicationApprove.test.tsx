// @vitest-environment jsdom
/**
 * Согласование публикации: разрешение привязано к digest и ничего не выдаёт.
 *
 * Ключевое: повторный approve того же digest помечается как повтор, а не как
 * новое разрешение, и без digest форма недоступна.
 */
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../../api/types";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import {
  PluginPublicationApprove,
  type PluginPublicationApproveProps,
} from "./PluginPublicationApprove";

const DIGEST = `sha256:${"b".repeat(64)}`;

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

function approval(created: boolean, replayed: boolean) {
  return {
    approval_id: "appr_1", company_id: "co_1", plugin_id: "hello_plugin",
    manifest_version: "1.0.0", artifact_digest: DIGEST, capabilities: ["cache"],
    created_by: "admin", created_at: "2026-11-10T00:00:00Z",
    consumed_at: null, created, replayed,
  };
}

type ApproveApi = Pick<ApiClient, "approvePluginPublication">;

function props(api: ApproveApi, overrides = {}): PluginPublicationApproveProps {
  return {
    api, companyId: "co_1", pluginId: "hello_plugin",
    manifestVersion: "1.0.0", artifactDigest: DIGEST,
    capabilities: ["cache"], ...overrides,
  };
}

async function submit(): Promise<void> {
  await act(async () => {
    host!.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
}

describe("PluginPublicationApprove", () => {
  it("отправляет ровно digest и capabilities, без политики", async () => {
    const approve = vi.fn(async (_body: unknown) => approval(true, false));
    await mount(<PluginPublicationApprove {...props({ approvePluginPublication: approve })} />);
    await submit();
    expect(approve).toHaveBeenCalledTimes(1);
    const payload = approve.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload).toEqual({
      company_id: "co_1", plugin_id: "hello_plugin", manifest_version: "1.0.0",
      artifact_digest: DIGEST, capabilities: { capabilities: ["cache"] },
    });
    // Политика egress и квоты задаются сервером, не клиентом.
    expect(JSON.stringify(payload)).not.toMatch(/egress|quota/i);
  });

  it("без digest кнопка заблокирована и сервер не вызывается", async () => {
    const approve = vi.fn();
    await mount(<PluginPublicationApprove {...props({ approvePluginPublication: approve }, { artifactDigest: "" })} />);
    const button = host!.querySelector<HTMLButtonElement>("form button")!;
    expect(button.disabled).toBe(true);
    expect(approve).not.toHaveBeenCalled();
  });

  it("повторный approve показывается как повтор, а не как новое разрешение", async () => {
    const approve = vi.fn(async () => approval(false, true));
    await mount(<PluginPublicationApprove {...props({ approvePluginPublication: approve })} />);
    await submit();
    expect(host!.textContent).toContain("уже была согласована");
  });

  it("ошибка показывается без текста исключения", async () => {
    const approve = vi.fn(async () => { throw new Error("postgresql://user:pw@db/internal"); });
    await mount(<PluginPublicationApprove {...props({ approvePluginPublication: approve })} />);
    await submit();
    expect(host!.querySelector("[role=alert]")!.textContent).toBeTruthy();
    expect(host!.textContent).not.toContain("postgresql://");
  });
});

import type { Frame, Page, Request } from "@playwright/test";
import { expect } from "@playwright/test";

import { PLUGIN_ACCEPTANCE } from "./environment";
import { staticEntryUrl } from "./http";

const SENSITIVE_QUERY = /(?:^|[?&#])[^=&#]*(?:token|secret|auth|bearer|credential|claims|password|api[_-]?key)[^=&#]*=/i;

export interface NetworkRecord {
  readonly method: string;
  readonly origin: string;
  readonly pathname: string;
  /** Only the explicitly non-credential RPC/bootstrap scope keys are retained. */
  readonly scopeQuery: Readonly<Record<string, string | null>>;
  readonly bodyScope: Readonly<Record<string, string | null>>;
}

export interface ObservedMessage {
  readonly direction: "host-to-plugin" | "plugin-to-host";
  readonly origin: string;
  readonly sourceMatches: boolean;
  readonly at: number;
  readonly data: Record<string, unknown>;
}

export interface ListenerState {
  readonly active: number;
  readonly adds: number;
  readonly removes: number;
}

export interface BrowserAcceptanceProbe {
  readonly records: readonly NetworkRecord[];
  readonly violations: readonly string[];
  backendCalls(pluginId?: string, companyId?: string): NetworkRecord[];
  detachedPluginFrames(): number;
  assertClean(): Promise<void>;
}

function safeRecord(request: Request): NetworkRecord {
  const url = new URL(request.url());
  let body: Record<string, unknown> = {};
  if (request.method() === "POST" && url.pathname.includes("/pluginpage/")) {
    try { body = request.postDataJSON() as Record<string, unknown>; } catch { body = {}; }
  }
  return {
    method: request.method(),
    origin: url.origin,
    pathname: url.pathname,
    scopeQuery: {
      company_id: url.searchParams.get("company_id"),
      cabinet_id: url.searchParams.get("cabinet_id"),
      project_id: url.searchParams.get("project_id"),
      limit: url.searchParams.get("limit"),
    },
    bodyScope: {
      company_id: typeof body.company_id === "string" ? body.company_id : null,
      cabinet_id: typeof body.cabinet_id === "string" ? body.cabinet_id : null,
      project_id: typeof body.project_id === "string" ? body.project_id : null,
    },
  };
}

/**
 * Passive in-browser observer. It neither suppresses nor synthesizes normal
 * traffic and never changes the application DOM, storage or message delivery.
 * Negative tests dispatch their own isolated MessageEvents explicitly.
 */
export async function installBrowserAcceptance(page: Page): Promise<BrowserAcceptanceProbe> {
  const records: NetworkRecord[] = [];
  const violations: string[] = [];
  const headerInspections = new Set<Promise<void>>();
  let detachedPluginFrames = 0;
  page.on("framedetached", (frame) => {
    if (frame.url().startsWith(PLUGIN_ACCEPTANCE.staticOrigin)) detachedPluginFrames += 1;
  });
  const allowedOrigins = new Set([
    PLUGIN_ACCEPTANCE.webOrigin,
    PLUGIN_ACCEPTANCE.gatewayOrigin,
    PLUGIN_ACCEPTANCE.staticOrigin,
  ]);

  page.on("response", (response) => {
    const url = new URL(response.url());
    if ((url.pathname.startsWith("/api/v1/") || url.pathname.startsWith("/pluginpage/"))
      && response.status() >= 300 && response.status() < 400) {
      violations.push("redirect detected on API/Gateway browser flow");
    }
    if (SENSITIVE_QUERY.test(response.headers()["location"] ?? "")) {
      violations.push("credential-like redirect location detected");
    }
  });

  page.on("request", (request) => {
    const record = safeRecord(request);
    records.push(record);
    if (SENSITIVE_QUERY.test(request.url())) violations.push("credential-like URL query detected");
    if (!allowedOrigins.has(record.origin)) violations.push("unexpected browser origin detected");
    const inspection = (async () => {
      try {
        // request.headers() omits security-related Cookie values. allHeaders()
        // is required for real outgoing credential assertions; values stay hidden.
        const headers = await request.allHeaders();
        if (record.origin === PLUGIN_ACCEPTANCE.staticOrigin
          && (headers.authorization || headers.cookie || headers["x-plugin-capability-token"])) {
          violations.push("credential header detected on direct static request");
        }
        if ((record.pathname.includes(`/pluginpage/${encodeURIComponent(PLUGIN_ACCEPTANCE.publicPluginId)}/`)
          || record.pathname.startsWith("/api/v1/public/plugins/"))
          && (headers.authorization || headers.cookie || headers["x-plugin-capability-token"])) {
          violations.push("credential header detected on anonymous public request");
        }
      } catch {
        violations.push("outgoing security header inspection failed");
      }
    })();
    headerInspections.add(inspection);
    void inspection.finally(() => headerInspections.delete(inspection));
  });

  await page.addInitScript(({ shellOrigin }) => {
    const globalWindow = window as unknown as {
      __agentscalePluginAcceptanceMessages?: () => ObservedMessage[];
      __agentscalePluginAcceptanceListenerState?: () => ListenerState;
    };
    const messages: ObservedMessage[] = [];
    let truncated = false;

    function dataOf(value: unknown): Record<string, unknown> | undefined {
      if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
      try {
        return JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
      } catch {
        return undefined;
      }
    }

    function record(
      event: MessageEvent,
      direction: ObservedMessage["direction"],
      sourceMatches: boolean,
    ): void {
      const data = dataOf(event.data);
      if (!data || messages.length >= 256) {
        truncated = true;
        return;
      }
      messages.push({ direction, origin: String(event.origin), sourceMatches, at: Date.now(), data });
    }

    if (window.parent !== window) {
      window.addEventListener("message", (event) => {
        if (event.source === window.parent && event.origin === shellOrigin) {
          record(event, "host-to-plugin", true);
        }
      }, true);
    } else {
      window.addEventListener("message", (event) => {
        const current = Array.from(document.querySelectorAll("iframe[data-plugin-id]"));
        const sourceMatches = current.length === 1
          && current[0] instanceof HTMLIFrameElement
          && event.source === current[0].contentWindow;
        if (sourceMatches) record(event, "plugin-to-host", true);
      }, true);
    }

    globalWindow.__agentscalePluginAcceptanceMessages = () => {
      if (truncated) throw new Error("bounded plugin message observer overflowed");
      return messages.map((entry) => ({ ...entry, data: { ...entry.data } }));
    };
  }, { shellOrigin: PLUGIN_ACCEPTANCE.shellOrigin });

  await page.addInitScript(() => {
    const globalWindow = window as unknown as {
      __agentscalePluginAcceptanceListenerState?: () => ListenerState;
      __agentscalePluginAcceptanceCaptureSource?: () => number;
      __agentscalePluginAcceptanceDispatchStored?: (input: {
        readonly index: number;
        readonly data: Record<string, unknown>;
        readonly origin: string;
      }) => void;
    };
    const activeByCapture = new Map();
    const nativeAdd = window.addEventListener.bind(window);
    const nativeRemove = window.removeEventListener.bind(window);
    let active = 0;
    let adds = 0;
    let removes = 0;
    window.addEventListener = function addCounted(
      type: string,
      listener: EventListenerOrEventListenerObject | null,
      options?: boolean | AddEventListenerOptions,
    ): void {
      if (type === "message" && listener) {
        const capture = typeof options === "boolean" ? options : Boolean(options?.capture);
        const captures = activeByCapture.get(listener) ?? new Set();
        if (!captures.has(capture)) {
          captures.add(capture);
          activeByCapture.set(listener, captures);
          active += 1;
          adds += 1;
        }
      }
      nativeAdd(type, listener as EventListener, options as boolean | AddEventListenerOptions);
    } as typeof window.addEventListener;
    window.removeEventListener = function removeCounted(
      type: string,
      listener: EventListenerOrEventListenerObject | null,
      options?: boolean | EventListenerOptions,
    ): void {
      if (type === "message" && listener) {
        const capture = typeof options === "boolean" ? options : Boolean(options?.capture);
        const captures = activeByCapture.get(listener);
        if (captures?.delete(capture)) {
          active -= 1;
          removes += 1;
        }
      }
      nativeRemove(type, listener as EventListener, options as boolean | EventListenerOptions);
    } as typeof window.removeEventListener;
    globalWindow.__agentscalePluginAcceptanceListenerState = () => ({ active, adds, removes });

    // A stale source is used only by the natural-expiry test after wall-clock
    // expiry. It is never treated as a still-open channel for a typed denial.
    const storedPluginSources: WindowProxy[] = [];
    globalWindow.__agentscalePluginAcceptanceCaptureSource = () => {
      const iframe = document.querySelector("iframe[data-plugin-id]");
      if (!(iframe instanceof HTMLIFrameElement) || !iframe.contentWindow) {
        throw new Error("current plugin source unavailable for natural-expiry probe");
      }
      storedPluginSources.push(iframe.contentWindow);
      return storedPluginSources.length - 1;
    };
    globalWindow.__agentscalePluginAcceptanceDispatchStored = ({ index, data, origin }) => {
      const source = storedPluginSources[index];
      if (!source) throw new Error("stored plugin source unavailable");
      window.dispatchEvent(new MessageEvent("message", { data, origin, source }));
    };
  });

  // Only URL forwarding is installed when web and gateway origins differ. Every
  // response still comes from the configured real HTTPS service; nothing is
  // fulfilled from a fixture or jsdom. No redirects are followed implicitly.
  await page.route("**/*", async (route) => {
    const request = route.request();
    const incoming = new URL(request.url());
    if (SENSITIVE_QUERY.test(request.url())) {
      violations.push("credential-like URL query blocked before forwarding");
      await route.abort("blockedbyclient");
      return;
    }
    const forwardsToGateway = incoming.origin === PLUGIN_ACCEPTANCE.webOrigin
      && incoming.origin !== PLUGIN_ACCEPTANCE.gatewayOrigin
      && (incoming.pathname.startsWith("/api/v1/") || incoming.pathname.startsWith("/pluginpage/"));
    if (!forwardsToGateway) {
      await route.continue();
      return;
    }
    const target = `${PLUGIN_ACCEPTANCE.gatewayOrigin}${incoming.pathname}${incoming.search}`;
    const response = await route.fetch({
      url: target,
      timeout: PLUGIN_ACCEPTANCE.httpTimeoutMs,
      maxRedirects: 0,
    });
    if (response.status() >= 300 && response.status() < 400) {
      violations.push("redirect returned by real Gateway forwarding");
      await route.abort("blockedbyclient");
      return;
    }
    await route.fulfill({ response });
  });

  return {
    records,
    violations,
    backendCalls(pluginId = PLUGIN_ACCEPTANCE.pluginId, companyId) {
      const prefix = `/pluginpage/${encodeURIComponent(pluginId)}/`;
      return records.filter((record) => {
        if (!record.pathname.startsWith(prefix) || !record.pathname.includes("/api/")) return false;
        return companyId === undefined
          || record.pathname.startsWith(`${prefix}${encodeURIComponent(companyId)}/api/`);
      });
    },
    detachedPluginFrames() {
      return detachedPluginFrames;
    },
    async assertClean() {
      while (headerInspections.size > 0) await Promise.all([...headerInspections]);
      expect(violations, "browser network must have no credential URLs or unexpected origins").toEqual([]);
    },
  };
}

export async function signIn(page: Page): Promise<void> {
  await page.goto(`${PLUGIN_ACCEPTANCE.webOrigin}/login`);
  await page.getByLabel("Email or username").fill(PLUGIN_ACCEPTANCE.memberUsername);
  await page.getByLabel("Password").fill(PLUGIN_ACCEPTANCE.memberPassword);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible({ timeout: 30_000 });
}

export function privatePageUrl(companyId = PLUGIN_ACCEPTANCE.companyId): string {
  return `${PLUGIN_ACCEPTANCE.webOrigin}/plugins/${encodeURIComponent(PLUGIN_ACCEPTANCE.pluginId)}/${encodeURIComponent(companyId)}`;
}

export function publicPageUrl(): string {
  return `${PLUGIN_ACCEPTANCE.webOrigin}/public/plugins/${encodeURIComponent(PLUGIN_ACCEPTANCE.publicCompanyId)}/${encodeURIComponent(PLUGIN_ACCEPTANCE.publicPluginId)}`;
}

export async function waitPrivatePluginReady(page: Page): Promise<void> {
  await expect(page.locator('.plugin-frame[data-plugin-phase="ready"]')).toBeVisible({ timeout: 30_000 });
  await assertIframePolicy(page);
}

export async function openPrivatePlugin(page: Page, companyId = PLUGIN_ACCEPTANCE.companyId): Promise<void> {
  await page.goto(privatePageUrl(companyId));
  await waitPrivatePluginReady(page);
}

export async function openPublicPlugin(page: Page): Promise<void> {
  await page.goto(publicPageUrl());
  await expect(page.locator('.plugin-frame[data-plugin-phase="ready"]')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.plugin-frame[data-plugin-public="true"]')).toBeVisible();
  await assertIframePolicy(page, true);
}

export async function pluginIframe(page: Page): Promise<Frame> {
  const handle = await page.locator("iframe[data-plugin-id]").elementHandle();
  const frame = await handle?.contentFrame();
  if (!frame) throw new Error("real plugin iframe has no content frame");
  return frame;
}

export async function assertIframePolicy(page: Page, publicPage = false): Promise<void> {
  const iframe = page.locator("iframe[data-plugin-id]");
  await expect(iframe).toHaveAttribute("sandbox", "allow-scripts allow-forms");
  await expect(iframe).toHaveAttribute("referrerpolicy", "no-referrer");
  await expect(iframe).toHaveAttribute("credentialless", "");
  await expect(iframe).toHaveAttribute("allow", "");
  await expect(iframe).toHaveAttribute(
    "data-plugin-id",
    publicPage ? PLUGIN_ACCEPTANCE.publicPluginId : PLUGIN_ACCEPTANCE.pluginId,
  );
  await expect(iframe).toHaveAttribute(
    "data-plugin-version",
    publicPage ? PLUGIN_ACCEPTANCE.publicPluginVersion : PLUGIN_ACCEPTANCE.pluginVersion,
  );

  const src = await iframe.getAttribute("src");
  const expected = publicPage
    ? staticEntryUrl(PLUGIN_ACCEPTANCE.publicFrontendEntry, PLUGIN_ACCEPTANCE.publicFrontendBasePath)
    : staticEntryUrl(PLUGIN_ACCEPTANCE.frontendEntry, PLUGIN_ACCEPTANCE.frontendBasePath);
  expect(src).toBe(expected);
  const parsed = new URL(src ?? "");
  expect(parsed.search).toBe("");
  expect(parsed.hash).toBe("");
  expect(parsed.username).toBe("");
  expect(parsed.password).toBe("");

  const handoff = JSON.parse((await iframe.getAttribute("name")) ?? "null") as Record<string, unknown>;
  expect(handoff).toEqual({
    protocol: "agentscale-plugin-shell/1",
    plugin_shell_origin: PLUGIN_ACCEPTANCE.shellOrigin,
    plugin_id: publicPage ? PLUGIN_ACCEPTANCE.publicPluginId : PLUGIN_ACCEPTANCE.pluginId,
    manifest_version: publicPage ? PLUGIN_ACCEPTANCE.publicPluginVersion : PLUGIN_ACCEPTANCE.pluginVersion,
    artifact_digest: publicPage ? PLUGIN_ACCEPTANCE.publicArtifactDigest : PLUGIN_ACCEPTANCE.artifactDigest,
  });
}

async function topMessages(page: Page): Promise<ObservedMessage[]> {
  return page.evaluate(() => {
    const read = (window as unknown as {
      __agentscalePluginAcceptanceMessages?: () => ObservedMessage[];
    }).__agentscalePluginAcceptanceMessages;
    return read ? read() : [];
  });
}

async function frameMessages(frame: Frame): Promise<ObservedMessage[]> {
  return frame.evaluate(() => {
    const read = (window as unknown as {
      __agentscalePluginAcceptanceMessages?: () => ObservedMessage[];
    }).__agentscalePluginAcceptanceMessages;
    return read ? read() : [];
  });
}

export interface HandshakeObservation {
  readonly hostReady: ObservedMessage;
  readonly pluginReady: ObservedMessage;
  readonly grantReady: ObservedMessage;
}

export async function latestHandshake(page: Page): Promise<HandshakeObservation> {
  const frame = await pluginIframe(page);
  const outgoing = (await topMessages(page)).filter((entry) => entry.data.type === "plugin.ready");
  const incoming = await frameMessages(frame);
  const pluginReady = outgoing[outgoing.length - 1];
  if (!pluginReady) throw new Error("no real plugin.ready observed in Chromium");
  const identity = pluginReady.data;
  expect(outgoing.filter((entry) => entry.data.instance_id === identity.instance_id
    && entry.data.nonce === identity.nonce), "exactly one plugin.ready for current host.ready").toHaveLength(1);
  const hostReadyMessages = incoming.filter((entry) => entry.data.type === "host.ready"
    && entry.data.instance_id === identity.instance_id && entry.data.nonce === identity.nonce);
  const grantReadyMessages = incoming.filter((entry) => entry.data.type === "grant.ready"
    && entry.data.instance_id === identity.instance_id && entry.data.nonce === identity.nonce);
  expect(hostReadyMessages, "exactly one host.ready for current channel identity").toHaveLength(1);
  expect(grantReadyMessages, "exactly one grant.ready response for current host.ready").toHaveLength(1);
  const hostReady = hostReadyMessages[0];
  const grantReady = grantReadyMessages[0];
  expect(hostReady.at).toBeLessThanOrEqual(pluginReady.at);
  expect(pluginReady.at).toBeLessThanOrEqual(grantReady.at);
  return { hostReady, pluginReady, grantReady };
}

export async function allPluginReady(page: Page): Promise<ObservedMessage[]> {
  return (await topMessages(page)).filter((entry) => entry.data.type === "plugin.ready");
}

export async function hostRpcResponses(frame: Frame): Promise<ObservedMessage[]> {
  return (await frameMessages(frame)).filter((entry) => entry.data.type === "plugin.rpc.response");
}

export async function dispatchToHost(
  page: Page,
  data: Record<string, unknown>,
  origin = "null",
): Promise<void> {
  await page.evaluate(({ data, origin }) => {
    const iframe = document.querySelector("iframe[data-plugin-id]");
    if (!(iframe instanceof HTMLIFrameElement) || !iframe.contentWindow) {
      throw new Error("plugin iframe source is unavailable for negative MessageEvent");
    }
    window.dispatchEvent(new MessageEvent("message", {
      data,
      origin,
      source: iframe.contentWindow,
    }));
  }, { data, origin });
}

export async function dispatchWrongSource(page: Page, data: Record<string, unknown>): Promise<void> {
  await page.evaluate((data) => {
    window.dispatchEvent(new MessageEvent("message", {
      data,
      origin: "null",
      source: window,
    }));
  }, data);
}

export interface PluginDispatchOptions {
  readonly origin?: string;
  readonly source?: "parent" | "self";
}

export async function dispatchToPlugin(
  frame: Frame,
  data: Record<string, unknown>,
  options: PluginDispatchOptions = {},
): Promise<void> {
  const origin = options.origin ?? PLUGIN_ACCEPTANCE.shellOrigin;
  const source = options.source ?? "parent";
  await frame.evaluate(({ data, origin, source }) => {
    window.dispatchEvent(new MessageEvent("message", {
      data,
      origin,
      source: source === "parent" ? window.parent : window,
    }));
  }, { data, origin, source });
}

export async function listenerState(page: Page): Promise<ListenerState> {
  return page.evaluate(() => {
    const read = (window as unknown as {
      __agentscalePluginAcceptanceListenerState?: () => ListenerState;
    }).__agentscalePluginAcceptanceListenerState;
    if (!read) throw new Error("listener counters unavailable in current shell document");
    return read();
  });
}

export async function capturePluginSource(page: Page): Promise<number> {
  return page.evaluate(() => {
    const capture = (window as unknown as {
      __agentscalePluginAcceptanceCaptureSource?: () => number;
    }).__agentscalePluginAcceptanceCaptureSource;
    if (!capture) throw new Error("plugin source capture unavailable");
    return capture();
  });
}

export async function dispatchStoredPluginSource(
  page: Page,
  index: number,
  data: Record<string, unknown>,
  origin = "null",
): Promise<void> {
  await page.evaluate(({ index, data, origin }) => {
    const dispatch = (window as unknown as {
      __agentscalePluginAcceptanceDispatchStored?: (input: {
        readonly index: number;
        readonly data: Record<string, unknown>;
        readonly origin: string;
      }) => void;
    }).__agentscalePluginAcceptanceDispatchStored;
    if (!dispatch) throw new Error("stored plugin source dispatch unavailable");
    dispatch({ index, data, origin });
  }, { index, data, origin });
}

export function rpcFromHandshake(
  handshake: HandshakeObservation,
  requestId: string,
  context: Record<string, unknown>,
): Record<string, unknown> {
  return {
    ...handshake.pluginReady.data,
    type: "plugin.rpc.request",
    request_id: requestId,
    method: "capability.call",
    capability: {
      id: PLUGIN_ACCEPTANCE.capabilityId,
      version: PLUGIN_ACCEPTANCE.capabilityVersion,
    },
    context,
    input: {
      method: PLUGIN_ACCEPTANCE.greetOperation,
      params: { ...context, message: "browser negative scope probe" },
    },
    idempotency_key: requestId,
  };
}

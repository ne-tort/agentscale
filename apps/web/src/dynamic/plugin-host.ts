import { createCapabilityBroker, BROKER_PROTOCOL, type BrokerPolicy } from "./broker";

export interface PluginIdentity { readonly pluginId: string; readonly version: string; readonly digest: string; }
export interface PluginHostOptions {
  readonly document: Document;
  readonly container: HTMLElement;
  readonly src: string;
  readonly allowedOrigins: ReadonlySet<string>;
  readonly identity: PluginIdentity;
  readonly broker: BrokerPolicy;
  readonly timeoutMs?: number;
  readonly onFailure?: (code: string) => void;
}
function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function randomId(): string {
  const bytes = new Uint8Array(18);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

export function mountIsolatedPlugin(options: PluginHostOptions) {
  const parsed = new URL(options.src);
  if (!options.allowedOrigins.has(parsed.origin) || parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) throw new TypeError("plugin_origin_not_allowlisted");
  const origin = parsed.origin;
  const instanceId = randomId();
  const broker = createCapabilityBroker(options.broker);
  const hostWindow = options.document.defaultView;
  if (!hostWindow) throw new TypeError("plugin_host_window_unavailable");
  const frame = options.document.createElement("iframe");
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("referrerpolicy", "no-referrer");
  frame.setAttribute("allow", "");
  frame.src = parsed.href;
  options.container.replaceChildren(frame);
  let closed = false;
  const timeoutMs = Math.max(100, Math.min(options.timeoutMs ?? 10_000, 60_000));
  const fail = (code: string) => { if (closed) return; options.onFailure?.(code); destroy(); };
  const timer = setTimeout(() => fail("PLUGIN_HANDSHAKE_TIMEOUT"), timeoutMs);
  // Opaque sandbox origin cannot satisfy the registered HTTPS origin check; keep this path fail-closed pending an approved bootstrap contract.
  const onWindowMessage = (event: MessageEvent) => {
    if (closed || event.source !== frame.contentWindow || event.origin !== origin || !isRecord(event.data)) return;
    if (event.data.type === "plugin.ready") fail("PLUGIN_HANDSHAKE_UNSUPPORTED");
  };
  const destroy = () => {
    if (closed) return;
    closed = true;
    clearTimeout(timer);
    hostWindow.removeEventListener("message", onWindowMessage);
    broker.kill();
    frame.remove();
    options.container.replaceChildren();
  };
  hostWindow.addEventListener("message", onWindowMessage);
  frame.addEventListener("error", () => fail("PLUGIN_FRAME_ERROR"), { once: true });
  return { frame, instanceId, destroy, kill: () => { broker.kill(); fail("PLUGIN_KILLED"); }, get active() { return !closed; } };
}

// @vitest-environment jsdom
/** Browser-level hello scope assertions. Written under the migration freeze; not run. */
import { afterEach, describe, expect, it, vi } from "vitest";
import helloSource from "../../../../../packages/plugins/hello-plugin/frontend/app.js?raw";

const DIGEST = "sha256:" + "a".repeat(64);
const IDENTITY = {
  protocol: "plugin-rpc/1",
  plugin_id: "hello_plugin",
  version: "0.1.0",
  artifact_digest: DIGEST,
  instance_id: "instance-1234567890",
  nonce: "nonce-1234567890",
} as const;

let environmentSequence = 0;
const helloMessageListeners: EventListenerOrEventListenerObject[] = [];

type HelloOptions = {
  readonly canonical?: boolean;
  readonly handoff?: "valid" | Record<string, unknown> | string;
};

function setupHello(options: HelloOptions = {}) {
  environmentSequence += 1;
  const origin = `https://shell-${environmentSequence}.example`;
  const root = document.documentElement;
  root.setAttribute("data-plugin-id", "hello_plugin");
  root.setAttribute("data-plugin-version", "0.1.0");
  root.setAttribute("data-host-origin", options.canonical === false ? "" : origin);
  document.body.innerHTML = `
    <p id="state"></p>
    <button id="greet" disabled></button>
    <button id="refresh" disabled></button>
    <ul id="greetings"></ul>
  `;
  const parentFrame = document.createElement("iframe");
  document.body.append(parentFrame);
  const parentWindow = parentFrame.contentWindow;
  if (!parentWindow) throw new Error("parent window fixture unavailable");
  Object.defineProperty(window, "parent", { configurable: true, value: parentWindow });
  if (options.handoff === "valid") {
    window.name = JSON.stringify({
      protocol: "agentscale-plugin-shell/1",
      plugin_shell_origin: origin,
      plugin_id: "hello_plugin",
      manifest_version: "0.1.0",
      artifact_digest: DIGEST,
    });
  } else if (typeof options.handoff === "string") {
    window.name = options.handoff;
  } else if (options.handoff) {
    window.name = JSON.stringify({
      protocol: "agentscale-plugin-shell/1",
      plugin_shell_origin: origin,
      plugin_id: "hello_plugin",
      manifest_version: "0.1.0",
      artifact_digest: DIGEST,
      ...options.handoff,
    });
  } else {
    window.name = "";
  }
  let requestSequence = 0;
  Object.defineProperty(window, "crypto", {
    configurable: true,
    value: { randomUUID: () => `request-${environmentSequence}-${++requestSequence}` },
  });
  const postMessage = vi.spyOn(parentWindow, "postMessage").mockImplementation(() => undefined);
  const addEventListener = window.addEventListener.bind(window);
  vi.spyOn(window, "addEventListener").mockImplementation((type, listener, options) => {
    if (type === "message") helloMessageListeners.push(listener);
    addEventListener(type, listener, options);
  });
  new Function(helloSource)();
  return { origin, postMessage };
}

function dispatch(origin: string, message: Record<string, unknown>): void {
  window.dispatchEvent(new MessageEvent("message", {
    origin,
    source: window.parent,
    data: message,
  }));
}

function dispatchHostReady(origin: string): void {
  dispatch(origin, { ...IDENTITY, type: "host.ready" });
}

function dispatchGrant(origin: string, scope: Record<string, unknown>): void {
  dispatch(origin, {
    ...IDENTITY,
    type: "grant.ready",
    capabilities: ["hello.greetings@1.0"],
    scope,
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  });
}

afterEach(() => {
  for (const listener of helloMessageListeners) window.removeEventListener("message", listener);
  helloMessageListeners.length = 0;
  window.name = "";
  vi.restoreAllMocks();
});

describe("hello frontend tenant scope assertions", () => {
  it("binds list and greet params to the verified scope and exact host origin", () => {
    const { origin, postMessage } = setupHello();
    const scope = { company_id: "co_123", cabinet_id: "cab_123", project_id: "pr_123" };
    dispatchHostReady(origin);
    dispatchGrant(origin, scope);

    expect(postMessage).toHaveBeenCalledTimes(2);
    const list = postMessage.mock.calls[1][0] as {
      input: { method: string; params: Record<string, unknown> };
      context: Record<string, unknown>;
    };
    expect(list.input).toEqual({ method: "list", params: scope });
    expect(list.context).toEqual(scope);
    expect(postMessage.mock.calls[1][1]).toBe(origin);

    (document.getElementById("greet") as HTMLButtonElement).click();
    expect(postMessage).toHaveBeenCalledTimes(3);
    const greet = postMessage.mock.calls[2][0] as {
      input: { method: string; params: Record<string, unknown> };
      context: Record<string, unknown>;
    };
    expect(greet.input).toEqual({
      method: "greet",
      params: { ...scope, message: "Привет от hello_plugin" },
    });
    expect(greet.context).toEqual(scope);
    expect(postMessage.mock.calls[2][1]).toBe(origin);
    expect(JSON.stringify(greet)).not.toMatch(/access_token|capability_token|plugin_claims/i);
  });

  it.each([
    ["missing", { project_id: "pr_123" }],
    ["empty", { company_id: "  " }],
    ["wrong type", { company_id: 42 }],
  ])("denies %s company scope before RPC", (_name, scope) => {
    const { origin, postMessage } = setupHello();
    dispatchHostReady(origin);
    dispatchGrant(origin, scope);

    // Only plugin.ready was sent; grant and automatic list RPC were rejected.
    expect(postMessage).toHaveBeenCalledTimes(1);
    expect((document.getElementById("greet") as HTMLButtonElement).disabled).toBe(true);
    expect(document.getElementById("state")?.textContent).toContain("некорректный grant.ready");
  });

  it("denies a changed company grant before any further RPC", () => {
    const { origin, postMessage } = setupHello();
    dispatchHostReady(origin);
    dispatchGrant(origin, { company_id: "co_123" });
    expect(postMessage).toHaveBeenCalledTimes(2);

    dispatchGrant(origin, { company_id: "co_other" });
    (document.getElementById("greet") as HTMLButtonElement).click();

    expect(postMessage).toHaveBeenCalledTimes(2);
    expect(document.getElementById("state")?.textContent).toContain("некорректный grant.ready");
  });
});

describe("hello server-derived iframe.name handoff", () => {
  it("accepts only the exact DTO fallback and keeps exact parent/origin binding", () => {
    const { origin, postMessage } = setupHello({ canonical: false, handoff: "valid" });
    dispatchHostReady(origin);
    dispatchGrant(origin, { company_id: "co_123" });
    expect(postMessage).toHaveBeenCalledTimes(2);
    expect(postMessage.mock.calls[0][1]).toBe(origin);
    expect(postMessage.mock.calls[1][1]).toBe(origin);
  });

  it("keeps data-host-origin canonical over a malformed fallback", () => {
    const { origin, postMessage } = setupHello({ handoff: "not-json" });
    dispatchHostReady(origin);
    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage.mock.calls[0][1]).toBe(origin);
  });

  it.each([
    ["missing", undefined],
    ["invalid JSON", "not-json"],
    ["wrong protocol", { protocol: "agentscale-plugin-shell/0" }],
    ["extra field", { unexpected: true }],
    ["HTTP origin", { plugin_shell_origin: "http://shell.example" }],
    ["wildcard origin", { plugin_shell_origin: "https://*.example" }],
    ["wrong origin", { plugin_shell_origin: "https://evil.example" }],
    ["wrong plugin", { plugin_id: "other_plugin" }],
    ["wrong version", { manifest_version: "9.9.9" }],
    ["wrong digest", { artifact_digest: "sha256:" + "b".repeat(64) }],
  ])("denies %s handoff before plugin.ready", (_name, handoff) => {
    const { origin, postMessage } = setupHello({ canonical: false, handoff });
    dispatchHostReady(origin);
    expect(postMessage).not.toHaveBeenCalled();
    expect((document.getElementById("greet") as HTMLButtonElement).disabled).toBe(true);
  });

  it("rejects a wrong event.origin even with a valid handoff", () => {
    const { origin, postMessage } = setupHello({ canonical: false, handoff: "valid" });
    dispatchHostReady("https://evil.example");
    expect(postMessage).not.toHaveBeenCalled();
    dispatchHostReady(origin);
    expect(postMessage).toHaveBeenCalledTimes(1);
  });

  it("never trusts grant.ready as the first message", () => {
    const { origin, postMessage } = setupHello({ canonical: false, handoff: "valid" });
    dispatchGrant(origin, { company_id: "co_123" });
    expect(postMessage).not.toHaveBeenCalled();
    expect(document.getElementById("state")?.textContent).toContain("некорректный grant.ready");
  });

  it.each(["artifact_digest", "instance_id", "nonce"] as const)(
    "revalidates %s on every post-handshake message",
    (field) => {
      const { origin, postMessage } = setupHello({ canonical: false, handoff: "valid" });
      dispatchHostReady(origin);
      dispatch(origin, {
        ...IDENTITY,
        [field]: field === "artifact_digest" ? "sha256:" + "b".repeat(64) : "wrong-1234567890",
        type: "grant.ready",
        capabilities: ["hello.greetings@1.0"],
        scope: { company_id: "co_123" },
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      });
      expect(postMessage).toHaveBeenCalledTimes(1);
      expect((document.getElementById("greet") as HTMLButtonElement).disabled).toBe(true);
    },
  );
});

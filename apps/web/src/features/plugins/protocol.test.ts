// @vitest-environment jsdom
/** Golden hello↔host messages против реальной JSON Schema. НЕ запускались. */
import { describe, expect, it } from "vitest";
import protocolSchema from "../../../../../packages/schemas/plugin-manifest/v1/protocol.json";
import helloSource from "../../../../../packages/plugins/hello-plugin/frontend/app.js?raw";
import helloHtml from "../../../../../packages/plugins/hello-plugin/frontend/index.html?raw";
import helloManifest from "../../../../../packages/plugins/hello-plugin/manifest.json";

type Schema = Record<string, unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function ref(root: Schema, pointer: string): Schema {
  if (!pointer.startsWith("#/")) throw new Error("Only local refs are supported: " + pointer);
  return pointer.slice(2).split("/").reduce<unknown>((current, part) =>
    isRecord(current) ? current[part.replace(/~1/g, "/").replace(/~0/g, "~")] : undefined, root) as Schema;
}

/** Test-only Draft 2020-12 subset ровно для ключей этой закрытой схемы. */
function valid(schema: Schema, value: unknown, root: Schema = protocolSchema): boolean {
  if (typeof schema.$ref === "string") return valid(ref(root, schema.$ref), value, root);
  if (Object.hasOwn(schema, "const") && value !== schema.const) return false;
  if (Array.isArray(schema.enum) && !schema.enum.includes(value)) return false;
  if (Array.isArray(schema.oneOf)) {
    const matches = schema.oneOf.filter((branch) => isRecord(branch) && valid(branch, value, root)).length;
    if (matches !== 1) return false;
  }
  if (Array.isArray(schema.allOf)
    && !schema.allOf.every((branch) => isRecord(branch) && valid(branch, value, root))) return false;
  if (isRecord(schema.if)) {
    const condition = valid(schema.if, value, root);
    if (condition && isRecord(schema.then) && !valid(schema.then, value, root)) return false;
    if (!condition && isRecord(schema.else) && !valid(schema.else, value, root)) return false;
  }
  if (isRecord(schema.not) && valid(schema.not, value, root)) return false;

  if (schema.type === "object" && !isRecord(value)) return false;
  if (isRecord(value)) {
    const required = Array.isArray(schema.required) ? schema.required : [];
    if (required.some((key) => typeof key !== "string" || !Object.hasOwn(value, key))) return false;
    const properties = isRecord(schema.properties) ? schema.properties : {};
    if (schema.additionalProperties === false
      && Object.keys(value).some((key) => !Object.hasOwn(properties, key))) return false;
    for (const [key, child] of Object.entries(properties)) {
      if (Object.hasOwn(value, key) && isRecord(child) && !valid(child, value[key], root)) return false;
    }
  }
  if (schema.type === "array") {
    if (!Array.isArray(value)) return false;
    if (typeof schema.maxItems === "number" && value.length > schema.maxItems) return false;
    if (schema.uniqueItems === true
      && new Set(value.map((item) => JSON.stringify(item))).size !== value.length) return false;
    if (isRecord(schema.items) && value.some((item) => !valid(schema.items as Schema, item, root))) return false;
  } else if (schema.type === "string") {
    if (typeof value !== "string") return false;
  } else if (schema.type === "number") {
    if (typeof value !== "number" || !Number.isFinite(value)) return false;
  } else if (schema.type === "boolean") {
    if (typeof value !== "boolean") return false;
  }

  if (typeof value === "string") {
    if (typeof schema.pattern === "string" && !new RegExp(schema.pattern).test(value)) return false;
    if (typeof schema.minLength === "number" && value.length < schema.minLength) return false;
    if (typeof schema.maxLength === "number" && value.length > schema.maxLength) return false;
  }
  if (typeof value === "number") {
    if (typeof schema.minimum === "number" && value < schema.minimum) return false;
    if (typeof schema.maximum === "number" && value > schema.maximum) return false;
  }
  return true;
}

const DIGEST = "sha256:" + "a".repeat(64);
const base = {
  protocol: "plugin-rpc/1",
  plugin_id: "hello_plugin",
  version: "0.1.0",
  artifact_digest: DIGEST,
  instance_id: "instance-1234567890",
  nonce: "nonce-1234567890",
} as const;

const hostReady = { ...base, type: "host.ready" };
const pluginReady = { ...base, type: "plugin.ready" };
const grantReady = {
  ...base,
  type: "grant.ready",
  capabilities: ["hello.greetings@1.0"],
  scope: { company_id: "co_123", project_id: "pr_123" },
  expires_at: "2026-01-01T00:05:00.000Z",
};
const helloRequest = {
  ...base,
  type: "plugin.rpc.request",
  request_id: "req-1",
  method: "capability.call",
  capability: { id: "hello.greetings", version: "1.0" },
  context: { company_id: "co_123", project_id: "pr_123" },
  idempotency_key: "req-1",
  input: { method: "greet", params: { company_id: "co_123", project_id: "pr_123", message: "Привет от hello_plugin" } },
};
const hostResponse = {
  ...base,
  type: "plugin.rpc.response",
  request_id: "req-1",
  ok: true,
  result: { items: [{ message: "hello" }] },
};
const pluginError = {
  ...base,
  type: "plugin.error",
  request_id: "req-1",
  error: { code: "CAPABILITY_DENIED", message: "Capability is not granted", trace_id: "req-1" },
};

describe("protocol.json golden hello↔host", () => {
  it.each([
    ["host.ready", hostReady],
    ["plugin.ready", pluginReady],
    ["grant.ready", grantReady],
    ["hello capability.call", helloRequest],
    ["host response", hostResponse],
    ["plugin.error", pluginError],
    ["host.navigate", {
      ...base, type: "plugin.rpc.request", request_id: "nav-1", method: "host.navigate",
      context: { company_id: "co_123" }, input: { path: "/projects/pr_123" },
    }],
    ["host.resize", {
      ...base, type: "plugin.rpc.request", request_id: "resize-1", method: "host.resize",
      context: { company_id: "co_123" }, input: { height: 480 },
    }],
  ])("%s валиден по реальной schema", (_name, message) => {
    expect(valid(protocolSchema, message)).toBe(true);
  });

  it.each(["artifact_digest", "instance_id", "nonce"] as const)("%s обязателен", (field) => {
    const message = { ...pluginReady } as Record<string, unknown>;
    delete message[field];
    expect(valid(protocolSchema, message)).toBe(false);
  });

  it.each(["kind", "requestId", "instanceId", "idempotencyKey", "digest", "app_handle", "event", "origin"])(
    "legacy/payload transport field %s запрещён",
    (field) => expect(valid(protocolSchema, { ...helloRequest, [field]: "legacy" })).toBe(false),
  );

  it("capability.call без idempotency_key и success без result не проходят", () => {
    const withoutIdempotency = { ...helloRequest } as Record<string, unknown>;
    delete withoutIdempotency.idempotency_key;
    expect(valid(protocolSchema, withoutIdempotency)).toBe(false);
    const withoutResult = { ...hostResponse } as Record<string, unknown>;
    delete withoutResult.result;
    expect(valid(protocolSchema, withoutResult)).toBe(false);
    expect(valid(protocolSchema, { ...hostResponse, error: pluginError.error })).toBe(false);
  });
});

describe("hello frontend transport hygiene", () => {
  it("нет fetch/direct network и старых диалектов", () => {
    expect(helloSource).not.toMatch(/fetch[(]|XMLHttpRequest|WebSocket|EventSource/);
    expect(helloSource).not.toMatch(/kind[ ]*:|requestId[ ]*:|instanceId[ ]*:|idempotencyKey[ ]*:|app_handle/);
    expect(helloSource).not.toContain("https://agentscale.dev");
    expect(helloSource).toContain("type: type");
    expect(helloSource).toContain("message.idempotency_key = requestId");
  });

  it("hello использует canonical @1.0 и unique typed operations из manifest", () => {
    expect(helloSource).toContain('data.capabilities.indexOf("hello.greetings@1.0")');
    expect(helloSource).toContain('message.capability = { id: "hello.greetings", version: "1.0" }');
    expect(helloSource).not.toContain('"hello.greetings@1"');
    const operations = helloManifest.rpc_operations;
    expect(operations).toHaveLength(2);
    expect(operations.every((item) => item.capability.id === "hello.greetings" && item.capability.version === "1.0")).toBe(true);
    expect(new Set(operations.map((item) => `${item.capability.id}@${item.capability.version}:${item.operation}`)).size)
      .toBe(operations.length);
    expect(new Set(operations.map((item) => item.backend_path)).size).toBe(operations.length);
  });

  it("meta CSP не pretending frame-ancestors; HTTP CSP задокументирован", () => {
    const meta = helloHtml.slice(helloHtml.indexOf('<meta http-equiv="Content-Security-Policy"'));
    expect(meta).toContain('http-equiv="Content-Security-Policy"');
    expect(meta).not.toContain("frame-ancestors");
    expect(meta).toContain("connect-src 'none'");
    expect(helloHtml).toContain("HTTP frame-ancestors обязателен");
    expect(helloHtml).toContain('data-host-origin=""');
  });
});

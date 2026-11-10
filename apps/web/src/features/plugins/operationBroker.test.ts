import { describe, expect, it, vi } from "vitest";
import type { JsonValue } from "../../../../../packages/schemas/plugin-manifest/v1/types";
import type { RpcOperationResponse } from "../../api/types";
import type { PluginRpcRequest } from "./rpc";
import {
  createManifestOperationBroker,
  createPluginGatewayTransport,
  PluginOperationError,
  type PluginOperationTransport,
} from "./operationBroker";

const LIST: RpcOperationResponse = {
  capability_id: "hello.greetings",
  capability_version: "1.0",
  operation: "list",
  http_method: "GET",
  backend_path: "/api/greetings/list",
  public: true,
};
const GREET: RpcOperationResponse = {
  capability_id: "hello.greetings",
  capability_version: "1.0",
  operation: "greet",
  http_method: "POST",
  backend_path: "/api/greetings",
  public: false,
};
const CAPABILITIES = ["hello.greetings@1.0"];

function call(
  input: JsonValue = { method: "list", params: {} },
  capability = { id: "hello.greetings", version: "1.0" },
): PluginRpcRequest {
  return {
    protocol: "plugin-rpc/1",
    type: "plugin.rpc.request",
    plugin_id: "hello_plugin",
    version: "0.1.0",
    artifact_digest: `sha256:${"a".repeat(64)}`,
    instance_id: "instance-1234567890",
    nonce: "nonce-1234567890",
    request_id: "request-1",
    method: "capability.call",
    capability,
    context: { company_id: "co_123" },
    idempotency_key: "request-1",
    input,
  };
}

function broker(
  transport: PluginOperationTransport,
  options: Partial<Parameters<typeof createManifestOperationBroker>[0]> = {},
) {
  return createManifestOperationBroker({
    operations: [LIST, GREET],
    capabilities: CAPABILITIES,
    transport,
    ...options,
  });
}

describe("manifest-scoped operation broker", () => {
  it("allows exact signed list/greet operations at canonical @1.0", async () => {
    const transport = vi.fn(async () => ({ items: [] }));
    const handler = broker(transport);
    await expect(handler(call())).resolves.toEqual({ items: [] });
    await handler(call({ method: "greet", params: { message: "hello" } }));
    expect(transport).toHaveBeenNthCalledWith(1, expect.objectContaining({
      declaration: LIST,
      idempotencyKey: "request-1",
    }));
    expect(transport).toHaveBeenNthCalledWith(2, expect.objectContaining({
      declaration: GREET,
      params: { message: "hello" },
    }));
  });

  it("returns typed denial for unsigned operation or non-canonical capability version", async () => {
    const transport = vi.fn(async () => ({}));
    const handler = broker(transport);
    await expect(handler(call({ method: "delete", params: {} }))).rejects.toMatchObject({
      name: "PluginOperationError",
      reason: "OPERATION_NOT_DECLARED",
      rpcCode: "CAPABILITY_DENIED",
    });
    await expect(handler(call(undefined, { id: "hello.greetings", version: "1" }))).rejects.toMatchObject({
      reason: "OPERATION_NOT_DECLARED",
      rpcCode: "CAPABILITY_DENIED",
    });
    expect(transport).not.toHaveBeenCalled();
  });

  it("public allows only exact public read-only GET and denies POST before transport", async () => {
    const transport = vi.fn(async () => ({ items: [] }));
    const handler = broker(transport, { publicPage: true, operations: [LIST] });
    await expect(handler(call())).resolves.toEqual({ items: [] });
    await expect(handler(call({ method: "greet", params: { message: "side effect" } })))
      .rejects.toMatchObject({ reason: "OPERATION_NOT_DECLARED", rpcCode: "CAPABILITY_DENIED" });

    const postHandler = broker(transport, { publicPage: true, operations: [{ ...GREET, public: true }] });
    await expect(postHandler(call({ method: "greet", params: { message: "side effect" } })))
      .rejects.toMatchObject({ reason: "MANIFEST_OPERATIONS_INVALID", rpcCode: "CAPABILITY_DENIED" });
    expect(transport).toHaveBeenCalledTimes(1);
  });

  it("rejects generic URL/proxy fields instead of forwarding them", async () => {
    const transport = vi.fn(async () => ({}));
    const handler = broker(transport);
    await expect(handler(call({
      method: "list",
      params: {},
      url: "https://evil.example/steal",
    }))).rejects.toMatchObject({ reason: "PAYLOAD_INVALID", rpcCode: "INVALID_ENVELOPE" });
    expect(transport).not.toHaveBeenCalled();
  });

  it("bounds payload bytes and per-operation request quota", async () => {
    const transport = vi.fn(async () => ({}));
    const limited = broker(transport, { maxPayloadBytes: 16 });
    await expect(limited(call({ method: "greet", params: { message: "far too large" } })))
      .rejects.toMatchObject({ reason: "PAYLOAD_TOO_LARGE", rpcCode: "PAYLOAD_TOO_LARGE" });

    const quota = broker(transport, { maxRequestsPerMinute: 1, now: () => 1_000 });
    await quota(call());
    await expect(quota(call())).rejects.toMatchObject({ reason: "QUOTA_EXCEEDED", rpcCode: "REQUEST_FAILED" });
    expect(transport).toHaveBeenCalledTimes(1);
  });

  it("fails closed when operation is not bound to bootstrap capabilities", async () => {
    const transport = vi.fn(async () => ({}));
    const handler = broker(transport, { capabilities: ["other.capability@1.0"] });
    await expect(handler(call())).rejects.toMatchObject({
      reason: "MANIFEST_OPERATIONS_INVALID",
      rpcCode: "CAPABILITY_DENIED",
    });
    expect(transport).not.toHaveBeenCalled();
    expect(new PluginOperationError("OPERATION_NOT_DECLARED", "CAPABILITY_DENIED").message)
      .toBe("OPERATION_NOT_DECLARED");
  });
});

describe("fixed plugin gateway transport", () => {
  it("uses only signed backend_path and sends anonymous public GET without Authorization", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    const transport = createPluginGatewayTransport({
      companyId: "co_123",
      pluginId: "hello_plugin",
      publicPage: true,
      fetcher,
      getAccessToken: () => { throw new Error("public must not read session token"); },
    });
    await transport({
      declaration: LIST,
      params: { limit: 2, tag: ["a", "b"] },
      idempotencyKey: "request-1",
    });
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/pluginpage/hello_plugin/co_123/api/greetings/list?limit=2&tag=a&tag=b");
    expect(init.method).toBe("GET");
    expect(init.headers).not.toHaveProperty("Authorization");
  });

  it("denies public POST and private request without session before fetch", async () => {
    const fetcher = vi.fn(async () => new Response("{}"));
    const publicTransport = createPluginGatewayTransport({
      companyId: "co_123", pluginId: "hello_plugin", publicPage: true, fetcher,
    });
    await expect(publicTransport({
      declaration: { ...GREET, public: true }, params: { message: "side effect" }, idempotencyKey: "request-1",
    })).rejects.toMatchObject({ reason: "OPERATION_NOT_DECLARED" });

    const privateTransport = createPluginGatewayTransport({
      companyId: "co_123", pluginId: "hello_plugin", fetcher,
    });
    await expect(privateTransport({ declaration: LIST, params: {}, idempotencyKey: "request-1" }))
      .rejects.toMatchObject({ reason: "SESSION_REQUIRED", rpcCode: "CAPABILITY_DENIED" });
    expect(fetcher).not.toHaveBeenCalled();
  });
});

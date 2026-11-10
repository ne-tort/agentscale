import type { RpcOperationResponse } from "../../api/types";
import type { JsonValue } from "../../../../../packages/schemas/plugin-manifest/v1/types";
import { normalizeRpcOperations } from "./bootstrap";
import type { PluginRequestHandler, PluginRpcErrorCode } from "./rpc";

export type PluginOperationDenial =
  | "OPERATION_NOT_DECLARED"
  | "MANIFEST_OPERATIONS_INVALID"
  | "PAYLOAD_INVALID"
  | "PAYLOAD_TOO_LARGE"
  | "QUOTA_EXCEEDED"
  | "SESSION_REQUIRED"
  | "TRANSPORT_DENIED"
  | "TRANSPORT_FAILED";

/** Safe, typed denial; no upstream body/URL/secret is copied into the message. */
export class PluginOperationError extends Error {
  readonly reason: PluginOperationDenial;
  readonly rpcCode: PluginRpcErrorCode;

  constructor(reason: PluginOperationDenial, rpcCode: PluginRpcErrorCode) {
    super(reason);
    this.name = "PluginOperationError";
    this.reason = reason;
    this.rpcCode = rpcCode;
  }
}

export interface PluginOperationTransportRequest {
  readonly declaration: RpcOperationResponse;
  readonly params: { readonly [key: string]: JsonValue };
  readonly idempotencyKey: string;
}

/** Trusted host adapter. It receives a declaration, never a plugin-selected URL. */
export type PluginOperationTransport = (
  request: PluginOperationTransportRequest,
) => JsonValue | Promise<JsonValue>;

export interface ManifestOperationBrokerOptions {
  readonly operations: unknown;
  readonly capabilities: readonly string[];
  readonly publicPage?: boolean;
  readonly transport: PluginOperationTransport;
  readonly maxPayloadBytes?: number;
  readonly maxRequestsPerMinute?: number;
  readonly maxInFlight?: number;
  readonly now?: () => number;
}

export interface PluginGatewayTransportOptions {
  readonly companyId: string;
  readonly pluginId: string;
  readonly publicPage?: boolean;
  readonly getAccessToken?: () => string | undefined;
  readonly fetcher?: typeof fetch;
  readonly maxResponseBytes?: number;
}

export const DEFAULT_OPERATION_PAYLOAD_BYTES = 16 * 1024;
export const DEFAULT_OPERATION_RESPONSE_BYTES = 64 * 1024;
export const DEFAULT_OPERATION_REQUESTS_PER_MINUTE = 60;
export const DEFAULT_OPERATION_IN_FLIGHT = 8;

function denial(reason: PluginOperationDenial, rpcCode: PluginRpcErrorCode): never {
  throw new PluginOperationError(reason, rpcCode);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function safeJson(value: unknown, depth = 0, budget = { nodes: 0 }): value is JsonValue {
  budget.nodes += 1;
  if (budget.nodes > 512 || depth > 8) return false;
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "string") return value.length <= 16_384;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.length <= 256
    && value.every((item) => safeJson(item, depth + 1, budget));
  return isRecord(value) && Object.keys(value).length <= 128
    && Object.entries(value).every(([key, item]) =>
      !["__proto__", "prototype", "constructor"].includes(key)
      && safeJson(item, depth + 1, budget));
}

function payloadBytes(value: unknown): number {
  try {
    const serialized = JSON.stringify(value);
    if (serialized === undefined) return Number.MAX_SAFE_INTEGER;
    return new TextEncoder().encode(serialized).byteLength;
  } catch {
    return Number.MAX_SAFE_INTEGER;
  }
}

function positiveLimit(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : fallback;
}

/**
 * Resolve capability.call to exactly one signed capability@major.minor +
 * operation declaration. Unknown operations fail before the transport. The
 * input union is closed: {method, params}; url/path/proxy fields are rejected.
 */
export function createManifestOperationBroker(options: ManifestOperationBrokerOptions): PluginRequestHandler {
  const publicPage = options.publicPage === true;
  const declarations = normalizeRpcOperations(options.operations, options.capabilities, publicPage);
  const byOperation = new Map<string, RpcOperationResponse>();
  for (const declaration of declarations ?? []) {
    byOperation.set(
      `${declaration.capability_id}@${declaration.capability_version}:${declaration.operation}`,
      declaration,
    );
  }
  const maxPayloadBytes = positiveLimit(options.maxPayloadBytes, DEFAULT_OPERATION_PAYLOAD_BYTES);
  const maxRequestsPerMinute = positiveLimit(options.maxRequestsPerMinute, DEFAULT_OPERATION_REQUESTS_PER_MINUTE);
  const maxInFlight = positiveLimit(options.maxInFlight, DEFAULT_OPERATION_IN_FLIGHT);
  const now = options.now ?? (() => Date.now());
  const calls = new Map<string, number[]>();
  let inFlight = 0;

  return async (request) => {
    if (request.method !== "capability.call") denial("OPERATION_NOT_DECLARED", "UNKNOWN_METHOD");
    if (declarations === undefined) denial("MANIFEST_OPERATIONS_INVALID", "CAPABILITY_DENIED");
    const input: unknown = request.input;
    if (!isRecord(input)) denial("PAYLOAD_INVALID", "INVALID_ENVELOPE");
    const keys = Object.keys(input).sort();
    if (keys.length !== 2 || keys[0] !== "method" || keys[1] !== "params"
      || typeof input.method !== "string" || !isRecord(input.params)
      || !safeJson(input.params) || payloadBytes(input.params) > maxPayloadBytes) {
      if (payloadBytes(input.params) > maxPayloadBytes) denial("PAYLOAD_TOO_LARGE", "PAYLOAD_TOO_LARGE");
      denial("PAYLOAD_INVALID", "INVALID_ENVELOPE");
    }
    const key = `${request.capability.id}@${request.capability.version}:${input.method}`;
    const declaration = byOperation.get(key);
    if (!declaration || (publicPage && (!declaration.public || declaration.http_method !== "GET"))) {
      denial("OPERATION_NOT_DECLARED", "CAPABILITY_DENIED");
    }

    const timestamp = now();
    if (!Number.isFinite(timestamp)) denial("QUOTA_EXCEEDED", "REQUEST_FAILED");
    const recent = (calls.get(key) ?? []).filter((item) => timestamp - item >= 0 && timestamp - item < 60_000);
    if (inFlight >= maxInFlight || recent.length >= maxRequestsPerMinute) {
      denial("QUOTA_EXCEEDED", "REQUEST_FAILED");
    }
    recent.push(timestamp);
    calls.set(key, recent);
    inFlight += 1;
    try {
      return await options.transport({
        declaration,
        params: input.params as { readonly [key: string]: JsonValue },
        idempotencyKey: request.idempotency_key,
      });
    } finally {
      inFlight -= 1;
    }
  };
}

function appendQuery(query: URLSearchParams, key: string, value: JsonValue): void {
  if (Array.isArray(value)) {
    for (const item of value) appendQuery(query, key, item);
    return;
  }
  if (typeof value === "string") query.append(key, value);
  else if (typeof value === "number" && Number.isFinite(value)) query.append(key, String(value));
  else if (typeof value === "boolean") query.append(key, String(value));
  else denial("PAYLOAD_INVALID", "INVALID_ENVELOPE");
}

/** Fixed gateway adapter; only a validated signed backend_path becomes a URL suffix. */
export function createPluginGatewayTransport(options: PluginGatewayTransportOptions): PluginOperationTransport {
  const fetcher = options.fetcher ?? fetch;
  const publicPage = options.publicPage === true;
  const maxResponseBytes = positiveLimit(options.maxResponseBytes, DEFAULT_OPERATION_RESPONSE_BYTES);
  return async ({ declaration, params }) => {
    if (publicPage && (!declaration.public || declaration.http_method !== "GET")) {
      denial("OPERATION_NOT_DECLARED", "CAPABILITY_DENIED");
    }
    if ((declaration.http_method !== "GET" && declaration.http_method !== "POST")
      || !/^[/]api[/][A-Za-z0-9._/-]+$/.test(declaration.backend_path)
      || declaration.backend_path.includes("..") || declaration.backend_path.split("/").includes(".")) {
      denial("MANIFEST_OPERATIONS_INVALID", "CAPABILITY_DENIED");
    }
    const token = publicPage ? undefined : options.getAccessToken?.();
    if (!publicPage && !token) denial("SESSION_REQUIRED", "CAPABILITY_DENIED");
    const query = new URLSearchParams();
    if (declaration.http_method === "GET") {
      for (const [key, value] of Object.entries(params)) appendQuery(query, key, value);
    }
    const suffix = declaration.http_method === "GET" && query.size ? `?${query.toString()}` : "";
    const url = `/pluginpage/${encodeURIComponent(options.pluginId)}/${encodeURIComponent(options.companyId)}${declaration.backend_path}${suffix}`;
    const body = declaration.http_method === "POST" ? JSON.stringify(params) : undefined;
    let response: Response;
    try {
      response = await fetcher(url, {
        method: declaration.http_method,
        headers: {
          Accept: "application/json",
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(body !== undefined ? { body } : {}),
      });
    } catch {
      denial("TRANSPORT_FAILED", "REQUEST_FAILED");
    }
    const declaredLength = Number(response.headers.get("content-length") ?? "0");
    if ((Number.isFinite(declaredLength) && declaredLength > maxResponseBytes)
      || response.status === 413) {
      denial("PAYLOAD_TOO_LARGE", "PAYLOAD_TOO_LARGE");
    }
    if (!response.ok) {
      if (response.status === 429) denial("QUOTA_EXCEEDED", "REQUEST_FAILED");
      if (response.status === 401 || response.status === 403) denial("TRANSPORT_DENIED", "CAPABILITY_DENIED");
      denial("TRANSPORT_FAILED", "REQUEST_FAILED");
    }
    let text: string;
    try { text = await response.text(); }
    catch { denial("TRANSPORT_FAILED", "REQUEST_FAILED"); }
    if (new TextEncoder().encode(text).byteLength > maxResponseBytes) {
      denial("PAYLOAD_TOO_LARGE", "PAYLOAD_TOO_LARGE");
    }
    if (!text) return null;
    try { return JSON.parse(text) as JsonValue; }
    catch { return denial("TRANSPORT_FAILED", "REQUEST_FAILED"); }
  };
}

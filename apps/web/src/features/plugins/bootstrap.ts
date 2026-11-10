import type { RpcOperationResponse } from "../../api/types";
import { assertSafeSrc } from "./PluginFrame";
import { PluginFrameError } from "./errors";
import { resolvePluginShellOrigin } from "./shellHandoff";

const ENTRY_PATH = /^[A-Za-z0-9._/-]+$/;
const ABSOLUTE_PATH = /^[/][A-Za-z0-9._/-]*$/;
const CAPABILITY_ID = /^[a-z][a-z0-9_-]{1,63}(?:[.][a-z][a-z0-9_-]{1,63})?$/;
const CAPABILITY_VERSION = /^[0-9]+[.][0-9]+$/;
const OPERATION = /^[a-z][a-z0-9_.-]{0,63}$/;
const BACKEND_PATH = /^[/]api[/][A-Za-z0-9._/-]+$/;

export interface TrustedFrontendDeclaration {
  readonly entry: string;
  readonly basePath: string;
  readonly shellOrigin?: string;
}

/** The same canonical route grammar as the bootstrap API. */
export function isCanonicalPluginRoutePath(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 512
    && value.startsWith("/") && ABSOLUTE_PATH.test(value)
    && !value.split("/").includes("..") && (value === "/" || !value.endsWith("/"));
}

export function normalizePluginRoutePaths(value: unknown): string[] | undefined {
  if (!Array.isArray(value) || value.length > 64) return undefined;
  const result: string[] = [];
  for (const item of value) {
    if (!isCanonicalPluginRoutePath(item)) return undefined;
    result.push(item);
  }
  return new Set(result).size === result.length ? result : undefined;
}

/**
 * Build an iframe URL only from the signed bootstrap frontend declaration.
 * In particular, company/plugin/version are never interpolated into a second,
 * frontend-owned storage layout.
 */
export function buildPluginSrc(staticOrigin: string, declaration: TrustedFrontendDeclaration): string {
  let origin: URL;
  try { origin = new URL(staticOrigin); }
  catch { throw new PluginFrameError("originMismatch", "plugin_static_origin_invalid"); }
  if (origin.protocol !== "https:" || origin.username || origin.password
    || origin.pathname !== "/" || origin.search || origin.hash || origin.origin !== staticOrigin) {
    throw new PluginFrameError("originMismatch", "plugin_static_origin_not_exact");
  }
  if (declaration.shellOrigin) {
    const shell = resolvePluginShellOrigin(declaration.shellOrigin);
    if (shell === origin.origin) {
      throw new PluginFrameError("originMismatch", "plugin_static_origin_is_shell");
    }
  }
  const entry = declaration.entry;
  const basePath = declaration.basePath;
  if (typeof entry !== "string" || entry.length === 0 || entry.length > 256
    || entry.startsWith("/") || !ENTRY_PATH.test(entry) || entry.includes("..")
    || entry.split("/").some((part) => part === "" || part === ".")) {
    throw new PluginFrameError("unsafeSrc", "plugin_frontend_entry_invalid");
  }
  if (typeof basePath !== "string" || basePath.length === 0 || basePath.length > 512
    || !ABSOLUTE_PATH.test(basePath) || basePath.includes("..") || basePath.includes("//")
    || basePath.split("/").includes(".")) {
    throw new PluginFrameError("unsafeSrc", "plugin_frontend_base_path_invalid");
  }
  const prefix = basePath === "/" ? "" : basePath.replace(/[/]$/, "");
  const src = `${origin.origin}${prefix}/${entry}`;
  const parsed = new URL(src);
  if (parsed.origin !== origin.origin) throw new PluginFrameError("originMismatch", "plugin_frontend_origin_escape");
  assertSafeSrc(src);
  return src;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Runtime validation and manifest/capability binding for bootstrap operations. */
export function normalizeRpcOperations(
  value: unknown,
  capabilities: readonly string[],
  publicPage: boolean,
): RpcOperationResponse[] | undefined {
  if (!Array.isArray(value) || value.length > 64) return undefined;
  const result: RpcOperationResponse[] = [];
  const operationKeys = new Set<string>();
  const backendPaths = new Set<string>();
  for (const raw of value) {
    if (!isRecord(raw)) return undefined;
    const keys = Object.keys(raw).sort();
    const expected = ["backend_path", "capability_id", "capability_version", "http_method", "operation", "public"];
    if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) return undefined;
    const {
      capability_id: capabilityId,
      capability_version: capabilityVersion,
      operation,
      http_method: httpMethod,
      backend_path: backendPath,
      public: isPublic,
    } = raw;
    if (typeof capabilityId !== "string" || !CAPABILITY_ID.test(capabilityId)
      || typeof capabilityVersion !== "string" || !CAPABILITY_VERSION.test(capabilityVersion)
      || typeof operation !== "string" || !OPERATION.test(operation)
      || (httpMethod !== "GET" && httpMethod !== "POST")
      || typeof backendPath !== "string" || !BACKEND_PATH.test(backendPath) || backendPath.includes("..")
      || backendPath.split("/").includes(".")
      || typeof isPublic !== "boolean"
      || !capabilities.includes(`${capabilityId}@${capabilityVersion}`)
      || (publicPage && (!isPublic || httpMethod !== "GET"))) {
      return undefined;
    }
    const operationKey = `${capabilityId}@${capabilityVersion}:${operation}`;
    if (operationKeys.has(operationKey) || backendPaths.has(backendPath)) return undefined;
    operationKeys.add(operationKey);
    backendPaths.add(backendPath);
    result.push({
      capability_id: capabilityId,
      capability_version: capabilityVersion,
      operation,
      http_method: httpMethod,
      backend_path: backendPath,
      public: isPublic,
    });
  }
  return result;
}

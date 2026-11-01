import type { DeclarativeAppManifest, UiNode } from "../../../../packages/schemas/declarative-app/v1";

export type ContextDimension = "tenantId" | "cabinetId" | "projectId" | "sessionId";
export interface RouteRequirements {
  readonly context: readonly ContextDimension[];
  readonly capabilities?: readonly string[];
}
export interface ValidatedRoute {
  readonly id: string;
  readonly path: string;
  readonly title: string;
  readonly root: string;
  readonly requirements: RouteRequirements;
}
export type SchemaValidation =
  | { readonly ok: true; readonly manifest: DeclarativeAppManifest }
  | { readonly ok: false; readonly code: "invalid_schema" | "unsupported_version"; readonly detail: string };

const ROUTE_DIMENSIONS = new Set<ContextDimension>(["tenantId", "cabinetId", "projectId", "sessionId"]);
const ROUTE_CAPABILITY = /^[a-z][a-z0-9._-]{0,127}$/;

export function validateRouteRequirements(value: unknown): value is RouteRequirements {
  if (!record(value) || !closed(value, ["context", "capabilities"], ["context"]) || !Array.isArray(value.context)) return false;
  const dimensions = value.context;
  if (new Set(dimensions).size !== dimensions.length || dimensions.some((dimension) => typeof dimension !== "string" || !ROUTE_DIMENSIONS.has(dimension as ContextDimension))) return false;
  if (value.capabilities !== undefined && (!Array.isArray(value.capabilities) || new Set(value.capabilities).size !== value.capabilities.length || value.capabilities.some((capability) => typeof capability !== "string" || !ROUTE_CAPABILITY.test(capability)))) return false;
  return true;
}

export function validateManifestRoute(value: unknown, ids: ReadonlySet<string>): value is { id: string; path: string; title: string; root: string } {
  return record(value) && closed(value, ["id", "path", "title", "root"], ["id", "path", "title", "root"]) && validateManifestRouteFields(value, ids);
}

export function validateRoute(value: unknown, ids: ReadonlySet<string>): value is ValidatedRoute {
  return record(value) && closed(value, ["id", "path", "title", "root", "requirements"], ["id", "path", "title", "root", "requirements"]) && validateManifestRouteFields(value, ids) && validateRouteRequirements(value.requirements);
}

function validateManifestRouteFields(value: Record<string, unknown>, ids: ReadonlySet<string>): boolean {
  return typeof value.id === "string" && value.id.length > 0 && typeof value.path === "string" && value.path.startsWith("/") && !value.path.includes("://") && !value.path.includes("..") && typeof value.title === "string" && value.title.length > 0 && typeof value.root === "string" && ids.has(value.root);
}

const APP_ID = /^[a-z][a-z0-9-]{2,63}$/;
const SEMVER = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/;
const CAPABILITIES = new Set(["ui.render", "ui.navigation", "storage.kv", "storage.files", "network.fetch", "events.subscribe", "platform.commands", "platform.queries", "secrets.read", "identity.read"]);
const NODE_TYPES = new Set(["stack", "row", "column", "text", "button", "input", "table", "slot"]);
const RESERVED_KEYS = new Set(["__proto__", "prototype", "constructor"]);

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function closed(value: Record<string, unknown>, keys: readonly string[], required: readonly string[]): boolean {
  return Object.keys(value).every((key) => keys.includes(key) && !RESERVED_KEYS.has(key)) && required.every((key) => key in value);
}
function validConstraints(value: unknown): boolean {
  if (!record(value) || !closed(value, ["resources", "methods", "domains", "eventTypes", "maxBytesPerRequest"], [])) return false;
  for (const key of ["resources", "methods", "domains", "eventTypes"] as const) {
    const list = value[key];
    if (list !== undefined && (!Array.isArray(list) || list.some((item) => typeof item !== "string" || !item.trim()) || new Set(list).size !== list.length)) return false;
  }
  return value.maxBytesPerRequest === undefined || (Number.isInteger(value.maxBytesPerRequest) && (value.maxBytesPerRequest as number) >= 1);
}

function safeJsonSchema(value: unknown, depth = 0): boolean {
  if (depth > 16) return false;
  if (Array.isArray(value)) return value.length <= 256 && value.every((item) => safeJsonSchema(item, depth + 1));
  if (!record(value)) return value === null || ["string", "number", "boolean"].includes(typeof value);
  if (Object.keys(value).some((key) => key.startsWith("x-") || key === "$ref" || key === "$id" || RESERVED_KEYS.has(key))) return false;
  return Object.values(value).every((item) => safeJsonSchema(item, depth + 1));
}

function validPluginMethod(value: unknown, names: Set<string>): boolean {
  if (!record(value) || !closed(value, ["name", "description", "inputSchema", "outputSchema", "requiredCapabilities"], ["name"]) || typeof value.name !== "string" || !/^[a-z][a-z0-9._-]*$/.test(value.name) || names.has(value.name) || (value.description !== undefined && typeof value.description !== "string")) return false;
  names.add(value.name);
  for (const key of ["inputSchema", "outputSchema"] as const) if (value[key] !== undefined && (!record(value[key]) || !safeJsonSchema(value[key]))) return false;
  return value.requiredCapabilities === undefined || (Array.isArray(value.requiredCapabilities) && new Set(value.requiredCapabilities).size === value.requiredCapabilities.length && value.requiredCapabilities.every((capability) => typeof capability === "string" && CAPABILITIES.has(capability)));
}

function validNode(value: unknown, ids: Set<string>, depth = 0): value is UiNode {
  if (!record(value) || depth > 32 || typeof value.type !== "string" || !NODE_TYPES.has(value.type)) return false;
  if (value.type === "stack" || value.type === "row" || value.type === "column") {
    if (!closed(value, ["type", "id", "children"], ["type", "children"]) || (value.id !== undefined && typeof value.id !== "string") || !Array.isArray(value.children)) return false;
    if (typeof value.id === "string") { if (!value.id || ids.has(value.id)) return false; ids.add(value.id); }
    return value.children.every((child) => validNode(child, ids, depth + 1));
  }
  if (value.type === "slot") {
    if (!closed(value, ["type", "id", "name"], ["type", "id", "name"]) || typeof value.id !== "string" || !value.id || typeof value.name !== "string" || !value.name) return false;
  } else if (!closed(value, ["type", "id", "props", "bindings"], ["type", "id"]) || typeof value.id !== "string" || !value.id || (value.props !== undefined && !record(value.props)) || (value.bindings !== undefined && !record(value.bindings))) return false;
  if (ids.has(value.id as string)) return false;
  ids.add(value.id as string);
  if (record(value.props) && JSON.stringify(value.props).match(/(?:javascript:|<script|https?:\/\/|\bfetch\s*\(|\beval\s*\()/i)) return false;
  if (record(value.bindings) && Object.values(value.bindings).some((binding) => typeof binding !== "string" || !/^(?:row\.[A-Za-z][\w.]*|context\.(?:tenantId|cabinetId|projectId|sessionId)|server\.[A-Za-z][\w.]*)$/.test(binding))) return false;
  return true;
}

export function validateDeclarativeManifest(input: unknown): SchemaValidation {
  if (!record(input)) return { ok: false, code: "invalid_schema", detail: "manifest_not_object" };
  if (input.protocol !== "1.0") return { ok: false, code: "unsupported_version", detail: "unsupported_protocol" };
  const keys = ["protocol", "appId", "version", "displayName", "description", "entrypoints", "requestedCapabilities", "quotas", "lifecycle", "metadata"];
  if (!closed(input, keys, ["protocol", "appId", "version", "displayName", "entrypoints", "requestedCapabilities"]) || typeof input.appId !== "string" || !APP_ID.test(input.appId) || typeof input.version !== "string" || !SEMVER.test(input.version) || typeof input.displayName !== "string" || !input.displayName.trim() || input.displayName.length > 120 || !record(input.entrypoints) || !Object.keys(input.entrypoints).length || Object.keys(input.entrypoints).some((key) => key !== "ui" && key !== "plugin") || !Array.isArray(input.requestedCapabilities)) return { ok: false, code: "invalid_schema", detail: "invalid_manifest_shape" };
  const requestedNames = new Set<string>();
  for (const request of input.requestedCapabilities) {
    if (!record(request) || !closed(request, ["name", "reason", "constraints", "optional"], ["name", "reason"]) || typeof request.name !== "string" || !CAPABILITIES.has(request.name) || requestedNames.has(request.name) || typeof request.reason !== "string" || !request.reason.trim() || (request.optional !== undefined && typeof request.optional !== "boolean") || (request.constraints !== undefined && !validConstraints(request.constraints))) return { ok: false, code: "invalid_schema", detail: "invalid_capability_request" };
    requestedNames.add(request.name);
  }
  const entry = input.entrypoints.ui;
  if (entry !== undefined) {
    if (!record(entry) || !closed(entry, ["kind", "root", "routes"], ["kind", "root"]) || entry.kind !== "declarative-ui") return { ok: false, code: "invalid_schema", detail: "invalid_ui_entrypoint" };
    const ids = new Set<string>();
    if (!validNode(entry.root, ids)) return { ok: false, code: "invalid_schema", detail: "invalid_or_unsafe_ui_node" };
    if (entry.routes !== undefined) {
      if (!Array.isArray(entry.routes)) return { ok: false, code: "invalid_schema", detail: "invalid_routes" };
      const routeIds = new Set<string>(), paths = new Set<string>();
      for (const route of entry.routes) {
        if (!record(route) || !closed(route, ["id", "path", "title", "root"], ["id", "path", "title", "root"]) || !validateManifestRoute(route, ids) || routeIds.has(route.id as string) || paths.has(route.path as string)) return { ok: false, code: "invalid_schema", detail: "invalid_or_duplicate_route" };
        routeIds.add(route.id); paths.add(route.path);
      }
    }
  }
  const plugin = input.entrypoints.plugin;
  if (plugin !== undefined && (!record(plugin) || !closed(plugin, ["kind", "runtime", "artifact", "rpc"], ["kind", "runtime", "artifact", "rpc"]) || plugin.kind !== "isolated-code-plugin" || plugin.runtime !== "sandbox-v1" || !record(plugin.artifact) || !closed(plugin.artifact, ["uri", "sha256"], ["uri", "sha256"]) || typeof plugin.artifact.uri !== "string" || typeof plugin.artifact.sha256 !== "string" || !/^[a-f\d]{64}$/i.test(plugin.artifact.sha256) || !record(plugin.rpc) || !closed(plugin.rpc, ["transport", "methods"], ["transport", "methods"]) || plugin.rpc.transport !== "host-message" || !Array.isArray(plugin.rpc.methods) || !plugin.rpc.methods.every((method) => validPluginMethod(method, new Set())))) return { ok: false, code: "invalid_schema", detail: "invalid_plugin_entrypoint" };
  return { ok: true, manifest: deepFreeze(input as unknown as DeclarativeAppManifest) };
}

export function deepFreeze<T>(value: T): T {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value as Record<string, unknown>)) deepFreeze(child);
  }
  return value;
}

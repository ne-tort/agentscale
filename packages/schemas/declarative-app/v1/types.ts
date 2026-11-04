/** Versioned protocol for declarative apps and isolated code plugins. */

export const DECLARATIVE_APP_PROTOCOL_VERSION = "1.0" as const;

export type ProtocolVersion = typeof DECLARATIVE_APP_PROTOCOL_VERSION;
export type AppId = string;
export type PluginId = string;
export type CapabilityName =
  | "ui.render"
  | "ui.navigation"
  | "storage.kv"
  | "storage.files"
  | "network.fetch"
  | "events.subscribe"
  | "platform.commands"
  | "platform.queries"
  | "secrets.read"
  | "identity.read";

export interface DeclarativeAppManifest {
  protocol: ProtocolVersion;
  appId: AppId;
  version: string;
  displayName: string;
  description?: string;
  entrypoints: {
    ui?: DeclarativeUiEntrypoint;
    plugin?: IsolatedPluginEntrypoint;
  };
  requestedCapabilities: CapabilityRequest[];
  quotas?: QuotaPolicy;
  lifecycle?: LifecyclePolicy;
  metadata?: Record<string, string>;
}

export interface DeclarativeUiEntrypoint {
  kind: "declarative-ui";
  root: UiNode;
  routes?: UiRoute[];
}

export interface IsolatedPluginEntrypoint {
  kind: "isolated-code-plugin";
  runtime: "sandbox-v1";
  artifact: { uri: string; sha256: string };
  rpc: { transport: "host-message"; methods: PluginMethod[] };
}

export interface UiRoute {
  id: string;
  path: string;
  title: string;
  root: string;
}

/**
 * One union member per discriminant literal: TypeScript only narrows a discriminated union when
 * each member carries a single literal `type`, so grouped discriminants would leave consumers
 * unable to reach `props`/`children`/`name`. The accepted shapes are unchanged.
 */
export type UiNode =
  | { type: "stack"; id?: string; children: UiNode[] }
  | { type: "row"; id?: string; children: UiNode[] }
  | { type: "column"; id?: string; children: UiNode[] }
  | { type: "text"; id: string; props: Record<string, JsonValue>; bindings?: Record<string, string> }
  | { type: "button"; id: string; props: Record<string, JsonValue>; bindings?: Record<string, string> }
  | { type: "input"; id: string; props: Record<string, JsonValue>; bindings?: Record<string, string> }
  | { type: "table"; id: string; props: Record<string, JsonValue>; bindings?: Record<string, string> }
  | { type: "slot"; id: string; name: string };

export interface PluginMethod {
  name: string;
  description?: string;
  inputSchema?: JsonObject;
  outputSchema?: JsonObject;
  requiredCapabilities?: CapabilityName[];
}

export interface CapabilityRequest {
  name: CapabilityName;
  reason: string;
  constraints?: CapabilityConstraints;
  optional?: boolean;
}

export interface CapabilityConstraints {
  resources?: string[];
  methods?: string[];
  domains?: string[];
  eventTypes?: string[];
  maxBytesPerRequest?: number;
}

export interface CapabilityGrant {
  name: CapabilityName;
  decision: "granted" | "denied";
  constraints?: CapabilityConstraints;
  expiresAt?: string;
  revokedAt?: string;
  grantedBy: "platform" | "administrator" | "user";
}

export interface GrantSet {
  appId: AppId;
  manifestVersion: string;
  grants: CapabilityGrant[];
  issuedAt: string;
  expiresAt?: string;
}

export interface QuotaPolicy {
  maxMemoryMb?: number;
  maxCpuMsPerMinute?: number;
  maxWallTimeMsPerInvocation?: number;
  maxConcurrentInvocations?: number;
  maxRpcMessageBytes?: number;
  maxStorageBytes?: number;
  maxNetworkRequestsPerMinute?: number;
}

export interface LifecyclePolicy {
  startupTimeoutMs?: number;
  shutdownTimeoutMs?: number;
  restart: "never" | "on-failure" | "always";
  maxRestarts?: number;
}

export type AppLifecycleState =
  | "registered"
  | "starting"
  | "running"
  | "stopping"
  | "stopped"
  | "failed"
  | "suspended";

export interface AppLifecycleEvent {
  protocol: ProtocolVersion;
  type: "lifecycle";
  eventId: string;
  appId: AppId;
  state: AppLifecycleState;
  occurredAt: string;
  reason?: string;
  restartCount?: number;
}

export interface RpcRequest<TParams extends JsonValue = JsonValue> {
  protocol: ProtocolVersion;
  type: "request";
  id: string;
  method: string;
  params?: TParams;
  deadlineAt?: string;
}

export interface RpcResponse<TResult extends JsonValue = JsonValue> {
  protocol: ProtocolVersion;
  type: "response";
  id: string;
  result: TResult;
}

export interface RpcError {
  code: string;
  message: string;
  details?: JsonValue;
}

export interface RpcErrorResponse {
  protocol: ProtocolVersion;
  type: "error";
  id: string;
  error: RpcError;
}

export interface RpcNotification<TParams extends JsonValue = JsonValue> {
  protocol: ProtocolVersion;
  type: "notification";
  method: string;
  params?: TParams;
}

export type RpcMessage = RpcRequest | RpcResponse | RpcErrorResponse | RpcNotification;

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

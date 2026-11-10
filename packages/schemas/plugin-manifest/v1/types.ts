/**
 * Типы манифеста плагина и протокола plugin-rpc/1 (F0).
 *
 * Чистые типы: без импортов из apps/web и без рантайм-зависимостей (кроме
 * константы версии протокола).
 * Источник истины — docs/ARCHITECTURE/plugin-architecture-complete.md (v4).
 * Зеркало контрактов: packages/schemas/plugin-manifest/v1/{schema,protocol}.json
 * и apps/api/src/prodavan/domain/plugins/types.py.
 */

export const PLUGIN_RPC_PROTOCOL = "plugin-rpc/1" as const;
export type PluginRpcProtocol = typeof PLUGIN_RPC_PROTOCOL;

/** Идентификатор плагина: ^[a-z][a-z0-9_]*$ (нормализуется валидатором). */
export type PluginId = string;

/** SemVer 2.0.0 (major.minor.patch с опциональными prerelease/build). */
export type SemVer = string;

/** Digest OCI-артефакта: sha256 + 64 hex. Вычисляется платформой, не манифестом. */
export type ArtifactDigest = string;

/** Только типизированные адаптеры gateway. db.direct отсутствует: БД через DSN (§2 v4). */
export type Capability =
  | "cache"
  | "objects"
  | "docs"
  | "events"
  | "jobs"
  | "pods"
  | "mcp"
  | "secrets"
  | "network";

export type MigrationEngine = "liquibase" | "flyway" | "alembic";

/** Режим egress-политики (§6.3 v4), хранится в grant в БД, а не в токене. */
export type EgressMode = "none" | "webhooks-only" | "full";

/** Статус экземпляра плагина (доменный, не статус Kubernetes Pod). */
export type PluginInstanceStatus =
  | "installed"
  | "active"
  | "suspended"
  | "revoked"
  | "quarantined";

/** Исход регистрации версии в реестре (AppVersion/RegistryOutcome). */
export type RegistryOutcome = "applied" | "conflict" | "quarantined" | "missing";

/**
 * Ссылка на долгоживущий CapabilityGrant в БД, а не встроенный allowlist.
 * Истечение короткого JWT не должно пересобирать сетевые правила (§6.1 v4).
 */
export type EgressRef = string;

export interface FrontendEntrypoint {
  /** Относительный путь к точке входа в версионированной статике. */
  entry: string;
  /** Абсолютный путь на отдельном static-origin (без cookie и API). */
  base_path: string;
}

export interface BackendEntrypoint {
  port: number;
  /** /health поднимается только после успешного apply миграций. */
  health: string;
}

export interface MigrationsDeclaration {
  engine: MigrationEngine;
  /** Путь к changelog внутри подписанного OCI-артефакта. */
  path: string;
}

/**
 * Манифест плагина (подписан как OCI annotation).
 *
 * artifact_digest и signature намеренно отсутствуют: их нельзя включать в
 * подписываемый документ (самореференция). Встроенный egress allowlist запрещён —
 * только egress_ref.
 */
export type RpcHttpMethod = "GET" | "POST";

/** Typed operation from signed manifest; generic URL/proxy запрещены. */
export interface RpcOperationDeclaration {
  readonly capability: {
    readonly id: string;
    readonly version: string;
  };
  readonly operation: string;
  readonly http_method: RpcHttpMethod;
  /** Только /api/... без host/query/fragment/dot-segments. */
  readonly backend_path: string;
  /** Разрешение отдельной public-операции; route_path всё равно обязателен в public_pages. */
  readonly public?: boolean;
}

export type McpInputSchemaType =
  | "object"
  | "array"
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "null";

/** Data-only JSON Schema subset; runtime code, URL and secret fields отсутствуют. */
export interface McpInputSchema {
  readonly type: McpInputSchemaType;
  readonly title?: string;
  readonly description?: string;
  readonly properties?: { readonly [name: string]: McpInputSchema };
  readonly required?: readonly string[];
  readonly additionalProperties?: boolean;
  readonly items?: McpInputSchema;
  readonly minProperties?: number;
  readonly maxProperties?: number;
  readonly minItems?: number;
  readonly maxItems?: number;
  readonly uniqueItems?: boolean;
  readonly minLength?: number;
  readonly maxLength?: number;
  readonly pattern?: string;
  readonly minimum?: number;
  readonly maximum?: number;
  readonly exclusiveMinimum?: number;
  readonly exclusiveMaximum?: number;
  readonly enum?: readonly JsonPrimitive[];
  readonly const?: JsonPrimitive;
}

export interface McpObjectInputSchema extends McpInputSchema {
  readonly type: "object";
}

export interface McpToolDeclaration {
  readonly name: string;
  readonly description: string;
  readonly version: SemVer;
  readonly input_schema: McpObjectInputSchema;
}

export interface McpServerDeclaration {
  readonly server_id: string;
  readonly tools: readonly McpToolDeclaration[];
}

/** Exact current pod executor scope tuple; commands, args и pod ids отсутствуют. */
export interface PodAllowlistEntry {
  readonly plugin_instance_id: string;
  readonly project_id: string;
  readonly job_id: string;
}

export interface PodsDeclaration {
  readonly allowlist: readonly PodAllowlistEntry[];
}

export type SecretDeliveryMode = "k8s_secret";

/** Canonical opaque reference; никогда не plaintext и не delivery Secret data. */
export type SecretRef = string;

export interface SecretDeliveryDeclaration {
  readonly mode: SecretDeliveryMode;
  readonly allowlist: readonly SecretRef[];
}

export interface PluginManifest {
  plugin_id: PluginId;
  version: SemVer;
  protocol: PluginRpcProtocol;
  frontend: FrontendEntrypoint;
  backend: BackendEntrypoint;
  capabilities: Capability[];
  migrations: MigrationsDeclaration;
  egress_ref: EgressRef;
  /** Явно объявленные страницы без сессии; [] означает «публичных страниц нет». */
  public_pages: string[];
  /** Входящие webhook-маршруты. */
  webhooks: string[];
  min_platform_version: SemVer;
  /** Optional frontend/backend allowlist; infra capabilities и grant не расширяются. */
  rpc_operations?: readonly RpcOperationDeclaration[];
  /** Signed data-only discovery declarations; отсутствие означает пустой список. */
  mcp_servers?: readonly McpServerDeclaration[];
  /** Optional exact pod scopes; отсутствие означает fail-closed executor access. */
  pods?: PodsDeclaration;
  /** Optional reference-only k8s secret delivery; отсутствие означает отсутствие delivery. */
  secret_delivery?: SecretDeliveryDeclaration;
}

/** Код решения gateway (свой enum, по идее application/capability_gateway_service.py). */
export type DecisionCode =
  | "allowed"
  | "invalid_request"
  | "capability_unknown"
  | "capability_not_requested"
  | "identity_denied"
  | "scope_denied"
  | "grant_missing"
  | "grant_not_yet_issued"
  | "grant_expired"
  | "grant_revoked"
  | "quota_exceeded"
  | "adapter_missing"
  | "adapter_failed";

/** Код ошибки валидации манифеста (domain/plugins/validation.py). */
export type ManifestValidationErrorCode =
  | "schema_invalid"
  | "not_an_object"
  | "schema_unavailable"
  | "plugin_id_empty"
  | "plugin_id_invalid"
  | "version_invalid"
  | "protocol_invalid"
  | "capability_unknown"
  | "capability_duplicate"
  | "self_reference_forbidden"
  | "embedded_egress_forbidden"
  | "egress_ref_invalid"
  | "min_platform_version_invalid"
  | "frontend_invalid"
  | "backend_invalid"
  | "migrations_invalid"
  | "public_pages_invalid"
  | "webhooks_invalid"
  | "rpc_operations_invalid"
  | "mcp_servers_invalid"
  | "pods_invalid"
  | "secret_delivery_invalid"
  | "unknown_field"
  | "missing_field";

/** Egress-политика из grant: режим + разрешённые хосты/пути. */
export interface EgressPolicy {
  mode: EgressMode;
  allowed_hosts: string[];
  allowed_paths: string[];
}

export interface CapabilityGrant {
  grant_id: string;
  plugin_id: PluginId;
  plugin_instance_id: string;
  capabilities: Capability[];
  egress_ref: EgressRef;
  egress_policy: EgressPolicy;
  quota_ref: string;
  artifact_digest: ArtifactDigest;
  revoked_at: string | null;
}

/**
 * Набор полей PluginClaims (§4.2 v4). Только типы, без криптографии.
 * Токен несёт grant_id и короткий TTL; egress/quotas живут в grant в БД.
 */
export interface PluginClaims {
  iss: "agentscale-plugins";
  aud: "capability-gateway";
  typ: "plugin_capability";
  sub: string;
  company_id: string;
  cabinet_id: string | null;
  project_id: string | null;
  plugin_id: PluginId;
  plugin_instance_id: string;
  manifest_version: SemVer;
  artifact_digest: ArtifactDigest;
  grant_id: string;
  scopes: Capability[];
  quota_ref: string;
  jti: string;
  exp: number;
}

// ─────────────────────────── plugin-rpc/1 ───────────────────────────

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

/**
 * Общие обязательные поля закрытого конверта. Origin здесь намеренно отсутствует:
 * доверие определяется только MessageEvent.origin + exact WindowProxy, а для
 * opaque sandbox дополнительно обязательными nonce/instance_id/artifact_digest.
 */
export interface PluginMessageBase {
  protocol: PluginRpcProtocol;
  plugin_id: PluginId;
  version: SemVer;
  artifact_digest: ArtifactDigest;
  instance_id: string;
  nonce: string;
}

/** host -> plugin: только после первого iframe load. */
export interface HostReadyMessage extends PluginMessageBase {
  type: "host.ready";
}

/** plugin -> host: обязательное эхо identity/digest/instance/nonce. */
export interface PluginReadyMessage extends PluginMessageBase {
  type: "plugin.ready";
}

export interface PluginRequestContext {
  company_id?: string;
  cabinet_id?: string;
  project_id?: string;
  session_id?: string;
}

/** host -> plugin: effective grant канала, без capability token. */
export interface GrantReadyMessage extends PluginMessageBase {
  type: "grant.ready";
  capabilities: readonly string[];
  scope: PluginRequestContext;
  expires_at: string;
}

export type PluginRpcMethod = "capability.call" | "host.navigate" | "host.resize";

export interface CapabilityRef {
  id: string;
  version: string;
}

export interface PluginHostNavigateInput {
  /** Только внутренний absolute path без origin, query, fragment и '..'. */
  path: string;
}

export interface PluginHostResizeInput {
  height: number;
}

interface PluginRpcRequestBase extends PluginMessageBase {
  type: "plugin.rpc.request";
  request_id: string;
  context: PluginRequestContext;
}

export interface CapabilityCallRequest extends PluginRpcRequestBase {
  method: "capability.call";
  capability: CapabilityRef;
  idempotency_key: string;
  input: JsonValue;
}

export interface HostNavigateRequest extends PluginRpcRequestBase {
  method: "host.navigate";
  input: PluginHostNavigateInput;
  idempotency_key?: string;
}

export interface HostResizeRequest extends PluginRpcRequestBase {
  method: "host.resize";
  input: PluginHostResizeInput;
  idempotency_key?: string;
}

/** Двунаправленный closed request union; generic proxy/fetch URL отсутствует. */
export type PluginRpcRequest =
  | CapabilityCallRequest
  | HostNavigateRequest
  | HostResizeRequest;

export type PluginRpcRequestFor<TMethod extends PluginRpcMethod> = Extract<
  PluginRpcRequest,
  { method: TMethod }
>;

export type PluginRpcErrorCode =
  | "ORIGIN_MISMATCH"
  | "PROTOCOL_UNSUPPORTED"
  | "HANDSHAKE_MISMATCH"
  | "HANDSHAKE_TIMEOUT"
  | "NONCE_MISMATCH"
  | "UNKNOWN_METHOD"
  | "CAPABILITY_DENIED"
  | "DUPLICATE_REQUEST_ID"
  | "PAYLOAD_TOO_LARGE"
  | "SCOPE_DENIED"
  | "GRANT_EXPIRED"
  | "INVALID_ENVELOPE"
  | "REQUEST_FAILED"
  | "REQUEST_TIMEOUT";

/** Тексты ошибок не содержат токенов, SQL, внутренних URL и stack traces. */
export interface PluginRpcError {
  code: PluginRpcErrorCode;
  message: string;
  trace_id: string;
}

interface PluginRpcResponseBase extends PluginMessageBase {
  type: "plugin.rpc.response";
  request_id: string;
}

export interface PluginRpcSuccessResponse extends PluginRpcResponseBase {
  ok: true;
  result: JsonValue;
}

export interface PluginRpcFailureResponse extends PluginRpcResponseBase {
  ok: false;
  error: PluginRpcError;
}

export type PluginRpcResponse = PluginRpcSuccessResponse | PluginRpcFailureResponse;

export interface PluginErrorMessage extends PluginMessageBase {
  type: "plugin.error";
  request_id?: string;
  error: PluginRpcError;
}

export type PluginRpcMessage =
  | HostReadyMessage
  | PluginReadyMessage
  | GrantReadyMessage
  | PluginRpcRequest
  | PluginRpcResponse
  | PluginErrorMessage;

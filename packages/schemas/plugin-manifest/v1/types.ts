/**
 * Типы манифеста плагина и протокола plugin-rpc/1 (F0).
 *
 * Чистые типы: без импортов из apps/web и без рантайм-зависимостей.
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

/**
 * Ссылка на долгоживущий CapabilityGrant в БД, а не встроенный allowlist.
 * Истечение короткого JWT не должно пересобирать сетевые правила (§6.1 v4).
 */
export type EgressRef = string;

/** Режим egress-политики, хранится в CapabilityGrant, а не в токене. */
export type EgressMode = "none" | "webhooks-only" | "full";

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
}

/** Статус экземпляра плагина (доменный, не статус Kubernetes Pod). */
export type PluginInstanceStatus =
  | "installed"
  | "active"
  | "suspended"
  | "revoked"
  | "quarantined";

/** Исход регистрации версии в реестре (AppVersion/RegistryOutcome). */
export type RegistryOutcome = "applied" | "conflict" | "quarantined" | "missing";

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
 * Проверка event.origin: точное совпадение с зарегистрированным static-origin.
 * opaque origin ("null") отклоняется; источник сверяется с MessageEvent.origin.
 */
export interface MessageOrigin {
  origin: string;
  source?: "iframe";
}

/** Общие поля закрытого конверта plugin-rpc/1. */
export interface PluginMessageBase {
  protocol: PluginRpcProtocol;
  plugin_id: PluginId;
  version: SemVer;
  artifact_digest: ArtifactDigest;
  instance_id: string;
  nonce: string;
  event?: MessageOrigin;
}

/** plugin -> host: первое сообщение handshake. */
export interface PluginReadyMessage extends PluginMessageBase {
  type: "plugin.ready";
}

export type PluginRpcMethod = "capability.call" | "host.navigate" | "host.resize";

export interface PluginRequestContext {
  company_id?: string;
  cabinet_id?: string;
  project_id?: string;
  session_id?: string;
}

export interface CapabilityRef {
  id: string;
  version: string;
}

/** host -> plugin: только объявленные typed-методы, без capability token. */
export interface PluginRpcRequest extends PluginMessageBase {
  type: "plugin.rpc.request";
  request_id: string;
  method: PluginRpcMethod;
  capability?: CapabilityRef;
  context: PluginRequestContext;
  idempotency_key?: string;
  input: JsonValue;
}

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
  | "INVALID_ENVELOPE";

/** Тексты ошибок не содержат токенов, SQL, внутренних URL и stack traces. */
export interface PluginRpcError {
  code: PluginRpcErrorCode;
  message: string;
  trace_id: string;
}

export interface PluginRpcResponse extends PluginMessageBase {
  type: "plugin.rpc.response";
  request_id: string;
  ok: boolean;
  result?: JsonValue;
  error?: PluginRpcError;
}

export interface PluginErrorMessage extends PluginMessageBase {
  type: "plugin.error";
  request_id?: string;
  error: PluginRpcError;
}

export type PluginRpcMessage =
  | PluginReadyMessage
  | PluginRpcRequest
  | PluginRpcResponse
  | PluginErrorMessage;

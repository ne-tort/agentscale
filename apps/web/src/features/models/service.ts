import type {
  AiKeyProbe,
  AvailableAiKey,
  LiveModel,
  LiveModelsResponse,
  ModelProject,
  ModelsService,
  ModelsServiceOptions,
  ModelsServiceRequestOptions,
  ProjectAiSelectionPatch,
} from "./types";

export interface ModelsApiErrorShape {
  code?: string;
  title?: string;
  detail?: string;
  status?: number;
  trace_id?: string;
}

export class ModelsServiceError extends Error {
  readonly code: string;
  readonly status: number;
  readonly traceId?: string;
  readonly retryable: boolean;

  constructor(message: string, details: ModelsApiErrorShape = {}) {
    super(message);
    this.name = "ModelsServiceError";
    this.code = details.code ?? "MODELS_REQUEST_FAILED";
    this.status = details.status ?? 0;
    this.traceId = details.trace_id;
    this.retryable = this.status === 0 || this.status === 408 || this.status === 429 || this.status >= 500;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function booleanValue(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function safeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0).map((item) => item.trim());
}

/**
 * Keep only public probe metadata. In particular, never copy secret_ref,
 * secret_ref_prefix, token, key, or arbitrary provider payloads into UI state.
 */
export function parsePublicProbe(value: unknown): AiKeyProbe | undefined {
  if (!isRecord(value)) return undefined;
  const status = stringValue(value.status);
  if (!status) return undefined;
  return {
    status,
    ...(stringValue(value.kind) ? { kind: stringValue(value.kind) } : {}),
    ...(numberValue(value.latency_ms) !== undefined ? { latency_ms: numberValue(value.latency_ms) } : {}),
    ...(numberValue(value.http_status) !== undefined ? { http_status: numberValue(value.http_status) } : {}),
    ...(stringValue(value.error_code) ? { error_code: stringValue(value.error_code) } : {}),
    ...(stringValue(value.default_model) ? { default_model: stringValue(value.default_model) } : {}),
    models: safeStringList(value.models),
    ...(stringValue(value.checked_at) ? { checked_at: stringValue(value.checked_at) } : {}),
    ...(booleanValue(value.stale) !== undefined ? { stale: booleanValue(value.stale) } : {}),
  };
}

export function parseAvailableAiKey(value: unknown): AvailableAiKey | undefined {
  if (!isRecord(value)) return undefined;
  const id = stringValue(value.id);
  const name = stringValue(value.name);
  const provider = stringValue(value.provider);
  if (!id || !name || !provider) return undefined;
  return {
    id,
    name,
    provider,
    ...(stringValue(value.api_kind) ? { api_kind: stringValue(value.api_kind) } : {}),
    ...(stringValue(value.catalog_entry_id) ? { catalog_entry_id: stringValue(value.catalog_entry_id) } : {}),
    ...(stringValue(value.status) ? { status: stringValue(value.status) } : {}),
    has_secret: value.has_secret === true,
    ...(parsePublicProbe(value.last_probe) ? { last_probe: parsePublicProbe(value.last_probe) } : {}),
  };
}

export function parseAvailableAiKeys(value: unknown): AvailableAiKey[] {
  if (!isRecord(value) || !Array.isArray(value.items)) return [];
  return value.items.map(parseAvailableAiKey).filter((item): item is AvailableAiKey => Boolean(item));
}

export function parseLiveModel(value: unknown): LiveModel | undefined {
  if (!isRecord(value)) return undefined;
  const id = stringValue(value.id);
  if (!id) return undefined;
  return {
    id,
    label: stringValue(value.label) ?? id,
    ...(booleanValue(value.catalog_matched) !== undefined ? { catalog_matched: booleanValue(value.catalog_matched) } : {}),
    ...(numberValue(value.input_price_usd_per_mtok) !== undefined ? { input_price_usd_per_mtok: numberValue(value.input_price_usd_per_mtok) } : {}),
    ...(numberValue(value.output_price_usd_per_mtok) !== undefined ? { output_price_usd_per_mtok: numberValue(value.output_price_usd_per_mtok) } : {}),
    ...(numberValue(value.max_context_tokens) !== undefined ? { max_context_tokens: numberValue(value.max_context_tokens) } : {}),
    ...(stringValue(value.publisher) ? { publisher: stringValue(value.publisher) } : {}),
    ...(stringValue(value.released_at) ? { released_at: stringValue(value.released_at) } : {}),
  };
}

export function parseLiveModels(value: unknown): LiveModelsResponse {
  if (!isRecord(value)) return { models: [] };
  const rawModels = Array.isArray(value.models) ? value.models : [];
  return {
    models: rawModels.map(parseLiveModel).filter((item): item is LiveModel => Boolean(item)),
    ...(stringValue(value.default_model) ? { default_model: stringValue(value.default_model) } : {}),
    ...(stringValue(value.source) ? { source: stringValue(value.source) } : {}),
    ...(booleanValue(value.stale) !== undefined ? { stale: booleanValue(value.stale) } : {}),
    ...(stringValue(value.checked_at) ? { checked_at: stringValue(value.checked_at) } : {}),
    ...(stringValue(value.error_code) ? { error_code: stringValue(value.error_code) } : {}),
  };
}

export function parseModelProject(value: unknown): ModelProject {
  if (!isRecord(value) || !stringValue(value.id) || !stringValue(value.name)) {
    throw new ModelsServiceError("Сервер вернул неполные данные проекта.", { code: "INVALID_PROJECT_RESPONSE" });
  }
  return {
    id: stringValue(value.id)!,
    name: stringValue(value.name)!,
    ...(stringValue(value.agent_provider) ? { agent_provider: stringValue(value.agent_provider) } : {}),
    ...(stringValue(value.resolved_ai_key_id) ? { resolved_ai_key_id: stringValue(value.resolved_ai_key_id) } : {}),
  };
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

export function modelsProjectPath(projectId: string, suffix = ""): string {
  return `/projects/${encode(projectId)}${suffix}`;
}

export function modelsApiError(response: Response, payload: unknown): ModelsServiceError {
  const data = isRecord(payload) ? payload : {};
  const nested = isRecord(data.error) ? data.error : data;
  const details: ModelsApiErrorShape = {
    code: stringValue(nested.code),
    title: stringValue(nested.title),
    detail: stringValue(nested.detail),
    status: response.status,
    trace_id: stringValue(nested.trace_id),
  };
  return new ModelsServiceError(details.detail ?? details.title ?? `Запрос завершился с кодом ${response.status}.`, details);
}

export function createModelsService(options: ModelsServiceOptions = {}): ModelsService {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetcher = options.fetcher ?? fetch;

  async function readJson(response: Response): Promise<unknown> {
    try {
      return await response.json();
    } catch {
      return undefined;
    }
  }

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
    } catch (cause) {
      if (isRecord(cause) && cause.name === "AbortError") throw cause;
      throw new ModelsServiceError(cause instanceof Error ? `Не удалось связаться с API: ${cause.message}` : "Не удалось связаться с API.", { code: "OFFLINE" });
    }
    if (!response.ok) throw modelsApiError(response, await readJson(response));
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  return {
    getProject: async (projectId: string, requestOptions?: ModelsServiceRequestOptions) =>
      parseModelProject(await request<unknown>(modelsProjectPath(projectId), { signal: requestOptions?.signal })),
    listAvailableKeys: async (projectId: string, requestOptions?: ModelsServiceRequestOptions) =>
      parseAvailableAiKeys(await request<unknown>(modelsProjectPath(projectId, "/ai-keys/available"), { signal: requestOptions?.signal })),
    listLiveModels: async (projectId: string, requestOptions?: ModelsServiceRequestOptions) =>
      parseLiveModels(await request<unknown>(modelsProjectPath(projectId, "/models/live"), { signal: requestOptions?.signal })),
    patchProject: async (projectId: string, patch: ProjectAiSelectionPatch) =>
      parseModelProject(await request<unknown>(modelsProjectPath(projectId), { method: "PATCH", body: JSON.stringify(patch) })),
  };
}

export function createDefaultModelsService(): ModelsService {
  return createModelsService();
}

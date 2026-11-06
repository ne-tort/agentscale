export type ModelId = string;

export type ProbeStatus = "ok" | "error" | "unavailable" | "none" | "stale" | string;

/** Public key metadata returned by GET /projects/{id}/ai-keys/available. */
export interface AvailableAiKey {
  id: string;
  name: string;
  provider: string;
  api_kind?: string;
  catalog_entry_id?: string;
  status?: string;
  has_secret: boolean;
  last_probe?: AiKeyProbe;
}

/** Probe fields safe for the browser. Secret material is deliberately absent. */
export interface AiKeyProbe {
  status: ProbeStatus;
  kind?: string;
  latency_ms?: number;
  http_status?: number;
  error_code?: string;
  default_model?: string;
  models: string[];
  checked_at?: string;
  stale?: boolean;
}

/** One model returned by GET /projects/{id}/models/live. */
export interface LiveModel {
  id: ModelId;
  label: string;
  catalog_matched?: boolean;
  input_price_usd_per_mtok?: number;
  output_price_usd_per_mtok?: number;
  max_context_tokens?: number;
  publisher?: string;
  released_at?: string;
}

export interface LiveModelsResponse {
  models: LiveModel[];
  default_model?: ModelId;
  source?: string;
  stale?: boolean;
  checked_at?: string;
  error_code?: string;
}

/** Project fields needed by the provider/key/model surface. */
export interface ModelProject {
  id: string;
  name: string;
  agent_provider?: string;
  resolved_ai_key_id?: string;
}

/** The project PATCH contract intentionally excludes model: backend has no project model field. */
export interface ProjectAiSelectionPatch {
  agent_provider: string;
  resolved_ai_key_id: string;
}

export interface ModelsServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: typeof fetch;
}

export interface ModelsServiceRequestOptions {
  signal?: AbortSignal;
}

export interface ModelsService {
  getProject(projectId: string, options?: ModelsServiceRequestOptions): Promise<ModelProject>;
  listAvailableKeys(projectId: string, options?: ModelsServiceRequestOptions): Promise<AvailableAiKey[]>;
  listLiveModels(projectId: string, options?: ModelsServiceRequestOptions): Promise<LiveModelsResponse>;
  patchProject(projectId: string, patch: ProjectAiSelectionPatch): Promise<ModelProject>;
}

export interface ModelsPickerProps {
  projectId: string;
  service?: ModelsService;
  /** Optional server data lets a host avoid a duplicate project GET. */
  initialProject?: ModelProject;
  initialKeys?: AvailableAiKey[];
  initialModels?: LiveModelsResponse;
  /** Current model comes from the chat host because PATCH project does not support a model field. */
  initialModelId?: ModelId;
  /** The host owns the confirmed model/session persistence boundary. */
  onModelChange?: (projectId: string, modelId: ModelId) => void | Promise<void>;
  onProjectSaved?: (project: ModelProject) => void;
}

export type KeyAvailabilityTone = "positive" | "warning" | "danger" | "neutral";

export interface KeyAvailability {
  tone: KeyAvailabilityTone;
  label: string;
  detail: string;
}

export type ProbePresentation = "verified" | "stale" | "error" | "unavailable" | "unchecked";

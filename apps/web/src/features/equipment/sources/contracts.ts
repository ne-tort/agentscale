export type CatalogRowBody = Record<string, unknown>;

export interface EquipmentCatalogRow {
  readonly row_id: string;
  readonly body: CatalogRowBody;
}

export interface EquipmentCatalogMetaDocument {
  readonly slug: string;
  readonly body: unknown;
}

export interface EquipmentCatalogAction {
  readonly id: string;
  readonly kind: string;
  readonly enabled: boolean;
}

export interface EquipmentCatalogMappingField {
  readonly key: string;
  readonly label: string;
  readonly required: boolean;
}

export interface EquipmentCatalogMetadata {
  readonly mappingFields: readonly EquipmentCatalogMappingField[];
  readonly indexAction?: EquipmentCatalogAction;
  readonly probeAction?: EquipmentCatalogAction;
  readonly databasesAction?: EquipmentCatalogAction;
  readonly tablesAction?: EquipmentCatalogAction;
  readonly pauseSupported: boolean;
}

export type CatalogFreshness = "fresh" | "stale" | "unknown" | "not-applicable";

/**
 * Why the freshness is what it is. A code rather than a sentence: the screen
 * turns it into words in the reader's language.
 */
export type CatalogFreshnessReason = "never-indexed" | "error" | "local" | "stale" | "fresh";

export interface CatalogFreshnessState {
  readonly freshness: CatalogFreshness;
  readonly reason: CatalogFreshnessReason;
  readonly lastGoodLabel?: string;
}

export interface EquipmentCatalogModule {
  readonly id: string;
  readonly name: string;
}

export interface EquipmentCatalogWorkspace {
  readonly module: EquipmentCatalogModule;
  readonly metadata: EquipmentCatalogMetadata;
  readonly rows: readonly EquipmentCatalogRow[];
}

export interface CatalogFileRef extends Record<string, unknown> {
  readonly asset_id: string;
  readonly version_id: string;
  readonly storage_key: string;
  readonly filename: string;
}

export interface CatalogSecretRef {
  readonly secret_ref: string;
}

export type CatalogOperation = "probe" | "databases" | "tables" | "index";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function rowsFrom(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.filter(isRecord);
  if (isRecord(value) && Array.isArray(value.items)) return value.items.filter(isRecord);
  return [];
}

function labels(value: unknown, fallback: string): string {
  if (typeof value === "string" && value.trim()) return value;
  if (isRecord(value)) {
    for (const key of ["ru", "en"]) {
      const candidate = value[key];
      if (typeof candidate === "string" && candidate.trim()) return candidate;
    }
  }
  return fallback;
}

function actionForKind(actions: readonly Record<string, unknown>[], kind: string): EquipmentCatalogAction | undefined {
  const action = actions.find((item) => item.kind === kind
    && isRecord(item.params)
    && item.params.table_slug === "catalogs");
  if (!action || typeof action.id !== "string") return undefined;
  return { id: action.id, kind, enabled: action.enabled !== false };
}

export function parseEquipmentCatalogMetadata(documents: readonly EquipmentCatalogMetaDocument[]): EquipmentCatalogMetadata {
  const bySlug = new Map(documents.map((document) => [document.slug, document.body]));
  const views = rowsFrom(bySlug.get("views"));
  const actions = rowsFrom(bySlug.get("actions"));
  const settings = views.find((view) => view.slug === "catalogs_settings");
  const ui = isRecord(settings?.ui_json) ? settings.ui_json : {};
  const fields = Array.isArray(ui.fields) ? ui.fields.filter(isRecord) : [];
  const mapField = fields.find((field) => field.column === "column_map");
  const schema = Array.isArray(mapField?.schema) ? mapField.schema.filter(isRecord) : [];
  const pauseSupported = fields.some((field) => field.column === "paused" && field.widget === "pause_toggle");
  const mappingFields = schema.flatMap((field) => typeof field.key === "string"
    ? [{ key: field.key, label: labels(field.label, field.key), required: field.required === true }]
    : []);
  return {
    mappingFields,
    indexAction: actionForKind(actions, "content.index_opensearch"),
    probeAction: actionForKind(actions, "content.probe_remote_sql"),
    databasesAction: actionForKind(actions, "content.list_remote_sql_databases"),
    tablesAction: actionForKind(actions, "content.list_remote_sql_tables"),
    pauseSupported,
  };
}

export function catalogSourceColumns(body: CatalogRowBody): string[] {
  const raw = body.columns_json;
  let columns: unknown = raw;
  if (typeof raw === "string") {
    try { columns = JSON.parse(raw) as unknown; }
    catch { return []; }
  }
  if (!Array.isArray(columns)) return [];
  // Предикат должен возвращать boolean: результат проверки на непустоту приводится явно.
  return [...new Set(columns.filter((item): item is string => typeof item === "string" && item.trim() !== "").map((item) => item.trim()))];
}

export function catalogColumnMap(body: CatalogRowBody): Record<string, string> {
  if (!isRecord(body.column_map)) return {};
  return Object.fromEntries(Object.entries(body.column_map).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
}

export function catalogMappingReady(mapping: Record<string, string>, fields: readonly EquipmentCatalogMappingField[]): boolean {
  const required = fields.filter((field) => field.required);
  return required.length > 0 && required.every((field) => Boolean(mapping[field.key]?.trim()));
}

export function secretRef(value: unknown): CatalogSecretRef | undefined {
  if (!isRecord(value) || typeof value.secret_ref !== "string" || !value.secret_ref.trim()) return undefined;
  return { secret_ref: value.secret_ref };
}

export function hasFileRef(value: unknown): value is CatalogFileRef {
  return isRecord(value) && typeof value.asset_id === "string" && typeof value.version_id === "string"
    && typeof value.storage_key === "string" && typeof value.filename === "string";
}

function parseDate(value: unknown): number | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  const result = Date.parse(value);
  return Number.isFinite(result) ? result : undefined;
}

export function catalogFreshness(body: CatalogRowBody, now = Date.now()): CatalogFreshnessState {
  const timestamp = parseDate(body.last_indexed_at);
  const lastGoodLabel = timestamp === undefined ? undefined : new Date(timestamp).toLocaleString();
  if (body.status === "error") return {
    freshness: timestamp === undefined ? "unknown" : "stale",
    reason: timestamp === undefined ? "never-indexed" : "error",
    ...(lastGoodLabel ? { lastGoodLabel } : {}),
  };
  if (body.source_kind !== "remote") return {
    freshness: timestamp === undefined ? "unknown" : "not-applicable",
    reason: timestamp === undefined ? "never-indexed" : "local",
    ...(lastGoodLabel ? { lastGoodLabel } : {}),
  };
  if (timestamp === undefined) return { freshness: "unknown", reason: "never-indexed" };
  const interval = typeof body.reindex_interval_hours === "number" && Number.isFinite(body.reindex_interval_hours)
    ? Math.max(1, body.reindex_interval_hours) : 24;
  const stale = now - timestamp >= interval * 60 * 60 * 1000;
  return {
    freshness: stale ? "stale" : "fresh",
    reason: stale ? "stale" : "fresh",
    ...(lastGoodLabel ? { lastGoodLabel } : {}),
  };
}

export function sanitizeCatalogMessage(value: unknown, sensitiveValues: readonly string[] = []): string {
  let text = typeof value === "string" ? value : "Операция не выполнена.";
  for (const secret of sensitiveValues) {
    if (secret) text = text.split(secret).join("[секрет скрыт]");
  }
  return text.replace(/(postgres(?:ql)?:\/\/)[^\s/@]+(?::[^\s/@]*)?@/gi, "$1***@")
    .replace(/(?:file|vault):\/\/[^\s"'<>]+/gi, "[секрет скрыт]")
    .replace(/(["']?secret_ref["']?\s*:\s*["'])[^"']+(["'])/gi, "$1[секрет скрыт]$2")
    .slice(0, 500);
}

export function displayCatalogValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Не указано";
  if (typeof value === "object") return "Настроено";
  return String(value);
}
/**
 * Two envelopes, one browser wire.
 *
 * The bridge has an **application** envelope (`RunStreamEvent`: `run_id`,
 * `sequence`) and a **wire** envelope (`web_api.SseEventEnvelope`: stable
 * UUIDv5 `event_id`, `event_type`, `schema_version`, payload). They must never
 * be mixed: an application cursor `run_id:sequence` is not a wire id, and a
 * UUID must never be parsed as a sequence or compared lexicographically.
 *
 * Conversion between the two is allowed only when a trusted run/sequence index
 * proves that the pair exists in the authorized stream. Without such an index
 * the module raises a typed `UpstreamStreamMalformed` instead of synthesizing
 * an id or a sequence — a fabricated replay identity is worse than a refusal,
 * because it makes a non-replayable stream look resumable.
 *
 * Every error carries only a stable reason code: no frame payload, no exception
 * text and no internal identifier ever reaches the message (redaction policy,
 * web-cutover-implementation §5).
 */

/** Wire schema version this client understands. */
export const WIRE_SCHEMA_VERSION = 1;

/** RFC 4122 URL namespace, the namespace used by `web_api.uuid5_from_run`. */
export const WIRE_EVENT_ID_NAMESPACE = "6ba7b811-9dad-11d1-80b4-00c04fd430c8";

export const WIRE_EVENT_TYPES = [
  "run_started",
  "text_delta",
  "transcript_block",
  "tool_call",
  "tool_result",
  "status",
  "error",
  "done",
  "keepalive",
] as const;

export type WireEventType = (typeof WIRE_EVENT_TYPES)[number];

/** Application envelope identity: `run_id` + `sequence`, no wire UUID. */
export type ApplicationEnvelope = {
  runId?: string;
  sequence?: number;
  eventType: string;
  payload: Record<string, unknown>;
  sessionId?: string;
  projectId?: string;
};

/** Canonical browser wire envelope. */
export type WireEnvelope = {
  eventId: string;
  eventType: WireEventType;
  schemaVersion: number;
  projectId?: string;
  sessionId?: string;
  runId?: string;
  sequence?: number;
  payload: Record<string, unknown>;
  resumeAfter?: number;
  occurredAt?: string;
};

/**
 * Trusted mapping between application `(run_id, sequence)` pairs and wire
 * event ids for one authorized stream. The browser may obtain it from a
 * detached-turn context or from a server-provided replay index; it must never
 * be inferred from frame order.
 */
export type RunSequenceIndex = {
  /** Run the authorized request is bound to, when known. */
  readonly runId?: string;
  /** True only when `(runId, sequence)` is a known event of this run. */
  has(runId: string, sequence: number): boolean;
  /** Deterministic reverse lookup; no derivation by arithmetic. */
  findByEventId(eventId: string): { runId: string; sequence: number } | undefined;
};

export type MalformedStage = "before_headers" | "after_headers";

/**
 * Typed protocol failure for an upstream chat stream.
 *
 * `stage` tells the transport whether the failure happened before the response
 * was opened (map to a JSON HTTP 502 `UPSTREAM_STREAM_MALFORMED`) or after
 * (emit a redacted terminal error frame and close).
 */
export class UpstreamStreamMalformed extends Error {
  readonly code = "upstream_stream_malformed";

  constructor(readonly reason: string, readonly stage: MalformedStage = "after_headers") {
    super(`upstream_stream_malformed:${reason}`);
    this.name = "UpstreamStreamMalformed";
  }
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const UUID_V5_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const WIRE_EVENT_TYPE_SET: ReadonlySet<string> = new Set(WIRE_EVENT_TYPES);

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isUuid(value: string): boolean {
  return UUID_PATTERN.test(value);
}

/** UUID version 5: the only shape that can be a deterministic replay id. */
export function isUuidV5(value: string): boolean {
  return UUID_V5_PATTERN.test(value);
}

export function isWireEventType(value: string): value is WireEventType {
  return WIRE_EVENT_TYPE_SET.has(value);
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === "string" && value !== "" ? value : undefined;
}

function nonNegativeInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}

/**
 * Classify a decoded JSON body. Ambiguity (both `type`/`data` and
 * `event_type`/`payload` markers) is a protocol error, not a preference order.
 */
export function envelopeKind(raw: unknown): "wire" | "application" | "unknown" {
  if (!isRecord(raw)) return "unknown";
  const wireMarker = "event_id" in raw || "event_type" in raw || "payload" in raw || "schema_version" in raw;
  const applicationMarker = "type" in raw || "data" in raw;
  if (wireMarker && applicationMarker) return "unknown";
  if (wireMarker) return "wire";
  if (applicationMarker) return "application";
  return "unknown";
}

export function readApplicationEnvelope(raw: unknown): ApplicationEnvelope {
  if (!isRecord(raw)) throw new UpstreamStreamMalformed("not_an_object");
  // Legacy type precedence: `raw.type`, then `raw.event`, then `data.type`.
  const eventType = nonEmptyString(raw.type)
    ?? nonEmptyString(raw.event)
    ?? (isRecord(raw.data) ? nonEmptyString(raw.data.type) : undefined);
  if (eventType === undefined) throw new UpstreamStreamMalformed("missing_type");
  if (!isRecord(raw.data)) throw new UpstreamStreamMalformed("invalid_data");
  const data = raw.data;
  // Identity may sit on the envelope for legacy frames (seq under `seq`, and
  // `run_id`/`session_id`/`project_id` on either level). It is read for cursor
  // comparison only; it is never used to synthesize a replay id.
  let runId: string | undefined;
  const rawRunId = raw.run_id ?? data.run_id;
  if (rawRunId !== null && rawRunId !== undefined) {
    runId = nonEmptyString(rawRunId);
    if (runId === undefined) throw new UpstreamStreamMalformed("invalid_run_id");
  }
  let sequence: number | undefined;
  const rawSeq = raw.seq ?? data.seq;
  if (rawSeq !== null && rawSeq !== undefined) {
    sequence = nonNegativeInteger(rawSeq);
    if (sequence === undefined) throw new UpstreamStreamMalformed("invalid_sequence");
  }
  const sessionId = nonEmptyString(raw.session_id) ?? nonEmptyString(data.session_id);
  const projectId = nonEmptyString(raw.project_id) ?? nonEmptyString(data.project_id);
  return {
    eventType,
    payload: data,
    ...(runId === undefined ? {} : { runId }),
    ...(sequence === undefined ? {} : { sequence }),
    ...(sessionId === undefined ? {} : { sessionId }),
    ...(projectId === undefined ? {} : { projectId }),
  };
}

/**
 * Validate a canonical wire envelope. `null` run/sequence is accepted only so
 * that a terminal non-replayable error can be surfaced; callers must use
 * `assertReplayable` before treating the envelope as a replayable event.
 */
export function readWireEnvelope(
  raw: unknown,
  options: { transportId?: string; transportEvent?: string } = {},
): WireEnvelope {
  if (!isRecord(raw)) throw new UpstreamStreamMalformed("not_an_object");

  const eventId = nonEmptyString(raw.event_id);
  if (eventId === undefined || !isUuid(eventId)) throw new UpstreamStreamMalformed("invalid_event_id");

  const eventType = nonEmptyString(raw.event_type);
  if (eventType === undefined || !isWireEventType(eventType)) throw new UpstreamStreamMalformed("unknown_event_type");

  const schemaVersion = nonNegativeInteger(raw.schema_version);
  if (schemaVersion === undefined || schemaVersion < 1) throw new UpstreamStreamMalformed("invalid_schema_version");
  if (schemaVersion !== WIRE_SCHEMA_VERSION) throw new UpstreamStreamMalformed("unsupported_schema_version");

  if (!isRecord(raw.payload)) throw new UpstreamStreamMalformed("invalid_payload");

  const runId = raw.run_id === undefined || raw.run_id === null ? undefined : nonEmptyString(raw.run_id);
  if (raw.run_id !== undefined && raw.run_id !== null && runId === undefined) throw new UpstreamStreamMalformed("invalid_run_id");

  const sequence = raw.sequence === undefined || raw.sequence === null ? undefined : nonNegativeInteger(raw.sequence);
  if (raw.sequence !== undefined && raw.sequence !== null && sequence === undefined) throw new UpstreamStreamMalformed("invalid_sequence");

  const resumeAfter = raw.resume_after === undefined || raw.resume_after === null ? undefined : nonNegativeInteger(raw.resume_after);
  if (raw.resume_after !== undefined && raw.resume_after !== null && resumeAfter === undefined) throw new UpstreamStreamMalformed("invalid_resume_after");

  const projectId = raw.project_id === undefined || raw.project_id === null ? undefined : nonEmptyString(raw.project_id);
  const sessionId = raw.session_id === undefined || raw.session_id === null ? undefined : nonEmptyString(raw.session_id);
  const occurredAt = raw.occurred_at === undefined || raw.occurred_at === null ? undefined : nonEmptyString(raw.occurred_at);

  // Transport fields must agree with the envelope: a browser that trusts only
  // one of them would advance the cursor with a value the other contradicts.
  if (options.transportId !== undefined && options.transportId !== "" && options.transportId !== eventId) {
    throw new UpstreamStreamMalformed("transport_id_mismatch");
  }
  if (options.transportEvent !== undefined && options.transportEvent !== "" && options.transportEvent !== eventType) {
    throw new UpstreamStreamMalformed("transport_event_mismatch");
  }

  return {
    eventId,
    eventType,
    schemaVersion,
    payload: raw.payload,
    ...(projectId === undefined ? {} : { projectId }),
    ...(sessionId === undefined ? {} : { sessionId }),
    ...(runId === undefined ? {} : { runId }),
    ...(sequence === undefined ? {} : { sequence }),
    ...(resumeAfter === undefined ? {} : { resumeAfter }),
    ...(occurredAt === undefined ? {} : { occurredAt }),
  };
}

/**
 * Replayable run events must carry both identities; a null identity is only
 * allowed for an explicitly non-replayable terminal protocol error.
 */
export function assertReplayable(envelope: WireEnvelope): { runId: string; sequence: number } {
  if (envelope.runId === undefined) throw new UpstreamStreamMalformed("run_identity_missing");
  if (envelope.sequence === undefined) throw new UpstreamStreamMalformed("sequence_missing");
  return { runId: envelope.runId, sequence: envelope.sequence };
}

function uuidToBytes(uuid: string): Uint8Array {
  const hex = uuid.replace(/-/g, "");
  const bytes = new Uint8Array(16);
  for (let index = 0; index < 16; index += 1) bytes[index] = Number.parseInt(hex.slice(index * 2, index * 2 + 2), 16);
  return bytes;
}

function bytesToUuid(bytes: Uint8Array): string {
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

async function sha1(bytes: Uint8Array): Promise<Uint8Array> {
  const subtle = typeof globalThis.crypto === "undefined" ? undefined : globalThis.crypto.subtle;
  if (!subtle) throw new UpstreamStreamMalformed("sha1_unavailable");
  // Copy into a fresh ArrayBuffer-backed view: environment typings differ on
  // whether a shared-buffer view is an acceptable BufferSource.
  const input = new Uint8Array(bytes);
  const digest = await subtle.digest("SHA-1", input);
  return new Uint8Array(digest);
}

/**
 * Canonical wire id for an application event: UUIDv5 over
 * `prodavan:web-run:{run_id}:{sequence}` in the RFC 4122 URL namespace, which
 * is exactly what `web_api.uuid5_from_run` computes server-side.
 */
export async function wireEventId(runId: string, sequence: number): Promise<string> {
  const namespace = uuidToBytes(WIRE_EVENT_ID_NAMESPACE);
  const name = new TextEncoder().encode(`prodavan:web-run:${runId}:${sequence}`);
  const input = new Uint8Array(namespace.length + name.length);
  input.set(namespace, 0);
  input.set(name, namespace.length);
  const hash = await sha1(input);
  const bytes = hash.slice(0, 16);
  bytes[6] = (bytes[6] & 0x0f) | 0x50;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  return bytesToUuid(bytes);
}

/**
 * Application envelope -> wire envelope.
 *
 * Refuses to run without a trusted index and without a proven
 * `(run_id, sequence)` membership: this is the only place allowed to derive a
 * UUID, and it never invents a run id or a sequence number.
 */
export async function toWireEnvelope(
  application: ApplicationEnvelope,
  context: {
    projectId: string;
    sessionId?: string;
    index?: RunSequenceIndex;
    occurredAt?: string;
    schemaVersion?: number;
  },
): Promise<WireEnvelope> {
  const runId = nonEmptyString(application.runId);
  if (runId === undefined) throw new UpstreamStreamMalformed("run_identity_missing");
  const sequence = nonNegativeInteger(application.sequence);
  if (sequence === undefined) throw new UpstreamStreamMalformed("sequence_missing");
  if (!isWireEventType(application.eventType)) throw new UpstreamStreamMalformed("unknown_event_type");
  if (nonEmptyString(context.projectId) === undefined) throw new UpstreamStreamMalformed("missing_project_scope");

  const index = context.index;
  if (!index) throw new UpstreamStreamMalformed("run_index_missing");
  if (index.runId !== undefined && index.runId !== runId) throw new UpstreamStreamMalformed("run_scope_mismatch");
  if (!index.has(runId, sequence)) throw new UpstreamStreamMalformed("run_sequence_untrusted");

  const eventId = await wireEventId(runId, sequence);
  const resolved = index.findByEventId(eventId);
  if (!resolved || resolved.runId !== runId || resolved.sequence !== sequence) {
    throw new UpstreamStreamMalformed("run_index_inconsistent");
  }

  return {
    eventId,
    eventType: application.eventType,
    schemaVersion: context.schemaVersion ?? WIRE_SCHEMA_VERSION,
    projectId: context.projectId,
    payload: application.payload,
    runId,
    sequence,
    resumeAfter: sequence,
    ...(application.sessionId === undefined && context.sessionId === undefined ? {} : { sessionId: application.sessionId ?? context.sessionId }),
    ...(context.occurredAt === undefined ? {} : { occurredAt: context.occurredAt }),
  };
}

/**
 * Resolve a wire `Last-Event-ID` (UUID) to the application identity.
 *
 * Never derives `(run_id, sequence)` from a UUID by any rule other than a
 * deterministic index lookup: UUIDs are not sequences and must not be ordered
 * or compared as numbers.
 */
export function resolveWireCursor(eventId: string, index?: RunSequenceIndex): { runId: string; sequence: number } {
  if (!isUuid(eventId)) throw new UpstreamStreamMalformed("invalid_event_id");
  if (!index) throw new UpstreamStreamMalformed("run_index_missing");
  if (!isUuidV5(eventId)) throw new UpstreamStreamMalformed("event_id_unresolved");
  const resolved = index.findByEventId(eventId);
  if (!resolved) throw new UpstreamStreamMalformed("event_id_unresolved");
  if (index.runId !== undefined && index.runId !== resolved.runId) throw new UpstreamStreamMalformed("run_scope_mismatch");
  if (!index.has(resolved.runId, resolved.sequence)) throw new UpstreamStreamMalformed("event_id_unresolved");
  return resolved;
}

/**
 * Value for the next `Last-Event-ID`.
 *
 * A transport id is reused verbatim. Without one, only a full application
 * identity is acceptable: with a trusted index it becomes the canonical UUID,
 * without one it stays the documented `run_id:sequence` compatibility form. A
 * bare sequence is never sent — it is ambiguous across runs.
 */
export async function lastEventIdFor(
  position: { eventId?: string; runId?: string; sequence?: number },
  index?: RunSequenceIndex,
): Promise<string | undefined> {
  const eventId = nonEmptyString(position.eventId);
  if (eventId !== undefined) {
    if (!isUuid(eventId)) throw new UpstreamStreamMalformed("invalid_event_id");
    return eventId;
  }
  const runId = nonEmptyString(position.runId);
  const sequence = nonNegativeInteger(position.sequence);
  if (runId === undefined || sequence === undefined) return undefined;
  if (!index) return `${runId}:${sequence}`;
  if (index.runId !== undefined && index.runId !== runId) throw new UpstreamStreamMalformed("run_scope_mismatch");
  if (!index.has(runId, sequence)) throw new UpstreamStreamMalformed("run_sequence_untrusted");
  return wireEventId(runId, sequence);
}

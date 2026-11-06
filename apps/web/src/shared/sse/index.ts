/**
 * Shared SSE transport primitives for the browser chat stream.
 *
 * `parser`  — spec-compliant incremental frame parser (data/event/id/retry,
 *             comments, CRLF, multi-line data, chunk boundaries, EOF flush).
 * `cursor`  — replay cursor: commit after successful handling, dedupe on
 *             replay, refuse cross-run frames.
 * `envelope`— application (`run_id:seq`) vs wire (UUIDv5) envelope separation
 *             and the trusted-index gate for any conversion.
 */

export { SseParser, SseParserFinalizedError, parseSseFrames } from "./parser";
export type { SseFrame, SseParseBatch } from "./parser";

export { SseCursor, streamIdentity } from "./cursor";
export type { CursorCommitResult, CursorVerdict, StreamIdentity } from "./cursor";

export {
  UpstreamStreamMalformed,
  WIRE_EVENT_ID_NAMESPACE,
  WIRE_EVENT_TYPES,
  WIRE_SCHEMA_VERSION,
  assertReplayable,
  envelopeKind,
  isRecord,
  isUuid,
  isUuidV5,
  isWireEventType,
  lastEventIdFor,
  readApplicationEnvelope,
  readWireEnvelope,
  resolveWireCursor,
  toWireEnvelope,
  wireEventId,
} from "./envelope";
export type {
  ApplicationEnvelope,
  MalformedStage,
  RunSequenceIndex,
  WireEnvelope,
  WireEventType,
} from "./envelope";

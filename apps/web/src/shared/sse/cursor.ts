/**
 * Replay cursor for the chat SSE stream.
 *
 * The cursor is the only place that decides whether a frame may reach the UI
 * and which `Last-Event-ID` must be sent on reconnect. Three invariants from
 * the bridge contract are enforced here:
 *
 * 1. A cursor commits **only after** the frame was handed to the consumer and
 *    the consumer did not fail. The service calls `commit()` after `yield`.
 * 2. A malformed or non-congruent frame never moves the cursor: `inspect()`
 *    reports it and `commit()` refuses it.
 * 3. Replay with `Last-Event-ID` must not duplicate already processed frames:
 *    a repeated event id or a non-increasing sequence within the same run is
 *    reported as `duplicate`/`stale` and dropped without a UI effect.
 *
 * An identity is replayable only when it carries a transport event id or a
 * full application identity (`run_id` + `sequence`). A bare sequence cannot be
 * committed: per the contract it is a session-local counter, not a canonical
 * run sequence, so it must never be turned into a cursor by itself.
 */

export type StreamIdentity = {
  /** Transport event id (`id:` / `event_id`), opaque; never parsed as a number. */
  eventId?: string;
  /** Application run identity. */
  runId?: string;
  /** Application sequence within that run (exclusive lower bound semantics). */
  sequence?: number;
};

export type CursorVerdict =
  | { kind: "deliver" }
  | { kind: "duplicate"; reason: "event_id" | "sequence" }
  | { kind: "stale"; reason: "sequence" }
  | { kind: "conflict"; reason: "run_mismatch" | "event_id_reused" }
  | { kind: "unverifiable"; reason: "run_missing" | "sequence_missing" };

export type CursorCommitResult =
  | { committed: true; lastEventId?: string }
  | { committed: false; reason: "not_replayable"; verdict: CursorVerdict }
  | { committed: false; reason: "rejected"; verdict: CursorVerdict };

function nonEmpty(value: string | undefined): string | undefined {
  return value === undefined || value === "" ? undefined : value;
}

function validSequence(value: number | undefined): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}

/**
 * How many handled transport ids a cursor remembers.
 *
 * A reconnect that echoes `Last-Event-ID` may replay a short window of frames,
 * not just the single last one, so deduplication by event id must not depend on
 * "is this the most recent id". The map is bounded: forgetting the oldest id
 * only downgrades its classification from `duplicate` to `stale`/`deliver`, it
 * never turns an unhandled frame into a duplicate.
 */
const HANDLED_EVENT_ID_LIMIT = 256;

function normalizeIdentity(identity: StreamIdentity): StreamIdentity {
  const eventId = nonEmpty(identity.eventId);
  const runId = nonEmpty(identity.runId);
  const sequence = validSequence(identity.sequence);
  return {
    ...(eventId === undefined ? {} : { eventId }),
    ...(runId === undefined ? {} : { runId }),
    ...(sequence === undefined ? {} : { sequence }),
  };
}

/**
 * True when a field was declared but carries no usable value.
 *
 * `seq: -1` and `seq: 1.5` are not "the frame did not declare a sequence" — they
 * declare an invalid one, and the contract refuses to turn either into a cursor
 * (`not_replayable`) rather than reading the frame as an incomplete identity.
 */
function declaredInvalidIdentity(identity: StreamIdentity): boolean {
  if (identity.eventId !== undefined && nonEmpty(identity.eventId) === undefined) return true;
  return identity.sequence !== undefined && validSequence(identity.sequence) === undefined;
}

export class SseCursor {
  private current: StreamIdentity = {};
  private header: string | undefined;
  /** Every transport id already handed to the consumer, oldest first. */
  private readonly handledEventIds = new Map<string, StreamIdentity>();

  constructor(seed?: StreamIdentity) {
    if (seed) {
      const identity = normalizeIdentity(seed);
      if (identity.eventId !== undefined || (identity.runId !== undefined && identity.sequence !== undefined)) {
        this.current = identity;
        this.header = identity.eventId ?? `${identity.runId}:${identity.sequence}`;
        if (identity.eventId !== undefined) this.rememberHandled(identity);
      }
    }
  }

  /** Last committed identity (defensive copy). */
  get position(): Readonly<StreamIdentity> {
    return { ...this.current };
  }

  /**
   * Exact value a reconnect must send as `Last-Event-ID`.
   *
   * It is always something the upstream itself produced (a transport id or a
   * `run_id:sequence` pair). No UUID or sequence is synthesized here.
   */
  get lastEventId(): string | undefined {
    return this.header;
  }

  /** True when the cursor can continue a stream (has a replayable position). */
  get isReplayable(): boolean {
    return this.header !== undefined;
  }

  inspect(candidate: StreamIdentity): CursorVerdict {
    const identity = normalizeIdentity(candidate);
    const current = this.current;

    if (identity.eventId !== undefined) {
      if (identity.eventId === current.eventId) {
        const sameRun = identity.runId === undefined || current.runId === undefined || identity.runId === current.runId;
        const sameSequence = identity.sequence === undefined || current.sequence === undefined || identity.sequence === current.sequence;
        return sameRun && sameSequence
          ? { kind: "duplicate", reason: "event_id" }
          : { kind: "conflict", reason: "event_id_reused" };
      }
      // A replay window may resend frames handled before the current one. The
      // id is only "already handled" when it is not the position we are at
      // (that case is above); a reused id with a contradictory identity is
      // still a conflict, never a silent duplicate.
      const handled = this.handledEventIds.get(identity.eventId);
      if (handled !== undefined) {
        const sameRun = identity.runId === undefined || handled.runId === undefined || identity.runId === handled.runId;
        const sameSequence = identity.sequence === undefined || handled.sequence === undefined || identity.sequence === handled.sequence;
        return sameRun && sameSequence
          ? { kind: "duplicate", reason: "event_id" }
          : { kind: "conflict", reason: "event_id_reused" };
      }
    }

    if (identity.runId !== undefined && current.runId !== undefined && identity.runId !== current.runId) {
      return { kind: "conflict", reason: "run_mismatch" };
    }

    if (identity.runId !== undefined && current.runId === identity.runId && identity.sequence !== undefined && current.sequence !== undefined) {
      if (identity.sequence === current.sequence) return { kind: "duplicate", reason: "sequence" };
      if (identity.sequence < current.sequence) return { kind: "stale", reason: "sequence" };
    }

    // Once a run is known, a frame that does not name its run cannot be proven
    // to belong to that run; it may be shown but must never move the cursor.
    if (current.runId !== undefined && identity.runId === undefined) return { kind: "unverifiable", reason: "run_missing" };
    if (identity.runId !== undefined && identity.sequence === undefined) return { kind: "unverifiable", reason: "sequence_missing" };

    return { kind: "deliver" };
  }

  /**
   * Advance the cursor. Call only after the frame was successfully processed
   * by the consumer. Refuses duplicates, stale frames, cross-run frames and
   * identities that cannot be replayed.
   */
  commit(candidate: StreamIdentity): CursorCommitResult {
    // An identity field that was declared but could not be normalized (empty
    // id, negative or fractional sequence) is an *invalid* identity, not an
    // incomplete one: there is no sequence to reason about, so it is reported as
    // not replayable instead of as a verdict about a value that never existed.
    if (declaredInvalidIdentity(candidate)) return { committed: false, reason: "not_replayable", verdict: { kind: "deliver" } };
    const verdict = this.inspect(candidate);
    if (verdict.kind !== "deliver") return { committed: false, reason: "rejected", verdict };
    const identity = normalizeIdentity(candidate);
    const replayable = identity.eventId !== undefined || (identity.runId !== undefined && identity.sequence !== undefined);
    // A complete run identity is replayable even without a transport id; a bare
    // sequence or a sequence without a run is not.
    if (!replayable) return { committed: false, reason: "not_replayable", verdict };
    this.current = identity;
    this.header = identity.eventId ?? `${identity.runId}:${identity.sequence}`;
    if (identity.eventId !== undefined) this.rememberHandled(identity);
    return { committed: true, lastEventId: this.header };
  }

  reset(): void {
    this.current = {};
    this.header = undefined;
    this.handledEventIds.clear();
  }

  private rememberHandled(identity: StreamIdentity): void {
    const eventId = identity.eventId;
    if (eventId === undefined) return;
    // Re-inserting moves the id to the newest position of the bounded window.
    this.handledEventIds.delete(eventId);
    this.handledEventIds.set(eventId, identity);
    while (this.handledEventIds.size > HANDLED_EVENT_ID_LIMIT) {
      const oldest = this.handledEventIds.keys().next();
      if (oldest.done) break;
      this.handledEventIds.delete(oldest.value);
    }
  }
}

/**
 * Build an identity from transport fields plus optional application fields.
 *
 * The builder deliberately does **not** normalize: declaring `eventId: ""` or
 * `sequence: -1` is a statement about the frame, and the cursor must be able to
 * tell it apart from a frame that declared nothing. Normalization happens
 * inside `SseCursor`, which owns the replay semantics.
 */
export function streamIdentity(input: {
  eventId?: string | undefined;
  runId?: string | undefined;
  sequence?: number | undefined;
}): StreamIdentity {
  return {
    ...(input.eventId === undefined ? {} : { eventId: input.eventId }),
    ...(input.runId === undefined ? {} : { runId: input.runId }),
    ...(input.sequence === undefined ? {} : { sequence: input.sequence }),
  };
}

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

export class SseCursor {
  private current: StreamIdentity = {};
  private header: string | undefined;

  constructor(seed?: StreamIdentity) {
    if (seed) {
      const identity = normalizeIdentity(seed);
      if (identity.eventId !== undefined || (identity.runId !== undefined && identity.sequence !== undefined)) {
        this.current = identity;
        this.header = identity.eventId ?? `${identity.runId}:${identity.sequence}`;
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

    if (identity.eventId !== undefined && current.eventId !== undefined && identity.eventId === current.eventId) {
      const sameRun = identity.runId === undefined || current.runId === undefined || identity.runId === current.runId;
      const sameSequence = identity.sequence === undefined || current.sequence === undefined || identity.sequence === current.sequence;
      return sameRun && sameSequence
        ? { kind: "duplicate", reason: "event_id" }
        : { kind: "conflict", reason: "event_id_reused" };
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
    const verdict = this.inspect(candidate);
    if (verdict.kind !== "deliver") return { committed: false, reason: "rejected", verdict };
    const identity = normalizeIdentity(candidate);
    const replayable = identity.eventId !== undefined || (identity.runId !== undefined && identity.sequence !== undefined);
    if (!replayable) return { committed: false, reason: "not_replayable", verdict };
    this.current = identity;
    this.header = identity.eventId ?? `${identity.runId}:${identity.sequence}`;
    return { committed: true, lastEventId: this.header };
  }

  reset(): void {
    this.current = {};
    this.header = undefined;
  }
}

/** Build an identity from transport fields plus optional application fields. */
export function streamIdentity(input: {
  eventId?: string | undefined;
  runId?: string | undefined;
  sequence?: number | undefined;
}): StreamIdentity {
  return normalizeIdentity({
    ...(input.eventId === undefined ? {} : { eventId: input.eventId }),
    ...(input.runId === undefined ? {} : { runId: input.runId }),
    ...(input.sequence === undefined ? {} : { sequence: input.sequence }),
  });
}

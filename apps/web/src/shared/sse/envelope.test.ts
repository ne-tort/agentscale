import { describe, expect, it } from "vitest";
import {
  UpstreamStreamMalformed,
  WIRE_EVENT_ID_NAMESPACE,
  assertReplayable,
  envelopeKind,
  isUuidV5,
  lastEventIdFor,
  readApplicationEnvelope,
  readWireEnvelope,
  resolveWireCursor,
  toWireEnvelope,
  wireEventId,
} from "./envelope";
import type { RunSequenceIndex } from "./envelope";

/**
 * Trusted index fixture: only the pairs listed here are known events.
 *
 * A real replay index is durable evidence of both directions — `has(run, seq)`
 * says a pair exists, and `findByEventId(uuid)` resolves the canonical UUIDv5 of
 * that same pair. The fixture therefore precomputes the reverse map from the
 * known sequences; an empty reverse map would claim pairs exist that it cannot
 * resolve, and `toWireEnvelope` correctly treats that as an inconsistent index.
 */
async function makeIndex(runId: string, sequences: number[]): Promise<RunSequenceIndex> {
  const known = new Set(sequences);
  const byId = new Map<string, number>();
  for (const sequence of sequences) byId.set(await wireEventId(runId, sequence), sequence);
  return {
    runId,
    has(candidateRun, sequence) {
      return candidateRun === runId && known.has(sequence);
    },
    findByEventId(eventId) {
      const sequence = byId.get(eventId);
      return sequence === undefined ? undefined : { runId, sequence };
    },
  };
}

function wireEnvelope(overrides: Record<string, unknown> = {}) {
  return {
    event_id: "2f8c1c0e-6f2c-5a4e-9a1f-2c1b0d9e8a77",
    event_type: "text_delta",
    schema_version: 1,
    occurred_at: "2026-01-01T00:00:00Z",
    project_id: "project-1",
    session_id: "session-1",
    run_id: "run-1",
    sequence: 7,
    payload: { text: "hello" },
    resume_after: 7,
    ...overrides,
  };
}

describe("envelope separation", () => {
  it("classifies application and wire shapes without mixing them", () => {
    expect(envelopeKind({ type: "text_delta", data: {} })).toBe("application");
    expect(envelopeKind(wireEnvelope())).toBe("wire");
    // A body carrying both shapes is ambiguous, not "prefer wire".
    expect(envelopeKind({ type: "text_delta", data: {}, event_id: "x", event_type: "text_delta" })).toBe("unknown");
    expect(envelopeKind(null)).toBe("unknown");
    expect(envelopeKind([1, 2])).toBe("unknown");
  });

  it("reads an application envelope with aliases and identity", () => {
    expect(readApplicationEnvelope({ type: "delta", data: { text: "x" }, run_id: "run-1", seq: 3 })).toEqual({
      eventType: "delta",
      payload: { text: "x" },
      runId: "run-1",
      sequence: 3,
    });
    // `type` falls back to `event`, then to `data.type`.
    expect(readApplicationEnvelope({ event: "block", data: {} }).eventType).toBe("block");
    expect(readApplicationEnvelope({ data: { type: "tool_result" } }).eventType).toBe("tool_result");
    // A frame without any `seq` parses, but carries no sequence. The fixture
    // must still name its type: an application frame with no type at all is
    // malformed (asserted below), so `{ data: {} }` could never reach this
    // assertion — the previous expectation was self-contradictory.
    expect(readApplicationEnvelope({ type: "delta", data: {} }).sequence).toBeUndefined();
    expect(() => readApplicationEnvelope({ type: "delta", data: {}, seq: -1 })).toThrow(UpstreamStreamMalformed);
    expect(() => readApplicationEnvelope({ type: "delta", data: {}, seq: "7" })).toThrow(UpstreamStreamMalformed);
    expect(() => readApplicationEnvelope({ type: "delta" })).toThrow(UpstreamStreamMalformed);
    expect(() => readApplicationEnvelope({ data: {} })).toThrow(UpstreamStreamMalformed);
  });

  it("validates a wire envelope and keeps transport values consistent", () => {
    const parsed = readWireEnvelope(wireEnvelope(), { transportId: "2f8c1c0e-6f2c-5a4e-9a1f-2c1b0d9e8a77", transportEvent: "text_delta" });
    expect(parsed).toMatchObject({ eventType: "text_delta", runId: "run-1", sequence: 7, resumeAfter: 7 });
    expect(() => readWireEnvelope(wireEnvelope(), { transportId: "other-id" })).toThrow(UpstreamStreamMalformed);
    expect(() => readWireEnvelope(wireEnvelope(), { transportEvent: "done" })).toThrow(UpstreamStreamMalformed);
    expect(() => readWireEnvelope(wireEnvelope({ event_type: "legacy_delta" }))).toThrow(UpstreamStreamMalformed);
    expect(() => readWireEnvelope(wireEnvelope({ schema_version: 2 }))).toThrow(UpstreamStreamMalformed);
    expect(() => readWireEnvelope(wireEnvelope({ event_id: "7" }))).toThrow(UpstreamStreamMalformed);
    expect(() => readWireEnvelope(wireEnvelope({ sequence: -3 }))).toThrow(UpstreamStreamMalformed);
  });

  it("requires run and sequence only for replayable events", () => {
    const envelope = readWireEnvelope(wireEnvelope());
    expect(assertReplayable(envelope)).toEqual({ runId: "run-1", sequence: 7 });
    const terminal = readWireEnvelope(wireEnvelope({ event_type: "error", run_id: null, sequence: null }));
    expect(() => assertReplayable(terminal)).toThrow(UpstreamStreamMalformed);
  });

  it("computes the canonical UUIDv5 for (run_id, sequence)", async () => {
    expect(WIRE_EVENT_ID_NAMESPACE).toBe("6ba7b811-9dad-11d1-80b4-00c04fd430c8");
    const id = await wireEventId("run-1", 7);
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(isUuidV5(id)).toBe(true);
    // Stable for the same pair, different for another sequence or run.
    expect(await wireEventId("run-1", 7)).toBe(id);
    expect(await wireEventId("run-1", 8)).not.toBe(id);
    expect(await wireEventId("run-2", 7)).not.toBe(id);
  });

  it("refuses to synthesize a wire envelope without a trusted index", async () => {
    await expect(toWireEnvelope({ eventType: "text_delta", payload: { text: "x" }, runId: "run-1", sequence: 7 }, { projectId: "project-1" }))
      .rejects.toBeInstanceOf(UpstreamStreamMalformed);
    // An index that does not know the pair refuses too: no sequence is invented.
    await expect(toWireEnvelope(
      { eventType: "text_delta", payload: {}, runId: "run-1", sequence: 9 },
      { projectId: "project-1", index: await makeIndex("run-1", [7]) },
    )).rejects.toBeInstanceOf(UpstreamStreamMalformed);
    // A missing run identity can never be filled from the request scope.
    await expect(toWireEnvelope(
      { eventType: "text_delta", payload: {} },
      { projectId: "project-1", index: await makeIndex("run-1", [7]) },
    )).rejects.toBeInstanceOf(UpstreamStreamMalformed);
  });

  it("converts only pairs proven by the trusted index", async () => {
    const envelope = await toWireEnvelope(
      { eventType: "text_delta", payload: { text: "hi" }, runId: "run-1", sequence: 7 },
      { projectId: "project-1", sessionId: "session-1", index: await makeIndex("run-1", [7]) },
    );
    expect(envelope).toEqual({
      eventId: await wireEventId("run-1", 7),
      eventType: "text_delta",
      schemaVersion: 1,
      projectId: "project-1",
      sessionId: "session-1",
      runId: "run-1",
      sequence: 7,
      payload: { text: "hi" },
      resumeAfter: 7,
    });
    await expect(toWireEnvelope(
      { eventType: "text_delta", payload: {}, runId: "run-2", sequence: 7 },
      { projectId: "project-1", index: await makeIndex("run-1", [7]) },
    )).rejects.toBeInstanceOf(UpstreamStreamMalformed);
  });

  it("rejects an index whose reverse lookup disagrees with the pair", async () => {
    const broken: RunSequenceIndex = {
      runId: "run-1",
      has: () => true,
      findByEventId: () => ({ runId: "run-1", sequence: 99 }),
    };
    await expect(toWireEnvelope(
      { eventType: "text_delta", payload: {}, runId: "run-1", sequence: 7 },
      { projectId: "project-1", index: broken },
    )).rejects.toBeInstanceOf(UpstreamStreamMalformed);
  });

  it("resolves a UUID cursor only through the index and never as a number", async () => {
    const id = await wireEventId("run-1", 7);
    const resolver: RunSequenceIndex = {
      runId: "run-1",
      has: (runId, sequence) => runId === "run-1" && sequence === 7,
      findByEventId: (eventId) => eventId === id ? { runId: "run-1", sequence: 7 } : undefined,
    };
    expect(resolveWireCursor(id, resolver)).toEqual({ runId: "run-1", sequence: 7 });
    expect(() => resolveWireCursor(id, undefined)).toThrow(UpstreamStreamMalformed);
    expect(() => resolveWireCursor("7", resolver)).toThrow(UpstreamStreamMalformed);
    // A UUID absent from the index is unresolved, not "parsed".
    const unresolved = await wireEventId("run-1", 8);
    expect(() => resolveWireCursor(unresolved, resolver)).toThrow(UpstreamStreamMalformed);
    // A non-v5 UUID cannot be a deterministic replay id.
    expect(() => resolveWireCursor("2f8c1c0e-6f2c-4a4e-9a1f-2c1b0d9e8a77", resolver)).toThrow(UpstreamStreamMalformed);
  });

  it("sends an application cursor only when it is fully known", async () => {
    expect(await lastEventIdFor({ runId: "run-1", sequence: 7 })).toBe("run-1:7");
    expect(await lastEventIdFor({ sequence: 7 })).toBeUndefined();
    expect(await lastEventIdFor({ runId: "run-1" })).toBeUndefined();
    const index = await makeIndex("run-1", [7]);
    expect(await lastEventIdFor({ runId: "run-1", sequence: 7 }, index)).toBe(await wireEventId("run-1", 7));
    await expect(lastEventIdFor({ runId: "run-1", sequence: 8 }, index)).rejects.toBeInstanceOf(UpstreamStreamMalformed);
    // A transport id is reused verbatim.
    expect(await lastEventIdFor({ eventId: await wireEventId("run-1", 7) })).toBe(await wireEventId("run-1", 7));
  });
});

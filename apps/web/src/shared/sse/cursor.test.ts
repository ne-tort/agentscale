import { describe, expect, it } from "vitest";
import { SseCursor, streamIdentity } from "./cursor";

describe("SseCursor", () => {
  it("starts empty and is not replayable", () => {
    const cursor = new SseCursor();
    expect(cursor.lastEventId).toBeUndefined();
    expect(cursor.isReplayable).toBe(false);
    expect(cursor.position).toEqual({});
  });

  it("commits a transport id and exposes it as Last-Event-ID", () => {
    const cursor = new SseCursor();
    const result = cursor.commit(streamIdentity({ eventId: "evt-1", runId: "run-1", sequence: 1 }));
    expect(result).toEqual({ committed: true, lastEventId: "evt-1" });
    expect(cursor.lastEventId).toBe("evt-1");
    expect(cursor.position).toEqual({ eventId: "evt-1", runId: "run-1", sequence: 1 });
  });

  it("never advances for a frame that failed before commit", () => {
    const cursor = new SseCursor();
    cursor.commit(streamIdentity({ eventId: "evt-1", runId: "run-1", sequence: 1 }));
    // A malformed frame is reported but never committed: inspect alone is a
    // read-only probe, and the cursor keeps its previous position.
    expect(cursor.inspect(streamIdentity({ eventId: "evt-2", runId: "run-1", sequence: 2 }))).toEqual({ kind: "deliver" });
    expect(cursor.lastEventId).toBe("evt-1");
    expect(cursor.position).toEqual({ eventId: "evt-1", runId: "run-1", sequence: 1 });
  });

  it("does not synthesize a cursor from a bare sequence", () => {
    const cursor = new SseCursor();
    const result = cursor.commit(streamIdentity({ sequence: 5 }));
    expect(result).toEqual({ committed: false, reason: "not_replayable", verdict: { kind: "deliver" } });
    expect(cursor.lastEventId).toBeUndefined();
    expect(cursor.isReplayable).toBe(false);
  });

  it("does not synthesize a run cursor from a run without a sequence", () => {
    const cursor = new SseCursor();
    expect(cursor.commit(streamIdentity({ runId: "run-1" }))).toEqual({
      committed: false,
      reason: "rejected",
      verdict: { kind: "unverifiable", reason: "sequence_missing" },
    });
    expect(cursor.lastEventId).toBeUndefined();
  });

  it("survives a replay without duplicating handled frames", () => {
    const cursor = new SseCursor();
    cursor.commit(streamIdentity({ eventId: "evt-1", runId: "run-1", sequence: 1 }));
    cursor.commit(streamIdentity({ eventId: "evt-2", runId: "run-1", sequence: 2 }));

    // Reconnect delivered the same two frames again.
    expect(cursor.inspect(streamIdentity({ eventId: "evt-1", runId: "run-1", sequence: 1 }))).toEqual({ kind: "duplicate", reason: "event_id" });
    expect(cursor.inspect(streamIdentity({ eventId: "evt-2", runId: "run-1", sequence: 2 }))).toEqual({ kind: "duplicate", reason: "event_id" });
    expect(cursor.commit(streamIdentity({ eventId: "evt-2", runId: "run-1", sequence: 2 })).committed).toBe(false);
    expect(cursor.lastEventId).toBe("evt-2");
    expect(cursor.inspect(streamIdentity({ eventId: "evt-3", runId: "run-1", sequence: 3 }))).toEqual({ kind: "deliver" });
  });

  it("deduplicates by sequence when the transport id differs", () => {
    const cursor = new SseCursor();
    cursor.commit(streamIdentity({ runId: "run-1", sequence: 7 }));
    expect(cursor.inspect(streamIdentity({ runId: "run-1", sequence: 7 }))).toEqual({ kind: "duplicate", reason: "sequence" });
    expect(cursor.inspect(streamIdentity({ runId: "run-1", sequence: 6 }))).toEqual({ kind: "stale", reason: "sequence" });
    expect(cursor.inspect(streamIdentity({ runId: "run-1", sequence: 8 }))).toEqual({ kind: "deliver" });
  });

  it("refuses a frame from another run and keeps the original position", () => {
    const cursor = new SseCursor();
    cursor.commit(streamIdentity({ runId: "run-1", sequence: 7 }));
    expect(cursor.inspect(streamIdentity({ runId: "run-2", sequence: 99 }))).toEqual({ kind: "conflict", reason: "run_mismatch" });
    expect(cursor.commit(streamIdentity({ runId: "run-2", sequence: 99 }))).toEqual({
      committed: false,
      reason: "rejected",
      verdict: { kind: "conflict", reason: "run_mismatch" },
    });
    expect(cursor.lastEventId).toBe("run-1:7");
  });

  it("treats a reused event id with another identity as a conflict", () => {
    const cursor = new SseCursor();
    cursor.commit(streamIdentity({ eventId: "evt-1", runId: "run-1", sequence: 1 }));
    expect(cursor.inspect(streamIdentity({ eventId: "evt-1", runId: "run-2", sequence: 9 }))).toEqual({ kind: "conflict", reason: "event_id_reused" });
  });

  it("does not advance for a frame that cannot be attributed to the known run", () => {
    const cursor = new SseCursor(streamIdentity({ runId: "run-1", sequence: 4 }));
    expect(cursor.inspect(streamIdentity({ eventId: "evt-x" }))).toEqual({ kind: "unverifiable", reason: "run_missing" });
    expect(cursor.commit(streamIdentity({ eventId: "evt-x" }))).toEqual({
      committed: false,
      reason: "rejected",
      verdict: { kind: "unverifiable", reason: "run_missing" },
    });
    expect(cursor.lastEventId).toBe("run-1:4");
  });

  it("seeds from a legacy run:sequence cursor and never from a bare sequence", () => {
    const seeded = new SseCursor(streamIdentity({ runId: "run-1", sequence: 7 }));
    expect(seeded.lastEventId).toBe("run-1:7");
    expect(seeded.inspect(streamIdentity({ runId: "run-1", sequence: 7 }))).toEqual({ kind: "duplicate", reason: "sequence" });

    const bare = new SseCursor(streamIdentity({ sequence: 7 }));
    expect(bare.lastEventId).toBeUndefined();
    expect(bare.isReplayable).toBe(false);
  });

  it("ignores invalid sequences and empty ids", () => {
    const cursor = new SseCursor();
    expect(cursor.commit(streamIdentity({ eventId: "", runId: "run-1", sequence: -1 }))).toEqual({
      committed: false,
      reason: "not_replayable",
      verdict: { kind: "deliver" },
    });
    expect(cursor.lastEventId).toBeUndefined();
    expect(cursor.commit(streamIdentity({ runId: "run-1", sequence: 1.5 })).committed).toBe(false);
  });

  it("resets its position on demand", () => {
    const cursor = new SseCursor();
    cursor.commit(streamIdentity({ eventId: "evt-1" }));
    cursor.reset();
    expect(cursor.lastEventId).toBeUndefined();
    expect(cursor.isReplayable).toBe(false);
  });
});

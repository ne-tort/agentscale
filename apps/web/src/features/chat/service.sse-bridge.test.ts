/**
 * Acceptance artifacts for the SSE bridge wiring in `features/chat/service`.
 *
 * These are written, not executed, during the migration verification freeze.
 * Each test names the contract clause it pins down
 * (`web-sse-bridge-contract.md` §2/§5/§7, `web-cutover-implementation.md` §3–§4).
 */
import { describe, expect, it } from "vitest";
import { CHAT_STREAM_ERROR_CODES, LegacyChatError, LegacyChatService } from "./service";
import type { PendingTurn } from "./types";

const turn: PendingTurn = {
  turnId: "turn-1",
  sessionId: "session-1",
  text: "hello",
  attachmentRefs: [],
  submittedAt: 1,
};

function sse(...frames: string[]): Response {
  return new Response(frames.join(""), { headers: { "Content-Type": "text/event-stream" } });
}

function frame(payload: unknown, transport: { id?: string; event?: string } = {}): string {
  return [
    ...(transport.id === undefined ? [] : [`id: ${transport.id}\n`]),
    ...(transport.event === undefined ? [] : [`event: ${transport.event}\n`]),
    `data: ${JSON.stringify(payload)}\n\n`,
  ].join("");
}

async function drain(service: LegacyChatService, target: PendingTurn = turn, signal?: AbortSignal) {
  const events: Array<{ type: string; data: Record<string, unknown> }> = [];
  let error: unknown;
  try {
    for await (const event of service.streamTurn(target, "project-1", signal)) events.push(event);
  } catch (caught) {
    error = caught;
  }
  return { events, error };
}

describe("chat stream bridge wiring", () => {
  it("parses CRLF, comments and a chunk split inside the last frame", async () => {
    const body = [
      ": keepalive\r\n\r\n",
      "data: {\"type\":\"_session\",\r\n",
      "data: \"data\":{\"session_id\":\"session-1\"}}\r\n\r\n",
      "data: {\"type\":\"text_delta\",\"data\":{\"text\":\"hi\"}}\r\n",
      "\r\n",
    ].join("");
    // Split at an arbitrary byte so the terminator straddles two reads.
    const encoder = new TextEncoder();
    const bytes = encoder.encode(body);
    const splitAt = body.indexOf("\r\n\r\n", 40) + 1;
    const service = new LegacyChatService({ fetchImpl: async () => new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, splitAt));
        controller.enqueue(bytes.slice(splitAt));
        controller.close();
      },
    })) });
    const { events, error } = await drain(service);
    expect(error).toBeUndefined();
    expect(events.map((event) => event.type)).toEqual(["_session", "text_delta"]);
    expect(events[0].data).toEqual({ session_id: "session-1" });
  });

  it("keeps the unterminated final frame at EOF", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => sse(
      frame({ type: "text_delta", data: { text: "a" } }),
      `data: ${JSON.stringify({ type: "_turn_complete", data: {} })}`,
    ) });
    const { events, error } = await drain(service);
    expect(error).toBeUndefined();
    expect(events.map((event) => event.type)).toEqual(["text_delta", "_turn_complete"]);
  });

  it("sends Last-Event-ID on reconnect and drops replayed frames", async () => {
    const calls: Array<Record<string, string>> = [];
    const encoder = new TextEncoder();
    let call = 0;
    const service = new LegacyChatService({ fetchImpl: async (_url, init) => {
      calls.push((init?.headers ?? {}) as Record<string, string>);
      call += 1;
      if (call === 1) {
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode(frame({ type: "text_delta", data: { text: "a" }, seq: 1 }, { id: "evt-1" })));
            controller.error(new TypeError("connection lost"));
          },
        }));
      }
      return sse(
        // The bridge replays from the cursor inclusively before advancing.
        frame({ type: "text_delta", data: { text: "a" }, seq: 1 }, { id: "evt-1" }),
        frame({ type: "text_delta", data: { text: "b" }, seq: 2 }, { id: "evt-2" }),
        frame({ type: "_turn_complete", data: {} }),
      );
    } });

    const first = await drain(service);
    expect((first.error as LegacyChatError).code).toBe(CHAT_STREAM_ERROR_CODES.streamOutcomeUnknown);
    expect(first.events.map((event) => event.data.text ?? event.data)).toEqual(["a", {}]);
    expect(service.lastEventIdFor(turn.turnId)).toBe("evt-1");

    const second = await drain(service);
    expect(second.error).toBeUndefined();
    // `evt-1` is replayed by the server but was already handled: no duplicate.
    expect(second.events.map((event) => event.type)).toEqual(["text_delta", "_turn_complete"]);
    expect(second.events[0].data).toEqual({ text: "b" });
    expect(calls[0]["Last-Event-ID"]).toBeUndefined();
    expect(calls[1]["Last-Event-ID"]).toBe("evt-1");
  });

  it("does not send a cursor after a terminal frame", async () => {
    const headers: Array<Record<string, string>> = [];
    const service = new LegacyChatService({ fetchImpl: async (_url, init) => {
      headers.push((init?.headers ?? {}) as Record<string, string>);
      return sse(
        frame({ type: "text_delta", data: { text: "x" }, seq: 1 }, { id: "evt-1" }),
        frame({ type: "_turn_complete", data: {} }),
      );
    } });

    expect((await drain(service)).error).toBeUndefined();
    expect(service.lastEventIdFor(turn.turnId)).toBeUndefined();
    expect((await drain(service)).error).toBeUndefined();
    expect(headers.map((header) => header["Last-Event-ID"])).toEqual([undefined, undefined]);
  });

  it("never commits a cursor for a legacy run_id:seq pair", async () => {
    // Legacy sequence is session-local: the pair proves no replay boundary, so
    // no Last-Event-ID may be derived from it.
    const headers: Array<Record<string, string>> = [];
    const service = new LegacyChatService({ fetchImpl: async (_url, init) => {
      headers.push((init?.headers ?? {}) as Record<string, string>);
      return sse(
        frame({ type: "text_delta", data: { text: "x" }, run_id: "run-1", seq: 7 }),
        frame({ type: "_turn_complete", data: {} }),
      );
    } });
    const serviceWithPending = service;
    // PendingTurn.sessionId is required: the composer creates the session before the
    // first turn is queued, so a turn without a session cannot reach the transport.
    // This case is only about cursor derivation, hence a normal session-scoped turn.
    const target = { ...turn, turnId: "turn-seq" };
    expect((await drain(serviceWithPending, target)).error).toBeUndefined();
    expect(serviceWithPending.lastEventIdFor("turn-seq")).toBeUndefined();
    expect(headers[0]["Last-Event-ID"]).toBeUndefined();
  });

  it("maps a malformed first frame to a stable 502 with an unknown outcome", async () => {
    // `web-sse-bridge-contract.md` §5: malformed before the first canonical
    // event is HTTP 502 UPSTREAM_STREAM_MALFORMED, not a partial delivery.
    const service = new LegacyChatService({ fetchImpl: async () => sse("data: {\"kind\":\"mystery\"}\n\n") });
    const { events, error } = await drain(service);
    expect(events).toEqual([]);
    expect(error).toBeInstanceOf(LegacyChatError);
    const failure = error as LegacyChatError;
    expect(failure.code).toBe(CHAT_STREAM_ERROR_CODES.upstreamStreamMalformed);
    expect(failure.status).toBe(502);
    expect(failure.retryable).toBe(false);
    expect(failure.unknownOutcome).toBe(true);
  });

  it("maps invalid JSON to the retryable invalid_json code without leaking payload", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => sse("data: {not json\n\n") });
    const { error } = await drain(service);
    const failure = error as LegacyChatError;
    expect(failure.code).toBe(CHAT_STREAM_ERROR_CODES.invalidJson);
    // The message is a fixed UI string: no exception text, no raw frame.
    expect(failure.message).not.toContain("not json");
    expect(failure.retryable).toBe(true);
  });

  it("treats a conflicting transport event name as malformed", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => sse(
      frame({ type: "text_delta", data: { text: "x" } }, { event: "done" }),
    ) });
    const { error } = await drain(service);
    expect((error as LegacyChatError).code).toBe(CHAT_STREAM_ERROR_CODES.upstreamStreamMalformed);
  });

  it("maps a legacy _error frame to the server code and a 403 status", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => sse(
      frame({ type: "_error", data: { code: "FORBIDDEN", detail: "runtime unavailable", status: 403 } }),
      frame({ type: "_turn_complete", data: {} }),
    ) });
    const { error } = await drain(service);
    const failure = error as LegacyChatError;
    expect(failure.code).toBe("FORBIDDEN");
    expect(failure.message).toBe("runtime unavailable");
    expect(failure.status).toBe(403);
    expect(failure.retryable).toBe(false);
    expect(failure.unknownOutcome).toBe(false);
  });

  it("normalizes the upstream bridge codes to stable client codes", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => sse(
      frame({ type: "_error", data: { code: "UPSTREAM_STREAM_MALFORMED", detail: "bad frame" } }),
    ) });
    const { error } = await drain(service);
    const failure = error as LegacyChatError;
    expect(failure.code).toBe(CHAT_STREAM_ERROR_CODES.upstreamStreamMalformed);
    expect(failure.status).toBe(502);
    expect(failure.retryable).toBe(false);
  });

  it("refuses a bare terminal error without run/seq instead of synthesizing one", async () => {
    // `web-sse-bridge-contract.md` §5: no run/seq context -> explicit code,
    // never a fabricated replayable event.
    const service = new LegacyChatService({ fetchImpl: async () => sse(
      frame({ type: "error", data: { code: "", message: "tail failed" } }),
    ) });
    const { events, error } = await drain(service);
    expect(events).toEqual([]);
    const failure = error as LegacyChatError;
    expect(failure.code).toBe(CHAT_STREAM_ERROR_CODES.streamTerminalError);
    expect(failure.message).toBe("tail failed");
    expect(failure.retryable).toBe(false);
    expect(service.lastEventIdFor(turn.turnId)).toBeUndefined();
  });

  it("maps a rejected cursor on the HTTP layer and forgets it", async () => {
    const calls: Array<Record<string, string>> = [];
    const service = new LegacyChatService({ fetchImpl: async (_url, init) => {
      calls.push((init?.headers ?? {}) as Record<string, string>);
      if (calls.length === 1) {
        return new Response(JSON.stringify({ code: "INVALID_LAST_EVENT_ID", detail: "cursor rejected" }), { status: 400 });
      }
      return sse(frame({ type: "_turn_complete", data: {} }));
    } });

    const rejected = await drain(service, turn);
    const failure = rejected.error as LegacyChatError;
    expect(failure.code).toBe(CHAT_STREAM_ERROR_CODES.invalidLastEventId);
    expect(failure.status).toBe(400);
    expect(failure.retryable).toBe(false);
  });

  it("maps an ambiguous bare-sequence cursor to 400 and a not-resumable run to 409", async () => {
    const ambiguous = new LegacyChatService({ fetchImpl: async () => new Response(
      JSON.stringify({ code: "AMBIGUOUS_LAST_EVENT_ID", detail: "ambiguous" }),
      { status: 400 },
    ) });
    const ambiguousFailure = (await drain(ambiguous)).error as LegacyChatError;
    expect(ambiguousFailure.code).toBe(CHAT_STREAM_ERROR_CODES.ambiguousLastEventId);
    expect(ambiguousFailure.status).toBe(400);
    expect(ambiguousFailure.retryable).toBe(false);

    const notResumable = new LegacyChatService({ fetchImpl: async () => new Response(
      JSON.stringify({ code: "RUN_NOT_RESUMABLE", detail: "gone" }),
      { status: 409 },
    ) });
    const notResumableFailure = (await drain(notResumable)).error as LegacyChatError;
    expect(notResumableFailure.code).toBe(CHAT_STREAM_ERROR_CODES.runNotResumable);
    expect(notResumableFailure.status).toBe(409);
    expect(notResumableFailure.retryable).toBe(false);
  });

  it("rejects a frame from another run instead of continuing the cursor", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => sse(
      frame({ type: "text_delta", data: { text: "a" }, run_id: "run-1", seq: 1 }, { id: "evt-1" }),
      frame({ type: "text_delta", data: { text: "b" }, run_id: "run-2", seq: 99 }, { id: "evt-2" }),
      frame({ type: "_turn_complete", data: {} }),
    ) });
    const { events, error } = await drain(service);
    expect(events.map((event) => event.data.text)).toEqual(["a"]);
    expect((error as LegacyChatError).code).toBe(CHAT_STREAM_ERROR_CODES.streamCursorConflict);
    expect((error as LegacyChatError).status).toBe(409);
  });

  it("accepts a canonical wire envelope and validates its transport fields", async () => {
    const eventId = "2f8c1c0e-6f2c-5a4e-9a1f-2c1b0d9e8a77";
    const wire = {
      event_id: eventId,
      event_type: "text_delta",
      schema_version: 1,
      occurred_at: "2026-01-01T00:00:00Z",
      project_id: "project-1",
      session_id: "session-1",
      run_id: "run-1",
      sequence: 7,
      payload: { text: "hello" },
      resume_after: 7,
    };
    const service = new LegacyChatService({ fetchImpl: async () => sse(
      frame(wire, { id: eventId, event: "text_delta" }),
      frame({ ...wire, event_id: "3f8c1c0e-6f2c-5a4e-9a1f-2c1b0d9e8a78", event_type: "done", sequence: 8, payload: {} }, { id: "3f8c1c0e-6f2c-5a4e-9a1f-2c1b0d9e8a78", event: "done" }),
    ) });
    const { events, error } = await drain(service);
    expect(error).toBeUndefined();
    expect(events.map((event) => event.type)).toEqual(["text_delta", "done"]);
    expect(events[0]).toMatchObject({ seq: 7, data: { text: "hello" } });
  });

  it("rejects a wire envelope whose transport id disagrees with event_id", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => sse(
      frame({
        event_id: "2f8c1c0e-6f2c-5a4e-9a1f-2c1b0d9e8a77",
        event_type: "text_delta",
        schema_version: 1,
        payload: { text: "x" },
        run_id: "run-1",
        sequence: 1,
      }, { id: "8f8c1c0e-6f2c-5a4e-9a1f-2c1b0d9e8a70" }),
    ) });
    const { error } = await drain(service);
    expect((error as LegacyChatError).code).toBe(CHAT_STREAM_ERROR_CODES.upstreamStreamMalformed);
  });

  it("reports an incomplete stream when no terminal frame arrives", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => sse(
      frame({ type: "text_delta", data: { text: "a" } }),
    ) });
    const { events, error } = await drain(service);
    expect(events.map((event) => event.type)).toEqual(["text_delta"]);
    const failure = error as LegacyChatError;
    expect(failure.code).toBe(CHAT_STREAM_ERROR_CODES.streamIncomplete);
    expect(failure.retryable).toBe(true);
    expect(failure.unknownOutcome).toBe(true);
  });
});

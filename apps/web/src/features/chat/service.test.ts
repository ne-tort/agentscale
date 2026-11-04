import { describe, expect, it } from "vitest";
import { parseLegacySseFrames, LegacyChatError, LegacyChatService } from "./service";
import type { PendingTurn } from "./types";

const turn: PendingTurn = {
  turnId: "turn-1",
  sessionId: "session-1",
  text: "hello",
  attachmentRefs: [],
  submittedAt: 1,
};

describe("legacy chat service", () => {
  it("parses CRLF comments and the append-only {type,data} frame", () => {
    const payload = JSON.stringify({ type: "text_delta", data: { text: "Hello" } });
    const frames = parseLegacySseFrames(`: keepalive\r\ndata: ${payload}\r\n\r\n`, true);
    expect(frames).toEqual([{ data: [payload] }]);
  });

  it("joins multiline data fields exactly as SSE specifies", () => {
    const frames = parseLegacySseFrames("data: {\"type\":\"status\",\ndata: \"data\":{}}\n\n", true);
    expect(frames[0].data).toEqual(["{\"type\":\"status\",", "\"data\":{}}"]);
  });

  it("defaults to the API v1 mount", async () => {
    let requestedUrl = "";
    const service = new LegacyChatService({ fetchImpl: async (url) => {
      requestedUrl = String(url);
      return new Response(JSON.stringify({ items: [] }));
    } });
    await service.listSessions("project-1");
    expect(requestedUrl).toBe("/api/v1/projects/project-1/agent/sessions");
  });

  it("posts the same idempotent turn to the mounted /api/v1 SSE route on reconnect", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const service = new LegacyChatService({ baseUrl: "/api", fetchImpl: async (url, init) => {
      calls.push({ url: String(url), init });
      const encoder = new TextEncoder();
      if (calls.length === 1) {
        const chunks = [
          encoder.encode("data: {\"type\":\"_session\",\"data\":{\"session_id\":\"session-1\"}}\n\n"),
          encoder.encode("data: {\"type\":\"text_delta\",\"data\":{\"text\":\"partial\"}}\n\n"),
        ];
        let delivered = 0;
        return new Response(new ReadableStream<Uint8Array>({
          // The failure is raised from `pull`, i.e. only after the consumer has
          // read the chunks. Erroring a stream that still holds queued chunks
          // discards them per the Streams spec, which would model a connection
          // that never delivered anything — the opposite of mid-stream loss.
          pull(controller) {
            if (delivered < chunks.length) {
              controller.enqueue(chunks[delivered]!);
              delivered += 1;
              return;
            }
            controller.error(new TypeError("connection lost mid-stream"));
          },
        }), { headers: { "Content-Type": "text/event-stream" } });
      }
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode("data: {\"type\":\"_session\",\"data\":{\"session_id\":\"session-1\"}}\n\n"));
          controller.enqueue(encoder.encode("data: {\"type\":\"done\",\"data\":{}}\n\n"));
          controller.enqueue(encoder.encode("data: {\"type\":\"_turn_complete\",\"data\":{}}\n\n"));
          controller.close();
        },
      });
      return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
    } });
    const received: Array<{ type: string; data: Record<string, unknown> }> = [];
    const partial: Array<{ type: string; data: Record<string, unknown> }> = [];
    let interrupted: unknown;
    try {
      for await (const event of service.streamTurn(turn, "project-1")) partial.push(event);
    } catch (error) {
      interrupted = error;
    }
    expect(interrupted).toBeInstanceOf(LegacyChatError);
    expect((interrupted as LegacyChatError).unknownOutcome).toBe(true);
    expect(partial.map((item) => item.type)).toEqual(["_session", "text_delta"]);
    for await (const event of service.streamTurn(turn, "project-1")) received.push(event);
    expect(received.map((item) => item.type)).toEqual(["_session", "done", "_turn_complete"]);
    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/projects/project-1/chat/stream",
      "/api/v1/projects/project-1/chat/stream",
    ]);
    expect(calls.map((call) => JSON.parse(String(call.init?.body)))).toEqual([
      { text: "hello", session_id: "session-1", attachment_refs: [], turn_id: "turn-1" },
      { text: "hello", session_id: "session-1", attachment_refs: [], turn_id: "turn-1" },
    ]);
  });

  it("maps session, transcript, draft, approval, and attachment calls to registered routes", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const service = new LegacyChatService({ baseUrl: "/api", fetchImpl: async (url, init) => {
      calls.push({ url: String(url), init });
      if (String(url).endsWith("/content")) {
        return new Response(new Uint8Array([0, 255]), {
          headers: { "Content-Type": "application/octet-stream", "Content-Disposition": 'inline; filename="data.bin"' },
        });
      }
      return new Response(JSON.stringify({ items: [] }), { headers: { "Content-Type": "application/json" } });
    } });

    await service.getSidebar("cabinet/1");
    await service.listSessions("project/1");
    await service.createSession("project/1", "model-1");
    await service.getTranscript("project/1", "session/1", { beforeSeq: 9, afterSeq: 3, limit: 25 });
    await service.getSessionTranscript("project/1", "session/1");
    await service.patchSession("project/1", "session/1", { title: "Renamed", pin: true });
    await service.getDraft("project/1");
    await service.saveDraft("project/1", "pending");
    await service.clearDraft("project/1");
    await service.getDraft("project/1", "session/1");
    await service.saveDraft("project/1", "session draft", "session/1");
    await service.clearDraft("project/1", "session/1");
    await service.listPendingApprovals("project/1", "session/1");
    await service.approve("project/1", "session/1", "approval/1", "deny");
    await service.cancel("project/1", "session/1");
    await service.uploadAttachment("project/1", { filename: "input.bin", contentBase64: "AA==", contentType: "application/octet-stream" });
    await service.listAttachments("project/1");
    await service.deleteAttachment("project/1", "attachment/1");
    const download = await service.downloadAttachment("project/1", "attachment/1");

    expect(calls.map(({ url, init }) => [url, init?.method])).toEqual([
      ["/api/v1/cabinets/cabinet%2F1/chats/sidebar", undefined],
      ["/api/v1/projects/project%2F1/agent/sessions", undefined],
      ["/api/v1/projects/project%2F1/agent/sessions", "POST"],
      ["/api/v1/projects/project%2F1/chat/transcript?session_id=session%2F1&limit=25&before_seq=9&after_seq=3", undefined],
      ["/api/v1/projects/project%2F1/agent/sessions/session%2F1/transcript", undefined],
      ["/api/v1/projects/project%2F1/agent/sessions/session%2F1", "PATCH"],
      ["/api/v1/projects/project%2F1/composer-draft", undefined],
      ["/api/v1/projects/project%2F1/composer-draft", "PUT"],
      ["/api/v1/projects/project%2F1/composer-draft", "DELETE"],
      ["/api/v1/projects/project%2F1/agent/sessions/session%2F1/composer-draft", undefined],
      ["/api/v1/projects/project%2F1/agent/sessions/session%2F1/composer-draft", "PUT"],
      ["/api/v1/projects/project%2F1/agent/sessions/session%2F1/composer-draft", "DELETE"],
      ["/api/v1/projects/project%2F1/agent/sessions/session%2F1/pending-approvals", undefined],
      ["/api/v1/projects/project%2F1/agent/sessions/session%2F1/tool-approvals", "POST"],
      ["/api/v1/projects/project%2F1/agent/sessions/session%2F1/cancel", "POST"],
      ["/api/v1/projects/project%2F1/attachments", "POST"],
      ["/api/v1/projects/project%2F1/attachments", undefined],
      ["/api/v1/projects/project%2F1/attachments/attachment%2F1", "DELETE"],
      ["/api/v1/projects/project%2F1/attachments/attachment%2F1/content", undefined],
    ]);
    expect(JSON.parse(String(calls[2].init?.body))).toEqual({ model: "model-1" });
    expect(JSON.parse(String(calls[5].init?.body))).toEqual({ title: "Renamed", pin: true });
    expect(JSON.parse(String(calls[7].init?.body))).toEqual({ text: "pending" });
    expect(JSON.parse(String(calls[13].init?.body))).toEqual({ id: "approval/1", decision: "deny" });
    expect(JSON.parse(String(calls[15].init?.body))).toEqual({ filename: "input.bin", content_base64: "AA==", content_type: "application/octet-stream" });
    expect(new Headers(calls[15].init?.headers).get("Content-Type")).toBe("application/json");
    expect(download).toMatchObject({ filename: "data.bin", contentType: "application/octet-stream", size: 2 });
    expect([...new Uint8Array(await download.blob.arrayBuffer())]).toEqual([0, 255]);
    expect(new Headers(calls[18].init?.headers).get("Accept")).toBe("application/octet-stream");
  });

  it("aborts the stream reader without turning explicit cancellation into an unknown outcome", async () => {
    let readerCancelled = false;
    const service = new LegacyChatService({ fetchImpl: async () => new Response(new ReadableStream<Uint8Array>({
      cancel() { readerCancelled = true; },
    })) });
    const controller = new AbortController();
    const iterator = service.streamTurn(turn, "project-1", controller.signal);
    const pendingRead = iterator.next();
    await Promise.resolve();
    controller.abort();
    let caught: unknown;
    try {
      await pendingRead;
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(DOMException);
    expect((caught as Error).name).toBe("AbortError");
    expect(readerCancelled).toBe(true);
  });

  it("marks cancel network failures unknown so the optimistic turn is retained", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => { throw new TypeError("offline"); } });
    let error: unknown;
    try {
      await service.cancel("project-1", "session-1");
    } catch (caught) {
      error = caught;
    }
    expect(error).toBeInstanceOf(LegacyChatError);
    expect((error as LegacyChatError).code).toBe("cancel_outcome_unknown");
    expect((error as LegacyChatError).unknownOutcome).toBe(true);
    expect((error as LegacyChatError).retryable).toBe(false);
  });

  it("blocks blind session-create retries after a network outcome is unknown", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => { throw new TypeError("offline"); } });
    let error: unknown;
    try {
      await service.createSession("project-1");
    } catch (caught) {
      error = caught;
    }
    expect(error).toBeInstanceOf(LegacyChatError);
    expect((error as LegacyChatError).code).toBe("session_create_outcome_unknown");
    expect((error as LegacyChatError).unknownOutcome).toBe(true);
    expect((error as LegacyChatError).retryable).toBe(false);
  });

  it("marks network failure as unknown outcome so callers can reattach without a second payload", async () => {
    const service = new LegacyChatService({ fetchImpl: async () => { throw new TypeError("offline"); } });
    let error: unknown;
    try {
      for await (const _event of service.streamTurn(turn, "project-1")) { /* consume */ }
    } catch (caught) {
      error = caught;
    }
    expect(error).toBeInstanceOf(LegacyChatError);
    expect((error as LegacyChatError).unknownOutcome).toBe(true);
    expect((error as LegacyChatError).retryable).toBe(true);
  });

  it("uses backend error status for completed SSE failures", async () => {
    const errorBody = JSON.stringify({ type: "_error", data: { code: "FORBIDDEN", detail: "runtime unavailable", status: 403 } });
    const service = new LegacyChatService({ fetchImpl: async () => new Response(`data: ${errorBody}\n\ndata: {"type":"_turn_complete","data":{}}\n\n`, {
      headers: { "Content-Type": "text/event-stream" },
    }) });
    let error: unknown;
    try {
      for await (const _event of service.streamTurn(turn, "project-1")) { /* consume */ }
    } catch (caught) {
      error = caught;
    }
    expect(error).toBeInstanceOf(LegacyChatError);
    expect((error as LegacyChatError).message).toBe("runtime unavailable");
    expect((error as LegacyChatError).status).toBe(403);
    expect((error as LegacyChatError).retryable).toBe(false);
    expect((error as LegacyChatError).unknownOutcome).toBe(false);
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatStreamOutcomeUnknownError, ChatStreamProtocolError, createApiClient } from "./client";
import type { ChatRunRequest, ChatStreamOptions, SseEventEnvelope } from "./types";

const scope = { tenant_id: "tenant-1", project_id: "project-1", session_id: "session-1", turn_id: "turn-1", run_id: "run-1" };
const binding = { ...scope, binding_digest: "sha256:binding-proof" };
const request: ChatRunRequest = { project_id: scope.project_id, session_id: scope.session_id, turn_id: scope.turn_id, text: "hello", attachment_refs: [] };
const envelope: SseEventEnvelope = { event_id: "event-1", event_type: "text_delta", schema_version: 1, occurred_at: "2026-10-31T00:00:00Z", project_id: scope.project_id, session_id: scope.session_id, turn_id: scope.turn_id, tenant_id: scope.tenant_id, run_id: scope.run_id, sequence: 1, payload: { text: "hello" } };
const options = (overrides: Partial<ChatStreamOptions> = {}): ChatStreamOptions => ({ scope, durableBinding: binding, onEvent: () => undefined, ...overrides });
const streamResponse = (body: string) => new Response(body, { headers: { "Content-Type": "text/event-stream" } });

function chunkedResponse(chunks: string[], cancel = vi.fn()) {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) { for (const chunk of chunks) controller.enqueue(encoder.encode(chunk)); controller.close(); },
    cancel,
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("chat SSE client contract (acceptance fixtures; execution deferred by migration verification freeze)", () => {
  it("parses CRLF, comments, id/event, multiline data and dispatches a final EOF frame", async () => {
    const json = JSON.stringify(envelope);
    const split = json.indexOf("payload");
    vi.stubGlobal("fetch", vi.fn(async () => chunkedResponse([
      `: heartbeat\r\nid: ${envelope.event_id}\r\nevent: text_delta\r\ndata: ${json.slice(0, split)}\r\ndata: ${json.slice(split)}`,
    ])));
    const client = createApiClient();
    const received = [] as SseEventEnvelope[];
    for await (const event of client.runChat(request, options({ onEvent: () => undefined }))) received.push(event);
    expect(received).toHaveLength(1);
    expect(received[0]).toMatchObject({ event_id: envelope.event_id, event_type: envelope.event_type });
  });

  it("validates transport identity, scope and schema before yielding", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse(`id: different\ndata: ${JSON.stringify(envelope)}\n\n`)));
    const client = createApiClient();
    await expect(async () => { for await (const _event of client.runChat(request, options({ onEvent: () => undefined }))) { /* consume */ } }).rejects.toBeInstanceOf(ChatStreamProtocolError);
  });

  it("suppresses duplicate committed IDs and rejects non-monotonic sequences", async () => {
    const second = { ...envelope, event_id: "event-2", sequence: 2 };
    const duplicate = { ...envelope, event_id: "event-1", sequence: 1 };
    const lower = { ...envelope, event_id: "event-3", sequence: 1 };
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse([
      `data: ${JSON.stringify(envelope)}\n\n`,
      `data: ${JSON.stringify(duplicate)}\n\n`,
      `data: ${JSON.stringify(second)}\n\n`,
      `data: ${JSON.stringify(lower)}\n\n`,
    ].join(""))));
    const onEvent = vi.fn();
    const client = createApiClient();
    const received: SseEventEnvelope[] = [];
    await expect(async () => { for await (const event of client.runChat(request, options({ onEvent }))) received.push(event); }).rejects.toThrow("non_monotonic_cursor");
    expect(received.map((event) => event.event_id)).toEqual(["event-1", "event-2"]);
    expect(onEvent).toHaveBeenCalledTimes(2);
  });

  it("commits cursor only after the event handler succeeds", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse(`id: ${envelope.event_id}\nevent: text_delta\ndata: ${JSON.stringify(envelope)}`)));
    const onCursorCommit = vi.fn();
    const onEvent = vi.fn(async () => { throw new Error("render_failed"); });
    const client = createApiClient();
    await expect(async () => { for await (const _event of client.runChat(request, options({ onEvent, onCursorCommit }))) { /* consume */ } }).rejects.toThrow("render_failed");
    expect(onCursorCommit).not.toHaveBeenCalled();
  });

  it("cancels reader when a handler fails", async () => {
    const cancel = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => chunkedResponse([`data: ${JSON.stringify(envelope)}\n\n`], cancel)));
    const client = createApiClient();
    await expect(async () => { for await (const _event of client.runChat(request, options({ onEvent: () => { throw new Error("handler_failed"); } }))) { /* consume */ } }).rejects.toThrow("handler_failed");
    expect(cancel).toHaveBeenCalled();
  });

  it("does not mark an event committed when durable cursor persistence fails", async () => {
    const cancel = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => chunkedResponse([`data: ${JSON.stringify(envelope)}\n\n`], cancel)));
    const onEvent = vi.fn();
    const onCursorCommit = vi.fn(async () => { throw new Error("cursor_store_failed"); });
    const client = createApiClient();
    await expect(async () => { for await (const _event of client.runChat(request, options({ onEvent, onCursorCommit }))) { /* consume */ } }).rejects.toThrow("cursor_store_failed");
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onCursorCommit).toHaveBeenCalledTimes(1);
    expect(cancel).toHaveBeenCalled();
  });

  it("fails before POST when durable run binding is absent or mismatched", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const client = createApiClient();
    await expect(async () => { for await (const _event of client.runChat(request, options({ durableBinding: undefined }))) { /* consume */ } }).rejects.toBeInstanceOf(ChatStreamProtocolError);
    await expect(async () => { for await (const _event of client.runChat(request, options({ durableBinding: { ...binding, session_id: "other" } }))) { /* consume */ } }).rejects.toBeInstanceOf(ChatStreamProtocolError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("marks transport failure after POST as unknown and requires same-turn reattach, never automatic retry", async () => {
    const fetch = vi.fn(async () => { throw new TypeError("connection_lost"); });
    vi.stubGlobal("fetch", fetch);
    const client = createApiClient();
    await expect(async () => { for await (const _event of client.runChat(request, options())) { /* consume */ } }).rejects.toBeInstanceOf(ChatStreamOutcomeUnknownError);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1]?.body).toBe(JSON.stringify(request));
  });
});

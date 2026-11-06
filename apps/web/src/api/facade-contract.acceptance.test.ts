import { describe, expect, it, vi } from "vitest";
import { createApiClient } from "./client";
import type { ChatRunRequest, ChatStreamEvent } from "./types";

const request: ChatRunRequest = {
  project_id: "project-1",
  session_id: "session-1",
  turn_id: "turn-1",
  text: "hello",
  attachment_refs: [],
};

function stream(body: string): Response {
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

/** The real `fetch` signature, captured where the per-test `fetch` doubles do not shadow it. */
type FetchLike = typeof fetch;

describe("legacy chat stream acceptance contract", () => {
  it("uses the real POST route and legacy body, with no durable run or replay claims", async () => {
    const fetch = vi.fn<FetchLike>(async () => stream("data: {\"type\":\"_turn_complete\",\"data\":{}}\n\n"));
    const client = createApiClient({ baseUrl: "/api/v1", fetcher: fetch });
    const received: ChatStreamEvent[] = [];

    for await (const event of client.runChat(request)) received.push(event);

    expect(received).toEqual([{ type: "_turn_complete", data: {} }]);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/v1/projects/project-1/chat/stream");
    expect(JSON.parse(String(init?.body))).toEqual({
      session_id: "session-1",
      text: "hello",
      attachment_refs: [],
      turn_id: "turn-1",
    });
    expect(init?.headers).not.toHaveProperty("Last-Event-ID");
    expect(init?.headers).not.toHaveProperty("X-Chat-Tenant-ID");
    expect(init?.headers).not.toHaveProperty("X-Chat-Run-ID");
  });

  it("treats keepalive comments as transport noise and preserves event order", async () => {
    const fetch = vi.fn<FetchLike>(async () => stream([
      ": keepalive\n\n",
      "data: {\"type\":\"_session\",\"data\":{\"session_id\":\"session-1\"}}\n\n",
      "data: {\"type\":\"text_delta\",\"data\":{\"text\":\"hello\"}}\n\n",
    ].join("")));
    const client = createApiClient({ fetcher: fetch });
    const events: ChatStreamEvent[] = [];
    for await (const event of client.runChat(request)) events.push(event);

    expect(events.map((event) => event.type)).toEqual(["_session", "text_delta"]);
  });
});
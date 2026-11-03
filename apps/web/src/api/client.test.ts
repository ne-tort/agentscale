import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, ChatStreamNetworkError, ChatStreamProtocolError, createApiClient } from "./client";
import type { ChatRunRequest, ChatStreamEvent } from "./types";

const request: ChatRunRequest = {
  project_id: "project-1",
  session_id: "session-1",
  turn_id: "turn-1",
  text: "hello",
  attachment_refs: [],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function streamResponse(body: string): Response {
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

function chunkedResponse(chunks: string[], cancel = vi.fn()): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
    cancel,
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("typed FastAPI client contracts", () => {
  it("targets /api/v1 and sends Bearer JSON without durable-run headers", async () => {
    const fetch = vi.fn(async () => jsonResponse({ id: "project-1", cabinet_id: "cabinet-1", name: "Project", status: "draft" }));
    const client = createApiClient({ getAccessToken: () => "access-token", fetcher: fetch });

    await client.getProject("project/1");

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/v1/projects/project%2F1");
    expect(init?.method).toBeUndefined();
    expect(init?.headers).toMatchObject({ Accept: "application/json", Authorization: "Bearer access-token" });
    expect(init?.headers).not.toHaveProperty("Last-Event-ID");
    expect(init?.headers).not.toHaveProperty("X-Chat-Run-ID");
  });

  it("covers cabinet selection, project CRUD, lifecycle, container and workspace JSON routes", async () => {
    const fetch = vi.fn(async () => jsonResponse({ items: [] }));
    const client = createApiClient({ fetcher: fetch });

    await client.listCabinets();
    await client.getCabinet("cabinet-1");
    await client.createCabinet({ name: "Cabinet", company_id: "company-1" });
    await client.renameCabinet("cabinet-1", "Renamed");
    await client.getSelection("cabinet-1");
    await client.setSelection("cabinet-1", { project_id: "project-1", chat_session_id: "session-1" });
    await client.chatSidebar("cabinet-1");
    await client.listProjects("cabinet-1");
    await client.createProject("cabinet-1", { name: "Project" });
    await client.getProject("project-1");
    await client.patchProject("project-1", { about: "about" });
    await client.deleteProject("project-1");
    await client.restoreProject("project-1");
    await client.purgeProject("project-1");
    await client.projectLifecycle("project-1", "launch");
    await client.projectLifecycle("project-1", "pause");
    await client.projectLifecycle("project-1", "resume");
    await client.projectLifecycle("project-1", "complete");
    await client.projectLifecycle("project-1", "reload");
    await client.containerStatus("project-1");
    await client.listWorkspaceEntries("project-1", "src");
    await client.previewWorkspace("project-1", "src/main.ts");
    await client.deleteWorkspaceEntry("project-1", "src/old.ts");
    await client.moveWorkspaceEntry("project-1", "src/a.ts", "src/b.ts");
    await client.copyWorkspaceEntry("project-1", "src/a.ts", "src/c.ts");

    const urls = fetch.mock.calls.map(([url]) => String(url));
    expect(urls).toContain("/api/v1/cabinets");
    expect(urls).toContain("/api/v1/cabinets/cabinet-1/me/selection");
    expect(urls).toContain("/api/v1/cabinets/cabinet-1/projects");
    expect(urls).toContain("/api/v1/projects/project-1/container");
    expect(urls).toContain("/api/v1/projects/project-1/container/workspace/entries?path=src");
    expect(urls).toContain("/api/v1/projects/project-1/container/workspace/preview?path=src%2Fmain.ts");
    expect(urls).toContain("/api/v1/projects/project-1/launch");
    expect(urls).toContain("/api/v1/projects/project-1/pause");
    expect(urls).toContain("/api/v1/projects/project-1/resume");
    expect(urls).toContain("/api/v1/projects/project-1/complete");
    expect(urls).toContain("/api/v1/projects/project-1/reload");
  });

  it("covers agent session CRUD, fork/title/pin, transcript, drafts and approvals", async () => {
    const fetch = vi.fn(async () => jsonResponse({ items: [] }));
    const client = createApiClient({ fetcher: fetch });

    await client.listSessions("project-1");
    await client.createSession("project-1", { model: "model-1", title: "Chat" });
    await client.getSession("project-1", "session-1");
    await client.patchSession("project-1", "session-1", { title: "Renamed", pin: true });
    await client.deleteSession("project-1", "session-1");
    await client.forkSession("project-1", "session-1");
    await client.cancelSession("project-1", "session-1");
    await client.sendMessage("project-1", "session-1", { text: "hello", attachment_refs: [] });
    await client.getTranscript("project-1", "session-1", { afterSeq: 3, limit: 25 });
    await client.getSessionTranscript("project-1", "session-1");
    await client.listPendingApprovals("project-1", "session-1");
    await client.resolveApproval("project-1", "session-1", "approval-1", "approve");
    await client.getDraft("project-1", "session-1");
    await client.saveDraft("project-1", "draft", "session-1");
    await client.clearDraft("project-1", "session-1");
    await client.listSessionEvents("project-1", "session-1");
    await client.appendSessionEvent("project-1", "session-1", { type: "status", data: { phase: "working" } });

    const urls = fetch.mock.calls.map(([url]) => String(url));
    expect(urls).toContain("/api/v1/projects/project-1/agent/sessions");
    expect(urls).toContain("/api/v1/projects/project-1/agent/sessions/session-1/fork");
    expect(urls).toContain("/api/v1/projects/project-1/agent/sessions/session-1/send");
    expect(urls).toContain("/api/v1/projects/project-1/chat/transcript?session_id=session-1&after_seq=3&limit=25");
    expect(urls).toContain("/api/v1/projects/project-1/agent/sessions/session-1/pending-approvals");
    expect(urls).toContain("/api/v1/projects/project-1/agent/sessions/session-1/tool-approvals");
    expect(urls).toContain("/api/v1/projects/project-1/agent/sessions/session-1/composer-draft");
    expect(urls).toContain("/api/v1/projects/project-1/agent/sessions/session-1/events");
  });

  it("keeps multipart and downloads outside JSON request handling", async () => {
    const binary = new Response("file", {
      headers: { "Content-Type": "text/plain", "Content-Disposition": "attachment; filename=note.txt" },
    });
    const fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ id: "attachment-1", filename: "note.txt" }))
      .mockResolvedValueOnce(binary);
    const client = createApiClient({ fetcher: fetch });

    await client.uploadCabinetContent("cabinet-1", new Blob(["file"], { type: "text/plain" }), "note.txt");
    const downloaded = await client.downloadAttachment("project-1", "attachment-1");

    const uploadInit = fetch.mock.calls[0][1];
    expect(uploadInit?.body).toBeInstanceOf(FormData);
    expect(uploadInit?.headers).not.toHaveProperty("Content-Type");
    expect(downloaded.filename).toBe("note.txt");
    expect(downloaded.contentType).toBe("text/plain");
    expect(downloaded.size).toBe(4);
  });

  it("sends JSON base64 attachments and exposes trigger, schedule, settings and model routes", async () => {
    const fetch = vi.fn(async () => jsonResponse({ items: [] }));
    const client = createApiClient({ fetcher: fetch });

    await client.listAttachments("project-1");
    await client.uploadAttachment("project-1", { filename: "note.txt", contentBase64: "bm90ZQ==", contentType: "text/plain" });
    await client.deleteAttachment("project-1", "attachment-1");
    await client.enqueueTrigger("project-1", { kind: "chat.message", payload: { text: "hello" } });
    await client.enqueueScheduleTrigger("project-1", { cron: "0 * * * *" });
    await client.listTriggers("project-1");
    await client.dispatchTriggers("project-1", 3);
    await client.getChatErrorPolicy("project-1");
    await client.setChatErrorPolicy("project-1", { interval_sec: 10, max_attempts: 2, fallback_models: [] });
    await client.listProjectModelsLive("project-1");
    await client.listProjectAiKeys("project-1");
    await client.listCompanyModels("company-1");
    await client.listCompanyKeys("company-1");
    await client.listKeyModels("company-1", "key-1");

    const uploadBody = fetch.mock.calls[1][1]?.body;
    expect(JSON.parse(String(uploadBody))).toEqual({ filename: "note.txt", content_base64: "bm90ZQ==", content_type: "text/plain" });
    const scheduleBody = fetch.mock.calls[4][1]?.body;
    expect(JSON.parse(String(scheduleBody))).toEqual({ kind: "system.schedule", payload: { cron: "0 * * * *" } });
    const urls = fetch.mock.calls.map(([url]) => String(url));
    expect(urls).toContain("/api/v1/projects/project-1/triggers");
    expect(urls).toContain("/api/v1/projects/project-1/triggers/dispatch?max=3");
    expect(urls).toContain("/api/v1/projects/project-1/chat-error-policy");
    expect(urls).toContain("/api/v1/projects/project-1/models/live");
    expect(urls).toContain("/api/v1/projects/project-1/ai-keys/available");
    expect(urls).toContain("/api/v1/companies/company-1/ai-models");
    expect(urls).toContain("/api/v1/companies/company-1/ai-keys");
    expect(urls).toContain("/api/v1/companies/company-1/ai-keys/key-1/models");
  });

  it("parses legacy {type,data} SSE, ignores keepalive comments, and does not invent replay headers", async () => {
    const events: ChatStreamEvent[] = [];
    const fetch = vi.fn(async () => chunkedResponse([
      ": keepalive\r\n\r\ndata: {\"type\":\"_session\",\"data\":{\"session_id\":\"session-1\"}}\n\n",
      "data: {\"type\":\"text_delta\",\"data\":{\"text\":\"hel",
      "lo\"}}\n\ndata: {\"type\":\"_turn_complete\",\"data\":{\"session_id\":\"session-1\"}}\n\n",
    ]));
    const client = createApiClient({ getAccessToken: () => "access-token", fetcher: fetch });
    const received: ChatStreamEvent[] = [];
    for await (const event of client.runChat(request, { onEvent: (event) => { events.push(event); } })) received.push(event);

    expect(received.map((event) => event.type)).toEqual(["_session", "text_delta", "_turn_complete"]);
    expect(events).toEqual(received);
    const [, init] = fetch.mock.calls[0];
    expect(init?.body).toBe(JSON.stringify({ session_id: "session-1", text: "hello", attachment_refs: [], turn_id: "turn-1" }));
    expect(init?.headers).toMatchObject({ Authorization: "Bearer access-token", Accept: "text/event-stream" });
    expect(init?.headers).not.toHaveProperty("Last-Event-ID");
    expect(init?.headers).not.toHaveProperty("X-Chat-Event-Sequence");
  });

  it("surfaces structured server stream errors and transport failures", async () => {
    const streamFetch = vi.fn(async () => streamResponse("data: {\"type\":\"_error\",\"data\":{\"code\":\"RUNTIME_UNAVAILABLE\",\"detail\":\"agent offline\",\"status\":503}}\n\n"));
    const client = createApiClient({ fetcher: streamFetch });
    await expect(async () => { for await (const _event of client.runChat(request)) { /* consume */ } }).rejects.toBeInstanceOf(ApiRequestError);

    const network = createApiClient({ fetcher: vi.fn(async () => { throw new TypeError("connection_lost"); }) });
    await expect(async () => { for await (const _event of network.runChat(request)) { /* consume */ } }).rejects.toBeInstanceOf(ChatStreamNetworkError);

    const malformed = createApiClient({ fetcher: vi.fn(async () => streamResponse("data: {\"type\":\"text_delta\"}\n\n")) });
    await expect(async () => { for await (const _event of malformed.runChat(request)) { /* consume */ } }).rejects.toBeInstanceOf(ChatStreamProtocolError);
  });
});
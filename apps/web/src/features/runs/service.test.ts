import { afterEach, describe, expect, it, vi } from "vitest";
import { createRunsService, approvalExpiryLabel, extractApprovalContext, normalizeWorkspacePath, RunsRequestError, transcriptRunStatus } from "./service";
import type { RunsErrorCode } from "./service";
import type { RunSession, RunTranscript, WorkspaceFile } from "./contracts";
import runsUnit from "../../i18n/units/runs";
import { format, type Locale, type Vars } from "../../i18n/core";

/**
 * The real runs copy, without the whole catalogue: another unit being edited in
 * parallel must not decide whether these assertions can run.
 */
const translator = (locale: Locale) => (key: string, vars?: Vars): string => {
  const template = runsUnit[locale][key.slice(key.indexOf(".") + 1)];
  return template === undefined ? key : format(template, vars);
};
const en = translator("en");
const ru = translator("ru");

const session: RunSession = { id: "session-3", project_id: "project-1", status: "active", title: "Monthly report", model: "model-a" };
const transcript: RunTranscript = { session_id: session.id, turn_in_progress: false, blocks: [{ kind: "approval", id: "approval-2", name: "mcp.calendar.create", input: { title: "Planning" }, reason: "Creates an external meeting" }] };
const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => vi.restoreAllMocks());

describe("session-backed Runs service", () => {
  it("loads durable session history using the project agent-session endpoint", async () => {
    const fetcher = vi.fn(async () => jsonResponse({ items: [session] }));
    const service = createRunsService({ baseUrl: "/api/v1", getAccessToken: () => "token-1", fetcher: fetcher as typeof fetch });
    await expect(service.listSessions("project / one")).resolves.toEqual([session]);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/projects/project%20%2F%20one/agent/sessions", expect.objectContaining({ headers: expect.objectContaining({ Authorization: "Bearer token-1" }) }));
  });

  it("loads transcript blocks from the actual paginated chat transcript endpoint", async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const fetcher: typeof fetch = async (input, init) => { calls.push({ input, init }); return jsonResponse(transcript); };
    const service = createRunsService({ fetcher });
    await expect(service.getTranscript("project-1", session.id, { beforeSeq: 24 })).resolves.toEqual(transcript);
    expect(String(calls[0].input)).toContain("/projects/project-1/chat/transcript?");
    expect(String(calls[0].input)).toContain("session_id=session-3");
    expect(String(calls[0].input)).toContain("limit=500");
    expect(String(calls[0].input)).toContain("before_seq=24");
  });

  it("keeps transcript pagination server-backed and scoped to this project/session", async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const fetcher: typeof fetch = async (input, init) => { calls.push({ input, init }); return jsonResponse(transcript); };
    const service = createRunsService({ fetcher });
    await service.getTranscript("project-1", session.id, { beforeSeq: 400 });
    expect(String(calls[0].input)).toContain("before_seq=400");
    expect(String(calls[0].input)).toContain("session_id=session-3");
  });

  it("loads a session detail from the actual per-session endpoint", async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const fetcher: typeof fetch = async (input, init) => { calls.push({ input, init }); return jsonResponse(session); };
    const service = createRunsService({ fetcher });
    await expect(service.getSession("project-1", session.id)).resolves.toEqual(session);
    expect(calls[0].input).toBe("/api/v1/projects/project-1/agent/sessions/session-3");
  });

  it("loads pending approvals and posts only the supported decision payload", async () => {
    const approval = { id: "approval-2", name: "mcp.calendar.create", input: { title: "Planning" } };
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [approval] }))
      .mockResolvedValueOnce(jsonResponse({ session_id: session.id, approval_id: approval.id, decision: "deny", events: [] }));
    const service = createRunsService({ fetcher: fetcher as typeof fetch });
    await expect(service.listPendingApprovals("project-1", session.id)).resolves.toEqual([approval]);
    await service.decideApproval("project-1", session.id, approval.id, "deny");
    expect(fetcher.mock.calls[0][0]).toContain(`/agent/sessions/${session.id}/pending-approvals`);
    expect(fetcher.mock.calls[1][0]).toContain(`/agent/sessions/${session.id}/tool-approvals`);
    expect(fetcher.mock.calls[1][1]).toMatchObject({ method: "POST", body: JSON.stringify({ id: approval.id, decision: "deny" }) });
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).not.toHaveProperty("reason");
  });

  it("uses the session cancel endpoint and returns the persisted session response", async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const cancelledSession: RunSession = { ...session, status: "cancelled" };
    const fetcher: typeof fetch = async (input, init) => { calls.push({ input, init }); return jsonResponse(cancelledSession); };
    const service = createRunsService({ fetcher });
    await expect(service.cancelSession("project-1", session.id)).resolves.toEqual(cancelledSession);
    expect(calls[0].input).toBe("/api/v1/projects/project-1/agent/sessions/session-3/cancel");
    expect(calls[0].init?.method).toBe("POST");
  });

  it("surfaces approval delivery failures without synthesizing a decision", async () => {
    const fetcher: typeof fetch = async () => jsonResponse({ code: "APPROVAL_DELIVERY_FAILED", detail: "still pending" }, 503);
    const service = createRunsService({ fetcher });
    await expect(service.decideApproval("project-1", session.id, "approval-2", "approve")).rejects.toMatchObject({
      status: 503, code: "unavailable", detail: "still pending", serverCode: "APPROVAL_DELIVERY_FAILED",
    } satisfies Partial<RunsRequestError>);
  });

  it("lists workspace entries and downloads files from project-scoped routes", async () => {
    const file: WorkspaceFile = { name: "report.csv", path: "exports/report.csv", kind: "file", size: 12, modified_at: null };
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const responses = [jsonResponse({ entries: [file] }), new Response("report,value", { status: 200 })];
    const fetcher: typeof fetch = async (input, init) => { calls.push({ input, init }); const response = responses.shift(); if (!response) throw new Error("unexpected fetch"); return response; };
    const service = createRunsService({ getAccessToken: () => "token-2", fetcher });
    await expect(service.listWorkspaceFiles("project-1", "exports")).resolves.toEqual([file]);
    await expect(service.downloadWorkspaceFile("project-1", file.path)).resolves.toBeInstanceOf(Blob);
    expect(calls[0].input).toBe("/api/v1/projects/project-1/container/workspace/entries?path=exports");
    expect(String(calls[1].input)).toBe("/api/v1/projects/project-1/container/workspace/content?path=exports%2Freport.csv");
    expect(calls[1].init?.headers).toMatchObject({ Authorization: "Bearer token-2" });
  });

  it("surfaces workspace and approval endpoint errors", async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const responses = [jsonResponse({ code: "RUNTIME_NOT_RUNNING", detail: "project Pod is stopped" }, 409)];
    const fetcher: typeof fetch = async (input, init) => { calls.push({ input, init }); const response = responses.shift(); if (!response) throw new Error("unexpected fetch"); return response; };
    const service = createRunsService({ fetcher });
    await expect(service.listWorkspaceFiles("project-1")).rejects.toMatchObject({ status: 409, code: "conflict", serverCode: "RUNTIME_NOT_RUNNING", detail: "project Pod is stopped" } satisfies Partial<RunsRequestError>);
  });
});

describe("request failures travel as codes, not sentences", () => {
  it("turns a transport failure into a network code and keeps the browser text as detail", async () => {
    const fetcher: typeof fetch = async () => { throw new TypeError("Failed to fetch"); };
    const service = createRunsService({ fetcher });
    await expect(service.listRuns("project-1")).rejects.toMatchObject({
      status: 0, code: "network", detail: "Failed to fetch",
    } satisfies Partial<RunsRequestError>);
  });

  it("maps HTTP statuses onto the codes the screen knows how to say", async () => {
    const cases: Array<[number, RunsErrorCode]> = [
      [401, "unauthorized"],
      [403, "forbidden"],
      [404, "notFound"],
      [409, "conflict"],
      [422, "invalidRequest"],
      [500, "server"],
      [503, "unavailable"],
    ];
    for (const [status, code] of cases) {
      const fetcher: typeof fetch = async () => jsonResponse({ code: "SOME_BACKEND_CODE", detail: "backend words" }, status);
      await expect(createRunsService({ fetcher }).listRuns("project-1")).rejects.toMatchObject({
        status, code, serverCode: "SOME_BACKEND_CODE", detail: "backend words",
      } satisfies Partial<RunsRequestError>);
    }
  });

  it("reports a body of the wrong shape as an invalid response instead of crashing the screen", async () => {
    const fetcher: typeof fetch = async () => jsonResponse({ items: "not-a-list" });
    await expect(createRunsService({ fetcher }).listRuns("project-1")).rejects.toMatchObject({
      code: "invalidResponse",
    } satisfies Partial<RunsRequestError>);
  });

  it("does not compose a sentence when the backend sends no message", async () => {
    const fetcher: typeof fetch = async () => new Response(null, { status: 404, statusText: "Not Found" });
    await expect(createRunsService({ fetcher }).listRuns("project-1")).rejects.toMatchObject({
      status: 404, code: "notFound", serverCode: undefined,
    } satisfies Partial<RunsRequestError>);
  });
});

describe("backend capability disclosure helpers", () => {
  it("does not report an active session as a completed run, and says so in the reader's language", () => {
    expect(transcriptRunStatus(en, transcript, session)).toBe("Active");
    expect(transcriptRunStatus(en, { ...transcript, turn_in_progress: true }, session)).toBe("Turn running");
    expect(transcriptRunStatus(en, undefined, undefined)).toBe("Status unavailable");
    expect(transcriptRunStatus(ru, transcript, session)).toBe("Активен");
    expect(transcriptRunStatus(ru, { ...transcript, turn_in_progress: true }, session)).toBe("Идёт ответ");
    expect(transcriptRunStatus(ru, undefined, undefined)).toBe("Статус недоступен");
  });

  it("labels unavailable and elapsed expiries without inventing a deadline", () => {
    expect(approvalExpiryLabel(en, "en", undefined, 0)).toBe("No expiry given");
    expect(approvalExpiryLabel(en, "en", "2020-01-01T00:00:00Z", Date.parse("2021-01-01T00:00:00Z"))).toBe("Expired");
    expect(approvalExpiryLabel(en, "en", "not-a-date", 0)).toBe("The expiry value is invalid");
    expect(approvalExpiryLabel(ru, "ru", "2020-01-01T00:00:00Z", Date.parse("2021-01-01T00:00:00Z"))).toBe("Срок истёк");
  });

  it("writes a deadline in the interface's language, not the browser's", () => {
    const now = Date.parse("2026-01-01T00:00:00Z");
    // en-US writes "Nov 15, 2026", en-GB "15 Nov 2026": the month name is the point.
    expect(approvalExpiryLabel(en, "en", "2026-11-15T12:00:00Z", now)).toMatch(/^Valid until .*Nov .*2026/);
    expect(approvalExpiryLabel(ru, "ru", "2026-11-15T12:00:00Z", now)).toMatch(/^Действует до .*нояб\. 2026/);
    expect(approvalExpiryLabel(ru, "ru", "2026-11-15T12:00:00Z", now)).not.toMatch(/Nov/);
  });

  it("normalizes directory paths without producing traversal segments", () => {
    expect(normalizeWorkspacePath("/exports\\monthly/./report/../final")).toBe("exports/monthly/report/final");
    expect(normalizeWorkspacePath("")).toBe("");
  });

  it("extracts request context from the matching durable transcript approval block", () => {
    const approval = { id: "approval-2", name: "mcp.calendar.create", input: { title: "Planning" } };
    expect(extractApprovalContext(transcript, approval)).toBe("Creates an external meeting");
    expect(extractApprovalContext({ ...transcript, blocks: [] }, approval)).toBeUndefined();
  });
});

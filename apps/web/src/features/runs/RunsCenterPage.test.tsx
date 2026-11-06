// @vitest-environment jsdom
// Route-level workflow for the Runs & approvals page.
//
// This file previously asserted the superseded session-backed Runs Center: it read a
// `?session=` deep link into a transcript pane, expected `.run-approval-card > h4`,
// "Approve action"/"Reject" buttons posting to `/agent/sessions/{id}/tool-approvals`, and
// copy such as "Source: agent session + transcript API". That page no longer exists:
// `RunsCenterPage` is now the durable Agent Runs surface (tabs Runs / Approvals /
// Session history, decisions posted to `/agent/approvals/{id}/decision` with an event-log
// readback). A transcript is no longer rendered inside a run, so the old expectations
// could not be satisfied by any component in the app -- the test was modelling a page that
// was removed, not a page that regressed. The checks below keep the same intent (real
// AppProviders auth restore, Bearer propagation, persisted approval decisions confirmed by
// readback, and no calls to run APIs the backend does not expose) against the current
// contract.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AUTH_STORAGE_KEY } from "../../app/auth/lifecycle";
import { AppProviders, useAuth } from "../../app/context";
import { RunsCenterPage } from "./RunsCenterPage";
import type { AgentRunRecord, ApprovalInboxItem, ControlPlaneEvent } from "./contracts";
import { POLL_BUDGET_MS } from "../../test-support/poll-budget";

function AuthenticatedRunsRoute() {
  const { isLoading, isAuthenticated } = useAuth();
  if (isLoading) return <p>Restoring authentication</p>;
  return isAuthenticated ? <RunsCenterPage /> : <p>Authentication unavailable</p>;
}

interface CapturedRequest {
  method: string;
  path: string;
  body?: string;
  authorization?: string;
  idempotencyKey?: string;
}

const projectId = "project-1";

const activeSession = {
  id: "session-active",
  project_id: projectId,
  status: "active",
  title: "Deployment review",
  provider: "test-provider",
  model: "test-model",
  created_at: "2026-01-02T03:04:05Z",
  agent_tokens_used: 42,
};
const closedSession = {
  id: "session-closed",
  project_id: projectId,
  status: "closed",
  title: "Archived activity",
  created_at: "2026-01-01T03:04:05Z",
};

const runRecord: AgentRunRecord = {
  id: "run-a",
  project_id: projectId,
  session_id: "session-active",
  requested_by_employee_id: "employee-1",
  idempotency_key: "run-key-a",
  status: "waiting_approval",
  attempt: 1,
  input_payload: { title: "Deployment review run" },
  output_payload: null,
  failure_code: null,
  failure_message: null,
  sequence: 4,
  started_at: "2026-01-02T03:05:00Z",
  heartbeat_at: null,
  finished_at: null,
  created_at: "2026-01-02T03:05:00Z",
  updated_at: "2026-01-02T03:06:00Z",
};

const approvalItem = (id: string, kind: string, approvalSequence: number, runSequence: number): ApprovalInboxItem => ({
  project_id: projectId,
  session_id: "session-active",
  run_status: "waiting_approval",
  approval_sequence: approvalSequence,
  run_sequence: runSequence,
  approval: {
    id,
    run_id: "run-a",
    tool_call_id: `tool-${id}`,
    approval_kind: kind,
    status: "pending",
    requested_payload: { path: "deploy.yaml" },
    decision_reason: null,
    decided_by_employee_id: null,
    requested_at: "2026-01-02T03:06:00Z",
    expires_at: null,
    decided_at: null,
    created_at: "2026-01-02T03:06:00Z",
    updated_at: "2026-01-02T03:06:00Z",
  },
});

const writeApproval = approvalItem("approval-write", "file.write", 2, 4);
const shellApproval = approvalItem("approval-shell", "shell.exec", 5, 6);

const runWaitingEvent: ControlPlaneEvent = {
  event_id: "event-waiting",
  event_type: "agent_run.waiting_approval",
  schema_version: 1,
  occurred_at: "2026-01-02T03:06:00Z",
  producer: "test-worker",
  aggregate_type: "agent_run",
  aggregate_id: "run-a",
  run_id: "run-a",
  session_id: "session-active",
  correlation_id: null,
  causation_id: null,
  idempotency_key: null,
  sequence: 4,
  payload: { status: "waiting_approval" },
};

function decisionEvent(item: ApprovalInboxItem, decision: "approve" | "deny", idempotencyKey: string): ControlPlaneEvent {
  return {
    event_id: `event-${item.approval.id}-${decision}`,
    event_type: decision === "approve" ? "approval_request.approved" : "approval_request.denied",
    schema_version: 1,
    occurred_at: "2026-01-02T03:07:00Z",
    producer: "test-worker",
    aggregate_type: "approval_request",
    aggregate_id: item.approval.id,
    run_id: "run-a",
    session_id: "session-active",
    correlation_id: null,
    causation_id: null,
    idempotency_key: idempotencyKey,
    sequence: item.approval_sequence + 1,
    payload: { status: decision === "approve" ? "approved" : "denied", control_plane: { command_idempotency_key: idempotencyKey } },
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status >= 200 && status < 300 ? "OK" : "Request failed",
    headers: new Headers({ "Content-Type": "application/json", "X-Has-More": "false" }),
    json: async () => payload,
    text: async () => JSON.stringify(payload),
    blob: async () => new Blob(),
  } as Response;
}

async function waitFor(assertion: () => boolean): Promise<void> {
  const deadline = Date.now() + POLL_BUDGET_MS;
  while (!assertion()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for the RunsCenterPage acceptance condition");
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
  }
}

function pageText(): string { return document.body.textContent ?? ""; }

function approvalCard(approvalKind: string): HTMLElement | undefined {
  return [...document.querySelectorAll<HTMLElement>(".run-approval-card")]
    .find((card) => card.querySelector("h3")?.textContent === approvalKind);
}

function buttonIn(element: HTMLElement, label: string): HTMLButtonElement | undefined {
  return [...element.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent?.trim() === label);
}

function clickPageButton(label: string): void {
  const button = [...document.querySelectorAll<HTMLButtonElement>("button")]
    .find((item) => item.textContent?.trim() === label);
  if (!button) throw new Error(`Missing button: ${label}`);
  button.click();
}

describe("RunsCenterPage route workflow", () => {
  let root: Root | undefined;
  let host: HTMLDivElement | undefined;

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    root = undefined;
    host?.remove();
    host = undefined;
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("restores auth, renders durable runs and sessions, and confirms approval decisions against the persisted event log", async () => {
    const requests: CapturedRequest[] = [];
    const decisions: Array<{ id: string; decision: string }> = [];
    const commandKeys = new Map<string, string>();
    let pendingInbox: ApprovalInboxItem[] = [writeApproval, shellApproval];

    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), window.location.origin);
      const method = init?.method ?? "GET";
      const headers = new Headers(init?.headers);
      const body = typeof init?.body === "string" ? init.body : undefined;
      requests.push({
        method,
        path: `${url.pathname}${url.search}`,
        body,
        authorization: headers.get("Authorization") ?? undefined,
        idempotencyKey: headers.get("Idempotency-Key") ?? undefined,
      });

      if (method === "GET" && url.pathname === "/api/v1/me") {
        // Mirrors the real /me contract (`normalizeCurrentUser` spreads `roles` and
        // `contours` and reads `work_context`), so the identity fixture must carry them.
        return jsonResponse({
          sub: "employee-1",
          email: "ada@example.test",
          roles: [],
          contours: ["employee"],
          active: true,
          company: null,
          employee: { id: "employee-1", status: "active" },
          work_context: { cabinet_id: "cabinet-1", project_id: projectId },
        });
      }
      if (method === "GET" && url.pathname === `/api/v1/projects/${projectId}/agent/runs`) {
        return jsonResponse({ items: [runRecord], limit: 50, offset: 0 });
      }
      if (method === "GET" && url.pathname === `/api/v1/projects/${projectId}/agent/runs/${runRecord.id}`) {
        return jsonResponse(runRecord);
      }
      if (method === "GET" && url.pathname === `/api/v1/projects/${projectId}/agent/runs/${runRecord.id}/events`) {
        const approvalId = url.searchParams.get("aggregate_id");
        if (url.searchParams.get("aggregate_type") === "approval_request" && approvalId) {
          const decided = [...pendingInbox, writeApproval, shellApproval].find((item) => item.approval.id === approvalId);
          const decision = approvalId === writeApproval.approval.id ? "approve" : "deny";
          const key = commandKeys.get(approvalId);
          if (!key) return jsonResponse({ run_id: runRecord.id, items: [], has_more: false, limit: 200 });
          return jsonResponse({ run_id: runRecord.id, items: [decisionEvent(decided ?? writeApproval, decision, key)], has_more: false, limit: 200 });
        }
        return jsonResponse({ run_id: runRecord.id, items: [runWaitingEvent], has_more: false, limit: 100 });
      }
      if (method === "GET" && url.pathname === "/api/v1/agent/approvals/inbox") {
        return jsonResponse({ items: pendingInbox, limit: 50 });
      }
      if (method === "GET" && url.pathname === `/api/v1/projects/${projectId}/agent/sessions`) {
        return jsonResponse({ items: [activeSession, closedSession] });
      }
      if (method === "POST" && url.pathname.startsWith(`/api/v1/projects/${projectId}/agent/approvals/`)
        && url.pathname.endsWith("/decision")) {
        const approvalId = url.pathname.split("/").at(-2) ?? "";
        const payload = JSON.parse(body ?? "{}") as { decision: string };
        const item = pendingInbox.find((candidate) => candidate.approval.id === approvalId);
        if (!item) return jsonResponse({ code: "AGENT_APPROVAL_NOT_PENDING", detail: "already decided" }, 409);
        const key = headers.get("Idempotency-Key") ?? "";
        commandKeys.set(approvalId, key);
        decisions.push({ id: approvalId, decision: payload.decision });
        pendingInbox = pendingInbox.filter((candidate) => candidate.approval.id !== approvalId);
        return jsonResponse({
          approval: { ...item.approval, status: payload.decision === "approve" ? "approved" : "denied" },
          run: runRecord,
          duplicate: false,
          queue_intents_recorded: 1,
        });
      }
      throw new Error(`Unexpected RunsCenterPage API request: ${method} ${url.pathname}${url.search}`);
    });
    vi.stubGlobal("fetch", fetcher);
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ access_token: "runs-test-token", refresh_token: "refresh-test-token" }));

    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => {
      root?.render(
        <MemoryRouter initialEntries={[`/projects/${projectId}/runs`]}>
          <AppProviders>
            <Routes>
              <Route path="/projects/:projectId/runs" element={<AuthenticatedRunsRoute />} />
              <Route path="/projects/:projectId/chat/:sessionId" element={<p>Project chat route</p>} />
            </Routes>
          </AppProviders>
        </MemoryRouter>,
      );
    });
    await waitFor(() => pageText().includes("Deployment review run")
      && pageText().includes("agent_run.waiting_approval")
      && requests.some((request) => request.authorization === "Bearer runs-test-token"));

    expect(document.querySelector("h1")?.textContent).toBe("Runs & approvals");
    expect(document.querySelectorAll(".run-session-card")).toHaveLength(1);
    expect(pageText()).toContain("waiting approval");
    expect(pageText()).toContain("Run run-a · attempt 1 · sequence 4");
    expect(pageText()).toContain("Linked chat session");
    expect(pageText()).toContain("Agent Runs API has no cost field or cost endpoint.");
    expect(pageText()).toContain("Agent Runs API has no run artifact endpoint.");

    // Runs, the scoped inbox and session history are all read for the routed project only.
    expect(requests.find((request) => request.path === "/api/v1/me")?.authorization).toBe("Bearer runs-test-token");
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/agent/runs?limit=50`)?.authorization)
      .toBe("Bearer runs-test-token");
    expect(requests.some((request) => request.path === `/api/v1/projects/${projectId}/agent/sessions`)).toBe(true);
    expect(requests.some((request) => request.path === "/api/v1/agent/approvals/inbox?project_id=project-1&limit=50")).toBe(true);

    await act(async () => { clickPageButton("Approvals 2"); });
    await waitFor(() => approvalCard("file.write") !== undefined && approvalCard("shell.exec") !== undefined);
    expect(pageText()).toContain("Pending human decisions");
    expect(pageText()).toContain('"path": "deploy.yaml"');

    const writeCard = approvalCard("file.write");
    expect(writeCard).toBeDefined();
    await act(async () => { buttonIn(writeCard!, "Approve")?.click(); });
    await waitFor(() => pageText().includes("Approval confirmed by the durable event timeline."));

    const approveCall = requests.find((request) => request.method === "POST" && request.path.endsWith("/approval-write/decision"));
    expect(approveCall).toMatchObject({
      method: "POST",
      path: `/api/v1/projects/${projectId}/agent/approvals/approval-write/decision`,
      body: JSON.stringify({
        expected_approval_sequence: 2,
        expected_run_sequence: 4,
        decision: "approve",
        reason: "Approved in project Runs & approvals",
      }),
      authorization: "Bearer runs-test-token",
    });
    expect(approveCall?.idempotencyKey).toBeTruthy();
    expect(requests.some((request) => request.path
      === `/api/v1/projects/${projectId}/agent/runs/run-a/events?limit=200&aggregate_type=approval_request&aggregate_id=approval-write&after_sequence=2`))
      .toBe(true);
    await waitFor(() => approvalCard("file.write") === undefined);
    expect(approvalCard("shell.exec")).toBeDefined();

    const shellCard = approvalCard("shell.exec");
    expect(shellCard).toBeDefined();
    await act(async () => { buttonIn(shellCard!, "Deny")?.click(); });
    await waitFor(() => pageText().includes("Denial confirmed by the durable event timeline."));

    const denyCall = requests.find((request) => request.method === "POST" && request.path.endsWith("/approval-shell/decision"));
    expect(denyCall).toMatchObject({
      path: `/api/v1/projects/${projectId}/agent/approvals/approval-shell/decision`,
      body: JSON.stringify({
        expected_approval_sequence: 5,
        expected_run_sequence: 6,
        decision: "deny",
        reason: "Denied in project Runs & approvals",
      }),
    });
    expect(denyCall?.idempotencyKey).toBeTruthy();
    // Each approval is decided under its own retained command key; reusing one key across two
    // different decisions would let the backend replay the first outcome for the second.
    expect(approveCall?.idempotencyKey).not.toBe(denyCall?.idempotencyKey);
    await waitFor(() => approvalCard("shell.exec") === undefined);
    expect(pageText()).toContain("No pending approvals");
    expect(decisions).toEqual([
      { id: "approval-write", decision: "approve" },
      { id: "approval-shell", decision: "deny" },
    ]);

    // Sessions stay a distinct, read-only resource: listed from the session API, never
    // promoted into Agent Run records and never written through.
    await act(async () => { clickPageButton("Session history 2"); });
    await waitFor(() => pageText().includes("Deployment review") && pageText().includes("Archived activity"));
    expect(pageText()).toContain("Agent sessions and transcripts are conversation history, not Agent Run records.");
    expect(pageText()).toContain("test-model");
    expect(requests.filter((request) => request.method !== "GET").map(({ path, body }) => ({ path, body }))).toEqual([
      {
        path: `/api/v1/projects/${projectId}/agent/approvals/approval-write/decision`,
        body: JSON.stringify({
          expected_approval_sequence: 2,
          expected_run_sequence: 4,
          decision: "approve",
          reason: "Approved in project Runs & approvals",
        }),
      },
      {
        path: `/api/v1/projects/${projectId}/agent/approvals/approval-shell/decision`,
        body: JSON.stringify({
          expected_approval_sequence: 5,
          expected_run_sequence: 6,
          decision: "deny",
          reason: "Denied in project Runs & approvals",
        }),
      },
    ]);

    const apiPaths = requests.map((request) => request.path);
    expect(apiPaths.some((path) => path.startsWith("/api/") && !path.startsWith("/api/v1/"))).toBe(false);
    // No invented capabilities: the durable Agent Runs API exposes no trace, artifact or cost
    // endpoint, so the page must never request one.
    expect(apiPaths.filter((path) => /\/(?:trace|traces|artifact|artifacts|cost|costs)(?:\/|\?|$)/i.test(path))).toEqual([]);
    expect(apiPaths.filter((path) => /\/tool-approvals|\/pending-approvals/.test(path))).toEqual([]);

    const chatLink = [...document.querySelectorAll<HTMLAnchorElement>("a")].find((link) => link.textContent?.includes("Open chat"));
    expect(chatLink?.getAttribute("href")).toBe(`/projects/${projectId}/chat/session-active`);
  });
});

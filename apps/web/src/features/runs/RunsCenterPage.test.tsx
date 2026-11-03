// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AUTH_STORAGE_KEY } from "../../app/auth/lifecycle";
import { AppProviders, useAuth } from "../../app/context";
import { RunsCenterPage } from "./RunsCenterPage";

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
const initialApprovals = [
  { id: "approval-write", name: "write_file", input: { path: "deploy.yaml", content: "replicas: 3" } },
  { id: "approval-shell", name: "shell", input: { command: "kubectl apply -f deploy.yaml" } },
];
const transcript = {
  session_id: activeSession.id,
  turn_in_progress: false,
  has_more: false,
  blocks: [
    { kind: "user", seq: 1, text: "Please inspect the deployment manifest." },
    { kind: "assistant_markdown", seq: 2, text: "I found a change that needs review." },
    {
      kind: "approval",
      seq: 3,
      id: "approval-write",
      name: "write_file",
      input: initialApprovals[0].input,
      reason: "The manifest update may restart project workloads.",
    },
    {
      kind: "approval",
      seq: 4,
      id: "approval-shell",
      name: "shell",
      input: initialApprovals[1].input,
      reason: "Applying the manifest changes the live project runtime.",
    },
  ],
};

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status >= 200 && status < 300 ? "OK" : "Request failed",
    json: async () => payload,
    text: async () => JSON.stringify(payload),
    blob: async () => new Blob(),
  } as Response;
}

async function waitFor(assertion: () => boolean): Promise<void> {
  const deadline = Date.now() + 3000;
  while (!assertion()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for the RunsCenterPage acceptance condition");
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
  }
}

function approvalCard(approvalId: string): HTMLElement | undefined {
  return [...document.querySelectorAll<HTMLElement>(".run-approval-card")]
    .find((card) => !card.classList.contains("run-inbox-card")
      && card.querySelector("h4")?.textContent === (approvalId === "approval-write" ? "write_file" : "shell"));
}

function buttonIn(element: HTMLElement, label: string): HTMLButtonElement | undefined {
  return [...element.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent?.trim() === label);
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
    vi.unstubAllGlobals();
  });

  it("loads and switches sessions, renders transcript approvals, confirms decisions, and avoids unsupported run APIs", async () => {
    const requests: CapturedRequest[] = [];
    const decisions: Array<{ id: string; decision: string }> = [];
    let pendingApprovals = [...initialApprovals];

    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), window.location.origin);
      const method = init?.method ?? "GET";
      const headers = new Headers(init?.headers);
      const body = typeof init?.body === "string" ? init.body : undefined;
      requests.push({ method, path: `${url.pathname}${url.search}`, body, authorization: headers.get("Authorization") ?? undefined });

      if (method === "GET" && url.pathname === "/api/v1/me") {
        return jsonResponse({ id: "employee-1", subject: "employee-1", employee_id: "employee-1", active: true, roles: [] });
      }
      if (method === "GET" && url.pathname === `/api/v1/projects/${projectId}/agent/sessions`) {
        return jsonResponse({ items: [activeSession, closedSession] });
      }
      if (method === "GET" && url.pathname === `/api/v1/projects/${projectId}/chat/transcript`) {
        return jsonResponse(url.searchParams.get("session_id") === activeSession.id
          ? transcript
          : { session_id: closedSession.id, turn_in_progress: false, has_more: false, blocks: [] });
      }
      if (method === "GET" && url.pathname === `/api/v1/projects/${projectId}/agent/sessions/${activeSession.id}/pending-approvals`) {
        return jsonResponse({ items: pendingApprovals });
      }
      if (method === "GET" && url.pathname === `/api/v1/projects/${projectId}/agent/sessions/${closedSession.id}/pending-approvals`) {
        return jsonResponse({ items: [] });
      }
      if (method === "GET" && url.pathname === `/api/v1/projects/${projectId}/agent/sessions/${activeSession.id}`) {
        return jsonResponse(activeSession);
      }
      if (method === "GET" && url.pathname === `/api/v1/projects/${projectId}/agent/sessions/${closedSession.id}`) {
        return jsonResponse(closedSession);
      }
      if (method === "POST" && url.pathname === `/api/v1/projects/${projectId}/agent/sessions/${activeSession.id}/tool-approvals`) {
        const decision = JSON.parse(body ?? "{}") as { id: string; decision: string };
        decisions.push(decision);
        pendingApprovals = pendingApprovals.filter((approval) => approval.id !== decision.id);
        return jsonResponse({ session_id: activeSession.id, approval_id: decision.id, decision: decision.decision });
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
        <MemoryRouter initialEntries={[`/projects/${projectId}/runs?session=${activeSession.id}`]}>
          <AppProviders>
            <Routes>
              <Route path="/projects/:projectId/runs" element={<AuthenticatedRunsRoute />} />
            </Routes>
          </AppProviders>
        </MemoryRouter>,
      );
    });
    await waitFor(() => approvalCard("approval-write") !== undefined
      && requests.some((request) => request.authorization === "Bearer runs-test-token"));

    const pageText = () => document.body.textContent ?? "";
    expect(document.querySelector("h1")?.textContent).toBe("Runs & approvals");
    expect(document.querySelectorAll(".run-session-card")).toHaveLength(2);
    expect(pageText()).toContain("Deployment review");
    expect(pageText()).toContain("Archived activity");
    expect(pageText()).toContain("Source: agent session + transcript API");
    expect(pageText()).toContain("Session: active");
    expect(pageText()).toContain("42");
    expect(pageText()).toContain("Please inspect the deployment manifest.");
    expect(pageText()).toContain("I found a change that needs review.");
    expect(pageText()).toContain("The manifest update may restart project workloads.");
    expect(pageText()).toContain("Applying the manifest changes the live project runtime.");
    expect(pageText()).toContain('"path": "deploy.yaml"');
    expect(pageText()).toContain("Run-level history is not exposed by this backend.");
    expect(pageText()).toContain("No run status endpoint");
    expect(pageText()).toContain("No run cost API or cost values");
    expect(pageText()).toContain("no run trace endpoint");

    const closedSessionButton = [...document.querySelectorAll<HTMLButtonElement>(".run-session-card")]
      .find((button) => button.textContent?.includes("Archived activity"));
    expect(closedSessionButton).toBeDefined();
    const requestsBeforeClosedSession = requests.length;
    await act(async () => { closedSessionButton?.click(); });
    await waitFor(() => pageText().includes("No saved activity yet")
      && document.querySelector(".run-detail-actions button")?.textContent !== "Refreshing…"
      && requests.slice(requestsBeforeClosedSession).some((request) => request.method === "GET"
        && request.path.startsWith(`/api/v1/projects/${projectId}/chat/transcript?session_id=${closedSession.id}`)));
    expect(document.querySelector(".run-detail-header h2")?.textContent).toBe("Archived activity");

    const activeSessionButton = [...document.querySelectorAll<HTMLButtonElement>(".run-session-card")]
      .find((button) => button.textContent?.includes("Deployment review"));
    expect(activeSessionButton).toBeDefined();
    const requestsBeforeActiveSession = requests.length;
    await act(async () => { activeSessionButton?.click(); });
    await waitFor(() => approvalCard("approval-write") !== undefined
      && pageText().includes("Please inspect the deployment manifest.")
      && requests.slice(requestsBeforeActiveSession).some((request) => request.method === "GET"
        && request.path.startsWith(`/api/v1/projects/${projectId}/chat/transcript?session_id=${activeSession.id}`))
      && requests.slice(requestsBeforeActiveSession).some((request) => request.method === "GET"
        && request.path === `/api/v1/projects/${projectId}/agent/sessions/${activeSession.id}/pending-approvals`));

    const writeApproval = approvalCard("approval-write");
    expect(writeApproval).toBeDefined();
    await act(async () => { buttonIn(writeApproval!, "Approve action")?.click(); });
    await waitFor(() => (document.body.textContent ?? "").includes("Approval decision saved; confirmed absent from pending inbox."));
    expect(approvalCard("approval-write")).toBeUndefined();
    expect(pageText()).toContain("Approval decision saved; confirmed absent from pending inbox.");
    expect(approvalCard("approval-shell")).toBeDefined();
    const approvePostIndex = requests.findIndex((request) => request.method === "POST"
      && request.path === `/api/v1/projects/${projectId}/agent/sessions/${activeSession.id}/tool-approvals`
      && request.body === JSON.stringify({ id: "approval-write", decision: "approve" }));
    expect(approvePostIndex).toBeGreaterThanOrEqual(0);
    expect(requests.slice(approvePostIndex + 1).some((request) => request.method === "GET"
      && request.path === `/api/v1/projects/${projectId}/agent/sessions/${activeSession.id}/pending-approvals`)).toBe(true);

    const shellApproval = approvalCard("approval-shell");
    expect(shellApproval).toBeDefined();
    await act(async () => { buttonIn(shellApproval!, "Reject")?.click(); });
    await waitFor(() => (document.body.textContent ?? "").includes("Rejection saved; confirmed absent from pending inbox."));
    expect(approvalCard("approval-shell")).toBeUndefined();
    expect(pageText()).toContain("Rejection saved; confirmed absent from pending inbox.");
    const rejectPostIndex = requests.findIndex((request) => request.method === "POST"
      && request.path === `/api/v1/projects/${projectId}/agent/sessions/${activeSession.id}/tool-approvals`
      && request.body === JSON.stringify({ id: "approval-shell", decision: "deny" }));
    expect(rejectPostIndex).toBeGreaterThanOrEqual(0);
    expect(requests.slice(rejectPostIndex + 1).some((request) => request.method === "GET"
      && request.path === `/api/v1/projects/${projectId}/agent/sessions/${activeSession.id}/pending-approvals`)).toBe(true);
    expect(pageText()).toContain("No pending approvals");
    expect(decisions).toEqual([
      { id: "approval-write", decision: "approve" },
      { id: "approval-shell", decision: "deny" },
    ]);
    expect(requests.filter((request) => request.method === "POST").map(({ path, body }) => ({ path, body }))).toEqual([
      {
        path: `/api/v1/projects/${projectId}/agent/sessions/${activeSession.id}/tool-approvals`,
        body: JSON.stringify({ id: "approval-write", decision: "approve" }),
      },
      {
        path: `/api/v1/projects/${projectId}/agent/sessions/${activeSession.id}/tool-approvals`,
        body: JSON.stringify({ id: "approval-shell", decision: "deny" }),
      },
    ]);

    const apiPaths = requests.map((request) => request.path);
    expect(apiPaths).toContain("/api/v1/me");
    expect(apiPaths).toContain(`/api/v1/projects/${projectId}/agent/sessions`);
    expect(apiPaths.some((path) => path.startsWith(`/api/v1/projects/${projectId}/chat/transcript?session_id=${activeSession.id}`))).toBe(true);
    expect(requests.find((request) => request.path === "/api/v1/me")?.authorization).toBe("Bearer runs-test-token");
    expect(requests.find((request) => request.path === `/api/v1/projects/${projectId}/agent/sessions`)?.authorization).toBe("Bearer runs-test-token");
    expect(apiPaths.filter((path) => /\/(?:runs|traces?|artifacts|costs?)(?:\/|\?|$)/i.test(path))).toEqual([]);
  });
});

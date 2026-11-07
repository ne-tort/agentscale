// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRunsService, RunsRequestError } from "./service";
import { RUN_DETAIL_POLL_INTERVAL_MS, RunsCenterPage } from "./RunsCenterPage";
import type { AgentRunRecord, ApprovalInboxItem, ControlPlaneEvent } from "./contracts";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import { POLL_BUDGET_MS } from "../../test-support/poll-budget";

vi.mock("../../app/context", () => ({ useAuth: () => ({ tokens: { access_token: "journey-token" } }) }));

const run = (id: string, patch: Partial<AgentRunRecord> = {}): AgentRunRecord => ({
  id,
  project_id: "project-a",
  session_id: null,
  requested_by_employee_id: "employee-a",
  idempotency_key: `key-${id}`,
  status: "running",
  attempt: 1,
  input_payload: { title: id },
  output_payload: null,
  failure_code: null,
  failure_message: null,
  sequence: 3,
  started_at: "2026-11-03T08:00:00Z",
  heartbeat_at: null,
  finished_at: null,
  created_at: "2026-11-03T08:00:00Z",
  updated_at: "2026-11-03T08:00:00Z",
  ...patch,
});

const approval: ApprovalInboxItem = {
  project_id: "project-a",
  session_id: "session-a",
  run_status: "waiting_approval",
  approval_sequence: 2,
  run_sequence: 4,
  approval: {
    id: "approval-a",
    run_id: "run-a",
    tool_call_id: "tool-9",
    approval_kind: "file.write",
    status: "pending",
    requested_payload: { path: "notes.md" },
    decision_reason: null,
    decided_by_employee_id: null,
    requested_at: "2026-11-03T08:00:00Z",
    expires_at: null,
    decided_at: null,
    created_at: "2026-11-03T08:00:00Z",
    updated_at: "2026-11-03T08:00:00Z",
  },
};

const event = (patch: Partial<ControlPlaneEvent> = {}): ControlPlaneEvent => ({
  event_id: "event-a",
  event_type: "approval_request.approved",
  schema_version: 1,
  occurred_at: "2026-11-03T08:01:00Z",
  producer: "test-worker",
  aggregate_type: "approval_request",
  aggregate_id: "approval-a",
  run_id: "run-a",
  session_id: "session-a",
  correlation_id: null,
  causation_id: null,
  idempotency_key: "command-a",
  sequence: 3,
  payload: { status: "approved", reason: "confirmed", control_plane: { command_idempotency_key: "command-a" } },
  ...patch,
});

const jsonResponse = (body: unknown, status = 200, headers: Record<string, string> = {}) => new Response(
  JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } },
);
const serviceWith = (fetcher: typeof fetch) => createRunsService({ baseUrl: "/api/v1", getAccessToken: () => "test-access", fetcher });

let pageRoot: Root | undefined;
let pageHost: HTMLDivElement | undefined;
let hiddenDescriptor: PropertyDescriptor | undefined;

// The page reads its copy through `useT`; English keeps these assertions about
// polling and write readback rather than about translation.
beforeEach(() => {
  window.localStorage.setItem("agentscale.locale", "en");
});

afterEach(async () => {
  if (pageRoot) await act(async () => pageRoot?.unmount());
  pageRoot = undefined;
  pageHost?.remove();
  pageHost = undefined;
  if (hiddenDescriptor) Object.defineProperty(document, "hidden", hiddenDescriptor);
  else Reflect.deleteProperty(document, "hidden");
  hiddenDescriptor = undefined;
  vi.useRealTimers();
  window.sessionStorage.clear();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

async function waitForPage(assertion: () => boolean): Promise<void> {
  const deadline = Date.now() + POLL_BUDGET_MS;
  while (!assertion()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for Runs Center acceptance condition");
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
  }
}

function pageText(): string { return document.body.textContent ?? ""; }

/** The selected run's heading; the run id itself lives in its tooltip. */
function detailTitle(): string {
  return document.querySelector(".run-detail-panel h2")?.textContent ?? "";
}

async function flushPendingFetches(): Promise<void> {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

async function mountRunPage(entry: string, projectRoute = "/projects/:projectId/runs", extraRoutes = false) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  pageHost = document.createElement("div");
  document.body.appendChild(pageHost);
  pageRoot = createRoot(pageHost);
  await act(async () => { pageRoot?.render(<SettingsProvider>{runPageRouter(entry, projectRoute, extraRoutes)}</SettingsProvider>); });
}

async function waitForPageMicrotasks(assertion: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 100 && !assertion(); attempt += 1) {
    await act(async () => { await Promise.resolve(); });
  }
  if (!assertion()) throw new Error("Timed out waiting for Runs Center state after fetch microtasks");
}

function clickPageButton(label: string): void {
  const button = [...document.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.trim() === label);
  if (!button) throw new Error(`Missing button: ${label}`);
  button.click();
}

function runPageRouter(entry: string, projectRoute = "/projects/:projectId/runs", extraRoutes = false) {
  return <MemoryRouter initialEntries={[entry]}><Routes>
    <Route path={projectRoute} element={<><RunsCenterPage />{extraRoutes && <ProjectSwitcher />}</>} />
    {extraRoutes && <Route path="/projects/:projectId/chat/:sessionId" element={<ProjectChatRoute />} />}
  </Routes></MemoryRouter>;
}

function ProjectChatRoute() {
  const navigate = useNavigate();
  return <><p>Project chat route</p><button type="button" onClick={() => navigate("/projects/project-a/runs?run=run-a")}>Return to runs</button></>;
}

function ProjectSwitcher() {
  const navigate = useNavigate();
  return <span>
    <button type="button" onClick={() => navigate("/projects/project-b/runs?run=run-b")}>Switch project</button>
    <button type="button" onClick={() => navigate("/projects/project-a/runs?run=run-a")}>Return to project</button>
    <button type="button" onClick={() => navigate("/projects/project-a/runs?run=run-new")}>Select another run</button>
    <button type="button" onClick={() => navigate("/projects/project-a/runs")}>Deselect run</button>
  </span>;
}

describe("Runs Center backend acceptance contracts", () => {
  it("forwards abort signals to run detail and persisted event reads", async () => {
    const observedSignals: Array<AbortSignal | null | undefined> = [];
    const fetcher: typeof fetch = async (_input, init) => {
      observedSignals.push(init?.signal);
      return jsonResponse({ id: "run-a", project_id: "project-a", session_id: null, requested_by_employee_id: "employee-a", idempotency_key: "key-run-a", status: "running", attempt: 1, input_payload: {}, output_payload: null, failure_code: null, failure_message: null, sequence: 3, started_at: null, heartbeat_at: null, finished_at: null, created_at: "2026-11-03T08:00:00Z", updated_at: "2026-11-03T08:00:00Z", run_id: "run-a", items: [], has_more: false, limit: 100 }, 200, { "X-Has-More": "false" });
    };
    const controller = new AbortController();
    const api = serviceWith(fetcher);
    await api.getRun("project-a", "run-a", controller.signal);
    await api.listRunEvents("project-a", "run-a", { limit: 100 }, controller.signal);
    expect(observedSignals).toEqual([controller.signal, controller.signal]);
  });

  it("pages multiple project runs with the backend keyset cursor", async () => {
    const calls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${url.pathname}${url.search}`);
      return !url.searchParams.has("cursor")
        ? jsonResponse({ items: [run("run-a")], limit: 1, offset: 0 }, 200, { "X-Has-More": "true", "X-Next-Cursor": "run-cursor" })
        : jsonResponse({ items: [run("run-b")], limit: 1, offset: 0 }, 200, { "X-Has-More": "false" });
    };
    const api = serviceWith(fetcher);
    const first = await api.listRuns("project-a", { limit: 1 });
    const second = await api.listRuns("project-a", { limit: 1, cursor: first.nextCursor });
    expect(first.items.map((item) => item.id)).toEqual(["run-a"]);
    expect(first.hasMore).toBe(true);
    expect(second.items.map((item) => item.id)).toEqual(["run-b"]);
    expect(second.hasMore).toBe(false);
    expect(calls).toEqual([
      "/api/v1/projects/project-a/agent/runs?limit=1",
      "/api/v1/projects/project-a/agent/runs?limit=1&cursor=run-cursor",
    ]);
  });

  it("pages event envelopes with an opaque cursor and preserves aggregate filtering", async () => {
    const calls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${url.pathname}${url.search}`);
      return !url.searchParams.has("cursor")
        ? jsonResponse({ run_id: "run-a", items: [event({ aggregate_type: "agent_run", aggregate_id: "run-a" })], has_more: true, limit: 1 }, 200, { "X-Has-More": "true", "X-Next-Cursor": "event-cursor" })
        : jsonResponse({ run_id: "run-a", items: [event({ event_id: "event-b", sequence: 4, aggregate_type: "agent_run", aggregate_id: "run-a" })], has_more: false, limit: 1 }, 200, { "X-Has-More": "false" });
    };
    const api = serviceWith(fetcher);
    const first = await api.listRunEvents("project-a", "run-a", { limit: 1, aggregateType: "agent_run", aggregateId: "run-a" });
    const second = await api.listRunEvents("project-a", "run-a", { limit: 1, cursor: first.nextCursor, aggregateType: "agent_run", aggregateId: "run-a" });
    expect(first.items[0].event_id).toBe("event-a");
    expect(second.items[0].event_id).toBe("event-b");
    expect(calls[0]).toBe("/api/v1/projects/project-a/agent/runs/run-a/events?limit=1&aggregate_type=agent_run&aggregate_id=run-a");
    expect(calls[1]).toContain("cursor=event-cursor");
    expect(calls[1]).not.toContain("after_sequence");
  });

  it("confirms approval decision by matching persisted event sequence and idempotency key", async () => {
    const calls: Array<{ path: string; method: string; body?: string; key?: string }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = new URL(String(input), "http://localhost");
      calls.push({ path: `${url.pathname}${url.search}`, method: init?.method ?? "GET", body: String(init?.body ?? ""), key: new Headers(init?.headers).get("Idempotency-Key") ?? undefined });
      if (init?.method === "POST") return jsonResponse({ approval: { ...approval.approval, status: "approved" }, run: run("run-a"), duplicate: false, queue_intents_recorded: 2 });
      return jsonResponse({ run_id: "run-a", has_more: false, limit: 200, items: [event()] }, 200, { "X-Has-More": "false" });
    };
    const result = await serviceWith(fetcher).decideApprovalWithReadback("project-a", { item: approval, decision: "approve", reason: "confirmed", idempotencyKey: "command-a" });
    expect(result).toMatchObject({ kind: "confirmed", value: { event_type: "approval_request.approved", sequence: 3 } });
    expect(calls[0]).toMatchObject({
      path: "/api/v1/projects/project-a/agent/approvals/approval-a/decision",
      method: "POST",
      key: "command-a",
      body: JSON.stringify({ expected_approval_sequence: 2, expected_run_sequence: 4, decision: "approve", reason: "confirmed" }),
    });
    expect(calls[1].path).toBe("/api/v1/projects/project-a/agent/runs/run-a/events?limit=200&aggregate_type=approval_request&aggregate_id=approval-a&after_sequence=2");
  });

  it("reports an approval conflict if the requested CAS event was not committed", async () => {
    const fetcher: typeof fetch = async (_input, init) => init?.method === "POST"
      ? jsonResponse({ code: "AGENT_RUN_CONFLICT", detail: "sequence changed" }, 409)
      : jsonResponse({ run_id: "run-a", has_more: false, limit: 200, items: [] }, 200, { "X-Has-More": "false" });
    const result = await serviceWith(fetcher).decideApprovalWithReadback("project-a", { item: approval, decision: "deny", idempotencyKey: "command-b" });
    expect(result).toMatchObject({ kind: "conflict", error: expect.objectContaining({ status: 409, code: "AGENT_RUN_CONFLICT" }) });
  });

  it("returns current persisted run state when cancel sequence conflicts", async () => {
    const calls: string[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${init?.method ?? "GET"} ${url.pathname}${url.search}`);
      if (url.pathname === "/api/v1/projects/project-a/agent/runs") return jsonResponse({ items: [run("run-cancel", { status: "running" })], limit: 50, offset: 0 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/agent/approvals/inbox") return jsonResponse({ items: [], limit: 50 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/projects/project-a/agent/sessions") return jsonResponse({ items: [] });
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-cancel") return jsonResponse(run("run-cancel", { sequence: 4, status: "succeeded" }));
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-cancel/events") return jsonResponse({
        run_id: "run-cancel", limit: 20, has_more: false,
        items: [event({ event_type: "agent_run.succeeded", aggregate_type: "agent_run", aggregate_id: "run-cancel", sequence: 4, payload: { status: "succeeded" } })],
      }, 200, { "X-Has-More": "false" });
      if (url.pathname.endsWith("/cancel") && init?.method === "POST") return jsonResponse({ code: "AGENT_RUN_CONFLICT", detail: "run advanced before cancellation" }, 409);
      throw new Error(`Unexpected cancellation request: ${url.pathname}${url.search}`);
    };
    const result = await serviceWith(fetcher).cancelRunWithReadback("project-a", { run: run("run-cancel", { status: "running" }), reason: "stop", idempotencyKey: "cancel-key" });
    expect(result).toMatchObject({ kind: "conflict", current: { status: "succeeded", sequence: 4 }, error: expect.objectContaining({ status: 409 }) });
    expect(calls).toEqual([
      "POST /api/v1/projects/project-a/agent/runs/run-cancel/cancel",
      "GET /api/v1/projects/project-a/agent/runs/run-cancel",
      "GET /api/v1/projects/project-a/agent/runs/run-cancel/events?limit=20&aggregate_type=agent_run&aggregate_id=run-cancel&after_sequence=3",
    ]);
  });

  it("keeps the run write outcome unknown when durable readback fails after a network error", async () => {
    const fetcher: typeof fetch = async (input, init) => {
      const url = new URL(String(input), "http://localhost");
      if (init?.method === "POST") throw new TypeError("connection reset after request body");
      if (url.pathname.endsWith("/agent/runs")) return jsonResponse({ code: "AGENT_RUN_PERSISTENCE_BLOCKED", detail: "read unavailable" }, 503);
      throw new Error("unexpected readback");
    };
    const result = await serviceWith(fetcher).createRunWithReadback("project-a", { session_id: null, input_payload: { title: "once" } }, "stable-create-key");
    expect(result.kind).toBe("unknown");
    if (result.kind === "unknown") {
      expect(result.error.message).toContain("connection reset");
      expect(result.readbackError).toBeInstanceOf(RunsRequestError);
    }
  });

  it("isolates project run and tenant inbox empty/error results to the supplied project", async () => {
    const paths: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input), "http://localhost");
      paths.push(`${url.pathname}${url.search}`);
      if (url.searchParams.get("project_id") === "project-denied" || url.pathname.includes("project-denied")) return jsonResponse({ code: "NOT_FOUND", detail: "Project not found" }, 404);
      return jsonResponse({ items: [], limit: 50, offset: 0 }, 200, { "X-Has-More": "false" });
    };
    const api = serviceWith(fetcher);
    await expect(api.listRuns("project-empty")).resolves.toMatchObject({ items: [], hasMore: false });
    await expect(api.listApprovalInbox("project-empty")).resolves.toMatchObject({ items: [], hasMore: false });
    await expect(api.listRuns("project-denied")).rejects.toMatchObject({ status: 404, code: "NOT_FOUND" } satisfies Partial<RunsRequestError>);
    await expect(api.listApprovalInbox("project-denied")).rejects.toMatchObject({ status: 404, code: "NOT_FOUND" } satisfies Partial<RunsRequestError>);
    expect(paths).toEqual([
      "/api/v1/projects/project-empty/agent/runs?limit=50",
      "/api/v1/agent/approvals/inbox?project_id=project-empty&limit=50",
      "/api/v1/projects/project-denied/agent/runs?limit=50",
      "/api/v1/agent/approvals/inbox?project_id=project-denied&limit=50",
    ]);
  });
});

describe("Runs Center user journey", () => {
  it("renders the page in Russian for a Russian reader", async () => {
    window.localStorage.setItem("agentscale.locale", "ru");
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/projects/project-a/agent/runs") return jsonResponse({ items: [], limit: 50, offset: 0 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/agent/approvals/inbox") return jsonResponse({ items: [], limit: 50 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/projects/project-a/agent/sessions") return jsonResponse({ items: [] });
      throw new Error(`Unexpected Russian-copy request: ${url.pathname}${url.search}`);
    };
    vi.stubGlobal("fetch", fetcher);
    await mountRunPage("/projects/project-a/runs");
    await waitForPageMicrotasks(() => pageText().includes("Запусков пока нет"));
    expect(document.querySelector("h1")?.textContent).toBe("Запуски и подтверждения");
    expect(pageText()).toContain("Подтверждения");
    expect(pageText()).toContain("История чатов");
    expect(pageText()).toContain("Выберите запуск");
    expect(pageText()).toContain("Создать запуск");
  });

  it("loads durable run detail, event history and project inbox, then verifies an approval decision", async () => {
    const realRun = run("run-a", { sequence: 4, status: "waiting_approval", session_id: "session-a" });
    const inboxItem: ApprovalInboxItem = { ...approval, run_status: "waiting_approval" };
    const runEvent = event({ event_type: "agent_run.waiting_approval", aggregate_type: "agent_run", aggregate_id: "run-a", run_id: "run-a", sequence: 4, payload: { status: "waiting_approval" } });
    const calls: Array<{ path: string; method: string; body?: string; key?: string }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = new URL(String(input), "http://localhost");
      calls.push({ path: `${url.pathname}${url.search}`, method: init?.method ?? "GET", body: String(init?.body ?? ""), key: new Headers(init?.headers).get("Idempotency-Key") ?? undefined });
      if (url.pathname === "/api/v1/projects/project-a/agent/runs") return jsonResponse({ items: [realRun], limit: 50, offset: 0 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/agent/approvals/inbox") return init?.method === "POST"
        ? jsonResponse({ items: [], limit: 50 }, 200, { "X-Has-More": "false" })
        : jsonResponse({ items: [inboxItem], limit: 50 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/projects/project-a/agent/sessions") return jsonResponse({ items: [] });
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-a") return jsonResponse(realRun);
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-a/events") {
        if (url.searchParams.get("aggregate_type") === "approval_request") {
          const key = calls.find((call) => call.method === "POST")?.key ?? "missing";
          return jsonResponse({ run_id: "run-a", items: [event({
            event_type: "approval_request.approved", sequence: 3,
            payload: { status: "approved", control_plane: { command_idempotency_key: key } },
          })], has_more: false, limit: 200 }, 200, { "X-Has-More": "false" });
        }
        return jsonResponse({ run_id: "run-a", items: [runEvent], has_more: false, limit: 100 }, 200, { "X-Has-More": "false" });
      }
      if (url.pathname === "/api/v1/projects/project-a/agent/approvals/approval-a/decision" && init?.method === "POST") {
        return jsonResponse({ approval: { ...approval.approval, status: "approved" }, run: realRun, duplicate: false, queue_intents_recorded: 2 });
      }
      throw new Error(`Unexpected user-journey request: ${init?.method ?? "GET"} ${url.pathname}${url.search}`);
    };
    vi.stubGlobal("fetch", fetcher);
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    pageHost = document.createElement("div");
    document.body.appendChild(pageHost);
    pageRoot = createRoot(pageHost);
    await act(async () => {
      pageRoot?.render(<SettingsProvider><MemoryRouter initialEntries={["/projects/project-a/runs"]}><Routes><Route path="/projects/:projectId/runs" element={<RunsCenterPage />} /></Routes></MemoryRouter></SettingsProvider>);
    });
    await waitForPage(() => pageText().includes("agent_run.waiting_approval") && pageText().includes("run-a"));
    expect(pageText()).toContain("No data");
    expect(pageText()).toContain("Cost is not reported for runs yet.");
    expect(pageText()).toContain("Result files are not reported for runs yet.");
    expect(calls.some((call) => call.path === "/api/v1/projects/project-a/agent/runs/run-a/events?limit=100")).toBe(true);

    await act(async () => { clickPageButton("Approvals 1"); });
    await waitForPage(() => pageText().includes("file.write"));
    await act(async () => { clickPageButton("Approve"); });
    await waitForPage(() => pageText().includes("Approval confirmed."));
    const decision = calls.find((call) => call.method === "POST");
    expect(decision).toMatchObject({
      path: "/api/v1/projects/project-a/agent/approvals/approval-a/decision",
      body: JSON.stringify({ expected_approval_sequence: 2, expected_run_sequence: 4, decision: "approve", reason: "Approved in project Runs & approvals" }),
    });
    expect(decision?.key).toBeTruthy();
    expect(calls.some((call) => call.path.includes("aggregate_type=approval_request") && call.path.includes("after_sequence=2"))).toBe(true);
    expect(calls.some((call) => call.path === "/api/v1/agent/approvals/inbox?project_id=project-a&limit=50")).toBe(true);
  });

  it("renders an empty project inbox and a retryable tenant/API read error without inventing approvals", async () => {
    let unavailable = true;
    const calls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${url.pathname}${url.search}`);
      if (url.pathname === "/api/v1/projects/project-a/agent/runs") return jsonResponse({ items: [], limit: 50, offset: 0 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/agent/approvals/inbox") return unavailable
        ? jsonResponse({ code: "AGENT_RUN_API_DISABLED", detail: "durable API disabled" }, 503)
        : jsonResponse({ items: [], limit: 50 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/projects/project-a/agent/sessions") return jsonResponse({ items: [] });
      throw new Error(`Unexpected scoped inbox request: ${url.pathname}${url.search}`);
    };
    vi.stubGlobal("fetch", fetcher);
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    pageHost = document.createElement("div");
    document.body.appendChild(pageHost);
    pageRoot = createRoot(pageHost);
    await act(async () => {
      pageRoot?.render(<SettingsProvider><MemoryRouter initialEntries={["/projects/project-a/runs"]}><Routes><Route path="/projects/:projectId/runs" element={<RunsCenterPage />} /></Routes></MemoryRouter></SettingsProvider>);
    });
    await waitForPage(() => calls.some((path) => path.startsWith("/api/v1/agent/approvals/inbox")));
    await act(async () => { clickPageButton("Approvals 0"); });
    await waitForPage(() => pageText().includes("Runs are not available in this environment yet."));
    expect(pageText()).toContain("Retry approval inbox");
    expect(calls).toContain("/api/v1/agent/approvals/inbox?project_id=project-a&limit=50");
    unavailable = false;
    await act(async () => { clickPageButton("Retry approval inbox"); });
    await waitForPage(() => pageText().includes("No pending approvals"));
    expect(pageText()).toContain("Nothing is waiting for your decision right now.");
  });

  it("finds a deep-linked run on a later cursor page without faking client pagination", async () => {
    const calls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${url.pathname}${url.search}`);
      if (url.pathname === "/api/v1/projects/project-a/agent/runs") return !url.searchParams.has("cursor")
        ? jsonResponse({ items: [run("first")], limit: 1, offset: 0 }, 200, { "X-Has-More": "true", "X-Next-Cursor": "page-2" })
        : jsonResponse({ items: [run("deep-linked")], limit: 1, offset: 0 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/agent/approvals/inbox") return jsonResponse({ items: [], limit: 50 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/projects/project-a/agent/sessions") return jsonResponse({ items: [] });
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/deep-linked") return jsonResponse(run("deep-linked"));
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/deep-linked/events") return jsonResponse({ run_id: "deep-linked", items: [], has_more: false, limit: 100 }, 200, { "X-Has-More": "false" });
      throw new Error(`Unexpected deep-link request: ${url.pathname}${url.search}`);
    };
    vi.stubGlobal("fetch", fetcher);
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    pageHost = document.createElement("div");
    document.body.appendChild(pageHost);
    pageRoot = createRoot(pageHost);
    await act(async () => {
      pageRoot?.render(<SettingsProvider><MemoryRouter initialEntries={["/projects/project-a/runs?run=deep-linked"]}><Routes><Route path="/projects/:projectId/runs" element={<RunsCenterPage />} /></Routes></MemoryRouter></SettingsProvider>);
    });
    await waitForPage(() => detailTitle().includes("deep-linked"));
    expect(calls).toContain("/api/v1/projects/project-a/agent/runs?limit=50&cursor=page-2");
    expect(calls).toContain("/api/v1/projects/project-a/agent/runs/deep-linked");
  });

  it("polls selected run and persisted events until GET readback is terminal", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    let currentRun = run("run-live", { sequence: 3, status: "running" });
    const succeededEvent = event({
      event_id: "run-live-succeeded",
      event_type: "agent_run.succeeded",
      aggregate_type: "agent_run",
      aggregate_id: "run-live",
      run_id: "run-live",
      sequence: 4,
      payload: { status: "succeeded" },
    });
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${url.pathname}${url.search}`);
      if (url.pathname === "/api/v1/projects/project-a/agent/runs") return jsonResponse({ items: [currentRun], limit: 50, offset: 0 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/agent/approvals/inbox") return jsonResponse({ items: [], limit: 50 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/projects/project-a/agent/sessions") return jsonResponse({ items: [] });
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-live") return jsonResponse(currentRun);
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-live/events") return jsonResponse({
        run_id: "run-live", items: currentRun.status === "succeeded" ? [succeededEvent] : [], has_more: false, limit: 500,
      }, 200, { "X-Has-More": "false" });
      throw new Error(`Unexpected poll request: ${url.pathname}${url.search}`);
    };
    vi.stubGlobal("fetch", fetcher);
    await mountRunPage("/projects/project-a/runs?run=run-live");
    await waitForPageMicrotasks(() => detailTitle().includes("run-live"));
    const readsBeforeTick = calls.filter((path) => path.endsWith("/run-live") || path.includes("/run-live/events")).length;
    currentRun = run("run-live", { sequence: 4, status: "succeeded", output_payload: { result: "complete" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS); });
    await waitForPageMicrotasks(() => pageText().includes("agent_run.succeeded"));
    expect(pageText()).toContain("Finished");
    expect(pageText()).toContain("complete");
    expect(calls.filter((path) => path.endsWith("/run-live") || path.includes("/run-live/events")).length).toBeGreaterThan(readsBeforeTick);
    const readsAtTerminal = calls.filter((path) => path.endsWith("/run-live") || path.includes("/run-live/events")).length;
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS * 3); });
    expect(calls.filter((path) => path.endsWith("/run-live") || path.includes("/run-live/events")).length).toBe(readsAtTerminal);
  });

  it("keeps last-known run state through a transient poll error and retries on the bounded interval", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    let failNextPoll = false;
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${url.pathname}${url.search}`);
      if (url.pathname === "/api/v1/projects/project-a/agent/runs") return jsonResponse({ items: [run("run-retry")], limit: 50, offset: 0 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/agent/approvals/inbox") return jsonResponse({ items: [], limit: 50 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/projects/project-a/agent/sessions") return jsonResponse({ items: [] });
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-retry") return failNextPoll
        ? jsonResponse({ detail: "temporary read failure" }, 503)
        : jsonResponse(run("run-retry"));
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-retry/events") return jsonResponse({ run_id: "run-retry", items: [], has_more: false, limit: 100 }, 200, { "X-Has-More": "false" });
      throw new Error(`Unexpected transient poll request: ${url.pathname}${url.search}`);
    };
    vi.stubGlobal("fetch", fetcher);
    await mountRunPage("/projects/project-a/runs?run=run-retry");
    await waitForPageMicrotasks(() => detailTitle().includes("run-retry"));
    failNextPoll = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS); });
    await waitForPageMicrotasks(() => pageText().includes("Live updates are delayed"));
    expect(pageText()).toContain("Running");
    const failedReadCount = calls.filter((path) => path.endsWith("/run-retry")).length;
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS - 1); });
    expect(calls.filter((path) => path.endsWith("/run-retry")).length).toBe(failedReadCount);
    failNextPoll = false;
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    await waitForPageMicrotasks(() => !pageText().includes("Live updates are delayed"));
    expect(detailTitle()).toContain("run-retry");
  });

  it("ignores a late old-run read after deep-link selection changes", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    let releaseOldPoll: ((response: Response) => void) | undefined;
    let pollSignal: AbortSignal | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${url.pathname}${url.search}`);
      if (url.pathname === "/api/v1/projects/project-a/agent/runs") return jsonResponse({ items: [run("run-old"), run("run-new", { status: "succeeded" })], limit: 50, offset: 0 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/agent/approvals/inbox") return jsonResponse({ items: [], limit: 50 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/projects/project-a/agent/sessions") return jsonResponse({ items: [] });
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-old") {
        if (init?.signal && calls.filter((path) => path.endsWith("/run-old")).length > 1) {
          pollSignal = init.signal;
          return new Promise<Response>((resolve) => { releaseOldPoll = resolve; });
        }
        return jsonResponse(run("run-old"));
      }
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-old/events") return jsonResponse({ run_id: "run-old", items: [], has_more: false, limit: 100 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-new") return jsonResponse(run("run-new", { status: "succeeded" }));
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-new/events") return jsonResponse({ run_id: "run-new", items: [], has_more: false, limit: 100 }, 200, { "X-Has-More": "false" });
      throw new Error(`Unexpected selection-switch request: ${url.pathname}${url.search}`);
    };
    vi.stubGlobal("fetch", fetcher);
    await mountRunPage("/projects/project-a/runs?run=run-old", "/projects/:projectId/runs", true);
    await waitForPageMicrotasks(() => detailTitle().includes("run-old"));
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS); });
    expect(releaseOldPoll).toBeDefined();
    await act(async () => { clickPageButton("Select another run"); });
    await waitForPageMicrotasks(() => detailTitle().includes("run-new"));
    expect(pollSignal?.aborted).toBe(true);
    await act(async () => { releaseOldPoll?.(jsonResponse(run("run-old", { status: "failed", failure_message: "late" }))); });
    await flushPendingFetches();
    expect(detailTitle()).toContain("run-new");
    // The late failure belonged to run-old; nothing about it may reach the pane.
    expect(document.querySelector(".run-capability-notice")).toBeNull();
  });

  it("pauses live reads while the document is hidden and resumes for the selected run", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${url.pathname}${url.search}`);
      if (url.pathname === "/api/v1/projects/project-a/agent/runs") return jsonResponse({ items: [run("run-visible")], limit: 50, offset: 0 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/agent/approvals/inbox") return jsonResponse({ items: [], limit: 50 }, 200, { "X-Has-More": "false" });
      if (url.pathname === "/api/v1/projects/project-a/agent/sessions") return jsonResponse({ items: [] });
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-visible") return jsonResponse(run("run-visible"));
      if (url.pathname === "/api/v1/projects/project-a/agent/runs/run-visible/events") return jsonResponse({ run_id: "run-visible", items: [], has_more: false, limit: 100 }, 200, { "X-Has-More": "false" });
      throw new Error(`Unexpected visibility request: ${url.pathname}${url.search}`);
    };
    vi.stubGlobal("fetch", fetcher);
    hiddenDescriptor = Object.getOwnPropertyDescriptor(document, "hidden");
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    await mountRunPage("/projects/project-a/runs?run=run-visible");
    await waitForPageMicrotasks(() => detailTitle().includes("run-visible"));
    const initialReads = calls.filter((path) => path.includes("/run-visible")).length;
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS * 2); });
    expect(calls.filter((path) => path.includes("/run-visible")).length).toBe(initialReads);
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    // Resuming deliberately does not read on the visibility event itself: the page arms a
    // zero-delay timer (`schedule(0)`), so the resumed read lands on the next macrotask.
    // This test drives a fake clock, so the timer has to be advanced -- microtask draining
    // alone can never fire it and would only prove the assertion is unreachable, not that
    // visibility recovery is broken.
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await waitForPageMicrotasks(() => calls.filter((path) => path.includes("/run-visible")).length > initialReads);
    expect(detailTitle()).toContain("run-visible");
  });

  it("stops polling when project or run selection changes, linked chat opens, and the page unmounts", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${url.pathname}${url.search}`);
      if (url.pathname.endsWith("/agent/runs") && url.pathname.startsWith("/api/v1/projects/")) {
        const projectId = url.pathname.includes("project-b") ? "project-b" : "project-a";
        const items = projectId === "project-b"
          ? [run("run-b", { project_id: projectId, session_id: "session-linked", status: "succeeded" })]
          : [run("run-a", { project_id: projectId, session_id: "session-linked" }), run("run-new", { project_id: projectId, status: "succeeded" })];
        return jsonResponse({ items, limit: 50, offset: 0 }, 200, { "X-Has-More": "false" });
      }
      if (url.pathname === "/api/v1/agent/approvals/inbox") return jsonResponse({ items: [], limit: 50 }, 200, { "X-Has-More": "false" });
      if (url.pathname.endsWith("/agent/sessions")) return jsonResponse({ items: [] });
      if (url.pathname.endsWith("/agent/runs/run-a")) return jsonResponse(run("run-a", { session_id: "session-linked" }));
      if (url.pathname.endsWith("/agent/runs/run-new")) return jsonResponse(run("run-new", { status: "succeeded" }));
      if (url.pathname.endsWith("/agent/runs/run-b")) return jsonResponse(run("run-b", { project_id: "project-b", status: "succeeded" }));
      if (url.pathname.endsWith("/agent/runs/run-a/events")) return jsonResponse({ run_id: "run-a", items: [], has_more: false, limit: 100 }, 200, { "X-Has-More": "false" });
      if (url.pathname.endsWith("/agent/runs/run-new/events")) return jsonResponse({ run_id: "run-new", items: [], has_more: false, limit: 100 }, 200, { "X-Has-More": "false" });
      if (url.pathname.endsWith("/agent/runs/run-b/events")) return jsonResponse({ run_id: "run-b", items: [], has_more: false, limit: 100 }, 200, { "X-Has-More": "false" });
      throw new Error(`Unexpected lifecycle request: ${url.pathname}${url.search}`);
    };
    vi.stubGlobal("fetch", fetcher);
    await mountRunPage("/projects/project-a/runs?run=run-a", "/projects/:projectId/runs", true);
    await waitForPageMicrotasks(() => detailTitle().includes("run-a"));
    const oldRunReads = () => calls.filter((path) => path.includes("/agent/runs/run-a")).length;
    const firstCount = oldRunReads();
    await act(async () => { clickPageButton("Switch project"); });
    await waitForPageMicrotasks(() => detailTitle().includes("run-b"));
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS * 2); });
    expect(oldRunReads()).toBe(firstCount);

    await act(async () => { clickPageButton("Return to project"); });
    await waitForPageMicrotasks(() => detailTitle().includes("run-a"));
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS); });
    await waitForPageMicrotasks(() => oldRunReads() > firstCount);
    const secondCount = oldRunReads();
    await act(async () => { clickPageButton("Select another run"); });
    await waitForPageMicrotasks(() => detailTitle().includes("run-new"));
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS * 2); });
    expect(oldRunReads()).toBe(secondCount);

    await act(async () => { clickPageButton("Return to project"); });
    await waitForPageMicrotasks(() => detailTitle().includes("run-a"));
    const thirdCount = oldRunReads();
    const chatLink = document.querySelector<HTMLAnchorElement>(".run-detail-link a") ?? undefined;
    expect(chatLink).toBeDefined();
    expect(chatLink?.getAttribute("title")).toBe("session-linked");
    await act(async () => { chatLink?.click(); });
    await waitForPageMicrotasks(() => pageText().includes("Project chat route"));
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS * 2); });
    expect(oldRunReads()).toBe(thirdCount);
    await act(async () => { clickPageButton("Return to runs"); });
    await waitForPageMicrotasks(() => detailTitle().includes("run-a"));
    await act(async () => { clickPageButton("Deselect run"); });
    await waitForPageMicrotasks(() => pageText().includes("Select a run"));
    const fourthCount = oldRunReads();
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS * 2); });
    expect(oldRunReads()).toBe(fourthCount);

    await act(async () => { clickPageButton("Return to project"); });
    await waitForPageMicrotasks(() => detailTitle().includes("run-a"));
    const readsBeforeUnmount = oldRunReads();
    await act(async () => pageRoot?.unmount());
    pageRoot = undefined;
    await act(async () => { await vi.advanceTimersByTimeAsync(RUN_DETAIL_POLL_INTERVAL_MS * 2); });
    expect(oldRunReads()).toBe(readsBeforeUnmount);
  });
});
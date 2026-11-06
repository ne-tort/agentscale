import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../../app/context";
import { createIdempotencyKey, createRunsService, RunsRequestError } from "./service";
import type { AgentRunRecord, ApprovalInboxItem, ControlPlaneEvent, RunSession } from "./contracts";
import "./runs.css";

const dateTime = (value?: string | null) => value && Number.isFinite(Date.parse(value))
  ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
const jsonText = (value: unknown) => JSON.stringify(value ?? {}, null, 2);
const canCancel = (run: AgentRunRecord) => run.status === "queued" || run.status === "running";
const isRefreshable = (status: string) => status === "queued" || status === "running" || status === "waiting_approval";
export const RUN_DETAIL_POLL_INTERVAL_MS = 5_000;
const runStatus = (status: string) => status.replaceAll("_", " ");
const projectHref = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`;
const sessionHref = (projectId: string, sessionId: string) => `${projectHref(projectId)}/chat/${encodeURIComponent(sessionId)}`;
const runHref = (projectId: string, runId: string) => `${projectHref(projectId)}/runs?run=${encodeURIComponent(runId)}`;
const actionStorageKey = (projectId: string, resourceId: string, action: string) => `agentscale.runs.action.${projectId}.${resourceId}.${action}`;

function retainedActionKey(projectId: string, resourceId: string, action: string): string {
  const storageKey = actionStorageKey(projectId, resourceId, action);
  try {
    const retained = window.sessionStorage.getItem(storageKey);
    if (retained) return retained;
    const key = createIdempotencyKey();
    window.sessionStorage.setItem(storageKey, key);
    return key;
  } catch { return createIdempotencyKey(); }
}

function clearActionKey(projectId: string, resourceId: string, action: string) {
  try { window.sessionStorage.removeItem(actionStorageKey(projectId, resourceId, action)); } catch { /* storage is optional */ }
}

type CreateBody = { session_id: string | null; input_payload: Record<string, unknown> };
interface PendingCreate { idempotencyKey: string; body: CreateBody }

function newPendingCreate(projectId: string, body: CreateBody): PendingCreate {
  const pending = { idempotencyKey: retainedActionKey(projectId, "new", "create"), body };
  try { window.sessionStorage.setItem(actionStorageKey(projectId, "new", "create-payload"), JSON.stringify(pending)); } catch { /* keep command stable in memory */ }
  return pending;
}

function readPendingCreate(projectId: string): PendingCreate | undefined {
  try {
    const value = window.sessionStorage.getItem(actionStorageKey(projectId, "new", "create-payload"));
    if (!value) return undefined;
    const pending = JSON.parse(value) as PendingCreate;
    return typeof pending.idempotencyKey === "string" && pending.body && typeof pending.body.input_payload === "object" ? pending : undefined;
  } catch { return undefined; }
}

function clearPendingCreate(projectId: string) {
  try { window.sessionStorage.removeItem(actionStorageKey(projectId, "new", "create-payload")); } catch { /* storage is optional */ }
  clearActionKey(projectId, "new", "create");
}

function formatError(error: unknown): string {
  if (error instanceof RunsRequestError) return `${error.code}: ${error.message}`;
  return error instanceof Error ? error.message : String(error);
}

function eventSummary(event: ControlPlaneEvent): string {
  const payload = event.payload;
  if (typeof payload.reason === "string" && payload.reason) return payload.reason;
  if (typeof payload.failure_message === "string" && payload.failure_message) return payload.failure_message;
  if (typeof payload.status === "string") return `Status: ${runStatus(payload.status)}`;
  return event.event_type.replaceAll(".", " ").replaceAll("_", " ");
}

export function RunsCenterPage() {
  const location = useLocation();
  const { projectId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { tokens } = useAuth();
  const tokenRef = useRef(tokens?.access_token);
  tokenRef.current = tokens?.access_token;
  const api = useMemo(() => createRunsService({ getAccessToken: () => tokenRef.current }), []);
  const [tab, setTab] = useState<"runs" | "approvals" | "sessions">(() => location.pathname.endsWith("/approvals") ? "approvals" : "runs");
  const [runs, setRuns] = useState<AgentRunRecord[]>([]);
  const runsRef = useRef<AgentRunRecord[]>([]);
  const [runsCursor, setRunsCursor] = useState<string>();
  const [runsHasMore, setRunsHasMore] = useState(false);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState<string>();
  const selectedRunId = searchParams.get("run") ?? undefined;
  const selectionRef = useRef({ projectId, runId: selectedRunId });
  selectionRef.current = { projectId, runId: selectedRunId };
  const [selectedRun, setSelectedRun] = useState<AgentRunRecord>();
  const [events, setEvents] = useState<ControlPlaneEvent[]>([]);
  const [pollError, setPollError] = useState<string>();
  const [pollRestart, setPollRestart] = useState(0);
  const [eventsCursor, setEventsCursor] = useState<string>();
  const [eventsHasMore, setEventsHasMore] = useState(false);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [approvals, setApprovals] = useState<ApprovalInboxItem[]>([]);
  const [approvalCursor, setApprovalCursor] = useState<string>();
  const [approvalsHasMore, setApprovalsHasMore] = useState(false);
  const [approvalsLoading, setApprovalsLoading] = useState(false);
  const [approvalsError, setApprovalsError] = useState<string>();
  const [sessions, setSessions] = useState<RunSession[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionsError, setSessionsError] = useState<string>();
  const [createSessionId, setCreateSessionId] = useState("");
  const [inputPayloadText, setInputPayloadText] = useState("{}");
  const [pendingCreate, setPendingCreate] = useState<PendingCreate | undefined>(() => readPendingCreate(projectId));
  const [actionBusy, setActionBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const runGeneration = useRef(0);
  const eventGeneration = useRef(0);
  const inboxGeneration = useRef(0);
  const actionGeneration = useRef(0);
  const pollGeneration = useRef(0);

  useEffect(() => {
    setTab(location.pathname.endsWith("/approvals") ? "approvals" : "runs");
  }, [location.pathname]);

  const loadRuns = useCallback(async (cursor?: string, append = false) => {
    const generation = ++runGeneration.current;
    setRunsLoading(true);
    setRunsError(undefined);
    try {
      let nextCursor = cursor;
      let hasMore = false;
      let hasNextPage = false;
      let collected = append ? [...runsRef.current] : [];
      for (let pageNumber = 0; pageNumber < 100; pageNumber += 1) {
        const page = await api.listRuns(projectId, { limit: 50, cursor: nextCursor });
        if (generation !== runGeneration.current) return;
        collected = [...new Map([...collected, ...page.items].map((run) => [run.id, run])).values()];
        hasMore = page.hasMore;
        hasNextPage = page.hasMore && Boolean(page.nextCursor);
        nextCursor = page.nextCursor;
        if (!selectedRunId || collected.some((run) => run.id === selectedRunId) || !hasNextPage) break;
      }
      if (generation !== runGeneration.current) return;
      runsRef.current = collected;
      setRuns(collected);
      setRunsCursor(nextCursor);
      setRunsHasMore(hasMore && hasNextPage);
      const requestedId = selectedRunId;
      const requestedRunFound = !requestedId || collected.some((run) => run.id === requestedId);
      if (requestedId && !requestedRunFound) {
        setSelectedRun(undefined);
        setEvents([]);
        setSearchParams((current) => { const next = new URLSearchParams(current); next.delete("run"); return next; }, { replace: true });
      } else if (!append && !requestedId && collected[0]) {
        setSearchParams((current) => { const next = new URLSearchParams(current); next.set("run", collected[0].id); return next; }, { replace: true });
      }
    } catch (err) {
      if (generation === runGeneration.current) setRunsError(formatError(err));
    } finally { if (generation === runGeneration.current) setRunsLoading(false); }
  }, [api, projectId, selectedRunId, setSearchParams]);

  const loadApprovals = useCallback(async (cursor?: string, append = false) => {
    const generation = ++inboxGeneration.current;
    setApprovalsLoading(true);
    setApprovalsError(undefined);
    try {
      const page = await api.listApprovalInbox(projectId, { limit: 50, cursor });
      if (generation !== inboxGeneration.current) return;
      setApprovals((current) => {
        const merged = append ? [...current, ...page.items] : page.items;
        return [...new Map(merged.map((item) => [item.approval.id, item])).values()];
      });
      setApprovalCursor(page.nextCursor);
      setApprovalsHasMore(page.hasMore && Boolean(page.nextCursor));
    } catch (err) {
      if (generation === inboxGeneration.current) setApprovalsError(formatError(err));
    } finally { if (generation === inboxGeneration.current) setApprovalsLoading(false); }
  }, [api, projectId]);

  const loadSessions = useCallback(async () => {
    setSessionsLoading(true);
    setSessionsError(undefined);
    try { setSessions(await api.listSessions(projectId)); }
    catch (err) { setSessionsError(err instanceof Error ? err.message : "Chat session history could not be loaded."); }
    finally { setSessionsLoading(false); }
  }, [api, projectId]);

  const loadRunDetail = useCallback(async (runId: string) => {
    const generation = ++eventGeneration.current;
    pollGeneration.current += 1;
    setPollRestart((current) => current + 1);
    setEventsLoading(true);
    setError(undefined);
    setPollError(undefined);
    try {
      const [run, page] = await Promise.all([api.getRun(projectId, runId), api.listRunEvents(projectId, runId, { limit: 100 })]);
      if (generation !== eventGeneration.current || selectionRef.current.projectId !== projectId || selectionRef.current.runId !== runId) return;
      setSelectedRun(run);
      setEvents(page.items);
      setEventsCursor(page.nextCursor);
      setEventsHasMore(page.hasMore && Boolean(page.nextCursor));
      setSearchParams((current) => { const next = new URLSearchParams(current); next.set("run", runId); next.delete("session"); return next; }, { replace: true });
    } catch (err) {
      if (generation === eventGeneration.current && selectionRef.current.projectId === projectId && selectionRef.current.runId === runId) setError(formatError(err));
    } finally {
      if (generation === eventGeneration.current && selectionRef.current.projectId === projectId && selectionRef.current.runId === runId) setEventsLoading(false);
    }
  }, [api, projectId, setSearchParams]);

  useEffect(() => {
    runGeneration.current += 1;
    eventGeneration.current += 1;
    inboxGeneration.current += 1;
    actionGeneration.current += 1;
    pollGeneration.current += 1;
    setRuns([]);
    runsRef.current = [];
    setRunsCursor(undefined);
    setRunsHasMore(false);
    setSelectedRun(undefined);
    setEvents([]);
    setApprovals([]);
    setApprovalCursor(undefined);
    setApprovalsHasMore(false);
    setSessions([]);
    setPendingCreate(readPendingCreate(projectId));
    void loadRuns();
    void loadApprovals();
    void loadSessions();
  }, [projectId]);

  useEffect(() => {
    if (selectedRunId) {
      setSelectedRun(undefined);
      setEvents([]);
      setPollError(undefined);
      void loadRunDetail(selectedRunId);
    } else { setSelectedRun(undefined); setEvents([]); setPollError(undefined); }
  }, [loadRunDetail, selectedRunId]);

  useEffect(() => {
    const run = selectedRun;
    const runId = selectedRunId;
    if (eventsLoading || !run || !runId || run.id !== runId || run.project_id !== projectId || !isRefreshable(run.status)) return;

    const generation = ++pollGeneration.current;
    let active = true;
    let timer: number | undefined;
    let controller: AbortController | undefined;
    const isCurrent = () => active
      && generation === pollGeneration.current
      && !document.hidden
      && selectionRef.current.projectId === projectId
      && selectionRef.current.runId === runId;
    const schedule = (delay = RUN_DETAIL_POLL_INTERVAL_MS) => {
      if (!isCurrent() || timer !== undefined) return;
      timer = window.setTimeout(() => {
        timer = undefined;
        void refresh();
      }, delay);
    };
    const refresh = async () => {
      if (!isCurrent()) return;
      const requestController = new AbortController();
      controller = requestController;
      try {
        const [latestRun, page, runEvents] = await Promise.all([
          api.getRun(projectId, runId, requestController.signal),
          api.listRunEvents(projectId, runId, { limit: 100 }, requestController.signal),
          api.listRunEvents(projectId, runId, {
            limit: 500,
            aggregateType: "agent_run",
            aggregateId: runId,
            afterSequence: run.sequence,
          }, requestController.signal),
        ]);
        if (!isCurrent() || requestController.signal.aborted) return;
        if (latestRun.id !== runId || latestRun.project_id !== projectId) throw new Error("Run readback did not match the selected project record.");
        setSelectedRun(latestRun);
        setEvents((current) => {
          const merged = new Map(current.map((event) => [event.event_id, event]));
          for (const event of page.items) merged.set(event.event_id, event);
          for (const event of runEvents.items) merged.set(event.event_id, event);
          return [...merged.values()].sort((left, right) => {
            const aggregateOrder = left.aggregate_type.localeCompare(right.aggregate_type);
            if (aggregateOrder !== 0) return aggregateOrder;
            const aggregateIdOrder = left.aggregate_id.localeCompare(right.aggregate_id);
            if (aggregateIdOrder !== 0) return aggregateIdOrder;
            return (left.sequence ?? 0) - (right.sequence ?? 0);
          });
        });
        setEventsCursor(page.nextCursor);
        setEventsHasMore(page.hasMore && Boolean(page.nextCursor));
        setPollError(undefined);
        if (isRefreshable(latestRun.status)) schedule();
      } catch (err) {
        if (isCurrent() && !requestController.signal.aborted) {
          setPollError(formatError(err));
          schedule();
        }
      } finally {
        if (controller === requestController) controller = undefined;
      }
    };
    const onVisibilityChange = () => {
      if (document.hidden) {
        if (timer !== undefined) window.clearTimeout(timer);
        timer = undefined;
        controller?.abort();
        controller = undefined;
      } else schedule(0);
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    schedule();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      if (generation === pollGeneration.current) pollGeneration.current += 1;
    };
  }, [api, eventsLoading, projectId, pollRestart, selectedRun, selectedRunId, tab]);

  const chooseRun = (runId: string) => {
    pollGeneration.current += 1;
    eventGeneration.current += 1;
    setSelectedRun(undefined);
    setEvents([]);
    setError(undefined);
    setNotice(undefined);
    setSearchParams((current) => { const next = new URLSearchParams(current); next.set("run", runId); next.delete("session"); return next; });
  };

  const deselectRun = () => {
    eventGeneration.current += 1;
    pollGeneration.current += 1;
    setSelectedRun(undefined);
    setEvents([]);
    setPollError(undefined);
    setSearchParams((current) => { const next = new URLSearchParams(current); next.delete("run"); return next; });
  };

  const createRun = async () => {
    setError(undefined);
    setNotice(undefined);
    let input: unknown;
    try { input = JSON.parse(inputPayloadText); }
    catch { setError("Input payload must be valid JSON."); return; }
    if (input === null || Array.isArray(input) || typeof input !== "object") { setError("Input payload must be a JSON object."); return; }
    const command = pendingCreate ?? newPendingCreate(projectId, { session_id: createSessionId || null, input_payload: input as Record<string, unknown> });
    if (!pendingCreate) setPendingCreate(command);
    setActionBusy("create");
    const generation = ++actionGeneration.current;
    try {
      const result = await api.createRunWithReadback(projectId, command.body, command.idempotencyKey);
      if (generation !== actionGeneration.current) return;
      if (result.kind === "confirmed") {
        clearPendingCreate(projectId);
        setPendingCreate(undefined);
        setInputPayloadText("{}");
        setCreateSessionId("");
        setNotice(`Run ${result.run.id} is persisted as ${runStatus(result.run.status)}. A queue intent is not proof of worker delivery or execution.`);
        setSearchParams((current) => { const next = new URLSearchParams(current); next.set("run", result.run.id); next.delete("session"); return next; }, { replace: true });
        setTab("runs");
        await Promise.all([loadRuns(), loadApprovals()]);
      } else {
        const readback = result.readbackError ? ` Readback also failed: ${result.readbackError.message}` : "";
        if (result.kind === "blocked") setError(`Run writes are unavailable: ${formatError(result.error)}.${readback} The command key is retained for a safe retry.`);
        else if (result.kind === "conflict") {
          setError(`Run creation conflicted: ${formatError(result.error)}.${readback}`);
          clearPendingCreate(projectId);
          setPendingCreate(undefined);
        } else setError(`Run creation outcome is unknown: ${formatError(result.error)}.${readback} No success is claimed; retry uses the same idempotency key.`);
      }
    } finally { if (generation === actionGeneration.current) setActionBusy(undefined); }
  };

  const cancelSelectedRun = async () => {
    if (!selectedRun || !canCancel(selectedRun)) return;
    setActionBusy("cancel");
    setError(undefined);
    setNotice(undefined);
    const generation = ++actionGeneration.current;
    try {
      const outcome = await api.cancelRunWithReadback(projectId, {
        run: selectedRun,
        idempotencyKey: retainedActionKey(projectId, selectedRun.id, "cancel"),
        reason: "Cancelled by project operator",
      });
      if (generation !== actionGeneration.current) return;
      if (outcome.kind === "confirmed") {
        clearActionKey(projectId, selectedRun.id, "cancel");
        setSelectedRun(outcome.value);
        setNotice(`Cancellation confirmed by persisted run status and event sequence ${outcome.value.sequence}.`);
        await loadRuns();
      } else if (outcome.kind === "conflict") {
        clearActionKey(projectId, selectedRun.id, "cancel");
        setError(`Cancellation conflict: ${formatError(outcome.error)}. Current run state was reloaded when available; review before retrying.`);
        await loadRunDetail(selectedRun.id);
      } else if (outcome.kind === "blocked") setError(`Run cancellation is unavailable: ${formatError(outcome.error)}. No cancellation is claimed.`);
      else setError(`Cancellation outcome is unknown: ${formatError(outcome.error)}${outcome.readbackError ? ` Readback failed: ${formatError(outcome.readbackError)}` : ""}. Refresh before retrying.`);
    } finally { if (generation === actionGeneration.current) setActionBusy(undefined); }
  };

  const decideApproval = async (item: ApprovalInboxItem, decision: "approve" | "deny") => {
    setActionBusy(item.approval.id);
    setError(undefined);
    setNotice(undefined);
    const generation = ++actionGeneration.current;
    try {
      const outcome = await api.decideApprovalWithReadback(projectId, {
        item,
        decision,
        reason: decision === "approve" ? "Approved in project Runs & approvals" : "Denied in project Runs & approvals",
        idempotencyKey: retainedActionKey(projectId, item.approval.id, `decision-${decision}`),
      });
      if (generation !== actionGeneration.current) return;
      if (outcome.kind === "confirmed") {
        clearActionKey(projectId, item.approval.id, `decision-${decision}`);
        setApprovals((current) => current.filter((approval) => approval.approval.id !== item.approval.id));
        setNotice(`${decision === "approve" ? "Approval" : "Denial"} confirmed by the durable event timeline.`);
        await Promise.all([loadApprovals(), loadRuns()]);
      } else if (outcome.kind === "conflict") {
        clearActionKey(projectId, item.approval.id, `decision-${decision}`);
        setError(`Approval decision conflict: ${formatError(outcome.error)}. Inbox reloaded; inspect the current pending state before deciding again.`);
        await loadApprovals();
      } else if (outcome.kind === "blocked") setError(`Approval writes are unavailable: ${formatError(outcome.error)}. The approval remains unresolved.`);
      else setError(`Approval outcome is unknown: ${formatError(outcome.error)}${outcome.readbackError ? ` Readback failed: ${formatError(outcome.readbackError)}` : ""}. The inbox is refreshed; no decision is claimed.`);
    } finally { if (generation === actionGeneration.current) setActionBusy(undefined); }
  };

  const loadMoreRunEvents = async () => {
    if (!selectedRun || !eventsCursor || eventsLoading) return;
    const generation = ++eventGeneration.current;
    setEventsLoading(true);
    setError(undefined);
    try {
      const page = await api.listRunEvents(projectId, selectedRun.id, { limit: 100, cursor: eventsCursor });
      if (generation !== eventGeneration.current) return;
      setEvents((current) => [...current, ...page.items.filter((event) => !current.some((item) => item.event_id === event.event_id))]);
      setEventsCursor(page.nextCursor);
      setEventsHasMore(page.hasMore && Boolean(page.nextCursor));
    } catch (err) { if (generation === eventGeneration.current) setError(formatError(err)); }
    finally { if (generation === eventGeneration.current) setEventsLoading(false); }
  };

  const loadMoreRuns = async () => { if (runsCursor && runsHasMore && !runsLoading) await loadRuns(runsCursor, true); };
  const loadMoreApprovals = async () => { if (approvalCursor && approvalsHasMore && !approvalsLoading) await loadApprovals(approvalCursor, true); };
  const pendingCommand = Boolean(pendingCreate);
  const writeBlocker = (() => {
    if (!runsError) return undefined;
    const code = runsError.match(/AGENT_RUN_[A-Z_]+/)?.[0];
    return code ?? (runsError.includes("disabled") ? "AGENT_RUN_API_DISABLED" : undefined);
  })();
  const writesBlocked = Boolean(writeBlocker);

  return <section className="runs-page">
    <header className="runs-top"><div><p className="run-eyebrow">Project control plane</p><h1>Runs & approvals</h1><p className="run-subtitle">Durable execution records and human decisions, separate from chat history.</p></div><Link className="run-chat-back" to={`${projectHref(projectId)}/chat${selectedRun?.session_id ? `/${encodeURIComponent(selectedRun.session_id)}` : ""}`}>Open project chat</Link></header>
    <div className="run-project-context"><span className="run-project-mark">P</span><span><small>PROJECT SCOPE</small><strong>{projectId}</strong></span><span className="run-source-note">All run and inbox reads are scoped to this project</span></div>
    <nav className="run-tabs" aria-label="Project activity views">
      <button type="button" className={tab === "runs" ? "is-active" : ""} onClick={() => setTab("runs")}>Runs <span>{runs.length}</span></button>
      <button type="button" className={tab === "approvals" ? "is-active" : ""} onClick={() => setTab("approvals")}>Approvals <span>{approvals.length}</span></button>
      <button type="button" className={tab === "sessions" ? "is-active" : ""} onClick={() => setTab("sessions")}>Session history <span>{sessions.length}</span></button>
    </nav>
    {(error || (tab === "runs" && runsError) || (tab === "approvals" && approvalsError) || (tab === "sessions" && sessionsError)) && <div className="run-error" role="alert"><strong>Request failed</strong><span>{error ?? runsError ?? approvalsError ?? sessionsError}</span></div>}
    {notice && <div className="run-notice" role="status">{notice}</div>}

    {tab === "runs" && <div className="run-workspace-grid">
      <aside className="run-list-panel">
        <div className="run-list-heading"><div><span className="run-eyebrow">DURABLE RECORDS</span><h2>Agent runs</h2></div><button className="run-icon-button" type="button" onClick={() => void loadRuns()} aria-label="Refresh agent runs" title="Refresh">↻</button></div>
        {runsLoading && runs.length === 0 ? <p className="run-list-empty">Loading persisted runs…</p> : runs.length === 0 ? <div className="run-list-empty"><strong>No runs returned</strong><span>{writesBlocked ? `${writeBlocker}: durable run reads are unavailable in this environment.` : "This project has no durable Agent Run records."}</span></div> : <div className="run-session-list">{runs.map((run) => <button className={`run-session-card ${run.id === selectedRunId ? "is-selected" : ""}`} type="button" key={run.id} onClick={() => chooseRun(run.id)}>
          <span className={`run-status-dot ${run.status === "running" ? "is-running" : run.status === "failed" ? "is-failed" : ""}`} />
          <span className="run-session-main"><strong>{run.input_payload?.title ? String(run.input_payload.title) : run.id}</strong><small>{run.status} · attempt {run.attempt} · seq {run.sequence}</small><small className="run-session-time">{dateTime(run.created_at)}</small></span>
          <span className={`run-status-pill status-${run.status}`}>{runStatus(run.status)}</span>
        </button>)}</div>}
        {runsError && <button className="run-secondary-button run-retry-button" type="button" onClick={() => void loadRuns()} disabled={runsLoading}>Retry run list</button>}
        {runsHasMore && <button className="run-secondary-button run-retry-button" type="button" onClick={() => void loadMoreRuns()} disabled={runsLoading}>{runsLoading ? "Loading…" : "Load next run page"}</button>}
        <div className="run-list-footer"><span>{runs.length} loaded</span><span>Server cursor paging</span></div>
      </aside>

      <main className="run-detail-panel" aria-label="Agent run detail">
        {!selectedRunId ? <div className="run-detail-empty"><h2>Select a run</h2><p>Run records appear here only when returned by the durable Agent Runs API.</p></div> : !selectedRun && eventsLoading ? <p className="run-inline-loading">Loading run record and persisted events…</p> : selectedRun ? <>
          <div className="run-detail-header"><div><div className="run-detail-kicker"><span className="run-eyebrow">AGENT RUN · DURABLE</span><span className={`run-status-pill status-${selectedRun.status}`}>{runStatus(selectedRun.status)}</span></div><h2>{selectedRun.input_payload?.title ? String(selectedRun.input_payload.title) : selectedRun.id}</h2><div className="run-session-id">Run {selectedRun.id} · attempt {selectedRun.attempt} · sequence {selectedRun.sequence}</div></div><div className="run-detail-actions"><button type="button" className="run-secondary-button" onClick={() => void loadRunDetail(selectedRun.id)} disabled={eventsLoading}>{eventsLoading ? "Refreshing…" : "Refresh"}</button><button type="button" className="run-secondary-button" onClick={deselectRun}>Clear selection</button>{canCancel(selectedRun) && <button type="button" className="run-stop-button" onClick={() => void cancelSelectedRun()} disabled={actionBusy !== undefined}>{actionBusy === "cancel" ? "Cancelling…" : "Cancel run"}</button>}</div></div>
          {selectedRun.session_id && <div className="run-detail-link"><span>Linked chat session</span><Link to={sessionHref(projectId, selectedRun.session_id)}>{selectedRun.session_id} ↗</Link></div>}
          <div className="run-stat-grid"><article><span>RUN STATUS</span><strong>{runStatus(selectedRun.status)}</strong><small>Agent Run record · sequence {selectedRun.sequence}</small></article><article><span>ATTEMPT</span><strong>{selectedRun.attempt}</strong><small>{selectedRun.requested_by_employee_id ? `Requested by ${selectedRun.requested_by_employee_id}` : "Requester not recorded"}</small></article><article><span>RUN COST</span><strong>Not supported</strong><small>Agent Runs API has no cost field or cost endpoint.</small></article><article><span>ARTIFACTS</span><strong>Not supported</strong><small>Agent Runs API has no run artifact endpoint.</small></article></div>
          {selectedRun.failure_message && <div className="run-capability-notice"><strong>{selectedRun.failure_code ?? "Run failure"}</strong><span>{selectedRun.failure_message}</span></div>}
          <section className="run-panel-section"><div className="run-section-heading"><div><span className="run-eyebrow">PERSISTED CONTROL-PLANE EVENTS</span><h3>Run timeline</h3></div><span className="run-section-caption">{events.length} events</span></div>
            {pollError && <div className="run-error" role="status"><strong>Live refresh delayed</strong><span>{pollError}. Showing the last persisted run and events.</span></div>}
            {eventsLoading && events.length === 0 ? <p className="run-inline-loading">Loading event timeline…</p> : events.length === 0 ? <div className="run-timeline-empty"><strong>No persisted events for this run</strong><span>Timeline contents are read from the run-scoped events endpoint.</span></div> : <div className="run-timeline">{events.map((event) => <article className="run-timeline-item" key={event.event_id}><span className="run-timeline-marker">{event.sequence ?? "·"}</span><div className="run-timeline-content"><div className="run-timeline-title"><strong>{event.event_type}</strong><span>{dateTime(event.occurred_at)}</span></div><p>{eventSummary(event)}</p><details><summary>Event payload</summary><pre>{jsonText(event)}</pre></details></div></article>)}</div>}
            {eventsHasMore && <button type="button" className="run-secondary-button" onClick={() => void loadMoreRunEvents()} disabled={eventsLoading}>{eventsLoading ? "Loading events…" : "Load next event page"}</button>}
          </section>
          <section className="run-panel-section"><div className="run-section-heading"><div><span className="run-eyebrow">RUN PAYLOAD</span><h3>Input and output</h3></div></div><div className="run-payload-grid"><div><strong>Input payload</strong><pre>{jsonText(selectedRun.input_payload)}</pre></div><div><strong>Output payload</strong><pre>{selectedRun.output_payload === null ? "No output payload recorded." : jsonText(selectedRun.output_payload)}</pre></div></div></section>
          <footer className="run-detail-footer"><span>Source: scoped Agent Runs record + control-plane event API</span>{selectedRun.session_id && <Link to={sessionHref(projectId, selectedRun.session_id)}>Open linked chat ↗</Link>}</footer>
        </> : <div className="run-detail-empty"><h2>Run unavailable</h2><p>Refresh the project run list to check whether the record is still visible in this scope.</p><button type="button" className="run-secondary-button" onClick={() => void loadRuns()}>Refresh runs</button></div>}
      </main>

      <section className="run-create-panel" aria-label="Create agent run"><div className="run-section-heading"><div><span className="run-eyebrow">DURABLE COMMAND</span><h3>Start a run</h3></div></div>
        <label className="run-form-field">Link chat session <select value={pendingCreate?.body.session_id ?? createSessionId} onChange={(event) => setCreateSessionId(event.target.value)} disabled={actionBusy === "create" || pendingCommand}><option value="">No session link</option>{sessions.map((session) => <option key={session.id} value={session.id}>{session.title?.trim() || session.id}</option>)}</select></label>
        <label className="run-form-field">Input payload (JSON)<textarea value={pendingCreate ? jsonText(pendingCreate.body.input_payload) : inputPayloadText} onChange={(event) => setInputPayloadText(event.target.value)} rows={5} disabled={actionBusy === "create" || pendingCommand} spellCheck={false} /></label>
        {pendingCommand && <p className="run-inbox-incomplete">A prior write has no confirmed outcome. Retry uses the same persisted idempotency key and exact payload.</p>}
        <button type="button" className="run-approve-button" onClick={() => void createRun()} disabled={actionBusy === "create" || writesBlocked}>{actionBusy === "create" ? "Submitting…" : pendingCommand ? "Retry same run command" : "Create durable run"}</button>
        <p className="run-muted">A persisted enqueue intent does not prove broker delivery or that a worker started. Writes can be blocked until the backend dispatcher is enabled.</p>
      </section>
    </div>}

    {tab === "approvals" && <section className="run-detail-panel run-inbox-panel"><div className="run-section-heading"><div><span className="run-eyebrow">PROJECT APPROVAL INBOX</span><h2>Pending human decisions</h2></div><button className="run-icon-button" type="button" onClick={() => void loadApprovals()} aria-label="Refresh approval inbox" title="Refresh">↻</button></div>
      {approvalsLoading && approvals.length === 0 ? <p className="run-inline-loading">Loading scoped approval inbox…</p> : approvals.length === 0 && !approvalsError ? <div className="run-approval-empty"><span>✓</span><div><strong>No pending approvals</strong><small>The backend returned no pending approvals visible in this project.</small></div></div> : approvals.map((item) => <article className="run-approval-card" key={item.approval.id}><div className="run-approval-card-head"><span className="run-approval-shield">!</span><div><span className="run-eyebrow">{item.approval.approval_kind} · {runStatus(item.run_status)}</span><h3>{item.approval.approval_kind}</h3></div><span className="run-expiry-tag">{item.approval.expires_at ? `Expires ${dateTime(item.approval.expires_at)}` : "No expiry provided"}</span></div>
        <div className="run-approval-args"><span>REQUESTED PAYLOAD</span><pre>{jsonText(item.approval.requested_payload)}</pre></div><div className="run-inbox-source"><span>Run <code>{item.approval.run_id}</code> · approval seq {item.approval_sequence} · run seq {item.run_sequence}</span><Link key={item.approval.id} to={runHref(projectId, item.approval.run_id)}>Open run ↗</Link>{item.session_id && <Link to={sessionHref(projectId, item.session_id)}>Open linked chat ↗</Link>}</div>
        <div className="run-approval-actions"><span>Decision uses both observed aggregate sequences and requires readback from the event timeline.</span><div><button type="button" className="run-deny-button" onClick={() => void decideApproval(item, "deny")} disabled={actionBusy !== undefined}>{actionBusy === item.approval.id ? "Saving…" : "Deny"}</button><button type="button" className="run-approve-button" onClick={() => void decideApproval(item, "approve")} disabled={actionBusy !== undefined}>{actionBusy === item.approval.id ? "Saving…" : "Approve"}</button></div></div>
      </article>)}
      {approvalsError && <button type="button" className="run-secondary-button" onClick={() => void loadApprovals()} disabled={approvalsLoading}>Retry approval inbox</button>}
      {approvalsHasMore && <button type="button" className="run-secondary-button run-retry-button" onClick={() => void loadMoreApprovals()} disabled={approvalsLoading}>{approvalsLoading ? "Loading…" : "Load next approval page"}</button>}
    </section>}

    {tab === "sessions" && <section className="run-detail-panel run-session-history"><div className="run-section-heading"><div><span className="run-eyebrow">CHAT STATE · DISTINCT RESOURCE</span><h2>Session history</h2></div><button className="run-icon-button" type="button" onClick={() => void loadSessions()} aria-label="Refresh session history" title="Refresh">↻</button></div>
      <p className="run-muted">Agent sessions and transcripts are conversation history, not Agent Run records. No sessions are converted into runs.</p>
      {sessionsLoading ? <p className="run-inline-loading">Loading session history…</p> : sessionsError ? <button type="button" className="run-secondary-button" onClick={() => void loadSessions()}>Retry session history</button> : sessions.length === 0 ? <div className="run-list-empty"><strong>No chat sessions</strong><span>The project session API returned an empty history.</span></div> : <div className="run-session-list">{sessions.map((session) => <article className="run-session-history-row" key={session.id}><div><strong>{session.title?.trim() || "Untitled session"}</strong><small>{session.status} · {session.id}</small><small>{session.model ?? session.provider ?? "Agent session"} · {dateTime(session.last_message_at ?? session.created_at)}</small></div><Link to={`/projects/${projectId}/chat/${session.id}`}>Open chat ↗</Link></article>)}</div>}
    </section>}
  </section>;
}
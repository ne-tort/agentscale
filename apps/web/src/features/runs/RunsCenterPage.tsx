import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../../app/context";
import { useI18n } from "../../i18n/useT";
import type { Translate } from "../../i18n/catalogue";
import { createIdempotencyKey, createRunsService, RunsRequestError } from "./service";
import type { AgentRunRecord, ApprovalInboxItem, ControlPlaneEvent, RunSession } from "./contracts";
import "./runs.css";

const dateTime = (value?: string | null) => value && Number.isFinite(Date.parse(value))
  ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
const jsonText = (value: unknown) => JSON.stringify(value ?? {}, null, 2);
const canCancel = (run: AgentRunRecord) => run.status === "queued" || run.status === "running";
const isRefreshable = (status: string) => status === "queued" || status === "running" || status === "waiting_approval";
export const RUN_DETAIL_POLL_INTERVAL_MS = 5_000;

/**
 * Status values the backend sends, said the way a person reads them.
 *
 * `waiting_approval` is a column value, not a sentence; anything the map does
 * not know falls back to the value with its underscores spaced, so a new status
 * appears as-is instead of disappearing.
 */
const STATUS_KEYS: Record<string, string> = {
  queued: "runs.statusQueued",
  running: "runs.statusRunning",
  waiting_approval: "runs.statusWaitingApproval",
  succeeded: "runs.statusSucceeded",
  failed: "runs.statusFailed",
  cancelled: "runs.statusCancelled",
  active: "runs.statusActive",
  suspended: "runs.statusSuspended",
  closed: "runs.statusClosed",
};

function statusLabel(t: Translate, status: string): string {
  const key = STATUS_KEYS[status];
  return key ? t(key) : status.replaceAll("_", " ");
}

/** Event names as a line of history, not as `aggregate.type` identifiers. */
const EVENT_KEYS: Record<string, string> = {
  "agent_run.queued": "runs.eventRunQueued",
  "agent_run.started": "runs.eventRunStarted",
  "agent_run.running": "runs.eventRunStarted",
  "agent_run.succeeded": "runs.eventRunSucceeded",
  "agent_run.failed": "runs.eventRunFailed",
  "agent_run.waiting_approval": "runs.eventRunWaiting",
  "agent_run.cancelled": "runs.eventRunCancelled",
  "approval_request.approved": "runs.eventApprovalApproved",
  "approval_request.denied": "runs.eventApprovalDenied",
};

function eventLabel(t: Translate, eventType: string): string {
  const key = EVENT_KEYS[eventType];
  return key ? t(key) : eventType.replaceAll("_", " ").replaceAll(".", " ");
}

/** Backend codes that mean something specific to the person on this screen. */
const ERROR_KEYS: Record<string, string> = {
  AGENT_RUN_API_DISABLED: "runs.errorWritesDisabled",
  AGENT_RUN_PERSISTENCE_BLOCKED: "runs.errorWritesBlocked",
  AGENT_RUN_PERSISTENCE_NOT_READY: "runs.errorWritesNotReady",
  AGENT_RUN_READBACK_MISMATCH: "runs.errorReadbackMismatch",
};

/**
 * An error as a sentence. The code stays in the `title` of the element that
 * shows it: support can still read it, the operator never has to.
 */
function describeError(t: Translate, raw: string): string {
  const match = /^([A-Z][A-Z0-9_]+):\s*([\s\S]*)$/.exec(raw);
  if (!match) return raw;
  const key = ERROR_KEYS[match[1]];
  if (key) return t(key);
  return match[2] || raw;
}
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

function eventSummary(event: ControlPlaneEvent, t: Translate): string {
  const payload = event.payload;
  if (typeof payload.reason === "string" && payload.reason) return payload.reason;
  if (typeof payload.failure_message === "string" && payload.failure_message) return payload.failure_message;
  if (typeof payload.status === "string") return t("runs.eventStatus", { status: statusLabel(t, payload.status) });
  return eventLabel(t, event.event_type);
}

export function RunsCenterPage() {
  const location = useLocation();
  const { projectId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { tokens } = useAuth();
  const { t, plural } = useI18n();
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
    catch (err) { setSessionsError(err instanceof Error ? err.message : t("runs.sessionsLoadFailed")); }
    finally { setSessionsLoading(false); }
  }, [api, projectId, t]);

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
        if (latestRun.id !== runId || latestRun.project_id !== projectId) throw new Error("AGENT_RUN_READBACK_MISMATCH: run readback did not match the selected project record.");
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
    catch { setError(t("runs.errorJsonInvalid")); return; }
    if (input === null || Array.isArray(input) || typeof input !== "object") { setError(t("runs.errorJsonObject")); return; }
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
        setNotice(t("runs.createdNotice", { status: statusLabel(t, result.run.status) }));
        setSearchParams((current) => { const next = new URLSearchParams(current); next.set("run", result.run.id); next.delete("session"); return next; }, { replace: true });
        setTab("runs");
        await Promise.all([loadRuns(), loadApprovals()]);
      } else if (result.kind === "blocked") {
        setError(t("runs.errorCreateBlocked"));
      } else if (result.kind === "conflict") {
        setError(t("runs.errorCreateConflict"));
        clearPendingCreate(projectId);
        setPendingCreate(undefined);
      } else {
        setError(t("runs.errorCreateUnknown"));
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
        setNotice(t("runs.cancelConfirmed"));
        await loadRuns();
      } else if (outcome.kind === "conflict") {
        clearActionKey(projectId, selectedRun.id, "cancel");
        setError(t("runs.errorCancelConflict"));
        await loadRunDetail(selectedRun.id);
      } else if (outcome.kind === "blocked") setError(t("runs.errorCancelBlocked"));
      else setError(t("runs.errorCancelUnknown"));
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
        setNotice(decision === "approve" ? t("runs.approvalConfirmed") : t("runs.denialConfirmed"));
        await Promise.all([loadApprovals(), loadRuns()]);
      } else if (outcome.kind === "conflict") {
        clearActionKey(projectId, item.approval.id, `decision-${decision}`);
        setError(t("runs.errorApprovalConflict"));
        await loadApprovals();
      } else if (outcome.kind === "blocked") setError(t("runs.errorApprovalBlocked"));
      else setError(t("runs.errorApprovalUnknown"));
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

  const errorText = error ?? (tab === "runs" ? runsError : tab === "approvals" ? approvalsError : sessionsError);

  return <section className="runs-page">
    <header className="runs-top">
      <div>
        <p className="run-eyebrow">{t("runs.pageEyebrow")}</p>
        <h1>{t("runs.title")}</h1>
        <p className="run-subtitle">{t("runs.subtitle")}</p>
      </div>
      <Link className="run-chat-back" to={`${projectHref(projectId)}/chat${selectedRun?.session_id ? `/${encodeURIComponent(selectedRun.session_id)}` : ""}`}>{t("runs.openProjectChat")}</Link>
    </header>
    <div className="run-project-context">
      <span className="run-project-mark">{t("runs.projectMark")}</span>
      <span><small>{t("runs.projectLabel")}</small><strong title={projectId}>{projectId}</strong></span>
      <span className="run-source-note">{t("runs.projectScopeNote")}</span>
    </div>
    <nav className="run-tabs" aria-label={t("runs.viewsLabel")}>
      <button type="button" className={tab === "runs" ? "is-active" : ""} onClick={() => setTab("runs")}>{t("runs.tabRuns")} <span>{runs.length}</span></button>
      <button type="button" className={tab === "approvals" ? "is-active" : ""} onClick={() => setTab("approvals")}>{t("runs.tabApprovals")} <span>{approvals.length}</span></button>
      <button type="button" className={tab === "sessions" ? "is-active" : ""} onClick={() => setTab("sessions")}>{t("runs.tabSessions")} <span>{sessions.length}</span></button>
    </nav>
    {errorText && <div className="run-error" role="alert"><strong>{t("runs.requestFailed")}</strong><span title={errorText}>{describeError(t, errorText)}</span></div>}
    {notice && <div className="run-notice" role="status">{notice}</div>}

    {tab === "runs" && <div className="run-workspace-grid">
      <aside className="run-list-panel">
        <div className="run-list-heading">
          <div><span className="run-eyebrow">{t("runs.listEyebrow")}</span><h2>{t("runs.runsTitle")}</h2></div>
          <button className="run-icon-button" type="button" onClick={() => void loadRuns()} aria-label={t("runs.refreshRuns")} title={t("runs.refresh")}>↻</button>
        </div>
        {runsLoading && runs.length === 0
          ? <p className="run-list-empty">{t("runs.loadingRuns")}</p>
          : runs.length === 0
            ? <div className="run-list-empty"><strong>{t("runs.runsEmpty")}</strong><span>{writesBlocked ? t("runs.runsUnavailable") : t("runs.runsEmptyHint")}</span></div>
            : <div className="run-session-list">{runs.map((run) => <button className={`run-session-card ${run.id === selectedRunId ? "is-selected" : ""}`} type="button" key={run.id} onClick={() => chooseRun(run.id)}>
              <span className={`run-status-dot ${run.status === "running" ? "is-running" : run.status === "failed" ? "is-failed" : ""}`} />
              <span className="run-session-main">
                <strong>{run.input_payload?.title ? String(run.input_payload.title) : run.id}</strong>
                <small>{t("runs.runMeta", { attempt: run.attempt, sequence: run.sequence })}</small>
                <small className="run-session-time">{dateTime(run.created_at)}</small>
              </span>
              <span className={`run-status-pill status-${run.status}`}>{statusLabel(t, run.status)}</span>
            </button>)}</div>}
        {runsError && <button className="run-secondary-button run-retry-button" type="button" onClick={() => void loadRuns()} disabled={runsLoading}>{t("runs.retryRuns")}</button>}
        {runsHasMore && <button className="run-secondary-button run-retry-button" type="button" onClick={() => void loadMoreRuns()} disabled={runsLoading}>{runsLoading ? t("common.loading") : t("runs.loadMoreRuns")}</button>}
        <div className="run-list-footer"><span>{plural("runs.loaded", runs.length)}</span><span>{t("runs.listFooterNote")}</span></div>
      </aside>

      <main className="run-detail-panel" aria-label={t("runs.detailLabel")}>
        {!selectedRunId
          ? <div className="run-detail-empty"><h2>{t("runs.selectRunTitle")}</h2><p>{t("runs.selectRunHint")}</p></div>
          : !selectedRun && eventsLoading
            ? <p className="run-inline-loading">{t("runs.loadingRun")}</p>
            : selectedRun
              ? <>
                <div className="run-detail-header">
                  <div>
                    <div className="run-detail-kicker"><span className="run-eyebrow">{t("runs.listEyebrow")}</span><span className={`run-status-pill status-${selectedRun.status}`}>{statusLabel(t, selectedRun.status)}</span></div>
                    <h2 title={selectedRun.id}>{selectedRun.input_payload?.title ? String(selectedRun.input_payload.title) : selectedRun.id}</h2>
                    <div className="run-session-id" title={selectedRun.id}>{t("runs.runMeta", { attempt: selectedRun.attempt, sequence: selectedRun.sequence })}</div>
                  </div>
                  <div className="run-detail-actions">
                    <button type="button" className="run-secondary-button" onClick={() => void loadRunDetail(selectedRun.id)} disabled={eventsLoading}>{eventsLoading ? t("runs.refreshing") : t("runs.refreshDetail")}</button>
                    <button type="button" className="run-secondary-button" onClick={deselectRun}>{t("runs.clearSelection")}</button>
                    {canCancel(selectedRun) && <button type="button" className="run-stop-button" onClick={() => void cancelSelectedRun()} disabled={actionBusy !== undefined}>{actionBusy === "cancel" ? t("runs.cancelling") : t("runs.cancelRun")}</button>}
                  </div>
                </div>
                {selectedRun.session_id && <div className="run-detail-link"><span>{t("runs.linkedSession")}</span><Link to={sessionHref(projectId, selectedRun.session_id)} title={selectedRun.session_id}>{t("runs.openChat")} ↗</Link></div>}
                <div className="run-stat-grid">
                  <article><span>{t("runs.statStatus")}</span><strong>{statusLabel(t, selectedRun.status)}</strong><small>{t("runs.statStatusNote", { sequence: selectedRun.sequence })}</small></article>
                  <article><span>{t("runs.statAttempt")}</span><strong>{selectedRun.attempt}</strong><small>{selectedRun.requested_by_employee_id ? t("runs.requestedBy", { employee: selectedRun.requested_by_employee_id }) : t("runs.requesterUnknown")}</small></article>
                  <article><span>{t("runs.statCost")}</span><strong>{t("runs.notReported")}</strong><small>{t("runs.costNote")}</small></article>
                  <article><span>{t("runs.statArtifacts")}</span><strong>{t("runs.notReported")}</strong><small>{t("runs.artifactsNote")}</small></article>
                </div>
                {selectedRun.failure_message && <div className="run-capability-notice"><strong title={selectedRun.failure_code ?? undefined}>{t("runs.runFailed")}</strong><span>{selectedRun.failure_message}</span></div>}
                <section className="run-panel-section">
                  <div className="run-section-heading">
                    <div><span className="run-eyebrow">{t("runs.timelineEyebrow")}</span><h3>{t("runs.timelineTitle")}</h3></div>
                    <span className="run-section-caption">{plural("runs.events", events.length)}</span>
                  </div>
                  {pollError && <div className="run-error" role="status"><strong>{t("runs.pollDelayed")}</strong><span title={pollError}>{t("runs.pollDelayedHint")} {describeError(t, pollError)}</span></div>}
                  {eventsLoading && events.length === 0
                    ? <p className="run-inline-loading">{t("runs.loadingTimeline")}</p>
                    : events.length === 0
                      ? <div className="run-timeline-empty"><strong>{t("runs.timelineEmpty")}</strong><span>{t("runs.timelineEmptyHint")}</span></div>
                      : <div className="run-timeline">{events.map((event) => <article className="run-timeline-item" key={event.event_id}>
                        <span className="run-timeline-marker">{event.sequence ?? "·"}</span>
                        <div className="run-timeline-content">
                          <div className="run-timeline-title"><strong title={event.event_type}>{eventLabel(t, event.event_type)}</strong><span>{dateTime(event.occurred_at)}</span></div>
                          <p>{eventSummary(event, t)}</p>
                          <details><summary>{t("runs.eventPayload")}</summary><pre>{jsonText(event)}</pre></details>
                        </div>
                      </article>)}</div>}
                  {eventsHasMore && <button type="button" className="run-secondary-button" onClick={() => void loadMoreRunEvents()} disabled={eventsLoading}>{eventsLoading ? t("runs.loadingEvents") : t("runs.loadMoreEvents")}</button>}
                </section>
                <section className="run-panel-section">
                  <div className="run-section-heading"><div><span className="run-eyebrow">{t("runs.payloadEyebrow")}</span><h3>{t("runs.payloadTitle")}</h3></div></div>
                  <div className="run-payload-grid">
                    <div><strong>{t("runs.inputPayload")}</strong><pre>{jsonText(selectedRun.input_payload)}</pre></div>
                    <div><strong>{t("runs.outputPayload")}</strong><pre>{selectedRun.output_payload === null ? t("runs.noOutput") : jsonText(selectedRun.output_payload)}</pre></div>
                  </div>
                </section>
                <footer className="run-detail-footer"><span>{t("runs.detailSource")}</span>{selectedRun.session_id && <Link to={sessionHref(projectId, selectedRun.session_id)}>{t("runs.openLinkedChat")} ↗</Link>}</footer>
              </>
              : <div className="run-detail-empty"><h2>{t("runs.runUnavailable")}</h2><p>{t("runs.runUnavailableHint")}</p><button type="button" className="run-secondary-button" onClick={() => void loadRuns()}>{t("runs.refreshRunsShort")}</button></div>}
      </main>

      <section className="run-create-panel" aria-label={t("runs.createLabel")}>
        <div className="run-section-heading"><div><span className="run-eyebrow">{t("runs.createEyebrow")}</span><h3>{t("runs.createTitle")}</h3></div></div>
        <label className="run-form-field">{t("runs.linkSession")} <select value={pendingCreate?.body.session_id ?? createSessionId} onChange={(event) => setCreateSessionId(event.target.value)} disabled={actionBusy === "create" || pendingCommand}><option value="">{t("runs.noSessionLink")}</option>{sessions.map((session) => <option key={session.id} value={session.id}>{session.title?.trim() || session.id}</option>)}</select></label>
        <label className="run-form-field">{t("runs.inputJson")}<textarea value={pendingCreate ? jsonText(pendingCreate.body.input_payload) : inputPayloadText} onChange={(event) => setInputPayloadText(event.target.value)} rows={5} disabled={actionBusy === "create" || pendingCommand} spellCheck={false} /></label>
        {pendingCommand && <p className="run-inbox-incomplete">{t("runs.pendingHint")}</p>}
        <button type="button" className="run-approve-button" onClick={() => void createRun()} disabled={actionBusy === "create" || writesBlocked}>{actionBusy === "create" ? t("runs.submitting") : pendingCommand ? t("runs.retryCommand") : t("runs.submitCreate")}</button>
        <p className="run-muted">{t("runs.createHint")}</p>
      </section>
    </div>}

    {tab === "approvals" && <section className="run-detail-panel run-inbox-panel">
      <div className="run-section-heading">
        <div><span className="run-eyebrow">{t("runs.approvalsEyebrow")}</span><h2>{t("runs.approvalsTitle")}</h2></div>
        <button className="run-icon-button" type="button" onClick={() => void loadApprovals()} aria-label={t("runs.refreshApprovals")} title={t("runs.refresh")}>↻</button>
      </div>
      {approvalsLoading && approvals.length === 0
        ? <p className="run-inline-loading">{t("runs.loadingApprovals")}</p>
        : approvals.length === 0 && !approvalsError
          ? <div className="run-approval-empty"><span>✓</span><div><strong>{t("runs.approvalsEmpty")}</strong><small>{t("runs.approvalsEmptyHint")}</small></div></div>
          : approvals.map((item) => <article className="run-approval-card" key={item.approval.id}>
            <div className="run-approval-card-head">
              <span className="run-approval-shield">!</span>
              <div><span className="run-eyebrow">{t("runs.pendingApprovalEyebrow", { status: statusLabel(t, item.run_status) })}</span><h3>{item.approval.approval_kind}</h3></div>
              <span className="run-expiry-tag">{item.approval.expires_at ? t("runs.expiresAt", { date: dateTime(item.approval.expires_at) }) : t("runs.noExpiry")}</span>
            </div>
            <div className="run-approval-args"><span>{t("runs.requestedPayload")}</span><pre>{jsonText(item.approval.requested_payload)}</pre></div>
            <div className="run-inbox-source">
              <span title={`${item.approval.id} · ${item.approval.run_id}`}>{t("runs.approvalRun", { sequence: item.run_sequence })} · {t("runs.approvalRunSequence", { sequence: item.approval_sequence })}</span>
              <Link key={item.approval.id} to={runHref(projectId, item.approval.run_id)} title={item.approval.run_id}>{t("runs.openRun")} ↗</Link>
              {item.session_id && <Link to={sessionHref(projectId, item.session_id)} title={item.session_id}>{t("runs.openChat")} ↗</Link>}
            </div>
            <div className="run-approval-actions">
              <span>{t("runs.decisionNote")}</span>
              <div>
                <button type="button" className="run-deny-button" onClick={() => void decideApproval(item, "deny")} disabled={actionBusy !== undefined}>{actionBusy === item.approval.id ? t("common.saving") : t("runs.deny")}</button>
                <button type="button" className="run-approve-button" onClick={() => void decideApproval(item, "approve")} disabled={actionBusy !== undefined}>{actionBusy === item.approval.id ? t("common.saving") : t("runs.approve")}</button>
              </div>
            </div>
          </article>)}
      {approvalsError && <button type="button" className="run-secondary-button" onClick={() => void loadApprovals()} disabled={approvalsLoading}>{t("runs.retryApprovals")}</button>}
      {approvalsHasMore && <button type="button" className="run-secondary-button run-retry-button" onClick={() => void loadMoreApprovals()} disabled={approvalsLoading}>{approvalsLoading ? t("common.loading") : t("runs.loadMoreApprovals")}</button>}
    </section>}

    {tab === "sessions" && <section className="run-detail-panel run-session-history">
      <div className="run-section-heading">
        <div><span className="run-eyebrow">{t("runs.sessionsEyebrow")}</span><h2>{t("runs.sessionsTitle")}</h2></div>
        <button className="run-icon-button" type="button" onClick={() => void loadSessions()} aria-label={t("runs.refreshSessions")} title={t("runs.refresh")}>↻</button>
      </div>
      <p className="run-muted">{t("runs.sessionsHint")}</p>
      {sessionsLoading
        ? <p className="run-inline-loading">{t("runs.loadingSessions")}</p>
        : sessionsError
          ? <button type="button" className="run-secondary-button" onClick={() => void loadSessions()}>{t("runs.retrySessions")}</button>
          : sessions.length === 0
            ? <div className="run-list-empty"><strong>{t("runs.sessionsEmpty")}</strong><span>{t("runs.sessionsEmptyHint")}</span></div>
            : <div className="run-session-list">{sessions.map((session) => <article className="run-session-history-row" key={session.id}>
              <div>
                <strong>{session.title?.trim() || t("runs.untitledSession")}</strong>
                <small>{statusLabel(t, session.status)} · {dateTime(session.last_message_at ?? session.created_at)}</small>
                <small>{session.model ?? session.provider ?? t("runs.agentSession")}</small>
              </div>
              <Link to={`/projects/${projectId}/chat/${session.id}`} title={session.id}>{t("runs.openChat")} ↗</Link>
            </article>)}</div>}
    </section>}
  </section>;
}

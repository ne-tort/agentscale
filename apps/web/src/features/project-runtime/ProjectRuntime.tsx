import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { Translate } from "../../i18n/catalogue";
import type { Locale } from "../../preferences/settings";
import { useI18n } from "../../i18n/useT";
import { createDefaultProjectRuntimeService, ProjectRuntimeRequestError } from "./service";
import type {
  ChatSidebarItem,
  ProjectAttachment,
  ProjectKey,
  ProjectRuntimePhase,
  ProjectRuntimeProps,
  ProjectRuntimeService,
  ProjectRuntimeSnapshot,
  RuntimeAction,
  RuntimeTab,
  WorkspaceEntry,
} from "./types";
import "./project-runtime.css";

const defaultService = createDefaultProjectRuntimeService();

/** Which sentence a phase is called by: an operator reads words, not the enum. */
const PHASE_KEYS: Record<ProjectRuntimePhase, string> = {
  absent: "runtime.phaseAbsent",
  preparing: "runtime.phasePreparing",
  pulling: "runtime.phasePulling",
  hydrating: "runtime.phaseHydrating",
  provisioning: "runtime.phaseProvisioning",
  starting: "runtime.phaseStarting",
  running: "runtime.phaseRunning",
  paused: "runtime.phasePaused",
  pausing: "runtime.phasePausing",
  suspended: "runtime.phaseSuspended",
  completed: "runtime.phaseCompleted",
  failed: "runtime.phaseFailed",
  error: "runtime.phaseError",
  degraded: "runtime.phaseDegraded",
  unknown: "runtime.phaseUnknown",
};

const PHASE_TONES: Record<ProjectRuntimePhase, "positive" | "warning" | "danger" | "neutral"> = {
  absent: "neutral",
  preparing: "warning",
  pulling: "warning",
  hydrating: "warning",
  provisioning: "warning",
  starting: "warning",
  running: "positive",
  paused: "neutral",
  pausing: "warning",
  suspended: "neutral",
  completed: "neutral",
  failed: "danger",
  error: "danger",
  degraded: "danger",
  unknown: "neutral",
};

const ACTION_KEYS: Record<RuntimeAction, string> = {
  launch: "runtime.actionLaunch",
  pause: "runtime.actionPause",
  resume: "runtime.actionResume",
  reload: "runtime.actionReload",
  sync: "runtime.actionSync",
  complete: "runtime.actionComplete",
};

const ACTION_CONFIRM_KEYS: Record<RuntimeAction, string> = {
  launch: "runtime.confirmLaunch",
  pause: "runtime.confirmPause",
  resume: "runtime.confirmResume",
  reload: "runtime.confirmReload",
  sync: "runtime.confirmSync",
  complete: "runtime.confirmComplete",
};

type Notice = { tone: "success" | "error" | "info"; message: string; key?: "launchPending" };
type ActionState = { action: RuntimeAction; error?: string };

function phaseLabel(t: Translate, phase: ProjectRuntimePhase): string {
  return t(PHASE_KEYS[phase]);
}

function actionLabel(t: Translate, action: RuntimeAction): string {
  return t(ACTION_KEYS[action]);
}

function isRuntimePhase(value: unknown): value is ProjectRuntimePhase {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(PHASE_TONES, value);
}

function phaseOf(snapshot?: ProjectRuntimeSnapshot): ProjectRuntimePhase {
  const candidate = snapshot?.container?.observed_state ?? snapshot?.container?.runtime?.observed_state ?? snapshot?.project.runtime?.observed_state;
  if (isRuntimePhase(candidate)) return candidate;
  if (snapshot?.project.status === "completed") return "completed";
  if (snapshot?.project.status === "paused") return "paused";
  if (snapshot?.project.status === "draft") return "absent";
  if (snapshot?.project.status === "error") return "error";
  if (snapshot?.project.launch_phase === "preparing") return "preparing";
  return "unknown";
}

function readableError(t: Translate, error: unknown): string {
  if (error instanceof ProjectRuntimeRequestError) {
    const suffix = error.traceId ? ` ${t("runtime.errorTrace", { code: error.traceId })}` : "";
    return `${error.message}${suffix}`;
  }
  return error instanceof Error ? error.message : t("runtime.errorGeneric");
}

function providerForKey(key: ProjectKey): string {
  const providers = new Set(["cursor", "codex", "claude_code", "xai"]);
  if (key.provider && providers.has(key.provider)) return key.provider;
  return key.api_kind === "anthropic_api" ? "claude_code" : "codex";
}

function formatBytes(value: number | null | undefined, t: Translate): string {
  if (value === undefined || value === null || !Number.isFinite(value)) return t("runtime.none");
  if (value < 1024) return `${Math.round(value)} ${t("runtime.unitBytes")}`;
  const units = ["runtime.unitKilobytes", "runtime.unitMegabytes", "runtime.unitGigabytes", "runtime.unitTerabytes"];
  let amount = value;
  let unit = -1;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${t(units[unit])}`;
}

function formatCpu(value: number | null | undefined, locale: Locale): string {
  if (value === undefined || value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value / 1000);
}

function formatDate(value: string | null | undefined, locale: Locale): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "—";
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function toneClass(tone: "positive" | "warning" | "danger" | "neutral"): string {
  return `project-runtime-pill project-runtime-pill--${tone}`;
}

function StatusPill({ phase }: { phase: ProjectRuntimePhase }) {
  const { t } = useI18n();
  return <span className={toneClass(PHASE_TONES[phase])}><span className="project-runtime-pill__dot" aria-hidden="true" />{phaseLabel(t, phase)}</span>;
}

function SectionHeading({ eyebrow, title, detail, action }: { eyebrow: string; title: string; detail?: string; action?: ReactNode }) {
  return <div className="project-runtime-section-heading"><div><p className="project-runtime-eyebrow">{eyebrow}</p><h2>{title}</h2>{detail && <p className="project-runtime-muted">{detail}</p>}</div>{action}</div>;
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="project-runtime-metric"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function RuntimeOverview({ snapshot, phase, onAction, actionState }: { snapshot: ProjectRuntimeSnapshot; phase: ProjectRuntimePhase; onAction: (action: RuntimeAction) => void; actionState?: ActionState }) {
  const { t, locale } = useI18n();
  const runtime = snapshot.container?.runtime ?? snapshot.project.runtime;
  const metrics = snapshot.metrics?.latest ?? runtime?.metrics ?? snapshot.container?.runtime_metrics;
  const metricAvailable = snapshot.metrics?.metrics_available ?? runtime?.metrics_available ?? Boolean(metrics);
  const actionBusy = actionState?.action;
  const selectedKey = snapshot.keys.find((key) => key.id === snapshot.project.resolved_ai_key_id);
  const launchReady = Boolean(snapshot.project.agent_provider && selectedKey && providerForKey(selectedKey) === snapshot.project.agent_provider && selectedKey.status === "active" && selectedKey.has_secret !== false);
  const readiness = runtime?.ready === true ? t("runtime.readyYes") : runtime?.ready === false ? t("runtime.readyNo") : t("runtime.readyUnknown");
  return <>
    <section className="project-runtime-hero-card">
      <div className="project-runtime-hero-main">
        <div className="project-runtime-mark" aria-hidden="true">{snapshot.project.name.slice(0, 1).toUpperCase()}</div>
        <div>
          <p className="project-runtime-eyebrow">{t("runtime.projectCardEyebrow")}</p>
          <h2>{snapshot.project.name}</h2>
          <p className="project-runtime-muted">{snapshot.project.about || t("runtime.noDescription")}</p>
          <div className="project-runtime-inline-meta">
            <StatusPill phase={phase} />
            {runtime?.restarts !== undefined && runtime.restarts !== null && <span>{t("runtime.restarts", { count: runtime.restarts })}</span>}
          </div>
        </div>
      </div>
      <div className="project-runtime-action-grid">
        <ActionButton action="launch" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} primary launchReady={launchReady} />
        <ActionButton action="resume" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} />
        <ActionButton action="pause" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} />
        <ActionButton action="reload" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} />
        <ActionButton action="sync" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} />
        <ActionButton action="complete" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} danger />
      </div>
    </section>
    {snapshot.project.status === "draft" && !launchReady && <p className="project-runtime-inline-error" role="status">{t("runtime.launchNeedsKey")}</p>}
    {actionState && <ProgressCard state={actionState} onRetry={() => onAction(actionState.action)} />}
    {(runtime?.last_error || snapshot.container?.last_error || phase === "failed" || phase === "error" || phase === "degraded") && <ErrorCard message={runtime?.last_error ?? snapshot.container?.last_error ?? t("runtime.errorNoReason")} retryable={phase === "failed" || phase === "error" || phase === "degraded"} onRetry={() => onAction("reload")} />}
    <section className="project-runtime-surface">
      <SectionHeading eyebrow={t("runtime.healthEyebrow")} title={t("runtime.healthTitle")} detail={t("runtime.healthDetail")} />
      <div className="project-runtime-metrics-grid">
        <MetricCard label={t("runtime.metricCpu")} value={metricAvailable ? formatCpu(metrics?.cpu_millicores, locale) : t("runtime.metricMissing")} detail={metricAvailable ? t("runtime.metricCpuDetail") : t("runtime.metricsPending")} />
        <MetricCard label={t("runtime.metricRam")} value={metricAvailable ? formatBytes(metrics?.memory_bytes, t) : t("runtime.metricMissing")} detail={metricAvailable ? t("runtime.metricRamDetail") : t("runtime.metricsPending")} />
        <MetricCard label={t("runtime.metricReadiness")} value={readiness} detail={t("runtime.metricReadinessDetail")} />
        <MetricCard label={t("runtime.metricUpdated")} value={formatDate(metrics?.timestamp || runtime?.observed_at, locale)} detail={snapshot.metrics?.stale ? t("runtime.metricStaleDetail") : t("runtime.metricUpdatedDetail")} />
      </div>
    </section>
    <details className="project-runtime-surface project-runtime-advanced">
      <summary>{t("runtime.advanced")}</summary>
      <h2>{t("runtime.technicalTitle")}</h2>
      <dl className="project-runtime-facts">
        <div><dt>{t("runtime.techPhase")}</dt><dd>{runtime?.phase || t("runtime.none")}</dd></div>
        <div><dt>{t("runtime.techDesired")}</dt><dd>{runtime?.desired_state || t("runtime.none")}</dd></div>
        <div><dt>{t("runtime.techOrchestrator")}</dt><dd>{runtime?.orchestrator_status || runtime?.status || t("runtime.none")}</dd></div>
        <div><dt>{t("runtime.techRuntimeRef")}</dt><dd>{runtime?.runtime_ref ? <code>{runtime.runtime_ref}</code> : t("runtime.none")}</dd></div>
        <div><dt>{t("runtime.techGeneration")}</dt><dd>{runtime?.hydrate_generation ?? t("runtime.none")}</dd></div>
        <div><dt>{t("runtime.techWorkspaceUpdated")}</dt><dd>{snapshot.project.workspace_outdated_at ? formatDate(snapshot.project.workspace_outdated_at, locale) : t("runtime.none")}</dd></div>
      </dl>
    </details>
  </>;
}

function ActionButton({ action, phase, projectStatus, busy, onClick, primary = false, danger = false, launchReady = true }: { action: RuntimeAction; phase: ProjectRuntimePhase; projectStatus: string; busy?: RuntimeAction; onClick: (action: RuntimeAction) => void; primary?: boolean; danger?: boolean; launchReady?: boolean }) {
  const { t } = useI18n();
  const allowed = action === "launch" ? projectStatus === "draft" && phase === "absent" : action === "resume" ? ["paused", "suspended", "completed"].includes(phase) : action === "pause" ? ["running", "degraded", "starting"].includes(phase) : action === "reload" ? ["running", "degraded", "failed", "error", "unknown"].includes(phase) : action === "sync" ? ["running", "paused", "degraded", "failed", "error"].includes(phase) : ["running", "paused", "degraded", "failed", "error", "absent"].includes(phase);
  const disabled = Boolean(busy) || !allowed || (action === "launch" && !launchReady);
  const title = disabled && !busy
    ? action === "launch" && !launchReady
      ? t("runtime.actionSaveFirst")
      : t("runtime.actionUnavailable", { state: phaseLabel(t, phase) })
    : undefined;
  return <button type="button" className={`project-runtime-button${primary ? " project-runtime-button--primary" : ""}${danger ? " project-runtime-button--danger" : ""}`} disabled={disabled} onClick={() => onClick(action)} title={title}>{busy === action ? t("runtime.actionPending") : actionLabel(t, action)}</button>;
}

function ProgressCard({ state, onRetry }: { state: ActionState; onRetry: () => void }) {
  const { t } = useI18n();
  return <section className="project-runtime-progress" aria-live="polite">
    <div className="project-runtime-progress__head"><div><p className="project-runtime-eyebrow">{t("runtime.progressEyebrow")}</p><strong>{actionLabel(t, state.action)}</strong></div></div>
    {state.error
      ? <div className="project-runtime-progress__failure"><p className="project-runtime-progress__error">{state.error}</p><button type="button" className="project-runtime-button project-runtime-button--quiet" onClick={onRetry}>{t("runtime.progressRetry", { action: actionLabel(t, state.action).toLowerCase() })}</button></div>
      : <p className="project-runtime-muted">{t("runtime.progressWaiting")}</p>}
  </section>;
}

function ErrorCard({ message, retryable, onRetry }: { message: string; retryable: boolean; onRetry: () => void }) {
  const { t } = useI18n();
  return <aside className="project-runtime-error" role="alert">
    <span className="project-runtime-error__icon" aria-hidden="true">!</span>
    <div><strong>{t("runtime.errorTitle")}</strong><p>{message}</p><small>{retryable ? t("runtime.errorRetryableHint") : t("runtime.errorHint")}</small></div>
    {retryable && <button type="button" className="project-runtime-button project-runtime-button--quiet" onClick={onRetry}>{t("runtime.errorRetry")}</button>}
  </aside>;
}

function ChatPanel({ snapshot, onNavigate }: { snapshot: ProjectRuntimeSnapshot; onNavigate: ProjectRuntimeProps["onNavigate"] }) {
  const { t } = useI18n();
  const projectChats = snapshot.chats?.project_chats ?? snapshot.chats?.projects.find((item) => item.project_id === snapshot.project.id)?.chats ?? [];
  return <section className="project-runtime-surface project-runtime-chat">
    <SectionHeading
      eyebrow={t("runtime.chatEyebrow")}
      title={t("runtime.chatTitle")}
      detail={t("runtime.chatDetail")}
      action={<button type="button" className="project-runtime-button project-runtime-button--primary" onClick={() => onNavigate("chat", snapshot.project.id)}>{t("runtime.openChat")}</button>}
    />
    {projectChats.length
      ? <div className="project-runtime-chat-list">{projectChats.map((chat) => <ChatRow key={chat.session_id} chat={chat} projectId={snapshot.project.id} onNavigate={onNavigate} />)}</div>
      : <div className="project-runtime-empty"><strong>{t("runtime.chatEmptyTitle")}</strong><span>{t("runtime.chatEmptyHint")}</span></div>}
  </section>;
}

function ChatRow({ chat, projectId, onNavigate }: { chat: ChatSidebarItem; projectId: string; onNavigate: ProjectRuntimeProps["onNavigate"] }) {
  const { t, locale } = useI18n();
  const title = chat.title || t("runtime.chatNewTitle");
  return <button
    type="button"
    className="project-runtime-chat-row"
    onClick={() => onNavigate("chat", projectId, chat.session_id)}
    aria-label={t("runtime.chatRowAria", { title })}
    title={chat.session_id}
  >
    <span className="project-runtime-chat-icon" aria-hidden="true">↗</span>
    <span><strong>{title}</strong><small>{chat.last_message_at ? formatDate(chat.last_message_at, locale) : t("runtime.noMessages")}</small></span>
    <span className="project-runtime-chat-status">{chat.has_messages ? t("runtime.hasMessages") : t("runtime.emptyChat")}</span>
  </button>;
}

function FilesPanel({ snapshot, service, phase, onRefreshStatus }: { snapshot: ProjectRuntimeSnapshot; service: ProjectRuntimeService; phase: ProjectRuntimePhase; onRefreshStatus: () => void }) {
  const { t, locale } = useI18n();
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<WorkspaceEntry[]>(snapshot.files?.entries ?? []);
  const [preview, setPreview] = useState<{ path: string; content: string }>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [hasReadWorkspace, setHasReadWorkspace] = useState(snapshot.files !== null);
  const requestSequence = useRef(0);
  const previousPhase = useRef(phase);
  const attachments = snapshot.attachments;
  const podUnavailable = phase !== "running";
  const workspaceUnavailable = snapshot.files === null && !hasReadWorkspace;
  const workspacePending = !podUnavailable && workspaceUnavailable;
  const visibleEntries = podUnavailable ? [] : entries;

  const browse = useCallback(async (nextPath: string) => {
    if (phase !== "running") {
      setEntries([]); setPreview(undefined); setError(undefined); setHasReadWorkspace(false);
      return;
    }
    const sequence = ++requestSequence.current;
    setLoading(true); setError(undefined);
    try {
      const result = await service.listWorkspace(snapshot.project.id, nextPath);
      if (sequence !== requestSequence.current) return;
      setPath(result.path); setEntries(result.entries); setPreview(undefined); setHasReadWorkspace(true);
    } catch (err) {
      if (sequence === requestSequence.current) setError(readableError(t, err));
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [phase, service, snapshot.project.id, t]);

  useEffect(() => {
    if (!workspacePending) return undefined;
    void browse("");
    const timer = window.setInterval(() => { void browse(""); }, 5000);
    return () => window.clearInterval(timer);
  }, [browse, workspacePending]);
  useEffect(() => {
    const changed = previousPhase.current !== phase;
    previousPhase.current = phase;
    if (phase !== "running") {
      if (changed) {
        requestSequence.current += 1;
        setLoading(false); setError(undefined); setEntries([]); setPreview(undefined); setPath(""); setHasReadWorkspace(false);
      }
      return;
    }
    if (changed) {
      setError(undefined);
      if (snapshot.files) { requestSequence.current += 1; setLoading(false); setPath(""); setEntries(snapshot.files.entries); setPreview(undefined); setHasReadWorkspace(true); }
      else setHasReadWorkspace(false);
      return;
    }
    if (snapshot.files?.path === path) { setEntries(snapshot.files.entries); setHasReadWorkspace(true); }
  }, [browse, path, phase, snapshot.files]);
  async function openFile(entry: WorkspaceEntry) {
    if (entry.kind !== "file") { await browse(entry.path); return; }
    const sequence = ++requestSequence.current;
    setLoading(true); setError(undefined);
    try {
      const result = await service.previewWorkspace(snapshot.project.id, entry.path);
      if (sequence === requestSequence.current) setPreview({ path: result.path, content: result.content });
    } catch (err) {
      if (sequence === requestSequence.current) setError(readableError(t, err));
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }
  async function download(entry: WorkspaceEntry) {
    let anchor: HTMLAnchorElement | undefined;
    let url: string | undefined;
    try {
      const blob = await service.downloadWorkspace(snapshot.project.id, entry.path);
      url = URL.createObjectURL(blob);
      anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = entry.name;
      anchor.hidden = true;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => { if (url) URL.revokeObjectURL(url); }, 0);
    } catch (err) {
      anchor?.remove();
      if (url) URL.revokeObjectURL(url);
      setError(readableError(t, err));
    }
  }
  return <div className="project-runtime-files-layout">
    <section className="project-runtime-surface">
      <SectionHeading
        eyebrow={t("runtime.filesEyebrow")}
        title={t("runtime.filesTitle")}
        detail={t("runtime.filesDetail")}
        action={<button type="button" className="project-runtime-button project-runtime-button--quiet" onClick={() => phase === "running" ? void browse(path) : onRefreshStatus()} disabled={loading}>{loading ? t("runtime.refreshing") : phase === "running" ? t("runtime.filesRefresh") : t("runtime.filesRefreshStatus")}</button>}
      />
      {podUnavailable && <p className="project-runtime-inline-error" role="status">{t("runtime.filesPodUnavailable")}</p>}
      {workspaceUnavailable && !podUnavailable && <p className="project-runtime-inline-error" role="status">{t("runtime.filesNotReady")}</p>}
      <div className="project-runtime-path">
        <button type="button" onClick={() => phase === "running" ? void browse("") : onRefreshStatus()}>{t("runtime.rootFolder")}</button>
        {path && <><span>/</span><span>{path}</span></>}
      </div>
      {error && <p className="project-runtime-inline-error" role="alert">{error}</p>}
      <div className="project-runtime-file-list">
        {visibleEntries.length
          ? visibleEntries.map((entry) => <div className="project-runtime-file-row" key={entry.path}>
              <button type="button" onClick={() => void openFile(entry)}>
                <span className={`project-runtime-file-kind project-runtime-file-kind--${entry.kind}`}>{entry.kind === "dir" ? t("runtime.kindFolder") : t("runtime.kindFile")}</span>
                <span><strong>{entry.name}</strong><small>{entry.path} · {entry.size === null || entry.size === undefined ? t("runtime.none") : formatBytes(entry.size, t)}</small></span>
              </button>
              {entry.kind === "file" && <button type="button" className="project-runtime-icon-button" onClick={() => void download(entry)} aria-label={t("runtime.downloadAria", { name: entry.name })} title={t("runtime.download")}>↓</button>}
            </div>)
          : <div className="project-runtime-empty">
              <strong>{podUnavailable ? t("runtime.filesEmptyPodTitle") : t("runtime.filesEmptyTitle")}</strong>
              <span>{loading ? t("runtime.loadingDetail") : podUnavailable ? t("runtime.filesEmptyPodHint") : workspaceUnavailable ? t("runtime.filesEmptyWaitingHint") : t("runtime.filesEmptyHint")}</span>
            </div>}
      </div>
      {preview && <article className="project-runtime-preview">
        <div className="project-runtime-preview__head"><strong>{preview.path}</strong><button type="button" className="project-runtime-icon-button" onClick={() => setPreview(undefined)} aria-label={t("runtime.closePreview")}>×</button></div>
        <pre>{preview.content}</pre>
      </article>}
    </section>
    <aside className="project-runtime-surface">
      <SectionHeading eyebrow={t("runtime.attachmentsEyebrow")} title={t("runtime.attachmentsTitle")} detail={t("runtime.attachmentsDetail")} />
      {attachments.length
        ? <div className="project-runtime-attachments">{attachments.map((attachment: ProjectAttachment) => <div className="project-runtime-attachment" key={attachment.id}>
            <span aria-hidden="true">▧</span>
            <div><strong>{attachment.filename}</strong><small>{formatBytes(attachment.size_bytes, t)} · {formatDate(attachment.created_at, locale)}</small></div>
          </div>)}</div>
        : <div className="project-runtime-empty"><strong>{t("runtime.attachmentsEmpty")}</strong><span>{t("runtime.attachmentsEmptyHint")}</span></div>}
    </aside>
  </div>;
}

function SettingsPanel({ snapshot, service, onSaved, onModelChange, onLaunch, selectedModel, onSelectedModelChange, busy }: { snapshot: ProjectRuntimeSnapshot; service: ProjectRuntimeService; onSaved: (snapshot: ProjectRuntimeSnapshot) => void; onModelChange: ProjectRuntimeProps["onModelChange"]; onLaunch: () => void; selectedModel: string; onSelectedModelChange: (model: string) => void; busy?: RuntimeAction }) {
  const { t, locale } = useI18n();
  const project = snapshot.project;
  const [name, setName] = useState(project.name);
  const [about, setAbout] = useState(project.about ?? "");
  const [budget, setBudget] = useState(project.budget_tokens === null || project.budget_tokens === undefined ? "" : String(project.budget_tokens));
  const [keyId, setKeyId] = useState(project.status === "draft" ? "" : project.resolved_ai_key_id ?? "");
  const [provider, setProvider] = useState(project.status === "draft" ? "" : project.agent_provider ?? "");

  const [saving, setSaving] = useState(false);
  const [modelSaving, setModelSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  useEffect(() => { setName(project.name); setAbout(project.about ?? ""); setBudget(project.budget_tokens === null || project.budget_tokens === undefined ? "" : String(project.budget_tokens)); setKeyId(project.resolved_ai_key_id ?? ""); setProvider(project.agent_provider ?? ""); }, [project.id]);
  async function submit(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(undefined); setNotice(undefined);
    const selectionChanged = provider !== project.agent_provider || keyId !== project.resolved_ai_key_id;
    try {
      if (selectionChanged && keyId && (!selectedKey || !provider || providerForKey(selectedKey) !== provider || selectedKey.status !== "active" || selectedKey.has_secret === false)) {
        throw new Error(t("runtime.errorKeyInactive"));
      }
      if (selectionChanged && provider && !keyId) throw new Error(t("runtime.errorSelectKey"));
      const saved = await service.patchProject(project.id, { name: name.trim(), about: about.trim() || null, budget_tokens: budget.trim() ? Number(budget) : null, ...(selectionChanged ? { agent_provider: provider.trim() || null, resolved_ai_key_id: keyId || null } : {}) });
      const expectedProvider = selectionChanged ? provider.trim() || null : project.agent_provider ?? null;
      const expectedKeyId = selectionChanged ? keyId || null : project.resolved_ai_key_id ?? null;
      if (saved.agent_provider !== expectedProvider || saved.resolved_ai_key_id !== expectedKeyId) {
        throw new Error(t("runtime.errorNotConfirmed"));
      }
      const latest = await service.load(project.id);
      if (latest.project.agent_provider !== saved.agent_provider || latest.project.resolved_ai_key_id !== saved.resolved_ai_key_id) {
        throw new Error(t("runtime.errorReadback"));
      }
      onSaved(latest);
      onSelectedModelChange("");
      setNotice(selectionChanged ? t("runtime.noticeSelection") : t("runtime.noticeSaved"));
    } catch (err) { setError(readableError(t, err)); } finally { setSaving(false); }
  }

  async function applyModel() {
    if (!selectedModel) return;
    if (phaseOf(snapshot) !== "running") {
      setError(t("runtime.errorModelNeedsRun"));
      return;
    }
    setModelSaving(true); setError(undefined); setNotice(undefined);
    try {
      await onModelChange(project.id, selectedModel);
      setNotice(t("runtime.noticeModelSent", { model: selectedModel }));
    } catch (err) {
      setError(readableError(t, err));
    } finally {
      setModelSaving(false);
    }
  }

  const selectedKey = snapshot.keys.find((key) => key.id === keyId);
  const providers = [...new Set(snapshot.keys.filter((key) => key.status === "active" && key.has_secret !== false).map(providerForKey))].sort();
  const savedSelectionMatches = provider === project.agent_provider && keyId === project.resolved_ai_key_id;
  const launchReady = project.status === "draft" && savedSelectionMatches && Boolean(selectedKey && selectedKey.status === "active" && selectedKey.has_secret !== false && providerForKey(selectedKey) === project.agent_provider);
  const keyStatus = (key: ProjectKey) => key.status === "active" || key.status === undefined ? t("runtime.keyStatusActive") : t("runtime.keyStatusInactive");
  return <section className="project-runtime-surface">
    <SectionHeading eyebrow={t("runtime.settingsEyebrow")} title={t("runtime.settingsTitle")} detail={t("runtime.settingsDetail")} />
    {project.status === "draft" && <p className="project-runtime-inline-error" role="status">{t("runtime.draftHint")} <a href="/projects">{t("runtime.draftLink")}</a>. {t("runtime.draftModelHint")}</p>}
    <form className="project-runtime-settings-form" onSubmit={(event) => void submit(event)}>
      <div className="project-runtime-form-grid">
        <label>{t("runtime.fieldName")}<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
        <label>{t("runtime.fieldBudget")}<input type="number" min="0" step="1" value={budget} onChange={(event) => setBudget(event.target.value)} placeholder={t("runtime.budgetPlaceholder")} /></label>
        <label className="project-runtime-field-wide">{t("runtime.fieldDescription")}<textarea rows={4} value={about} onChange={(event) => setAbout(event.target.value)} placeholder={t("runtime.descriptionPlaceholder")} /></label>
        <label>{t("runtime.fieldProvider")}
          <select value={provider} onChange={(event) => { setProvider(event.target.value); setKeyId(""); onSelectedModelChange(""); }}>
            <option value="">{t("runtime.providerPlaceholder")}</option>
            {providers.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>{t("runtime.fieldKey")}
          <select value={keyId} onChange={(event) => { setKeyId(event.target.value); onSelectedModelChange(""); }} disabled={!provider}>
            <option value="">{t("runtime.keyPlaceholder")}</option>
            {snapshot.keys.filter((key) => providerForKey(key) === provider).map((key: ProjectKey) => <option key={key.id} value={key.id}>{t("runtime.keyOption", { name: key.name, provider: providerForKey(key) })}</option>)}
          </select>
          <small>{selectedKey ? t("runtime.keyOptionStatus", { provider: providerForKey(selectedKey), status: keyStatus(selectedKey) }) : t("runtime.keyHintEmpty")}</small>
        </label>
        <label>{t("runtime.fieldModel")}
          <select value={selectedModel} onChange={(event) => onSelectedModelChange(event.target.value)} disabled={!keyId || !savedSelectionMatches || snapshot.models.source !== "live"}>
            <option value="">{t("runtime.modelPlaceholder")}</option>
            {snapshot.models.models.map((item) => <option key={item.id} value={item.id}>{item.label || item.id}</option>)}
          </select>
          <small>{!savedSelectionMatches ? t("runtime.modelHintNotSaved") : snapshot.models.source === "live" ? t("runtime.modelHintLive") : t("runtime.modelHintUnavailable")}</small>
          <button type="button" className="project-runtime-button project-runtime-button--quiet" onClick={() => void applyModel()} disabled={!selectedModel || modelSaving || phaseOf(snapshot) !== "running"}>{modelSaving ? t("runtime.openingChat") : t("runtime.newChat")}</button>
        </label>
      </div>
      {error && <p className="project-runtime-inline-error" role="alert">{error}</p>}
      {notice && <p className="project-runtime-inline-success" role="status">{notice}</p>}
      <div className="project-runtime-form-actions">
        <span className="project-runtime-muted">{t("runtime.formHint")}</span>
        <button type="submit" className="project-runtime-button project-runtime-button--primary" disabled={saving || !name.trim()}>{saving ? t("runtime.saving") : t("runtime.save")}</button>
        {project.status === "draft" && <button type="button" className="project-runtime-button project-runtime-button--primary" onClick={onLaunch} disabled={Boolean(busy) || !launchReady} title={!launchReady ? t("runtime.launchTitle") : undefined}>{busy === "launch" ? t("runtime.actionPending") : t("runtime.launch")}</button>}
      </div>
    </form>
  </section>;
}

export function ProjectRuntime({ projectId, service = defaultService, initialSnapshot, initialTab = "overview", onNavigate, onModelChange }: ProjectRuntimeProps) {
  const { t, plural, locale } = useI18n();
  const [snapshot, setSnapshot] = useState<ProjectRuntimeSnapshot | undefined>(initialSnapshot);
  const [snapshotProjectId, setSnapshotProjectId] = useState(initialSnapshot?.project.id);
  const [tab, setTab] = useState<RuntimeTab>(initialTab);
  const [loading, setLoading] = useState(!initialSnapshot || initialSnapshot.project.id !== projectId);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<Notice>();
  const [actionState, setActionState] = useState<ActionState>();
  const [selectedModel, setSelectedModel] = useState("");
  const requestSequence = useRef(0);
  const currentProjectId = useRef(projectId);
  currentProjectId.current = projectId;
  const currentSnapshot = snapshotProjectId === projectId ? snapshot : undefined;
  const phase = phaseOf(currentSnapshot);

  const refresh = useCallback(async (quiet = false) => {
    const sequence = ++requestSequence.current;
    if (!quiet) setLoading(true);
    setError(undefined);
    try {
      const latest = await service.load(projectId);
      if (sequence !== requestSequence.current || currentProjectId.current !== projectId) return undefined;
      if (latest.project.id !== projectId) throw new Error(t("runtime.errorOtherProject"));
      setSnapshot(latest);
      setSnapshotProjectId(projectId);
      return latest;
    } catch (err) {
      if (sequence === requestSequence.current && currentProjectId.current === projectId) setError(readableError(t, err));
      return undefined;
    } finally {
      if (sequence === requestSequence.current && currentProjectId.current === projectId) setLoading(false);
    }
  }, [projectId, service, t]);

  useEffect(() => {
    requestSequence.current += 1;
    setActionState(undefined);
    setSelectedModel("");
    setNotice(undefined);
    setError(undefined);
    setTab(initialTab);
    if (initialSnapshot?.project.id === projectId) {
      setSnapshot(initialSnapshot);
      setSnapshotProjectId(projectId);
      setLoading(false);
    } else {
      setLoading(true);
      void refresh();
    }
    return () => { requestSequence.current += 1; };
  }, [initialSnapshot, projectId, refresh]);
  useEffect(() => {
    if (!currentSnapshot || actionState || (tab !== "overview" && tab !== "files" && notice?.key !== "launchPending")) return undefined;
    const timer = window.setInterval(() => {
      void refresh(true).then((latest) => {
        setNotice((current) => current?.key === "launchPending" && latest && phaseOf(latest) === "running"
          ? { tone: "success", message: t("runtime.podStarted") }
          : current);
      });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [actionState, currentSnapshot, notice?.key, refresh, t, tab]);

  async function executeAction(action: RuntimeAction) {
    if (ACTION_CONFIRM_KEYS[action] && !window.confirm(t(ACTION_CONFIRM_KEYS[action]))) return;
    setNotice(undefined); setError(undefined); setActionState({ action });
    try {
      const actionResult = await service.runAction(projectId, action);
      if (actionResult && typeof actionResult === "object" && "id" in actionResult && actionResult.id !== projectId) {
        throw new Error(t("runtime.errorActionResult"));
      }
      if (currentProjectId.current !== projectId) return;
      const latest = await refresh();
      if (!latest) {
        setActionState(undefined);
        return;
      }
      if (action === "launch" && ["error", "failed", "degraded"].includes(phaseOf(latest))) {
        throw new Error(latest.container?.last_error ?? latest.project.runtime?.last_error ?? t("runtime.errorLaunch"));
      }
      if (action === "launch" && phaseOf(latest) !== "running") {
        if (latest.project.status === "draft") throw new Error(t("runtime.errorLaunchDraft"));
        setNotice({ tone: "info", message: t("runtime.launchPending"), key: "launchPending" });
      } else {
        setNotice({ tone: "success", message: t("runtime.actionDoneNamed", { action: actionLabel(t, action).toLowerCase() }) });
      }
      setActionState(undefined);
    } catch (err) {
      if (currentProjectId.current !== projectId) return;
      const message = readableError(t, err);
      setActionState((current) => current?.action === action ? { ...current, error: message } : current);
      setError(message);
      return;
    }
    setActionState(undefined);
  }

  function selectTab(nextTab: RuntimeTab) { setTab(nextTab); onNavigate(nextTab, projectId); }
  if (!currentSnapshot && !error) return <main className="project-runtime"><div className="project-runtime-loading"><span className="project-runtime-spinner" aria-hidden="true" /><strong>{t("runtime.loadingTitle")}</strong><p>{t("runtime.loadingDetail")}</p></div></main>;
  if (!currentSnapshot) return <main className="project-runtime"><div className="project-runtime-error-page" role="alert"><strong>{t("runtime.unavailableTitle")}</strong><p>{error || t("runtime.unavailableDetail")}</p><button type="button" className="project-runtime-button project-runtime-button--primary" onClick={() => void refresh()}>{t("runtime.retry")}</button></div></main>;

  return <main className="project-runtime">
    <header className="project-runtime-header">
      <div>
        <p className="project-runtime-eyebrow">{t("runtime.title")}</p>
        <h1>{currentSnapshot.project.name}</h1>
        <p className="project-runtime-muted">{t("runtime.subtitle")}</p>
      </div>
      <div className="project-runtime-header-meta">
        <StatusPill phase={phase} />
        <button type="button" className="project-runtime-button project-runtime-button--quiet" onClick={() => void refresh()} disabled={loading}>{loading ? t("runtime.refreshing") : t("runtime.refresh")}</button>
      </div>
    </header>
    {notice && <div className={`project-runtime-notice project-runtime-notice--${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>{notice.message}</div>}
    {tab !== "overview" && actionState && <ProgressCard state={actionState} onRetry={() => void executeAction(actionState.action)} />}
    {error && (!actionState || !actionState.error) && <div className="project-runtime-notice project-runtime-notice--error" role="alert">{error}<button type="button" onClick={() => void refresh()}>{t("runtime.retryLoad")}</button></div>}
    <nav className="project-runtime-tabs" aria-label={t("runtime.tabsAria")}>
      {(["overview", "chat", "files", "settings"] as RuntimeTab[]).map((item) => <button key={item} type="button" className={tab === item ? "is-active" : ""} onClick={() => selectTab(item)}>
        {item === "overview" ? t("runtime.tabOverview") : item === "chat" ? t("runtime.tabChat") : item === "files" ? t("runtime.tabFiles") : t("runtime.tabSettings")}
        <small>{item === "overview"
          ? phaseLabel(t, phase)
          : item === "chat"
            ? plural("runtime.chatCount", currentSnapshot.chats?.project_chats.length ?? 0)
            : item === "files"
              ? plural("runtime.fileCount", currentSnapshot.files?.entries.length ?? 0)
              : t("runtime.tabSettingsHint")}</small>
      </button>)}
    </nav>
    {tab === "overview" && <RuntimeOverview snapshot={currentSnapshot} phase={phase} onAction={(action) => void executeAction(action)} actionState={actionState} />}
    {tab === "chat" && <ChatPanel snapshot={currentSnapshot} onNavigate={onNavigate} />}
    {tab === "files" && <FilesPanel snapshot={currentSnapshot} service={service} phase={phase} onRefreshStatus={() => void refresh()} />}
    {tab === "settings" && <SettingsPanel
      snapshot={currentSnapshot}
      service={service}
      onSaved={(latest) => { setSnapshot(latest); setSelectedModel(""); }}
      onModelChange={onModelChange}
      onLaunch={() => void executeAction("launch")}
      selectedModel={selectedModel}
      onSelectedModelChange={setSelectedModel}
      busy={actionState?.action}
    />}
  </main>;
}

export { PHASE_KEYS, phaseLabel, phaseOf, readableError };

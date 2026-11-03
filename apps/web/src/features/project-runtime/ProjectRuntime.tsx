import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
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
const ACTION_CONFIRMATIONS: Record<RuntimeAction, string> = {
  launch: "Запустить runtime проекта? Backend подготовит workspace и provision контейнера.",
  pause: "Поставить runtime проекта на паузу? Активный ход агента будет сохранён перед остановкой.",
  resume: "Продолжить проект? Backend снова provision контейнера и восстановит runtime.",
  reload: "Перезагрузить runtime? Текущий workspace будет сохранён перед операцией.",
  sync: "Синхронизировать workspace? Файлы проекта будут заново материализованы в runtime.",
  complete: "Завершить проект? Runtime будет остановлен, а проект перейдёт в completed.",
};
const ACTION_LABELS: Record<RuntimeAction, string> = {
  launch: "Запустить",
  pause: "Пауза",
  resume: "Продолжить",
  reload: "Перезагрузить",
  sync: "Синхронизировать",
  complete: "Завершить",
};
const PHASE_LABELS: Record<ProjectRuntimePhase, string> = {
  absent: "Не запущен",
  preparing: "Подготовка",
  pulling: "Загрузка образа",
  hydrating: "Hydrating workspace",
  provisioning: "Provisioning",
  starting: "Запуск",
  running: "Работает",
  paused: "На паузе",
  pausing: "Остановка",
  suspended: "Приостановлен",
  completed: "Завершён",
  failed: "Ошибка",
  error: "Ошибка",
  degraded: "Ограничен",
  unknown: "Состояние неизвестно",
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

type Notice = { tone: "success" | "error" | "info"; message: string };
type ActionState = { action: RuntimeAction; error?: string };
const LAUNCH_PENDING_NOTICE = "Запуск принят backend. Pod ещё готовится; наблюдаемое состояние будет обновляться автоматически.";

function isRuntimePhase(value: unknown): value is ProjectRuntimePhase {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(PHASE_LABELS, value);
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

function readableError(error: unknown): string {
  if (error instanceof ProjectRuntimeRequestError) {
    const suffix = error.traceId ? ` Код поддержки: ${error.traceId}.` : "";
    return `${error.message}${suffix}`;
  }
  return error instanceof Error ? error.message : "Операция не выполнена. Повторите попытку.";
}

function providerForKey(key: ProjectKey): string {
  const providers = new Set(["cursor", "codex", "claude_code", "xai"]);
  if (key.provider && providers.has(key.provider)) return key.provider;
  return key.api_kind === "anthropic_api" ? "claude_code" : "codex";
}

function formatBytes(value?: number | null): string {
  if (value === undefined || value === null || !Number.isFinite(value)) return "—";
  if (value < 1024) return `${Math.round(value)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = -1;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${units[unit]}`;
}

function formatCpu(value?: number | null): string {
  if (value === undefined || value === null || !Number.isFinite(value)) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} vCPU` : `${Math.round(value)} mCPU`;
}

function formatDate(value?: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function toneClass(tone: "positive" | "warning" | "danger" | "neutral"): string {
  return `project-runtime-pill project-runtime-pill--${tone}`;
}

function StatusPill({ phase }: { phase: ProjectRuntimePhase }) {
  return <span className={toneClass(PHASE_TONES[phase])}><span className="project-runtime-pill__dot" aria-hidden="true" />{PHASE_LABELS[phase]}</span>;
}

function SectionHeading({ eyebrow, title, detail, action }: { eyebrow: string; title: string; detail?: string; action?: ReactNode }) {
  return <div className="project-runtime-section-heading"><div><p className="project-runtime-eyebrow">{eyebrow}</p><h2>{title}</h2>{detail && <p className="project-runtime-muted">{detail}</p>}</div>{action}</div>;
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="project-runtime-metric"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function RuntimeOverview({ snapshot, phase, onAction, actionState, selectedModel }: { snapshot: ProjectRuntimeSnapshot; phase: ProjectRuntimePhase; onAction: (action: RuntimeAction) => void; actionState?: ActionState; selectedModel: string }) {
  const runtime = snapshot.container?.runtime ?? snapshot.project.runtime;
  const metrics = snapshot.metrics?.latest ?? runtime?.metrics ?? snapshot.container?.runtime_metrics;
  const metricAvailable = snapshot.metrics?.metrics_available ?? runtime?.metrics_available ?? Boolean(metrics);
  const actionBusy = actionState?.action;
  const selectedKey = snapshot.keys.find((key) => key.id === snapshot.project.resolved_ai_key_id);
  const launchReady = Boolean(snapshot.project.agent_provider && selectedKey && providerForKey(selectedKey) === snapshot.project.agent_provider && selectedKey.status === "active" && selectedKey.has_secret !== false);
  return <>
    <section className="project-runtime-hero-card">
      <div className="project-runtime-hero-main"><div className="project-runtime-mark" aria-hidden="true">{snapshot.project.name.slice(0, 1).toUpperCase()}</div><div><p className="project-runtime-eyebrow">Project runtime</p><h2>{snapshot.project.name}</h2><p className="project-runtime-muted">{snapshot.project.about || "Описание проекта пока не задано."}</p><div className="project-runtime-inline-meta"><StatusPill phase={phase} />{runtime?.runtime_ref && <code>{runtime.runtime_ref}</code>}{runtime?.restarts !== undefined && runtime.restarts !== null && <span>{runtime.restarts} рестартов</span>}</div></div></div>
      <div className="project-runtime-action-grid"><ActionButton action="launch" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} primary launchReady={launchReady} /><ActionButton action="resume" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} /><ActionButton action="pause" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} /><ActionButton action="reload" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} /><ActionButton action="sync" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} /><ActionButton action="complete" phase={phase} projectStatus={snapshot.project.status} busy={actionBusy} onClick={onAction} danger /></div>
    </section>
    {snapshot.project.status === "draft" && !launchReady && <p className="project-runtime-inline-error" role="status">Перед запуском сохраните provider и активный AI key на вкладке Settings. Backend launch принимает provider/key, но не model; live model появится после готовности Pod. Запуск не подменяет ключ.</p>}
    {actionState && <ProgressCard state={actionState} onRetry={() => onAction(actionState.action)} />}
    {(runtime?.last_error || snapshot.container?.last_error || phase === "failed" || phase === "error" || phase === "degraded") && <ErrorCard message={runtime?.last_error ?? snapshot.container?.last_error ?? "Backend сообщил ошибочное состояние runtime без дополнительной причины."} retryable={phase === "failed" || phase === "error" || phase === "degraded"} onRetry={() => onAction("reload")} />}
    <section className="project-runtime-surface"><SectionHeading eyebrow="Runtime health" title="Наблюдаемое состояние" detail="CPU и RAM показываются только когда backend вернул свежий sample." /><div className="project-runtime-metrics-grid"><MetricCard label="CPU" value={metricAvailable ? formatCpu(metrics?.cpu_millicores) : "Недоступно"} detail={metricAvailable ? "Текущая нагрузка контейнера" : runtime?.metrics_unavailable_reason ?? "Метрики ещё не поступили"} /><MetricCard label="RAM" value={metricAvailable ? formatBytes(metrics?.memory_bytes) : "Недоступно"} detail={metricAvailable ? "Текущее потребление памяти" : "Backend не подтвердил sample"} /><MetricCard label="Phase" value={runtime?.phase || "—"} detail={runtime?.ready === true ? "Readiness подтверждён" : runtime?.ready === false ? "Readiness не подтверждён" : "Readiness не сообщён"} /><MetricCard label="Обновлено" value={formatDate(metrics?.timestamp || runtime?.observed_at)} detail={snapshot.metrics?.stale ? "Sample устарел" : "Источник: runtime metrics"} /></div></section>
    <section className="project-runtime-surface"><SectionHeading eyebrow="Runtime facts" title="Контракт backend" detail="Здесь отображаются intent и observation, чтобы ошибка не маскировалась под placeholder." /><dl className="project-runtime-facts"><div><dt>Desired state</dt><dd>{runtime?.desired_state || "—"}</dd></div><div><dt>Orchestrator</dt><dd>{runtime?.orchestrator_status || runtime?.status || "—"}</dd></div><div><dt>Hydrate generation</dt><dd>{runtime?.hydrate_generation ?? "—"}</dd></div><div><dt>Workspace outdated</dt><dd>{snapshot.project.workspace_outdated_at ? formatDate(snapshot.project.workspace_outdated_at) : "Нет"}</dd></div></dl></section>
  </>;
}

function ActionButton({ action, phase, projectStatus, busy, onClick, primary = false, danger = false, launchReady = true }: { action: RuntimeAction; phase: ProjectRuntimePhase; projectStatus: string; busy?: RuntimeAction; onClick: (action: RuntimeAction) => void; primary?: boolean; danger?: boolean; launchReady?: boolean }) {
  const allowed = action === "launch" ? projectStatus === "draft" && phase === "absent" : action === "resume" ? ["paused", "suspended", "completed"].includes(phase) : action === "pause" ? ["running", "degraded", "starting"].includes(phase) : action === "reload" ? ["running", "degraded", "failed", "error", "unknown"].includes(phase) : action === "sync" ? ["running", "paused", "degraded", "failed", "error"].includes(phase) : ["running", "paused", "degraded", "failed", "error", "absent"].includes(phase);
  const disabled = Boolean(busy) || !allowed || (action === "launch" && !launchReady);
  const title = disabled && !busy ? action === "launch" && !launchReady ? "Сначала сохраните выбранные provider и активный key в Settings." : `Действие недоступно при состоянии «${PHASE_LABELS[phase]}».` : undefined;
  return <button type="button" className={`project-runtime-button${primary ? " project-runtime-button--primary" : ""}${danger ? " project-runtime-button--danger" : ""}`} disabled={disabled} onClick={() => onClick(action)} title={title}>{busy === action ? "Ожидаем backend…" : ACTION_LABELS[action]}</button>;
}

function ProgressCard({ state, onRetry }: { state: ActionState; onRetry: () => void }) {
  return <section className="project-runtime-progress" aria-live="polite"><div className="project-runtime-progress__head"><div><p className="project-runtime-eyebrow">Операция runtime</p><strong>{ACTION_LABELS[state.action]}</strong></div></div>{state.error ? <div className="project-runtime-progress__failure"><p className="project-runtime-progress__error">{state.error}</p><button type="button" className="project-runtime-button project-runtime-button--quiet" onClick={onRetry}>Повторить {ACTION_LABELS[state.action].toLowerCase()}</button></div> : <p className="project-runtime-muted">Ожидаем ответ и повторное чтение состояния из backend. Статус Pod будет показан после подтверждения наблюдением.</p>}</section>;
}

function ErrorCard({ message, retryable, onRetry }: { message: string; retryable: boolean; onRetry: () => void }) {
  return <aside className="project-runtime-error" role="alert"><span className="project-runtime-error__icon" aria-hidden="true">!</span><div><strong>Runtime не готов</strong><p>{message}</p><small>{retryable ? "Повторите reload после устранения причины." : "Проверьте состояние проекта и backend."}</small></div>{retryable && <button type="button" className="project-runtime-button project-runtime-button--quiet" onClick={onRetry}>Повторить reload</button>}</aside>;
}

function ChatPanel({ snapshot, onNavigate }: { snapshot: ProjectRuntimeSnapshot; onNavigate: ProjectRuntimeProps["onNavigate"] }) {
  const projectChats = snapshot.chats?.project_chats ?? snapshot.chats?.projects.find((item) => item.project_id === snapshot.project.id)?.chats ?? [];
  return <section className="project-runtime-surface project-runtime-chat"><SectionHeading eyebrow="Chat" title="Чаты проекта" detail="Выберите существующую session, чтобы продолжить работу агента." action={<button type="button" className="project-runtime-button project-runtime-button--primary" onClick={() => onNavigate("chat", snapshot.project.id)}>Открыть чат</button>} />{projectChats.length ? <div className="project-runtime-chat-list">{projectChats.map((chat) => <ChatRow key={chat.session_id} chat={chat} projectId={snapshot.project.id} onNavigate={onNavigate} />)}</div> : <div className="project-runtime-empty"><strong>Сессии пока не найдены</strong><span>Создание и отправка сообщений живут в chat surface backend.</span></div>}</section>;
}

function ChatRow({ chat, projectId, onNavigate }: { chat: ChatSidebarItem; projectId: string; onNavigate: ProjectRuntimeProps["onNavigate"] }) {
  return <button type="button" className="project-runtime-chat-row" onClick={() => onNavigate("chat", projectId, chat.session_id)}><span className="project-runtime-chat-icon" aria-hidden="true">↗</span><span><strong>{chat.title || "Новая сессия"}</strong><small>{chat.session_id} · {chat.last_message_at ? formatDate(chat.last_message_at) : "нет сообщений"}</small></span><span className="project-runtime-chat-status">{chat.status || (chat.has_messages ? "Есть сообщения" : "Пустая")}</span></button>;
}

function FilesPanel({ snapshot, service, phase, onRefreshStatus }: { snapshot: ProjectRuntimeSnapshot; service: ProjectRuntimeService; phase: ProjectRuntimePhase; onRefreshStatus: () => void }) {
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
      if (sequence === requestSequence.current) setError(readableError(err));
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [phase, service, snapshot.project.id]);

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
      if (sequence === requestSequence.current) setError(readableError(err));
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
      setError(readableError(err));
    }
  }
  return <div className="project-runtime-files-layout"><section className="project-runtime-surface"><SectionHeading eyebrow="Files" title="Workspace" detail="Live entries из runtime контейнера. Создание папок недоступно: backend не предоставляет mkdir API." action={<button type="button" className="project-runtime-button project-runtime-button--quiet" onClick={() => phase === "running" ? void browse(path) : onRefreshStatus()} disabled={loading}>{loading ? "Обновляем…" : phase === "running" ? "Обновить" : "Обновить статус Pod"}</button>} />{podUnavailable && <p className="project-runtime-inline-error" role="status">Live workspace недоступен: backend разрешает чтение только при running Pod. Обновите статус Pod; файлы не загружаются из архива или MinIO.</p>}{workspaceUnavailable && !podUnavailable && <p className="project-runtime-inline-error" role="status">Live workspace пока не доступен; повторяем чтение после запуска Pod.</p>}<div className="project-runtime-path"><button type="button" onClick={() => phase === "running" ? void browse("") : onRefreshStatus()}>/workspace</button>{path && <><span>/</span><span>{path}</span></>}</div>{error && <p className="project-runtime-inline-error" role="alert">{error}</p>}<div className="project-runtime-file-list">{visibleEntries.length ? visibleEntries.map((entry) => <div className="project-runtime-file-row" key={entry.path}><button type="button" onClick={() => void openFile(entry)}><span className={`project-runtime-file-kind project-runtime-file-kind--${entry.kind}`}>{entry.kind === "dir" ? "DIR" : "FILE"}</span><span><strong>{entry.name}</strong><small>{entry.path} · {entry.size === null || entry.size === undefined ? "—" : `${entry.size} B`}</small></span></button>{entry.kind === "file" && <button type="button" className="project-runtime-icon-button" onClick={() => void download(entry)} aria-label={`Скачать ${entry.name}`} title="Скачать">↓</button>}</div>) : <div className="project-runtime-empty"><strong>{podUnavailable ? "Pod недоступен" : "В workspace нет entries"}</strong><span>{loading ? "Читаем runtime…" : podUnavailable ? "Запустите или возобновите проект, затем обновите статус." : workspaceUnavailable ? "Ожидаем готовность runtime для чтения workspace." : "Backend вернул пустую директорию."}</span></div>}</div>{preview && <article className="project-runtime-preview"><div className="project-runtime-preview__head"><strong>{preview.path}</strong><button type="button" className="project-runtime-icon-button" onClick={() => setPreview(undefined)} aria-label="Закрыть preview">×</button></div><pre>{preview.content}</pre></article>}</section><aside className="project-runtime-surface"><SectionHeading eyebrow="Attachments" title="Файлы проекта" detail="Вложения доступны через отдельный project attachment API." />{attachments.length ? <div className="project-runtime-attachments">{attachments.map((attachment) => <div className="project-runtime-attachment" key={attachment.id}><span aria-hidden="true">▧</span><div><strong>{attachment.filename}</strong><small>{formatBytes(attachment.size_bytes)} · {formatDate(attachment.created_at)}</small></div></div>)}</div> : <div className="project-runtime-empty"><strong>Вложений пока нет</strong><span>Загрузка файлов требует attachment workflow.</span></div>}</aside></div>;
}

function SettingsPanel({ snapshot, service, onSaved, onModelChange, onLaunch, selectedModel, onSelectedModelChange, busy }: { snapshot: ProjectRuntimeSnapshot; service: ProjectRuntimeService; onSaved: (snapshot: ProjectRuntimeSnapshot) => void; onModelChange: ProjectRuntimeProps["onModelChange"]; onLaunch: () => void; selectedModel: string; onSelectedModelChange: (model: string) => void; busy?: RuntimeAction }) {
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
        throw new Error("Выбранный AI key неактивен, без секрета или не соответствует выбранному provider.");
      }
      if (selectionChanged && provider && !keyId) throw new Error("Выберите доступный AI key для выбранного provider.");
      const saved = await service.patchProject(project.id, { name: name.trim(), about: about.trim() || null, budget_tokens: budget.trim() ? Number(budget) : null, ...(selectionChanged ? { agent_provider: provider.trim() || null, resolved_ai_key_id: keyId || null } : {}) });
      const expectedProvider = selectionChanged ? provider.trim() || null : project.agent_provider ?? null;
      const expectedKeyId = selectionChanged ? keyId || null : project.resolved_ai_key_id ?? null;
      if (saved.agent_provider !== expectedProvider || saved.resolved_ai_key_id !== expectedKeyId) {
        throw new Error("Backend не подтвердил выбранные provider и AI key.");
      }
      const latest = await service.load(project.id);
      if (latest.project.agent_provider !== saved.agent_provider || latest.project.resolved_ai_key_id !== saved.resolved_ai_key_id) {
        throw new Error("Повторное чтение backend не подтвердило выбранные provider и AI key.");
      }
      onSaved(latest);
      onSelectedModelChange("");
      setNotice(selectionChanged ? "Provider и AI key сохранены и подтверждены backend. После запуска Pod будут доступны live models." : "Настройки проекта сохранены и повторно подтверждены backend.");
    } catch (err) { setError(readableError(err)); } finally { setSaving(false); }
  }

  async function applyModel() {
    if (!selectedModel) return;
    if (phaseOf(snapshot) !== "running") {
      setError("Модель применяется при создании agent session; сначала запустите Pod.");
      return;
    }
    setModelSaving(true); setError(undefined); setNotice(undefined);
    try {
      await onModelChange(project.id, selectedModel);
      setNotice(`Создание agent session с моделью ${selectedModel} передано chat surface.`);
    } catch (err) {
      setError(readableError(err));
    } finally {
      setModelSaving(false);
    }
  }

  const selectedKey = snapshot.keys.find((key) => key.id === keyId);
  const providers = [...new Set(snapshot.keys.filter((key) => key.status === "active" && key.has_secret !== false).map(providerForKey))].sort();
  const savedSelectionMatches = provider === project.agent_provider && keyId === project.resolved_ai_key_id;
  const launchReady = project.status === "draft" && savedSelectionMatches && Boolean(selectedKey && selectedKey.status === "active" && selectedKey.has_secret !== false && providerForKey(selectedKey) === project.agent_provider);
  return <section className="project-runtime-surface"><SectionHeading eyebrow="Settings" title="Настройки проекта" detail="Provider/key сохраняются и подтверждаются повторным чтением backend." />{project.status === "draft" && <p className="project-runtime-inline-error" role="status">Новый черновик создаётся в Projects через реальный POST `/cabinets/{'{cabinet_id}'}/projects`, поскольку там доступен кабинет; <a href="/projects">Перейти к созданию черновика</a>. Live model нельзя выбрать до запуска: `/models/live` требует запущенный Pod, а project launch не принимает model. После запуска выберите live model здесь для новой agent session.</p>}<form className="project-runtime-settings-form" onSubmit={(event) => void submit(event)}><div className="project-runtime-form-grid"><label>Название<input value={name} onChange={(event) => setName(event.target.value)} required /></label><label>Budget tokens<input type="number" min="0" step="1" value={budget} onChange={(event) => setBudget(event.target.value)} placeholder="Без лимита" /></label><label className="project-runtime-field-wide">Description<textarea rows={4} value={about} onChange={(event) => setAbout(event.target.value)} placeholder="Что делает этот проект?" /></label><label>Provider<select value={provider} onChange={(event) => { setProvider(event.target.value); setKeyId(""); onSelectedModelChange(""); }}><option value="">Выберите provider</option>{providers.map((item) => <option key={item} value={item}>{item}</option>)}</select></label><label>AI key<select value={keyId} onChange={(event) => { setKeyId(event.target.value); onSelectedModelChange(""); }} disabled={!provider}><option value="">Выберите доступный key</option>{snapshot.keys.filter((key) => providerForKey(key) === provider).map((key: ProjectKey) => <option key={key.id} value={key.id}>{key.name} · {providerForKey(key)} · {key.status || "active"}</option>)}</select><small>{selectedKey ? `${providerForKey(selectedKey)} · ${selectedKey.status || "active"}` : "Список содержит доступные runtime keys backend."}</small></label><label>Model<select value={selectedModel} onChange={(event) => onSelectedModelChange(event.target.value)} disabled={!keyId || !savedSelectionMatches || snapshot.models.source !== "live"}><option value="">Выберите модель явно</option>{snapshot.models.models.map((item) => <option key={item.id} value={item.id}>{item.label || item.id}</option>)}</select><small>{!savedSelectionMatches ? "Сначала сохраните provider и key; модели загружаются для сохранённого key." : snapshot.models.source === "live" ? "Live models подтверждены backend. Выбор применяется при создании agent session." : "Live model недоступна до запуска Pod: endpoint `/models/live` требует running runtime."}</small><button type="button" className="project-runtime-button project-runtime-button--quiet" onClick={() => void applyModel()} disabled={!selectedModel || modelSaving || phaseOf(snapshot) !== "running"}>{modelSaving ? "Открываем чат…" : "Новый чат с выбранной моделью"}</button></label></div>{error && <p className="project-runtime-inline-error" role="alert">{error}</p>}{notice && <p className="project-runtime-inline-success" role="status">{notice}</p>}<div className="project-runtime-form-actions"><span className="project-runtime-muted">Project launch принимает сохранённые provider/key; выбранная модель передаётся только при создании agent session после готовности Pod.</span><button type="submit" className="project-runtime-button project-runtime-button--primary" disabled={saving || !name.trim()}>{saving ? "Сохраняем…" : "Сохранить настройки"}</button>{project.status === "draft" && <button type="button" className="project-runtime-button project-runtime-button--primary" onClick={onLaunch} disabled={Boolean(busy) || !launchReady} title={!launchReady ? "Сначала явно выберите и сохраните provider и активный AI key." : undefined}>{busy === "launch" ? "Ожидаем backend…" : "Запустить проект"}</button>}</div></form></section>;
}

export function ProjectRuntime({ projectId, service = defaultService, initialSnapshot, initialTab = "overview", onNavigate, onModelChange }: ProjectRuntimeProps) {
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
      if (latest.project.id !== projectId) throw new Error("Backend вернул snapshot другого проекта.");
      setSnapshot(latest);
      setSnapshotProjectId(projectId);
      return latest;
    } catch (err) {
      if (sequence === requestSequence.current && currentProjectId.current === projectId) setError(readableError(err));
      return undefined;
    } finally {
      if (sequence === requestSequence.current && currentProjectId.current === projectId) setLoading(false);
    }
  }, [projectId, service]);

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
    if (!currentSnapshot || actionState || (tab !== "overview" && tab !== "files" && notice?.message !== LAUNCH_PENDING_NOTICE)) return undefined;
    const timer = window.setInterval(() => {
      void refresh(true).then((latest) => {
        setNotice((current) => current?.message === LAUNCH_PENDING_NOTICE && latest && phaseOf(latest) === "running"
          ? { tone: "success", message: "Pod запущен и readiness подтверждена backend." }
          : current);
      });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [actionState, currentSnapshot, notice?.message, refresh, tab]);

  async function executeAction(action: RuntimeAction) {
    if (ACTION_CONFIRMATIONS[action] && !window.confirm(ACTION_CONFIRMATIONS[action])) return;
    setNotice(undefined); setError(undefined); setActionState({ action });
    try {
      const actionResult = await service.runAction(projectId, action);
      if (actionResult && typeof actionResult === "object" && "id" in actionResult && actionResult.id !== projectId) {
        throw new Error("Backend вернул результат операции для другого проекта.");
      }
      if (currentProjectId.current !== projectId) return;
      const latest = await refresh();
      if (!latest) {
        setActionState(undefined);
        return;
      }
      if (action === "launch" && ["error", "failed", "degraded"].includes(phaseOf(latest))) {
        throw new Error(latest.container?.last_error ?? latest.project.runtime?.last_error ?? "Backend сообщил ошибку запуска Pod.");
      }
      if (action === "launch" && phaseOf(latest) !== "running") {
        if (latest.project.status === "draft") throw new Error("Запрос запуска завершился, но повторное чтение backend всё ещё показывает draft.");
        setNotice({ tone: "info", message: "Запуск принят backend. Pod ещё готовится; наблюдаемое состояние будет обновляться автоматически." });
      } else {
        setNotice({ tone: "success", message: `${ACTION_LABELS[action]} подтверждено backend.` });
      }
      setActionState(undefined);
    } catch (err) {
      if (currentProjectId.current !== projectId) return;
      const message = readableError(err);
      setActionState((current) => current?.action === action ? { ...current, error: message } : current);
      setError(message);
      return;
    }
    setActionState(undefined);
  }

  function selectTab(nextTab: RuntimeTab) { setTab(nextTab); onNavigate(nextTab, projectId); }
  if (!currentSnapshot && !error) return <main className="project-runtime"><div className="project-runtime-loading"><span className="project-runtime-spinner" aria-hidden="true" /><strong>Загружаем project runtime</strong><p>Читаем project, container, metrics, files, keys и chats из backend.</p></div></main>;
  if (!currentSnapshot) return <main className="project-runtime"><div className="project-runtime-error-page" role="alert"><strong>Project runtime недоступен</strong><p>{error || "Backend не вернул snapshot проекта."}</p><button type="button" className="project-runtime-button project-runtime-button--primary" onClick={() => void refresh()}>Повторить</button></div></main>;

  return <main className="project-runtime"><header className="project-runtime-header"><div><p className="project-runtime-eyebrow">Project / {currentSnapshot.project.id}</p><h1>{currentSnapshot.project.name}</h1><p className="project-runtime-muted">Управление runtime, workspace, AI configuration и chat связью.</p></div><div className="project-runtime-header-meta"><StatusPill phase={phase} /><button type="button" className="project-runtime-button project-runtime-button--quiet" onClick={() => void refresh()} disabled={loading}>{loading ? "Обновляем…" : "Обновить"}</button></div></header>{notice && <div className={`project-runtime-notice project-runtime-notice--${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>{notice.message}</div>}{tab !== "overview" && actionState && <ProgressCard state={actionState} onRetry={() => void executeAction(actionState.action)} />}{error && (!actionState || !actionState.error) && <div className="project-runtime-notice project-runtime-notice--error" role="alert">{error}<button type="button" onClick={() => void refresh()}>Повторить загрузку</button></div>}<nav className="project-runtime-tabs" aria-label="Project runtime sections">{(["overview", "chat", "files", "settings"] as RuntimeTab[]).map((item) => <button key={item} type="button" className={tab === item ? "is-active" : ""} onClick={() => selectTab(item)}>{item === "overview" ? "Runtime" : item === "chat" ? "Chat" : item === "files" ? "Files" : "Settings"}<small>{item === "overview" ? PHASE_LABELS[phase] : item === "chat" ? `${currentSnapshot.chats?.project_chats.length ?? 0} sessions` : item === "files" ? `${currentSnapshot.files?.entries.length ?? 0} entries` : "Project config"}</small></button>)}</nav>{tab === "overview" && <RuntimeOverview snapshot={currentSnapshot} phase={phase} onAction={(action) => void executeAction(action)} actionState={actionState} selectedModel={selectedModel} />}{tab === "chat" && <ChatPanel snapshot={currentSnapshot} onNavigate={onNavigate} />}{tab === "files" && <FilesPanel snapshot={currentSnapshot} service={service} phase={phase} onRefreshStatus={() => void refresh()} />}{tab === "settings" && <SettingsPanel snapshot={currentSnapshot} service={service} onSaved={(latest) => { setSnapshot(latest); setSelectedModel(""); }} onModelChange={onModelChange} onLaunch={() => void executeAction("launch")} selectedModel={selectedModel} onSelectedModelChange={setSelectedModel} busy={actionState?.action} />}</main>;
}

export { ACTION_LABELS, PHASE_LABELS, phaseOf, readableError };

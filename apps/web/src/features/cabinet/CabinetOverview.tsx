import { useEffect, useMemo, useState } from "react";
import type { CabinetNavigationTarget, CabinetOverviewService, CabinetOverviewSnapshot, CabinetOverviewSection, ProjectSummary } from "./contracts";
import "./cabinet-overview.css";

export interface CabinetOverviewProps {
  cabinetId: string;
  cabinetName: string;
  service: CabinetOverviewService;
  onNavigate(target: CabinetNavigationTarget): void;
  initialSnapshot?: CabinetOverviewSnapshot;
}

type ModuleDestination = { projectId: string; moduleId: string; moduleName: string; viewSlug: string; title: string; placement: "management" | "data" };

const statusLabels: Record<string, string> = {
  draft: "Черновик",
  active: "Активен",
  paused: "Приостановлен",
  error: "Ошибка",
  completed: "Завершён",
};

const statusTones: Record<string, string> = {
  draft: "neutral",
  active: "positive",
  paused: "warning",
  error: "danger",
  completed: "muted",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numberMetric(metrics: Record<string, unknown> | undefined, key: string): number | undefined {
  const value = metrics?.[key];
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}

function formatCount(value: number): string {
  return new Intl.NumberFormat("ru-RU").format(value);
}

function formatActivity(value: unknown): string | undefined {
  if (typeof value !== "string" || value.trim() === "") return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function moduleDestinations(snapshot: CabinetOverviewSnapshot): ModuleDestination[] {
  const result: ModuleDestination[] = [];
  const projectId = snapshot.selection?.project_id;
  if (!projectId) return result;
  for (const module of snapshot.modules ?? []) {
    for (const raw of snapshot.moduleTabs?.[module.id] ?? []) {
      if (!isRecord(raw) || typeof raw.view_slug !== "string") continue;
      const nav = raw.nav;
      const placement = isRecord(nav) && nav.placement === "data" ? "data" : "management";
      const title = typeof raw.title === "string" && raw.title.trim() ? raw.title.trim() : module.name;
      result.push({ projectId, moduleId: module.id, moduleName: module.name, viewSlug: raw.view_slug, title, placement });
    }
  }
  return result.sort((left, right) => left.title.localeCompare(right.title, "ru"));
}

function projectChatTarget(snapshot: CabinetOverviewSnapshot, projects: ProjectSummary[]): { projectId: string; sessionId?: string } | undefined {
  const chatReadableProjects = projects.filter((project) => ["active", "paused", "error"].includes(project.status));
  const projectIds = new Set(chatReadableProjects.map((project) => project.id));
  const sidebar = snapshot.sidebar;
  const chats = [
    ...(sidebar?.pinned ?? []),
    ...(sidebar?.projects.flatMap((project) => project.chats) ?? []),
  ];
  const selectedSession = snapshot.selection?.chat_session_id;
  if (selectedSession) {
    const match = chats.find((chat) => chat.session_id === selectedSession && projectIds.has(chat.project_id));
    if (match) return { projectId: match.project_id, sessionId: match.session_id };
  }
  const selectedProject = snapshot.selection?.project_id;
  if (selectedProject && projectIds.has(selectedProject)) return { projectId: selectedProject };
  const firstChat = chats.find((chat) => projectIds.has(chat.project_id));
  return firstChat ? { projectId: firstChat.project_id, sessionId: firstChat.session_id } : undefined;
}

function projectState(project: ProjectSummary): string | undefined {
  if (typeof project.observed_state === "string" && project.observed_state) return project.observed_state;
  return undefined;
}

function sectionErrorLabel(section: CabinetOverviewSection): string {
  const labels: Record<CabinetOverviewSection, string> = {
    cabinets: "список кабинетов",
    cabinet: "данные кабинета",
    projects: "проекты",
    metrics: "метрики и присутствие",
    presence: "присутствие",
    selection: "выбор проекта и чата",
    sidebar: "список чатов",
    modules: "навигация модулей",
  };
  return labels[section];
}

export function CabinetOverview({ cabinetId, cabinetName, service, onNavigate, initialSnapshot }: CabinetOverviewProps) {
  const [snapshot, setSnapshot] = useState<CabinetOverviewSnapshot | undefined>(initialSnapshot);
  const [loading, setLoading] = useState(initialSnapshot === undefined);
  const [loadError, setLoadError] = useState<string>();
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(initialSnapshot === undefined);
    setLoadError(undefined);
    if (initialSnapshot === undefined) setSnapshot(undefined);
    void service.load(cabinetId, { signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      const hasData = result.cabinet !== undefined || result.projects !== undefined || result.cabinets !== undefined;
      if (!hasData) {
        setSnapshot(undefined);
        setLoadError("Не удалось загрузить данные кабинета. Попробуйте ещё раз.");
        return;
      }
      setSnapshot(result);
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setLoadError(error instanceof Error ? error.message : "Не удалось загрузить данные кабинета.");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [cabinetId, initialSnapshot, service, attempt]);

  const projects = snapshot?.projects ?? [];
  const destinations = useMemo(() => snapshot ? moduleDestinations(snapshot) : [], [snapshot]);
  const management = destinations.filter((item) => item.placement === "management");
  const data = destinations.filter((item) => item.placement === "data");
  const chat = snapshot ? projectChatTarget(snapshot, projects) : undefined;
  const cabinet = snapshot?.cabinet ?? snapshot?.cabinets?.find((item) => item.id === cabinetId);
  const metrics = snapshot?.metrics;
  const employeesOnline = numberMetric(metrics, "employees_online");
  const employeesTotal = numberMetric(metrics, "employees_total");
  const tokensUsed = numberMetric(metrics, "agent_tokens_used");
  const requests = numberMetric(metrics, "agent_requests") ?? numberMetric(metrics, "agent_messages");
  const activity = formatActivity(metrics?.last_activity_at);
  const confirmedProjectCount = snapshot?.projects ? snapshot.projects.length : typeof cabinet?.projects_count === "number" && Number.isFinite(cabinet.projects_count) ? cabinet.projects_count : undefined;
  const sectionErrors = snapshot ? (Object.entries(snapshot.errors) as Array<[CabinetOverviewSection, Error]>).filter(([, error]) => error instanceof Error) : [];

  return <main className="cabinet-overview" aria-labelledby="cabinet-overview-title">
    {loading && <div className="cabinet-overview-loading" role="status" aria-live="polite"><span className="cabinet-overview-spinner" aria-hidden="true" />Загрузка обзора кабинета…</div>}
    {!loading && loadError && <section className="cabinet-overview-alert" role="alert"><div><strong>Обзор временно недоступен</strong><span>{loadError}</span></div><button type="button" onClick={() => setAttempt((value) => value + 1)}>Повторить</button></section>}
    {!loading && snapshot && <>
      {sectionErrors.length > 0 && <section className="cabinet-overview-alert cabinet-overview-alert--partial" role="status">
        <strong>Часть данных кабинета недоступна</strong>
        <span>{sectionErrors.map(([section]) => sectionErrorLabel(section)).join(", ")}.</span>
        <button type="button" onClick={() => setAttempt((value) => value + 1)}>Обновить</button>
      </section>}

      <header className="cabinet-overview-header">
        <div>
          <p className="cabinet-overview-eyebrow">Рабочее пространство</p>
          <h1 id="cabinet-overview-title">{cabinet?.name ?? cabinetName}</h1>
          <p className="cabinet-overview-subtitle">Обзор кабинета и доступ к рабочим разделам</p>
        </div>
        <button type="button" className="cabinet-overview-project-link" onClick={() => onNavigate({ type: "projects" })}>Все проекты <span aria-hidden="true">→</span></button>
      </header>

      <section className="cabinet-overview-context" aria-label="Контекст кабинета">
        <div className="cabinet-overview-context-icon" aria-hidden="true">{(cabinet?.name ?? cabinetName).trim().slice(0, 1).toLocaleUpperCase("ru") || "К"}</div>
        <div className="cabinet-overview-context-copy">
          <span>Кабинет</span>
          <strong>{cabinet?.name ?? cabinetName}</strong>
        </div>
        <div className="cabinet-overview-context-copy">
          <span>Компания</span>
          <strong>{typeof cabinet?.company_name === "string" && cabinet.company_name.trim() ? cabinet.company_name : cabinet?.company_id ?? (typeof cabinet?.owner_company_id === "string" ? cabinet.owner_company_id : undefined) ?? "Не указана"}</strong>
        </div>
        <div className="cabinet-overview-context-copy">
          <span>Проекты в кабинете</span>
          <strong>{confirmedProjectCount !== undefined ? formatCount(confirmedProjectCount) : "Недоступно"}</strong>
        </div>
      </section>

      <section className="cabinet-overview-quick-links" aria-label="Основные разделы">
        <button type="button" className="cabinet-overview-quick-link" disabled={!management[0]} onClick={() => management[0] && onNavigate({ type: "management", projectId: management[0].projectId, moduleId: management[0].moduleId, viewSlug: management[0].viewSlug, title: management[0].title })}>
          <span className="cabinet-overview-quick-icon" aria-hidden="true">⚙</span><span><strong>Управление</strong><small>{management[0] ? "Настройки и инструменты кабинета" : "Нет подключённых разделов"}</small></span><span className="cabinet-overview-arrow" aria-hidden="true">→</span>
        </button>
        <button type="button" className="cabinet-overview-quick-link" disabled={!data[0]} onClick={() => data[0] && onNavigate({ type: "data", projectId: data[0].projectId, moduleId: data[0].moduleId, viewSlug: data[0].viewSlug, title: data[0].title })}>
          <span className="cabinet-overview-quick-icon" aria-hidden="true">▤</span><span><strong>Данные</strong><small>{data[0] ? "Открыть данные подключённого модуля" : "Нет подключённых разделов"}</small></span><span className="cabinet-overview-arrow" aria-hidden="true">→</span>
        </button>
        <button type="button" className="cabinet-overview-quick-link" onClick={() => onNavigate({ type: "projects" })}>
          <span className="cabinet-overview-quick-icon" aria-hidden="true">▦</span><span><strong>Проекты</strong><small>{confirmedProjectCount !== undefined ? `${formatCount(confirmedProjectCount)} в кабинете` : "Открыть проекты"}</small></span><span className="cabinet-overview-arrow" aria-hidden="true">→</span>
        </button>
        <button type="button" className="cabinet-overview-quick-link" disabled={!chat} onClick={() => chat && onNavigate({ type: "chat", projectId: chat.projectId, ...(chat.sessionId ? { sessionId: chat.sessionId } : {}) })}>
          <span className="cabinet-overview-quick-icon" aria-hidden="true">◌</span><span><strong>Чат</strong><small>{chat ? chat.sessionId ? "Продолжить последний выбранный диалог" : `Открыть чат проекта «${projects.find((project) => project.id === chat.projectId)?.name ?? "Проект"}»` : "Сначала выберите проект"}</small></span><span className="cabinet-overview-arrow" aria-hidden="true">→</span>
        </button>
      </section>

      <section className="cabinet-overview-section" aria-labelledby="cabinet-projects-title">
        <div className="cabinet-overview-section-heading"><div><p className="cabinet-overview-eyebrow">Работа в кабинете</p><h2 id="cabinet-projects-title">Проекты</h2></div><button type="button" className="cabinet-overview-text-link" onClick={() => onNavigate({ type: "projects" })}>Перейти к проектам <span aria-hidden="true">→</span></button></div>
        {!snapshot.projects && <p className="cabinet-overview-inline-empty">Список проектов сейчас недоступен.</p>}
        {snapshot.projects?.length === 0 && <div className="cabinet-overview-empty"><strong>В этом кабинете пока нет проектов</strong><span>Созданные проекты появятся здесь.</span></div>}
        {!!snapshot.projects?.length && <div className="cabinet-overview-project-grid">{snapshot.projects.map((project) => <button type="button" className="cabinet-overview-project-card" key={project.id} onClick={() => onNavigate({ type: "project", projectId: project.id })}>
          <span className="cabinet-overview-project-top"><span className={`cabinet-overview-status cabinet-overview-status--${statusTones[project.status] ?? "neutral"}`}>{statusLabels[project.status] ?? project.status}</span>{project.selected || snapshot.selection?.project_id === project.id ? <span className="cabinet-overview-selected">Выбран</span> : null}</span>
          <strong>{project.name}</strong>
          {typeof project.about === "string" && project.about.trim() && <span className="cabinet-overview-project-about">{project.about}</span>}
          {projectState(project) && <span className="cabinet-overview-runtime">Среда: {projectState(project)}</span>}
          <span className="cabinet-overview-card-arrow" aria-hidden="true">Открыть проект <span>→</span></span>
        </button>)}</div>}
      </section>

      <section className="cabinet-overview-section" aria-labelledby="cabinet-activity-title">
        <div className="cabinet-overview-section-heading"><div><p className="cabinet-overview-eyebrow">Только подтверждённые API-данные</p><h2 id="cabinet-activity-title">Активность и присутствие</h2></div></div>
        <div className="cabinet-overview-metrics">
          {employeesOnline !== undefined && employeesTotal !== undefined && <article className="cabinet-overview-metric"><span>Сотрудники онлайн</span><strong>{formatCount(employeesOnline)} <small>из {formatCount(employeesTotal)}</small></strong></article>}
          {tokensUsed !== undefined && <article className="cabinet-overview-metric"><span>Токены агента</span><strong>{formatCount(tokensUsed)}</strong></article>}
          {requests !== undefined && <article className="cabinet-overview-metric"><span>Запросы к агенту</span><strong>{formatCount(requests)}</strong></article>}
          {activity && <article className="cabinet-overview-metric cabinet-overview-metric--activity"><span>Последняя активность</span><strong>{activity}</strong></article>}
          {employeesOnline === undefined && employeesTotal === undefined && tokensUsed === undefined && requests === undefined && !activity && <div className="cabinet-overview-empty cabinet-overview-empty--compact"><strong>{snapshot.errors.metrics ? "Метрики недоступны" : "Пока нет данных об активности"}</strong><span>Показатели появятся, когда API предоставит подтверждённые значения.</span></div>}
        </div>
      </section>

      <NavigationSection title="Управление" description="Настройки и рабочие инструменты кабинета" placement="management" entries={management} onNavigate={onNavigate} unavailable={Boolean(snapshot.errors.modules)} />
      <NavigationSection title="Данные" description="Разделы с данными, доступные через подключённые модули" placement="data" entries={data} onNavigate={onNavigate} unavailable={Boolean(snapshot.errors.modules)} />
    </>}
  </main>;
}

function NavigationSection({ title, description, placement, entries, onNavigate, unavailable }: {
  title: string;
  description: string;
  placement: "management" | "data";
  entries: ModuleDestination[];
  onNavigate: CabinetOverviewProps["onNavigate"];
  unavailable: boolean;
}) {
  return <section className="cabinet-overview-section" aria-label={title}>
    <div className="cabinet-overview-section-heading"><div><p className="cabinet-overview-eyebrow">Разделы кабинета</p><h2>{title}</h2><p>{description}</p></div></div>
    {unavailable && entries.length === 0 && <p className="cabinet-overview-inline-empty">Навигация модулей сейчас недоступна.</p>}
    {!unavailable && entries.length === 0 && <div className="cabinet-overview-empty cabinet-overview-empty--compact"><strong>Нет подключённых разделов</strong><span>Этот раздел появится, когда подключённый модуль предоставит вкладку.</span></div>}
    {entries.length > 0 && <div className="cabinet-overview-nav-grid">{entries.map((entry) => <button key={`${entry.moduleId}:${entry.viewSlug}`} type="button" className="cabinet-overview-nav-card" onClick={() => onNavigate({ type: placement, projectId: entry.projectId, moduleId: entry.moduleId, viewSlug: entry.viewSlug, title: entry.title })}>
      <span className="cabinet-overview-nav-icon" aria-hidden="true">{placement === "management" ? "⚙" : "▤"}</span><span className="cabinet-overview-nav-copy"><strong>{entry.title}</strong><small>{entry.moduleName}</small></span><span className="cabinet-overview-card-arrow" aria-hidden="true">Открыть <span>→</span></span>
    </button>)}</div>}
  </section>;
}

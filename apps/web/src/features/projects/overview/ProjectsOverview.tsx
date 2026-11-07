import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { Cabinet, Id, ProjectLifecycle, ProjectSummary } from "../../../api/types";
import { useI18n } from "../../../i18n/useT";
import { formatDate } from "../../../i18n/format";
import {
  type ProjectOverviewAction,
  type ProjectOverviewService,
} from "./service";
import {
  filterProjects,
  favoriteStorageKey,
  observedStateLabel,
  persistFavorites,
  projectActionLabels,
  projectActions,
  projectOverviewHref,
  projectStatusLabels,
  projectStatusTone,
  projectWorkspaceLinks,
  readFavorites,
  lifecycleResultMatches,
  lifecycleTargetReached,
  type ProjectStatusFilter,
} from "./view-model";
import "./projects-overview.css";

export interface ProjectsOverviewProps {
  service: ProjectOverviewService;
  employeeId: Id;
  initialCabinetId?: Id;
}

type Notice = { tone: "success" | "warning" | "error"; message: string };
type PendingAction = { projectId: Id; action: ProjectOverviewAction | "select" };
type ProjectRowAction = ProjectOverviewAction | "confirm-completion" | "cancel-completion";

function browserStorage(): Storage | undefined {
  try { return typeof window === "undefined" ? undefined : window.localStorage; } catch { return undefined; }
}

function cabinetStorageKey(employeeId: Id): string {
  return `agentscale.projects.cabinet:${employeeId}`;
}

function readCabinetPreference(employeeId: Id): Id | undefined {
  try { return browserStorage()?.getItem(cabinetStorageKey(employeeId)) ?? undefined; } catch { return undefined; }
}

function persistCabinetPreference(employeeId: Id, cabinetId: Id): void {
  try { browserStorage()?.setItem(cabinetStorageKey(employeeId), cabinetId); } catch { /* cabinet selection still works for this page */ }
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : "Не удалось выполнить запрос. Попробуйте ещё раз.";
}

function projectHref(projectId: Id): string {
  return projectOverviewHref(projectId);
}

function statusTone(status: ProjectLifecycle): string {
  return projectStatusTone(status);
}

function ProjectRow({
  project,
  selected,
  favorite,
  pendingAction,
  confirmingCompletion,
  selecting,
  busy,
  onSelect,
  onToggleFavorite,
  onRunAction,
  onConfirmCompletion,
}: {
  project: ProjectSummary;
  selected: boolean;
  favorite: boolean;
  pendingAction?: ProjectOverviewAction | "select";
  confirmingCompletion: boolean;
  selecting: boolean;
  busy: boolean;
  onSelect: () => void;
  onToggleFavorite: () => void;
  onRunAction: (action: ProjectRowAction) => void;
  onConfirmCompletion: () => void;
}) {
  const { locale } = useI18n();
  const actions = projectActions(project.status, project.observed_state);
  const stateLabel = observedStateLabel(project.observed_state);
  const workspaceLinks = projectWorkspaceLinks(project.id);
  // The row used to force `ru-RU`, so an English screen still read Russian months.
  const updatedLabel = formatDate(project.updated_at, locale);
  return <article className={`projects-row${selected ? " projects-row--selected" : ""}`}>
    <button
      type="button"
      className="projects-favorite"
      aria-label={favorite ? `Убрать проект «${project.name}» из избранного` : `Добавить проект «${project.name}» в избранное`}
      aria-pressed={favorite}
      title={favorite ? "Убрать из избранного" : "В избранное"}
      onClick={onToggleFavorite}
    >{favorite ? "★" : "☆"}</button>
    <div className="projects-row-main">
      <div className="projects-row-title">
        <Link className="projects-name" to={projectHref(project.id)}>{project.name}</Link>
        {selected && <span className="projects-selected-label">Выбран</span>}
      </div>
      <div className="projects-row-meta">
        <span>{project.id}</span>
        {stateLabel && <span>{stateLabel}</span>}
        {updatedLabel && <span>Изменён {updatedLabel}</span>}
      </div>
      {project.about && <p className="projects-description">{project.about}</p>}
    </div>
    <div className="projects-row-status">
      <span className={`projects-status projects-status--${statusTone(project.status)}`}>{projectStatusLabels[project.status]}</span>
      {project.status === "error" && <span className="projects-attention">Требует внимания</span>}
    </div>
    <div className="projects-row-actions">
      {actions.filter((action) => action !== "complete").map((action) => <button
        type="button"
        className="projects-action"
        key={action}
        onClick={() => onRunAction(action)}
        disabled={busy}
      >{pendingAction === action ? "Выполняется…" : projectActionLabels[action]}</button>)}
      {actions.includes("complete") && (confirmingCompletion
        ? <span className="projects-complete-confirm"><span>Завершить проект?</span><button type="button" className="projects-action projects-action--danger" onClick={onConfirmCompletion} disabled={busy}>{pendingAction === "complete" ? "Выполняется…" : "Подтвердить"}</button><button type="button" className="projects-cancel" onClick={() => onRunAction("cancel-completion")} aria-label="Отменить завершение" disabled={busy}>Отмена</button></span>
        : <button type="button" className="projects-action projects-action--quiet" onClick={() => onRunAction("confirm-completion")} disabled={busy}>Завершить</button>)}
      <button type="button" className={`projects-select${selected ? " projects-select--active" : ""}`} onClick={onSelect} disabled={selected || busy} aria-pressed={selected}>{selected ? "Текущий проект" : selecting ? "Сохранение…" : "Выбрать"}</button>
    </div>
    <nav className="projects-row-workspace" aria-label={`Рабочая среда проекта ${project.name}`}>
      <Link to={workspaceLinks.runtime}>Среда</Link><Link to={workspaceLinks.chat}>Чат</Link><Link to={workspaceLinks.files}>Файлы</Link>
    </nav>
  </article>;
}

export function ProjectsOverview({ service, employeeId, initialCabinetId }: ProjectsOverviewProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  /**
   * `useSearchParams` возвращает новый `setSearchParams` на каждое изменение location
   * (его identity замемоизирована на текущем `searchParams`). Если положить его в deps
   * `loadCabinets`, то запись `?cabinet=`, которую делает сам загрузчик, меняет identity
   * загрузчика, mount-эффект перезапускается и делает второй, никем не запрошенный
   * round-trip кабинетов и проектов. Этот лишний запрос не только дублирует сеть: его
   * падение попадает в тот же catch и стирает уже успешно загруженные строки.
   * Держим сеттер в ref, чтобы загрузчики оставались стабильными.
   */
  const setSearchParamsRef = useRef(setSearchParams);
  setSearchParamsRef.current = setSearchParams;
  const requestedCabinetId = searchParams.get("cabinet") ?? initialCabinetId;
  const [cabinets, setCabinets] = useState<Cabinet[]>([]);
  const [cabinetId, setCabinetId] = useState<Id>(requestedCabinetId ?? "");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<Id>();
  const [isCabinetsLoading, setIsCabinetsLoading] = useState(true);
  const [isProjectsLoading, setIsProjectsLoading] = useState(false);
  const [cabinetError, setCabinetError] = useState<string>();
  const [projectError, setProjectError] = useState<string>();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<ProjectStatusFilter>("all");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(() => readFavorites(browserStorage(), favoriteStorageKey(employeeId)));
  const [showCreate, setShowCreate] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [pendingAction, setPendingAction] = useState<PendingAction>();
  const [confirmingCompletionId, setConfirmingCompletionId] = useState<Id>();
  const [notice, setNotice] = useState<Notice>();
  const requestNumber = useRef(0);
  const cabinetRequestNumber = useRef(0);
  const mutationNumber = useRef(0);
  const requestedCabinetRef = useRef(requestedCabinetId);
  const cabinetIdRef = useRef(cabinetId);
  const favoriteKey = favoriteStorageKey(employeeId);

  const refreshProjects = useCallback(async (targetCabinetId: Id, signal?: AbortSignal): Promise<{ projects: ProjectSummary[]; selectedProjectId?: Id } | undefined> => {
    const requestId = ++requestNumber.current;
    setIsProjectsLoading(true);
    setProjectError(undefined);
    setNotice(undefined);
    const [projectsResult, selectionResult] = await Promise.allSettled([
      service.listProjects(targetCabinetId, signal),
      service.getSelection(targetCabinetId, signal),
    ]);
    if (requestId !== requestNumber.current || signal?.aborted || cabinetIdRef.current !== targetCabinetId) return undefined;
    if (projectsResult.status === "rejected") {
      setProjects([]);
      setSelectedProjectId(undefined);
      setProjectError(readableError(projectsResult.reason));
    } else {
      setProjects(projectsResult.value);
      if (selectionResult.status === "fulfilled") {
        const selected = selectionResult.value;
        setSelectedProjectId(selected && projectsResult.value.some((project) => project.id === selected) ? selected : undefined);
      } else {
        setSelectedProjectId(undefined);
        setProjectError(`Список проектов загружен, но выбранный проект не открылся: ${readableError(selectionResult.reason)}`);
      }
    }
    setIsProjectsLoading(false);
    if (projectsResult.status !== "fulfilled" || selectionResult.status !== "fulfilled") return undefined;
    const selectedProjectId = selectionResult.value && projectsResult.value.some((project) => project.id === selectionResult.value)
      ? selectionResult.value
      : undefined;
    return { projects: projectsResult.value, selectedProjectId };
  }, [service]);

  const loadCabinets = useCallback(async (signal?: AbortSignal) => {
    const loadId = ++cabinetRequestNumber.current;
    setIsCabinetsLoading(true);
    setCabinetError(undefined);
    try {
      const result = await service.listCabinets(signal);
      if (signal?.aborted || loadId !== cabinetRequestNumber.current) return;
      setCabinets(result);
      const requested = requestedCabinetRef.current;
      const savedCabinetId = (requested && result.some((cabinet) => cabinet.id === requested)
        ? requested
        : readCabinetPreference(employeeId)) || cabinetIdRef.current;
      const nextCabinetId = result.some((cabinet) => cabinet.id === savedCabinetId) ? savedCabinetId : result[0]?.id ?? "";
      cabinetIdRef.current = nextCabinetId;
      setProjects([]);
      setSelectedProjectId(undefined);
      if (nextCabinetId) persistCabinetPreference(employeeId, nextCabinetId);
      setCabinetId(nextCabinetId);
      if (requested !== nextCabinetId) {
        requestedCabinetRef.current = nextCabinetId || undefined;
        setSearchParamsRef.current((current) => {
          const next = new URLSearchParams(current);
          if (nextCabinetId) next.set("cabinet", nextCabinetId);
          else next.delete("cabinet");
          return next;
        }, { replace: true });
      }
      if (nextCabinetId) await refreshProjects(nextCabinetId, signal);
      else {
        setIsProjectsLoading(false);
      }
    } catch (error) {
      if (signal?.aborted || loadId !== cabinetRequestNumber.current) return;
      cabinetIdRef.current = "";
      setCabinets([]);
      setCabinetId("");
      setProjects([]);
      setSelectedProjectId(undefined);
      setCabinetError(readableError(error));
      setIsProjectsLoading(false);
    } finally {
      if (!signal?.aborted && loadId === cabinetRequestNumber.current) setIsCabinetsLoading(false);
    }
  }, [employeeId, refreshProjects, service]);

  useEffect(() => {
    const controller = new AbortController();
    void loadCabinets(controller.signal);
    return () => {
      controller.abort();
      requestNumber.current += 1;
      cabinetRequestNumber.current += 1;
    };
  }, [loadCabinets]);

  useEffect(() => {
    setFavoriteIds(readFavorites(browserStorage(), favoriteKey));
  }, [favoriteKey]);

  const visibleProjects = useMemo(
    () => filterProjects(projects, search, statusFilter, favoritesOnly, favoriteIds),
    [favoriteIds, favoritesOnly, projects, search, statusFilter],
  );

  const changeCabinet = useCallback(async (nextCabinetId: Id) => {
    mutationNumber.current += 1;
    requestNumber.current += 1;
    // Инвалидируем незавершённый первичный `loadCabinets` (его результат относится к прежнему
    // кабинету). Его `finally` сверяет `loadId` с счётчиком и потому уже не сбросит флаг
    // загрузки — без явного сброса здесь экран навсегда остаётся в состоянии
    // «Загрузка кабинетов…», хотя список кабинетов уже загружен и переключение осмысленно.
    cabinetRequestNumber.current += 1;
    setIsCabinetsLoading(false);
    cabinetIdRef.current = nextCabinetId;
    requestedCabinetRef.current = nextCabinetId;
    persistCabinetPreference(employeeId, nextCabinetId);
    setCabinetId(nextCabinetId);
    setProjects([]);
    setSelectedProjectId(undefined);
    setConfirmingCompletionId(undefined);
    setIsCreating(false);
    setPendingAction(undefined);
    setCabinetError(undefined);
    setProjectError(undefined);
    setNotice(undefined);
    setSearchParamsRef.current((current) => {
      const next = new URLSearchParams(current);
      next.set("cabinet", nextCabinetId);
      return next;
    });
    await refreshProjects(nextCabinetId);
  }, [employeeId, refreshProjects]);

  useEffect(() => {
    requestedCabinetRef.current = requestedCabinetId;
    if (requestedCabinetId && cabinets.some((cabinet) => cabinet.id === requestedCabinetId) && requestedCabinetId !== cabinetIdRef.current) {
      void changeCabinet(requestedCabinetId);
    } else if (requestedCabinetId && cabinets.length > 0 && !cabinets.some((cabinet) => cabinet.id === requestedCabinetId)) {
      setSearchParamsRef.current((current) => {
        const next = new URLSearchParams(current);
        if (cabinetIdRef.current) next.set("cabinet", cabinetIdRef.current);
        else next.delete("cabinet");
        return next;
      }, { replace: true });
    }
  }, [cabinets, changeCabinet, requestedCabinetId]);

  async function selectProject(project: ProjectSummary) {
    const mutationId = ++mutationNumber.current;
    const targetCabinetId = project.cabinet_id;
    setProjectError(undefined);
    setNotice(undefined);
    setPendingAction({ projectId: project.id, action: "select" });
    try {
      await service.selectProject(targetCabinetId, project.id);
      if (mutationId !== mutationNumber.current || cabinetIdRef.current !== targetCabinetId) return;
      setSelectedProjectId(project.id);
      setNotice({ tone: "success", message: `«${project.name}» выбран текущим проектом.` });
    } catch (error) {
      if (mutationId === mutationNumber.current && cabinetIdRef.current === targetCabinetId) {
        setProjectError(`Не удалось выбрать проект: ${readableError(error)}`);
      }
    } finally {
      if (mutationId === mutationNumber.current) setPendingAction(undefined);
    }
  }

  function toggleFavorite(projectId: Id) {
    setFavoriteIds((current) => {
      const next = new Set(current);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      persistFavorites(browserStorage(), favoriteKey, next);
      return next;
    });
  }

  async function runAction(project: ProjectSummary, action: ProjectOverviewAction) {
    if (action === "complete") {
      setConfirmingCompletionId(undefined);
    }
    const mutationId = ++mutationNumber.current;
    const targetCabinetId = project.cabinet_id;
    setProjectError(undefined);
    setNotice(undefined);
    setPendingAction({ projectId: project.id, action });
    try {
      const actionResult = await service.lifecycle(project.id, action);
      if (actionResult.id !== project.id || actionResult.cabinet_id !== targetCabinetId) {
        throw new Error("Результат относится к другому проекту или кабинету — обновите список.");
      }
      const routeResultMatchesAction = action === "resume"
        && (actionResult.status === "paused" || actionResult.status === "completed")
        ? true
        : lifecycleResultMatches(action, actionResult);
      if (mutationId !== mutationNumber.current || cabinetIdRef.current !== targetCabinetId) return;
      const refreshed = await refreshProjects(targetCabinetId);
      if (mutationId !== mutationNumber.current || cabinetIdRef.current !== targetCabinetId || !refreshed) return;
      const listed = refreshed.projects.find((item) => item.id === project.id);
      const readback = await service.getProject(project.id);
      if (mutationId !== mutationNumber.current || cabinetIdRef.current !== targetCabinetId) return;
      if (!listed || readback.id !== project.id || readback.cabinet_id !== targetCabinetId
        || listed.status !== actionResult.status || readback.status !== actionResult.status
        || !routeResultMatchesAction || !lifecycleResultMatches(action, readback)) {
        setProjectError("Действие принято, но повторное чтение списка и проекта не подтверждает одинаковый статус. Обновите список перед следующей операцией.");
        return;
      }
      const targetReached = lifecycleTargetReached(action, readback.status);
      setNotice({ tone: targetReached ? "success" : "warning", message: targetReached
        ? `«${projectActionLabels[action]}» выполнено. Статус проекта: «${projectStatusLabels[readback.status]}». Среда запускается отдельно и может отставать.`
        : `Команда «${projectActionLabels[action]}» принята — статус ещё меняется: «${projectStatusLabels[readback.status]}». Среда запускается отдельно не считается готовым.` });
    } catch (error) {
      if (mutationId === mutationNumber.current && cabinetIdRef.current === targetCabinetId) {
        setProjectError(`Не удалось выполнить действие «${projectActionLabels[action].toLocaleLowerCase()}»: ${readableError(error)}`);
      }
    } finally {
      if (mutationId === mutationNumber.current) setPendingAction(undefined);
    }
  }

  async function createProject(event: FormEvent) {
    event.preventDefault();
    if (!cabinetId || !projectName.trim()) return;
    const submittedName = projectName.trim();
    setIsCreating(true);
    const mutationId = ++mutationNumber.current;
    const targetCabinetId = cabinetId;
    setProjectError(undefined);
    setNotice(undefined);
    let creationAccepted = false;
    try {
      const created = await service.createProject(targetCabinetId, submittedName);
      creationAccepted = true;
      if (mutationId !== mutationNumber.current || cabinetIdRef.current !== targetCabinetId) return;
      if (created.cabinet_id !== targetCabinetId || created.name !== submittedName || created.status !== "draft") {
        throw new Error("Ответ создания не совпадает с ожидаемым названием или статусом «Черновик».");
      }
      const [createdList, createdReadback] = await Promise.all([
        service.listProjects(targetCabinetId),
        service.getProject(created.id),
      ]);
      const listedCreated = createdList.find((project) => project.id === created.id);
      if (mutationId !== mutationNumber.current || cabinetIdRef.current !== targetCabinetId) return;
      if (!listedCreated || listedCreated.cabinet_id !== targetCabinetId || listedCreated.name !== submittedName
        || listedCreated.status !== created.status || createdReadback.id !== created.id
        || createdReadback.cabinet_id !== targetCabinetId || createdReadback.name !== submittedName
        || createdReadback.status !== created.status) {
        throw new Error("Повторное чтение списка и проекта не подтвердило создание.");
      }
      const selected = await service.selectProject(targetCabinetId, created.id);
      if (selected !== created.id) throw new Error("Выбранный проект не подтверждён повторным чтением.");
      const refreshed = await refreshProjects(targetCabinetId);
      if (mutationId !== mutationNumber.current || cabinetIdRef.current !== targetCabinetId) return;
      if (!refreshed) {
        setProjectError("Проект создан, но обновлённый список не удалось подтвердить. Повторите обновление.");
        return;
      }
      const listed = refreshed.projects.find((project) => project.id === created.id);
      if (!listed || listed.cabinet_id !== targetCabinetId || listed.name !== submittedName
        || listed.status !== created.status
        || refreshed.selectedProjectId !== created.id) {
        setProjectError("Повторное чтение списка, проекта или выбранного проекта не совпало. Успех создания не подтверждён.");
        return;
      }
      const verified = await service.getProject(created.id);
      if (mutationId !== mutationNumber.current || cabinetIdRef.current !== targetCabinetId) return;
      if (verified.id !== created.id || verified.cabinet_id !== targetCabinetId
        || verified.name !== listed.name || verified.status !== listed.status) {
        setProjectError("Повторное чтение project GET не совпало со списком. Успех создания не подтверждён.");
        return;
      }
      if (mutationId !== mutationNumber.current || cabinetIdRef.current !== targetCabinetId) return;
      setProjectName("");
      setSearch("");
      setStatusFilter("all");
      setFavoritesOnly(false);
      setShowCreate(false);
      setNotice({ tone: "success", message: `Проект «${created.name}» создан и выбран. Статус «${projectStatusLabels[verified.status]}» подтверждён повторным чтением.` });
    } catch (error) {
      if (mutationId === mutationNumber.current && cabinetIdRef.current === targetCabinetId) {
        setProjectError(`${creationAccepted ? "Проект создан, но его состояние не удалось подтвердить повторным чтением" : "Не удалось создать проект"}: ${readableError(error)}`);
      }
    } finally {
      if (mutationId === mutationNumber.current) setIsCreating(false);
    }
  }

  const selectedCabinet = cabinets.find((cabinet) => cabinet.id === cabinetId);
  const activeCount = projects.filter((project) => project.status === "active").length;
  const attentionCount = projects.filter((project) => project.status === "error").length;

  return <main className="projects-overview" aria-busy={isCabinetsLoading || isProjectsLoading}>
    <header className="projects-overview-header">
      <div><p className="projects-eyebrow">Рабочее пространство</p><h1>Проекты</h1><p className="projects-subtitle">Проекты кабинета и состояние их рабочих сред.</p></div>
      <div className="projects-header-controls">
        <label className="projects-cabinet-picker">Кабинет
          <select value={cabinetId} onChange={(event) => void changeCabinet(event.target.value)} disabled={isCabinetsLoading || cabinets.length === 0 || isCreating || pendingAction !== undefined} aria-label="Выбрать кабинет">
            {cabinets.length === 0 && <option value="">Кабинеты не найдены</option>}
            {cabinets.map((cabinet) => <option key={cabinet.id} value={cabinet.id}>{cabinet.name}</option>)}
          </select>
        </label>
        {cabinetId && <button type="button" className="projects-primary" onClick={() => { setShowCreate((value) => !value); setProjectError(undefined); }} disabled={isCreating || isProjectsLoading || pendingAction !== undefined} aria-expanded={showCreate}>{showCreate ? "Закрыть" : "＋ Новый проект"}</button>}
      </div>
    </header>

    {cabinetError && <section className="projects-feedback projects-feedback--error" role="alert"><div><strong>Не удалось загрузить кабинеты</strong><span>{cabinetError}</span></div><button type="button" className="projects-action" onClick={() => void loadCabinets()}>Повторить</button></section>}
    {!isCabinetsLoading && !cabinetError && cabinets.length === 0 && <section className="projects-empty"><strong>Нет доступных кабинетов</strong><span>Для сотрудника пока не назначено ни одного кабинета.</span></section>}
    {isCabinetsLoading && <section className="projects-loading" role="status">Загрузка кабинетов…</section>}

    {!isCabinetsLoading && !cabinetError && selectedCabinet && <>
      {showCreate && <form className="projects-create-form" onSubmit={createProject}>
        <div><p className="projects-eyebrow">Новый проект · {selectedCabinet.name}</p><h2>Создать проект</h2><p>Проект появится в статусе «Черновик». Рабочая среда создаётся отдельно при запуске.</p></div>
        <label>Название проекта<input value={projectName} onChange={(event) => setProjectName(event.target.value)} autoFocus maxLength={200} required placeholder="Например, Анализ рынка" /></label>
        <div className="projects-form-actions"><span>{projectName.trim().length}/200</span><button type="submit" className="projects-primary" disabled={isCreating || pendingAction !== undefined || isProjectsLoading || !projectName.trim()}>{isCreating ? "Создание…" : "Создать"}</button></div>
      </form>}

      <section className="projects-summary" aria-label="Сводка проектов">
        <div><span>Всего</span><strong>{projects.length}</strong></div>
        <div><span>Активны</span><strong>{activeCount}</strong></div>
        <div><span>Требуют внимания</span><strong>{attentionCount}</strong></div>
        <div className="projects-summary-context"><span>Кабинет</span><strong>{selectedCabinet.name}</strong></div>
      </section>

      {projectError && <section className="projects-feedback projects-feedback--error" role="alert"><div><strong>Операция не завершена</strong><span>{projectError}</span></div><button type="button" className="projects-action" onClick={() => cabinetId && void refreshProjects(cabinetId)} disabled={isProjectsLoading || pendingAction !== undefined}>{isProjectsLoading ? "Обновление…" : "Обновить список"}</button></section>}
      {notice && <p className={`projects-feedback projects-feedback--${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>{notice.message}</p>}

      <section className="projects-list-section" aria-label="Список проектов">
        <div className="projects-toolbar">
          <label className="projects-search"><span aria-hidden="true">⌕</span><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск по названию или описанию" aria-label="Поиск проектов" /></label>
          <label className="projects-status-filter">Статус<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as ProjectStatusFilter)} aria-label="Фильтр по статусу"><option value="all">Все статусы</option>{Object.entries(projectStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="projects-favorites-filter"><input type="checkbox" checked={favoritesOnly} onChange={(event) => setFavoritesOnly(event.target.checked)} />Избранные</label>
          <span className="projects-result-count">{visibleProjects.length} из {projects.length}</span>
        </div>

        {isProjectsLoading && projects.length > 0 && <span className="projects-refreshing" role="status">Обновление списка…</span>}
        {isProjectsLoading && projects.length === 0 && <div className="projects-loading" role="status">Загрузка проектов кабинета…</div>}
        {!isProjectsLoading && !projectError && projects.length === 0 && <div className="projects-empty"><strong>В кабинете пока нет проектов</strong><span>Создайте проект, чтобы подготовить отдельную рабочую среду агента.</span>{!showCreate && <button type="button" className="projects-text-button" onClick={() => setShowCreate(true)}>Создать первый проект</button>}</div>}
        {projects.length > 0 && visibleProjects.length === 0 && <div className="projects-empty"><strong>Подходящих проектов нет</strong><span>Измените запрос или фильтры, чтобы увидеть проекты кабинета.</span><button type="button" className="projects-text-button" onClick={() => { setSearch(""); setStatusFilter("all"); setFavoritesOnly(false); }}>Сбросить фильтры</button></div>}
        {visibleProjects.length > 0 && <div className="projects-list" role="list">{visibleProjects.map((project) => {
          return <div role="listitem" key={project.id}><ProjectRow
            project={project}
            selected={selectedProjectId === project.id}
            favorite={favoriteIds.has(project.id)}
            pendingAction={pendingAction?.projectId === project.id ? pendingAction.action : undefined}
            confirmingCompletion={confirmingCompletionId === project.id}
            selecting={pendingAction?.projectId === project.id && pendingAction.action === "select"}
            busy={pendingAction !== undefined || isProjectsLoading || isCreating}
            onSelect={() => void selectProject(project)}
            onToggleFavorite={() => toggleFavorite(project.id)}
            onRunAction={(action) => {
              if (action === "confirm-completion") setConfirmingCompletionId(project.id);
              else if (action === "cancel-completion") setConfirmingCompletionId(undefined);
              else void runAction(project, action);
            }}
            onConfirmCompletion={() => void runAction(project, "complete")}
          /></div>;
        })}</div>}
      </section>
    </>}
  </main>;
}

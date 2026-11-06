import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { ProjectControlAction, ProjectControlsPatch, ProjectControlsProps, ProjectControlsProject } from "./types";
import { patchProjectWithReadback, runProjectActionWithReadback } from "./operations";
import {
  formatProjectUpdatedAt,
  lifecycleCapabilities,
  projectBudgetInputValue,
  projectControlActionLabels,
  projectControlStatusLabels,
  projectControlsLinks,
} from "./view-model";
import "./project-controls.css";

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : "Операция не выполнена. Повторите попытку.";
}

function statusTone(status: string): string {
  if (status === "active") return "positive";
  if (status === "paused") return "warning";
  if (status === "error") return "danger";
  if (status === "completed" || status === "deleted") return "muted";
  return "neutral";
}

function budgetValue(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : Number.NaN;
}

function changedPatch(project: ProjectControlsProject, name: string, about: string, budgetTokens: string): ProjectControlsPatch {
  const patch: ProjectControlsPatch = {};
  const normalizedName = name.trim();
  if (normalizedName !== project.name) patch.name = normalizedName;
  const normalizedAbout = about.trim();
  if ((project.about ?? "") !== normalizedAbout) patch.about = normalizedAbout || null;
  const nextBudget = budgetValue(budgetTokens);
  if (!Number.isNaN(nextBudget) && nextBudget !== (project.budget_tokens ?? null)) patch.budget_tokens = nextBudget;
  return patch;
}

export function ProjectControlsPanel({ projectId, service, initialProject, links, onProjectChange }: ProjectControlsProps) {
  const matchingInitialProject = initialProject?.id === projectId ? initialProject : undefined;
  const hasInitialProject = matchingInitialProject !== undefined;
  const [project, setProject] = useState<ProjectControlsProject | undefined>(matchingInitialProject);
  const [loadedProjectId, setLoadedProjectId] = useState(matchingInitialProject?.id);
  const [loading, setLoading] = useState(!matchingInitialProject);
  const [loadError, setLoadError] = useState<string>();
  const [notice, setNotice] = useState<{ tone: "pending" | "success" | "warning" | "error"; message: string }>();
  const [pendingAction, setPendingAction] = useState<ProjectControlAction>();
  const [saving, setSaving] = useState(false);
  const [confirmingComplete, setConfirmingComplete] = useState(false);
  const [name, setName] = useState(matchingInitialProject?.name ?? "");
  const [about, setAbout] = useState(matchingInitialProject?.about ?? "");
  const [budgetTokens, setBudgetTokens] = useState(() => projectBudgetInputValue(matchingInitialProject?.budget_tokens));
  const loadSequence = useRef(0);
  const mutationSequence = useRef(0);
  const currentProject = loadedProjectId === projectId && project?.id === projectId ? project : undefined;
  const capabilities = useMemo(() => currentProject ? lifecycleCapabilities(currentProject) : [], [currentProject]);
  const hrefs = useMemo(() => projectControlsLinks(projectId, links), [links, projectId]);

  const acceptProject = useCallback((next: ProjectControlsProject) => {
    setProject(next);
    setLoadedProjectId(next.id);
    setName(next.name);
    setAbout(next.about ?? "");
    setBudgetTokens(projectBudgetInputValue(next.budget_tokens));
    onProjectChange?.(next);
  }, [onProjectChange]);

  const refresh = useCallback(async (quiet = false) => {
    const sequence = ++loadSequence.current;
    if (!quiet) setLoading(true);
    setLoadError(undefined);
    try {
      const latest = await service.getProject(projectId);
      if (sequence !== loadSequence.current) return;
      if (latest.id !== projectId) throw new Error("Backend вернул другой проект.");
      acceptProject(latest);
    } catch (error) {
      if (sequence === loadSequence.current) setLoadError(readableError(error));
    } finally {
      if (sequence === loadSequence.current && !quiet) setLoading(false);
    }
  }, [acceptProject, projectId, service]);

  useEffect(() => {
    setPendingAction(undefined);
    setSaving(false);
    void refresh(hasInitialProject);
    return () => {
      loadSequence.current += 1;
      mutationSequence.current += 1;
    };
  }, [hasInitialProject, projectId, refresh]);

  async function runAction(action: ProjectControlAction) {
    if (!currentProject || pendingAction || saving) return;
    if (action === "complete" && !confirmingComplete) {
      setConfirmingComplete(true);
      return;
    }
    setConfirmingComplete(false);
    const mutationId = ++mutationSequence.current;
    setNotice({ tone: "pending", message: `Выполняется «${projectControlActionLabels[action].toLocaleLowerCase()}». Ожидаем подтверждение backend.` });
    setPendingAction(action);
    try {
      const result = await runProjectActionWithReadback(service, projectId, action, (updated) => {
        if (mutationId !== mutationSequence.current || updated.id !== projectId) return;
        acceptProject(updated);
        setNotice({ tone: "pending", message: `Backend вернул результат «${projectControlActionLabels[action].toLocaleLowerCase()}». Проверяем project GET.` });
      });
      if (mutationId !== mutationSequence.current) return;
      if (result.project?.id === projectId) acceptProject(result.project);
      setNotice(result.readbackVerified && result.project
        ? result.targetReached === false
          ? { tone: "warning", message: `Команда «${projectControlActionLabels[action]}» принята и сверена с project GET, но целевое состояние ещё не достигнуто. Текущий статус: «${projectControlStatusLabels[result.project.status] ?? result.project.status}». Pod readiness не подтверждается этой командой.` }
          : { tone: "success", message: `«${projectControlActionLabels[action]}» и статус «${projectControlStatusLabels[result.project.status] ?? result.project.status}» подтверждены route и project GET. Pod readiness не предполагается.` }
        : result.mutationAccepted
          ? { tone: "warning", message: result.project
            ? `Операция принята, но не удалось подтвердить актуальное состояние: ${readableError(result.readbackError)}. Обновите данные перед следующей операцией.`
            : `Операция принята, но backend не подтвердил актуальный проект: ${readableError(result.readbackError)}. Обновите данные перед следующей операцией.` }
          : { tone: "error", message: "Backend не подтвердил выполнение операции." });
    } catch (error) {
      if (mutationId === mutationSequence.current) {
        setNotice({ tone: "error", message: `Не удалось выполнить «${projectControlActionLabels[action].toLocaleLowerCase()}»: ${readableError(error)}` });
      }
    } finally {
      if (mutationId === mutationSequence.current) setPendingAction(undefined);
    }
  }

  async function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!currentProject || saving || pendingAction) return;
    const patch = changedPatch(currentProject, name, about, budgetTokens);
    if (Object.keys(patch).length === 0) return;
    if ("name" in patch && (!patch.name || patch.name.length > 200)) {
      setNotice({ tone: "error", message: "Введите название длиной от 1 до 200 символов." });
      return;
    }
    if ("about" in patch && (patch.about?.length ?? 0) > 8000) {
      setNotice({ tone: "error", message: "Описание не должно превышать 8000 символов." });
      return;
    }
    if ("budget_tokens" in patch && patch.budget_tokens !== null && !Number.isSafeInteger(patch.budget_tokens)) {
      setNotice({ tone: "error", message: "Бюджет должен быть целым неотрицательным числом." });
      return;
    }

    if (budgetTokens !== "" && (!Number.isSafeInteger(budgetParsed) || Number.isNaN(budgetParsed))) {
      setNotice({ tone: "error", message: "Бюджет должен быть целым неотрицательным числом." });
      return;
    }

    const previousProject = currentProject;
    const mutationId = ++mutationSequence.current;
    const optimisticProject = { ...currentProject, ...patch } as ProjectControlsProject;
    acceptProject(optimisticProject);
    setSaving(true);
    setNotice({ tone: "pending", message: "Изменения применены локально. Сохраняем проект в backend." });
    try {
      const result = await patchProjectWithReadback(service, projectId, patch, (updated) => {
        if (mutationId !== mutationSequence.current || updated.id !== projectId) return;
        acceptProject(updated);
        setNotice({ tone: "pending", message: "Изменения приняты backend. Проверяем сохранённые данные повторным чтением." });
      });
      if (mutationId !== mutationSequence.current) return;
      if (result.project?.id === projectId) acceptProject(result.project);
      setNotice(result.readbackVerified && result.project
        ? { tone: "success", message: "Настройки сохранены и подтверждены повторным чтением проекта." }
        : { tone: "warning", message: result.project
          ? `Настройки приняты, но не удалось проверить сохранённые данные: ${readableError(result.readbackError)}. Обновите проект перед дальнейшими изменениями.`
          : `Изменения отправлены, но ответ backend и повторное чтение не подтвердили проект: ${readableError(result.readbackError)}. Локальные значения показаны как неподтверждённые.` });
    } catch (error) {
      if (mutationId === mutationSequence.current) {
        acceptProject(previousProject);
        setNotice({ tone: "error", message: `Сохранение отклонено, локальные изменения отменены: ${readableError(error)}` });
      }
    } finally {
      if (mutationId === mutationSequence.current) setSaving(false);
    }
  }

  if (!currentProject) {
    const projectIsLoading = loading || loadedProjectId !== projectId;
    return <main className="project-controls" aria-busy={projectIsLoading}>
      {projectIsLoading && <div className="project-controls__loading" role="status">Загружаем настройки проекта…</div>}
      {!projectIsLoading && <section className="project-controls__feedback project-controls__feedback--error" role="alert"><div><strong>Не удалось открыть настройки проекта</strong><span>{loadError ?? "Проект не найден."}</span></div><button type="button" className="project-controls__button" onClick={() => void refresh()}>Повторить</button></section>}
    </main>;
  }

  const budgetParsed = budgetValue(budgetTokens);
  const patch = changedPatch(currentProject, name, about, budgetTokens);
  const busy = saving || pendingAction !== undefined;
  const statusLabel = projectControlStatusLabels[currentProject.status] ?? currentProject.status;
  const runtimeState = currentProject.runtime?.observed_state ?? currentProject.observed_state;
  const updatedAt = formatProjectUpdatedAt(currentProject.updated_at);

  return <main className="project-controls" aria-busy={busy || loading}>
    <header className="project-controls__header">
      <div className="project-controls__title">
        <p className="project-controls__eyebrow">Project controls</p>
        <h1>{currentProject.name}</h1>
        <p className="project-controls__description">Состояние проекта, lifecycle-действия и основные параметры workspace.</p>
      </div>
      <div className="project-controls__state">
        <span className={`project-controls__status project-controls__status--${statusTone(currentProject.status)}`}><span aria-hidden="true" />{statusLabel}</span>
        <span className="project-controls__project-id">{currentProject.id}</span>
      </div>
    </header>

    {notice && <div className={`project-controls__feedback project-controls__feedback--${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>
      <span>{notice.message}</span>
      {notice.tone === "warning" && <button type="button" className="project-controls__text-button" onClick={() => void refresh()}>Обновить данные</button>}
    </div>}
    {loadError && <div className="project-controls__feedback project-controls__feedback--error" role="alert"><span>Не удалось обновить проект: {loadError}</span><button type="button" className="project-controls__text-button" onClick={() => void refresh()}>Повторить</button></div>}

    <section className="project-controls__summary" aria-label="Сводка проекта">
      <div className="project-controls__summary-item"><span>Наблюдаемое состояние</span><strong>{runtimeState || "Не сообщено"}</strong></div>
      <div className="project-controls__summary-item"><span>Рабочая среда</span><strong>{currentProject.runtime?.runtime_ref ?? currentProject.container_ref ?? "Не привязана"}</strong></div>
      <div className="project-controls__summary-item"><span>Последнее изменение</span><strong>{updatedAt}</strong></div>
    </section>

    <section className="project-controls__section" aria-labelledby="project-controls-lifecycle-title">
      <div className="project-controls__section-heading"><div><p className="project-controls__eyebrow">Lifecycle</p><h2 id="project-controls-lifecycle-title">Управление проектом</h2></div><span className="project-controls__section-note">Доступные команды зависят от состояния проекта</span></div>
      <div className="project-controls__actions">
        {capabilities.map(({ action, enabled, reason }) => <button
          key={action}
          type="button"
          className={`project-controls__button${action === "launch" ? " project-controls__button--primary" : ""}${action === "complete" ? " project-controls__button--danger" : ""}`}
          disabled={!enabled || busy || loading}
          title={!enabled ? reason : undefined}
          aria-label={`${projectControlActionLabels[action]}${!enabled && reason ? `. ${reason}` : ""}`}
          onClick={() => void runAction(action)}
        >{pendingAction === action ? "Выполняется…" : projectControlActionLabels[action]}</button>)}
      </div>
      {confirmingComplete && <div className="project-controls__confirmation" role="alertdialog" aria-label="Подтвердить завершение проекта">
        <div><strong>Завершить проект «{currentProject.name}»?</strong><span>Рабочая среда будет остановлена, а проект перейдёт в статус «Завершён».</span></div>
        <button type="button" className="project-controls__button project-controls__button--danger" disabled={busy} onClick={() => void runAction("complete")}>Подтвердить завершение</button>
        <button type="button" className="project-controls__button" disabled={busy} onClick={() => setConfirmingComplete(false)}>Отмена</button>
      </div>}
      <p className="project-controls__runtime-hint">{currentProject.runtime?.last_error && <span className="project-controls__runtime-error">{currentProject.runtime.last_error} </span>}Статус сверяется с project GET; состояние Pod проверяется отдельно.</p>
    </section>

    <section className="project-controls__section" aria-labelledby="project-controls-settings-title">
      <div className="project-controls__section-heading"><div><p className="project-controls__eyebrow">Configuration</p><h2 id="project-controls-settings-title">Параметры проекта</h2></div><span className="project-controls__section-note">Изменения сохраняются в backend</span></div>
      <form className="project-controls__form" onSubmit={(event) => void saveSettings(event)}>
        <label className="project-controls__field project-controls__field--name">Название
          <input name="name" type="text" value={name} onChange={(event) => setName(event.target.value)} minLength={1} maxLength={200} required disabled={busy} />
          <small>{name.trim().length}/200</small>
        </label>
        <label className="project-controls__field project-controls__field--budget">Лимит бюджета, токены
          <input name="budget_tokens" type="number" inputMode="numeric" min={0} step={1} value={budgetTokens} placeholder="Без ограничения" onChange={(event) => setBudgetTokens(event.target.value)} disabled={busy} aria-invalid={budgetTokens !== "" && (Number.isNaN(budgetParsed) || !Number.isSafeInteger(budgetParsed))} />
          <small>{budgetTokens === "" ? "Пустое значение снимает лимит." : "Укажите целое число от 0."}</small>
        </label>
        <label className="project-controls__field project-controls__field--description">Описание
          <textarea name="about" rows={5} value={about} onChange={(event) => setAbout(event.target.value)} maxLength={8000} placeholder="Кратко опишите назначение проекта" disabled={busy} />
          <small>{about.length}/8000</small>
        </label>
        <div className="project-controls__form-footer"><span>Поддерживается повторное чтение после сохранения.</span><button type="submit" className="project-controls__button project-controls__button--primary" disabled={busy || loading || Object.keys(patch).length === 0 || !name.trim() || name.trim().length > 200 || (budgetTokens !== "" && (!Number.isSafeInteger(budgetParsed) || Number.isNaN(budgetParsed)))}>{saving ? "Сохраняем…" : "Сохранить изменения"}</button></div>
      </form>
    </section>

    <nav className="project-controls__links" aria-label="Разделы проекта">
      <a href={hrefs.runtime}><span aria-hidden="true">01</span><span><strong>Runtime</strong><small>Состояние рабочей среды</small></span><span aria-hidden="true">↗</span></a>
      <a href={hrefs.chat}><span aria-hidden="true">02</span><span><strong>Chat</strong><small>Диалоги и сессии агента</small></span><span aria-hidden="true">↗</span></a>
      <a href={hrefs.files}><span aria-hidden="true">03</span><span><strong>Files</strong><small>Файлы workspace проекта</small></span><span aria-hidden="true">↗</span></a>
      <a href={hrefs.models}><span aria-hidden="true">04</span><span><strong>Models</strong><small>Провайдеры и модели</small></span><span aria-hidden="true">↗</span></a>
    </nav>
  </main>;
}

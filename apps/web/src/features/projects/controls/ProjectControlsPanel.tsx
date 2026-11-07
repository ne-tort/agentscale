import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { ProjectControlAction, ProjectControlsPatch, ProjectControlsProps, ProjectControlsProject } from "./types";
import { patchProjectWithReadback, runProjectActionWithReadback } from "./operations";
import {
  formatProjectUpdatedAt,
  lifecycleCapabilities,
  projectBudgetInputValue,
  projectControlActionLabel,
  projectControlsLinks,
  projectStateLabel,
} from "./view-model";
import { useI18n } from "../../../i18n/useT";
import type { Translate } from "../../../i18n/catalogue";
import "./project-controls.css";

function readableError(t: Translate, error: unknown): string {
  return error instanceof Error ? error.message : t("projects.errorUnknown");
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
  const { locale, t } = useI18n();
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
  // Reloading the project must not depend on the active language: switching it
  // would otherwise refetch the screen the user is reading.
  const translateRef = useRef(t);
  translateRef.current = t;
  const currentProject = loadedProjectId === projectId && project?.id === projectId ? project : undefined;
  const capabilities = useMemo(() => currentProject ? lifecycleCapabilities(currentProject, t) : [], [currentProject, t]);
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
      if (latest.id !== projectId) throw new Error(translateRef.current("projects.errorWrongProject"));
      acceptProject(latest);
    } catch (error) {
      if (sequence === loadSequence.current) setLoadError(readableError(translateRef.current, error));
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
    const actionName = projectControlActionLabel(t, action);
    const mutationId = ++mutationSequence.current;
    setNotice({ tone: "pending", message: t("projects.noticePending", { action: actionName }) });
    setPendingAction(action);
    try {
      const result = await runProjectActionWithReadback(service, projectId, action, (updated) => {
        if (mutationId !== mutationSequence.current || updated.id !== projectId) return;
        acceptProject(updated);
        setNotice({ tone: "pending", message: t("projects.noticeAccepted", { action: actionName }) });
      });
      if (mutationId !== mutationSequence.current) return;
      if (result.project?.id === projectId) acceptProject(result.project);
      setNotice(result.readbackVerified && result.project
        ? result.targetReached === false
          ? { tone: "warning", message: t("projects.noticePendingTarget", { action: actionName, state: projectStateLabel(t, result.project.status) ?? t("projects.stateUnrecognised") }) }
          : { tone: "success", message: t("projects.noticeDone", { action: actionName, state: projectStateLabel(t, result.project.status) ?? t("projects.stateUnrecognised") }) }
        : result.mutationAccepted
          ? { tone: "warning", message: t("projects.noticeUnverified", { error: readableError(t, result.readbackError) }) }
          : { tone: "error", message: t("projects.noticeRejected") });
    } catch (error) {
      if (mutationId === mutationSequence.current) {
        setNotice({ tone: "error", message: t("projects.noticeFailed", { action: actionName, error: readableError(t, error) }) });
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
      setNotice({ tone: "error", message: t("projects.validationName") });
      return;
    }
    if ("about" in patch && (patch.about?.length ?? 0) > 8000) {
      setNotice({ tone: "error", message: t("projects.validationAbout") });
      return;
    }
    if ("budget_tokens" in patch && patch.budget_tokens !== null && !Number.isSafeInteger(patch.budget_tokens)) {
      setNotice({ tone: "error", message: t("projects.validationBudget") });
      return;
    }

    const typedBudget = budgetValue(budgetTokens);
    if (typedBudget !== null && (Number.isNaN(typedBudget) || !Number.isSafeInteger(typedBudget))) {
      setNotice({ tone: "error", message: t("projects.validationBudget") });
      return;
    }

    const previousProject = currentProject;
    const mutationId = ++mutationSequence.current;
    const optimisticProject = { ...currentProject, ...patch } as ProjectControlsProject;
    acceptProject(optimisticProject);
    setSaving(true);
    setNotice({ tone: "pending", message: t("projects.noticeSaving") });
    try {
      const result = await patchProjectWithReadback(service, projectId, patch, (updated) => {
        if (mutationId !== mutationSequence.current || updated.id !== projectId) return;
        acceptProject(updated);
      });
      if (mutationId !== mutationSequence.current) return;
      if (result.project?.id === projectId) acceptProject(result.project);
      setNotice(result.readbackVerified && result.project
        ? { tone: "success", message: t("projects.noticeSaved") }
        : { tone: "warning", message: t("projects.noticeSaveUnverified", { error: readableError(t, result.readbackError) }) });
    } catch (error) {
      if (mutationId === mutationSequence.current) {
        acceptProject(previousProject);
        setNotice({ tone: "error", message: t("projects.noticeSaveFailed", { error: readableError(t, error) }) });
      }
    } finally {
      if (mutationId === mutationSequence.current) setSaving(false);
    }
  }

  if (!currentProject) {
    const projectIsLoading = loading || loadedProjectId !== projectId;
    return <main className="project-controls" aria-busy={projectIsLoading}>
      {projectIsLoading && <div className="project-controls__loading" role="status">{t("projects.loading")}</div>}
      {!projectIsLoading && <section className="project-controls__feedback project-controls__feedback--error" role="alert"><div><strong>{t("projects.loadFailedTitle")}</strong><span>{loadError ?? t("projects.loadFailedFallback")}</span></div><button type="button" className="project-controls__button" onClick={() => void refresh()}>{t("common.retry")}</button></section>}
    </main>;
  }

  const typedBudget = budgetValue(budgetTokens);
  const budgetIsInvalid = budgetTokens !== "" && (typedBudget === null || Number.isNaN(typedBudget) || !Number.isSafeInteger(typedBudget));
  const patch = changedPatch(currentProject, name, about, budgetTokens);
  const busy = saving || pendingAction !== undefined;
  const runtimeState = currentProject.runtime?.observed_state ?? currentProject.observed_state;
  const stateLabel = projectStateLabel(t, runtimeState)
    ?? projectStateLabel(t, currentProject.status)
    ?? t("projects.stateUnrecognised");
  const environmentRef = currentProject.runtime?.runtime_ref ?? currentProject.container_ref;
  const updatedAt = formatProjectUpdatedAt(currentProject.updated_at, locale) ?? t("projects.updatedNever");
  const anyActionEnabled = capabilities.some((capability) => capability.enabled);

  return <main className="project-controls" aria-label={t("projects.title")} aria-busy={busy || loading}>
    <header className="project-controls__header">
      <div className="project-controls__title">
        <p className="project-controls__eyebrow">{t("projects.eyebrow")}</p>
        <h1>{currentProject.name}</h1>
        <p className="project-controls__description">{t("projects.description")}</p>
      </div>
      <div className="project-controls__state">
        <span className={`project-controls__status project-controls__status--${statusTone(currentProject.status)}`}><span aria-hidden="true" />{stateLabel}</span>
      </div>
    </header>

    {notice && <div className={`project-controls__feedback project-controls__feedback--${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>
      <span>{notice.message}</span>
      {notice.tone === "warning" && <button type="button" className="project-controls__text-button" onClick={() => void refresh()}>{t("projects.refresh")}</button>}
    </div>}
    {loadError && <div className="project-controls__feedback project-controls__feedback--error" role="alert"><span>{t("projects.refreshFailed", { error: loadError })}</span><button type="button" className="project-controls__text-button" onClick={() => void refresh()}>{t("common.retry")}</button></div>}

    <section className="project-controls__summary" aria-label={t("projects.summaryLabel")}>
      <div className="project-controls__summary-item"><span>{t("projects.summaryState")}</span><strong>{stateLabel}</strong></div>
      <div className="project-controls__summary-item"><span>{t("projects.summaryActions")}</span><strong>{anyActionEnabled ? t("projects.summaryActionsAvailable") : t("projects.summaryActionsUnavailable")}</strong></div>
      <div className="project-controls__summary-item"><span>{t("projects.summaryUpdated")}</span><strong>{updatedAt}</strong></div>
    </section>

    <section className="project-controls__section" aria-labelledby="project-controls-actions-title">
      <div className="project-controls__section-heading"><div><p className="project-controls__eyebrow">{t("projects.actionsEyebrow")}</p><h2 id="project-controls-actions-title">{t("projects.actionsTitle")}</h2></div><span className="project-controls__section-note">{t("projects.actionsNote")}</span></div>
      <div className="project-controls__actions">
        {capabilities.map(({ action, enabled, reason }) => <button
          key={action}
          type="button"
          className={`project-controls__button${action === "launch" ? " project-controls__button--primary" : ""}${action === "complete" ? " project-controls__button--danger" : ""}`}
          disabled={!enabled || busy || loading}
          title={!enabled ? reason : undefined}
          aria-label={`${projectControlActionLabel(t, action)}${!enabled && reason ? `. ${reason}` : ""}`}
          onClick={() => void runAction(action)}
        >{pendingAction === action ? t("projects.actionInProgress") : projectControlActionLabel(t, action)}</button>)}
      </div>
      {confirmingComplete && <div className="project-controls__confirmation" role="alertdialog" aria-label={t("projects.confirmTitle", { name: currentProject.name })}>
        <div><strong>{t("projects.confirmTitle", { name: currentProject.name })}</strong><span>{t("projects.confirmDetail")}</span></div>
        <button type="button" className="project-controls__button project-controls__button--danger" disabled={busy} onClick={() => void runAction("complete")}>{t("projects.confirmAction")}</button>
        <button type="button" className="project-controls__button" disabled={busy} onClick={() => setConfirmingComplete(false)}>{t("common.cancel")}</button>
      </div>}
    </section>

    <nav className="project-controls__links" aria-label={t("projects.linksLabel")}>
      <a href={hrefs.runtime}><span aria-hidden="true">01</span><span><strong>{t("projects.linkRuntime")}</strong><small>{t("projects.linkRuntimeHint")}</small></span><span aria-hidden="true">↗</span></a>
      <a href={hrefs.chat}><span aria-hidden="true">02</span><span><strong>{t("projects.linkChat")}</strong><small>{t("projects.linkChatHint")}</small></span><span aria-hidden="true">↗</span></a>
      <a href={hrefs.files}><span aria-hidden="true">03</span><span><strong>{t("projects.linkFiles")}</strong><small>{t("projects.linkFilesHint")}</small></span><span aria-hidden="true">↗</span></a>
      <a href={hrefs.models}><span aria-hidden="true">04</span><span><strong>{t("projects.linkModels")}</strong><small>{t("projects.linkModelsHint")}</small></span><span aria-hidden="true">↗</span></a>
    </nav>

    {/* Everything below describes how the platform tracks the project, not what the
        person can do with it, so it stays folded away until it is asked for. */}
    <details className="project-controls__advanced">
      <summary className="project-controls__advanced-summary">{t("projects.advanced")}</summary>
      <div className="project-controls__advanced-body">
        <section className="project-controls__summary" aria-label={t("projects.diagnosticsLabel")}>
          <div className="project-controls__summary-item"><span>{t("projects.diagRuntime")}</span><strong>{runtimeState ? `${stateLabel} · ${runtimeState}` : t("projects.stateUnknown")}</strong></div>
          <div className="project-controls__summary-item"><span>{t("projects.diagEnvironment")}</span><strong>{environmentRef || t("projects.diagEnvironmentNone")}</strong></div>
          <div className="project-controls__summary-item"><span>{t("projects.diagProjectId")}</span><strong>{currentProject.id}</strong></div>
        </section>
        {currentProject.runtime?.last_error && <p className="project-controls__runtime-hint"><span className="project-controls__runtime-error">{t("projects.diagLastError")}: {currentProject.runtime.last_error}</span></p>}
        <p className="project-controls__runtime-hint">{t("projects.runtimeHint")}</p>

        <section className="project-controls__section" aria-labelledby="project-controls-settings-title">
          <div className="project-controls__section-heading"><div><p className="project-controls__eyebrow">{t("projects.settingsEyebrow")}</p><h2 id="project-controls-settings-title">{t("projects.settingsTitle")}</h2></div><span className="project-controls__section-note">{t("projects.settingsNote")}</span></div>
          <form className="project-controls__form" onSubmit={(event) => void saveSettings(event)}>
            <label className="project-controls__field project-controls__field--name">{t("projects.fieldName")}
              <input name="name" type="text" value={name} onChange={(event) => setName(event.target.value)} minLength={1} maxLength={200} required disabled={busy} />
              <small>{name.trim().length}/200</small>
            </label>
            <label className="project-controls__field project-controls__field--budget">{t("projects.fieldBudget")}
              <input name="budget_tokens" type="number" inputMode="numeric" min={0} step={1} value={budgetTokens} placeholder={t("projects.budgetPlaceholder")} onChange={(event) => setBudgetTokens(event.target.value)} disabled={busy} aria-invalid={budgetIsInvalid} />
              <small>{budgetTokens === "" ? t("projects.budgetHintEmpty") : t("projects.budgetHint")}</small>
            </label>
            <label className="project-controls__field project-controls__field--description">{t("projects.fieldAbout")}
              <textarea name="about" rows={5} value={about} onChange={(event) => setAbout(event.target.value)} maxLength={8000} placeholder={t("projects.aboutPlaceholder")} disabled={busy} />
              <small>{about.length}/8000</small>
            </label>
            <div className="project-controls__form-footer"><span>{t("projects.formFooter")}</span><button type="submit" className="project-controls__button project-controls__button--primary" disabled={busy || loading || Object.keys(patch).length === 0 || !name.trim() || name.trim().length > 200 || budgetIsInvalid}>{saving ? t("projects.saving") : t("projects.saveChanges")}</button></div>
          </form>
        </section>
      </div>
    </details>
  </main>;
}

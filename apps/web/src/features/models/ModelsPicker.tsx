import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { Translate } from "../../i18n/catalogue";
import { useI18n } from "../../i18n/useT";
import { createDefaultModelsService, ModelsServiceError } from "./service";
import {
  filterKeys,
  filterModels,
  formatContext,
  formatPrice,
  keyAvailability,
  modelCapabilityLabel,
  modelsAvailability,
  probePresentation,
  providerOptions,
} from "./view-model";
import type {
  AvailableAiKey,
  KeyAvailability,
  LiveModel,
  LiveModelsResponse,
  ModelProject,
  ModelsPickerProps,
  ProbePresentation,
} from "./types";
import "./models.css";

const defaultService = createDefaultModelsService();

type Notice = { tone: "success" | "warning" | "error" | "info"; message: string };
type LoadErrors = { keys?: string; models?: string };

function readableError(t: Translate, error: unknown): string {
  if (error instanceof ModelsServiceError) {
    return error.traceId ? `${error.message} ${t("models.errorTrace", { code: error.traceId })}` : error.message;
  }
  return error instanceof Error ? error.message : t("models.errorGeneric");
}

function selectedModelLabel(model: LiveModel | undefined, modelId: string): string {
  return model?.label || modelId;
}

function availabilityClass(availability: KeyAvailability): string {
  return `models-pill models-pill--${availability.tone}`;
}

const PROBE_KEYS: Record<ProbePresentation, string> = {
  verified: "models.probeVerified",
  stale: "models.probeStale",
  error: "models.probeError",
  unavailable: "models.probeUnavailable",
  unchecked: "models.probeUnchecked",
};

function ProbeBadge({ probe }: { probe?: ProbePresentation }) {
  const { t } = useI18n();
  const current = probe ?? "unchecked";
  return <span className={`models-probe models-probe--${current}`}>{t(PROBE_KEYS[current])}</span>;
}

function KeyCard({
  keyItem,
  selected,
  current,
  disabled,
  onSelect,
}: {
  keyItem: AvailableAiKey;
  selected: boolean;
  current: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  const { t } = useI18n();
  const availability = keyAvailability(keyItem, t);
  const probe = probePresentation(keyItem.last_probe);
  return <button
    type="button"
    className={`models-key-card${selected ? " models-key-card--selected" : ""}${current ? " models-key-card--current" : ""}`}
    disabled={disabled}
    onClick={onSelect}
    aria-pressed={selected}
    aria-label={selected ? t("models.deselectKeyAria", { name: keyItem.name }) : t("models.selectKeyAria", { name: keyItem.name })}
  >
    <span className="models-key-card__topline">
      <strong>{keyItem.name}</strong>
      {current && <span className="models-current-tag">{t("models.currentBadge")}</span>}
    </span>
    <span className="models-key-card__meta"><span>{keyItem.provider || t("models.keyProviderUnknown")}</span></span>
    <span className="models-key-card__status"><span className={availabilityClass(availability)}>{availability.label}</span><ProbeBadge probe={probe} /></span>
    <span className="models-key-card__detail">{availability.detail}</span>
    {disabled && <span className="models-key-card__disabled">{t("models.keyDisabledHint")}</span>}
  </button>;
}

function ModelCard({
  model,
  selected,
  current,
  disabled,
  onSelect,
}: {
  model: LiveModel;
  selected: boolean;
  current: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  const { t, locale } = useI18n();
  const capability = modelCapabilityLabel(model, t);
  const name = model.label || model.id;
  return <button
    type="button"
    className={`models-model-card${selected ? " models-model-card--selected" : ""}${current ? " models-model-card--current" : ""}`}
    disabled={disabled}
    onClick={onSelect}
    aria-pressed={selected}
    aria-label={selected ? t("models.deselectModelAria", { name }) : t("models.selectModelAria", { name })}
    title={model.id}
  >
    <span className="models-model-card__headline"><strong>{name}</strong>{current && <span className="models-current-tag">{t("models.currentBadge")}</span>}</span>
    <span className={`models-capability models-capability--${capability.tone}`}>{capability.label}</span>
    <span className="models-model-card__meta">{model.publisher && <span>{model.publisher}</span>}{formatContext(model.max_context_tokens, t, locale) && <span>{formatContext(model.max_context_tokens, t, locale)}</span>}{formatPrice(model.input_price_usd_per_mtok) && <span>{t("models.priceIn", { price: formatPrice(model.input_price_usd_per_mtok) })}</span>}{formatPrice(model.output_price_usd_per_mtok) && <span>{t("models.priceOut", { price: formatPrice(model.output_price_usd_per_mtok) })}</span>}</span>
  </button>;
}

function SelectionSummary({
  project,
  selectedKey,
  currentModelId,
  selectedModelId,
  models,
  keyDirty,
  modelDirty,
}: {
  project: ModelProject;
  selectedKey?: AvailableAiKey;
  currentModelId: string;
  selectedModelId: string;
  models: LiveModelsResponse;
  keyDirty: boolean;
  modelDirty: boolean;
}) {
  const { t } = useI18n();
  const selectedModel = models.models.find((model) => model.id === selectedModelId);
  const dirtyWhat = keyDirty && modelDirty ? t("models.dirtyBoth") : keyDirty ? t("models.dirtyKey") : t("models.dirtyModel");
  return <aside className="models-summary" aria-labelledby="models-summary-title">
    <div className="models-summary__heading"><p className="models-eyebrow">{t("models.summaryEyebrow")}</p><h2 id="models-summary-title">{t("models.summaryTitle")}</h2></div>
    <dl>
      <div><dt>{t("models.provider")}</dt><dd>{selectedKey?.provider || project.agent_provider || t("models.notChosen")}</dd></div>
      <div><dt>{t("models.key")}</dt><dd>{selectedKey ? <strong>{selectedKey.name}</strong> : project.resolved_ai_key_id ? <strong>{t("models.keyUnavailable")}</strong> : t("models.notChosen")}</dd></div>
      <div><dt>{t("models.model")}</dt><dd>{currentModelId ? <><strong>{selectedModelLabel(selectedModel, currentModelId)}</strong><code>{currentModelId}</code></> : t("models.modelNotChosen")}</dd></div>
    </dl>
    {(keyDirty || modelDirty) && <p className="models-summary__dirty" role="status">{t("models.dirty", { what: dirtyWhat })}</p>}
  </aside>;
}

export function ModelsPicker({
  projectId,
  service = defaultService,
  initialProject,
  initialKeys,
  initialModels,
  initialModelId,
  onModelChange,
  onProjectSaved,
}: ModelsPickerProps) {
  const { t, locale } = useI18n();
  const [project, setProject] = useState<ModelProject | undefined>(initialProject);
  const [keys, setKeys] = useState<AvailableAiKey[]>(initialKeys ?? []);
  const [models, setModels] = useState<LiveModelsResponse>(initialModels ?? { models: [] });
  const [draftKeyId, setDraftKeyId] = useState(initialProject?.resolved_ai_key_id ?? "");
  const [currentModelId, setCurrentModelId] = useState(initialModelId ?? "");
  const [selectedModelId, setSelectedModelId] = useState(initialModelId ?? "");
  const [keyQuery, setKeyQuery] = useState("");
  const [modelQuery, setModelQuery] = useState("");
  const [providerFilter, setProviderFilter] = useState("all");
  const [loading, setLoading] = useState(!initialProject || !initialModels);
  const [savingProject, setSavingProject] = useState(false);
  const [savingModel, setSavingModel] = useState(false);
  const [refreshingModels, setRefreshingModels] = useState(false);
  const [errors, setErrors] = useState<LoadErrors>({});
  const [actionError, setActionError] = useState<string>();
  const [notice, setNotice] = useState<Notice>();
  const [keyWarningAcknowledged, setKeyWarningAcknowledged] = useState(false);
  const [modelWarningAcknowledged, setModelWarningAcknowledged] = useState(false);
  const requestNumber = useRef(0);
  const initialDataUsed = useRef(false);

  const load = useCallback(async (signal?: AbortSignal, quiet = false) => {
    const requestId = ++requestNumber.current;
    const useInitialData = !initialDataUsed.current;
    initialDataUsed.current = true;
    if (!quiet) setLoading(true);
    setErrors({});
    setActionError(undefined);
    try {
      const loadedProject = useInitialData && initialProject
        ? initialProject
        : await service.getProject(projectId, { signal });
      if (signal?.aborted || requestId !== requestNumber.current) return;
      setProject(loadedProject);
      setDraftKeyId(loadedProject.resolved_ai_key_id ?? "");

      const [keyResult, modelResult] = await Promise.allSettled([
        useInitialData && initialKeys ? Promise.resolve(initialKeys) : service.listAvailableKeys(projectId, { signal }),
        useInitialData && initialModels ? Promise.resolve(initialModels) : service.listLiveModels(projectId, { signal }),
      ]);
      if (signal?.aborted || requestId !== requestNumber.current) return;
      if (keyResult.status === "fulfilled") setKeys(keyResult.value);
      if (modelResult.status === "fulfilled") setModels(modelResult.value);
      setErrors({
        ...(keyResult.status === "rejected" ? { keys: readableError(t, keyResult.reason) } : {}),
        ...(modelResult.status === "rejected" ? { models: readableError(t, modelResult.reason) } : {}),
      });
    } catch (error) {
      if (signal?.aborted || requestId !== requestNumber.current) return;
      setErrors({ keys: readableError(t, error) });
    } finally {
      if (!quiet && !signal?.aborted && requestId === requestNumber.current) setLoading(false);
    }
  }, [initialKeys, initialModels, initialProject, projectId, service, t]);

  useEffect(() => {
    initialDataUsed.current = false;
  }, [projectId]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      controller.abort();
      requestNumber.current += 1;
    };
  }, [load]);

  useEffect(() => {
    if (initialModelId === undefined) return;
    setCurrentModelId(initialModelId);
    setSelectedModelId(initialModelId);
  }, [initialModelId, projectId]);

  const selectedKey = useMemo(() => keys.find((key) => key.id === draftKeyId), [draftKeyId, keys]);
  const currentKeyId = project?.resolved_ai_key_id ?? "";
  const keyDirty = Boolean(project && draftKeyId && draftKeyId !== currentKeyId);
  const modelDirty = selectedModelId !== currentModelId;
  const selectedModel = models.models.find((model) => model.id === selectedModelId);
  const currentModelIsLive = !currentModelId || models.models.some((model) => model.id === currentModelId);
  const selectedModelNeedsWarning = Boolean(selectedModel && selectedModel.catalog_matched === false);
  const selectedAvailability = selectedKey ? keyAvailability(selectedKey, t) : undefined;
  const keyNeedsAcknowledgement = Boolean(selectedAvailability && selectedAvailability.tone !== "positive" && keyDirty);
  const filteredKeys = useMemo(() => filterKeys(keys, keyQuery, providerFilter), [keys, keyQuery, providerFilter]);
  const filteredModels = useMemo(() => filterModels(models.models, modelQuery), [models.models, modelQuery]);
  const providers = useMemo(() => providerOptions(keys), [keys]);
  const availability = modelsAvailability(models, t);
  const modelBlockedByKey = keyDirty;
  const modelCapabilityUnavailable = models.source !== "live" || models.stale === true || Boolean(models.error_code);
  const modelSaveBlocked = !onModelChange || !modelDirty || !selectedModelId || modelBlockedByKey || modelCapabilityUnavailable || (selectedModelNeedsWarning && !modelWarningAcknowledged);

  function selectKey(key: AvailableAiKey) {
    setActionError(undefined);
    setNotice(undefined);
    setKeyWarningAcknowledged(false);
    setDraftKeyId(key.id);
    setProviderFilter(key.provider);
    if (key.id !== currentKeyId) {
      setSelectedModelId("");
      setModelWarningAcknowledged(false);
    }
  }

  function selectModel(model: LiveModel) {
    setActionError(undefined);
    setNotice(undefined);
    setModelWarningAcknowledged(false);
    setSelectedModelId(model.id);
  }

  async function saveProjectSelection(event?: FormEvent) {
    event?.preventDefault();
    if (!project || !selectedKey) return;
    if (keyNeedsAcknowledgement && !keyWarningAcknowledged) {
      setActionError(t("models.errorKeyAck"));
      return;
    }
    setSavingProject(true);
    setActionError(undefined);
    setNotice(undefined);
    try {
      const patch = { agent_provider: selectedKey.provider, resolved_ai_key_id: selectedKey.id } as const;
      await service.patchProject(project.id, patch);
      const confirmed = await service.getProject(project.id);
      if (confirmed.resolved_ai_key_id !== selectedKey.id || confirmed.agent_provider !== selectedKey.provider) {
        throw new ModelsServiceError(t("models.errorSelectionNotSaved"), { code: "PROJECT_SELECTION_NOT_CONFIRMED" });
      }
      setProject(confirmed);
      setDraftKeyId(confirmed.resolved_ai_key_id ?? "");
      setKeyWarningAcknowledged(false);
      onProjectSaved?.(confirmed);
      setRefreshingModels(true);
      try {
        const live = await service.listLiveModels(project.id);
        setModels(live);
        setErrors((current) => ({ ...current, models: undefined }));
        setNotice({ tone: "success", message: t("models.noticeKeySaved", { provider: selectedKey.provider, name: selectedKey.name }) });
      } catch (error) {
        setErrors((current) => ({ ...current, models: readableError(t, error) }));
        setNotice({ tone: "warning", message: t("models.noticeKeySavedNoModels", { detail: readableError(t, error) }) });
      } finally {
        setRefreshingModels(false);
      }
    } catch (error) {
      setActionError(t("models.errorSaveKey", { detail: readableError(t, error) }));
    } finally {
      setSavingProject(false);
    }
  }

  async function saveModel() {
    if (!onModelChange || !modelDirty) return;
    if (modelBlockedByKey) {
      setActionError(t("models.errorKeyFirst"));
      return;
    }
    if (selectedModelNeedsWarning && !modelWarningAcknowledged) {
      setActionError(t("models.errorModelAck"));
      return;
    }
    setSavingModel(true);
    setActionError(undefined);
    setNotice(undefined);
    try {
      await onModelChange(projectId, selectedModelId);
      setCurrentModelId(selectedModelId);
      setModelWarningAcknowledged(false);
      setNotice({ tone: "success", message: t("models.noticeModelApplied", { name: selectedModelLabel(selectedModel, selectedModelId) }) });
    } catch (error) {
      setActionError(t("models.errorApplyModel", { detail: readableError(t, error) }));
    } finally {
      setSavingModel(false);
    }
  }

  async function refreshModels() {
    setRefreshingModels(true);
    setActionError(undefined);
    try {
      const result = await service.listLiveModels(projectId);
      setModels(result);
      setErrors((current) => ({ ...current, models: undefined }));
      setNotice({ tone: result.source === "live" ? "success" : "warning", message: result.source === "live" ? t("models.noticeModelsRefreshed") : t("models.noticeModelsNotLive") });
    } catch (error) {
      setErrors((current) => ({ ...current, models: readableError(t, error) }));
      setNotice({ tone: "warning", message: t("models.noticeModelsUnavailable", { detail: readableError(t, error) }) });
    } finally {
      setRefreshingModels(false);
    }
  }

  if (loading && !project) {
    return <main className="models-picker"><div className="models-loading" role="status" aria-live="polite"><span className="models-spinner" aria-hidden="true" /><strong>{t("models.loadingTitle")}</strong><span>{t("models.loadingDetail")}</span></div></main>;
  }

  if (!project) {
    return <main className="models-picker"><div className="models-error-page" role="alert"><strong>{t("models.unavailableTitle")}</strong><p>{errors.keys || errors.models || t("models.backendNoProject")}</p><button type="button" className="models-button models-button--primary" onClick={() => void load()}>{t("models.retry")}</button></div></main>;
  }

  return <main className="models-picker">
    <header className="models-header">
      <div><p className="models-eyebrow">{t("models.eyebrow")}</p><h1>{t("models.title")}</h1><p className="models-header__detail">{t("models.subtitle")}. {t("models.headerDetail", { name: project.name })}</p></div>
      <button type="button" className="models-button models-button--quiet" onClick={() => void load(undefined, false)} disabled={loading || savingProject || refreshingModels}>{loading ? t("models.refreshing") : t("models.refresh")}</button>
    </header>

    {notice && <div className={`models-notice models-notice--${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"} aria-live="polite">{notice.message}</div>}
    {actionError && <div className="models-notice models-notice--error" role="alert">{actionError}</div>}

    <SelectionSummary project={project} selectedKey={selectedKey} currentModelId={currentModelId} selectedModelId={selectedModelId} models={models} keyDirty={keyDirty} modelDirty={modelDirty} />

    <section className="models-section" aria-labelledby="models-key-section-title">
      <div className="models-section__heading"><div><p className="models-eyebrow">{t("models.keysEyebrow")}</p><h2 id="models-key-section-title">{t("models.keysTitle")}</h2><p>{t("models.keysDetail")}</p></div><span className="models-count">{t("models.keysCount", { count: keys.length })}</span></div>
      {errors.keys && <div className="models-inline-error" role="alert"><strong>{t("models.keysLoadErrorTitle")}</strong><span>{errors.keys}</span><button type="button" className="models-text-button" onClick={() => void load()}>{t("models.retry")}</button></div>}
      {!errors.keys && <>
        <div className="models-key-toolbar">
          <label className="models-search" htmlFor="models-key-search"><span aria-hidden="true">⌕</span><span className="models-visually-hidden">{t("models.keySearchAria")}</span><input id="models-key-search" value={keyQuery} onChange={(event) => setKeyQuery(event.target.value)} placeholder={t("models.keySearchPlaceholder")} /></label>
          <label className="models-select-field" htmlFor="models-provider-filter"><span>{t("models.providerFilter")}</span><select id="models-provider-filter" value={providerFilter} onChange={(event) => setProviderFilter(event.target.value)}><option value="all">{t("models.allProviders")}</option>{providers.map((provider) => <option value={provider} key={provider}>{provider}</option>)}</select></label>
        </div>
        {keys.length === 0 ? <div className="models-empty"><strong>{t("models.keysEmptyTitle")}</strong><span>{t("models.keysEmptyHint")}</span></div> : filteredKeys.length === 0 ? <div className="models-empty"><strong>{t("models.keysNoMatchTitle")}</strong><span>{t("models.keysNoMatchHint")}</span></div> : <div className="models-key-grid">{filteredKeys.map((keyItem) => <KeyCard key={keyItem.id} keyItem={keyItem} selected={keyItem.id === draftKeyId} current={keyItem.id === currentKeyId} disabled={!keyItem.has_secret || (keyItem.status !== undefined && keyItem.status !== "active")} onSelect={() => selectKey(keyItem)} />)}</div>}
        {project.resolved_ai_key_id && !keys.some((key) => key.id === project.resolved_ai_key_id) && <div className="models-inline-warning" role="alert"><strong>{t("models.currentKeyMissingTitle")}</strong><span>{t("models.currentKeyMissingDetail")}</span></div>}
        {selectedKey && selectedAvailability && selectedAvailability.tone !== "positive" && <div className={`models-key-warning models-key-warning--${selectedAvailability.tone}`} role="alert"><strong>{selectedAvailability.label}</strong><span>{selectedAvailability.detail}</span>{keyNeedsAcknowledgement && <label className="models-check"><input type="checkbox" checked={keyWarningAcknowledged} onChange={(event) => setKeyWarningAcknowledged(event.target.checked)} />{t("models.keyWarningAck")}</label>}</div>}
        <div className="models-save-row"><p>{keyDirty ? t("models.keySavePending", { provider: selectedKey?.provider ?? t("models.provider"), name: selectedKey?.name ?? t("models.key") }) : t("models.keySaveReady")}</p><button type="button" className="models-button models-button--primary" onClick={() => void saveProjectSelection()} disabled={savingProject || !keyDirty || !selectedKey || keyNeedsAcknowledgement && !keyWarningAcknowledged}>{savingProject ? t("models.savingKey") : t("models.saveKey")}</button></div>
        {keys.length > 0 && <details className="models-advanced">
          <summary>{t("models.advanced")}</summary>
          <p>{t("models.keyIds", { list: keys.map((keyItem) => `${keyItem.name} — ${keyItem.id}`).join(", ") })}</p>
          {project.resolved_ai_key_id && <p>{t("models.currentKeyId", { id: project.resolved_ai_key_id })}</p>}
        </details>}
      </>}
    </section>

    <section className="models-section" aria-labelledby="models-live-section-title">
      <div className="models-section__heading"><div><p className="models-eyebrow">{t("models.modelsEyebrow")}</p><h2 id="models-live-section-title">{t("models.modelsTitle")}</h2><p>{t("models.modelsDetail")}</p></div><button type="button" className="models-button models-button--quiet" onClick={() => void refreshModels()} disabled={refreshingModels || savingProject || modelBlockedByKey}>{refreshingModels ? t("models.refreshingModels") : t("models.refreshModels")}</button></div>
      <div className={`models-availability models-availability--${availability.tone}`}><span className="models-availability__dot" aria-hidden="true" /><div><strong>{availability.label}</strong><span>{availability.detail}</span></div></div>
      {errors.models && <div className="models-inline-error" role="alert"><strong>{t("models.modelsLoadErrorTitle")}</strong><span>{errors.models}</span><button type="button" className="models-text-button" onClick={() => void refreshModels()}>{t("models.retry")}</button></div>}
      {modelBlockedByKey && <div className="models-inline-warning" role="status"><strong>{t("models.modelsBlockedTitle")}</strong><span>{t("models.modelsBlockedDetail")}</span></div>}
      {!errors.models && !modelBlockedByKey && <>
        <div className="models-model-toolbar"><label className="models-search" htmlFor="models-model-search"><span aria-hidden="true">⌕</span><span className="models-visually-hidden">{t("models.modelSearchAria")}</span><input id="models-model-search" value={modelQuery} onChange={(event) => setModelQuery(event.target.value)} placeholder={t("models.modelSearchPlaceholder")} /></label><span className="models-count">{t("models.modelCount", { shown: filteredModels.length, total: models.models.length })}</span></div>
        {currentModelId && !currentModelIsLive && <div className="models-inline-warning" role="alert"><strong>{t("models.currentModelMissingTitle")}</strong><span>{t("models.currentModelMissingDetail")}</span></div>}
        {models.models.length === 0 ? <div className="models-empty"><strong>{t("models.modelsEmptyTitle")}</strong><span>{t("models.modelsEmptyHint")}</span></div> : filteredModels.length === 0 ? <div className="models-empty"><strong>{t("models.modelsNoMatchTitle")}</strong><span>{t("models.modelsNoMatchHint")}</span></div> : <div className="models-model-grid">{filteredModels.map((model) => <ModelCard key={model.id} model={model} selected={model.id === selectedModelId} current={model.id === currentModelId} disabled={savingModel} onSelect={() => selectModel(model)} />)}</div>}
        {selectedModelNeedsWarning && selectedModel && <div className="models-inline-warning" role="alert"><strong>{t("models.modelWarningTitle")}</strong><span>{t("models.modelWarningDetail", { name: selectedModelLabel(selectedModel, selectedModel.id) })}</span><label className="models-check"><input type="checkbox" checked={modelWarningAcknowledged} onChange={(event) => setModelWarningAcknowledged(event.target.checked)} />{t("models.modelWarningAck")}</label></div>}
        <div className="models-save-row models-save-row--model"><p>{!onModelChange ? t("models.modelNoCallback") : modelCapabilityUnavailable ? t("models.modelBlockedByCapability") : modelDirty ? t("models.modelSavePending", { name: selectedModelLabel(selectedModel, selectedModelId) }) : currentModelId ? t("models.modelSaveReady") : t("models.modelChooseHint")}</p><button type="button" className="models-button models-button--primary" onClick={() => void saveModel()} disabled={modelSaveBlocked || savingModel}>{savingModel ? t("models.applyingModel") : t("models.applyModel")}</button></div>
        {models.models.length > 0 && <details className="models-advanced">
          <summary>{t("models.advanced")}</summary>
          <p>{t("models.modelIds", { list: models.models.map((model) => model.id).join(", ") })}</p>
        </details>}
      </>}
    </section>

    <footer className="models-footer">
      <span>{t("models.footerSecret")}</span>
      <details>
        <summary>{t("models.advanced")}</summary>
        <span>{t("models.footerSource")} /projects/:projectId/ai-keys/available, /projects/:projectId/models/live</span>
      </details>
    </footer>
  </main>;
}

export { readableError };

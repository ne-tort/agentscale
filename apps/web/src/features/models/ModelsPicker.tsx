import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
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

function readableError(error: unknown): string {
  if (error instanceof ModelsServiceError) {
    return error.traceId ? `${error.message} Код поддержки: ${error.traceId}.` : error.message;
  }
  return error instanceof Error ? error.message : "Операция с AI configuration не выполнена.";
}

function selectedModelLabel(model: LiveModel | undefined, modelId: string): string {
  return model?.label || modelId || "Модель не выбрана";
}

function availabilityClass(availability: KeyAvailability): string {
  return `models-pill models-pill--${availability.tone}`;
}

function ProbeBadge({ probe }: { probe?: ProbePresentation }) {
  const label: Record<ProbePresentation, string> = {
    verified: "Проверен",
    stale: "Stale",
    error: "Ошибка probe",
    unavailable: "Probe недоступен",
    unchecked: "Не проверен",
  };
  return <span className={`models-probe models-probe--${probe ?? "unchecked"}`}>{label[probe ?? "unchecked"]}</span>;
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
  const availability = keyAvailability(keyItem);
  const probe = probePresentation(keyItem.last_probe);
  return <button
    type="button"
    className={`models-key-card${selected ? " models-key-card--selected" : ""}${current ? " models-key-card--current" : ""}`}
    disabled={disabled}
    onClick={onSelect}
    aria-pressed={selected}
    aria-label={`${selected ? "Выбран" : "Выбрать"} key ${keyItem.name}`}
  >
    <span className="models-key-card__topline">
      <strong>{keyItem.name}</strong>
      {current && <span className="models-current-tag">Текущий</span>}
    </span>
    <span className="models-key-card__meta"><span>{keyItem.provider}</span>{keyItem.api_kind && <span>{keyItem.api_kind}</span>}<code>{keyItem.id}</code></span>
    <span className="models-key-card__status"><span className={availabilityClass(availability)}>{availability.label}</span><ProbeBadge probe={probe} /></span>
    <span className="models-key-card__detail">{availability.detail}</span>
    {disabled && <span className="models-key-card__disabled">Выбор недоступен: backend не подтвердил рабочий секрет.</span>}
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
  const capability = modelCapabilityLabel(model);
  return <button
    type="button"
    className={`models-model-card${selected ? " models-model-card--selected" : ""}${current ? " models-model-card--current" : ""}`}
    disabled={disabled}
    onClick={onSelect}
    aria-pressed={selected}
    aria-label={`${selected ? "Выбрана" : "Выбрать"} модель ${model.label}`}
  >
    <span className="models-model-card__headline"><strong>{model.label}</strong>{current && <span className="models-current-tag">Текущая</span>}</span>
    <code>{model.id}</code>
    <span className={`models-capability models-capability--${capability.tone}`}>{capability.label}</span>
    <span className="models-model-card__meta">{model.publisher && <span>{model.publisher}</span>}{formatContext(model.max_context_tokens) && <span>{formatContext(model.max_context_tokens)}</span>}{formatPrice(model.input_price_usd_per_mtok) && <span>in {formatPrice(model.input_price_usd_per_mtok)}</span>}{formatPrice(model.output_price_usd_per_mtok) && <span>out {formatPrice(model.output_price_usd_per_mtok)}</span>}</span>
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
  const selectedModel = models.models.find((model) => model.id === selectedModelId);
  return <aside className="models-summary" aria-labelledby="models-summary-title">
    <div className="models-summary__heading"><p className="models-eyebrow">Текущий выбор</p><h2 id="models-summary-title">Project AI configuration</h2></div>
    <dl>
      <div><dt>Provider</dt><dd>{selectedKey?.provider || project.agent_provider || "Не выбран"}</dd></div>
      <div><dt>Key</dt><dd>{selectedKey ? <><strong>{selectedKey.name}</strong><code>{selectedKey.id}</code></> : project.resolved_ai_key_id ? <><strong>Key недоступен в available списке</strong><code>{project.resolved_ai_key_id}</code></> : "Не выбран"}</dd></div>
      <div><dt>Model</dt><dd>{currentModelId ? <><strong>{selectedModelLabel(selectedModel, currentModelId)}</strong><code>{currentModelId}</code></> : "Host ещё не передал текущую model"}</dd></div>
    </dl>
    {(keyDirty || modelDirty) && <p className="models-summary__dirty" role="status">Есть несохранённые изменения: {[keyDirty && "provider/key", modelDirty && "model"].filter(Boolean).join(" и ")}.</p>}
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
        ...(keyResult.status === "rejected" ? { keys: readableError(keyResult.reason) } : {}),
        ...(modelResult.status === "rejected" ? { models: readableError(modelResult.reason) } : {}),
      });
    } catch (error) {
      if (signal?.aborted || requestId !== requestNumber.current) return;
      setErrors({ keys: readableError(error) });
    } finally {
      if (!quiet && !signal?.aborted && requestId === requestNumber.current) setLoading(false);
    }
  }, [initialKeys, initialModels, initialProject, projectId, service]);

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
  const selectedAvailability = selectedKey ? keyAvailability(selectedKey) : undefined;
  const keyNeedsAcknowledgement = Boolean(selectedAvailability && selectedAvailability.tone !== "positive" && keyDirty);
  const filteredKeys = useMemo(() => filterKeys(keys, keyQuery, providerFilter), [keys, keyQuery, providerFilter]);
  const filteredModels = useMemo(() => filterModels(models.models, modelQuery), [models.models, modelQuery]);
  const providers = useMemo(() => providerOptions(keys), [keys]);
  const availability = modelsAvailability(models);
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
      setActionError("Подтвердите предупреждение о состоянии key перед сохранением.");
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
        throw new ModelsServiceError("Backend не подтвердил сохранённые provider и key.", { code: "PROJECT_SELECTION_NOT_CONFIRMED" });
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
        setNotice({ tone: "success", message: `Provider «${selectedKey.provider}» и key «${selectedKey.name}» сохранены. Live models обновлены.` });
      } catch (error) {
        setErrors((current) => ({ ...current, models: readableError(error) }));
        setNotice({ tone: "warning", message: `Provider и key сохранены. Live models пока не подтверждены: ${readableError(error)}` });
      } finally {
        setRefreshingModels(false);
      }
    } catch (error) {
      setActionError(`Не удалось сохранить provider/key: ${readableError(error)}`);
    } finally {
      setSavingProject(false);
    }
  }

  async function saveModel() {
    if (!onModelChange || !modelDirty) return;
    if (modelBlockedByKey) {
      setActionError("Сначала сохраните provider/key: live models относятся к текущему сохранённому key.");
      return;
    }
    if (selectedModelNeedsWarning && !modelWarningAcknowledged) {
      setActionError("Подтвердите предупреждение о неподтверждённой catalog compatibility.");
      return;
    }
    setSavingModel(true);
    setActionError(undefined);
    setNotice(undefined);
    try {
      await onModelChange(projectId, selectedModelId);
      setCurrentModelId(selectedModelId);
      setModelWarningAcknowledged(false);
      setNotice({ tone: "success", message: `Модель «${selectedModelLabel(selectedModel, selectedModelId)}» применена host-сервисом.` });
    } catch (error) {
      setActionError(`Не удалось применить model: ${readableError(error)}`);
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
      setNotice({ tone: result.source === "live" ? "success" : "warning", message: result.source === "live" ? "Live models перечитаны и подтверждены backend." : "Backend вернул список без live-подтверждения." });
    } catch (error) {
      setErrors((current) => ({ ...current, models: readableError(error) }));
      setNotice({ tone: "warning", message: `Live models недоступны: ${readableError(error)}` });
    } finally {
      setRefreshingModels(false);
    }
  }

  if (loading && !project) {
    return <main className="models-picker"><div className="models-loading" role="status" aria-live="polite"><span className="models-spinner" aria-hidden="true" /><strong>Загружаем AI configuration</strong><span>Читаем доступные keys и live models проекта.</span></div></main>;
  }

  if (!project) {
    return <main className="models-picker"><div className="models-error-page" role="alert"><strong>AI configuration недоступна</strong><p>{errors.keys || errors.models || "Backend не вернул проект."}</p><button type="button" className="models-button models-button--primary" onClick={() => void load()}>Повторить</button></div></main>;
  }

  return <main className="models-picker">
    <header className="models-header">
      <div><p className="models-eyebrow">Project AI settings</p><h1>Provider, key и model</h1><p className="models-header__detail">Проект «{project.name}». Provider и key сохраняются через подтверждённый PATCH проекта; модели читаются только из live runtime.</p></div>
      <button type="button" className="models-button models-button--quiet" onClick={() => void load(undefined, false)} disabled={loading || savingProject || refreshingModels}>{loading ? "Обновляем…" : "Обновить"}</button>
    </header>

    {notice && <div className={`models-notice models-notice--${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"} aria-live="polite">{notice.message}</div>}
    {actionError && <div className="models-notice models-notice--error" role="alert">{actionError}</div>}

    <SelectionSummary project={project} selectedKey={selectedKey} currentModelId={currentModelId} selectedModelId={selectedModelId} models={models} keyDirty={keyDirty} modelDirty={modelDirty} />

    <section className="models-section" aria-labelledby="models-key-section-title">
      <div className="models-section__heading"><div><p className="models-eyebrow">01 / Credential boundary</p><h2 id="models-key-section-title">AI provider и key</h2><p>Показываются только keys, которые backend подтвердил доступными для этого проекта.</p></div><span className="models-count">{keys.length} доступно</span></div>
      {errors.keys && <div className="models-inline-error" role="alert"><strong>Available keys не загрузились</strong><span>{errors.keys}</span><button type="button" className="models-text-button" onClick={() => void load()}>Повторить</button></div>}
      {!errors.keys && <>
        <div className="models-key-toolbar">
          <label className="models-search" htmlFor="models-key-search"><span aria-hidden="true">⌕</span><span className="models-visually-hidden">Поиск provider или key</span><input id="models-key-search" value={keyQuery} onChange={(event) => setKeyQuery(event.target.value)} placeholder="Поиск по provider, имени или id key" /></label>
          <label className="models-select-field" htmlFor="models-provider-filter"><span>Provider</span><select id="models-provider-filter" value={providerFilter} onChange={(event) => setProviderFilter(event.target.value)}><option value="all">Все provider</option>{providers.map((provider) => <option value={provider} key={provider}>{provider}</option>)}</select></label>
        </div>
        {keys.length === 0 ? <div className="models-empty"><strong>Backend не вернул доступных keys</strong><span>Создание, удаление и CRUD keys находятся за пределами этой feature zone.</span></div> : filteredKeys.length === 0 ? <div className="models-empty"><strong>Ничего не найдено</strong><span>Измените поиск или фильтр provider.</span></div> : <div className="models-key-grid">{filteredKeys.map((keyItem) => <KeyCard key={keyItem.id} keyItem={keyItem} selected={keyItem.id === draftKeyId} current={keyItem.id === currentKeyId} disabled={!keyItem.has_secret || (keyItem.status !== undefined && keyItem.status !== "active")} onSelect={() => selectKey(keyItem)} />)}</div>}
        {project.resolved_ai_key_id && !keys.some((key) => key.id === project.resolved_ai_key_id) && <div className="models-inline-warning" role="alert"><strong>Текущий key больше не доступен</strong><span>Backend project record ссылается на <code>{project.resolved_ai_key_id}</code>, которого нет в `/ai-keys/available`. Выберите подтверждённый key, чтобы исправить конфигурацию.</span></div>}
        {selectedKey && selectedAvailability && selectedAvailability.tone !== "positive" && <div className={`models-key-warning models-key-warning--${selectedAvailability.tone}`} role="alert"><strong>{selectedAvailability.label}</strong><span>{selectedAvailability.detail} Сохранение не скрывает это состояние.</span>{keyNeedsAcknowledgement && <label className="models-check"><input type="checkbox" checked={keyWarningAcknowledged} onChange={(event) => setKeyWarningAcknowledged(event.target.checked)} />Я понимаю, что provider/key ещё не имеют успешного свежего probe.</label>}</div>}
        <div className="models-save-row"><p>{keyDirty ? `Будет сохранено: ${selectedKey?.provider ?? "provider"} · ${selectedKey?.name ?? "key"}.` : "Provider и key совпадают с подтверждённым project record."}</p><button type="button" className="models-button models-button--primary" onClick={() => void saveProjectSelection()} disabled={savingProject || !keyDirty || !selectedKey || keyNeedsAcknowledgement && !keyWarningAcknowledged}>{savingProject ? "Сохраняем и проверяем…" : "Сохранить provider и key"}</button></div>
      </>}
    </section>

    <section className="models-section" aria-labelledby="models-live-section-title">
      <div className="models-section__heading"><div><p className="models-eyebrow">02 / Runtime capability</p><h2 id="models-live-section-title">Live models</h2><p>Список относится к текущему сохранённому key. Несохранённый key не используется для неподтверждённого запроса.</p></div><button type="button" className="models-button models-button--quiet" onClick={() => void refreshModels()} disabled={refreshingModels || savingProject || modelBlockedByKey}>{refreshingModels ? "Проверяем…" : "Проверить live models"}</button></div>
      <div className={`models-availability models-availability--${availability.tone}`}><span className="models-availability__dot" aria-hidden="true" /><div><strong>{availability.label}</strong><span>{availability.detail}</span></div></div>
      {errors.models && <div className="models-inline-error" role="alert"><strong>Live models не подтверждены</strong><span>{errors.models}</span><button type="button" className="models-text-button" onClick={() => void refreshModels()}>Повторить</button></div>}
      {modelBlockedByKey && <div className="models-inline-warning" role="status"><strong>Сначала сохраните provider/key</strong><span>Текущий список live models относится к прежнему key и не может подтвердить совместимость с черновым выбором.</span></div>}
      {!errors.models && !modelBlockedByKey && <>
        <div className="models-model-toolbar"><label className="models-search" htmlFor="models-model-search"><span aria-hidden="true">⌕</span><span className="models-visually-hidden">Поиск модели</span><input id="models-model-search" value={modelQuery} onChange={(event) => setModelQuery(event.target.value)} placeholder="Поиск по model id, label или publisher" /></label><span className="models-count">{filteredModels.length} из {models.models.length}</span></div>
        {currentModelId && !currentModelIsLive && <div className="models-inline-warning" role="alert"><strong>Текущая model не подтверждена live</strong><span><code>{currentModelId}</code> отсутствует в текущем `/models/live`. Выберите доступную model; старый выбор не будет молча сохранён.</span></div>}
        {models.models.length === 0 ? <div className="models-empty"><strong>Live models пока нет</strong><span>Проверьте provider/key и состояние project runtime.</span></div> : filteredModels.length === 0 ? <div className="models-empty"><strong>Модели не найдены</strong><span>Измените поисковый запрос.</span></div> : <div className="models-model-grid">{filteredModels.map((model) => <ModelCard key={model.id} model={model} selected={model.id === selectedModelId} current={model.id === currentModelId} disabled={savingModel} onSelect={() => selectModel(model)} />)}</div>}
        {selectedModelNeedsWarning && selectedModel && <div className="models-inline-warning" role="alert"><strong>Совместимость не подтверждена</strong><span>Live provider вернул «{selectedModel.label}», но catalog не сопоставил эту model. Выберите подтверждённую model или подтвердите риск перед применением.</span><label className="models-check"><input type="checkbox" checked={modelWarningAcknowledged} onChange={(event) => setModelWarningAcknowledged(event.target.checked)} />Я понимаю, что catalog compatibility не подтверждена.</label></div>}
        <div className="models-save-row models-save-row--model"><p>{!onModelChange ? "Host callback для применения model не подключён; PATCH project model не заявляет." : modelCapabilityUnavailable ? "Применение заблокировано: live capability не подтверждена свежим ответом backend." : modelDirty ? `Будет применена: ${selectedModelLabel(selectedModel, selectedModelId)}.` : currentModelId ? "Model совпадает с текущим выбором host." : "Выберите live model для применения."}</p><button type="button" className="models-button models-button--primary" onClick={() => void saveModel()} disabled={modelSaveBlocked || savingModel}>{savingModel ? "Применяем…" : "Применить model"}</button></div>
      </>}
    </section>

    <footer className="models-footer"><span>Secret values не отображаются и не участвуют в client state.</span><span>Источник: `/projects/:projectId/ai-keys/available`, `/projects/:projectId/models/live`, PATCH project.</span></footer>
  </main>;
}

export { readableError };

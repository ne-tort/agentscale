import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useAuth } from "../../app/context";
import { AutomationRequestError, createDefaultAutomationService, parseJsonObject } from "./service";
import {
  PROJECT_TRIGGER_KINDS,
  type AutomationCollection,
  type AgentRun,
  type AutomationService,
  type AutomationsPageProps,
  type AutomationsViewProps,
  type JsonObject,
  type ProjectTrigger,
  type ProjectTriggerKind,
  type Schedule,
  type ScheduleInput,
  type WebhookDelivery,
  type WebhookSubscription,
} from "./types";
import "./automations.css";

const SUPPORTED_WEBHOOK_EVENTS = ["chat.message.completed", "chat.turn.failed"] as const;
const PROJECT_COMMAND_KINDS = new Set<string>(["chat.message", "chat.regenerate", "project.prepare"]);
const INBOUND_EVENT_KINDS = new Set<string>(["webhook.http", "telegram.message"]);
const SCHEDULE_EVENT_KINDS = new Set<string>(["system.schedule"]);
const INITIAL_SCHEDULE: ScheduleInput = {
  name: "",
  cron: "0 9 * * 1-5",
  timezone: "UTC",
  payload: {},
  enabled: true,
};

const TRIGGER_KIND_LABELS: Record<ProjectTriggerKind, string> = {
  "chat.message": "Сообщение агента (chat.message)",
  "chat.regenerate": "Повторная генерация (chat.regenerate)",
  "project.prepare": "Подготовка проекта (project.prepare)",
  "system.schedule": "Срабатывание расписания (system.schedule)",
  "webhook.http": "HTTP webhook event (webhook.http)",
  "telegram.message": "Сообщение Telegram (telegram.message)",
};

function errorMessage(error: unknown): string {
  if (error instanceof AutomationRequestError) {
    const support = error.traceId ? ` Код поддержки: ${error.traceId}.` : "";
    return `${error.message}${support}`;
  }
  return error instanceof Error ? error.message : "Запрос не подтверждён backend.";
}

function dateTime(value?: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "Не предоставлено";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function formatJson(value: JsonObject): string {
  return JSON.stringify(value, null, 2);
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}

async function copyOneTimeSecret(value: string): Promise<void> {
  if (!navigator.clipboard?.writeText) throw new Error("Буфер обмена браузера недоступен в этом контексте.");
  await navigator.clipboard.writeText(value);
}

function collection<T>(): AutomationCollection<T> {
  return { status: "loading", items: [] };
}

function StatePill({ value, tone }: { value: string; tone?: "positive" | "warning" | "danger" | "neutral" }) {
  return <span className={`automation-pill automation-pill--${tone ?? "neutral"}`}><span aria-hidden="true" />{value}</span>;
}

function SectionHeader({ headingId, eyebrow, title, detail, action }: { headingId?: string; eyebrow: string; title: string; detail: string; action?: ReactNode }) {
  return <div className="automation-section-heading"><div><p className="automation-eyebrow">{eyebrow}</p><h2 id={headingId}>{title}</h2><p>{detail}</p></div>{action}</div>;
}

function CollectionState({ status, error, empty, children, onRetry, label }: {
  status: AutomationCollection<unknown>["status"];
  error?: string;
  empty: boolean;
  children: ReactNode;
  onRetry: () => void;
  label: string;
}) {
  if (status === "loading") return <div className="automation-state" role="status" aria-live="polite"><span className="automation-spinner" aria-hidden="true" /><strong>Загружаем {label}</strong><span>Читаем сохранённое состояние backend.</span></div>;
  if (status === "error") return <div className="automation-state automation-state--error" role="alert"><strong>{label} недоступны</strong><span>{error || "Не удалось загрузить данные."}</span><button type="button" className="automation-button automation-button--quiet" onClick={onRetry}>Повторить загрузку</button></div>;
  if (empty) return <div className="automation-state"><span className="automation-state-icon" aria-hidden="true">⌁</span><strong>Пока нет данных</strong><span>{children}</span></div>;
  return <>{children}</>;
}

function ScheduleEditor({ schedule, busy, onSave, onCancel }: {
  schedule?: Schedule;
  busy: boolean;
  onSave: (input: ScheduleInput) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState(schedule?.name ?? INITIAL_SCHEDULE.name);
  const [cron, setCron] = useState(schedule?.cron ?? INITIAL_SCHEDULE.cron);
  const [timezone, setTimezone] = useState(schedule?.timezone ?? INITIAL_SCHEDULE.timezone);
  const [payloadText, setPayloadText] = useState(formatJson(schedule?.payload ?? INITIAL_SCHEDULE.payload));
  const [enabled, setEnabled] = useState(schedule?.enabled ?? INITIAL_SCHEDULE.enabled);
  const [error, setError] = useState<string>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    const cleanName = name.trim();
    const cleanCron = cron.trim().replace(/\s+/g, " ");
    const cleanTimezone = timezone.trim();
    if (!cleanName) { setError("Введите название расписания."); return; }
    if (cleanCron.split(" ").length !== 5 || cleanCron.length < 9) { setError("Cron должен содержать пять полей: минута час день месяц день-недели."); return; }
    if (!cleanTimezone) { setError("Укажите timezone, например UTC или Europe/Moscow."); return; }
    try { new Intl.DateTimeFormat("en", { timeZone: cleanTimezone }); }
    catch { setError("Браузер не распознал IANA timezone. Проверьте значение; backend выполнит окончательную проверку."); return; }
    let payload: JsonObject;
    try { payload = parseJsonObject(payloadText); }
    catch (caught) { setError(errorMessage(caught)); return; }
    await onSave({ name: cleanName, cron: cleanCron, timezone: cleanTimezone, payload, enabled });
  }

  return <form className="automation-form automation-schedule-form" onSubmit={(event) => void submit(event)} aria-label={schedule ? `Редактировать расписание ${schedule.name}` : "Новое расписание"}>
    <div className="automation-form-grid">
      <label>Название<input value={name} onChange={(event) => setName(event.target.value)} required maxLength={200} /></label>
      <label>Cron<input value={cron} onChange={(event) => setCron(event.target.value)} required minLength={9} maxLength={120} aria-describedby="automation-cron-help" /><small id="automation-cron-help">Пять полей, по стандарту cron. Backend подтвердит выражение.</small></label>
      <label>Timezone<input value={timezone} onChange={(event) => setTimezone(event.target.value)} required maxLength={64} placeholder="UTC или Europe/Moscow" /></label>
      <label className="automation-checkbox"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span>Расписание включено</span></label>
      <label className="automation-field-wide">Payload (JSON object)<textarea rows={5} spellCheck={false} value={payloadText} onChange={(event) => setPayloadText(event.target.value)} aria-describedby="automation-payload-help" /><small id="automation-payload-help">Payload будет передан в очередь триггеров; ввод должен быть JSON-объектом.</small></label>
    </div>
    {error && <p className="automation-inline-error" role="alert">{error}</p>}
    <div className="automation-form-actions"><button type="button" className="automation-button automation-button--quiet" onClick={onCancel} disabled={busy}>Отмена</button><button type="submit" className="automation-button automation-button--primary" disabled={busy}>{busy ? "Сохраняем…" : schedule ? "Сохранить расписание" : "Создать расписание"}</button></div>
  </form>;
}

function ScheduleCard({ schedule, busy, onEdit, onToggle, onDelete }: {
  schedule: Schedule;
  busy: boolean;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
}) {
  return <article className="automation-card" aria-label={`Расписание ${schedule.name}`}>
    <div className="automation-card-heading"><div><span className="automation-card-icon" aria-hidden="true">◷</span><div className="automation-card-title"><h3>{schedule.name}</h3><code>{schedule.cron}</code></div></div><StatePill value={schedule.enabled ? "Включено" : "Выключено"} tone={schedule.enabled ? "positive" : "neutral"} /></div>
    <dl className="automation-facts"><div><dt>Timezone</dt><dd>{schedule.timezone}</dd></div><div><dt>Следующее срабатывание</dt><dd>{schedule.enabled ? dateTime(schedule.next_run_at) : "Отключено"}</dd></div><div><dt>Последнее срабатывание</dt><dd>{dateTime(schedule.last_run_at)}</dd></div></dl>
    <p className="automation-capability-hint">Время расписания подтверждает постановку события планировщиком, а не исход Agent Run.</p>
    <details className="automation-review"><summary>Проверить payload</summary><pre>{formatJson(schedule.payload)}</pre></details>
    <div className="automation-card-actions"><button type="button" className="automation-button automation-button--quiet" onClick={onToggle} disabled={busy} aria-label={`${schedule.enabled ? "Выключить" : "Включить"} расписание ${schedule.name}`}>{busy ? "Обновляем…" : schedule.enabled ? "Выключить" : "Включить"}</button><button type="button" className="automation-button automation-button--quiet" onClick={onEdit} disabled={busy}>Изменить</button><button type="button" className="automation-button automation-button--danger-quiet" onClick={onDelete} disabled={busy}>Удалить</button></div>
  </article>;
}

function TriggerStatus({ trigger }: { trigger: ProjectTrigger }) {
  if (trigger.status === "queued") return <StatePill value="В очереди" tone="warning" />;
  if (trigger.status === "done") return <StatePill value="Завершён по данным trigger" tone="positive" />;
  if (trigger.status === "failed") return <StatePill value="Ошибка" tone="danger" />;
  return <StatePill value={`Состояние: ${trigger.status}`} />;
}

function TriggerCard({ trigger }: { trigger: ProjectTrigger }) {
  const origin = SCHEDULE_EVENT_KINDS.has(trigger.kind)
    ? "Расписание"
    : INBOUND_EVENT_KINDS.has(trigger.kind)
      ? "Входящее событие"
      : "Project command";
  return <article className="automation-card automation-trigger-card" aria-label={`Триггер ${trigger.kind} ${trigger.id}`}>
    <div className="automation-card-heading"><div><span className="automation-card-icon" aria-hidden="true">↗</span><div className="automation-card-title"><h3>{trigger.kind}</h3><code>{origin} · {trigger.id}</code></div></div><TriggerStatus trigger={trigger} /></div>
    <dl className="automation-facts automation-facts--trigger"><div><dt>Создан</dt><dd>{dateTime(trigger.created_at)}</dd></div><div><dt>Попытки</dt><dd>{trigger.attempts === undefined ? "Не предоставлено" : trigger.attempts}</dd></div><div><dt>Следующая доступность</dt><dd>{dateTime(trigger.available_at)}</dd></div></dl>
    {trigger.last_error && <div className="automation-trigger-error" role="alert"><strong>Последняя ошибка из backend</strong><p>{trigger.last_error}</p></div>}
    {trigger.status === "failed" && <p className="automation-capability-hint">Повторить именно этот failed trigger нельзя: подтверждённый dispatch забирает следующий eligible trigger из очереди, а не принимает trigger_id.</p>}
    <details className="automation-review"><summary>Проверить payload и lease</summary><pre>{formatJson(trigger.payload)}</pre><dl><div><dt>Lease до</dt><dd>{dateTime(trigger.lease_until)}</dd></div><div><dt>Worker</dt><dd>{trigger.leased_by || "Не предоставлено"}</dd></div></dl></details>
  </article>;
}

function TriggerComposer({ projectId, busy, onEnqueue }: { projectId: string; busy: boolean; onEnqueue: (kind: ProjectTriggerKind, payload: JsonObject) => Promise<void> }) {
  const [kind, setKind] = useState<ProjectTriggerKind>("chat.message");
  const [payloadText, setPayloadText] = useState("{\n  \"text\": \"\"\n}");
  const [error, setError] = useState<string>();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    try { await onEnqueue(kind, parseJsonObject(payloadText)); }
    catch (caught) { setError(errorMessage(caught)); }
  }
  return <form className="automation-form automation-trigger-composer" onSubmit={(event) => void submit(event)}>
    <div className="automation-form-grid"><label>Trigger kind<select value={kind} onChange={(event) => setKind(event.target.value as ProjectTriggerKind)}><optgroup label="Project commands">{PROJECT_TRIGGER_KINDS.filter((value) => PROJECT_COMMAND_KINDS.has(value)).map((value) => <option value={value} key={value}>{TRIGGER_KIND_LABELS[value]}</option>)}</optgroup><optgroup label="Schedule and inbound event kinds">{PROJECT_TRIGGER_KINDS.filter((value) => SCHEDULE_EVENT_KINDS.has(value) || INBOUND_EVENT_KINDS.has(value)).map((value) => <option value={value} key={value}>{TRIGGER_KIND_LABELS[value]}</option>)}</optgroup></select></label><label className="automation-field-wide">Payload (JSON object)<textarea rows={4} spellCheck={false} value={payloadText} onChange={(event) => setPayloadText(event.target.value)} /></label></div>
    {error && <p className="automation-inline-error" role="alert">{error}</p>}
    <div className="automation-form-actions"><span>POST `/projects/{projectId}/triggers` ставит trigger в очередь. Здесь нет подписи входящего webhook; подписанные ingress вызываются отдельным endpoint.</span><button type="submit" className="automation-button automation-button--primary" disabled={busy}>{busy ? "Ставим в очередь…" : "Поставить в очередь"}</button></div>
  </form>;
}

function WebhookComposer({ projectId, busy, onCreate }: { projectId: string; busy: boolean; onCreate: (input: { url: string; events: string[]; project_id: string }) => Promise<void> }) {
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>([SUPPORTED_WEBHOOK_EVENTS[0]]);
  const [error, setError] = useState<string>();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    if (!events.length) { setError("Выберите хотя бы одно событие."); return; }
    try { await onCreate({ url: url.trim(), events, project_id: projectId }); setUrl(""); }
    catch (caught) { setError(errorMessage(caught)); }
  }
  return <form className="automation-form automation-webhook-form" onSubmit={(event) => void submit(event)}>
    <div className="automation-form-grid"><label className="automation-field-wide">URL получателя<input type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/hooks/agentscale" required maxLength={2000} /></label><fieldset className="automation-field-wide automation-event-options"><legend>События</legend>{SUPPORTED_WEBHOOK_EVENTS.map((eventName) => <label className="automation-checkbox" key={eventName}><input type="checkbox" checked={events.includes(eventName)} onChange={(event) => setEvents((current) => event.target.checked ? [...current, eventName] : current.filter((item) => item !== eventName))} /><code>{eventName}</code></label>)}</fieldset></div>
    {error && <p className="automation-inline-error" role="alert">{error}</p>}
    <div className="automation-form-actions"><span>Секрет будет возвращён backend один раз при создании. Сохраните его в доверенном secret store.</span><button type="submit" className="automation-button automation-button--primary" disabled={busy}>{busy ? "Создаём…" : "Добавить исходящий webhook"}</button></div>
  </form>;
}

function WebhookCard({ subscription, busy, onToggle, onDelete }: { subscription: WebhookSubscription; busy: boolean; onToggle: () => void; onDelete: () => void }) {
  return <article className="automation-card" aria-label={`Исходящий webhook ${subscription.url}`}>
    <div className="automation-card-heading"><div><span className="automation-card-icon" aria-hidden="true">⇧</span><div className="automation-card-title"><h3>{subscription.url}</h3><code>{subscription.id}</code></div></div><StatePill value={subscription.enabled ? "Активен" : "Отключён"} tone={subscription.enabled ? "positive" : "neutral"} /></div>
    <div className="automation-event-list" aria-label="Подписанные события">{subscription.events.map((eventName) => <code key={eventName}>{eventName}</code>)}</div>
    <dl className="automation-facts"><div><dt>Последовательные ошибки delivery</dt><dd>{subscription.fail_count === undefined ? "Не предоставлено" : subscription.fail_count}</dd></div><div><dt>Создан</dt><dd>{dateTime(subscription.created_at)}</dd></div></dl>
    <p className="automation-capability-hint">Счётчик ошибок подписки не является списком доставок и не подтверждает конкретный delivery outcome.</p>
    <div className="automation-card-actions"><button type="button" className="automation-button automation-button--quiet" onClick={onToggle} disabled={busy} aria-label={`${subscription.enabled ? "Отключить" : "Включить"} webhook ${subscription.url}`}>{busy ? "Обновляем…" : subscription.enabled ? "Отключить" : "Включить"}</button><button type="button" className="automation-button automation-button--danger-quiet" onClick={onDelete} disabled={busy}>Удалить</button></div>
  </article>;
}

function DeliveryCard({ delivery, busy, retryAllowed, onRetry }: { delivery: WebhookDelivery; busy: boolean; retryAllowed: boolean; onRetry: () => void }) {
  const statusTone = delivery.status === "delivered" ? "positive" : delivery.status === "exhausted" ? "danger" : delivery.status === "pending" ? "warning" : "neutral";
  return <article className="automation-card" aria-label={`Webhook delivery ${delivery.id}`}>
    <div className="automation-card-heading"><div><span className="automation-card-icon" aria-hidden="true">⇧</span><div className="automation-card-title"><h3>{delivery.event_type}</h3><code>{delivery.id}</code></div></div><StatePill value={delivery.status} tone={statusTone} /></div>
    <dl className="automation-facts"><div><dt>Subscription ID</dt><dd><code>{delivery.subscription_id}</code></dd></div><div><dt>Получатель</dt><dd>{delivery.url}</dd></div><div><dt>Попытки</dt><dd>{delivery.attempts}</dd></div><div><dt>HTTP response</dt><dd>{delivery.response_status ?? "Не предоставлено"}</dd></div></dl>
    {delivery.last_error && <div className="automation-trigger-error" role="alert"><strong>Последняя ошибка доставки</strong><p>{delivery.last_error}</p></div>}
    <details className="automation-review"><summary>История попыток</summary><pre>{JSON.stringify(delivery.retry_history, null, 2)}</pre></details>
    {delivery.status === "exhausted" && (retryAllowed
      ? <div className="automation-card-actions"><button type="button" className="automation-button automation-button--quiet" onClick={onRetry} disabled={busy}>{busy ? "Повторяем…" : "Повторить доставку"}</button></div>
      : <p className="automation-capability-hint">Retry недоступен: эта subscription не подтверждена в списке подписок текущего проекта.</p>)}
  </article>;
}

export function AutomationsView({ projectId, service }: AutomationsViewProps) {
  const [schedules, setSchedules] = useState<AutomationCollection<Schedule>>(collection());
  const [triggers, setTriggers] = useState<AutomationCollection<ProjectTrigger>>(collection());
  const [runs, setRuns] = useState<AutomationCollection<AgentRun>>(collection());
  const [webhooks, setWebhooks] = useState<AutomationCollection<WebhookSubscription>>(collection());
  const [deliveries, setDeliveries] = useState<AutomationCollection<WebhookDelivery>>(collection());
  const [deliveryProjectSubscriptionIds, setDeliveryProjectSubscriptionIds] = useState<Set<string>>(new Set());
  const [deliveryScope, setDeliveryScope] = useState<"company" | "project">("company");
  const [deliveryReturnedCount, setDeliveryReturnedCount] = useState(0);
  const [editingSchedule, setEditingSchedule] = useState<Schedule>();
  const [creatingSchedule, setCreatingSchedule] = useState(false);
  const [busy, setBusy] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [operationError, setOperationError] = useState<string>();
  const [createdSecret, setCreatedSecret] = useState<string>();
  const [secretCopyMessage, setSecretCopyMessage] = useState<string>();

  const reloadSchedules = useCallback(async () => {
    setSchedules((current) => ({ ...current, status: "loading", error: undefined }));
    try { setSchedules({ status: "ready", items: await service.listSchedules(projectId) }); }
    catch (error) { setSchedules((current) => ({ ...current, status: "error", error: errorMessage(error) })); }
  }, [projectId, service]);

  const reloadTriggers = useCallback(async () => {
    setTriggers((current) => ({ ...current, status: "loading", error: undefined }));
    try { setTriggers({ status: "ready", items: await service.listTriggers(projectId) }); }
    catch (error) { setTriggers((current) => ({ ...current, status: "error", error: errorMessage(error) })); }
  }, [projectId, service]);

  const reloadRuns = useCallback(async () => {
    setRuns((current) => ({ ...current, status: "loading", error: undefined }));
    try { setRuns({ status: "ready", items: await service.listAgentRuns(projectId) }); }
    catch (error) { setRuns((current) => ({ ...current, status: "error", error: errorMessage(error) })); }
  }, [projectId, service]);

  const reloadWebhooks = useCallback(async () => {
    setWebhooks((current) => ({ ...current, status: "loading", error: undefined }));
    try {
      const items = await service.listWebhooks(projectId);
      setWebhooks({ status: "ready", items });
      setDeliveryProjectSubscriptionIds(new Set(items.map((item) => item.id)));
    }
    catch (error) { setWebhooks((current) => ({ ...current, status: "error", error: errorMessage(error) })); }
  }, [projectId, service]);

  const reloadDeliveries = useCallback(async () => {
    setDeliveries((current) => ({ ...current, status: "loading", error: undefined }));
    try {
      const recent = await service.listWebhookDeliveries(200);
      setDeliveryReturnedCount(recent.length);
      setDeliveries({ status: "ready", items: recent });
    } catch (error) { setDeliveries((current) => ({ ...current, status: "error", error: errorMessage(error) })); }
  }, [projectId, service]);

  const refreshAll = useCallback(() => {
    setNotice(undefined);
    void Promise.all([reloadSchedules(), reloadTriggers(), reloadRuns(), reloadWebhooks(), reloadDeliveries()]);
  }, [reloadSchedules, reloadTriggers, reloadRuns, reloadWebhooks, reloadDeliveries]);

  useEffect(() => { refreshAll(); }, [refreshAll]);

  function clearFeedback() { setNotice(undefined); setOperationError(undefined); }

  async function saveSchedule(input: ScheduleInput) {
    const operation = editingSchedule ? `schedule:${editingSchedule.id}` : "schedule:create";
    setBusy(operation); clearFeedback();
    try {
      const saved = editingSchedule
        ? await service.updateSchedule(projectId, editingSchedule.id, input)
        : await service.createSchedule(projectId, input);
      const persisted = await service.listSchedules(projectId);
      setSchedules({ status: "ready", items: persisted });
      const confirmed = persisted.find((item) => item.id === saved.id);
      if (!confirmed || confirmed.name !== input.name || confirmed.cron !== input.cron || confirmed.timezone !== input.timezone || confirmed.enabled !== input.enabled || stableJson(confirmed.payload) !== stableJson(input.payload)) {
        throw new Error("Сервер принял изменение, но актуальный список не подтвердил сохранённые поля. Проверьте состояние перед повтором.");
      }
      setNotice(editingSchedule ? `Расписание «${confirmed.name}» сохранено и подтверждено GET списком.` : `Расписание «${confirmed.name}» создано и подтверждено GET списком.`);
      setEditingSchedule(undefined); setCreatingSchedule(false);
    } catch (error) {
      setOperationError(`${errorMessage(error)} Проверьте список расписаний перед повтором.`);
      void reloadSchedules();
    } finally { setBusy(undefined); }
  }

  async function toggleSchedule(schedule: Schedule) {
    const operation = `schedule:${schedule.id}`;
    setBusy(operation); clearFeedback();
    try {
      await service.updateSchedule(projectId, schedule.id, { enabled: !schedule.enabled });
      const persisted = await service.listSchedules(projectId);
      setSchedules({ status: "ready", items: persisted });
      const confirmed = persisted.find((item) => item.id === schedule.id);
      if (!confirmed || confirmed.enabled === schedule.enabled) throw new Error("Backend не подтвердил изменение enabled в списке расписаний.");
      setNotice(`Состояние расписания «${confirmed.name}» подтверждено backend: ${confirmed.enabled ? "включено" : "выключено"}.`);
    } catch (error) {
      setOperationError(`${errorMessage(error)} Сначала обновите список и проверьте текущее состояние.`);
      void reloadSchedules();
    } finally { setBusy(undefined); }
  }

  async function deleteSchedule(schedule: Schedule) {
    if (!window.confirm(`Удалить расписание «${schedule.name}»?`)) return;
    const operation = `schedule:${schedule.id}`;
    setBusy(operation); clearFeedback();
    try {
      await service.deleteSchedule(projectId, schedule.id);
      const persisted = await service.listSchedules(projectId);
      setSchedules({ status: "ready", items: persisted });
      if (persisted.some((item) => item.id === schedule.id)) throw new Error("Расписание всё ещё присутствует в backend списке; удаление не подтверждено.");
      setNotice(`Удаление расписания «${schedule.name}» подтверждено backend.`);
    } catch (error) {
      setOperationError(`${errorMessage(error)} Обновите список перед повтором.`);
      void reloadSchedules();
    } finally { setBusy(undefined); }
  }

  async function enqueueTrigger(kind: ProjectTriggerKind, payload: JsonObject) {
    setBusy("trigger:create"); clearFeedback();
    try {
      const queued = await service.enqueueTrigger(projectId, { kind, payload });
      const persisted = await service.listTriggers(projectId);
      setTriggers({ status: "ready", items: persisted });
      const confirmed = persisted.find((item) => item.id === queued.id);
      if (!confirmed) throw new Error("POST ответил, но GET список пока не содержит новый trigger; его постановка не подтверждена.");
      setNotice(`Trigger ${confirmed.id} подтверждён в backend со статусом «${confirmed.status}». Это подтверждение записи в очереди, не выполнения агента.`);
    } catch (error) {
      setOperationError(`${errorMessage(error)} Обновите историю триггеров перед повтором, чтобы не создать дубликат.`);
      void reloadTriggers();
    } finally { setBusy(undefined); }
  }

  async function dispatchNextTrigger() {
    setBusy("trigger:dispatch"); clearFeedback();
    try {
      const result = await service.dispatchNextTrigger(projectId);
      const persisted = await service.listTriggers(projectId);
      setTriggers({ status: "ready", items: persisted });
      setNotice(result.dispatched
        ? `Endpoint dispatch ответил dispatched=true${result.trigger_id ? ` для trigger ${result.trigger_id}` : ""}. Это статус обработчика очереди, а не отдельная связанная запись Agent Run.`
        : `Backend не обработал trigger: ${result.reason ?? "причина не предоставлена"}.`);
    } catch (error) {
      setOperationError(`${errorMessage(error)} Обновите очередь перед повтором.`);
      void reloadTriggers();
    } finally { setBusy(undefined); }
  }

  async function createWebhook(input: { url: string; events: string[]; project_id: string }) {
    setBusy("webhook:create"); clearFeedback(); setCreatedSecret(undefined); setSecretCopyMessage(undefined);
    let created: Awaited<ReturnType<AutomationService["createWebhook"]>>;
    try {
      created = await service.createWebhook(input);
    } catch (error) {
      setOperationError(`${errorMessage(error)} Проверьте список подписок перед повтором; повторный POST может создать дубликат.`);
      void reloadWebhooks();
      setBusy(undefined);
      throw error;
    }
    setCreatedSecret(created.secret);
    try {
      const persisted = await service.listWebhooks(projectId);
      setWebhooks({ status: "ready", items: persisted });
      setDeliveryProjectSubscriptionIds(new Set(persisted.map((item) => item.id)));
      const confirmed = persisted.find((item) => item.id === created.id);
      if (!confirmed) throw new Error("Webhook создан, но GET списка пока не подтвердил подписку.");
      setNotice(`Исходящая подписка для ${confirmed.url} подтверждена backend. Это не подтверждает ни одной delivery.`);
    } catch (error) {
      setOperationError(`${errorMessage(error)} Подписка создана; секрет можно скопировать сейчас. Не отправляйте повторный POST без проверки.`);
      void reloadWebhooks();
    } finally { setBusy(undefined); }
  }

  async function toggleWebhook(subscription: WebhookSubscription) {
    const operation = `webhook:${subscription.id}`;
    setBusy(operation); clearFeedback();
    try {
      await service.updateWebhook(subscription.id, { enabled: !subscription.enabled });
      const persisted = await service.listWebhooks(projectId);
      setWebhooks({ status: "ready", items: persisted });
      setDeliveryProjectSubscriptionIds(new Set(persisted.map((item) => item.id)));
      const confirmed = persisted.find((item) => item.id === subscription.id);
      if (!confirmed || confirmed.enabled === subscription.enabled) throw new Error("Изменение enabled не подтверждено GET списком подписок.");
      setNotice(`Состояние исходящего webhook подтверждено backend: ${confirmed.enabled ? "включён" : "выключен"}.`);
    } catch (error) {
      setOperationError(`${errorMessage(error)} Проверьте актуальный список перед повтором.`);
      void reloadWebhooks();
    } finally { setBusy(undefined); }
  }

  async function deleteWebhook(subscription: WebhookSubscription) {
    if (!window.confirm(`Удалить исходящий webhook ${subscription.url}?`)) return;
    const operation = `webhook:${subscription.id}`;
    setBusy(operation); clearFeedback();
    try {
      await service.deleteWebhook(subscription.id);
      const persisted = await service.listWebhooks(projectId);
      setWebhooks({ status: "ready", items: persisted });
      setDeliveryProjectSubscriptionIds(new Set(persisted.map((item) => item.id)));
      if (persisted.some((item) => item.id === subscription.id)) throw new Error("Подписка всё ещё присутствует в backend списке; удаление не подтверждено.");
      setNotice("Удаление исходящей webhook-подписки подтверждено backend.");
    } catch (error) {
      setOperationError(`${errorMessage(error)} Обновите список перед повтором.`);
      void reloadWebhooks();
    } finally { setBusy(undefined); }
  }

  async function retryWebhookDelivery(delivery: WebhookDelivery) {
    setBusy(`delivery:${delivery.id}`); clearFeedback();
    try {
      if (delivery.status !== "exhausted" || !deliveryProjectSubscriptionIds.has(delivery.subscription_id)) {
        throw new Error("Retry разрешён только для exhausted delivery подписки текущего проекта.");
      }
      const [subscriptions, beforeRetry] = await Promise.all([
        service.listWebhooks(projectId),
        service.listWebhookDeliveries(200),
      ]);
      const subscriptionIds = new Set(subscriptions.map((item) => item.id));
      setDeliveryProjectSubscriptionIds(subscriptionIds);
      const exact = beforeRetry.find((item) => item.id === delivery.id);
      if (!exact || exact.status !== "exhausted" || exact.subscription_id !== delivery.subscription_id || !subscriptionIds.has(exact.subscription_id)) {
        throw new Error("Актуальный ответ API не подтвердил exact delivery как exhausted и принадлежащую subscription текущего проекта; POST retry не отправлен.");
      }
      const result = await service.retryWebhookDelivery(delivery.id);
      const persisted = await service.listWebhookDeliveries(200);
      const confirmed = persisted.find((item) => item.id === delivery.id);
      const previousRetryCount = exact.retry_history.filter((entry) => entry.kind === "manual_retry").length;
      const currentRetryCount = confirmed?.retry_history.filter((entry) => entry.kind === "manual_retry").length ?? 0;
      if (!confirmed || confirmed.subscription_id !== exact.subscription_id || currentRetryCount <= previousRetryCount || result.id !== delivery.id || result.status !== "pending") {
        throw new Error("GET истории не подтвердил ручную попытку доставки после retry.");
      }
      setDeliveryReturnedCount(persisted.length);
      setDeliveries({ status: "ready", items: persisted });
      setNotice(`Ручной retry доставки ${delivery.id} подтверждён записью manual_retry в истории backend; текущее состояние: ${confirmed.status}.`);
    } catch (error) {
      setOperationError(`${errorMessage(error)} Обновите историю доставок перед повтором.`);
      void reloadDeliveries();
    } finally { setBusy(undefined); }
  }

  return <main className="automations-page">
    <header className="automations-header"><div><p className="automation-eyebrow">Project / {projectId}</p><h1>Автоматизации</h1><p>Управление расписаниями, очередью project triggers и исходящими webhook-подписками.</p></div><button type="button" className="automation-button automation-button--quiet" onClick={refreshAll} disabled={busy !== undefined}>Обновить данные</button></header>
    {notice && <div className="automation-notice automation-notice--success" role="status" aria-live="polite">{notice}</div>}
    {operationError && <div className="automation-notice automation-notice--error" role="alert"><div><strong>Изменение не подтверждено</strong><span>{operationError}</span></div><button type="button" className="automation-button automation-button--quiet" onClick={refreshAll}>Проверить состояние</button></div>}

    <section className="automation-panel" aria-labelledby="schedules-heading">
      <SectionHeader headingId="schedules-heading" eyebrow="01 / Cron" title="Расписания" detail="CRUD расписаний проекта. Сервер проверяет cron и timezone; запуск планировщика выполняется backend." action={<button type="button" className="automation-button automation-button--primary" onClick={() => { setEditingSchedule(undefined); setCreatingSchedule((value) => !value); clearFeedback(); }} disabled={busy !== undefined}>{creatingSchedule ? "Закрыть форму" : "＋ Новое расписание"}</button>} />
      {creatingSchedule && <ScheduleEditor key="new-schedule" busy={busy === "schedule:create"} onSave={saveSchedule} onCancel={() => setCreatingSchedule(false)} />}
      {editingSchedule && <ScheduleEditor key={editingSchedule.id} schedule={editingSchedule} busy={busy === `schedule:${editingSchedule.id}`} onSave={saveSchedule} onCancel={() => setEditingSchedule(undefined)} />}
      <CollectionState status={schedules.status} error={schedules.error} empty={schedules.items.length === 0 && !creatingSchedule && !editingSchedule} onRetry={() => void reloadSchedules()} label="расписания">
        {schedules.items.length === 0 ? "Создайте первое расписание проекта." : <div className="automation-card-grid">{schedules.items.map((schedule) => <ScheduleCard key={schedule.id} schedule={schedule} busy={busy === `schedule:${schedule.id}`} onEdit={() => { setCreatingSchedule(false); setEditingSchedule(schedule); clearFeedback(); }} onToggle={() => void toggleSchedule(schedule)} onDelete={() => void deleteSchedule(schedule)} />)}</div>}
      </CollectionState>
    </section>

    <section className="automation-panel" aria-labelledby="triggers-heading">
      <SectionHeader headingId="triggers-heading" eyebrow="02 / Queue" title="Project triggers" detail="События проекта и команды ставятся в очередь; dispatch забирает следующий доступный trigger, не выбирает конкретный id." action={<div className="automation-header-actions"><button type="button" className="automation-button automation-button--quiet" onClick={() => void reloadTriggers()} disabled={triggers.status === "loading"}>{triggers.status === "loading" ? "Обновляем…" : "Обновить очередь"}</button><button type="button" className="automation-button automation-button--primary" onClick={() => void dispatchNextTrigger()} disabled={busy !== undefined}>{busy === "trigger:dispatch" ? "Обрабатываем…" : "Обработать следующий"}</button></div>} />
      <TriggerComposer projectId={projectId} busy={busy === "trigger:create"} onEnqueue={enqueueTrigger} />
      <CollectionState status={triggers.status} error={triggers.error} empty={triggers.items.length === 0} onRetry={() => void reloadTriggers()} label="project triggers">
        {triggers.items.length === 0 ? "Очередь проекта пока пуста." : <div className="automation-card-grid">{triggers.items.map((trigger) => <TriggerCard key={trigger.id} trigger={trigger} />)}</div>}
      </CollectionState>
      <div className="automation-boundary-note automation-boundary-note--warning" role="note"><strong>Изменение project trigger не поддержано</strong><span>Очередь поддерживает enqueue, list и dispatch следующего доступного элемента; endpoint для редактирования, удаления или retry конкретного trigger отсутствует. Чтобы изменить событие, поставьте новое; старую запись эта UI не удалит.</span></div>
      <div className="automation-boundary-note" role="note"><strong>Agent Run history</strong><span>Список `/projects/:projectId/agent/runs` показывает durable Agent Runs, если gated backend доступен. Он не связывает run с project trigger; точный run outcome конкретного trigger остаётся неподтверждённым. Ручной dispatch не принимает trigger id.</span></div>
      <CollectionState status={runs.status} error={runs.error} empty={runs.items.length === 0} onRetry={() => void reloadRuns()} label="Agent Runs">
        {runs.items.length === 0 ? "Для проекта пока нет записей в Agent Runs." : <div className="automation-card-grid">{runs.items.map((run) => <article className="automation-card" aria-label={`Agent run ${run.id}`} key={run.id}><div className="automation-card-heading"><div><span className="automation-card-icon" aria-hidden="true">◉</span><div className="automation-card-title"><h3>{run.status}</h3><code>{run.id}</code></div></div><StatePill value={`Попытка ${run.attempt}`} /></div><dl className="automation-facts"><div><dt>Создан</dt><dd>{dateTime(run.created_at)}</dd></div><div><dt>Завершён</dt><dd>{dateTime(run.finished_at)}</dd></div><div><dt>Sequence</dt><dd>{run.sequence}</dd></div></dl>{(run.failure_code || run.failure_message) && <div className="automation-trigger-error" role="alert"><strong>{run.failure_code || "Ошибка Agent Run"}</strong><p>{run.failure_message || "Сообщение об ошибке не предоставлено."}</p></div>}{run.output_payload && <details className="automation-review"><summary>Результат Agent Run</summary><pre>{formatJson(run.output_payload)}</pre></details>}</article>)}</div>}
      </CollectionState>
      <div className="automation-form-actions"><span>История Agent Run отдельна от project-trigger queue; точная причинная связь trigger → run API не возвращает.</span><button type="button" className="automation-button automation-button--quiet" onClick={() => void reloadRuns()} disabled={runs.status === "loading"}>{runs.status === "loading" ? "Загружаем…" : "Обновить Agent Runs"}</button></div>
    </section>

    <section className="automation-panel" aria-labelledby="webhooks-heading">
      <SectionHeader headingId="webhooks-heading" eyebrow="03 / Outbound events" title="Исходящие webhooks" detail="Список подписок отфильтрован по проекту. Delivery history доступна только как company-wide срез; project view показывает совпадения subscription_id, а retry разрешён только для подтверждённой подписки проекта." action={<button type="button" className="automation-button automation-button--quiet" onClick={() => { void reloadWebhooks(); void reloadDeliveries(); }} disabled={webhooks.status === "loading" || deliveries.status === "loading"}>{webhooks.status === "loading" || deliveries.status === "loading" ? "Обновляем…" : "Обновить подписки и доставки"}</button>} />
      {createdSecret && <div className="automation-secret-note" role="status"><strong>Секрет доступен только сейчас</strong><span>После создания скопируйте секрет в доверенное хранилище. Значение не выводится на экран и повторно из API не читается.</span><button type="button" className="automation-button automation-button--primary" onClick={() => { void copyOneTimeSecret(createdSecret).then(() => { setSecretCopyMessage("Секрет скопирован в буфер обмена."); setCreatedSecret(undefined); }, () => setSecretCopyMessage("Не удалось скопировать. Повторите попытку сейчас; значение останется скрытым.")); }} aria-label="Скопировать одноразовый секрет">Скопировать одноразовый секрет</button><button type="button" className="automation-button automation-button--quiet" onClick={() => { setCreatedSecret(undefined); setSecretCopyMessage("Секрет скрыт. Повторно получить его нельзя."); }}>Скрыть</button>{secretCopyMessage && <span role="status">{secretCopyMessage}</span>}</div>}
      {!createdSecret && secretCopyMessage && <p className="automation-capability-hint" role="status">{secretCopyMessage}</p>}
      <WebhookComposer projectId={projectId} busy={busy === "webhook:create"} onCreate={createWebhook} />
      <CollectionState status={webhooks.status} error={webhooks.error} empty={webhooks.items.length === 0} onRetry={() => void reloadWebhooks()} label="webhook-подписки">
        {webhooks.items.length === 0 ? "Для проекта пока нет исходящих подписок." : <div className="automation-card-grid">{webhooks.items.map((subscription) => <WebhookCard key={subscription.id} subscription={subscription} busy={busy === `webhook:${subscription.id}`} onToggle={() => void toggleWebhook(subscription)} onDelete={() => void deleteWebhook(subscription)} />)}</div>}
      </CollectionState>
      <SectionHeader eyebrow="Delivery log" title="История исходящих доставок" detail="Company-wide список последних записей delivery; project attribution неполная, потому что delivery response не содержит project_id. Совпадение subscription_id с project-filtered списком подписок даёт только known matches, но весь список не является историей одного проекта." />
      {deliveries.status === "ready" && <div className="automation-delivery-summary" role="note">
        <strong>Получено от API: {deliveryReturnedCount} из максимум 200 строк.</strong>
        {webhooks.status === "ready"
          ? <span>{deliveries.items.filter((item) => deliveryProjectSubscriptionIds.has(item.subscription_id)).length} строк совпадают по subscription_id с подписками этого проекта; {Math.max(0, deliveryReturnedCount - deliveries.items.filter((item) => deliveryProjectSubscriptionIds.has(item.subscription_id)).length)} строк не связаны с этим project-filtered списком. Несовпадение не определяет проект таких строк.</span>
          : <span>Project attribution по subscription_id неполная: список подписок проекта пока не подтверждён, поэтому совпадения не рассчитаны.</span>}
        <span>Endpoint возвращает только недавний срез без total или пагинации. Более старые записи сверх лимита не включаются; точный размер оставшейся истории API не сообщает. В режиме совпадений строки без project-subscription membership опущены, и их проект неизвестен.</span>
      </div>}
      <div className="automation-delivery-scope" role="group" aria-label="Область истории доставок">
        <button type="button" className={deliveryScope === "company" ? "is-active" : ""} aria-pressed={deliveryScope === "company"} onClick={() => setDeliveryScope("company")}>Company-wide ({deliveries.items.length})</button>
        <button type="button" className={deliveryScope === "project" ? "is-active" : ""} aria-pressed={deliveryScope === "project"} disabled={webhooks.status !== "ready"} onClick={() => setDeliveryScope("project")}>Совпадения с подписками проекта ({webhooks.status === "ready" ? deliveries.items.filter((item) => deliveryProjectSubscriptionIds.has(item.subscription_id)).length : "—"})</button>
      </div>
      <CollectionState status={deliveries.status} error={deliveries.error} empty={deliveries.items.length === 0} onRetry={() => void reloadDeliveries()} label="историю доставок">
        {deliveries.items.length === 0 ? "В недавнем company-wide срезе нет delivery." : (() => {
          if (deliveryScope === "project" && webhooks.status !== "ready") return <p>Project attribution по subscription ID недоступна, пока project-filtered список подписок не подтверждён.</p>;
          const shownDeliveries = deliveryScope === "company" ? deliveries.items : deliveries.items.filter((item) => deliveryProjectSubscriptionIds.has(item.subscription_id));
          return shownDeliveries.length === 0
            ? <p>В полученном company-wide срезе нет строк с subscription_id из списка этого проекта.</p>
            : <div className="automation-card-grid">{shownDeliveries.map((delivery) => <DeliveryCard key={delivery.id} delivery={delivery} busy={busy === `delivery:${delivery.id}`} retryAllowed={webhooks.status === "ready" && deliveryProjectSubscriptionIds.has(delivery.subscription_id)} onRetry={() => void retryWebhookDelivery(delivery)} />)}</div>;
        })()}
      </CollectionState>
      <div className="automation-boundary-note automation-boundary-note--warning" role="note"><strong>Доступность Agent Run history зависит от backend gate</strong><span>GET `/projects/{projectId}/agent/runs` требует включённого durable Agent Run API и готовой persistence-схемы; при blocker он показан как ошибка загрузки, без подмены данных.</span></div>
    </section>

    <section className="automation-panel automation-ingress-panel" aria-labelledby="ingress-heading">
      <SectionHeader headingId="ingress-heading" eyebrow="04 / Inbound events" title="Ingress — это отдельный поток" detail="Signed incoming hooks принимают внешние события в проектную очередь; это не исходящая webhook-подписка." />
      <div className="automation-ingress-grid"><article><StatePill value="Входящий поток" tone="warning" /><h3>Project trigger ingress</h3><p>`webhook.http` и `telegram.message` подписываются входящим HMAC-контрактом в project trigger queue. Они не принадлежат outbound subscriptions и не отправляют события получателю.</p></article><article><StatePill value="Исходящий поток" tone="positive" /><h3>Webhook subscription</h3><p>`/external/webhooks` подписывает получателя на события завершения/ошибки хода. Subscription отправляет события наружу и не принимает внешний trigger.</p></article></div>
      <p className="automation-capability-hint">Управление company ingress-секретами (`GET /external/webhooks/incoming`, `POST/DELETE /external/webhooks/incoming/{channel}/secret`) не входит в эту project-scoped UI. Настройку необходимо выполнять в предусмотренной company settings; здесь входящий trigger и исходящая подписка не смешиваются.</p>
    </section>
    <footer className="automation-footer">Состояния и сообщения об ошибках отражают данные backend. Run, delivery и ingress-секреты не подменяются локальной симуляцией.</footer>
  </main>;
}

export function AutomationsPage({ projectId, service }: AutomationsPageProps) {
  const { tokens } = useAuth();
  const defaultService = useMemo(() => createDefaultAutomationService(() => tokens?.access_token), [tokens?.access_token]);
  return <AutomationsView projectId={projectId} service={service ?? defaultService} />;
}

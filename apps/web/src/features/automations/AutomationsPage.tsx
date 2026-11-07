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
import { useI18n } from "../../i18n/useT";
import type { Translate } from "../../i18n/catalogue";
import type { Locale } from "../../preferences/settings";
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

/** The event kinds, in the words a person schedules them by. */
const TRIGGER_KIND_KEYS: Record<ProjectTriggerKind, string> = {
  "chat.message": "automations.kindChatMessage",
  "chat.regenerate": "automations.kindChatRegenerate",
  "project.prepare": "automations.kindProjectPrepare",
  "system.schedule": "automations.kindSystemSchedule",
  "webhook.http": "automations.kindWebhookHttp",
  "telegram.message": "automations.kindTelegramMessage",
};

function triggerKindLabel(t: Translate, kind: string): string {
  const key = TRIGGER_KIND_KEYS[kind as ProjectTriggerKind];
  return key ? t(key) : t("automations.kindOther");
}

/**
 * The two events a subscription can carry, in words. The event name itself is
 * still shown beside the checkbox as a code chip, because that is what the
 * receiving side has to match on.
 */
function eventWord(t: Translate, eventType: string): string {
  if (eventType === "chat.message.completed") return t("automations.eventChatCompleted");
  if (eventType === "chat.turn.failed") return t("automations.eventChatFailed");
  return eventType;
}

function errorMessage(t: Translate, error: unknown): string {
  if (error instanceof AutomationRequestError) {
    const support = error.traceId ? ` ${t("automations.supportCode", { id: error.traceId })}` : "";
    return `${error.message}${support}`;
  }
  return error instanceof Error ? error.message : t("automations.errorUnknown");
}

function dateTime(value: string | null | undefined, locale: Locale, t: Translate): string {
  if (!value || !Number.isFinite(Date.parse(value))) return t("automations.notProvided");
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
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

async function copyOneTimeSecret(t: Translate, value: string): Promise<void> {
  if (!navigator.clipboard?.writeText) throw new Error(t("automations.errorClipboard"));
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

function CollectionState({ status, error, empty, children, onRetry, loadingLabel, unavailableLabel }: {
  status: AutomationCollection<unknown>["status"];
  error?: string;
  empty: boolean;
  children: ReactNode;
  onRetry: () => void;
  loadingLabel: string;
  unavailableLabel: string;
}) {
  const { t } = useI18n();
  if (status === "loading") return <div className="automation-state" role="status" aria-live="polite"><span className="automation-spinner" aria-hidden="true" /><strong>{loadingLabel}</strong><span>{t("automations.stateLoadingDetail")}</span></div>;
  if (status === "error") return <div className="automation-state automation-state--error" role="alert"><strong>{unavailableLabel}</strong><span>{error || t("automations.stateLoadFailed")}</span><button type="button" className="automation-button automation-button--quiet" onClick={onRetry}>{t("automations.stateRetry")}</button></div>;
  if (empty) return <div className="automation-state"><span className="automation-state-icon" aria-hidden="true">⌁</span><strong>{t("automations.stateEmptyTitle")}</strong><span>{children}</span></div>;
  return <>{children}</>;
}

function ScheduleEditor({ schedule, busy, onSave, onCancel }: {
  schedule?: Schedule;
  busy: boolean;
  onSave: (input: ScheduleInput) => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useI18n();
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
    if (!cleanName) { setError(t("automations.errorNameRequired")); return; }
    if (cleanCron.split(" ").length !== 5 || cleanCron.length < 9) { setError(t("automations.errorCronShape")); return; }
    if (!cleanTimezone) { setError(t("automations.errorTimezoneRequired")); return; }
    try { new Intl.DateTimeFormat("en", { timeZone: cleanTimezone }); }
    catch { setError(t("automations.errorTimezoneUnknown")); return; }
    let payload: JsonObject;
    try { payload = parseJsonObject(payloadText); }
    catch { setError(t("automations.errorJson")); return; }
    await onSave({ name: cleanName, cron: cleanCron, timezone: cleanTimezone, payload, enabled });
  }

  return <form className="automation-form automation-schedule-form" onSubmit={(event) => void submit(event)} aria-label={schedule ? t("automations.scheduleEditAria", { name: schedule.name }) : t("automations.scheduleNewAria")}>
    <div className="automation-form-grid">
      <label>{t("automations.fieldName")}<input value={name} onChange={(event) => setName(event.target.value)} required maxLength={200} /></label>
      <label>{t("automations.fieldCron")}<input value={cron} onChange={(event) => setCron(event.target.value)} required minLength={9} maxLength={120} aria-describedby="automation-cron-help" /><small id="automation-cron-help">{t("automations.cronHelp")}</small></label>
      <label>{t("automations.fieldTimezone")}<input value={timezone} onChange={(event) => setTimezone(event.target.value)} required maxLength={64} placeholder={t("automations.timezonePlaceholder")} /></label>
      <label className="automation-checkbox"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span>{t("automations.fieldEnabled")}</span></label>
      <label className="automation-field-wide">{t("automations.fieldPayload")}<textarea rows={5} spellCheck={false} value={payloadText} onChange={(event) => setPayloadText(event.target.value)} aria-describedby="automation-payload-help" /><small id="automation-payload-help">{t("automations.payloadHelp")}</small></label>
    </div>
    {error && <p className="automation-inline-error" role="alert">{error}</p>}
    <div className="automation-form-actions"><button type="button" className="automation-button automation-button--quiet" onClick={onCancel} disabled={busy}>{t("common.cancel")}</button><button type="submit" className="automation-button automation-button--primary" disabled={busy}>{busy ? t("common.saving") : schedule ? t("automations.saveSchedule") : t("automations.createSchedule")}</button></div>
  </form>;
}

function ScheduleCard({ schedule, busy, onEdit, onToggle, onDelete }: {
  schedule: Schedule;
  busy: boolean;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const { locale, t } = useI18n();
  return <article className="automation-card" aria-label={t("automations.scheduleAria", { name: schedule.name })}>
    <div className="automation-card-heading"><div><span className="automation-card-icon" aria-hidden="true">◷</span><div className="automation-card-title"><h3>{schedule.name}</h3><code>{schedule.cron}</code></div></div><StatePill value={schedule.enabled ? t("automations.enabled") : t("automations.disabled")} tone={schedule.enabled ? "positive" : "neutral"} /></div>
    <dl className="automation-facts"><div><dt>{t("automations.fieldTimezone")}</dt><dd>{schedule.timezone}</dd></div><div><dt>{t("automations.nextRun")}</dt><dd>{schedule.enabled ? dateTime(schedule.next_run_at, locale, t) : t("automations.switchedOff")}</dd></div><div><dt>{t("automations.lastRun")}</dt><dd>{dateTime(schedule.last_run_at, locale, t)}</dd></div></dl>
    <p className="automation-capability-hint">{t("automations.scheduleHint")}</p>
    <details className="automation-review"><summary>{t("automations.reviewPayload")}</summary><pre>{formatJson(schedule.payload)}</pre></details>
    <div className="automation-card-actions"><button type="button" className="automation-button automation-button--quiet" onClick={onToggle} disabled={busy} aria-label={schedule.enabled ? t("automations.toggleOffAria", { name: schedule.name }) : t("automations.toggleOnAria", { name: schedule.name })}>{busy ? t("automations.updating") : schedule.enabled ? t("automations.toggleOff") : t("automations.toggleOn")}</button><button type="button" className="automation-button automation-button--quiet" onClick={onEdit} disabled={busy}>{t("common.edit")}</button><button type="button" className="automation-button automation-button--danger-quiet" onClick={onDelete} disabled={busy}>{t("common.delete")}</button></div>
  </article>;
}

function TriggerStatus({ trigger }: { trigger: ProjectTrigger }) {
  const { t } = useI18n();
  if (trigger.status === "queued") return <StatePill value={statusWord(t, trigger.status)} tone="warning" />;
  if (trigger.status === "done") return <StatePill value={statusWord(t, trigger.status)} tone="positive" />;
  if (trigger.status === "failed") return <StatePill value={statusWord(t, trigger.status)} tone="danger" />;
  return <StatePill value={statusWord(t, trigger.status)} />;
}

/** Queue states in words, so `queued` never reaches the screen on its own. */
function statusWord(t: Translate, status: string): string {
  if (status === "queued") return t("automations.triggerQueued");
  if (status === "done") return t("automations.triggerDone");
  if (status === "failed") return t("automations.triggerFailed");
  return t("automations.triggerStatusUnknown");
}

function runStatusWord(t: Translate, status: AgentRun["status"]): string {
  const keys: Record<AgentRun["status"], string> = {
    queued: "automations.runQueued",
    running: "automations.runRunning",
    waiting_approval: "automations.runWaitingApproval",
    succeeded: "automations.runSucceeded",
    failed: "automations.runFailed",
    cancelled: "automations.runCancelled",
  };
  return t(keys[status] ?? "automations.runStatusUnknown");
}

function TriggerCard({ trigger }: { trigger: ProjectTrigger }) {
  const { locale, t } = useI18n();
  const origin = SCHEDULE_EVENT_KINDS.has(trigger.kind)
    ? t("automations.originSchedule")
    : INBOUND_EVENT_KINDS.has(trigger.kind)
      ? t("automations.originInbound")
      : t("automations.originCommand");
  return <article className="automation-card automation-trigger-card" aria-label={t("automations.triggerAria", { kind: triggerKindLabel(t, trigger.kind) })}>
    <div className="automation-card-heading"><div><span className="automation-card-icon" aria-hidden="true">↗</span><div className="automation-card-title"><h3>{triggerKindLabel(t, trigger.kind)}</h3><code>{origin}</code></div></div><TriggerStatus trigger={trigger} /></div>
    <dl className="automation-facts automation-facts--trigger"><div><dt>{t("automations.created")}</dt><dd>{dateTime(trigger.created_at, locale, t)}</dd></div><div><dt>{t("automations.attempts")}</dt><dd>{trigger.attempts === undefined ? t("automations.notProvided") : trigger.attempts}</dd></div><div><dt>{t("automations.nextAttempt")}</dt><dd>{dateTime(trigger.available_at, locale, t)}</dd></div></dl>
    {trigger.last_error && <div className="automation-trigger-error" role="alert"><strong>{t("automations.lastError")}</strong><p>{trigger.last_error}</p></div>}
    {trigger.status === "failed" && <p className="automation-capability-hint">{t("automations.triggerFailedHint")}</p>}
    <details className="automation-review"><summary>{t("automations.reviewEvent")}</summary><pre>{formatJson(trigger.payload)}</pre><dl><div><dt>{t("automations.lockedUntil")}</dt><dd>{dateTime(trigger.lease_until, locale, t)}</dd></div><div><dt>{t("automations.handler")}</dt><dd>{trigger.leased_by || t("automations.notProvided")}</dd></div></dl></details>
  </article>;
}

function TriggerComposer({ busy, onEnqueue }: { busy: boolean; onEnqueue: (kind: ProjectTriggerKind, payload: JsonObject) => Promise<void> }) {
  const { t } = useI18n();
  const [kind, setKind] = useState<ProjectTriggerKind>("chat.message");
  const [payloadText, setPayloadText] = useState("{\n  \"text\": \"\"\n}");
  const [error, setError] = useState<string>();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    let payload: JsonObject;
    try { payload = parseJsonObject(payloadText); }
    catch { setError(t("automations.errorJson")); return; }
    await onEnqueue(kind, payload);
  }
  return <form className="automation-form automation-trigger-composer" onSubmit={(event) => void submit(event)}>
    <div className="automation-form-grid"><label>{t("automations.fieldKind")}<select value={kind} onChange={(event) => setKind(event.target.value as ProjectTriggerKind)}><optgroup label={t("automations.groupCommands")}>{PROJECT_TRIGGER_KINDS.filter((value) => PROJECT_COMMAND_KINDS.has(value)).map((value) => <option value={value} key={value}>{triggerKindLabel(t, value)}</option>)}</optgroup><optgroup label={t("automations.groupScheduleInbound")}>{PROJECT_TRIGGER_KINDS.filter((value) => SCHEDULE_EVENT_KINDS.has(value) || INBOUND_EVENT_KINDS.has(value)).map((value) => <option value={value} key={value}>{triggerKindLabel(t, value)}</option>)}</optgroup></select></label><label className="automation-field-wide">{t("automations.fieldPayload")}<textarea rows={4} spellCheck={false} value={payloadText} onChange={(event) => setPayloadText(event.target.value)} /></label></div>
    {error && <p className="automation-inline-error" role="alert">{error}</p>}
    <div className="automation-form-actions"><span>{t("automations.enqueueHint")}</span><button type="submit" className="automation-button automation-button--primary" disabled={busy}>{busy ? t("automations.enqueuing") : t("automations.enqueue")}</button></div>
  </form>;
}

function WebhookComposer({ projectId, busy, onCreate }: { projectId: string; busy: boolean; onCreate: (input: { url: string; events: string[]; project_id: string }) => Promise<void> }) {
  const { t } = useI18n();
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>([SUPPORTED_WEBHOOK_EVENTS[0]]);
  const [error, setError] = useState<string>();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    if (!events.length) { setError(t("automations.errorNoEvents")); return; }
    try { await onCreate({ url: url.trim(), events, project_id: projectId }); setUrl(""); }
    catch { setError(t("automations.errorEnqueueFailed")); }
  }
  return <form className="automation-form automation-webhook-form" onSubmit={(event) => void submit(event)}>
    <div className="automation-form-grid"><label className="automation-field-wide">{t("automations.fieldUrl")}<input type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder={t("automations.urlPlaceholder")} required maxLength={2000} /></label><fieldset className="automation-field-wide automation-event-options"><legend>{t("automations.fieldEvents")}</legend>{SUPPORTED_WEBHOOK_EVENTS.map((eventName) => <label className="automation-checkbox" key={eventName}><input type="checkbox" checked={events.includes(eventName)} onChange={(event) => setEvents((current) => event.target.checked ? [...current, eventName] : current.filter((item) => item !== eventName))} /><span>{eventWord(t, eventName)}</span> <code>{eventName}</code></label>)}</fieldset></div>
    {error && <p className="automation-inline-error" role="alert">{error}</p>}
    <div className="automation-form-actions"><span>{t("automations.webhookHint")}</span><button type="submit" className="automation-button automation-button--primary" disabled={busy}>{busy ? t("automations.creating") : t("automations.createWebhook")}</button></div>
  </form>;
}

function WebhookCard({ subscription, busy, onToggle, onDelete }: { subscription: WebhookSubscription; busy: boolean; onToggle: () => void; onDelete: () => void }) {
  const { locale, t } = useI18n();
  return <article className="automation-card" aria-label={t("automations.webhookAria", { url: subscription.url })}>
    <div className="automation-card-heading"><div><span className="automation-card-icon" aria-hidden="true">⇧</span><div className="automation-card-title"><h3>{subscription.url}</h3></div></div><StatePill value={subscription.enabled ? t("automations.webhookOn") : t("automations.webhookOff")} tone={subscription.enabled ? "positive" : "neutral"} /></div>
    <div className="automation-event-list" aria-label={t("automations.eventsLabel")}>{subscription.events.map((eventName) => <code key={eventName}>{eventName}</code>)}</div>
    <dl className="automation-facts"><div><dt>{t("automations.failCount")}</dt><dd>{subscription.fail_count === undefined ? t("automations.notProvided") : subscription.fail_count}</dd></div><div><dt>{t("automations.created")}</dt><dd>{dateTime(subscription.created_at, locale, t)}</dd></div></dl>
    <p className="automation-capability-hint">{t("automations.webhookHintCount")}</p>
    <div className="automation-card-actions"><button type="button" className="automation-button automation-button--quiet" onClick={onToggle} disabled={busy} aria-label={subscription.enabled ? t("automations.webhookTurnOffAria", { url: subscription.url }) : t("automations.webhookTurnOnAria", { url: subscription.url })}>{busy ? t("automations.updating") : subscription.enabled ? t("automations.webhookTurnOff") : t("automations.webhookTurnOn")}</button><button type="button" className="automation-button automation-button--danger-quiet" onClick={onDelete} disabled={busy}>{t("common.delete")}</button></div>
  </article>;
}

function DeliveryState({ status }: { status: string }) {
  const { t } = useI18n();
  if (status === "delivered") return <StatePill value={t("automations.deliveryDelivered")} tone="positive" />;
  if (status === "exhausted") return <StatePill value={t("automations.deliveryExhausted")} tone="danger" />;
  if (status === "pending") return <StatePill value={t("automations.deliveryPending")} tone="warning" />;
  return <StatePill value={t("automations.deliveryStatusUnknown")} />;
}

function DeliveryCard({ delivery, busy, retryAllowed, onRetry }: { delivery: WebhookDelivery; busy: boolean; retryAllowed: boolean; onRetry: () => void }) {
  const { t } = useI18n();
  return <article className="automation-card" aria-label={t("automations.deliveryAria")}>
    <div className="automation-card-heading"><div><span className="automation-card-icon" aria-hidden="true">⇧</span><div className="automation-card-title"><h3>{eventWord(t, delivery.event_type)}</h3><code>{delivery.event_type}</code></div></div><DeliveryState status={delivery.status} /></div>
    <dl className="automation-facts"><div><dt>{t("automations.recipient")}</dt><dd>{delivery.url}</dd></div><div><dt>{t("automations.attempts")}</dt><dd>{delivery.attempts}</dd></div><div><dt>{t("automations.httpResponse")}</dt><dd>{delivery.response_status ?? t("automations.notProvided")}</dd></div></dl>
    {delivery.last_error && <div className="automation-trigger-error" role="alert"><strong>{t("automations.lastDeliveryError")}</strong><p>{delivery.last_error}</p></div>}
    <details className="automation-review"><summary>{t("automations.attemptHistory")}</summary><pre>{JSON.stringify(delivery.retry_history, null, 2)}</pre></details>
    {delivery.status === "exhausted" && (retryAllowed
      ? <div className="automation-card-actions"><button type="button" className="automation-button automation-button--quiet" onClick={onRetry} disabled={busy}>{busy ? t("automations.retrying") : t("automations.retryDelivery")}</button></div>
      : <p className="automation-capability-hint">{t("automations.retryBlocked")}</p>)}
  </article>;
}

export function AutomationsView({ projectId, service }: AutomationsViewProps) {
  const { locale, t } = useI18n();
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
    catch (error) { setSchedules((current) => ({ ...current, status: "error", error: errorMessage(t, error) })); }
  }, [projectId, service, t]);

  const reloadTriggers = useCallback(async () => {
    setTriggers((current) => ({ ...current, status: "loading", error: undefined }));
    try { setTriggers({ status: "ready", items: await service.listTriggers(projectId) }); }
    catch (error) { setTriggers((current) => ({ ...current, status: "error", error: errorMessage(t, error) })); }
  }, [projectId, service, t]);

  const reloadRuns = useCallback(async () => {
    setRuns((current) => ({ ...current, status: "loading", error: undefined }));
    try { setRuns({ status: "ready", items: await service.listAgentRuns(projectId) }); }
    catch (error) { setRuns((current) => ({ ...current, status: "error", error: errorMessage(t, error) })); }
  }, [projectId, service, t]);

  const reloadWebhooks = useCallback(async () => {
    setWebhooks((current) => ({ ...current, status: "loading", error: undefined }));
    try {
      const items = await service.listWebhooks(projectId);
      setWebhooks({ status: "ready", items });
      setDeliveryProjectSubscriptionIds(new Set(items.map((item) => item.id)));
    }
    catch (error) { setWebhooks((current) => ({ ...current, status: "error", error: errorMessage(t, error) })); }
  }, [projectId, service, t]);

  const reloadDeliveries = useCallback(async () => {
    setDeliveries((current) => ({ ...current, status: "loading", error: undefined }));
    try {
      const recent = await service.listWebhookDeliveries(200);
      setDeliveryReturnedCount(recent.length);
      setDeliveries({ status: "ready", items: recent });
    } catch (error) { setDeliveries((current) => ({ ...current, status: "error", error: errorMessage(t, error) })); }
  }, [projectId, service, t]);

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
        throw new Error(t("automations.noticeScheduleMismatch"));
      }
      setNotice(editingSchedule ? t("automations.noticeScheduleSaved", { name: confirmed.name }) : t("automations.noticeScheduleCreated", { name: confirmed.name }));
      setEditingSchedule(undefined); setCreatingSchedule(false);
    } catch (error) {
      setOperationError(`${errorMessage(t, error)} ${t("automations.checkSchedules")}`);
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
      if (!confirmed || confirmed.enabled === schedule.enabled) throw new Error(t("automations.noticeToggleMismatch"));
      setNotice(confirmed.enabled ? t("automations.noticeScheduleOn", { name: confirmed.name }) : t("automations.noticeScheduleOff", { name: confirmed.name }));
    } catch (error) {
      setOperationError(`${errorMessage(t, error)} ${t("automations.checkScheduleState")}`);
      void reloadSchedules();
    } finally { setBusy(undefined); }
  }

  async function deleteSchedule(schedule: Schedule) {
    if (!window.confirm(t("automations.confirmDeleteSchedule", { name: schedule.name }))) return;
    const operation = `schedule:${schedule.id}`;
    setBusy(operation); clearFeedback();
    try {
      await service.deleteSchedule(projectId, schedule.id);
      const persisted = await service.listSchedules(projectId);
      setSchedules({ status: "ready", items: persisted });
      if (persisted.some((item) => item.id === schedule.id)) throw new Error(t("automations.noticeScheduleStillThere"));
      setNotice(t("automations.noticeScheduleDeleted", { name: schedule.name }));
    } catch (error) {
      setOperationError(`${errorMessage(t, error)} ${t("automations.refreshBeforeRetry")}`);
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
      if (!confirmed) throw new Error(t("automations.noticeTriggerMissing"));
      setNotice(t("automations.noticeTriggerQueued", { kind: triggerKindLabel(t, confirmed.kind), state: statusWord(t, confirmed.status) }));
    } catch (error) {
      setOperationError(`${errorMessage(t, error)} ${t("automations.checkTriggers")}`);
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
        ? t("automations.noticeDispatched")
        : t("automations.noticeNothingToDispatch", { reason: result.reason ?? t("automations.notProvided") }));
    } catch (error) {
      setOperationError(`${errorMessage(t, error)} ${t("automations.checkTriggers")}`);
      void reloadTriggers();
    } finally { setBusy(undefined); }
  }

  async function createWebhook(input: { url: string; events: string[]; project_id: string }) {
    setBusy("webhook:create"); clearFeedback(); setCreatedSecret(undefined); setSecretCopyMessage(undefined);
    let created: Awaited<ReturnType<AutomationService["createWebhook"]>>;
    try {
      created = await service.createWebhook(input);
    } catch (error) {
      setOperationError(`${errorMessage(t, error)} ${t("automations.checkWebhooksBeforeRepeat")}`);
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
      if (!confirmed) throw new Error(t("automations.noticeWebhookMissing"));
      setNotice(t("automations.noticeWebhookCreated", { url: confirmed.url }));
    } catch (error) {
      setOperationError(`${errorMessage(t, error)} ${t("automations.secretStillAvailable")}`);
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
      if (!confirmed || confirmed.enabled === subscription.enabled) throw new Error(t("automations.noticeToggleMismatch"));
      setNotice(confirmed.enabled ? t("automations.noticeWebhookOn") : t("automations.noticeWebhookOff"));
    } catch (error) {
      setOperationError(`${errorMessage(t, error)} ${t("automations.checkWebhooksBeforeRepeat")}`);
      void reloadWebhooks();
    } finally { setBusy(undefined); }
  }

  async function deleteWebhook(subscription: WebhookSubscription) {
    if (!window.confirm(t("automations.confirmDeleteWebhook", { url: subscription.url }))) return;
    const operation = `webhook:${subscription.id}`;
    setBusy(operation); clearFeedback();
    try {
      await service.deleteWebhook(subscription.id);
      const persisted = await service.listWebhooks(projectId);
      setWebhooks({ status: "ready", items: persisted });
      setDeliveryProjectSubscriptionIds(new Set(persisted.map((item) => item.id)));
      if (persisted.some((item) => item.id === subscription.id)) throw new Error(t("automations.noticeWebhookStillThere"));
      setNotice(t("automations.noticeWebhookDeleted"));
    } catch (error) {
      setOperationError(`${errorMessage(t, error)} ${t("automations.refreshBeforeRetry")}`);
      void reloadWebhooks();
    } finally { setBusy(undefined); }
  }

  async function retryWebhookDelivery(delivery: WebhookDelivery) {
    setBusy(`delivery:${delivery.id}`); clearFeedback();
    try {
      if (delivery.status !== "exhausted" || !deliveryProjectSubscriptionIds.has(delivery.subscription_id)) {
        throw new Error(t("automations.noticeRetryNotAllowed"));
      }
      const [subscriptions, beforeRetry] = await Promise.all([
        service.listWebhooks(projectId),
        service.listWebhookDeliveries(200),
      ]);
      const subscriptionIds = new Set(subscriptions.map((item) => item.id));
      setDeliveryProjectSubscriptionIds(subscriptionIds);
      const exact = beforeRetry.find((item) => item.id === delivery.id);
      if (!exact || exact.status !== "exhausted" || exact.subscription_id !== delivery.subscription_id || !subscriptionIds.has(exact.subscription_id)) {
        throw new Error(t("automations.noticeRetryStale"));
      }
      const result = await service.retryWebhookDelivery(delivery.id);
      const persisted = await service.listWebhookDeliveries(200);
      const confirmed = persisted.find((item) => item.id === delivery.id);
      const previousRetryCount = exact.retry_history.filter((entry) => entry.kind === "manual_retry").length;
      const currentRetryCount = confirmed?.retry_history.filter((entry) => entry.kind === "manual_retry").length ?? 0;
      if (!confirmed || confirmed.subscription_id !== exact.subscription_id || currentRetryCount <= previousRetryCount || result.id !== delivery.id || result.status !== "pending") {
        throw new Error(t("automations.noticeRetryUnconfirmed"));
      }
      setDeliveryReturnedCount(persisted.length);
      setDeliveries({ status: "ready", items: persisted });
      setNotice(t("automations.noticeRetryStarted", { state: statusWord(t, confirmed.status) }));
    } catch (error) {
      setOperationError(`${errorMessage(t, error)} ${t("automations.refreshDeliveries")}`);
      void reloadDeliveries();
    } finally { setBusy(undefined); }
  }

  const projectDeliveries = deliveries.items.filter((item) => deliveryProjectSubscriptionIds.has(item.subscription_id));
  const shownDeliveries = deliveryScope === "company" ? deliveries.items : projectDeliveries;

  return <main className="automations-page">
    <header className="automations-header"><div><p className="automation-eyebrow">{t("automations.eyebrow")}</p><h1>{t("automations.title")}</h1><p>{t("automations.subtitle")}</p></div><button type="button" className="automation-button automation-button--quiet" onClick={refreshAll} disabled={busy !== undefined}>{t("automations.refresh")}</button></header>
    {notice && <div className="automation-notice automation-notice--success" role="status" aria-live="polite">{notice}</div>}
    {operationError && <div className="automation-notice automation-notice--error" role="alert"><div><strong>{t("automations.noticeErrorTitle")}</strong><span>{operationError}</span></div><button type="button" className="automation-button automation-button--quiet" onClick={refreshAll}>{t("automations.checkState")}</button></div>}

    <section className="automation-panel" aria-labelledby="schedules-heading">
      <SectionHeader headingId="schedules-heading" eyebrow={t("automations.schedulesEyebrow")} title={t("automations.schedulesTitle")} detail={t("automations.schedulesDetail")} action={<button type="button" className="automation-button automation-button--primary" onClick={() => { setEditingSchedule(undefined); setCreatingSchedule((value) => !value); clearFeedback(); }} disabled={busy !== undefined}>{creatingSchedule ? t("automations.closeForm") : t("automations.newSchedule")}</button>} />
      {creatingSchedule && <ScheduleEditor key="new-schedule" busy={busy === "schedule:create"} onSave={saveSchedule} onCancel={() => setCreatingSchedule(false)} />}
      {editingSchedule && <ScheduleEditor key={editingSchedule.id} schedule={editingSchedule} busy={busy === `schedule:${editingSchedule.id}`} onSave={saveSchedule} onCancel={() => setEditingSchedule(undefined)} />}
      <CollectionState status={schedules.status} error={schedules.error} empty={schedules.items.length === 0 && !creatingSchedule && !editingSchedule} onRetry={() => void reloadSchedules()} loadingLabel={t("automations.schedulesLoading")} unavailableLabel={t("automations.schedulesUnavailable")}>
        {schedules.items.length === 0 ? t("automations.schedulesEmpty") : <div className="automation-card-grid">{schedules.items.map((schedule) => <ScheduleCard key={schedule.id} schedule={schedule} busy={busy === `schedule:${schedule.id}`} onEdit={() => { setCreatingSchedule(false); setEditingSchedule(schedule); clearFeedback(); }} onToggle={() => void toggleSchedule(schedule)} onDelete={() => void deleteSchedule(schedule)} />)}</div>}
      </CollectionState>
    </section>

    <section className="automation-panel" aria-labelledby="triggers-heading">
      <SectionHeader headingId="triggers-heading" eyebrow={t("automations.triggersEyebrow")} title={t("automations.triggersTitle")} detail={t("automations.triggersDetail")} action={<div className="automation-header-actions"><button type="button" className="automation-button automation-button--quiet" onClick={() => void reloadTriggers()} disabled={triggers.status === "loading"}>{triggers.status === "loading" ? t("automations.updating") : t("automations.refreshQueue")}</button><button type="button" className="automation-button automation-button--primary" onClick={() => void dispatchNextTrigger()} disabled={busy !== undefined}>{busy === "trigger:dispatch" ? t("automations.dispatching") : t("automations.dispatchNext")}</button></div>} />
      <TriggerComposer busy={busy === "trigger:create"} onEnqueue={enqueueTrigger} />
      <CollectionState status={triggers.status} error={triggers.error} empty={triggers.items.length === 0} onRetry={() => void reloadTriggers()} loadingLabel={t("automations.triggersLoading")} unavailableLabel={t("automations.triggersUnavailable")}>
        {triggers.items.length === 0 ? t("automations.triggersEmpty") : <div className="automation-card-grid">{triggers.items.map((trigger) => <TriggerCard key={trigger.id} trigger={trigger} />)}</div>}
      </CollectionState>
      <div className="automation-boundary-note automation-boundary-note--warning" role="note"><strong>{t("automations.triggerBoundaryTitle")}</strong><span>{t("automations.triggerBoundaryDetail")}</span></div>
      <div className="automation-boundary-note" role="note"><strong>{t("automations.runsBoundaryTitle")}</strong><span>{t("automations.runsBoundaryDetail")}</span></div>
      <CollectionState status={runs.status} error={runs.error} empty={runs.items.length === 0} onRetry={() => void reloadRuns()} loadingLabel={t("automations.runsLoading")} unavailableLabel={t("automations.runsUnavailable")}>
        {runs.items.length === 0 ? t("automations.runsEmpty") : <div className="automation-card-grid">{runs.items.map((run) => <article className="automation-card" aria-label={t("automations.runAria")} key={run.id}><div className="automation-card-heading"><div><span className="automation-card-icon" aria-hidden="true">◉</span><div className="automation-card-title"><h3>{runStatusWord(t, run.status)}</h3></div></div><StatePill value={t("automations.runAttempt", { count: run.attempt })} /></div><dl className="automation-facts"><div><dt>{t("automations.runStarted")}</dt><dd>{dateTime(run.created_at, locale, t)}</dd></div><div><dt>{t("automations.runFinished")}</dt><dd>{dateTime(run.finished_at, locale, t)}</dd></div><div><dt>{t("automations.runSequence")}</dt><dd>{run.sequence}</dd></div></dl>{(run.failure_code || run.failure_message) && <div className="automation-trigger-error" role="alert"><strong>{run.failure_code || t("automations.runErrorTitle")}</strong><p>{run.failure_message || t("automations.runErrorMessage")}</p></div>}{run.output_payload && <details className="automation-review"><summary>{t("automations.runOutput")}</summary><pre>{formatJson(run.output_payload)}</pre></details>}</article>)}</div>}
      </CollectionState>
      <div className="automation-form-actions"><span>{t("automations.runsFooter")}</span><button type="button" className="automation-button automation-button--quiet" onClick={() => void reloadRuns()} disabled={runs.status === "loading"}>{runs.status === "loading" ? t("automations.updating") : t("automations.refreshRuns")}</button></div>
    </section>

    <section className="automation-panel" aria-labelledby="webhooks-heading">
      <SectionHeader headingId="webhooks-heading" eyebrow={t("automations.webhooksEyebrow")} title={t("automations.webhooksTitle")} detail={t("automations.webhooksDetail")} action={<button type="button" className="automation-button automation-button--quiet" onClick={() => { void reloadWebhooks(); void reloadDeliveries(); }} disabled={webhooks.status === "loading" || deliveries.status === "loading"}>{webhooks.status === "loading" || deliveries.status === "loading" ? t("automations.updating") : t("automations.refreshSubscriptions")}</button>} />
      {createdSecret && <div className="automation-secret-note" role="status"><strong>{t("automations.secretTitle")}</strong><span>{t("automations.secretBody")}</span><button type="button" className="automation-button automation-button--primary" onClick={() => { void copyOneTimeSecret(t, createdSecret).then(() => { setSecretCopyMessage(t("automations.secretCopied")); setCreatedSecret(undefined); }, () => setSecretCopyMessage(t("automations.secretCopyFailed"))); }} aria-label={t("automations.secretCopyAria")}>{t("automations.secretCopy")}</button><button type="button" className="automation-button automation-button--quiet" onClick={() => { setCreatedSecret(undefined); setSecretCopyMessage(t("automations.secretHidden")); }}>{t("automations.secretHide")}</button>{secretCopyMessage && <span role="status">{secretCopyMessage}</span>}</div>}
      {!createdSecret && secretCopyMessage && <p className="automation-capability-hint" role="status">{secretCopyMessage}</p>}
      <WebhookComposer projectId={projectId} busy={busy === "webhook:create"} onCreate={createWebhook} />
      <CollectionState status={webhooks.status} error={webhooks.error} empty={webhooks.items.length === 0} onRetry={() => void reloadWebhooks()} loadingLabel={t("automations.webhooksLoading")} unavailableLabel={t("automations.webhooksUnavailable")}>
        {webhooks.items.length === 0 ? t("automations.webhooksEmpty") : <div className="automation-card-grid">{webhooks.items.map((subscription) => <WebhookCard key={subscription.id} subscription={subscription} busy={busy === `webhook:${subscription.id}`} onToggle={() => void toggleWebhook(subscription)} onDelete={() => void deleteWebhook(subscription)} />)}</div>}
      </CollectionState>
      <SectionHeader eyebrow={t("automations.deliveriesEyebrow")} title={t("automations.deliveriesTitle")} detail={t("automations.deliveriesDetail")} />
      {deliveries.status === "ready" && <div className="automation-delivery-summary" role="note">
        <strong>{t("automations.deliverySummaryCapped", { returned: deliveryReturnedCount })}</strong>
        {webhooks.status === "ready"
          ? <span>{t("automations.deliverySummaryMatches", { matched: projectDeliveries.length, rest: Math.max(0, deliveryReturnedCount - projectDeliveries.length) })}</span>
          : <span>{t("automations.deliverySummaryNoSubscriptions")}</span>}
        <span>{t("automations.deliverySummaryScope")}</span>
      </div>}
      <div className="automation-delivery-scope" role="group" aria-label={t("automations.deliveryScopeLabel")}>
        <button type="button" className={deliveryScope === "company" ? "is-active" : ""} aria-pressed={deliveryScope === "company"} onClick={() => setDeliveryScope("company")}>{t("automations.deliveryScopeCompany", { count: deliveries.items.length })}</button>
        <button type="button" className={deliveryScope === "project" ? "is-active" : ""} aria-pressed={deliveryScope === "project"} disabled={webhooks.status !== "ready"} onClick={() => setDeliveryScope("project")}>{t("automations.deliveryScopeProject", { count: webhooks.status === "ready" ? projectDeliveries.length : "—" })}</button>
      </div>
      <CollectionState status={deliveries.status} error={deliveries.error} empty={deliveries.items.length === 0} onRetry={() => void reloadDeliveries()} loadingLabel={t("automations.deliveriesLoading")} unavailableLabel={t("automations.deliveriesUnavailable")}>
        {deliveries.items.length === 0 ? t("automations.deliveriesEmpty") : (() => {
          if (deliveryScope === "project" && webhooks.status !== "ready") return <p>{t("automations.deliveryProjectUnavailable")}</p>;
          return shownDeliveries.length === 0
            ? <p>{t("automations.deliveryNoMatches")}</p>
            : <div className="automation-card-grid">{shownDeliveries.map((delivery) => <DeliveryCard key={delivery.id} delivery={delivery} busy={busy === `delivery:${delivery.id}`} retryAllowed={webhooks.status === "ready" && deliveryProjectSubscriptionIds.has(delivery.subscription_id)} onRetry={() => void retryWebhookDelivery(delivery)} />)}</div>;
        })()}
      </CollectionState>
      <div className="automation-boundary-note automation-boundary-note--warning" role="note"><strong>{t("automations.deliveryGateTitle")}</strong><span>{t("automations.deliveryGateDetail")}</span></div>
    </section>

    <section className="automation-panel automation-ingress-panel" aria-labelledby="ingress-heading">
      <SectionHeader headingId="ingress-heading" eyebrow={t("automations.ingressEyebrow")} title={t("automations.ingressTitle")} detail={t("automations.ingressDetail")} />
      <div className="automation-ingress-grid"><article><StatePill value={t("automations.ingressIn")} tone="warning" /><h3>{t("automations.ingressInTitle")}</h3><p>{t("automations.ingressInBody")}</p></article><article><StatePill value={t("automations.ingressOut")} tone="positive" /><h3>{t("automations.ingressOutTitle")}</h3><p>{t("automations.ingressOutBody")}</p></article></div>
      <p className="automation-capability-hint">{t("automations.ingressHint")}</p>
    </section>
    <footer className="automation-footer">{t("automations.footer")}</footer>
  </main>;
}

export function AutomationsPage({ projectId, service }: AutomationsPageProps) {
  const { tokens } = useAuth();
  const defaultService = useMemo(() => createDefaultAutomationService(() => tokens?.access_token), [tokens?.access_token]);
  return <AutomationsView projectId={projectId} service={service ?? defaultService} />;
}

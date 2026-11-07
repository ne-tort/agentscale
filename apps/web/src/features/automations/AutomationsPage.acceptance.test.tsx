// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import { AutomationsView } from "./AutomationsPage";
import { AutomationRequestError } from "./service";
import type { AutomationService, Schedule, WebhookDelivery, WebhookSubscription } from "./types";

function createService(overrides: Partial<AutomationService> = {}): AutomationService {
  return {
  listSchedules: async () => [],
  createSchedule: async () => { throw new Error("not used"); },
  updateSchedule: async () => { throw new Error("not used"); },
  deleteSchedule: async () => { throw new Error("not used"); },
  listTriggers: async () => [],
  enqueueTrigger: async () => { throw new Error("not used"); },
  dispatchNextTrigger: async () => ({ dispatched: false, reason: "no queued triggers" }),
  listAgentRuns: async () => [],
  listWebhooks: async () => [],
  createWebhook: async () => { throw new Error("not used"); },
  updateWebhook: async () => { throw new Error("not used"); },
  deleteWebhook: async () => { throw new Error("not used"); },
  listWebhookDeliveries: async () => [],
  retryWebhookDelivery: async () => { throw new Error("not used"); },
    ...overrides,
  };
}

const service = createService();

let root: Root | undefined;
let host: HTMLDivElement | undefined;
let clipboardDescriptor: PropertyDescriptor | undefined;

async function mount(serviceOverride: AutomationService): Promise<HTMLDivElement> {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(createElement(SettingsProvider, null, createElement(AutomationsView, { projectId: "project-1", service: serviceOverride })));
  });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  return host;
}

async function flush(): Promise<void> {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

function button(label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find((item) => item.textContent?.trim() === label || item.getAttribute("aria-label") === label);
  if (!found) throw new Error(`Button not found: ${label}`);
  return found;
}

async function setInput(input: HTMLInputElement | HTMLTextAreaElement, value: string): Promise<void> {
  await act(async () => {
    const prototype = input instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  if (clipboardDescriptor) Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else Reflect.deleteProperty(navigator, "clipboard");
  clipboardDescriptor = undefined;
  vi.restoreAllMocks();
});

describe("project automations acceptance surface", () => {
  it("renders loading surfaces, accessible forms, and explicit capability blockers", () => {
    const html = renderToStaticMarkup(createElement(SettingsProvider, null, createElement(AutomationsView, { projectId: "project-1", service })));

    expect(html).toContain("Автоматизации");
    expect(html).toContain("Загружаем расписания");
    expect(html).toContain("Загружаем события");
    expect(html).toContain("Загружаем вебхуки");
    expect(html).toContain('aria-labelledby="schedules-heading"');
    expect(html).toContain('aria-labelledby="triggers-heading"');
    expect(html).toContain('aria-labelledby="webhooks-heading"');
    expect(html).toContain("Запуски агента");
    expect(html).toContain("История доставок");
    expect(html).toContain("Отредактировать, удалить или повторить отдельное событие нельзя");
    expect(html).toContain("связать запуск с конкретным событием нельзя");
    expect(html).toContain("Входящие события — отдельный поток");
    // The one-time-secret controls belong to the post-create state only; a freshly
    // mounted, still-loading screen has no secret to copy and must not offer one.
    // That flow is pinned by "never renders the one-time webhook secret and offers
    // a copy action", which creates the subscription first.
    expect(html).not.toContain("Скопировать секрет");
    expect(html).not.toContain("run succeeded");
    expect(html).not.toContain("delivery succeeded");
    // The internal vocabulary stays out of the copy a person reads.
    expect(html).not.toContain("trigger id");
    expect(html).not.toContain("payload");
    expect(html).not.toContain("endpoint");
  });

  it("creates, updates, and deletes a schedule with GET readback", async () => {
    const schedules: Schedule[] = [];
    let nextId = 1;
    const liveService = createService({
      listSchedules: async () => [...schedules],
      createSchedule: async (_projectId, input) => {
        const saved: Schedule = { ...input, id: `schedule-${nextId++}`, project_id: "project-1" };
        schedules.push(saved);
        return saved;
      },
      updateSchedule: async (_projectId, id, patch) => {
        const index = schedules.findIndex((item) => item.id === id);
        schedules[index] = { ...schedules[index]!, ...patch };
        return schedules[index]!;
      },
      deleteSchedule: async (_projectId, id) => { schedules.splice(schedules.findIndex((item) => item.id === id), 1); },
    });
    const container = await mount(liveService);
    await act(async () => button("Новое расписание").click());
    const createForm = container.querySelector<HTMLFormElement>('form[aria-label="Новое расписание"]');
    const name = createForm?.querySelector<HTMLInputElement>("input");
    if (!createForm || !name) throw new Error("Schedule creation form missing");
    await setInput(name, "Morning digest");
    await act(async () => createForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await flush();
    expect(container.textContent).toContain("Morning digest");
    expect(container.textContent).toContain("Расписание «Morning digest» создано");

    await act(async () => button("Изменить").click());
    const editForm = container.querySelector<HTMLFormElement>('form[aria-label="Редактирование расписания «Morning digest»"]');
    const editName = editForm?.querySelector<HTMLInputElement>("input");
    if (!editForm || !editName) throw new Error("Schedule edit form missing");
    await setInput(editName, "Updated digest");
    await act(async () => editForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await flush();
    expect(container.textContent).toContain("Updated digest");

    vi.spyOn(window, "confirm").mockReturnValue(true);
    await act(async () => button("Удалить").click());
    await flush();
    // The success notice names the schedule it removed, so a plain "not to contain
    // the name" would also forbid honest feedback. The claim under test is that the
    // schedule row itself is gone and the backend list no longer has it.
    expect(schedules).toHaveLength(0);
    expect(container.querySelector('[aria-label="Расписание «Updated digest»"]')).toBeNull();
    expect(container.textContent).toContain("Расписание «Updated digest» удалено");
  });

  it("creates a project trigger and confirms queue membership without claiming an agent run", async () => {
    const triggers: Array<{ id: string; project_id: string; kind: string; payload: Record<string, unknown>; status: string }> = [];
    const liveService = createService({
      listTriggers: async () => [...triggers],
      enqueueTrigger: async (_projectId, input) => {
        const queued = { ...input, id: "trigger-1", project_id: "project-1", status: "queued" };
        triggers.push(queued);
        return queued;
      },
    });
    const container = await mount(liveService);
    const form = container.querySelector<HTMLFormElement>(".automation-trigger-composer");
    if (!form) throw new Error("Project trigger form missing");
    await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await flush();
    expect(container.textContent).toContain("Событие «Сообщение агенту» в очереди");
    expect(container.textContent).toContain("Состояние: «В очереди»");
    expect(container.textContent).toContain("а не выполнения");
    expect(container.textContent).not.toContain("результат Agent Run trigger-1");
    expect(triggers).toHaveLength(1);
  });

  it("shows delivery errors and only offers retry for exhausted deliveries", async () => {
    const subscription: WebhookSubscription = { id: "webhook-1", url: "https://hook.test", events: ["chat.turn.failed"], project_id: "project-1", enabled: true };
    const delivery: WebhookDelivery = { id: "delivery-1", subscription_id: subscription.id, url: subscription.url, subscription_enabled: true, event_type: "chat.turn.failed", status: "exhausted", attempts: 5, retry_history: [{ attempt: 5, response_status: 500 }], response_status: 500, last_error: "http 500", created_at: "2026-01-01T00:00:00Z", next_attempt_at: null, delivered_at: null };
    let current = delivery;
    const liveService = createService({
      listWebhooks: async () => [subscription],
      listWebhookDeliveries: async () => [current],
      retryWebhookDelivery: async (id) => {
        current = { ...current, status: "pending", attempts: 0, retry_history: [...current.retry_history, { kind: "manual_retry" }], response_status: null, last_error: null };
        return { id, status: "pending" };
      },
    });
    const container = await mount(liveService);
    expect(container.textContent).toContain("http 500");
    expect(container.textContent).toContain("История попыток");
    await act(async () => button("Повторить доставку").click());
    await flush();
    expect(container.textContent).toContain("Повторная доставка запущена");
    expect(container.textContent).toContain("В очереди");
    expect(container.textContent).toContain("manual_retry");
    expect(container.textContent).not.toContain("Повторить доставку");
  });

  it("labels the capped company-wide list and filters only known project subscription matches", async () => {
    const ownSubscription: WebhookSubscription = { id: "opaque-project-subscription", url: "https://own.test", events: ["chat.turn.failed"], project_id: "project-1", enabled: true };
    const makeDelivery = (id: string, subscriptionId: string): WebhookDelivery => ({
      id, subscription_id: subscriptionId, url: `https://${id}.test`, subscription_enabled: true,
      event_type: "chat.turn.failed", status: "exhausted", attempts: 5, retry_history: [], response_status: null,
      last_error: null, created_at: "2026-01-01T00:00:00Z", next_attempt_at: null, delivered_at: null,
    });
    const deliveries = [makeDelivery("delivery-project", ownSubscription.id), ...Array.from({ length: 199 }, (_, index) => makeDelivery(`delivery-company-${index}`, `opaque-subscription-${index}`))];
    const liveService = createService({ listWebhooks: async () => [ownSubscription], listWebhookDeliveries: async () => deliveries });
    const container = await mount(liveService);

    expect(container.textContent).toContain("Последние доставки по всей компании");
    expect(container.textContent).toContain("Получено от сервиса: 200 из максимум 200 строк");
    expect(container.textContent).toContain("199 с ним не связаны");
    // The caveat is the second sentence of one paragraph, so it is lowercase in the
    // DOM. Asserting the capitalised form only pinned a copy-paste artefact.
    expect(container.textContent).toContain("Сервис отдаёт только недавние записи");
    expect(container.textContent).toContain("delivery-company-0");
    expect(container.textContent).toContain("Повторить нельзя");

    await act(async () => button("Совпадения с подписками проекта (1)").click());
    expect(container.textContent).toContain("delivery-project");
    expect(container.textContent).not.toContain("delivery-company-0");
    expect(container.textContent).toContain("Повторить доставку");
  });

  it("keeps the company-wide delivery view available when project subscription attribution is unavailable", async () => {
    const delivery: WebhookDelivery = { id: "company-only-delivery", subscription_id: "subscription-outside-list", url: "https://company.test", subscription_enabled: true, event_type: "chat.turn.failed", status: "vendor_state", attempts: 1, retry_history: [], response_status: null, last_error: null, created_at: "2026-01-01T00:00:00Z", next_attempt_at: null, delivered_at: null };
    const liveService = createService({
      listWebhooks: async () => { throw new Error("project subscriptions unavailable"); },
      listWebhookDeliveries: async () => [delivery],
    });
    const container = await mount(liveService);
    expect(container.textContent).toContain("project subscriptions unavailable");
    expect(container.textContent).toContain("Получено от сервиса: 1 из максимум 200 строк");
    expect(container.textContent).toContain("https://company.test");
    // An unfamiliar state is described, not printed as the raw enum value.
    expect(container.textContent).toContain("Состояние не сообщено");
    expect(container.textContent).not.toContain("vendor_state");
    expect(container.textContent).toContain("Список подписок проекта пока не загружен");
    expect(container.textContent).not.toContain("Повторить доставку");
  });

  it("does not POST retry when the exact row is no longer exhausted", async () => {
    const subscription: WebhookSubscription = { id: "opaque-subscription", url: "https://hook.test", events: ["chat.turn.failed"], project_id: "project-1", enabled: true };
    const delivery: WebhookDelivery = { id: "delivery-exact", subscription_id: subscription.id, url: subscription.url, subscription_enabled: true, event_type: "chat.turn.failed", status: "exhausted", attempts: 5, retry_history: [], response_status: null, last_error: "failed", created_at: "2026-01-01T00:00:00Z", next_attempt_at: null, delivered_at: null };
    let latest: WebhookDelivery = delivery;
    let subscriptions: WebhookSubscription[] = [subscription];
    let retryPosts = 0;
    const liveService = createService({
      listWebhooks: async () => subscriptions,
      listWebhookDeliveries: async () => [latest],
      retryWebhookDelivery: async (id) => { retryPosts += 1; return { id, status: "pending" }; },
    });
    const container = await mount(liveService);
    latest = { ...delivery, status: "delivered" };
    await act(async () => button("Повторить доставку").click());
    await flush();
    expect(retryPosts).toBe(0);
    expect(container.textContent).toContain("исчерпана и относится к подписке проекта");
  });

  it("does not POST retry when the fresh project-filtered subscription list no longer contains the row subscription", async () => {
    const subscription: WebhookSubscription = { id: "opaque-subscription", url: "https://hook.test", events: ["chat.turn.failed"], project_id: "project-1", enabled: true };
    const delivery: WebhookDelivery = { id: "delivery-membership", subscription_id: subscription.id, url: subscription.url, subscription_enabled: true, event_type: "chat.turn.failed", status: "exhausted", attempts: 5, retry_history: [], response_status: null, last_error: "failed", created_at: "2026-01-01T00:00:00Z", next_attempt_at: null, delivered_at: null };
    let subscriptionReads = 0;
    let retryPosts = 0;
    const liveService = createService({
      listWebhooks: async () => { subscriptionReads += 1; return subscriptionReads === 1 ? [subscription] : []; },
      listWebhookDeliveries: async () => [delivery],
      retryWebhookDelivery: async (id) => { retryPosts += 1; return { id, status: "pending" }; },
    });
    const container = await mount(liveService);
    expect(container.textContent).toContain("Повторить доставку");
    await act(async () => button("Повторить доставку").click());
    await flush();
    expect(retryPosts).toBe(0);
    expect(container.textContent).toContain("исчерпана и относится к подписке проекта");
  });

  it("requires a newly appended manual_retry marker in GET readback", async () => {
    const subscription: WebhookSubscription = { id: "webhook-readback", url: "https://hook.test", events: ["chat.turn.failed"], project_id: "project-1", enabled: true };
    const delivery: WebhookDelivery = { id: "delivery-readback", subscription_id: subscription.id, url: subscription.url, subscription_enabled: true, event_type: "chat.turn.failed", status: "exhausted", attempts: 5, retry_history: [{ kind: "manual_retry" }], response_status: null, last_error: "failed", created_at: "2026-01-01T00:00:00Z", next_attempt_at: null, delivered_at: null };
    let retryPosts = 0;
    const liveService = createService({
      listWebhooks: async () => [subscription],
      listWebhookDeliveries: async () => [delivery],
      retryWebhookDelivery: async (id) => { retryPosts += 1; return { id, status: "pending" }; },
    });
    const container = await mount(liveService);
    await act(async () => button("Повторить доставку").click());
    await flush();
    expect(retryPosts).toBe(1);
    expect(container.textContent).toContain("История доставок не подтвердила повторную попытку");
    expect(container.textContent).not.toContain("Повторная доставка запущена");
  });

  it("never renders the one-time webhook secret and offers a copy action", async () => {
    const secret = "secret-that-must-not-appear-in-the-dom";
    const subscription: WebhookSubscription = { id: "webhook-2", url: "https://hooks.example.test/events", events: ["chat.message.completed"], project_id: "project-1", enabled: true };
    const clipboardWrite = vi.fn(async () => {});
    clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: clipboardWrite } });
    const liveService = createService({
      listWebhooks: async () => [subscription],
      createWebhook: async () => ({ ...subscription, secret }),
    });
    const container = await mount(liveService);
    const url = container.querySelector<HTMLInputElement>('input[type="url"]');
    const form = container.querySelector<HTMLFormElement>(".automation-webhook-form");
    if (!url || !form) throw new Error("Webhook creation form missing");
    await setInput(url, subscription.url);
    await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await flush();
    expect(container.textContent).toContain("Секрет доступен только сейчас");
    expect(container.innerHTML).not.toContain(secret);
    await act(async () => button("Скопировать секрет").click());
    await flush();
    expect(clipboardWrite).toHaveBeenCalledWith(secret);
    expect(container.innerHTML).not.toContain(secret);
    expect(container.textContent).toContain("Секрет скопирован в буфер обмена.");
  });

  it("creates, updates, and deletes an outgoing subscription with list readback", async () => {
    const subscriptions: WebhookSubscription[] = [];
    const liveService = createService({
      // A real endpoint answers with fresh JSON, never with the object the client
      // already holds. Aliasing the stored object here let the fixture mutate the
      // very value React was comparing against, so the post-toggle readback check
      // compared a field with itself instead of with the previous state.
      listWebhooks: async () => subscriptions.map((item) => ({ ...item })),
      createWebhook: async (input) => {
        const created: WebhookSubscription = { ...input, id: "webhook-crud", enabled: true };
        subscriptions.push(created);
        return { ...created, secret: "secret-for-copy-only" };
      },
      updateWebhook: async (id, patch) => {
        const index = subscriptions.findIndex((item) => item.id === id);
        if (index < 0) throw new Error("subscription not found");
        subscriptions[index] = { ...subscriptions[index]!, ...patch };
        return { id, enabled: subscriptions[index]!.enabled };
      },
      deleteWebhook: async (id) => {
        const index = subscriptions.findIndex((item) => item.id === id);
        if (index < 0) throw new Error("subscription not found");
        subscriptions.splice(index, 1);
        return { ok: true };
      },
    });
    const container = await mount(liveService);
    const url = container.querySelector<HTMLInputElement>('input[type="url"]');
    const form = container.querySelector<HTMLFormElement>(".automation-webhook-form");
    if (!url || !form) throw new Error("Webhook creation form missing");
    await setInput(url, "https://hooks.example.test/automation");
    await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await flush();
    expect(container.textContent).toContain("https://hooks.example.test/automation");
    expect(container.textContent).toContain("Подписка для https://hooks.example.test/automation создана");

    await act(async () => button("Отключить вебхук https://hooks.example.test/automation").click());
    await flush();
    expect(container.textContent).toContain("Вебхук выключен");

    vi.spyOn(window, "confirm").mockReturnValue(true);
    await act(async () => button("Удалить").click());
    await flush();
    expect(container.textContent).not.toContain("https://hooks.example.test/automation");
    expect(subscriptions).toHaveLength(0);
  });

  it("reports dispatch failures without claiming a successful run", async () => {
    const liveService = createService({
      dispatchNextTrigger: async () => { throw new Error("dispatch unavailable"); },
    });
    const container = await mount(liveService);
    await act(async () => button("Обработать следующее").click());
    await flush();
    expect(container.textContent).toContain("dispatch unavailable");
    expect(container.textContent).not.toContain("dispatched=true");
  });

  it("surfaces a backend retry conflict without claiming that delivery was retried", async () => {
    const subscription: WebhookSubscription = { id: "webhook-3", url: "https://hook.test", events: ["chat.turn.failed"], project_id: "project-1", enabled: true };
    const delivery: WebhookDelivery = { id: "delivery-3", subscription_id: subscription.id, url: subscription.url, subscription_enabled: true, event_type: "chat.turn.failed", status: "exhausted", attempts: 5, retry_history: [], response_status: null, last_error: null, created_at: "2026-01-01T00:00:00Z", next_attempt_at: null, delivered_at: null };
    const liveService = createService({
      listWebhooks: async () => [subscription],
      listWebhookDeliveries: async () => [delivery],
      retryWebhookDelivery: async () => { throw new AutomationRequestError("delivery not found or not exhausted", { status: 409, code: "NOT_FOUND_OR_NOT_RETRYABLE" }); },
    });
    const container = await mount(liveService);
    await act(async () => button("Повторить доставку").click());
    await flush();
    expect(container.textContent).toContain("delivery not found or not exhausted");
    expect(container.textContent).not.toContain("Повторная доставка запущена");
    expect(container.textContent).toContain("exhausted");
  });

  it("shows the real gated Agent Run history blocker with a retryable read action", async () => {
    const liveService = createService({ listAgentRuns: async () => { throw new Error("durable Agent Run API is disabled"); } });
    const container = await mount(liveService);
    expect(container.textContent).toContain("durable Agent Run API is disabled");
    expect(container.textContent).toContain("Повторить загрузку");
  });
});

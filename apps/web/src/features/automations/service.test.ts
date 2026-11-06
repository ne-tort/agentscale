import { describe, expect, it } from "vitest";
import { AutomationRequestError, createAutomationService, parseJsonObject } from "./service";
import type { AgentRun, Schedule, ProjectTrigger, WebhookCreateResponse, WebhookDelivery, WebhookSubscription } from "./types";

type CapturedRequest = { url: string; init?: RequestInit };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestBody(request: CapturedRequest): unknown {
  return request.init?.body ? JSON.parse(String(request.init.body)) : undefined;
}

const delivery: WebhookDelivery = {
  id: "delivery-1", subscription_id: "webhook-1", url: "https://hooks.example.test/agent", subscription_enabled: true,
  event_type: "chat.turn.failed", status: "vendor_pending", attempts: 2, retry_history: [], response_status: null,
  last_error: null, created_at: "2026-01-01T00:00:00Z", next_attempt_at: null, delivered_at: null,
};

const run: AgentRun = {
  id: "run-1", project_id: "project/1", session_id: null, requested_by_employee_id: null, idempotency_key: "key-1",
  status: "queued", attempt: 1, input_payload: {}, output_payload: null, failure_code: null, failure_message: null,
  sequence: 1, started_at: null, heartbeat_at: null, finished_at: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
};

describe("automation service contract", () => {
  it("uses only confirmed schedule, trigger, and webhook routes", async () => {
    const calls: CapturedRequest[] = [];
    const schedule: Schedule = {
      id: "schedule-1",
      project_id: "project/1",
      name: "Weekday digest",
      cron: "0 9 * * 1-5",
      timezone: "Europe/Moscow",
      payload: { text: "digest" },
      enabled: true,
    };
    const trigger: ProjectTrigger = {
      id: "trigger-1",
      project_id: "project/1",
      kind: "chat.message",
      payload: { text: "hello" },
      status: "queued",
    };
    const webhook: WebhookSubscription = {
      id: "webhook-1",
      url: "https://hooks.example.test/agent",
      events: ["chat.message.completed"],
      project_id: "project/1",
      enabled: true,
    };
    const createdWebhook: WebhookCreateResponse = { ...webhook, secret: "one-time-secret" };
    const service = createAutomationService({
      baseUrl: "/api/v1",
      getAccessToken: () => "token-1",
      fetcher: async (input, init) => {
        const url = String(input);
        calls.push({ url, init });
        if (url.endsWith("/projects/project%2F1/schedules") && init?.method === "POST") return jsonResponse(schedule, 201);
        if (url.endsWith("/projects/project%2F1/schedules") && !init?.method) return jsonResponse({ items: [schedule] });
        if (url.endsWith("/projects/project%2F1/schedules/schedule-1") && init?.method === "PATCH") return jsonResponse(schedule);
        if (url.endsWith("/projects/project%2F1/schedules/schedule-1") && init?.method === "DELETE") return new Response(null, { status: 204 });
        if (url.endsWith("/projects/project%2F1/triggers") && init?.method === "POST") return jsonResponse(trigger, 202);
        if (url.endsWith("/projects/project%2F1/triggers") && !init?.method) return jsonResponse({ items: [trigger] });
        if (url.endsWith("/projects/project%2F1/triggers/dispatch") && init?.method === "POST") return jsonResponse({ dispatched: true, trigger_id: trigger.id, kind: trigger.kind, action: "run_chat.message" });
        if (url.endsWith("/projects/project%2F1/agent/runs?limit=50") && !init?.method) return jsonResponse({ items: [run], limit: 50, offset: 0 });
        if (url.endsWith("/external/webhooks?project_id=project%2F1") && !init?.method) return jsonResponse({ items: [webhook] });
        if (url === "/api/v1/external/webhooks" && init?.method === "POST") return jsonResponse(createdWebhook, 201);
        if (url.endsWith("/external/webhooks/webhook-1") && init?.method === "PATCH") return jsonResponse({ id: webhook.id, enabled: false });
        if (url.endsWith("/external/webhooks/webhook-1") && init?.method === "DELETE") return jsonResponse({ ok: true });
        if (url.endsWith("/external/webhook-deliveries?limit=200") && !init?.method) return jsonResponse({ items: [delivery] });
        if (url.endsWith("/external/webhook-deliveries/delivery-1/retry") && init?.method === "POST") return jsonResponse({ id: "delivery-1", status: "pending" });
        throw new Error(`unexpected request: ${url} ${init?.method ?? "GET"}`);
      },
    });

    await expect(service.listSchedules("project/1")).resolves.toEqual([schedule]);
    await expect(service.createSchedule("project/1", {
      name: schedule.name,
      cron: schedule.cron,
      timezone: schedule.timezone,
      payload: schedule.payload,
      enabled: schedule.enabled,
    })).resolves.toEqual(schedule);
    await expect(service.updateSchedule("project/1", schedule.id, { enabled: false })).resolves.toEqual(schedule);
    await expect(service.deleteSchedule("project/1", schedule.id)).resolves.toBeUndefined();
    await expect(service.listTriggers("project/1")).resolves.toEqual([trigger]);
    await expect(service.enqueueTrigger("project/1", { kind: trigger.kind, payload: trigger.payload })).resolves.toEqual(trigger);
    await expect(service.dispatchNextTrigger("project/1")).resolves.toMatchObject({ dispatched: true, trigger_id: trigger.id });
    await expect(service.listAgentRuns("project/1")).resolves.toEqual([run]);
    await expect(service.listWebhooks("project/1")).resolves.toEqual([webhook]);
    await expect(service.createWebhook({ url: webhook.url, events: webhook.events, project_id: "project/1" })).resolves.toEqual(createdWebhook);
    await expect(service.updateWebhook(webhook.id, { enabled: false })).resolves.toEqual({ id: webhook.id, enabled: false });
    await expect(service.deleteWebhook(webhook.id)).resolves.toEqual({ ok: true });
    await expect(service.listWebhookDeliveries()).resolves.toEqual([delivery]);
    await expect(service.retryWebhookDelivery("delivery-1")).resolves.toEqual({ id: "delivery-1", status: "pending" });

    expect(calls.every(({ init }) => (init?.headers as Record<string, string>).Authorization === "Bearer token-1")).toBe(true);
    expect(calls.map(({ url, init }) => `${init?.method ?? "GET"} ${url}`)).toEqual([
      "GET /api/v1/projects/project%2F1/schedules",
      "POST /api/v1/projects/project%2F1/schedules",
      "PATCH /api/v1/projects/project%2F1/schedules/schedule-1",
      "DELETE /api/v1/projects/project%2F1/schedules/schedule-1",
      "GET /api/v1/projects/project%2F1/triggers",
      "POST /api/v1/projects/project%2F1/triggers",
      "POST /api/v1/projects/project%2F1/triggers/dispatch",
      "GET /api/v1/projects/project%2F1/agent/runs?limit=50",
      "GET /api/v1/external/webhooks?project_id=project%2F1",
      "POST /api/v1/external/webhooks",
      "PATCH /api/v1/external/webhooks/webhook-1",
      "DELETE /api/v1/external/webhooks/webhook-1",
      "GET /api/v1/external/webhook-deliveries?limit=200",
      "POST /api/v1/external/webhook-deliveries/delivery-1/retry",
    ]);
    expect(requestBody(calls[1]!)).toEqual({
      name: schedule.name,
      cron: schedule.cron,
      timezone: schedule.timezone,
      payload: schedule.payload,
      enabled: true,
    });
    expect(requestBody(calls[5]!)).toEqual({ kind: "chat.message", payload: { text: "hello" } });
    expect(requestBody(calls[9]!)).toEqual({ url: webhook.url, events: webhook.events, project_id: "project/1" });
  });

  it("accepts only JSON objects as payloads", () => {
    expect(parseJsonObject('{"text":"ok"}')).toEqual({ text: "ok" });
    expect(() => parseJsonObject("[]")).toThrow("JSON-объектом");
    expect(() => parseJsonObject("null")).toThrow("JSON-объектом");
    expect(() => parseJsonObject("not-json")).toThrow("корректным JSON");
  });

  it("preserves backend error and retryability without inventing success", async () => {
    const service = createAutomationService({
      fetcher: async () => jsonResponse({ code: "PROJECT_PAUSED", title: "Project paused", detail: "project is paused" }, 409),
    });

    await expect(service.listTriggers("project-1")).rejects.toMatchObject({
      code: "PROJECT_PAUSED",
      status: 409,
      retryable: false,
      message: "project is paused",
    });
  });

  it("rejects mismatched and malformed delivery response shapes while preserving unknown statuses", async () => {
    const makeService = (body: unknown) => createAutomationService({ fetcher: async () => jsonResponse(body) });
    await expect(makeService({ items: [{ ...delivery, project_id: "project/1" }] }).listWebhookDeliveries()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    await expect(makeService({ items: [{ ...delivery, attempts: "2" }] }).listWebhookDeliveries()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    await expect(makeService({ items: [{ ...delivery, retry_history: [null] }] }).listWebhookDeliveries()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    await expect(makeService({ items: [{ ...delivery, surprise: true }] }).listWebhookDeliveries()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    await expect(makeService({ items: [delivery] }).listWebhookDeliveries()).resolves.toMatchObject([{ status: "vendor_pending" }]);
  });

  it("validates run list envelopes, record fields, and project attribution", async () => {
    const makeService = (body: unknown) => createAutomationService({ fetcher: async () => jsonResponse(body) });
    await expect(makeService({ items: [run], limit: 50, offset: 0 }).listAgentRuns("project/1")).resolves.toEqual([run]);
    await expect(makeService({ items: [run], limit: 50 }).listAgentRuns("project/1")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    await expect(makeService({ items: [{ ...run, project_id: "another-project" }], limit: 50, offset: 0 }).listAgentRuns("project/1")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    await expect(makeService({ items: [{ ...run, input_payload: [] }], limit: 50, offset: 0 }).listAgentRuns("project/1")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    await expect(makeService({ items: [{ ...run, unexpected: true }], limit: 50, offset: 0 }).listAgentRuns("project/1")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    await expect(makeService({ items: [{ ...run, status: "unknown" }], limit: 50, offset: 0 }).listAgentRuns("project/1")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it("rejects retry responses that do not confirm the exact requested pending delivery", async () => {
    const wrongId = createAutomationService({ fetcher: async () => jsonResponse({ id: "another-delivery", status: "pending" }) });
    const wrongState = createAutomationService({ fetcher: async () => jsonResponse({ id: "delivery-1", status: "exhausted" }) });
    await expect(wrongId.retryWebhookDelivery("delivery-1")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    await expect(wrongState.retryWebhookDelivery("delivery-1")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });
});

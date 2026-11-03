import { describe, expect, it, vi } from "vitest";
import {
  createModelsService,
  ModelsServiceError,
  parseAvailableAiKey,
  parseLiveModels,
} from "./service";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const project = {
  id: "project-42",
  name: "Model Lab",
  agent_provider: "codex",
  resolved_ai_key_id: "key-current",
};

const key = {
  id: "key-current",
  name: "Team Codex",
  provider: "codex",
  api_kind: "codex_sdk",
  has_secret: true,
  status: "active",
  secret_ref: "must-never-reach-ui",
  last_probe: {
    status: "ok",
    models: ["gpt-5"],
    checked_at: "2026-11-02T10:00:00Z",
    token: "must-never-reach-ui",
  },
};

const models = {
  models: [{ id: "gpt-5", label: "GPT-5", catalog_matched: true }],
  default_model: "gpt-5",
  source: "live",
};

describe("models service acceptance contract", () => {
  it("uses only the confirmed project, available-key, live-model and project PATCH routes", async () => {
    const calls: Array<{ url: string; method: string; body?: string }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET", body: typeof init?.body === "string" ? init.body : undefined });
      if (url.endsWith("/api/v1/projects/project-42") && init?.method === "PATCH") return jsonResponse(project);
      if (url.endsWith("/api/v1/projects/project-42")) return jsonResponse(project);
      if (url.endsWith("/api/v1/projects/project-42/ai-keys/available")) return jsonResponse({ items: [key] });
      if (url.endsWith("/api/v1/projects/project-42/models/live")) return jsonResponse(models);
      throw new Error(`unexpected endpoint ${url}`);
    });
    const service = createModelsService({ fetcher, getAccessToken: () => "access-token" });

    await service.getProject("project-42");
    await service.listAvailableKeys("project-42");
    await service.listLiveModels("project-42");
    await service.patchProject("project-42", { agent_provider: "codex", resolved_ai_key_id: "key-current" });

    expect(calls.map((call) => [call.url, call.method])).toEqual([
      ["/api/v1/projects/project-42", "GET"],
      ["/api/v1/projects/project-42/ai-keys/available", "GET"],
      ["/api/v1/projects/project-42/models/live", "GET"],
      ["/api/v1/projects/project-42", "PATCH"],
    ]);
    expect(calls.at(-1)?.body).toBe(JSON.stringify({ agent_provider: "codex", resolved_ai_key_id: "key-current" }));
    expect(fetcher.mock.calls.every(([, init]) => init?.headers && (init.headers as Record<string, string>).Authorization === "Bearer access-token")).toBe(true);
  });

  it("redacts secret values while preserving public probe and capability metadata", () => {
    const parsed = parseAvailableAiKey(key);
    expect(parsed).toMatchObject({
      id: "key-current",
      provider: "codex",
      has_secret: true,
      last_probe: { status: "ok", models: ["gpt-5"] },
    });
    expect(JSON.stringify(parsed)).not.toContain("must-never-reach-ui");
    expect(parsed).not.toHaveProperty("secret_ref");
    expect(parsed?.last_probe).not.toHaveProperty("token");

    expect(parseLiveModels(models)).toMatchObject({ source: "live", default_model: "gpt-5", models: [{ id: "gpt-5", catalog_matched: true }] });
  });

  it("keeps backend error code/detail and marks live failures retryable", async () => {
    const service = createModelsService({
      fetcher: vi.fn(async () => jsonResponse({ code: "MODELS_UNAVAILABLE", title: "Models unavailable", detail: "runtime is paused" }, 503)),
    });

    await expect(service.listLiveModels("project-42")).rejects.toMatchObject({
      code: "MODELS_UNAVAILABLE",
      message: "runtime is paused",
      status: 503,
      retryable: true,
    });
    expect(ModelsServiceError).toBeDefined();
  });

  it("does not expose CRUD methods for keys or models", () => {
    const service = createModelsService({ fetcher: vi.fn(async () => jsonResponse({})) });
    expect(service).not.toHaveProperty("createKey");
    expect(service).not.toHaveProperty("deleteKey");
    expect(service).not.toHaveProperty("probeKey");
    expect(service).not.toHaveProperty("createModel");
    expect(service).not.toHaveProperty("deleteModel");
  });
});

// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelsPicker } from "./ModelsPicker";
import type { AvailableAiKey, LiveModelsResponse, ModelProject, ModelsService } from "./types";

const project: ModelProject = {
  id: "project-42",
  name: "Model Lab",
  agent_provider: "codex",
  resolved_ai_key_id: "key-current",
};

const keys: AvailableAiKey[] = [
  {
    id: "key-current",
    name: "Team Codex",
    provider: "codex",
    api_kind: "codex_sdk",
    status: "active",
    has_secret: true,
    last_probe: { status: "ok", models: ["gpt-5"], checked_at: "2026-11-02T10:00:00Z" },
  },
  {
    id: "key-stale",
    name: "Stale Claude",
    provider: "claude_code",
    api_kind: "claude_sdk",
    status: "active",
    has_secret: true,
    last_probe: { status: "ok", models: ["claude-sonnet"], stale: true },
  },
  {
    id: "key-error",
    name: "Error OpenAI",
    provider: "openai",
    api_kind: "openai_api",
    status: "active",
    has_secret: true,
    last_probe: { status: "error", error_code: "PROBE_TIMEOUT", models: [] },
  },
];

const liveModels: LiveModelsResponse = {
  models: [
    { id: "gpt-5", label: "GPT-5", publisher: "OpenAI", catalog_matched: true, max_context_tokens: 128000 },
    { id: "vendor-experimental", label: "Vendor Experimental", catalog_matched: false },
  ],
  default_model: "gpt-5",
  source: "live",
};

const service: ModelsService = {
  getProject: async () => project,
  listAvailableKeys: async () => keys,
  listLiveModels: async () => liveModels,
  patchProject: async (_projectId, patch) => ({ ...project, ...patch }),
};

describe("AI provider/key/model picker acceptance surface", () => {
  it("renders current selection, searches, capability labels, stale/probe warning, save language and secret safety", () => {
    const markup = renderToStaticMarkup(
      <ModelsPicker
        projectId="project-42"
        service={service}
        initialProject={project}
        initialKeys={keys}
        initialModels={liveModels}
        initialModelId="gpt-5"
        onModelChange={() => undefined}
      />,
    );

    expect(markup).toContain("Provider, key и model");
    expect(markup).toContain("Team Codex");
    expect(markup).toContain("Текущий");
    expect(markup).toContain("GPT-5");
    expect(markup).toContain("Совместимость подтверждена");
    expect(markup).toContain("Live capability");
    expect(markup).toContain("Ошибка probe");
    expect(markup).toContain("Stale");
    expect(markup).toContain("Проверка устарела");
    expect(markup).toContain("PROBE_TIMEOUT");
    expect(markup).toContain("Сохранить provider и key");
    expect(markup).toContain("Применить model");
    expect(markup).toContain("/projects/:projectId/ai-keys/available");
    expect(markup).toContain("/projects/:projectId/models/live");
    expect(markup).toContain("Secret values не отображаются");
    expect(markup.toLowerCase()).not.toContain("secret_ref");
    expect(markup.toLowerCase()).not.toContain("token");
    expect(markup).toContain('id="models-key-search"');
    expect(markup).toContain('id="models-model-search"');
  });

  it("keeps model application behind the explicit host boundary because project PATCH has no model field", () => {
    const markup = renderToStaticMarkup(
      <ModelsPicker projectId="project-42" service={service} initialProject={project} initialKeys={keys} initialModels={liveModels} initialModelId="gpt-5" />,
    );

    expect(markup).toContain("Host callback для применения model не подключён");
    expect(markup).toContain("PATCH project model не заявляет");
  });

  it("shows a clear incompatibility warning before applying an unmatched live model", () => {
    const markup = renderToStaticMarkup(<ModelsPicker projectId="project-42" service={service} initialProject={project} initialKeys={keys} initialModels={liveModels} initialModelId="vendor-experimental" onModelChange={() => undefined} />);

    expect(markup).toContain("Совместимость не подтверждена");
    expect(markup).toContain("catalog compatibility не подтверждена");
    expect(markup).toContain("Выберите подтверждённую model или подтвердите риск перед применением.");
    expect(markup).toContain("Я понимаю, что catalog compatibility не подтверждена.");
  });

  it("blocks an unmatched model until the user explicitly acknowledges the warning", async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    const onModelChange = vi.fn(async () => undefined);
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <ModelsPicker
          projectId="project-42"
          service={service}
          initialProject={project}
          initialKeys={keys}
          initialModels={liveModels}
          initialModelId="gpt-5"
          onModelChange={onModelChange}
        />,
      );
    });

    const experimentalModel = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Выбрать модель Vendor Experimental"]',
    );
    expect(experimentalModel).not.toBeNull();
    await act(async () => experimentalModel?.click());

    const applyButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Применить model"),
    );
    const acknowledgement = container.querySelector<HTMLInputElement>('input[type="checkbox"]');
    expect(container.textContent).toContain("Совместимость не подтверждена");
    expect(acknowledgement?.checked).toBe(false);
    expect(applyButton?.disabled).toBe(true);
    expect(onModelChange).not.toHaveBeenCalled();

    await act(async () => acknowledgement?.click());
    expect(acknowledgement?.checked).toBe(true);
    expect(applyButton?.disabled).toBe(false);
    await act(async () => applyButton?.click());

    expect(onModelChange).toHaveBeenCalledTimes(1);
    expect(onModelChange).toHaveBeenCalledWith("project-42", "vendor-experimental");

    await act(async () => root.unmount());
    container.remove();
  });

  it("shows a clear stale current-model warning when live capability no longer contains it", () => {
    const staleModels: LiveModelsResponse = { models: [{ id: "new-model", label: "New model", catalog_matched: true }], source: "live" };
    const markup = renderToStaticMarkup(<ModelsPicker projectId="project-42" service={service} initialProject={project} initialKeys={keys} initialModels={staleModels} initialModelId="gpt-5" onModelChange={() => undefined} />);

    expect(markup).toContain("Текущая model не подтверждена live");
    expect(markup).toContain("gpt-5");
    expect(markup).toContain("старый выбор не будет молча сохранён");
  });
});

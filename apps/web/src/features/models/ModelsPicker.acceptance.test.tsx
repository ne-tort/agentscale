// @vitest-environment jsdom
import { act, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SettingsProvider } from "../../preferences/SettingsProvider";
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

/** Copy comes from the settings provider (Russian by default), so every render is wrapped. */
function render(element: ReactElement): string {
  return renderToStaticMarkup(<SettingsProvider>{element}</SettingsProvider>);
}

describe("AI provider/key/model picker acceptance surface", () => {
  it("renders current selection, searches, capability labels, stale/probe warning, save language and secret safety", () => {
    const markup = render(
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

    expect(markup).toContain("Провайдер, ключ и модель");
    expect(markup).toContain("Team Codex");
    expect(markup).toContain("Текущий");
    expect(markup).toContain("GPT-5");
    expect(markup).toContain("Совместимость подтверждена");
    expect(markup).toContain("Модели подтверждены");
    expect(markup).toContain("Ошибка проверки");
    expect(markup).toContain("Проверка устарела");
    expect(markup).toContain("PROBE_TIMEOUT");
    expect(markup).toContain("Сохранить провайдера и ключ");
    expect(markup).toContain("Применить модель");
    expect(markup).toContain("/projects/:projectId/ai-keys/available");
    expect(markup).toContain("/projects/:projectId/models/live");
    expect(markup).toContain("Секреты не показываются");
    expect(markup.replace(/\u00a0/g, " ")).toContain("128 000 токенов контекста");
    expect(markup.toLowerCase()).not.toContain("secret_ref");
    expect(markup.toLowerCase()).not.toContain("token");
    expect(markup).toContain('id="models-key-search"');
    expect(markup).toContain('id="models-model-search"');
  });

  it("keeps model application behind the explicit host boundary because project PATCH has no model field", () => {
    const markup = render(
      <ModelsPicker projectId="project-42" service={service} initialProject={project} initialKeys={keys} initialModels={liveModels} initialModelId="gpt-5" />,
    );

    expect(markup).toContain("Применение модели здесь недоступно");
    expect(markup).toContain("Применить модель");
  });

  it("shows a clear incompatibility warning before applying an unmatched live model", () => {
    const markup = render(<ModelsPicker projectId="project-42" service={service} initialProject={project} initialKeys={keys} initialModels={liveModels} initialModelId="vendor-experimental" onModelChange={() => undefined} />);

    expect(markup).toContain("Совместимость не подтверждена");
    expect(markup).toContain("Я понимаю, что совместимость не подтверждена.");
    expect(markup).toContain("Выберите другую модель или подтвердите риск.");
  });

  it("blocks an unmatched model until the user explicitly acknowledges the warning", async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    const onModelChange = vi.fn(async () => undefined);
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <SettingsProvider>
          <ModelsPicker
            projectId="project-42"
            service={service}
            initialProject={project}
            initialKeys={keys}
            initialModels={liveModels}
            initialModelId="gpt-5"
            onModelChange={onModelChange}
          />
        </SettingsProvider>,
      );
    });

    const experimentalModel = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Выбрать модель «Vendor Experimental»"]',
    );
    expect(experimentalModel).not.toBeNull();
    await act(async () => experimentalModel?.click());

    const applyButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Применить модель"),
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
    const markup = render(<ModelsPicker projectId="project-42" service={service} initialProject={project} initialKeys={keys} initialModels={staleModels} initialModelId="gpt-5" onModelChange={() => undefined} />);

    expect(markup).toContain("Текущей модели нет в списке");
    expect(markup).toContain("gpt-5");
    expect(markup).toContain("прежний выбор не сохранится молча");
  });

  it("speaks English end to end when the reader chose English", () => {
    window.localStorage.setItem("agentscale.locale", "en");
    try {
      const markup = render(
        <ModelsPicker projectId="project-42" service={service} initialProject={project} initialKeys={keys} initialModels={liveModels} initialModelId="gpt-5" onModelChange={() => undefined} />,
      );

      expect(markup).toContain("Save provider and key");
      expect(markup).toContain("Apply model");
      expect(markup).toContain("Compatibility confirmed");
      expect(markup).toContain("Advanced");
      // The screen a person reads must not leak the Russian unit when English is chosen.
      expect(markup).not.toMatch(/[А-Яа-я]/);
    } finally {
      window.localStorage.removeItem("agentscale.locale");
    }
  });
});

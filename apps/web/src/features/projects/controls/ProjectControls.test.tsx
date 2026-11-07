// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";
import { SettingsProvider } from "../../../preferences/SettingsProvider";
import { ProjectControlsPanel } from "./ProjectControlsPanel";
import type { ProjectControlsProject, ProjectControlsService } from "./types";

const project: ProjectControlsProject = {
  id: "project-42",
  cabinet_id: "cabinet-9",
  name: "Research workspace",
  status: "paused",
  runtime: { observed_state: "suspended" },
  about: "A focused project description.",
  budget_tokens: 0,
  updated_at: "2026-11-01T10:00:00Z",
};

const service: ProjectControlsService = {
  getProject: async () => project,
  patchProject: async () => project,
  runAction: async () => project,
};

function render(element: ReactElement): string {
  return renderToStaticMarkup(<SettingsProvider>{element}</SettingsProvider>);
}

describe("ProjectControlsPanel acceptance surface", () => {
  it("answers what the project is and what can be done with it before any diagnostics", () => {
    const markup = render(<ProjectControlsPanel projectId={project.id} initialProject={project} service={service} />);

    expect(markup).toContain("Research workspace");
    // The state is spelled out in words rather than as the raw `suspended` enum,
    // which survives only inside the disclosure, after "Дополнительно".
    expect(markup).toContain("На паузе");
    expect(markup.indexOf("suspended")).toBeGreaterThan(markup.indexOf("Дополнительно"));
    expect(markup).toContain('href="/projects/project-42/runtime"');
    expect(markup).toContain('href="/projects/project-42/chat"');
    expect(markup).toContain('href="/projects/project-42/files"');
    expect(markup).toContain('href="/projects/project-42/models"');
    expect(markup).toContain("Возобновить");
    expect(markup).toContain("Синхронизировать файлы");
    expect(markup).toContain('aria-label="Запустить. Действие «Запустить» недоступно: состояние «На паузе»."');
  });

  it("folds the configuration form and the runtime detail into a closed disclosure", () => {
    const markup = render(<ProjectControlsPanel projectId={project.id} initialProject={project} service={service} />);

    // `details` without the `open` attribute is collapsed by default, and the
    // fields stay in the DOM so every control is still reachable.
    expect(markup).toContain("<details class=\"project-controls__advanced\">");
    expect(markup).not.toContain("<details class=\"project-controls__advanced\" open");
    expect(markup).toContain("Дополнительно");
    expect(markup).toContain("A focused project description.");
    expect(markup).toContain('name="budget_tokens"');
    expect(markup).toContain('name="about"');
    // The raw project id and the environment reference are diagnostics, not the
    // first thing a person needs.
    expect(markup).toContain("project-42");
    // Copy that describes the platform's own bookkeeping is gone from the screen.
    expect(markup).not.toContain("Project controls");
    expect(markup).not.toContain("Lifecycle");
    expect(markup).not.toContain("Configuration");
    expect(markup).not.toContain("повторное чтение");
  });

  it("keeps a clear loading state when no initial project is supplied", () => {
    const markup = render(<ProjectControlsPanel projectId={project.id} service={service} />);
    expect(markup).toContain("Загружаем настройки проекта");
  });
});

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
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

describe("ProjectControlsPanel acceptance surface", () => {
  it("renders the backend project summary, editable settings, state-gated actions, and workspace links", () => {
    const markup = renderToStaticMarkup(<ProjectControlsPanel projectId={project.id} initialProject={project} service={service} />);

    expect(markup).toContain("Research workspace");
    expect(markup).toContain("Приостановлен");
    expect(markup).toContain("A focused project description.");
    expect(markup).toContain('name="budget_tokens"');
    expect(markup).toContain('name="about"');
    expect(markup).toContain('href="/projects/project-42/runtime"');
    expect(markup).toContain('href="/projects/project-42/chat"');
    expect(markup).toContain('href="/projects/project-42/files"');
    expect(markup).toContain('href="/projects/project-42/models"');
    expect(markup).toContain("Возобновить");
    expect(markup).toContain('aria-label="Запустить. Действие «Запустить» недоступно при состоянии «suspended»."');
    expect(markup).toContain("Синхронизировать workspace");
  });

  it("keeps a clear loading state when no initial project is supplied", () => {
    const markup = renderToStaticMarkup(<ProjectControlsPanel projectId={project.id} service={service} />);
    expect(markup).toContain("Загружаем настройки проекта");
  });
});

import { describe, expect, it, vi } from "vitest";
import type { ProjectSummary } from "../../../api/types";
import { favoriteStorageKey, filterProjects, lifecycleResultMatches, lifecycleTargetReached, observedStateLabel, persistFavorites, projectActions, projectStatusLabels, readFavorites } from "./view-model";

const projects: ProjectSummary[] = [
  { id: "p-1", cabinet_id: "c-1", name: "Market study", about: "Regional demand", status: "active", selected: false, new_chat_enabled: false },
  { id: "p-2", cabinet_id: "c-1", name: "Supplier audit", status: "paused", selected: false, new_chat_enabled: false },
  { id: "p-3", cabinet_id: "c-1", name: "Legacy review", status: "completed", selected: false, new_chat_enabled: false },
  { id: "p-4", cabinet_id: "c-1", name: "Recovery", status: "error", selected: false, new_chat_enabled: false },
];

describe("project overview view model", () => {
  it("combines case-insensitive search, lifecycle, and favorite filters", () => {
    expect(filterProjects(projects, "REGIONAL", "all", false, new Set()).map((project) => project.id)).toEqual(["p-1"]);
    expect(filterProjects(projects, "", "paused", false, new Set()).map((project) => project.id)).toEqual(["p-2"]);
    expect(filterProjects(projects, "", "all", true, new Set(["p-2", "p-4"])).map((project) => project.id)).toEqual(["p-2", "p-4"]);
    expect(filterProjects(projects, "missing", "all", false, new Set())).toEqual([]);
  });

  it("presents real actions for each lifecycle state and readable runtime status", () => {
    expect(projectActions("draft")).toEqual(["launch"]);
    expect(projectActions("active")).toEqual(["pause", "reload", "complete"]);
    expect(projectActions("paused")).toEqual(["complete"]);
    expect(projectActions("error")).toEqual(["reload", "complete"]);
    expect(projectActions("completed")).toEqual([]);
    expect(projectStatusLabels).toMatchObject({ draft: "Черновик", active: "Активен", paused: "Приостановлен", error: "Ошибка", completed: "Завершён" });
    expect(observedStateLabel("pulling")).toBe("Загрузка образа");
    expect(observedStateLabel("new-backend-state")).toBe("new-backend-state");
  });

  it("gates lifecycle operations on observed state and distinguishes a pending resume", () => {
    expect(projectActions("active", "preparing")).toEqual([]);
    expect(projectActions("active", "running")).toEqual(["pause", "reload", "complete"]);
    expect(projectActions("paused", "suspended")).toEqual(["resume", "complete"]);
    expect(projectActions("completed", "suspended")).toEqual(["resume"]);
    expect(projectActions("completed")).toEqual([]);
    expect(projectActions("paused", "future-runtime-state")).toEqual(["complete"]);
    expect(lifecycleResultMatches("resume", { status: "paused", observed_state: "suspended" })).toBe(true);
    expect(lifecycleTargetReached("resume", "paused")).toBe(false);
    expect(lifecycleTargetReached("resume", "active")).toBe(true);
    expect(lifecycleResultMatches("reload", { status: "error" })).toBe(true);
  });

  it("scopes favorite persistence by employee and tolerates unavailable or malformed storage", () => {
    expect(favoriteStorageKey("employee-7")).not.toBe(favoriteStorageKey("employee-8"));
    expect(readFavorites(undefined, "favorites")).toEqual(new Set());
    expect(readFavorites({ getItem: () => "not-json" }, "favorites")).toEqual(new Set());
    const setItem = vi.fn();
    persistFavorites({ setItem }, "favorites", new Set(["p-1", "p-3"]));
    expect(setItem).toHaveBeenCalledWith("favorites", '["p-1","p-3"]');
  });
});

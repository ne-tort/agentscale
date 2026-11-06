import { describe, expect, it } from "vitest";
import type { ProjectControlsProject } from "./types";
import { lifecycleCapabilities, projectBudgetInputValue, projectControlsLinks } from "./view-model";

describe("project controls view model", () => {
  it("exposes lifecycle capabilities by backend project state", () => {
    const enabled = (status: string) => lifecycleCapabilities({ status } as ProjectControlsProject).filter((item) => item.enabled).map((item) => item.action);

    expect(enabled("draft")).toEqual(["launch"]);
    expect(enabled("active")).toEqual(["pause", "reload", "sync", "complete"]);
    expect(enabled("paused")).toEqual(["sync", "complete"]);
    expect(enabled("error")).toEqual(["reload", "complete"]);
    expect(enabled("completed")).toEqual([]);
    expect(enabled("deleted")).toEqual([]);
    expect(lifecycleCapabilities({ status: "active", observed_state: "preparing" } as ProjectControlsProject).filter((item) => item.enabled).map((item) => item.action))
      .toEqual([]);
    expect(lifecycleCapabilities({ status: "active", observed_state: "running" } as ProjectControlsProject).filter((item) => item.enabled).map((item) => item.action))
      .toEqual(["pause", "reload", "sync", "complete"]);
    expect(lifecycleCapabilities({ status: "paused", runtime: { observed_state: "suspended" } } as ProjectControlsProject).filter((item) => item.enabled).map((item) => item.action))
      .toEqual(["resume"]);
    expect(lifecycleCapabilities({ status: "completed", runtime: { observed_state: "suspended" } } as ProjectControlsProject).filter((item) => item.enabled).map((item) => item.action))
      .toEqual(["resume"]);
    expect(lifecycleCapabilities({ status: "paused" } as ProjectControlsProject).filter((item) => item.enabled).map((item) => item.action))
      .toEqual(["sync", "complete"]);
    expect(lifecycleCapabilities({ status: "paused", observed_state: "future-runtime-state" } as ProjectControlsProject).filter((item) => item.enabled).map((item) => item.action))
      .toEqual(["sync", "complete"]);
    expect(lifecycleCapabilities({ status: "completed", observed_state: "future-runtime-state" } as ProjectControlsProject).filter((item) => item.enabled).map((item) => item.action))
      .toEqual([]);
  });

  it("builds project-scoped links without changing encoded identifiers", () => {
    expect(projectControlsLinks("project/42")).toEqual({
      runtime: "/projects/project%2F42/runtime",
      chat: "/projects/project%2F42/chat",
      files: "/projects/project%2F42/files",
      models: "/projects/project%2F42/models",
    });
    expect(projectControlsLinks("p-1", { models: "/projects/p-1/configuration/models" }).models)
      .toBe("/projects/p-1/configuration/models");
  });

  it("keeps an unset budget distinguishable from zero", () => {
    expect(projectBudgetInputValue(null)).toBe("");
    expect(projectBudgetInputValue(undefined)).toBe("");
    expect(projectBudgetInputValue(0)).toBe("0");
    expect(projectBudgetInputValue(1200)).toBe("1200");
  });
});

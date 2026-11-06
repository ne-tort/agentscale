import { describe, expect, it } from "vitest";
import type { ProjectSummary } from "../../../api/types";
import { projectOverviewHref, filterProjects } from "./view-model";

const visibleProject: ProjectSummary = {
  id: "project / 42",
  cabinet_id: "cabinet-9",
  name: "Operations workspace",
  about: "Vendor and delivery review",
  status: "active",
  selected: false,
  new_chat_enabled: false,
};

describe("project overview URL and filtering acceptance", () => {
  it("preserves a project as a direct URL target while filtering only the visible list", () => {
    expect(projectOverviewHref(visibleProject.id)).toBe("/projects/project%20%2F%2042");
    expect(filterProjects([visibleProject], "delivery", "active", false, new Set())).toEqual([visibleProject]);
    expect(filterProjects([visibleProject], "delivery", "paused", false, new Set())).toEqual([]);
  });
});

import { describe, expect, it } from "vitest";
import { resourceWorkspaceFixture } from "./fixtures";
import {
  canPlanMaterialization,
  canSyncMaterialization,
  formatGeneration,
  latestVersionNumber,
  selectVisibleResources,
  summarizeDiff,
  versionsForResource,
} from "./view-model";
import type { ResourceServiceCapabilities } from "./contracts";

const enabled: ResourceServiceCapabilities = {
  readWorkspace: true,
  readDiff: true,
  updateProfile: true,
  createResource: true,
  createVersion: true,
  bindResource: true,
  planMaterialization: true,
  syncMaterialization: true,
};

// Declared with the full capability key set so a new capability breaks the test
// instead of silently staying `true` through an untyped Object.fromEntries cast.
const disabledFlags: Record<keyof ResourceServiceCapabilities, boolean> = {
  readWorkspace: false,
  readDiff: false,
  updateProfile: false,
  createResource: false,
  createVersion: false,
  bindResource: false,
  planMaterialization: false,
  syncMaterialization: false,
};
const disabled: ResourceServiceCapabilities = disabledFlags;

describe("resource workspace view model", () => {
  it("filters typed resources without flattening prompts, files, or MCP", () => {
    expect(selectVisibleResources(resourceWorkspaceFixture.resources, "review", "all").map((resource) => resource.kind)).toEqual(["prompt", "file"]);
    expect(selectVisibleResources(resourceWorkspaceFixture.resources, "", "mcp").map((resource) => resource.id)).toEqual(["resource-mcp"]);
    expect(selectVisibleResources(resourceWorkspaceFixture.resources, "operations cabinet", "file").map((resource) => resource.id)).toEqual(["resource-file"]);
  });

  it("orders immutable versions newest first and preserves exact active version", () => {
    const versions = versionsForResource(resourceWorkspaceFixture.versions, "resource-prompt");
    expect(versions.map((version) => version.number)).toEqual([2, 1]);
    expect(latestVersionNumber(resourceWorkspaceFixture.resources[0], resourceWorkspaceFixture.versions)).toBe(2);
  });

  it("only enables lifecycle commands when the typed service capability and target exist", () => {
    expect(canPlanMaterialization(disabled, "project-ops")).toBe(false);
    expect(canPlanMaterialization(enabled, "")).toBe(false);
    expect(canPlanMaterialization(enabled, "project-ops")).toBe(true);
    expect(canSyncMaterialization(enabled, resourceWorkspaceFixture.materialization)).toBe(true);
    expect(canSyncMaterialization(disabled, resourceWorkspaceFixture.materialization)).toBe(false);
    expect(canSyncMaterialization(enabled, { ...resourceWorkspaceFixture.materialization!, status: "blocked" })).toBe(false);
    expect(canSyncMaterialization(enabled, { ...resourceWorkspaceFixture.materialization!, status: "failed" })).toBe(false);
    expect(canSyncMaterialization(enabled, { ...resourceWorkspaceFixture.materialization!, status: "applying" })).toBe(false);
  });

  it("keeps generation and diff counts inspectable", () => {
    expect(formatGeneration(4, 3)).toBe("g4 desired · g3 applied");
    expect(formatGeneration(4)).toBe("g4 desired · unapplied");
    expect(summarizeDiff({
      resourceId: "resource-prompt",
      fromVersion: 1,
      toVersion: 2,
      summary: "Added evidence requirement",
      fromDigest: "sha256:old",
      toDigest: "sha256:new",
      lines: [
        { type: "context", text: "Review" },
        { type: "deletion", text: "without evidence" },
        { type: "addition", text: "with evidence" },
      ],
    })).toEqual({ additions: 1, deletions: 1, context: 1 });
  });
});

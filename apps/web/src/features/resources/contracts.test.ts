import { describe, expect, it } from "vitest";
import { RESOURCE_CONTRACT_BLOCKERS } from "./blockers";
import { createBlockedResourceService, ResourceContractBlockedError } from "./contracts";


describe("resource service boundary", () => {
  it("keeps every unconfirmed operation blocked and reports no capabilities", async () => {
    const service = createBlockedResourceService();

    expect(Object.values(service.capabilities).every((value) => value === false)).toBe(true);
    await expect(service.loadWorkspace({ profileId: "profile-ops" })).rejects.toBeInstanceOf(ResourceContractBlockedError);
    await expect(service.planMaterialization({ profileId: "profile-ops", projectId: "project-ops" })).rejects.toMatchObject({ operation: "Planning materialization" });
    await expect(service.syncMaterialization({ planId: "plan-ops", expectedGeneration: 4 })).rejects.toBeInstanceOf(ResourceContractBlockedError);
  });

  it("keeps blockers explicit so a UI cannot turn a missing adapter into success", () => {
    expect(RESOURCE_CONTRACT_BLOCKERS.map((blocker) => blocker.code)).toEqual([
      "resource_web_facade_missing",
      "resource_scope_gate_blocked",
      "resource_snapshot_adapter_missing",
      "materialization_apply_adapter_missing",
      "resource_promotion_blocked",
    ]);
    expect(RESOURCE_CONTRACT_BLOCKERS.every((blocker) => blocker.severity === "blocking")).toBe(true);
  });
});

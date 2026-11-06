import { describe, expect, it, vi } from "vitest";
import { patchProjectWithReadback, runProjectActionWithReadback } from "./operations";
import type { ProjectControlsProject, ProjectControlsService } from "./types";

const before: ProjectControlsProject = { id: "p-1", cabinet_id: "c-1", name: "Before", status: "draft" };
const after: ProjectControlsProject = { ...before, name: "After", status: "active" };

function service(overrides: Partial<ProjectControlsService> = {}): ProjectControlsService {
  return {
    getProject: vi.fn(async () => after),
    patchProject: vi.fn(async () => after),
    runAction: vi.fn(async () => after),
    ...overrides,
  };
}

describe("project controls mutation readback", () => {
  it("reads the project after PATCH and returns the readback as authoritative", async () => {
    const api = service({
      patchProject: vi.fn(async () => ({ ...before, name: "Patch response" })),
      getProject: vi.fn(async () => after),
    });

    const result = await patchProjectWithReadback(api, "p-1", { name: "After" });

    expect(api.patchProject).toHaveBeenCalledWith("p-1", { name: "After" });
    expect(api.getProject).toHaveBeenCalledWith("p-1");
    expect(result).toEqual({ project: after, readbackVerified: true, mutationAccepted: true });
  });

  it("forwards a validated mutation response before the readback finishes", async () => {
    const onMutationAccepted = vi.fn();
    const api = service({
      patchProject: vi.fn(async () => after),
      getProject: vi.fn(async () => after),
    });

    await patchProjectWithReadback(api, "p-1", { name: "After" }, onMutationAccepted);

    expect(onMutationAccepted).toHaveBeenCalledWith(after);
  });

  it("reads back after lifecycle success and reports a readback failure without fabricating status", async () => {
    const readbackError = new Error("offline");
    const api = service({
      runAction: vi.fn(async () => after),
      getProject: vi.fn(async () => { throw readbackError; }),
    });

    const result = await runProjectActionWithReadback(api, "p-1", "launch");

    expect(api.runAction).toHaveBeenCalledWith("p-1", "launch");
    expect(result).toEqual({ project: after, readbackVerified: false, mutationAccepted: true, readbackError });
  });

  it("does not attempt readback when the write itself fails", async () => {
    const writeError = new Error("rejected");
    const api = service({ patchProject: vi.fn(async () => { throw writeError; }) });

    await expect(patchProjectWithReadback(api, "p-1", { name: "After" })).rejects.toBe(writeError);
    expect(api.getProject).not.toHaveBeenCalled();
  });

  it("rejects a lifecycle route result that reports an unrelated status", async () => {
    const paused: ProjectControlsProject = { ...before, status: "paused" };
    const api = service({
      runAction: vi.fn(async () => paused),
      getProject: vi.fn(async () => paused),
    });

    const result = await runProjectActionWithReadback(api, "p-1", "launch");
    expect(result).toMatchObject({ mutationAccepted: true, readbackVerified: false });
    expect(result.readbackError).toBeInstanceOf(Error);
    expect(api.getProject).toHaveBeenCalledWith("p-1");
  });

  it("keeps an idempotent paused resume unverified when GET has no runtime evidence", async () => {
    const pending: ProjectControlsProject = { ...before, status: "paused" };
    const api = service({ runAction: vi.fn(async () => pending), getProject: vi.fn(async () => pending) });

    const result = await runProjectActionWithReadback(api, "p-1", "resume");
    expect(result.readbackVerified).toBe(false);
    expect(result.mutationAccepted).toBe(true);
    expect(result.readbackError).toBeInstanceOf(Error);
  });

  it("accepts a paused resume readback only when runtime observation confirms an in-progress Pod", async () => {
    const pending: ProjectControlsProject = { ...before, status: "paused", observed_state: "suspended", runtime: { observed_state: "suspended", desired_state: "running" } };
    const api = service({ runAction: vi.fn(async () => ({ ...pending, runtime: undefined })), getProject: vi.fn(async () => pending) });
    const result = await runProjectActionWithReadback(api, "p-1", "resume");
    expect(result.readbackVerified).toBe(true);
    expect(result.targetReached).toBe(false);
  });

  it("does not verify when project GET disagrees with the lifecycle route result", async () => {
    const paused: ProjectControlsProject = { ...after, status: "paused" };
    const api = service({
      runAction: vi.fn(async () => after),
      getProject: vi.fn(async () => paused),
    });

    const result = await runProjectActionWithReadback(api, "p-1", "launch");
    expect(result.readbackVerified).toBe(false);
    expect(result.readbackError).toBeInstanceOf(Error);
  });
});

import { describe, expect, it } from "vitest";
import { matchPath } from "react-router-dom";

describe("URL-first route contract", () => {
  it("keeps project and chat deep links addressable", () => {
    expect(matchPath("/projects/:projectId", "/projects/p-123")?.params.projectId).toBe("p-123");
    expect(matchPath("/projects/:projectId/chat/:sessionId", "/projects/p-123/chat/s-456")?.params).toEqual({ projectId: "p-123", sessionId: "s-456" });
  });
});

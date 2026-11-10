import { describe, expect, it } from "vitest";
import { buildPluginSrc, normalizeRpcOperations } from "./bootstrap";
import { PluginFrameError } from "./errors";
import { pluginRoutePathFromLocation } from "./routePath";

const ORIGIN = "https://plugins.example.test";
const SHELL = "https://agentscale.example.test";
const LIST = {
  capability_id: "hello.greetings",
  capability_version: "1.0",
  operation: "list",
  http_method: "GET",
  backend_path: "/api/greetings/list",
  public: true,
} as const;

describe("signed bootstrap frontend declaration", () => {
  it("joins custom base_path and nested frontend_entry without tenant path reconstruction", () => {
    expect(buildPluginSrc(ORIGIN, { entry: "assets/shell.html", basePath: "/custom/company/plugin" }))
      .toBe(`${ORIGIN}/custom/company/plugin/assets/shell.html`);
    expect(buildPluginSrc(ORIGIN, { entry: "index.html", basePath: "/" }))
      .toBe(`${ORIGIN}/index.html`);
  });

  it("rejects insecure/shell origins, traversal and URL escape", () => {
    expect(() => buildPluginSrc("http://plugins.example.test", { entry: "index.html", basePath: "/" }))
      .toThrowError(PluginFrameError);
    expect(() => buildPluginSrc(SHELL, { entry: "index.html", basePath: "/", shellOrigin: SHELL }))
      .toThrowError(PluginFrameError);
    for (const declaration of [
      { entry: "../secret", basePath: "/custom" },
      { entry: "/absolute", basePath: "/custom" },
      { entry: "shell.html?token=x", basePath: "/custom" },
      { entry: "shell.html", basePath: "/custom/../secret" },
      { entry: "shell.html", basePath: "//evil.example" },
      { entry: "shell.html", basePath: "/%2e%2e/secret" },
    ]) {
      expect(() => buildPluginSrc(ORIGIN, declaration)).toThrowError(PluginFrameError);
    }
  });

  it("binds @1.0 operations to capabilities and rejects duplicates/public writes", () => {
    expect(normalizeRpcOperations([LIST], ["hello.greetings@1.0"], true)).toEqual([LIST]);
    expect(normalizeRpcOperations([LIST], ["hello.greetings@1"], false)).toBeUndefined();
    expect(normalizeRpcOperations([LIST, LIST], ["hello.greetings@1.0"], false)).toBeUndefined();
    expect(normalizeRpcOperations(
      [{ ...LIST, operation: "greet", http_method: "POST", backend_path: "/api/greetings" }],
      ["hello.greetings@1.0"],
      true,
    )).toBeUndefined();
  });
});

describe("shell nested routePath extraction", () => {
  it("extracts private and public nested paths from location", () => {
    expect(pluginRoutePathFromLocation("/plugins/hello_plugin/co_123/docs/section/42"))
      .toBe("/docs/section/42");
    expect(pluginRoutePathFromLocation("/public/plugins/co_123/hello_plugin/docs/welcome", true))
      .toBe("/docs/welcome");
    expect(pluginRoutePathFromLocation("/plugins/hello_plugin/co_123", true)).toBe("");
    expect(pluginRoutePathFromLocation("/public/plugins/co_123/hello_plugin", true)).toBe("/");
  });

  it("fails closed for non-canonical nested paths", () => {
    expect(pluginRoutePathFromLocation("/plugins/hello_plugin/co_123/../admin")).toBe("");
    expect(pluginRoutePathFromLocation("/plugins/hello_plugin/co_123/docs/")).toBe("");
    expect(pluginRoutePathFromLocation("/plugins/hello_plugin/co_123/%2e%2e/admin")).toBe("");
  });
});

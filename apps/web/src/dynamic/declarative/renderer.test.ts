// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { renderDeclarativeApp } from "./renderer";

const manifest = {
  protocol: "1.0", appId: "demo-app", version: "1.0.0", displayName: "Demo",
  entrypoints: { ui: { kind: "declarative-ui", root: { type: "stack", children: [
    { type: "text", id: "safe-label", props: { text: "<img src=x onerror=alert(1)>" } },
    { type: "button", id: "action", props: { label: "Run" } },
  ] } } }, requestedCapabilities: [],
};

describe("declarative renderer", () => {
  it("renders allowlisted elements as text, never parses HTML", () => {
    const container = document.createElement("main");
    renderDeclarativeApp(container, manifest);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("renders table loading/error/empty and ready rows as text only", () => {
    const tableManifest = { ...manifest, entrypoints: { ui: { kind: "declarative-ui", root: { type: "table", id: "orders", props: { label: "Orders" } } } } };
    const loading = document.createElement("main");
    renderDeclarativeApp(loading, tableManifest, { orders: { state: "loading" } });
    expect(loading.textContent).toContain("Loading");
    const ready = document.createElement("main");
    renderDeclarativeApp(ready, tableManifest, { orders: { state: "ready", columns: ["name"], rows: [{ name: "<script>" }] } });
    expect(ready.querySelector("script")).toBeNull();
    expect(ready.textContent).toContain("<script>");
  });

  it("rejects unknown component discriminants", () => {
    const invalid = structuredClone(manifest);
    (invalid.entrypoints.ui.root.children[0] as { type: string }).type = "html";
    expect(() => renderDeclarativeApp(document.createElement("main"), invalid)).toThrow("invalid_declarative_ui");
  });

  it("does not wire button interactions to unapproved command execution", () => {
    const container = document.createElement("main");
    renderDeclarativeApp(container, manifest);
    const button = container.querySelector("button");
    expect(button?.disabled).toBe(true);
    expect(button?.getAttribute("aria-disabled")).toBe("true");
  });
});

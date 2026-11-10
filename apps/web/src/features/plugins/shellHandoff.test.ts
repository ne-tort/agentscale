import { describe, expect, it } from "vitest";

import { PluginFrameError } from "./errors";
import { buildPluginShellHandoff, resolvePluginShellOrigin } from "./shellHandoff";

const SHELL = "https://agentscale.example.test";

describe("resolvePluginShellOrigin", () => {
  it("returns the exact https origin", () => {
    expect(resolvePluginShellOrigin(SHELL)).toBe(SHELL);
  });

  it.each([
    ["", "plugin_shell_origin_required"],
    ["  https://a.test", "plugin_shell_origin_required"],
    ["https://a.test ", "plugin_shell_origin_required"],
    ["https://*.example.test", "plugin_wildcard_origin_denied"],
    ["http://agentscale.example.test", "plugin_shell_origin_insecure"],
    ["https://agentscale.example.test/path", "plugin_shell_origin_not_exact"],
    ["https://agentscale.example.test?x=1", "plugin_shell_origin_not_exact"],
    ["https://agentscale.example.test#frag", "plugin_shell_origin_not_exact"],
    ["https://user:pass@agentscale.example.test", "plugin_shell_origin_not_exact"],
    ["HTTPS://AGENTSCALE.EXAMPLE.TEST", "plugin_shell_origin_not_exact"],
  ])("rejects %s", (raw, reason) => {
    expect(() => resolvePluginShellOrigin(raw)).toThrow(PluginFrameError);
    try {
      resolvePluginShellOrigin(raw);
    } catch (error) {
      expect((error as PluginFrameError).code).toBe("originMismatch");
      expect((error as PluginFrameError).detail).toBe(reason);
    }
  });
});

describe("buildPluginShellHandoff", () => {
  it("serialises the bounded DTO the iframe reads from its name", () => {
    const digest = `sha256:${'a'.repeat(64)}`;
    const payload = JSON.parse(
      buildPluginShellHandoff({
        shellOrigin: SHELL,
        pluginId: "hello_plugin",
        manifestVersion: "1.2.3",
        artifactDigest: digest,
      }),
    );
    expect(payload).toEqual({
      protocol: "agentscale-plugin-shell/1",
      plugin_shell_origin: SHELL,
      plugin_id: "hello_plugin",
      manifest_version: "1.2.3",
      artifact_digest: digest,
    });
  });

  it("never emits a wildcard origin", () => {
    expect(() =>
      buildPluginShellHandoff({
        shellOrigin: "*",
        pluginId: "hello_plugin",
        manifestVersion: "1.2.3",
        artifactDigest: `sha256:${'a'.repeat(64)}`,
      }),
    ).toThrow(PluginFrameError);
  });
});

import { PluginFrameError } from "./errors";

export const PLUGIN_SHELL_HANDOFF_PROTOCOL = "agentscale-plugin-shell/1";

export interface PluginShellHandoffInput {
  readonly shellOrigin: string;
  readonly pluginId: string;
  readonly manifestVersion: string;
  readonly artifactDigest: string;
}

/** Validate the exact server-derived DTO origin; browser location is never consulted. */
export function resolvePluginShellOrigin(raw: string): string {
  if (!raw || raw !== raw.trim()) {
    throw new PluginFrameError("originMismatch", "plugin_shell_origin_required");
  }
  if (raw.includes("*")) {
    throw new PluginFrameError("originMismatch", "plugin_wildcard_origin_denied");
  }
  let parsed: URL;
  try { parsed = new URL(raw); }
  catch { throw new PluginFrameError("originMismatch", "plugin_shell_origin_invalid"); }
  if (parsed.protocol !== "https:") {
    throw new PluginFrameError("originMismatch", "plugin_shell_origin_insecure");
  }
  if (parsed.username || parsed.password || parsed.pathname !== "/"
    || parsed.search || parsed.hash || parsed.origin !== raw) {
    throw new PluginFrameError("originMismatch", "plugin_shell_origin_not_exact");
  }
  return parsed.origin;
}

/** Serialize the bounded public-safe DTO consumed as iframe.name/window.name. */
export function buildPluginShellHandoff(input: PluginShellHandoffInput): string {
  const shellOrigin = resolvePluginShellOrigin(input.shellOrigin);
  return JSON.stringify({
    protocol: PLUGIN_SHELL_HANDOFF_PROTOCOL,
    plugin_shell_origin: shellOrigin,
    plugin_id: input.pluginId,
    manifest_version: input.manifestVersion,
    artifact_digest: input.artifactDigest,
  });
}

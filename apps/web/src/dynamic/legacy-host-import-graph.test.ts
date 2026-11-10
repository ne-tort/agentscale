import { describe, expect, it } from "vitest";

/**
 * Static source graph, deliberately independent of the legacy host modules.
 * Raw glob imports keep this test inside the web/Vite contract without adding
 * Node fs/types to the browser tsconfig.
 */
const modules = import.meta.glob("../**/*.{ts,tsx,md}", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

const REMOVED_LEGACY_FILES = [
  "dynamic/broker.ts",
  "dynamic/plugin-host.ts",
  "dynamic/plugin-host.test.ts",
  "dynamic/declarative/host.tsx",
  "dynamic/declarative/index.ts",
  "dynamic/declarative/acceptance.test.tsx",
] as const;

const REMOVED_LEGACY_MODULE_STEMS = [
  "dynamic/broker",
  "dynamic/plugin-host",
  "dynamic/declarative",
  "dynamic/declarative/host",
  "dynamic/declarative/index",
] as const;

const CURRENT_PLUGIN_PATH = [
  "app/router.tsx",
  "app/plugin-routes.tsx",
  "api/client.ts",
  "features/plugins/PluginPage.tsx",
  "features/plugins/pluginInfo.ts",
  "features/plugins/bootstrap.ts",
  "features/plugins/usePluginFrame.ts",
  "features/plugins/PluginFrame.tsx",
  "features/plugins/rpc.ts",
  "features/plugins/operationBroker.ts",
] as const;

function canonicalGlobKey(key: string): string {
  if (key.startsWith("./")) return `dynamic/${key.slice(2)}`;
  if (key.startsWith("../")) return key.slice(3);
  return key;
}

const sources = new Map<string, string>(
  Object.entries(modules).map(([key, source]) => [canonicalGlobKey(key), source]),
);

function normalizePath(path: string): string {
  const result: string[] = [];
  for (const segment of path.split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === ".." && result.length > 0 && result[result.length - 1] !== "..") result.pop();
    else result.push(segment);
  }
  return result.join("/");
}

function moduleBase(importer: string, specifier: string): string | undefined {
  if (!specifier.startsWith(".")) return undefined;
  const importerDirectory = importer.split("/").slice(0, -1).join("/");
  return normalizePath(`${importerDirectory}/${specifier}`);
}

function specifiers(source: string): string[] {
  const result: string[] = [];
  const staticImports = /(?:^|[\r\n])\s*(?:import|export)\s+(?:type\s+)?(?:[^"']*?\sfrom\s+)?["']([^"']+)["']/g;
  const dynamicImports = /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g;
  for (const pattern of [staticImports, dynamicImports]) {
    for (const match of source.matchAll(pattern)) result.push(match[1]);
  }
  return result;
}

function resolveSource(importer: string, specifier: string): string | undefined {
  const base = moduleBase(importer, specifier);
  if (base === undefined || base.startsWith("../")) return undefined;
  // Styles and other assets are edges, but not TypeScript graph vertices.
  if (/\.[a-z0-9]+$/i.test(base) && !/\.[jt]sx?$/i.test(base)) return undefined;
  const withoutJsExtension = base.replace(/\.jsx?$/i, "");
  const candidates = [
    base,
    `${withoutJsExtension}.ts`,
    `${withoutJsExtension}.tsx`,
    `${withoutJsExtension}/index.ts`,
    `${withoutJsExtension}/index.tsx`,
  ];
  const resolved = candidates.find((candidate) => sources.has(candidate) && /\.tsx?$/.test(candidate));
  if (!resolved) throw new Error(`Unresolved source import ${specifier} in ${importer}`);
  return resolved;
}

function reachableFrom(entry: string): ReadonlySet<string> {
  const visited = new Set<string>();
  const queue = [entry];
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (visited.has(current)) continue;
    visited.add(current);
    const source = sources.get(current);
    if (source === undefined) throw new Error(`Missing graph entry ${current}`);
    for (const specifier of specifiers(source)) {
      const resolved = resolveSource(current, specifier);
      if (resolved && !visited.has(resolved)) queue.push(resolved);
    }
  }
  return visited;
}

describe("plugin bootstrap static import graph", () => {
  it("keeps the signed bootstrap and plugin-rpc path reachable from the shell entry", () => {
    const reachable = reachableFrom("main.tsx");
    for (const required of CURRENT_PLUGIN_PATH) {
      expect(reachable.has(required), `${required} must remain reachable from main.tsx`).toBe(true);
    }
  });

  it("does not allow the removed legacy host modules or imports to return", () => {
    for (const removed of REMOVED_LEGACY_FILES) {
      expect(sources.has(removed), `${removed} must stay removed`).toBe(false);
    }
    for (const [importer, source] of sources) {
      if (!/\.tsx?$/.test(importer)) continue;
      for (const specifier of specifiers(source)) {
        const base = moduleBase(importer, specifier);
        if (base === undefined) continue;
        const moduleStem = base.replace(/\.tsx?$/, "");
        expect(
          REMOVED_LEGACY_MODULE_STEMS.includes(moduleStem as typeof REMOVED_LEGACY_MODULE_STEMS[number]),
          `${importer} must not import removed legacy module ${base}`,
        ).toBe(false);
      }
    }
  });
});

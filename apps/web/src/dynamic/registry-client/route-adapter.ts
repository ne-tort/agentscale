import type { SignedRegistrySnapshot } from "../registry";

export interface RegistryRoutePointer {
  readonly appId: string;
  readonly version: string;
  readonly routeId: string;
}

export type RegistryRouteLookup =
  | { readonly state: "match"; readonly pointer: RegistryRoutePointer }
  | { readonly state: "not_found" }
  | { readonly state: "ambiguous" }
  | { readonly state: "invalid_path" }
  | { readonly state: "invalid_snapshot" };

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validPath(pathname: string): boolean {
  if (!pathname.startsWith("/") || pathname.startsWith("//") || /[\\?#\u0000-\u001f\u007f]/.test(pathname)) return false;
  try {
    return pathname.split("/").every((segment) => {
      const decoded = decodeURIComponent(segment);
      return decoded !== "." && decoded !== ".." && !decoded.includes("/") && !decoded.includes("\\");
    });
  } catch {
    return false;
  }
}

/**
 * Turns a URL path into an untrusted registry pointer. The caller must still resolve that
 * pointer through SignedRegistryResolver; this adapter never returns a manifest or a grant.
 */
export function routeToSnapshotPointer(snapshotHint: unknown, pathname: string): RegistryRouteLookup {
  try {
    if (typeof pathname !== "string" || !validPath(pathname)) return { state: "invalid_path" };
    if (!record(snapshotHint) || !Array.isArray(snapshotHint.entries)) return { state: "invalid_snapshot" };

    const matches: RegistryRoutePointer[] = [];
    for (const entry of snapshotHint.entries) {
      if (!record(entry) || typeof entry.appId !== "string" || typeof entry.version !== "string" || !Array.isArray(entry.routes)) continue;
      for (const route of entry.routes) {
        if (!record(route) || route.path !== pathname || typeof route.id !== "string") continue;
        matches.push(Object.freeze({ appId: entry.appId, version: entry.version, routeId: route.id }));
      }
    }

    if (matches.length === 0) return { state: "not_found" };
    if (matches.length > 1) return { state: "ambiguous" };
    return { state: "match", pointer: matches[0] };
  } catch {
    return { state: "invalid_snapshot" };
  }
}

export type RegistryRouteSnapshotHint = Pick<SignedRegistrySnapshot, "entries">;
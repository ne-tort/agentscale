import { ContentOpsError, type ContentOpsService, type ContentOpsServiceOptions } from "./contracts";
import type {
  ContentAclEntry,
  ContentAsset,
  ContentAssetVersion,
  CreateContentAssetInput,
  DownloadedContentVersion,
} from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function safeAsset(value: unknown): ContentAsset {
  if (!isRecord(value) || typeof value.id !== "string") {
    throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned an invalid asset.");
  }
  return {
    id: value.id,
    owner_scope: text(value.owner_scope) ?? "",
    owner_company_id: text(value.owner_company_id),
    visibility: text(value.visibility) ?? "",
    mime: text(value.mime),
    title: text(value.title),
    tags: Array.isArray(value.tags) ? value.tags.filter((tag): tag is string => typeof tag === "string") : [],
    created_by: text(value.created_by),
    latest_version: typeof value.latest_version === "number" ? value.latest_version : null,
    created_at: text(value.created_at),
    updated_at: text(value.updated_at),
  };
}

function safeVersion(value: unknown, expectedAssetId?: string): ContentAssetVersion {
  if (!isRecord(value) || typeof value.id !== "string" || typeof value.asset_id !== "string" || typeof value.version !== "number") {
    throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned an invalid version.");
  }
  if (expectedAssetId !== undefined && value.asset_id !== expectedAssetId) {
    throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned a version for a different asset.");
  }
  return {
    id: value.id,
    asset_id: value.asset_id,
    version: value.version,
    size: typeof value.size === "number" ? value.size : 0,
    sha256: text(value.sha256),
    etag: text(value.etag),
    object_metadata: safeObjectMetadata(value.object_metadata),
    created_at: text(value.created_at),
  };
}

function safeObjectMetadata(value: unknown): Record<string, unknown> {
  const safe = safeMetadataValue(value);
  return isRecord(safe) ? safe : {};
}

function safeMetadataValue(value: unknown): unknown {
  if (typeof value === "string") return /^[a-z][a-z\d+.-]*:/i.test(value) || value.startsWith("//") ? undefined : value;
  if (Array.isArray(value)) return value.map(safeMetadataValue).filter((item) => item !== undefined);
  if (!isRecord(value)) return value;
  const safe: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    if (/(storage.?key|url|uri)$/i.test(key)) continue;
    const sanitized = safeMetadataValue(item);
    if (sanitized !== undefined) safe[key] = sanitized;
  }
  return safe;
}

function safeAclEntry(value: unknown): ContentAclEntry {
  if (!isRecord(value) || typeof value.principal_kind !== "string" || typeof value.principal_id !== "string" || typeof value.permission !== "string") {
    throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned an invalid access entry.");
  }
  const kinds = ["employee", "company", "platform"];
  const permissions = ["read", "write", "admin"];
  if (!kinds.includes(value.principal_kind) || !permissions.includes(value.permission)) {
    throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned an unsupported access entry.");
  }
  return {
    ...(typeof value.id === "string" ? { id: value.id } : {}),
    principal_kind: value.principal_kind as ContentAclEntry["principal_kind"],
    principal_id: value.principal_id,
    permission: value.permission as ContentAclEntry["permission"],
  };
}

function safeFilename(value: string | null, fallback: string): string {
  if (!value) return fallback;
  const disposition = value.toLowerCase();
  const encodedMarker = "filename*=utf-8''";
  const encodedStart = disposition.indexOf(encodedMarker);
  const encoded = encodedStart >= 0 ? value.slice(encodedStart + encodedMarker.length).split(";")[0]?.trim() : undefined;
  let filename = fallback;
  if (encoded) {
    try {
      filename = decodeURIComponent(encoded.replace(/^"|"$/g, ""));
    } catch {
      return fallback;
    }
  } else {
    filename = value.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
  }
  const basename = filename.split(String.fromCharCode(92)).join("/").split("/").pop() ?? "";
  const safe = Array.from(basename).filter((character) => {
    const code = character.charCodeAt(0);
    return code >= 32 && code !== 127;
  }).join("");
  return safe || fallback.split("/").pop() || "download";
}

function safeError(response: Response): ContentOpsError {
  const status = response.status;
  const messages: Record<number, string> = {
    401: "Sign in again to use the Content Service.",
    403: "You do not have permission to perform this content operation.",
    404: "The selected asset or version is no longer available.",
    409: "The content changed before this operation completed. Refresh and retry.",
    413: "The file exceeds the 100 MiB upload limit.",
    422: "The content is not ready. Check the selected asset and try again.",
  };
  const fallback = status >= 500 ? "The Content Service encountered an error. Try again." : "The Content Service rejected this request.";
  return new ContentOpsError(`HTTP_${status}`, status, messages[status] ?? fallback);
}

export function createContentOpsService(options: ContentOpsServiceOptions = {}): ContentOpsService {
  const configuredBase = options.baseUrl ?? "/api/v1";
  const baseUrl = configuredBase.endsWith("/") ? configuredBase.slice(0, -1) : configuredBase;
  const fetcher = options.fetcher ?? fetch;

  async function request(path: string, init: RequestInit = {}): Promise<Response> {
    const token = options.getAccessToken?.();
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
    } catch {
      throw new ContentOpsError("OFFLINE", 0, "The Content Service could not be reached. Try again.");
    }
    if (!response.ok) throw safeError(response);
    return response;
  }

  async function json(path: string, init: RequestInit = {}): Promise<unknown> {
    const response = await request(path, init);
    try {
      return await response.json();
    } catch {
      throw new ContentOpsError("INVALID_RESPONSE", response.status, "The Content API returned an invalid response.");
    }
  }

  return {
    async listAssets(companyId, requestOptions) {
      const query = new URLSearchParams({ company_id: companyId, limit: "200" });
      const payload = await json(`/content/assets?${query}`, { signal: requestOptions?.signal });
      if (!isRecord(payload) || !Array.isArray(payload.items)) {
        throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned an invalid asset list.");
      }
      if (payload.items.some((item) => !isRecord(item) || typeof item.id !== "string" || typeof item.owner_company_id !== "string")) {
        throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned an invalid asset list.");
      }
      return payload.items.filter((item) => isRecord(item) && item.owner_company_id === companyId).map(safeAsset);
    },
    async createAsset(input: CreateContentAssetInput, requestOptions) {
      const created = safeAsset(await json("/content/assets", {
        method: "POST",
        signal: requestOptions?.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }));
      if (created.owner_company_id !== input.owner_company_id) {
        throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned an asset outside the requested company.");
      }
      return created;
    },
    async beginVersion(assetId, mime, requestOptions) {
      const payload = await json(`/content/assets/${encodeURIComponent(assetId)}/versions`, {
        method: "POST",
        signal: requestOptions?.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mime }),
      });
      if (!isRecord(payload)) throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned an invalid version.");
      return safeVersion(payload.version, assetId);
    },
    async uploadVersion(assetId, versionId, file, mime, requestOptions) {
      return safeVersion(await json(`/content/assets/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(versionId)}/upload`, {
        method: "PUT",
        signal: requestOptions?.signal,
        headers: { "Content-Type": mime },
        body: file,
      }), assetId);
    },
    async downloadVersion(assetId, versionId, fallbackFilename, requestOptions): Promise<DownloadedContentVersion> {
      const query = new URLSearchParams({ blob_version_id: versionId, proxy: "true" });
      const response = await request(`/content/assets/${encodeURIComponent(assetId)}/download?${query}`, {
        signal: requestOptions?.signal,
        headers: { Accept: "application/octet-stream" },
      });
      return {
        blob: await response.blob(),
        filename: safeFilename(response.headers.get("Content-Disposition"), fallbackFilename),
        contentType: response.headers.get("Content-Type") ?? "application/octet-stream",
      };
    },
    async listAcl(assetId, requestOptions) {
      const payload = await json(`/content/assets/${encodeURIComponent(assetId)}/acl`, { signal: requestOptions?.signal });
      if (!isRecord(payload) || !Array.isArray(payload.entries)) {
        throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned an invalid access list.");
      }
      return payload.entries.map(safeAclEntry);
    },
    async replaceAcl(assetId, entries, requestOptions) {
      const payload = await json(`/content/assets/${encodeURIComponent(assetId)}/acl`, {
        method: "PUT",
        signal: requestOptions?.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries: entries.map(({ principal_kind, principal_id, permission }) => ({ principal_kind, principal_id, permission })) }),
      });
      if (!isRecord(payload) || !Array.isArray(payload.entries)) {
        throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content API returned an invalid access list.");
      }
      return payload.entries.map(safeAclEntry);
    },
  };
}

export { ContentOpsError } from "./contracts";
export type { ContentOpsService, ContentOpsServiceOptions } from "./contracts";

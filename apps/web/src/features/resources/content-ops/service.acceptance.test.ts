import { afterEach, describe, expect, it, vi } from "vitest";
import { ContentOpsError } from "./contracts";
import { createContentOpsService } from "./service";

const asset = {
  id: "cast_asset-1",
  owner_scope: "company",
  owner_company_id: "company-1",
  visibility: "company",
  mime: "text/plain",
  title: "notes.txt",
  tags: [],
  created_by: "employee-1",
  latest_version: 1,
  created_at: "2026-10-01T12:00:00Z",
  updated_at: "2026-10-01T12:00:00Z",
};

const version = {
  id: "cbv_version-1",
  asset_id: asset.id,
  version: 1,
  storage_key: "private/object/key",
  size: 5,
  sha256: "a".repeat(64),
  etag: "opaque-etag",
  object_metadata: {},
  created_at: "2026-10-01T12:00:00Z",
};

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

describe("Content operations acceptance contract", () => {
  it("lists and creates assets only through the confirmed company asset routes", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => String(input).includes("?company_id=")
      ? jsonResponse({ items: [asset] })
      : jsonResponse(asset));
    const service = createContentOpsService({ getAccessToken: () => "test-token", fetcher });

    await service.listAssets("company/1");
    await service.createAsset({ owner_company_id: "company-1", visibility: "company", mime: "text/plain", title: "notes.txt", tags: [] });

    expect(String(fetcher.mock.calls[0]?.[0])).toBe("/api/v1/content/assets?company_id=company%2F1&limit=200");
    expect(fetcher.mock.calls[1]?.[0]).toBe("/api/v1/content/assets");
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({ owner_company_id: "company-1", visibility: "company", mime: "text/plain", title: "notes.txt", tags: [] });
    expect(fetcher.mock.calls[0]?.[1]?.headers).toMatchObject({ Authorization: "Bearer test-token" });
  });

  it("rejects a create response outside the requested company", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({ ...asset, owner_company_id: "company-2" }));
    const service = createContentOpsService({ fetcher });

    await expect(service.createAsset({ owner_company_id: "company-1", visibility: "company", mime: "text/plain", title: "notes.txt", tags: [] }))
      .rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it("never returns assets whose company differs from the requested company", async () => {
    const otherCompanyAsset = { ...asset, id: "other-asset", owner_company_id: "company-2" };
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({ items: [asset, otherCompanyAsset, { id: "malformed" }] }));
    const service = createContentOpsService({ fetcher });

    await expect(service.listAssets("company-1")).resolves.toEqual([expect.objectContaining({ id: asset.id })]);
    expect(String(fetcher.mock.calls[0]?.[0])).toBe("/api/v1/content/assets?company_id=company-1&limit=200");
  });

  it("begins a version, uploads raw bytes, and never exposes upload URLs or storage keys", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => String(input).endsWith("/versions")
      ? jsonResponse({ version: { ...version, size: 0, sha256: null }, upload_url: "https://signed.invalid/private", method: "PUT" })
      : jsonResponse(version));
    const service = createContentOpsService({ fetcher });
    const begun = await service.beginVersion(asset.id, "text/plain");
    const uploaded = await service.uploadVersion(asset.id, begun.id, new Blob(["hello"]), "text/plain");

    expect(fetcher.mock.calls.map(([url, init]) => [String(url), init?.method])).toEqual([
      [`/api/v1/content/assets/${asset.id}/versions`, "POST"],
      [`/api/v1/content/assets/${asset.id}/versions/${version.id}/upload`, "PUT"],
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({ mime: "text/plain" });
    expect(fetcher.mock.calls[1]?.[1]?.headers).toMatchObject({ "Content-Type": "text/plain" });
    expect(uploaded.sha256).toBe("a".repeat(64));
    expect(JSON.stringify({ begun, uploaded })).not.toMatch(/upload_url|storage_key|signed\.invalid|private\/object/);
  });

  it("removes storage keys and signed URLs from nested version metadata", async () => {
    const exposedVersion = { ...version, object_metadata: { source: "upload", storageKey: "private/key", download_url: "https://signed.invalid/file", nested: { signed_url: "https://signed.invalid/file", link: "mailto:private@example.test", label: "safe" } } };
    const service = createContentOpsService({ fetcher: vi.fn(async () => jsonResponse(exposedVersion)) });

    const loaded = await service.uploadVersion(asset.id, version.id, new Blob(["bytes"]), "text/plain");
    expect(loaded.object_metadata).toEqual({ source: "upload", nested: { label: "safe" } });
    expect(JSON.stringify(loaded)).not.toMatch(/storageKey|signed_url|signed\.invalid|private\/key/);
  });

  it("downloads an exact version through proxy mode and does not return the redirect URL", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response("hello", { headers: { "Content-Type": "text/plain", "Content-Disposition": 'attachment; filename="notes.txt"' } }));
    const service = createContentOpsService({ fetcher });
    const result = await service.downloadVersion(asset.id, version.id, "fallback.txt");

    expect(String(fetcher.mock.calls[0]?.[0])).toContain(`/content/assets/${asset.id}/download?blob_version_id=${version.id}&proxy=true`);
    expect(result.filename).toBe("notes.txt");
    expect(await result.blob.text()).toBe("hello");
    expect(JSON.stringify(result)).not.toContain("url");
  });

  it("encodes exact version ids and rejects non-success download responses safely", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({ detail: "private storage key" }, 404));
    const service = createContentOpsService({ fetcher });

    const failure = await service.downloadVersion("asset/1", "version/2", "download.bin").catch((error: unknown) => error);
    expect(failure).toMatchObject({ code: "HTTP_404", status: 404 });
    expect((failure as Error).message).not.toContain("private storage key");
    expect(String(fetcher.mock.calls[0]?.[0])).toBe("/api/v1/content/assets/asset%2F1/download?blob_version_id=version%2F2&proxy=true");
  });

  it("does not expose a client method for an unconfirmed version-history route", () => {
    const service = createContentOpsService({ fetcher: vi.fn() });
    expect("listVersions" in service).toBe(false);
  });

  it("rejects malformed upload version metadata and malformed list envelopes", async () => {
    const malformedVersionService = createContentOpsService({ fetcher: vi.fn(async () => jsonResponse({ version: { id: "version-only" } })) });
    await expect(malformedVersionService.beginVersion(asset.id, "text/plain")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });

    const wrongAssetVersionService = createContentOpsService({ fetcher: vi.fn(async () => jsonResponse({ ...version, asset_id: "company-2-asset" })) });
    await expect(wrongAssetVersionService.uploadVersion(asset.id, version.id, new Blob(["bytes"]), "text/plain"))
      .rejects.toMatchObject({ code: "INVALID_RESPONSE" });

    const malformedListService = createContentOpsService({ fetcher: vi.fn(async () => jsonResponse({ items: "not-an-array" })) });
    await expect(malformedListService.listAssets("company-1")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });

    const malformedAssetService = createContentOpsService({ fetcher: vi.fn(async () => jsonResponse({ items: [{ id: "missing-company" }] })) });
    await expect(malformedAssetService.listAssets("company-1")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it("reads and replaces the complete asset ACL using the confirmed entries envelope", async () => {
    const grant = { principal_kind: "company", principal_id: "company-2", permission: "read" };
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "PUT"
      ? jsonResponse({ entries: [{ id: "acl-1", ...grant }] })
      : jsonResponse({ entries: [{ id: "acl-1", ...grant }] }));
    const service = createContentOpsService({ fetcher });

    await service.listAcl(asset.id);
    await service.replaceAcl(asset.id, [grant]);

    expect(fetcher.mock.calls.map(([url, init]) => [String(url), init?.method ?? "GET"])).toEqual([
      [`/api/v1/content/assets/${asset.id}/acl`, "GET"],
      [`/api/v1/content/assets/${asset.id}/acl`, "PUT"],
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({ entries: [grant] });
  });

  it("maps upload-limit errors to a safe message without exposing server details", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({ detail: "https://signed.invalid?X-Amz-Credential=raw-secret" }, 413));
    const service = createContentOpsService({ fetcher });

    await expect(service.listAssets("company-1")).rejects.toMatchObject({
      code: "HTTP_413",
      status: 413,
      message: "The file exceeds the 100 MiB upload limit.",
    } satisfies Partial<ContentOpsError>);
  });
});

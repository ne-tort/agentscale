import type {
  ContentAclEntry,
  ContentAsset,
  ContentAssetVersion,
  CreateContentAssetInput,
  DownloadedContentVersion,
} from "./types";

export interface ContentOpsService {
  listAssets(companyId: string, options?: { signal?: AbortSignal }): Promise<ContentAsset[]>;
  createAsset(input: CreateContentAssetInput, options?: { signal?: AbortSignal }): Promise<ContentAsset>;
  beginVersion(assetId: string, mime: string, options?: { signal?: AbortSignal }): Promise<ContentAssetVersion>;
  uploadVersion(assetId: string, versionId: string, file: Blob, mime: string, options?: { signal?: AbortSignal }): Promise<ContentAssetVersion>;
  downloadVersion(assetId: string, versionId: string, fallbackFilename: string, options?: { signal?: AbortSignal }): Promise<DownloadedContentVersion>;
  listAcl(assetId: string, options?: { signal?: AbortSignal }): Promise<ContentAclEntry[]>;
  replaceAcl(assetId: string, entries: ContentAclEntry[], options?: { signal?: AbortSignal }): Promise<ContentAclEntry[]>;
}

export interface ContentOpsServiceOptions {
  baseUrl?: string;
  getAccessToken?: () => string | undefined;
  fetcher?: typeof fetch;
}

export class ContentOpsError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, status: number, message: string) {
    super(message);
    this.name = "ContentOpsError";
    this.code = code;
    this.status = status;
  }
}

export type ContentVisibility = "company" | "private";
/**
 * Every principal kind the Content API's ACL model can return.
 *
 * The read path must accept all of them: `safeAclEntry` rejects an unknown kind, and
 * it runs on `listAcl`, so a kind this union omits makes an entire asset's access list
 * unreadable — including entries the operator never touched. `cabinet`, `project` and
 * `role` are returned for scoped and role grants (`ContentPrincipalKind`).
 */
export type AclPrincipalKind = "employee" | "company" | "platform" | "cabinet" | "project" | "role";
export type AclPermission = "read" | "write" | "admin";

export interface ContentAsset {
  id: string;
  owner_scope: string;
  owner_company_id: string | null;
  visibility: ContentVisibility | string;
  mime: string | null;
  title: string | null;
  tags: string[];
  created_by: string | null;
  latest_version: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ContentAssetVersion {
  id: string;
  asset_id: string;
  version: number;
  size: number;
  sha256: string | null;
  etag: string | null;
  object_metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface ContentAclEntry {
  id?: string;
  principal_kind: AclPrincipalKind;
  principal_id: string;
  permission: AclPermission;
}

export interface CreateContentAssetInput {
  owner_company_id: string;
  visibility: ContentVisibility;
  mime: string;
  title: string;
  tags: string[];
}

export interface DownloadedContentVersion {
  blob: Blob;
  filename: string;
  contentType: string;
}

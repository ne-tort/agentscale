import { useEffect, useRef, useState, type FormEvent } from "react";
import { ContentOpsError, type ContentOpsService } from "./contracts";
import { contentFileFeedback, CONTENT_UPLOAD_MAX_BYTES } from "./validation";
import type { ContentAclEntry, ContentAsset, ContentAssetVersion, ContentVisibility } from "./types";
import { useI18n } from "../../../i18n/useT";
import { formatDateTime } from "../../../i18n/format";
import type { Translate } from "../../../i18n/catalogue";
import type { Locale } from "../../../preferences/settings";
import "./content-ops.css";

type UploadMode = "new" | "existing";
type UploadAttempt = { assetId: string; fileName: string; fileSize: number; versionId?: string };

export interface ContentOpsPanelProps {
  companyId: string;
  service: ContentOpsService;
  initialAssets?: ContentAsset[];
  workspaceBindingHref?: string;
}

/**
 * A timestamp in the language the interface is in.
 *
 * The server sends ISO strings; a value that cannot be read is shown as it came
 * rather than silently dropped, because a broken timestamp is still evidence.
 */
function dateLabel(value: string | null | undefined, locale: Locale, t: Translate): string {
  if (!value) return t("resources.contentNotProvided");
  return formatDateTime(value, locale) || value;
}

function safeFailure(error: unknown, t: Translate): string {
  if (error instanceof ContentOpsError) return error.message;
  return t("resources.contentFailed");
}

function assetLabel(asset: ContentAsset, t: Translate): string {
  return asset.title || t("resources.contentUntitled");
}

/** The ACL stores `employee`, `company`, `platform`, `cabinet`, `project`, `role`. */
function principalKindLabel(t: Translate, kind: ContentAclEntry["principal_kind"]): string {
  switch (kind) {
    case "company": return t("resources.contentPrincipalCompany");
    case "platform": return t("resources.contentPrincipalPlatform");
    case "cabinet": return t("resources.contentPrincipalCabinet");
    case "project": return t("resources.contentPrincipalProject");
    case "role": return t("resources.contentPrincipalRole");
    default: return t("resources.contentPrincipalEmployee");
  }
}

function permissionLabel(t: Translate, permission: ContentAclEntry["permission"]): string {
  return permission === "write" ? t("resources.contentPermWrite") : permission === "admin" ? t("resources.contentPermAdmin") : t("resources.contentPermRead");
}

function visibilityLabel(t: Translate, visibility: string): string {
  return visibility === "private" ? t("resources.contentVisibilityPrivate") : t("resources.contentVisibilityCompany");
}

function sortVersions(items: ContentAssetVersion[]): ContentAssetVersion[] {
  const compareText = (left: string, right: string): number => left < right ? -1 : left > right ? 1 : 0;
  return [...items].sort((left, right) => left.version - right.version
    || compareText(left.created_at ?? "", right.created_at ?? "")
    || compareText(left.id, right.id));
}

function companyAssets(items: ContentAsset[], companyId: string): ContentAsset[] {
  return items.filter((asset) => asset.owner_company_id === companyId);
}

function downloadBlob(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}

export function ContentOpsPanel({ companyId, service, initialAssets = [], workspaceBindingHref }: ContentOpsPanelProps) {
  const { t, locale } = useI18n();
  const [assets, setAssets] = useState<ContentAsset[]>(() => companyAssets(initialAssets, companyId));
  const [versions, setVersions] = useState<ContentAssetVersion[]>([]);
  const [selectedAssetId, setSelectedAssetId] = useState(() => companyAssets(initialAssets, companyId)[0]?.id ?? "");
  const [selectedVersionId, setSelectedVersionId] = useState("");
  const [mode, setMode] = useState<UploadMode>("new");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [visibility, setVisibility] = useState<ContentVisibility>("company");
  const [attempt, setAttempt] = useState<UploadAttempt | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [downloadFailedVersionId, setDownloadFailedVersionId] = useState("");
  const [aclEntries, setAclEntries] = useState<ContentAclEntry[]>([]);
  const [aclBaseline, setAclBaseline] = useState<ContentAclEntry[]>([]);
  const [aclLoadedFor, setAclLoadedFor] = useState("");
  const [aclBusy, setAclBusy] = useState(false);
  const [aclError, setAclError] = useState("");
  const [principalKind, setPrincipalKind] = useState<ContentAclEntry["principal_kind"]>("employee");
  const [principalId, setPrincipalId] = useState("");
  const [permission, setPermission] = useState<ContentAclEntry["permission"]>("read");
  const fileInput = useRef<HTMLInputElement>(null);

  const scopedAssets = companyAssets(assets, companyId);
  const selectedAsset = scopedAssets.find((asset) => asset.id === selectedAssetId);
  const assetVersions = sortVersions(versions.filter((version) => version.asset_id === selectedAssetId));
  const selectedVersion = assetVersions.find((version) => version.id === selectedVersionId);
  const feedback = file ? contentFileFeedback(file, t) : null;
  const uploadDraftDirty = Boolean(file || title || visibility !== "company" || mode !== "new");

  async function reloadAssets(): Promise<void> {
    setLoading(true);
    setError("");
    try {
      const result = companyAssets(await service.listAssets(companyId), companyId);
      setAssets(result);
      setSelectedAssetId((current) => result.some((asset) => asset.id === current) ? current : result[0]?.id ?? "");
    } catch (cause) {
      setError(safeFailure(cause, t));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reloadAssets();
  }, [companyId, service]);

  useEffect(() => {
    setVersions([]);
    setSelectedAssetId("");
    setSelectedVersionId("");
  }, [companyId]);

  useEffect(() => {
    if (!assetVersions.some((version) => version.id === selectedVersionId)) {
      setSelectedVersionId(assetVersions.at(-1)?.id ?? "");
    }
  }, [selectedAssetId, versions, selectedVersionId]);

  useEffect(() => {
    setAclEntries([]);
    setAclBaseline([]);
    setAclLoadedFor("");
    setAclError("");
  }, [selectedAssetId]);

  function resetForm(): void {
    const abandoned = attempt;
    setFile(null);
    setTitle("");
    setAttempt(null);
    setError("");
    setNotice(abandoned
      ? t("resources.contentFormClearedStaged")
      : t("resources.contentFormCleared"));
    if (fileInput.current) fileInput.current.value = "";
  }

  function chooseMode(next: UploadMode): void {
    if (attempt) return;
    setMode(next);
    setError("");
    setNotice("");
  }

  async function submitUpload(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!file || !feedback) {
      setError(t("resources.contentChooseFile"));
      return;
    }
    if (!feedback.accepted) {
      setError(t("resources.contentFileTooLarge"));
      return;
    }
    if (attempt && (attempt.fileName !== file.name || attempt.fileSize !== file.size)) {
      setError(t("resources.contentClearPrevious"));
      return;
    }
    if (mode === "existing" && (!selectedAsset || selectedAsset.id !== selectedAssetId)) {
      setError(t("resources.contentSelectAssetFirst"));
      return;
    }

    setBusy(true);
    setError("");
    setNotice("");
    let current = attempt;
    try {
      if (!current) {
        if (mode === "new") {
          const asset = await service.createAsset({
            owner_company_id: companyId,
            visibility,
            mime: feedback.mime,
            title: title.trim() || file.name,
            tags: [],
          });
          if (asset.owner_company_id !== companyId) {
            throw new ContentOpsError("INVALID_RESPONSE", 0, t("resources.contentAssetOutsideCompany"));
          }
          current = { assetId: asset.id, fileName: file.name, fileSize: file.size };
          setAttempt(current);
          setAssets((existing) => [asset, ...existing.filter((item) => item.id !== asset.id)]);
          setSelectedAssetId(asset.id);
        } else {
          current = { assetId: selectedAssetId, fileName: file.name, fileSize: file.size };
          setAttempt(current);
        }
      }
      // Read the retryable version id into a local before the branch: reassigning
      // `current` below would reset the `!current.versionId` narrowing for later lines.
      let versionId = current.versionId;
      if (!versionId) {
        const version = await service.beginVersion(current.assetId, feedback.mime);
        versionId = version.id;
        current = { ...current, versionId };
        setAttempt(current);
      }
      const saved = await service.uploadVersion(current.assetId, versionId, file, feedback.mime);
      setVersions((existing) => sortVersions([...existing.filter((version) => version.id !== saved.id), saved]));
      setSelectedAssetId(current.assetId);
      setSelectedVersionId(saved.id);
      setAttempt(null);
      setFile(null);
      setTitle("");
      if (fileInput.current) fileInput.current.value = "";
      setNotice(t("resources.contentUploaded", { version: saved.version, digest: saved.sha256 ?? t("resources.contentNotProvided") }));
      try {
        setAssets(companyAssets(await service.listAssets(companyId), companyId));
      } catch {
        setNotice(t("resources.contentUploadedRefreshFailed", { version: saved.version }));
      }
    } catch (cause) {
      setError(safeFailure(cause, t));
    } finally {
      setBusy(false);
    }
  }

  async function retryUpload(): Promise<void> {
    if (!file) return;
    await submitUpload({ preventDefault() {} } as FormEvent<HTMLFormElement>);
  }

  async function downloadSelectedVersion(): Promise<void> {
    if (!selectedAsset || !selectedVersion) return;
    setBusy(true);
    setError("");
    try {
      const result = await service.downloadVersion(selectedAsset.id, selectedVersion.id, selectedAsset.title || "download");
      downloadBlob(result.blob, result.filename);
      setDownloadFailedVersionId("");
      setNotice(t("resources.contentDownloaded", { version: selectedVersion.version }));
    } catch (cause) {
      setDownloadFailedVersionId(selectedVersion.id);
      setError(safeFailure(cause, t));
    } finally {
      setBusy(false);
    }
  }

  async function loadAcl(): Promise<void> {
    if (!selectedAsset) return;
    setAclBusy(true);
    setAclError("");
    try {
      const loadedEntries = await service.listAcl(selectedAsset.id);
      setAclEntries(loadedEntries);
      setAclBaseline(loadedEntries);
      setAclLoadedFor(selectedAsset.id);
    } catch (cause) {
      setAclError(safeFailure(cause, t));
    } finally {
      setAclBusy(false);
    }
  }

  function addAclEntry(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const normalizedId = principalId.trim();
    if (!normalizedId) return;
    if (aclEntries.some((entry) => entry.principal_kind === principalKind && entry.principal_id === normalizedId && entry.permission === permission)) {
      setAclError(t("resources.contentGrantDuplicate"));
      return;
    }
    setAclEntries((entries) => [...entries, { principal_kind: principalKind, principal_id: normalizedId, permission }]);
    setPrincipalId("");
    setAclError("");
  }

  async function saveAcl(): Promise<void> {
    if (!selectedAsset || aclLoadedFor !== selectedAsset.id) return;
    setAclBusy(true);
    setAclError("");
    try {
      const savedEntries = await service.replaceAcl(selectedAsset.id, aclEntries);
      setAclEntries(savedEntries);
      setAclBaseline(savedEntries);
      setNotice(t("resources.contentAclReplaced"));
    } catch (cause) {
      setAclError(safeFailure(cause, t));
    } finally {
      setAclBusy(false);
    }
  }

  return <main className="content-ops" aria-labelledby="content-ops-title">
    <header className="content-ops__heading" title={companyId}>
      <div><p className="content-ops__eyebrow">{t("resources.contentEyebrow")}</p><h1 id="content-ops-title">{t("resources.contentTitle")}</h1></div>
      <button type="button" className="content-ops__button content-ops__button--quiet" onClick={() => void reloadAssets()} disabled={loading || busy}>{loading ? t("resources.contentRefreshing") : t("resources.contentRefresh")}</button>
    </header>
    {error && <div className="content-ops__notice content-ops__notice--error" role="alert">{error}{attempt && file && <button type="button" onClick={() => void retryUpload()} disabled={busy}>{t("resources.contentRetryUpload")}</button>}</div>}
    {notice && <p className="content-ops__notice" role="status">{notice}</p>}

    <div className="content-ops__layout">
      <section className="content-ops__section" aria-labelledby="content-upload-title">
        <div className="content-ops__section-head"><div><h2 id="content-upload-title">{t("resources.contentUploadTitle")}</h2><p>{t("resources.contentUploadHint")}</p></div></div>
        <div className="content-ops__segmented" role="group" aria-label={t("resources.contentUploadDestination")}>
          <button type="button" aria-pressed={mode === "new"} onClick={() => chooseMode("new")} disabled={Boolean(attempt)}>{t("resources.contentNewAsset")}</button>
          <button type="button" aria-pressed={mode === "existing"} onClick={() => chooseMode("existing")} disabled={Boolean(attempt)}>{t("resources.contentExistingAsset")}</button>
        </div>
        <form className="content-ops__form" onSubmit={(event) => void submitUpload(event)}>
          {mode === "new" ? <>
            <label>{t("resources.contentAssetTitle")}<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={260} placeholder={t("resources.contentAssetTitlePlaceholder")} disabled={Boolean(attempt)} /></label>
            <label>{t("resources.contentVisibility")}<select value={visibility} onChange={(event) => setVisibility(event.target.value as ContentVisibility)} disabled={Boolean(attempt)}><option value="company">{t("resources.contentVisibilityCompany")}</option><option value="private">{t("resources.contentVisibilityPrivate")}</option></select></label>
          </> : <label>{t("resources.contentAsset")}<select value={selectedAssetId} onChange={(event) => setSelectedAssetId(event.target.value)} disabled={!scopedAssets.length || Boolean(attempt)} required><option value="">{t("resources.contentSelectAsset")}</option>{scopedAssets.map((asset) => <option value={asset.id} key={asset.id}>{assetLabel(asset, t)}{asset.latest_version ? ` · v${asset.latest_version}` : ""}</option>)}</select></label>}
          <label className="content-ops__file-field">{t("resources.contentFile")}<input ref={fileInput} type="file" onChange={(event) => { setFile(event.currentTarget.files?.[0] ?? null); setError(""); setNotice(""); }} disabled={Boolean(attempt) || busy} /></label>
          {feedback && <div className={`content-ops__file-feedback${feedback.accepted ? "" : " content-ops__file-feedback--error"}`} role="status"><strong>{feedback.mime}</strong><span>{t("resources.contentFileSizeOf", { size: feedback.sizeLabel })}</span><span>{feedback.message}</span></div>}
          <p className="content-ops__hint">{t("resources.contentFileHint")}</p>
          {attempt && <p className="content-ops__hint">{t("resources.contentRetryHint")}</p>}
{uploadDraftDirty && <p className="content-ops__hint" role="status">{t("resources.contentUnsavedUpload")}</p>}
          <div className="content-ops__actions"><button type="button" className="content-ops__button content-ops__button--quiet" onClick={resetForm} disabled={busy}>{t("resources.contentClear")}</button><button type="submit" className="content-ops__button content-ops__button--primary" disabled={busy || !file || !feedback?.accepted}>{busy ? t("resources.contentUploading") : attempt ? t("resources.contentRetryUpload") : t("resources.contentUploadVersion")}</button></div>
        </form>
      </section>

      <section className="content-ops__section" aria-labelledby="content-asset-title">
        <div className="content-ops__section-head"><div><h2 id="content-asset-title">{t("resources.contentAssetAndVersion")}</h2><p>{t("resources.contentAssetAndVersionHint")}</p></div></div>
        <label>{t("resources.contentAsset")}<select value={selectedAssetId} onChange={(event) => setSelectedAssetId(event.target.value)} disabled={!scopedAssets.length}><option value="">{t("resources.contentNoAssetSelected")}</option>{scopedAssets.map((asset) => <option value={asset.id} key={asset.id}>{assetLabel(asset, t)}{asset.latest_version ? ` · v${asset.latest_version}` : ""}</option>)}</select></label>
        {selectedAsset ? <>
        <div className="content-ops__asset-meta" title={selectedAsset.id}><div><span>{t("resources.contentAssetTitle")}</span><strong>{assetLabel(selectedAsset, t)}</strong></div><div><span>MIME</span><strong>{selectedAsset.mime || t("resources.contentNotProvided")}</strong></div><div><span>{t("resources.contentLatestVersion")}</span><strong>{selectedAsset.latest_version ? `v${selectedAsset.latest_version}` : t("resources.contentNoVersions")}</strong></div><div><span>{t("resources.contentVisibility")}</span><strong>{visibilityLabel(t, selectedAsset.visibility)}</strong></div><div><span>{t("resources.contentUploadedBy")}</span><code title={selectedAsset.created_by ?? undefined}>{selectedAsset.created_by || t("resources.contentNotProvided")}</code></div><div><span>{t("resources.contentCreated")}</span><strong>{dateLabel(selectedAsset.created_at, locale, t)}</strong></div><div><span>{t("resources.contentUpdated")}</span><strong>{dateLabel(selectedAsset.updated_at, locale, t)}</strong></div></div>
          <label>{t("resources.version")}<select value={selectedVersionId} onChange={(event) => { setSelectedVersionId(event.target.value); setDownloadFailedVersionId(""); setError(""); }} disabled={!assetVersions.length}><option value="">{selectedAsset.latest_version ? `v${selectedAsset.latest_version} · ${t("resources.contentVersionDetailsUnavailable")}` : t("resources.contentNoVersionDetails")}</option>{assetVersions.map((version) => <option key={version.id} value={version.id}>v{version.version}</option>)}</select></label>
          <p className="content-ops__hint">{t("resources.contentVersionHistoryBlocked")}</p>
          {!assetVersions.length && <p className="content-ops__empty">{t("resources.contentNoExactVersion")}</p>}
          {selectedVersion && <>
            <dl className="content-ops__version-details" title={selectedVersion.id}><div><dt>{t("resources.contentVersionId")}</dt><dd>v{selectedVersion.version}</dd></div><div><dt>{t("resources.contentSize")}</dt><dd>{t("resources.contentSizeBytes", { count: selectedVersion.size })}</dd></div><div><dt>SHA-256</dt><dd><code>{selectedVersion.sha256 || t("resources.contentNotProvided")}</code></dd></div><div><dt>ETag</dt><dd><code>{selectedVersion.etag || t("resources.contentNotProvided")}</code></dd></div><div><dt>{t("resources.contentVersionCreated")}</dt><dd>{dateLabel(selectedVersion.created_at, locale, t)}</dd></div><div><dt>{t("resources.contentObjectMetadata")}</dt><dd><code>{Object.keys(selectedVersion.object_metadata).length ? JSON.stringify(selectedVersion.object_metadata) : t("resources.contentNotProvided")}</code></dd></div></dl>
            <p className="content-ops__provenance">{t("resources.contentProvenanceHint")}</p>
            <button type="button" className="content-ops__button content-ops__button--quiet" onClick={() => void downloadSelectedVersion()} disabled={busy}>{busy ? t("resources.contentDownloading") : downloadFailedVersionId === selectedVersion.id ? t("resources.contentDownloadRetry") : t("resources.contentDownload")}</button>
          </>}
          <div className="content-ops__binding"><div><h3>{t("resources.contentWorkspaceBinding")}</h3><p>{t("resources.contentWorkspaceBindingHint")}</p></div>{workspaceBindingHref ? <a href={workspaceBindingHref}>{t("resources.contentOpenBinding")}</a> : <span>{t("resources.contentNotLinked")}</span>}</div>
        </> : <p className="content-ops__empty">{loading ? t("resources.contentLoadingAssets") : t("resources.contentNoAssets")}</p>}
      </section>
    </div>

    {selectedAsset && <section className="content-ops__section content-ops__acl" aria-labelledby="content-acl-title">
      <div className="content-ops__section-head"><div><h2 id="content-acl-title">{t("resources.contentAclTitle")}</h2><p>{t("resources.contentAclHint")}</p></div><button type="button" className="content-ops__button content-ops__button--quiet" onClick={() => void loadAcl()} disabled={aclBusy}>{aclBusy ? t("common.loading") : t("resources.contentLoadAcl")}</button></div>
      {aclError && <p className="content-ops__notice content-ops__notice--error" role="alert">{aclError}</p>}
      {aclLoadedFor === selectedAsset.id && <>
        {JSON.stringify(aclEntries.map(({ principal_kind, principal_id, permission }) => ({ principal_kind, principal_id, permission }))) !== JSON.stringify(aclBaseline.map(({ principal_kind, principal_id, permission }) => ({ principal_kind, principal_id, permission }))) && <p className="content-ops__hint" role="status">{t("resources.contentUnsavedAcl")}</p>}
        <ul className="content-ops__acl-list">{aclEntries.map((entry, index) => <li key={entry.id ?? `${entry.principal_kind}-${entry.principal_id}-${entry.permission}-${index}`}><span>{principalKindLabel(t, entry.principal_kind)}</span><code>{entry.principal_id}</code><strong>{permissionLabel(t, entry.permission)}</strong><button type="button" aria-label={t("resources.contentRemoveGrant", { principal: entry.principal_id })} onClick={() => setAclEntries((current) => current.filter((_, row) => row !== index))}>{t("resources.contentRemove")}</button></li>)}</ul>
        <form className="content-ops__acl-form" onSubmit={addAclEntry}>
          <label>{t("resources.contentPrincipalType")}<select value={principalKind} onChange={(event) => setPrincipalKind(event.target.value as ContentAclEntry["principal_kind"])}><option value="employee">{t("resources.contentPrincipalEmployee")}</option><option value="company">{t("resources.contentPrincipalCompany")}</option><option value="platform">{t("resources.contentPrincipalPlatform")}</option></select></label>
          <label>{t("resources.contentPrincipalId")}<input value={principalId} onChange={(event) => setPrincipalId(event.target.value)} required /></label>
          <label>{t("resources.contentPermission")}<select value={permission} onChange={(event) => setPermission(event.target.value as ContentAclEntry["permission"])}><option value="read">{t("resources.contentPermRead")}</option><option value="write">{t("resources.contentPermWrite")}</option><option value="admin">{t("resources.contentPermAdmin")}</option></select></label>
          <button type="submit" className="content-ops__button content-ops__button--quiet">{t("resources.contentAddGrant")}</button>
        </form>
        <div className="content-ops__actions"><button type="button" className="content-ops__button content-ops__button--primary" onClick={() => void saveAcl()} disabled={aclBusy}>{aclBusy ? t("common.saving") : t("resources.contentReplaceAcl")}</button></div>
      </>}
      {aclLoadedFor !== selectedAsset.id && <p className="content-ops__hint">{t("resources.contentAclLoadFirst")}</p>}
    </section>}

    <p className="content-ops__footnote">{t("resources.contentFootnote", { size: CONTENT_UPLOAD_MAX_BYTES / (1024 * 1024) })}</p>
  </main>;
}

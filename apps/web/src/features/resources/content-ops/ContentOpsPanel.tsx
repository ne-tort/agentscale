import { useEffect, useRef, useState, type FormEvent } from "react";
import { ContentOpsError, type ContentOpsService } from "./contracts";
import { contentFileFeedback, CONTENT_UPLOAD_MAX_BYTES } from "./validation";
import type { ContentAclEntry, ContentAsset, ContentAssetVersion, ContentVisibility } from "./types";
import "./content-ops.css";

type UploadMode = "new" | "existing";
type UploadAttempt = { assetId: string; fileName: string; fileSize: number; versionId?: string };

export interface ContentOpsPanelProps {
  companyId: string;
  service: ContentOpsService;
  initialAssets?: ContentAsset[];
  workspaceBindingHref?: string;
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return "Not provided by API";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function safeFailure(error: unknown): string {
  if (error instanceof ContentOpsError) return error.message;
  return "The content operation failed. Try again.";
}

function assetLabel(asset: ContentAsset): string {
  return asset.title || asset.id;
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
  const feedback = file ? contentFileFeedback(file) : null;
  const uploadDraftDirty = Boolean(file || title || visibility !== "company" || mode !== "new");

  async function reloadAssets(): Promise<void> {
    setLoading(true);
    setError("");
    try {
      const result = companyAssets(await service.listAssets(companyId), companyId);
      setAssets(result);
      setSelectedAssetId((current) => result.some((asset) => asset.id === current) ? current : result[0]?.id ?? "");
    } catch (cause) {
      setError(safeFailure(cause));
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
      ? "Form cleared. The Content API has no delete route for an unfinished upload; any staged asset or version remains on the server."
      : "Form cleared.");
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
      setError("Choose a file to upload.");
      return;
    }
    if (!feedback.accepted) {
      setError("The file exceeds the 100 MiB upload limit.");
      return;
    }
    if (attempt && (attempt.fileName !== file.name || attempt.fileSize !== file.size)) {
      setError("Clear the previous attempt before choosing a different file.");
      return;
    }
    if (mode === "existing" && (!selectedAsset || selectedAsset.id !== selectedAssetId)) {
      setError("Select an asset in this company before uploading a new version.");
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
            throw new ContentOpsError("INVALID_RESPONSE", 0, "The Content Service returned an asset outside this company.");
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
      if (!current.versionId) {
        const version = await service.beginVersion(current.assetId, feedback.mime);
        current = { ...current, versionId: version.id };
        setAttempt(current);
      }
      const saved = await service.uploadVersion(current.assetId, current.versionId, file, feedback.mime);
      setVersions((existing) => sortVersions([...existing.filter((version) => version.id !== saved.id), saved]));
      setSelectedAssetId(current.assetId);
      setSelectedVersionId(saved.id);
      setAttempt(null);
      setFile(null);
      setTitle("");
      if (fileInput.current) fileInput.current.value = "";
      setNotice(`Uploaded v${saved.version}. SHA-256 ${saved.sha256 ?? "not provided"}.`);
      try {
        setAssets(companyAssets(await service.listAssets(companyId), companyId));
      } catch {
        setNotice(`Uploaded v${saved.version}; asset list refresh failed. Use Refresh to reload it.`);
      }
    } catch (cause) {
      setError(safeFailure(cause));
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
      setNotice(`Downloaded v${selectedVersion.version}.`);
    } catch (cause) {
      setDownloadFailedVersionId(selectedVersion.id);
      setError(safeFailure(cause));
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
      setAclError(safeFailure(cause));
    } finally {
      setAclBusy(false);
    }
  }

  function addAclEntry(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const normalizedId = principalId.trim();
    if (!normalizedId) return;
    if (aclEntries.some((entry) => entry.principal_kind === principalKind && entry.principal_id === normalizedId && entry.permission === permission)) {
      setAclError("That access grant is already listed.");
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
      setNotice("Asset access list replaced.");
    } catch (cause) {
      setAclError(safeFailure(cause));
    } finally {
      setAclBusy(false);
    }
  }

  return <main className="content-ops" aria-labelledby="content-ops-title">
    <header className="content-ops__heading">
      <div><p className="content-ops__eyebrow">Resources · Content Service · Company {companyId}</p><h1 id="content-ops-title">Assets</h1></div>
      <button type="button" className="content-ops__button content-ops__button--quiet" onClick={() => void reloadAssets()} disabled={loading || busy}>{loading ? "Refreshing..." : "Refresh assets"}</button>
    </header>
    {error && <div className="content-ops__notice content-ops__notice--error" role="alert">{error}{attempt && file && <button type="button" onClick={() => void retryUpload()} disabled={busy}>Retry upload</button>}</div>}
    {notice && <p className="content-ops__notice" role="status">{notice}</p>}

    <div className="content-ops__layout">
      <section className="content-ops__section" aria-labelledby="content-upload-title">
        <div className="content-ops__section-head"><div><h2 id="content-upload-title">Upload</h2><p>Choose a new asset or append a version to an existing asset.</p></div></div>
        <div className="content-ops__segmented" role="group" aria-label="Upload destination">
          <button type="button" aria-pressed={mode === "new"} onClick={() => chooseMode("new")} disabled={Boolean(attempt)}>New asset</button>
          <button type="button" aria-pressed={mode === "existing"} onClick={() => chooseMode("existing")} disabled={Boolean(attempt)}>Existing asset</button>
        </div>
        <form className="content-ops__form" onSubmit={(event) => void submitUpload(event)}>
          {mode === "new" ? <>
            <label>Asset title<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={260} placeholder="Defaults to file name" disabled={Boolean(attempt)} /></label>
            <label>Visibility<select value={visibility} onChange={(event) => setVisibility(event.target.value as ContentVisibility)} disabled={Boolean(attempt)}><option value="company">Company</option><option value="private">Private</option></select></label>
          </> : <label>Asset<select value={selectedAssetId} onChange={(event) => setSelectedAssetId(event.target.value)} disabled={!scopedAssets.length || Boolean(attempt)} required><option value="">Select an asset</option>{scopedAssets.map((asset) => <option value={asset.id} key={asset.id}>{assetLabel(asset)}{asset.latest_version ? ` · v${asset.latest_version}` : ""}</option>)}</select></label>}
          <label className="content-ops__file-field">File<input ref={fileInput} type="file" onChange={(event) => { setFile(event.currentTarget.files?.[0] ?? null); setError(""); setNotice(""); }} disabled={Boolean(attempt) || busy} /></label>
          {feedback && <div className={`content-ops__file-feedback${feedback.accepted ? "" : " content-ops__file-feedback--error"}`} role="status"><strong>{feedback.mime}</strong><span>{feedback.sizeLabel} of 100 MiB</span><span>{feedback.message}</span></div>}
          <p className="content-ops__hint">The backend restricts size to 100 MiB but does not publish a MIME allowlist. A blank browser MIME type is sent as application/octet-stream.</p>
          {attempt && <p className="content-ops__hint">Retry reuses the staged asset/version when the API returned its ID. Clearing does not delete staged server records.</p>}
{uploadDraftDirty && <p className="content-ops__hint" role="status">Unsaved upload changes · local draft only. No server asset is created until upload starts.</p>}
          <div className="content-ops__actions"><button type="button" className="content-ops__button content-ops__button--quiet" onClick={resetForm} disabled={busy}>Clear</button><button type="submit" className="content-ops__button content-ops__button--primary" disabled={busy || !file || !feedback?.accepted}>{busy ? "Uploading..." : attempt ? "Retry upload" : "Upload version"}</button></div>
        </form>
      </section>

      <section className="content-ops__section" aria-labelledby="content-asset-title">
        <div className="content-ops__section-head"><div><h2 id="content-asset-title">Asset and version</h2><p>Select an asset and an exact version known to this session.</p></div></div>
        <label>Asset<select value={selectedAssetId} onChange={(event) => setSelectedAssetId(event.target.value)} disabled={!scopedAssets.length}><option value="">No asset selected</option>{scopedAssets.map((asset) => <option value={asset.id} key={asset.id}>{assetLabel(asset)}{asset.latest_version ? ` · v${asset.latest_version}` : ""}</option>)}</select></label>
        {selectedAsset ? <>
        <div className="content-ops__asset-meta"><div><span>Asset ID</span><code>{selectedAsset.id}</code></div><div><span>MIME</span><strong>{selectedAsset.mime || "Not provided"}</strong></div><div><span>Reported latest</span><strong>{selectedAsset.latest_version ? `v${selectedAsset.latest_version}` : "No versions"}</strong></div><div><span>Visibility</span><strong>{selectedAsset.visibility}</strong></div><div><span>Created by</span><code>{selectedAsset.created_by || "Not provided"}</code></div><div><span>Asset created</span><strong>{dateLabel(selectedAsset.created_at)}</strong></div><div><span>Asset updated</span><strong>{dateLabel(selectedAsset.updated_at)}</strong></div></div>
          <label>Version<select value={selectedVersionId} onChange={(event) => { setSelectedVersionId(event.target.value); setDownloadFailedVersionId(""); setError(""); }} disabled={!assetVersions.length}><option value="">{selectedAsset.latest_version ? `v${selectedAsset.latest_version} details unavailable from API` : "No version details available"}</option>{assetVersions.map((version) => <option key={version.id} value={version.id}>v{version.version} · {version.id}</option>)}</select></label>
          <p className="content-ops__hint">Version history is blocked: the API reports the latest version number but has no version-list route. Existing version IDs, history pages and metadata cannot be loaded. The selector contains only exact versions uploaded in this session.</p>
          {!assetVersions.length && <p className="content-ops__empty">No exact version is available in this session. Existing-version inspection is unavailable.</p>}
          {selectedVersion && <>
            <dl className="content-ops__version-details"><div><dt>Version ID</dt><dd><code>{selectedVersion.id}</code></dd></div><div><dt>Version</dt><dd>v{selectedVersion.version}</dd></div><div><dt>Size</dt><dd>{selectedVersion.size} bytes</dd></div><div><dt>SHA-256</dt><dd><code>{selectedVersion.sha256 || "Not provided"}</code></dd></div><div><dt>ETag</dt><dd><code>{selectedVersion.etag || "Not provided"}</code></dd></div><div><dt>Version created</dt><dd>{dateLabel(selectedVersion.created_at)}</dd></div><div><dt>Object metadata</dt><dd><code>{Object.keys(selectedVersion.object_metadata).length ? JSON.stringify(selectedVersion.object_metadata) : "Not provided"}</code></dd></div></dl>
            <p className="content-ops__provenance"><strong>Provenance available from API:</strong> asset creator/time and version creation time. No source-system provenance route is exposed.</p>
            <button type="button" className="content-ops__button content-ops__button--quiet" onClick={() => void downloadSelectedVersion()} disabled={busy}>{busy ? "Downloading..." : downloadFailedVersionId === selectedVersion.id ? "Retry download selected version" : "Download selected version"}</button>
          </>}
          <div className="content-ops__binding"><div><h3>Workspace binding</h3><p>The Content API has no workspace-binding endpoint. This link is supplied by the host Resources UI.</p></div>{workspaceBindingHref ? <a href={workspaceBindingHref}>Open workspace binding</a> : <span>Not linked by host UI</span>}</div>
        </> : <p className="content-ops__empty">{loading ? "Loading company assets..." : "No assets returned for this company."}</p>}
      </section>
    </div>

    {selectedAsset && <section className="content-ops__section content-ops__acl" aria-labelledby="content-acl-title">
      <div className="content-ops__section-head"><div><h2 id="content-acl-title">Asset access</h2><p>ACL replacement sends the complete list to the Content API.</p></div><button type="button" className="content-ops__button content-ops__button--quiet" onClick={() => void loadAcl()} disabled={aclBusy}>{aclBusy ? "Loading..." : "Load access list"}</button></div>
      {aclError && <p className="content-ops__notice content-ops__notice--error" role="alert">{aclError}</p>}
      {aclLoadedFor === selectedAsset.id && <>
        {JSON.stringify(aclEntries.map(({ principal_kind, principal_id, permission }) => ({ principal_kind, principal_id, permission }))) !== JSON.stringify(aclBaseline.map(({ principal_kind, principal_id, permission }) => ({ principal_kind, principal_id, permission }))) && <p className="content-ops__hint" role="status">Unsaved access-list changes · local draft only.</p>}
        <ul className="content-ops__acl-list">{aclEntries.map((entry, index) => <li key={entry.id ?? `${entry.principal_kind}-${entry.principal_id}-${entry.permission}-${index}`}><span>{entry.principal_kind}</span><code>{entry.principal_id}</code><strong>{entry.permission}</strong><button type="button" aria-label={`Remove ${entry.principal_kind} ${entry.principal_id}`} onClick={() => setAclEntries((current) => current.filter((_, row) => row !== index))}>Remove</button></li>)}</ul>
        <form className="content-ops__acl-form" onSubmit={addAclEntry}>
          <label>Principal type<select value={principalKind} onChange={(event) => setPrincipalKind(event.target.value as ContentAclEntry["principal_kind"])}><option value="employee">Employee</option><option value="company">Company</option><option value="platform">Platform</option></select></label>
          <label>Principal ID<input value={principalId} onChange={(event) => setPrincipalId(event.target.value)} required /></label>
          <label>Permission<select value={permission} onChange={(event) => setPermission(event.target.value as ContentAclEntry["permission"])}><option value="read">Read</option><option value="write">Write</option><option value="admin">Admin</option></select></label>
          <button type="submit" className="content-ops__button content-ops__button--quiet">Add grant</button>
        </form>
        <div className="content-ops__actions"><button type="button" className="content-ops__button content-ops__button--primary" onClick={() => void saveAcl()} disabled={aclBusy}>{aclBusy ? "Saving..." : "Replace access list"}</button></div>
      </>}
      {aclLoadedFor !== selectedAsset.id && <p className="content-ops__hint">Load the current list before editing; replacement is not an incremental grant operation.</p>}
    </section>}

    <p className="content-ops__footnote">Upload limit: {CONTENT_UPLOAD_MAX_BYTES / (1024 * 1024)} MiB. Upload URLs and storage keys returned internally by the backend are discarded and never rendered.</p>
  </main>;
}

import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import "./resources.css";
import { RESOURCE_CONTRACT_BLOCKERS } from "./blockers";
import { createBlockedResourceService, ResourceContractBlockedError, type ResourceService } from "./contracts";
import { useI18n } from "../../i18n/useT";
import { formatDateTime } from "../../i18n/format";
import type { Translate } from "../../i18n/catalogue";
import { ContentOpsPanel } from "./content-ops/ContentOpsPanel";
import type { ContentOpsService } from "./content-ops/contracts";
import type { ContentAsset } from "./content-ops/types";
import {
  canPlanMaterialization,
  canSyncMaterialization,
  latestVersionNumber,
  materializationStatusTone,
  resourceKindGlyph,
  resourceLifecycleTone,
  selectVisibleResources,
  summarizeDiff,
  versionsForResource,
  type ResourceKindFilter,
} from "./view-model";
import type {
  AgentProfile,
  MaterializationPlan,
  ResourceBinding,
  ResourceContractBlocker,
  ResourceDiff,
  ResourceKind,
  ResourcePayload,
  ResourceProject,
  ResourceRecord,
  ResourceScope,
  ResourceScopeKind,
  ResourceVersion,
  ResourceWorkspaceSnapshot,
} from "./types";

const blockedService = createBlockedResourceService();

export type ResourceWorkspaceProps = {
  service?: ResourceService;
  profileId?: string;
  projectId?: string;
  companyId?: string;
  contentOpsService?: ContentOpsService;
  initialAssets?: ContentAsset[];
  initialTab?: "overview" | "resources" | "assets" | "materialization" | "evidence";
  initialSnapshot?: ResourceWorkspaceSnapshot;
};

type WorkspaceTab = "overview" | "resources" | "assets" | "materialization" | "evidence";

type Notice = {
  tone: "success" | "error" | "info";
  message: string;
};

function shortDigest(value?: string): string {
  if (!value) return "—";
  return value.length > 22 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
}

function blockerMessage(t: Translate, error: unknown): string {
  if (error instanceof ResourceContractBlockedError) return t("resources.blockedContract");
  return error instanceof Error ? error.message : t("resources.operationFailed");
}

/**
 * The service talks in values (`active`, `local`, `planned`); the screen talks in
 * words. Keeping the mapping here means a new enum value falls back to a readable
 * key name instead of leaking the raw token into a sentence.
 */
function kindLabel(t: Translate, kind: ResourceKind): string {
  return kind === "prompt" ? t("resources.kindPrompt") : kind === "file" ? t("resources.kindFile") : t("resources.kindMcp");
}

function scopeKindLabel(t: Translate, kind: ResourceScopeKind): string {
  return t(`resources.scopeKind${kind.charAt(0).toUpperCase()}${kind.slice(1)}`);
}

function lifecycleLabel(t: Translate, status: ResourceRecord["lifecycle"]): string {
  return t(`resources.lifecycle${status.charAt(0).toUpperCase()}${status.slice(1)}`);
}

function profileStatusLabel(t: Translate, status: AgentProfile["status"]): string {
  return t(`resources.profileStatus${status.charAt(0).toUpperCase()}${status.slice(1)}`);
}

function versionStatusLabel(t: Translate, status: ResourceVersion["status"]): string {
  return t(`resources.versionStatus${status.charAt(0).toUpperCase()}${status.slice(1)}`);
}

function planStatusLabel(t: Translate, status: MaterializationPlan["status"]): string {
  return t(`resources.planStatus${status.charAt(0).toUpperCase()}${status.slice(1)}`);
}

function bindingModeLabel(t: Translate, mode: ResourceBinding["mode"]): string {
  return mode === "global" ? t("resources.modeGlobalShort") : t("resources.modeLocalShort");
}

function generationLabel(t: Translate, desired?: number, applied?: number): string {
  if (desired === undefined) return "—";
  return applied === undefined
    ? t("resources.generationUnapplied", { desired })
    : t("resources.generationApplied", { desired, applied });
}

function updateResourceSnapshot(
  current: ResourceWorkspaceSnapshot,
  patch: Partial<ResourceWorkspaceSnapshot>,
): ResourceWorkspaceSnapshot {
  return { ...current, ...patch };
}

function Stat({ label, value, detail }: { label: string; value: string | number; detail?: string }) {
  return <div className="resources-stat"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>;
}

function StatusPill({ tone, children }: { tone: "positive" | "warning" | "danger" | "neutral"; children: ReactNode }) {
  return <span className={`resources-pill resources-pill--${tone}`}>{children}</span>;
}

function SectionHeading({ eyebrow, title, detail, action }: { eyebrow?: string; title: string; detail?: string; action?: ReactNode }) {
  return <div className="resources-section-heading"><div>{eyebrow && <p className="resources-eyebrow">{eyebrow}</p>}<h2>{title}</h2>{detail && <p className="resources-muted">{detail}</p>}</div>{action}</div>;
}

/**
 * Why the page is stopped, in words a person can act on.
 *
 * Blockers that arrive from the service with their own wording keep it: the code
 * and owner stay in the tooltip, because they help whoever picks up the ticket
 * and mean nothing to the person reading the screen.
 */
function blockerCopy(t: Translate, blocker: ResourceContractBlocker): { title: string; detail: string } {
  switch (blocker.code) {
    case "resource_web_facade_missing":
      return { title: t("resources.blockerFacadeTitle"), detail: t("resources.blockerFacadeDetail") };
    case "resource_scope_gate_blocked":
      return { title: t("resources.blockerScopeTitle"), detail: t("resources.blockerScopeDetail") };
    case "resource_snapshot_adapter_missing":
      return { title: t("resources.blockerSnapshotTitle"), detail: t("resources.blockerSnapshotDetail") };
    case "materialization_apply_adapter_missing":
      return { title: t("resources.blockerApplyTitle"), detail: t("resources.blockerApplyDetail") };
    case "resource_promotion_blocked":
      return { title: t("resources.blockerPromotionTitle"), detail: t("resources.blockerPromotionDetail") };
    default:
      return { title: blocker.title, detail: blocker.detail };
  }
}

function BlockersPanel({ blockers, compact = false }: { blockers: readonly ResourceContractBlocker[]; compact?: boolean }) {
  const { t } = useI18n();
  if (!blockers.length) return null;
  return <aside className={`resources-blockers${compact ? " resources-blockers--compact" : ""}`} aria-label={t("resources.blockersLabel")}>
    <div className="resources-blockers__header"><span className="resources-alert-icon" aria-hidden="true">!</span><div><p className="resources-eyebrow">{t("resources.blockersEyebrow")}</p><h2>{t("resources.blockersTitle")}</h2></div><span className="resources-blockers__count">{blockers.length}</span></div>
    <div className="resources-blockers__list">{blockers.map((blocker) => {
      const copy = blockerCopy(t, blocker);
      return <div className="resources-blocker" key={blocker.code} title={`${blocker.code} · ${blocker.owner}`}><div><strong>{copy.title}</strong><span>{copy.detail}</span></div></div>;
    })}</div>
  </aside>;
}

function ScopeBadge({ scope }: { scope: ResourceScope }) {
  const { t } = useI18n();
  return <span className="resources-scope" title={scope.id ? `${t("resources.scope")}: ${scope.id}` : undefined}><span>{scopeKindLabel(t, scope.kind)}</span><strong>{scope.label ?? t("resources.scopeUnlabeled")}</strong></span>;
}

function ResourceIcon({ kind }: { kind: ResourceKind }) {
  return <span className={`resources-icon resources-icon--${kind}`} aria-hidden="true">{resourceKindGlyph(kind)}</span>;
}

function ProfileCard({
  profile,
  canUpdate,
  busy,
  onSave,
}: {
  profile: AgentProfile;
  canUpdate: boolean;
  busy: boolean;
  onSave: (name: string, description: string) => Promise<boolean>;
}) {
  const { t, plural, locale } = useI18n();
  const [name, setName] = useState(profile.name);
  const [description, setDescription] = useState(profile.description);
  const [editing, setEditing] = useState(false);
  const unsaved = name !== profile.name || description !== profile.description;
  // A timestamp the server sent is shown in the interface's language; one that
  // cannot be read is shown as it came, and a missing one as a dash.
  const updatedLabel = formatDateTime(profile.updatedAt, locale) || profile.updatedAt || "—";

  useEffect(() => {
    setName(profile.name);
    setDescription(profile.description);
  }, [profile.description, profile.name]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const saved = await onSave(name.trim(), description.trim());
    if (saved) setEditing(false);
  }

  return <article className="resources-profile-card">
    <div className="resources-profile-mark" aria-hidden="true">{profile.name.slice(0, 1).toUpperCase() || "A"}</div>
    <div className="resources-profile-main">
      <div className="resources-card-kicker"><span>{t("resources.profileEyebrow")}</span><StatusPill tone={profile.status === "active" ? "positive" : profile.status === "blocked" ? "danger" : "warning"}>{profileStatusLabel(t, profile.status)}</StatusPill></div>
      {editing ? <form className="resources-inline-form" onSubmit={submit}>
        {unsaved && <p className="resources-form-hint" role="status">{t("resources.unsavedProfile")}</p>}
        <label>{t("resources.profileName")}<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
        <label>{t("resources.description")}<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></label>
        <div className="resources-form-actions"><button type="button" className="resources-button resources-button--quiet" onClick={() => setEditing(false)}>{t("common.cancel")}</button><button className="resources-button resources-button--primary" disabled={!canUpdate || busy || !name.trim()}>{busy ? t("common.saving") : t("resources.saveProfile")}</button></div>
        {!canUpdate && <p className="resources-form-hint">{t("resources.profileEditLockedHint")}</p>}
      </form> : <>
        <div className="resources-profile-title-row"><div><h2>{profile.name}</h2><p>{profile.description || t("resources.noDescription")}</p></div><button className="resources-button resources-button--quiet" onClick={() => setEditing(true)} disabled={!canUpdate} title={!canUpdate ? t("resources.editProfileBlockedTitle") : undefined}>{t("resources.editProfile")}</button></div>
        <div className="resources-profile-meta"><span>{t("resources.revision")} <strong>{profile.revision}</strong></span><span>{t("resources.updated")} <strong>{updatedLabel}</strong></span><span>{t("resources.scope")} <strong>{plural("resources.projectCount", profile.projectCount)}</strong></span></div>{!canUpdate && <p className="resources-disabled-note">{t("resources.profileEditDisabled")}</p>}
      </>}
    </div>
  </article>;
}

function ResourceCard({
  resource,
  versions,
  selected,
  onSelect,
}: {
  resource: ResourceRecord;
  versions: ResourceVersion[];
  selected: boolean;
  onSelect: () => void;
}) {
  const { t, plural } = useI18n();
  const latest = latestVersionNumber(resource, versions);
  return <button type="button" className={`resources-resource-card${selected ? " resources-resource-card--selected" : ""}`} onClick={onSelect} aria-pressed={selected}>
    <div className="resources-resource-card__top"><ResourceIcon kind={resource.kind} /><StatusPill tone={resourceLifecycleTone(resource.lifecycle)}>{lifecycleLabel(t, resource.lifecycle)}</StatusPill></div>
    <div className="resources-resource-card__body"><h3>{resource.title}</h3><p>{resource.description || resource.slug}</p><ScopeBadge scope={resource.scope} /></div>
    <div className="resources-resource-card__footer"><span>{kindLabel(t, resource.kind)}</span><span>v{latest ?? "—"} · {plural("resources.bindingCount", resource.bindingCount)}</span></div>
  </button>;
}

function ResourceDetail({ resource, versions, bindings, onOpenDiff }: { resource: ResourceRecord; versions: ResourceVersion[]; bindings: ResourceBinding[]; onOpenDiff: (from: number, to: number) => void }) {
  const { t, plural } = useI18n();
  const resourceVersions = versionsForResource(versions, resource.id);
  const resourceBindings = bindings.filter((binding) => binding.resourceId === resource.id);
  const currentVersion = resourceVersions.find((version) => version.number === resource.activeVersionNumber) ?? resourceVersions[0];
  return <article className="resources-detail-card">
    <div className="resources-detail-card__header"><div className="resources-resource-title"><ResourceIcon kind={resource.kind} /><div><p className="resources-eyebrow">{kindLabel(t, resource.kind)} · {resource.slug}</p><h3>{resource.title}</h3><p className="resources-muted">{resource.description || t("resources.noDescription")}</p></div></div><ScopeBadge scope={resource.scope} /></div>
    <div className="resources-detail-grid"><div><span className="resources-label">{t("resources.activeVersion")}</span><strong>{resource.activeVersionNumber ? `v${resource.activeVersionNumber}` : t("resources.notPublished")}</strong><small>{currentVersion?.label ?? t("resources.noVersionLabel")}</small></div><div><span className="resources-label">{t("resources.history")}</span><strong>{plural("resources.versionCount", resourceVersions.length)}</strong><small>{t("resources.appendOnly")}</small></div><div><span className="resources-label">{t("resources.bindings")}</span><strong>{resourceBindings.length}</strong><small>{resourceBindings.length ? resourceBindings.map((binding) => binding.projectName).join(", ") : t("resources.noProjects")}</small></div><div><span className="resources-label">{t("resources.sourceDigest")}</span><strong className="resources-mono">{shortDigest(resource.provenance.digest)}</strong><small>{resource.provenance.sourceType} · {t("resources.generationValue", { value: resource.provenance.sourceGeneration })}</small></div></div>
    <div className="resources-binding-list"><div className="resources-binding-list__header"><span className="resources-label">{t("resources.projectBindings")}</span><span>{plural("resources.targetCount", resourceBindings.length)}</span></div>{resourceBindings.length ? resourceBindings.map((binding) => <div className="resources-binding-row" key={binding.id}><div><strong>{binding.projectName}</strong><span><ScopeBadge scope={binding.target} />{bindingModeLabel(t, binding.mode)} · v{binding.versionNumber}</span></div><code>{binding.workspacePath ?? t("resources.noWorkspacePath")}</code></div>) : <span className="resources-muted">{t("resources.noBindings")}</span>}</div>
    <div className="resources-version-strip"><div><span className="resources-label">{t("resources.versionHistory")}</span><div className="resources-version-list">{resourceVersions.length ? resourceVersions.map((version) => <span className={`resources-version-chip${version.number === resource.activeVersionNumber ? " resources-version-chip--active" : ""}`} key={version.id}>v{version.number}<small>{versionStatusLabel(t, version.status)}</small></span>) : <span className="resources-muted">{t("resources.noVersions")}</span>}</div></div>{resourceVersions.length > 1 && <button className="resources-button resources-button--quiet" onClick={() => onOpenDiff(resourceVersions[1].number, resourceVersions[0].number)}>{t("resources.compareLatest")}</button>}</div>
  </article>;
}

function ProfileOverview({ snapshot, service, busy, onSaveProfile, onSelectTab }: { snapshot: ResourceWorkspaceSnapshot; service: ResourceService; busy: string | undefined; onSaveProfile: (name: string, description: string) => Promise<boolean>; onSelectTab: (tab: WorkspaceTab) => void }) {
  const { t, plural } = useI18n();
  const active = snapshot.resources.filter((resource) => resource.lifecycle === "active").length;
  const bound = snapshot.bindings.length;
  const plan = snapshot.materialization;
  // Entries on a *plan* are all `planned`: the plan is a preview, and an apply result
  // is a separate response (`MaterializationOutcome`). The ring therefore counts the
  // entry set, not a per-entry applied flag that a plan never carries.
  const plannedEntries = plan ? plan.entries : [];
  return <div className="resources-overview-grid">
    <div className="resources-overview-main"><ProfileCard profile={snapshot.profile} canUpdate={service.capabilities.updateProfile} busy={busy === "profile"} onSave={onSaveProfile} />
      <div className="resources-stats-grid"><Stat label={t("resources.statResources")} value={snapshot.resources.length} detail={t("resources.statResourcesActive", { count: active })} /><Stat label={t("resources.statVersions")} value={snapshot.versions.length} detail={t("resources.statVersionsHint")} /><Stat label={t("resources.statBindings")} value={bound} detail={t("resources.statBindingsHint")} /><Stat label={t("resources.statPlan")} value={plan ? t("resources.generation", { desired: plan.desiredGeneration }) : "—"} detail={plan ? planStatusLabel(t, plan.status) : t("resources.noPlanLoaded")} /></div>
      <section className="resources-surface"><SectionHeading eyebrow={t("resources.mixEyebrow")} title={t("resources.mixTitle")} detail={t("resources.mixDetail")} action={<button className="resources-button resources-button--quiet" onClick={() => onSelectTab("resources")}>{t("resources.manageResources")}</button>} />
        <div className="resources-mix-grid">{(["prompt", "file", "mcp"] as ResourceKind[]).map((kind) => { const count = snapshot.resources.filter((resource) => resource.kind === kind).length; return <button type="button" className="resources-mix-card" key={kind} onClick={() => onSelectTab("resources")}><ResourceIcon kind={kind} /><span>{kindLabel(t, kind)}</span><strong>{count}</strong><small>{t("resources.mixUnit")}</small></button>; })}</div>
      </section>
    </div>
    <div className="resources-overview-side"><section className="resources-surface resources-surface--accent"><SectionHeading eyebrow={t("resources.materializationEyebrow")} title={plan ? planStatusLabel(t, plan.status) : t("resources.noPlan")} detail={plan ? generationLabel(t, plan.desiredGeneration, plan.appliedGeneration) : t("resources.planHint")} /><div className="resources-plan-preview">{plan ? <><div className="resources-plan-ring"><strong>{plannedEntries.length}</strong><span>/ {plan.entries.length}</span></div><div><strong>{plan.projectName}</strong><p>{plural("resources.operationCount", plan.entries.length)} · {shortDigest(plan.digest)}</p><button className="resources-text-button" onClick={() => onSelectTab("materialization")}>{t("resources.viewPlan")}</button></div></> : <button className="resources-button resources-button--primary" onClick={() => onSelectTab("materialization")} disabled={!service.capabilities.planMaterialization}>{t("resources.openMaterialization")}</button>}</div></section><BlockersPanel blockers={snapshot.contract.blockers} compact /></div>
  </div>;
}

function ResourceCatalog({ snapshot, service, onSelectResource, selectedResourceId, onOpenDiff, onCreateResource, busy }: { snapshot: ResourceWorkspaceSnapshot; service: ResourceService; onSelectResource: (id: string) => void; selectedResourceId?: string; onOpenDiff: (from: number, to: number) => void;  onCreateResource: (input: { kind: ResourceKind; slug: string; title: string; description: string; scope: ResourceScope }) => Promise<boolean>; busy?: string }) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState<ResourceKindFilter>("all");
  const [showCreate, setShowCreate] = useState(false);
  const [draftKind, setDraftKind] = useState<ResourceKind>("prompt");
  const [slug, setSlug] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [scopeKind, setScopeKind] = useState<ResourceScopeKind>("project");
  const [scopeId, setScopeId] = useState("");
  const visible = useMemo(() => selectVisibleResources(snapshot.resources, search, kind), [kind, search, snapshot.resources]);

  async function submitCreate(event: FormEvent) {
    event.preventDefault();
    const created = await onCreateResource({ kind: draftKind, slug: slug.trim(), title: title.trim(), description: description.trim(), scope: { kind: scopeKind, id: scopeId.trim() } });
    if (!created) return;
    setSlug(""); setTitle(""); setDescription(""); setScopeId(""); setShowCreate(false);
  }

  const selected = snapshot.resources.find((resource) => resource.id === selectedResourceId) ?? visible[0];
  return <div className="resources-catalog-layout"><div className="resources-catalog-main"><SectionHeading eyebrow={t("resources.catalogEyebrow")} title={t("resources.catalogTitle")} detail={t("resources.catalogDetail")} action={<button className="resources-button resources-button--primary" onClick={() => setShowCreate((value) => !value)}>{showCreate ? t("resources.closeForm") : t("resources.addResource")}</button>} />
    {showCreate && <form className="resources-form-card" onSubmit={submitCreate}><div className="resources-form-card__header"><div><p className="resources-eyebrow">{t("resources.newResourceEyebrow")}</p><h3>{t("resources.newResourceTitle")}</h3></div><span className="resources-form-status">{t("resources.newResourceStatus")}</span></div>{(slug || title || description || scopeId) && <p className="resources-form-hint" role="status">{t("resources.unsavedResource")}</p>}<div className="resources-form-grid"><label>{t("resources.fieldType")}<select value={draftKind} onChange={(event) => setDraftKind(event.target.value as ResourceKind)}><option value="prompt">{t("resources.kindPrompt")}</option><option value="file">{t("resources.kindFile")}</option><option value="mcp">{t("resources.kindMcp")}</option></select></label><label>{t("resources.fieldSlug")}<input value={slug} onChange={(event) => setSlug(event.target.value)} placeholder={t("resources.slugPlaceholder")} required /></label><label>{t("resources.fieldTitle")}<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={t("resources.titlePlaceholder")} required /></label><label>{t("resources.fieldOwnerScope")}<select value={scopeKind} onChange={(event) => setScopeKind(event.target.value as ResourceScopeKind)}>{(["platform", "company", "cabinet", "project", "session"] as ResourceScopeKind[]).map((value) => <option key={value} value={value}>{scopeKindLabel(t, value)}</option>)}</select></label><label>{t("resources.fieldScopeId")}<input value={scopeId} onChange={(event) => setScopeId(event.target.value)} placeholder={t("resources.scopeIdPlaceholder")} required /></label><label className="resources-field-wide">{t("resources.fieldDescription")}<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={2} placeholder={t("resources.descriptionPlaceholder")} /></label></div><div className="resources-form-actions"><span className="resources-form-hint">{t("resources.createHint")}</span><button className="resources-button resources-button--primary" disabled={!service.capabilities.createResource || busy === "create-resource" || !slug.trim() || !title.trim() || !scopeId.trim()} title={!service.capabilities.createResource ? t("resources.createBlockedTitle") : undefined}>{busy === "create-resource" ? t("resources.creating") : t("resources.createResource")}</button></div></form>}
    <div className="resources-toolbar"><label className="resources-search"><span aria-hidden="true">⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t("resources.searchPlaceholder")} aria-label={t("resources.searchLabel")} /></label><div className="resources-filter-group" role="group" aria-label={t("resources.filterLabel")}>{(["all", "prompt", "file", "mcp"] as ResourceKindFilter[]).map((value) => <button type="button" key={value} className={kind === value ? "resources-filter resources-filter--active" : "resources-filter"} onClick={() => setKind(value)}>{value === "all" ? t("resources.filterAll") : kindLabel(t, value)}</button>)}</div><span className="resources-toolbar-count">{t("resources.shown", { count: visible.length })}</span></div>
    <div className="resources-resource-grid">{visible.length ? visible.map((resource) => <ResourceCard key={resource.id} resource={resource} versions={snapshot.versions} selected={resource.id === selected?.id} onSelect={() => onSelectResource(resource.id)} />) : <div className="resources-empty"><strong>{t("resources.noMatchesTitle")}</strong><span>{t("resources.noMatchesHint")}</span></div>}</div>
{!service.capabilities.createResource && <p className="resources-disabled-note">{t("resources.createDisabled")}</p>}{!service.capabilities.readWorkspace && <p className="resources-disabled-note">{t("resources.workspaceUnavailable")}</p>}{!service.capabilities.readDiff && <p className="resources-disabled-note">{t("resources.diffDisabled")}</p>}

  </div>{selected ? <div className="resources-catalog-detail"><ResourceDetail resource={selected} versions={snapshot.versions} bindings={snapshot.bindings} onOpenDiff={onOpenDiff} /></div> : <div className="resources-catalog-detail"><div className="resources-empty resources-empty--tall"><strong>{t("resources.selectResourceTitle")}</strong><span>{t("resources.selectResourceHint")}</span></div></div>}</div>;
}

function VersionEditor({ resource, versions, service, busy, onCreateVersion }: { resource?: ResourceRecord; versions: ResourceVersion[]; service: ResourceService; busy?: string; onCreateVersion: (input: { label: string; changeNote: string; payload: ResourcePayload }) => Promise<boolean> }) {
  const { t } = useI18n();
  const [label, setLabel] = useState("");
  const [changeNote, setChangeNote] = useState("");
  const [baseVersionId, setBaseVersionId] = useState("");
  const [body, setBody] = useState("");
  const [path, setPath] = useState("");
  const [server, setServer] = useState("");
  const [tools, setTools] = useState("");
  const resourceVersions = resource ? versionsForResource(versions, resource.id) : [];
  const selectedVersion = resourceVersions.find((version) => version.id === baseVersionId) ?? resourceVersions[0];

  useEffect(() => {
    setBaseVersionId("");
    setLabel("");
    setChangeNote("");
    setBody("");
    setPath("");
    setServer("");
    setTools("");
  }, [resource?.id]);

  if (!resource) return <div className="resources-empty"><strong>{t("resources.chooseResourceTitle")}</strong><span>{t("resources.versionFormHint")}</span></div>;
  // `payload` is a function declaration (hoisted), so TypeScript cannot carry the
  // `!resource` narrowing of the parameter into its body; bind the narrowed value.
  const selected = resource;

  function payload(): ResourcePayload {
    if (selected.kind === "prompt") return { kind: "prompt", body, variables: Array.from(body.matchAll(/\{\{\s*([\w.-]+)\s*\}\}/g), (match) => match[1]).filter((value, index, values) => values.indexOf(value) === index) };
    if (selected.kind === "file") return { kind: "file", path, contents: body };
    return { kind: "mcp", server, tools: tools.split(",").map((tool) => tool.trim()).filter(Boolean) };
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const created = await onCreateVersion({ label: label.trim(), changeNote: changeNote.trim(), payload: payload() });
    if (!created) return;
    setLabel(""); setChangeNote(""); setBody(""); setPath(""); setServer(""); setTools("");
  }

  return <form className="resources-form-card resources-version-editor" onSubmit={submit}><div className="resources-form-card__header"><div><p className="resources-eyebrow">{t("resources.newVersionEyebrow")}</p><h3>{resource.title}</h3></div><span className="resources-form-status">{kindLabel(t, resource.kind)}</span></div><label className="resources-field-wide">{t("resources.versionToInspect")}<select value={selectedVersion?.id ?? ""} onChange={(event) => setBaseVersionId(event.target.value)} disabled={!resourceVersions.length}>{resourceVersions.length ? resourceVersions.map((version) => <option key={version.id} value={version.id}>v{version.number} · {version.label} · {versionStatusLabel(t, version.status)}</option>) : <option value="">{t("resources.noVersions")}</option>}</select></label>{selectedVersion && <><p className="resources-form-hint">{t("resources.inspectHint", { number: selectedVersion.number, status: versionStatusLabel(t, selectedVersion.status), author: selectedVersion.createdBy, digest: shortDigest(selectedVersion.digest) })}</p><pre className="resources-version-preview">{JSON.stringify(selectedVersion.payload, null, 2)}</pre><button type="button" className="resources-button resources-button--quiet" disabled={!service.capabilities.createVersion} title={!service.capabilities.createVersion ? t("resources.versionCreateBlockedTitle") : undefined} onClick={() => { setLabel(t("resources.basedOn", { number: selectedVersion.number })); if (selectedVersion.payload.kind === "prompt") { setBody(selectedVersion.payload.body); setPath(""); setServer(""); setTools(""); } else if (selectedVersion.payload.kind === "file") { setPath(selectedVersion.payload.path); setBody(selectedVersion.payload.contents); setServer(""); setTools(""); } else { setServer(selectedVersion.payload.server); setTools(selectedVersion.payload.tools.join(", ")); setPath(""); setBody(""); } }}>{t("resources.useAsStartingPoint")}</button></>}{(label || changeNote || body || path || server || tools) && <p className="resources-form-hint" role="status">{t("resources.unsavedVersion")}</p>}<div className="resources-form-grid"><label>{t("resources.versionLabel")}<input value={label} onChange={(event) => setLabel(event.target.value)} placeholder={t("resources.versionLabelPlaceholder")} required /></label><label>{t("resources.changeNote")}<input value={changeNote} onChange={(event) => setChangeNote(event.target.value)} placeholder={t("resources.changeNotePlaceholder")} /></label>{resource.kind === "file" && <label>{t("resources.workspacePath")}<input value={path} onChange={(event) => setPath(event.target.value)} placeholder="config/policy.md" required /></label>}{resource.kind === "mcp" && <><label>{t("resources.serverRef")}<input value={server} onChange={(event) => setServer(event.target.value)} placeholder="catalog-mcp" required /></label><label>{t("resources.allowedTools")}<input value={tools} onChange={(event) => setTools(event.target.value)} placeholder="search, get_item" required /></label></>}{resource.kind === "prompt" && <p className="resources-form-hint resources-field-wide">{t("resources.promptVariablesHint")}</p>}<label className="resources-field-wide">{resource.kind === "prompt" ? t("resources.promptBody") : resource.kind === "file" ? t("resources.fileContents") : t("resources.serverNotes")}<textarea value={body} onChange={(event) => setBody(event.target.value)} rows={7} placeholder={resource.kind === "prompt" ? t("resources.promptBodyPlaceholder") : resource.kind === "file" ? "# Policy\n" : t("resources.serverNotesPlaceholder")} required /></label></div><div className="resources-form-actions"><span className="resources-form-hint">{t("resources.immutableHint")}</span><button className="resources-button resources-button--primary" disabled={!service.capabilities.createVersion || busy === "create-version"} title={!service.capabilities.createVersion ? t("resources.versionCreateBlockedTitle") : undefined}>{busy === "create-version" ? t("resources.publishing") : t("resources.createVersion")}</button></div>{!service.capabilities.createVersion && <p className="resources-disabled-note">{t("resources.versionCreateDisabled")}</p>}</form>;
}

function BindingForm({ resource, projects, service, busy, onBind }: { resource?: ResourceRecord; projects: ResourceProject[]; service: ResourceService; busy?: string; onBind: (input: { projectId: string; projectName: string; target: ResourceScope; mode: "local" | "global"; versionNumber: number; workspacePath?: string; childMayEdit: boolean }) => Promise<boolean> }) {
  const { t } = useI18n();
  const availableVersions = resource ? resource.versionNumbers : [];
  const [projectId, setProjectId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [version, setVersion] = useState(resource?.activeVersionNumber ? String(resource.activeVersionNumber) : availableVersions[0] ? String(availableVersions[0]) : "");
  const [mode, setMode] = useState<"local" | "global">("local");
  const [path, setPath] = useState("");
  const [childMayEdit, setChildMayEdit] = useState(false);
  const versionDirty = version !== String(resource?.activeVersionNumber ?? resource?.versionNumbers[0] ?? "");

  useEffect(() => {
    setVersion(resource?.activeVersionNumber ? String(resource.activeVersionNumber) : resource?.versionNumbers[0] ? String(resource.versionNumbers[0]) : "");
  }, [resource?.activeVersionNumber, resource?.id, resource?.versionNumbers]);

  if (!resource) return <div className="resources-empty"><strong>{t("resources.chooseResourceTitle")}</strong><span>{t("resources.bindingFormHint")}</span></div>;
  const selectedProject = projects.find((project) => project.id === projectId);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!selectedProject) return;
    const bound = await onBind({ projectId, projectName: selectedProject.name, target: { kind: "project", id: targetId.trim() || projectId, label: targetId.trim() || selectedProject.name }, mode, versionNumber: Number(version), workspacePath: path.trim() || undefined, childMayEdit });
    if (!bound) return;
    setProjectId(""); setTargetId(""); setPath(""); setChildMayEdit(false);
  }
  return <form className="resources-form-card resources-binding-form" onSubmit={submit}><div className="resources-form-card__header"><div><p className="resources-eyebrow">{t("resources.bindingEyebrow")}</p><h3>{t("resources.bindingTitle")}</h3></div><span className="resources-form-status">{t("resources.bindingStatus")}</span></div><div className="resources-form-grid"><label>{t("resources.project")}<select value={projectId} onChange={(event) => setProjectId(event.target.value)} required><option value="">{t("resources.selectProject")}</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><label>{t("resources.version")}<select value={version} onChange={(event) => setVersion(event.target.value)} required>{availableVersions.length ? availableVersions.map((number) => <option key={number} value={number}>v{number}</option>) : <option value="">{t("resources.noVersionsAvailable")}</option>}</select></label><label>{t("resources.targetId")}<input value={targetId} onChange={(event) => setTargetId(event.target.value)} placeholder={t("resources.targetIdPlaceholder")} /></label><label>{t("resources.bindingMode")}<select value={mode} onChange={(event) => setMode(event.target.value as "local" | "global")}><option value="local">{t("resources.modeLocal")}</option><option value="global">{t("resources.modeGlobal")}</option></select></label><label className="resources-field-wide">{t("resources.workspacePath")}<input value={path} onChange={(event) => setPath(event.target.value)} placeholder={resource.kind === "file" ? "config/policy.md" : t("resources.optionalPath")} /></label><label className="resources-checkbox resources-field-wide"><input type="checkbox" checked={childMayEdit} onChange={(event) => setChildMayEdit(event.target.checked)} /> {t("resources.allowChildEdits")} <span>{t("resources.allowChildEditsHint")}</span></label></div><div className="resources-form-actions"><span className="resources-form-hint">{t("resources.bindingHint")}</span><button className="resources-button resources-button--primary" disabled={!service.capabilities.bindResource || busy === "bind-resource" || !selectedProject || !availableVersions.includes(Number(version))} title={!service.capabilities.bindResource ? t("resources.bindingBlockedTitle") : undefined}>{busy === "bind-resource" ? t("resources.bindingBusy") : t("resources.bindResource")}</button></div>{!service.capabilities.bindResource && <p className="resources-disabled-note">{t("resources.bindingDisabled")}</p>}{(targetId || path || childMayEdit || projectId || mode !== "local" || versionDirty) && <p className="resources-form-hint" role="status">{t("resources.unsavedBinding")}</p>}</form>;
}

function MaterializationView({ snapshot, service, selectedProjectId, busy, onPlan, onSync }: { snapshot: ResourceWorkspaceSnapshot; service: ResourceService; selectedProjectId?: string; busy?: string; onPlan: (projectId: string) => Promise<boolean>; onSync: () => Promise<boolean> }) {
  const { t } = useI18n();
  const plan = snapshot.materialization;
  const [projectId, setProjectId] = useState(selectedProjectId ?? snapshot.projects[0]?.id ?? "");
  useEffect(() => { if (selectedProjectId) setProjectId(selectedProjectId); }, [selectedProjectId]);
  const canPlan = canPlanMaterialization(service.capabilities, projectId);
  const canSync = canSyncMaterialization(service.capabilities, plan);
  const entries = plan?.entries ?? [];
  return <div className="resources-materialization-layout"><section className="resources-surface resources-materialization-main"><SectionHeading eyebrow={t("resources.matEyebrow")} title={t("resources.matTitle")} detail={t("resources.matDetail")} action={<div className="resources-inline-select"><span>{t("resources.project")}</span><select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">{t("resources.selectProject")}</option>{snapshot.projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></div>} />
    <div className="resources-materialization-actions"><div title={plan?.revisionId}><span className="resources-label">{t("resources.currentPlan")}</span><strong>{plan ? planStatusLabel(t, plan.status) : t("resources.noPlanLoaded")}</strong><p>{plan ? `${plan.projectName} · ${generationLabel(t, plan.desiredGeneration, plan.appliedGeneration)}` : t("resources.matEmptyHint")}</p></div><div className="resources-form-actions"><button className="resources-button resources-button--quiet" disabled={!canPlan || busy === "plan"} onClick={() => projectId && onPlan(projectId)} title={!canPlan ? t("resources.planNeedsProject") : undefined}>{busy === "plan" ? t("resources.planning") : t("resources.buildPlan")}</button><button className="resources-button resources-button--primary" disabled={!canSync || busy === "sync"} onClick={onSync} title={!canSync ? t("resources.syncNeedsPlan") : undefined}>{busy === "sync" ? t("resources.syncing") : t("resources.syncNow")}</button></div></div>{!service.capabilities.planMaterialization && <p className="resources-disabled-note">{t("resources.matDisabled")}</p>}
    {plan ? <><div className="resources-plan-status-row"><div className="resources-plan-status"><span className={`resources-status-dot resources-status-dot--${materializationStatusTone(plan.status)}`} /><div><strong>{planStatusLabel(t, plan.status)}</strong><span>{generationLabel(t, plan.desiredGeneration, plan.appliedGeneration)}</span></div></div><div className="resources-plan-identifiers"><span>{t("resources.planLabel")} <code>{shortDigest(plan.digest)}</code></span><span>{t("resources.attemptKey")} <code>{shortDigest(plan.idempotencyKey)}</code></span></div></div><div className="resources-entry-table"><div className="resources-entry-row resources-entry-row--head"><span>{t("resources.colOperation")}</span><span>{t("resources.colTarget")}</span><span>{t("resources.colStatus")}</span><span>{t("resources.colProvenance")}</span></div>{entries.length ? entries.map((entry) => <div className="resources-entry-row" key={entry.id}><span className="resources-entry-operation"><ResourceIcon kind={entry.kind} /><strong>{kindLabel(t, entry.kind)}</strong>{entry.required ? <em>{t("resources.required")}</em> : <em>{t("resources.optional")}</em>}</span><span className="resources-mono">{entry.target}</span><span><StatusPill tone="neutral">{t("resources.entryPlanned")}</StatusPill>{entry.diagnostic && <small className="resources-entry-diagnostic">{entry.diagnostic}</small>}</span><span className="resources-provenance-inline"><code>{shortDigest(entry.provenance.digest)}</code><small>{entry.provenance.sourceType} · {t("resources.generationValue", { value: entry.provenance.sourceGeneration })}</small></span></div>) : <div className="resources-empty"><strong>{t("resources.planEmptyTitle")}</strong><span>{t("resources.planEmptyHint")}</span></div>}</div>{plan.blockers.length > 0 && <div className="resources-plan-blockers"><strong>{t("resources.planBlockers")}</strong>{plan.blockers.map((blocker) => <span key={blocker}>• {blocker}</span>)}</div>}</> : <div className="resources-empty resources-empty--tall"><strong>{t("resources.noPlanTitle")}</strong><span>{t("resources.noPlanHint")}</span></div>}
  </section><aside className="resources-materialization-side"><section className="resources-surface"><SectionHeading eyebrow={t("resources.guardEyebrow")} title={t("resources.evidenceTitle")} detail={t("resources.evidenceDetail")} /><div className="resources-evidence-list"><div><span>{t("resources.desiredGeneration")}</span><strong>{plan ? t("resources.generationValue", { value: plan.desiredGeneration }) : "—"}</strong></div><div><span>{t("resources.appliedGeneration")}</span><strong>{plan?.appliedGeneration === undefined ? "—" : t("resources.generationValue", { value: plan.appliedGeneration })}</strong></div><div><span>{t("resources.sourceRevision")}</span><strong>{plan?.provenance.sourceRevision ?? "—"}</strong></div><div><span>{t("resources.planDigest")}</span><code>{shortDigest(plan?.digest)}</code></div></div></section><BlockersPanel blockers={snapshot.contract.blockers} compact /></aside></div>;
}

function EvidenceView({ snapshot, selectedResource, diff, onLoadDiff, service, busy }: { snapshot: ResourceWorkspaceSnapshot; selectedResource?: ResourceRecord; diff?: ResourceDiff; onLoadDiff: (from: number, to: number) => Promise<boolean>; service: ResourceService; busy?: string }) {
  const { t } = useI18n();
  const resourceVersions = selectedResource ? versionsForResource(snapshot.versions, selectedResource.id) : [];
  const [fromVersion, setFromVersion] = useState(resourceVersions[1]?.number ? String(resourceVersions[1].number) : "");
  const [toVersion, setToVersion] = useState(resourceVersions[0]?.number ? String(resourceVersions[0].number) : "");
  useEffect(() => { setFromVersion(resourceVersions[1]?.number ? String(resourceVersions[1].number) : ""); setToVersion(resourceVersions[0]?.number ? String(resourceVersions[0].number) : ""); }, [selectedResource?.id, snapshot.versions]);
  const summary = diff ? summarizeDiff(diff) : undefined;
  return <div className="resources-evidence-layout"><section className="resources-surface"><SectionHeading eyebrow={t("resources.diffEyebrow")} title={t("resources.diffTitle")} detail={t("resources.diffDetail")} /><div className="resources-evidence-selector"><label>{t("resources.resourceLabel")}<select value={selectedResource?.id ?? ""} disabled><option value="">{selectedResource?.title ?? t("resources.selectFromResources")}</option></select></label><label>{t("resources.from")}<select value={fromVersion} onChange={(event) => setFromVersion(event.target.value)} disabled={!selectedResource}>{resourceVersions.map((version) => <option key={version.id} value={version.number}>v{version.number} · {version.label}</option>)}</select></label><label>{t("resources.to")}<select value={toVersion} onChange={(event) => setToVersion(event.target.value)} disabled={!selectedResource}>{resourceVersions.map((version) => <option key={version.id} value={version.number}>v{version.number} · {version.label}</option>)}</select></label><button className="resources-button resources-button--primary" disabled={!service.capabilities.readDiff || !selectedResource || !fromVersion || !toVersion || fromVersion === toVersion || busy === "diff"} onClick={() => onLoadDiff(Number(fromVersion), Number(toVersion))}>{busy === "diff" ? t("resources.comparing") : t("resources.compareVersions")}</button></div>{!service.capabilities.readDiff && <p className="resources-disabled-note">{t("resources.compareDisabled")}</p>}{diff ? <div className="resources-diff"><div className="resources-diff__summary"><div><strong>{diff.summary}</strong><span>v{diff.fromVersion} → v{diff.toVersion}</span></div><div className="resources-diff__counts"><span className="resources-diff-add">+{summary?.additions}</span><span className="resources-diff-del">−{summary?.deletions}</span><code>{shortDigest(diff.toDigest)}</code></div></div><div className="resources-diff__lines">{diff.lines.map((line, index) => <div className={`resources-diff-line resources-diff-line--${line.type}`} key={`${index}-${line.text}`}><span>{line.type === "addition" ? "+" : line.type === "deletion" ? "−" : " "}</span><code>{line.text}</code></div>)}</div></div> : <div className="resources-empty resources-empty--tall"><strong>{t("resources.noDiffTitle")}</strong><span>{t("resources.noDiffHint")}</span></div>}</section><section className="resources-surface"><SectionHeading eyebrow={t("resources.sourceChainEyebrow")} title={t("resources.sourceChainTitle")} detail={t("resources.sourceChainDetail")} />{selectedResource ? <div className="resources-provenance-card"><div className="resources-provenance-card__title"><ResourceIcon kind={selectedResource.kind} /><div><strong>{selectedResource.title}</strong><span>{selectedResource.slug}</span></div><StatusPill tone="neutral">{lifecycleLabel(t, selectedResource.lifecycle)}</StatusPill></div><div className="resources-provenance-grid"><div><span>{t("resources.sourceType")}</span><strong>{selectedResource.provenance.sourceType}</strong></div><div><span>{t("resources.sourceId")}</span><strong className="resources-mono" title={selectedResource.provenance.sourceId}>{shortDigest(selectedResource.provenance.sourceId)}</strong></div><div><span>{t("resources.sourceGenerationLabel")}</span><strong>{t("resources.generationValue", { value: selectedResource.provenance.sourceGeneration })}</strong></div><div><span>{t("resources.sourceRevision")}</span><strong>{selectedResource.provenance.sourceRevision ?? "—"}</strong></div><div className="resources-field-wide"><span>{t("resources.digest")}</span><code>{selectedResource.provenance.digest}</code></div></div></div> : <div className="resources-empty"><strong>{t("resources.selectForProvenanceTitle")}</strong><span>{t("resources.selectForProvenanceHint")}</span></div>}</section></div>;
}

function AssetsView({ companyId, service, initialAssets }: { companyId?: string; service?: ContentOpsService; initialAssets?: ContentAsset[] }) {
  const { t } = useI18n();
  if (!companyId || !service) return <section className="resources-surface"><SectionHeading eyebrow={t("resources.assetsEyebrow")} title={t("resources.assetsUnavailable")} detail={t("resources.assetsDetail")} /><p className="resources-disabled-note">{t("resources.assetsNote")}</p></section>;
  return <ContentOpsPanel companyId={companyId} service={service} initialAssets={initialAssets} />;
}

function WorkspaceNav({ active, onSelect }: { active: WorkspaceTab; onSelect: (tab: WorkspaceTab) => void }) {
  const { t } = useI18n();
  const tabs: Array<{ id: WorkspaceTab; label: string; hint: string }> = [{ id: "overview", label: t("resources.tabOverview"), hint: t("resources.tabOverviewHint") }, { id: "resources", label: t("resources.tabResources"), hint: t("resources.tabResourcesHint") }, { id: "assets", label: t("resources.tabAssets"), hint: t("resources.tabAssetsHint") }, { id: "materialization", label: t("resources.tabMaterialization"), hint: t("resources.tabMaterializationHint") }, { id: "evidence", label: t("resources.tabEvidence"), hint: t("resources.tabEvidenceHint") }];
  return <nav className="resources-tabs" aria-label={t("resources.navLabel")}>{tabs.map((tab) => <button key={tab.id} type="button" className={active === tab.id ? "resources-tab resources-tab--active" : "resources-tab"} onClick={() => onSelect(tab.id)} aria-current={active === tab.id ? "page" : undefined}><strong>{tab.label}</strong><span>{tab.hint}</span></button>)}</nav>;
}

export function ResourceWorkspace({ service = blockedService, profileId = "", projectId, companyId, contentOpsService, initialAssets, initialTab, initialSnapshot }: ResourceWorkspaceProps) {
  const { t } = useI18n();
  const [snapshot, setSnapshot] = useState<ResourceWorkspaceSnapshot | undefined>(initialSnapshot);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>(initialTab ?? "overview");
  const [selectedResourceId, setSelectedResourceId] = useState<string | undefined>(initialSnapshot?.resources[0]?.id);
  const [busy, setBusy] = useState<string>();
  const [notice, setNotice] = useState<Notice>();
  const [loadError, setLoadError] = useState<string>();
  const [diff, setDiff] = useState<ResourceDiff>();

  useEffect(() => {
    if (initialSnapshot) {
      setSnapshot(initialSnapshot);
      setSelectedResourceId(initialSnapshot.resources[0]?.id);
      setLoadError(undefined);
      return;
    }
    if (!profileId) {
      setLoadError(t("resources.profileIdRequired"));
      return;
    }
    if (!service.capabilities.readWorkspace) {
      setLoadError(t("resources.workspaceBlocked"));
      return;
    }
    const controller = new AbortController();
    setBusy("load");
    setLoadError(undefined);
    service.loadWorkspace({ profileId, projectId }, { signal: controller.signal }).then((loaded) => {
      if (controller.signal.aborted) return;
      setSnapshot(loaded);
      setSelectedResourceId(loaded.resources[0]?.id);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setLoadError(blockerMessage(t, error));
    }).finally(() => {
      if (!controller.signal.aborted) setBusy(undefined);
    });
    return () => controller.abort();
  }, [initialSnapshot, profileId, projectId, service]);

  function execute<T>(operation: string, action: () => Promise<T>, onSuccess: (result: T) => void, successMessage: string): Promise<boolean> {
    setBusy(operation);
    setNotice(undefined);
    return action().then((result) => {
      onSuccess(result);
      setNotice({ tone: "success", message: successMessage });
      return true;
    }).catch((error: unknown) => {
      setNotice({ tone: "error", message: blockerMessage(t, error) });
      return false;
    }).finally(() => setBusy(undefined));
  }

  const blockedWithoutSnapshot = !service.capabilities.readWorkspace && Boolean(profileId);
  if (!snapshot) return <main className="resources-workspace"><div className="resources-page-heading"><div><p className="resources-eyebrow">{t("resources.eyebrow")}</p><h1>{t("resources.workspaceTitle")}</h1><p className="resources-muted">{t("resources.loadingSnapshot")}</p></div><StatusPill tone="warning">{busy === "load" ? t("resources.statusLoading") : t("resources.statusUnavailable")}</StatusPill></div><WorkspaceNav active={activeTab} onSelect={setActiveTab} />{activeTab === "assets" ? <AssetsView companyId={companyId} service={contentOpsService} initialAssets={initialAssets} /> : <><BlockersPanel blockers={RESOURCE_CONTRACT_BLOCKERS} /><div className="resources-empty resources-empty--tall"><strong>{loadError ?? (blockedWithoutSnapshot ? t("resources.loadBlockedTitle") : t("resources.noSnapshotTitle"))}</strong><span>{loadError || blockedWithoutSnapshot ? t("resources.loadBlockedHint") : t("resources.noSnapshotHint")}</span></div></>}</main>;

  const blockers: ResourceContractBlocker[] = snapshot.contract.ready
    ? snapshot.contract.blockers
    : snapshot.contract.blockers.length ? snapshot.contract.blockers : [...RESOURCE_CONTRACT_BLOCKERS];
  const selectedResource = snapshot.resources.find((resource) => resource.id === selectedResourceId);
  // The command handlers below are function declarations, which are hoisted, so the
  // `!snapshot` narrowing above does not reach them; bind the narrowed value once.
  const workspace = snapshot;
  function saveProfile(name: string, description: string) {
    return execute("profile", () => service.updateProfile({ profileId: workspace.profile.id, name, description, expectedRevision: workspace.profile.revision }), (profile) => setSnapshot((current) => current ? updateResourceSnapshot(current, { profile }) : current), t("resources.noticeProfileSaved"));
  }

  function createResource(input: { kind: ResourceKind; slug: string; title: string; description: string; scope: ResourceScope }) {
    return execute("create-resource", () => service.createResource({ profileId: workspace.profile.id, ...input }), (resource) => { setSnapshot((current) => current ? updateResourceSnapshot(current, { resources: [...current.resources, resource] }) : current); setSelectedResourceId(resource.id); setActiveTab("resources"); }, t("resources.noticeResourceCreated"));
  }

  function createVersion(input: { label: string; changeNote: string; payload: ResourcePayload }): Promise<boolean> {
    if (!selectedResource) return Promise.resolve(false);
    return execute("create-version", () => service.createVersion({ resourceId: selectedResource.id, ...input, expectedActiveVersion: selectedResource.activeVersionNumber }), (version) => setSnapshot((current) => current ? updateResourceSnapshot(current, { versions: [...current.versions, version], resources: current.resources.map((resource) => resource.id === version.resourceId ? { ...resource, activeVersionNumber: version.number, versionNumbers: Array.from(new Set([...resource.versionNumbers, version.number])).sort((left, right) => left - right) } : resource) }) : current), t("resources.noticeVersionCreated"));
  }

  function bindResource(input: { projectId: string; projectName: string; target: ResourceScope; mode: "local" | "global"; versionNumber: number; workspacePath?: string; childMayEdit: boolean }): Promise<boolean> {
    if (!selectedResource) return Promise.resolve(false);
    return execute("bind-resource", () => service.bindResource({ resourceId: selectedResource.id, ...input }), (binding) => setSnapshot((current) => current ? updateResourceSnapshot(current, { bindings: [...current.bindings, binding], resources: current.resources.map((resource) => resource.id === binding.resourceId ? { ...resource, bindingCount: resource.bindingCount + 1 } : resource) }) : current), t("resources.noticeBindingCreated"));
  }

  function planMaterializationForProject(nextProjectId: string) {
    return execute("plan", () => service.planMaterialization({ profileId: workspace.profile.id, projectId: nextProjectId, expectedGeneration: workspace.materialization?.desiredGeneration }), (plan) => setSnapshot((current) => current ? updateResourceSnapshot(current, { materialization: plan }) : current), t("resources.noticePlanReady"));
  }

  function syncMaterialization() {
    const materialization = workspace.materialization;
    if (!materialization) return Promise.resolve(false);
    return execute("sync", () => service.syncMaterialization({ planId: materialization.id, expectedGeneration: materialization.desiredGeneration }), (plan) => setSnapshot((current) => current ? updateResourceSnapshot(current, { materialization: plan }) : current), t("resources.noticeSyncDone"));
  }

  function loadDiff(fromVersion: number, toVersion: number) {
    if (!selectedResource) return Promise.resolve(false);
    return execute("diff", () => service.loadDiff({ resourceId: selectedResource.id, fromVersion, toVersion }), setDiff, t("resources.noticeDiffLoaded"));
  }

  return <main className="resources-workspace"><header className="resources-page-heading"><div><p className="resources-eyebrow">{t("resources.eyebrow")}</p><h1>{snapshot.profile.name}</h1><p className="resources-muted">{t("resources.workspaceSubtitle")}</p></div><div className="resources-heading-meta"><StatusPill tone={snapshot.contract.ready ? "positive" : "danger"}>{snapshot.contract.ready ? t("resources.contractReady") : t("resources.contractBlocked")}</StatusPill><span className="resources-revision">{snapshot.profile.revision}</span></div></header>{notice && <div className={`resources-notice resources-notice--${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>{notice.message}</div>}<WorkspaceNav active={activeTab} onSelect={setActiveTab} />{activeTab === "overview" && <ProfileOverview snapshot={{ ...snapshot, contract: { ...snapshot.contract, blockers } }} service={service} busy={busy} onSaveProfile={saveProfile} onSelectTab={setActiveTab} />}{activeTab === "resources" && <><ResourceCatalog snapshot={snapshot} service={service} selectedResourceId={selectedResourceId} onSelectResource={(id) => { setSelectedResourceId(id); setDiff(undefined); }} onOpenDiff={(from, to) => { setActiveTab("evidence"); void loadDiff(from, to); }} onCreateResource={createResource} busy={busy} /><div className="resources-secondary-forms"><VersionEditor resource={selectedResource} versions={snapshot.versions} service={service} busy={busy} onCreateVersion={createVersion} /><BindingForm resource={selectedResource} projects={snapshot.projects} service={service} busy={busy} onBind={bindResource} /></div></>}{activeTab === "assets" && <AssetsView companyId={companyId} service={contentOpsService} initialAssets={initialAssets} />}{activeTab === "materialization" && <MaterializationView snapshot={{ ...snapshot, contract: { ...snapshot.contract, blockers } }} service={service} selectedProjectId={projectId} busy={busy} onPlan={planMaterializationForProject} onSync={syncMaterialization} />}{activeTab === "evidence" && <EvidenceView snapshot={{ ...snapshot, contract: { ...snapshot.contract, blockers } }} selectedResource={selectedResource} diff={diff} onLoadDiff={loadDiff} service={service} busy={busy} />}<div className="resources-bottom-blockers"><BlockersPanel blockers={blockers} /></div><footer className="resources-footer"><span>{t("resources.footerEvidence")}</span><span>{t("resources.footerDigests")}</span></footer></main>;
}

import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import "./resources.css";
import { RESOURCE_CONTRACT_BLOCKERS } from "./blockers";
import { createBlockedResourceService, ResourceContractBlockedError, type ResourceService } from "./contracts";
import { ContentOpsPanel } from "./content-ops/ContentOpsPanel";
import type { ContentOpsService } from "./content-ops/contracts";
import type { ContentAsset } from "./content-ops/types";
import {
  canPlanMaterialization,
  canSyncMaterialization,
  formatGeneration,
  latestVersionNumber,
  materializationStatusLabel,
  materializationStatusTone,
  resourceKindGlyph,
  resourceKindLabel,
  resourceLifecycleLabel,
  resourceLifecycleTone,
  selectVisibleResources,
  summarizeDiff,
  versionsForResource,
  type ResourceKindFilter,
} from "./view-model";
import type {
  AgentProfile,
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

function formatDate(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function shortDigest(value?: string): string {
  if (!value) return "—";
  return value.length > 22 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
}

function blockerMessage(error: unknown): string {
  if (error instanceof ResourceContractBlockedError) return "Заблокировано: контракт ресурса ещё не подтверждён.";
  return error instanceof Error ? error.message : "Операция не выполнена.";
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

function BlockersPanel({ blockers, compact = false }: { blockers: readonly ResourceContractBlocker[]; compact?: boolean }) {
  if (!blockers.length) return null;
  return <aside className={`resources-blockers${compact ? " resources-blockers--compact" : ""}`} aria-label="Contract blockers">
    <div className="resources-blockers__header"><span className="resources-alert-icon" aria-hidden="true">!</span><div><p className="resources-eyebrow">Fail closed</p><h2>Contract blockers</h2></div><span className="resources-blockers__count">{blockers.length}</span></div>
    <div className="resources-blockers__list">{blockers.map((blocker) => <div className="resources-blocker" key={blocker.code}><div><strong>{blocker.title}</strong><span>{blocker.detail}</span></div><code>{blocker.code} · {blocker.owner}</code></div>)}</div>
  </aside>;
}

function ScopeBadge({ scope }: { scope: ResourceScope }) {
  return <span className="resources-scope"><span>{scope.kind}</span><strong>{scope.label ?? "Unlabeled scope"}</strong><code>{scope.id || "id unavailable"}</code></span>;
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
  const [name, setName] = useState(profile.name);
  const [description, setDescription] = useState(profile.description);
  const [editing, setEditing] = useState(false);
  const unsaved = name !== profile.name || description !== profile.description;

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
      <div className="resources-card-kicker"><span>Agent profile</span><StatusPill tone={profile.status === "active" ? "positive" : profile.status === "blocked" ? "danger" : "warning"}>{profile.status}</StatusPill></div>
      {editing ? <form className="resources-inline-form" onSubmit={submit}>
        {unsaved && <p className="resources-form-hint" role="status">Unsaved profile changes · local draft only.</p>}
        <label>Profile name<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
        <label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></label>
        <div className="resources-form-actions"><button type="button" className="resources-button resources-button--quiet" onClick={() => setEditing(false)}>Cancel</button><button className="resources-button resources-button--primary" disabled={!canUpdate || busy || !name.trim()}>{busy ? "Saving…" : "Save profile"}</button></div>
        {!canUpdate && <p className="resources-form-hint">Editing unlocks when the reviewed profile command is wired.</p>}
      </form> : <>
        <div className="resources-profile-title-row"><div><h2>{profile.name}</h2><p>{profile.description || "No description"}</p></div><button className="resources-button resources-button--quiet" onClick={() => setEditing(true)} disabled={!canUpdate} title={!canUpdate ? "Profile update contract is blocked" : undefined}>Edit profile</button></div>
        <div className="resources-profile-meta"><span>Revision <strong>{profile.revision}</strong></span><span>Updated <strong>{formatDate(profile.updatedAt)}</strong></span><span>Scope <strong>{profile.projectCount} projects</strong></span></div>{!canUpdate && <p className="resources-disabled-note">Profile editing is disabled: no reviewed profile update command or revision guard is connected.</p>}
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
  const latest = latestVersionNumber(resource, versions);
  return <button type="button" className={`resources-resource-card${selected ? " resources-resource-card--selected" : ""}`} onClick={onSelect} aria-pressed={selected}>
    <div className="resources-resource-card__top"><ResourceIcon kind={resource.kind} /><StatusPill tone={resourceLifecycleTone(resource.lifecycle)}>{resourceLifecycleLabel(resource.lifecycle)}</StatusPill></div>
    <div className="resources-resource-card__body"><h3>{resource.title}</h3><p>{resource.description || resource.slug}</p><ScopeBadge scope={resource.scope} /></div>
    <div className="resources-resource-card__footer"><span>{resourceKindLabel(resource.kind)}</span><span>v{latest ?? "—"} · {resource.bindingCount} bindings</span></div>
  </button>;
}

function ResourceDetail({ resource, versions, bindings, onOpenDiff }: { resource: ResourceRecord; versions: ResourceVersion[]; bindings: ResourceBinding[]; onOpenDiff: (from: number, to: number) => void }) {
  const resourceVersions = versionsForResource(versions, resource.id);
  const resourceBindings = bindings.filter((binding) => binding.resourceId === resource.id);
  const currentVersion = resourceVersions.find((version) => version.number === resource.activeVersionNumber) ?? resourceVersions[0];
  return <article className="resources-detail-card">
    <div className="resources-detail-card__header"><div className="resources-resource-title"><ResourceIcon kind={resource.kind} /><div><p className="resources-eyebrow">{resourceKindLabel(resource.kind)} · {resource.slug}</p><h3>{resource.title}</h3><p className="resources-muted">{resource.description || "No description"}</p></div></div><ScopeBadge scope={resource.scope} /></div>
    <div className="resources-detail-grid"><div><span className="resources-label">Active version</span><strong>{resource.activeVersionNumber ? `v${resource.activeVersionNumber}` : "Not published"}</strong><small>{currentVersion?.label ?? "No version label"}</small></div><div><span className="resources-label">History</span><strong>{resourceVersions.length} versions</strong><small>Append-only revisions</small></div><div><span className="resources-label">Bindings</span><strong>{resourceBindings.length}</strong><small>{resourceBindings.length ? resourceBindings.map((binding) => binding.projectName).join(", ") : "No projects"}</small></div><div><span className="resources-label">Source digest</span><strong className="resources-mono">{shortDigest(resource.provenance.digest)}</strong><small>{resource.provenance.sourceType} · g{resource.provenance.sourceGeneration}</small></div></div>
    <div className="resources-binding-list"><div className="resources-binding-list__header"><span className="resources-label">Project bindings</span><span>{resourceBindings.length} explicit target{resourceBindings.length === 1 ? "" : "s"}</span></div>{resourceBindings.length ? resourceBindings.map((binding) => <div className="resources-binding-row" key={binding.id}><div><strong>{binding.projectName}</strong><span><ScopeBadge scope={binding.target} />{binding.mode} · v{binding.versionNumber}</span></div><code>{binding.workspacePath ?? "No workspace path"}</code></div>) : <span className="resources-muted">No project binding returned.</span>}</div>
    <div className="resources-version-strip"><div><span className="resources-label">Version history</span><div className="resources-version-list">{resourceVersions.length ? resourceVersions.map((version) => <span className={`resources-version-chip${version.number === resource.activeVersionNumber ? " resources-version-chip--active" : ""}`} key={version.id}>v{version.number}<small>{version.status}</small></span>) : <span className="resources-muted">No versions returned</span>}</div></div>{resourceVersions.length > 1 && <button className="resources-button resources-button--quiet" onClick={() => onOpenDiff(resourceVersions[1].number, resourceVersions[0].number)}>Compare latest</button>}</div>
  </article>;
}

function ProfileOverview({ snapshot, service, busy, onSaveProfile, onSelectTab }: { snapshot: ResourceWorkspaceSnapshot; service: ResourceService; busy: string | undefined; onSaveProfile: (name: string, description: string) => Promise<boolean>; onSelectTab: (tab: WorkspaceTab) => void }) {
  const active = snapshot.resources.filter((resource) => resource.lifecycle === "active").length;
  const bound = snapshot.bindings.length;
  const plan = snapshot.materialization;
  // Entries on a *plan* are all `planned`: the plan is a preview, and an apply result
  // is a separate response (`MaterializationOutcome`). The ring therefore counts the
  // entry set, not a per-entry applied flag that a plan never carries.
  const plannedEntries = plan ? plan.entries : [];
  return <div className="resources-overview-grid">
    <div className="resources-overview-main"><ProfileCard profile={snapshot.profile} canUpdate={service.capabilities.updateProfile} busy={busy === "profile"} onSave={onSaveProfile} />
      <div className="resources-stats-grid"><Stat label="Resources" value={snapshot.resources.length} detail={`${active} active`} /><Stat label="Versions" value={snapshot.versions.length} detail="Immutable history" /><Stat label="Project bindings" value={bound} detail="Explicit targets" /><Stat label="Plan generation" value={plan ? `g${plan.desiredGeneration}` : "—"} detail={plan ? materializationStatusLabel(plan.status) : "No plan loaded"} /></div>
      <section className="resources-surface"><SectionHeading eyebrow="Resource mix" title="What this profile carries" detail="Prompts, files, and MCP servers stay visible as first-class resources." action={<button className="resources-button resources-button--quiet" onClick={() => onSelectTab("resources")}>Manage resources</button>} />
        <div className="resources-mix-grid">{(["prompt", "file", "mcp"] as ResourceKind[]).map((kind) => { const count = snapshot.resources.filter((resource) => resource.kind === kind).length; return <button type="button" className="resources-mix-card" key={kind} onClick={() => onSelectTab("resources")}><ResourceIcon kind={kind} /><span>{resourceKindLabel(kind)}</span><strong>{count}</strong><small>resources</small></button>; })}</div>
      </section>
    </div>
    <div className="resources-overview-side"><section className="resources-surface resources-surface--accent"><SectionHeading eyebrow="Materialization" title={plan ? materializationStatusLabel(plan.status) : "No plan"} detail={plan ? formatGeneration(plan.desiredGeneration, plan.appliedGeneration) : "Build a reviewed plan for a project."} /><div className="resources-plan-preview">{plan ? <><div className="resources-plan-ring"><strong>{plannedEntries.length}</strong><span>/ {plan.entries.length}</span></div><div><strong>{plan.projectName}</strong><p>{plan.entries.length} operations · {shortDigest(plan.digest)}</p><button className="resources-text-button" onClick={() => onSelectTab("materialization")}>View plan →</button></div></> : <button className="resources-button resources-button--primary" onClick={() => onSelectTab("materialization")} disabled={!service.capabilities.planMaterialization}>Open materialization</button>}</div></section><BlockersPanel blockers={snapshot.contract.blockers} compact /></div>
  </div>;
}

function ResourceCatalog({ snapshot, service, onSelectResource, selectedResourceId, onOpenDiff, onCreateResource, busy }: { snapshot: ResourceWorkspaceSnapshot; service: ResourceService; onSelectResource: (id: string) => void; selectedResourceId?: string; onOpenDiff: (from: number, to: number) => void;  onCreateResource: (input: { kind: ResourceKind; slug: string; title: string; description: string; scope: ResourceScope }) => Promise<boolean>; busy?: string }) {
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
  return <div className="resources-catalog-layout"><div className="resources-catalog-main"><SectionHeading eyebrow="Resource catalog" title="Profile resources" detail="Select a resource to inspect versions, bindings, and provenance." action={<button className="resources-button resources-button--primary" onClick={() => setShowCreate((value) => !value)}>{showCreate ? "Close form" : "Add resource"}</button>} />
    {showCreate && <form className="resources-form-card" onSubmit={submitCreate}><div className="resources-form-card__header"><div><p className="resources-eyebrow">New resource</p><h3>Start a typed resource</h3></div><span className="resources-form-status">Payload comes in a version</span></div>{(slug || title || description || scopeId) && <p className="resources-form-hint" role="status">Unsaved resource changes · local draft only.</p>}<div className="resources-form-grid"><label>Type<select value={draftKind} onChange={(event) => setDraftKind(event.target.value as ResourceKind)}><option value="prompt">Prompt</option><option value="file">File</option><option value="mcp">MCP server</option></select></label><label>Slug<input value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="review-policy" required /></label><label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Review policy" required /></label><label>Owner scope<select value={scopeKind} onChange={(event) => setScopeKind(event.target.value as ResourceScopeKind)}>{["platform", "company", "cabinet", "project", "session"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label><label>Scope id<input value={scopeId} onChange={(event) => setScopeId(event.target.value)} placeholder="project-123" required /></label><label className="resources-field-wide">Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={2} placeholder="What should this resource provide?" /></label></div><div className="resources-form-actions"><span className="resources-form-hint">No resource is created until the service returns an authoritative record.</span><button className="resources-button resources-button--primary" disabled={!service.capabilities.createResource || busy === "create-resource" || !slug.trim() || !title.trim() || !scopeId.trim()} title={!service.capabilities.createResource ? "Resource create API is not connected" : undefined}>{busy === "create-resource" ? "Creating…" : "Create resource"}</button></div></form>}
    <div className="resources-toolbar"><label className="resources-search"><span aria-hidden="true">⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search title, slug, scope" aria-label="Search resources" /></label><div className="resources-filter-group" role="group" aria-label="Resource type filter">{(["all", "prompt", "file", "mcp"] as ResourceKindFilter[]).map((value) => <button type="button" key={value} className={kind === value ? "resources-filter resources-filter--active" : "resources-filter"} onClick={() => setKind(value)}>{value === "all" ? "All" : resourceKindLabel(value)}</button>)}</div><span className="resources-toolbar-count">{visible.length} shown</span></div>
    <div className="resources-resource-grid">{visible.length ? visible.map((resource) => <ResourceCard key={resource.id} resource={resource} versions={snapshot.versions} selected={resource.id === selected?.id} onSelect={() => onSelectResource(resource.id)} />) : <div className="resources-empty"><strong>No matching resources</strong><span>Try another filter or wait for the resource facade to return data.</span></div>}</div>
{!service.capabilities.createResource && <p className="resources-disabled-note">Create is disabled: no confirmed resource create endpoint is connected.</p>}{!service.capabilities.readWorkspace && <p className="resources-disabled-note">Resource profiles and prompt/file/MCP lists are unavailable: no reviewed workspace snapshot endpoint is connected.</p>}{!service.capabilities.readDiff && <p className="resources-disabled-note">Version comparison is disabled: no exact-version diff API is connected.</p>}

  </div>{selected ? <div className="resources-catalog-detail"><ResourceDetail resource={selected} versions={snapshot.versions} bindings={snapshot.bindings} onOpenDiff={onOpenDiff} /></div> : <div className="resources-catalog-detail"><div className="resources-empty resources-empty--tall"><strong>Select a resource</strong><span>Version history and bindings will appear here.</span></div></div>}</div>;
}

function VersionEditor({ resource, versions, service, busy, onCreateVersion }: { resource?: ResourceRecord; versions: ResourceVersion[]; service: ResourceService; busy?: string; onCreateVersion: (input: { label: string; changeNote: string; payload: ResourcePayload }) => Promise<boolean> }) {
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

  if (!resource) return <div className="resources-empty"><strong>Choose a resource first</strong><span>A version form is shown after selecting a card.</span></div>;
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

  return <form className="resources-form-card resources-version-editor" onSubmit={submit}><div className="resources-form-card__header"><div><p className="resources-eyebrow">New immutable version</p><h3>{resource.title}</h3></div><span className="resources-form-status">{resourceKindLabel(resource.kind)}</span></div><label className="resources-field-wide">Version to inspect<select value={selectedVersion?.id ?? ""} onChange={(event) => setBaseVersionId(event.target.value)} disabled={!resourceVersions.length}>{resourceVersions.length ? resourceVersions.map((version) => <option key={version.id} value={version.id}>v{version.number} · {version.label} · {version.status}</option>) : <option value="">No resource versions returned</option>}</select></label>{selectedVersion && <><p className="resources-form-hint">Inspecting v{selectedVersion.number} · {selectedVersion.status} · {selectedVersion.createdBy} · {shortDigest(selectedVersion.digest)}. Saving appends a new version; this source version stays unchanged.</p><pre className="resources-version-preview">{JSON.stringify(selectedVersion.payload, null, 2)}</pre><button type="button" className="resources-button resources-button--quiet" disabled={!service.capabilities.createVersion} title={!service.capabilities.createVersion ? "Resource version create API is not connected" : undefined} onClick={() => { setLabel(`Based on v${selectedVersion.number}`); if (selectedVersion.payload.kind === "prompt") { setBody(selectedVersion.payload.body); setPath(""); setServer(""); setTools(""); } else if (selectedVersion.payload.kind === "file") { setPath(selectedVersion.payload.path); setBody(selectedVersion.payload.contents); setServer(""); setTools(""); } else { setServer(selectedVersion.payload.server); setTools(selectedVersion.payload.tools.join(", ")); setPath(""); setBody(""); } }}>Use as starting point</button></>}{(label || changeNote || body || path || server || tools) && <p className="resources-form-hint" role="status">Unsaved version changes · local draft only.</p>}<div className="resources-form-grid"><label>Version label<input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Tighten approval rules" required /></label><label>Change note<input value={changeNote} onChange={(event) => setChangeNote(event.target.value)} placeholder="Why this changed" /></label>{resource.kind === "file" && <label>Workspace path<input value={path} onChange={(event) => setPath(event.target.value)} placeholder="config/policy.md" required /></label>}{resource.kind === "mcp" && <><label>Server reference<input value={server} onChange={(event) => setServer(event.target.value)} placeholder="catalog-mcp" required /></label><label>Allowed tools<input value={tools} onChange={(event) => setTools(event.target.value)} placeholder="search, get_item" required /></label></>}{resource.kind === "prompt" && <p className="resources-form-hint resources-field-wide">Use {"{{variable}}"} placeholders when the prompt needs explicit runtime inputs.</p>}<label className="resources-field-wide">{resource.kind === "prompt" ? "Prompt body" : resource.kind === "file" ? "File contents" : "Server notes"}<textarea value={body} onChange={(event) => setBody(event.target.value)} rows={7} placeholder={resource.kind === "prompt" ? "You are reviewing…" : resource.kind === "file" ? "# Policy\n" : "Describe the server boundary and review notes."} required /></label></div><div className="resources-form-actions"><span className="resources-form-hint">Published versions remain immutable; saving appends a new revision.</span><button className="resources-button resources-button--primary" disabled={!service.capabilities.createVersion || busy === "create-version"} title={!service.capabilities.createVersion ? "Resource version create API is not connected" : undefined}>{busy === "create-version" ? "Publishing…" : "Create version"}</button></div>{!service.capabilities.createVersion && <p className="resources-disabled-note">Version creation is disabled: the resource API and version CAS contract are not connected.</p>}</form>;
}

function BindingForm({ resource, projects, service, busy, onBind }: { resource?: ResourceRecord; projects: ResourceProject[]; service: ResourceService; busy?: string; onBind: (input: { projectId: string; projectName: string; target: ResourceScope; mode: "local" | "global"; versionNumber: number; workspacePath?: string; childMayEdit: boolean }) => Promise<boolean> }) {
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

  if (!resource) return <div className="resources-empty"><strong>Choose a resource first</strong><span>Binding fields are scoped to the selected resource.</span></div>;
  const selectedProject = projects.find((project) => project.id === projectId);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!selectedProject) return;
    const bound = await onBind({ projectId, projectName: selectedProject.name, target: { kind: "project", id: targetId.trim() || projectId, label: targetId.trim() || selectedProject.name }, mode, versionNumber: Number(version), workspacePath: path.trim() || undefined, childMayEdit });
    if (!bound) return;
    setProjectId(""); setTargetId(""); setPath(""); setChildMayEdit(false);
  }
  return <form className="resources-form-card resources-binding-form" onSubmit={submit}><div className="resources-form-card__header"><div><p className="resources-eyebrow">Project binding</p><h3>Make the target explicit</h3></div><span className="resources-form-status">Pinned version required</span></div><div className="resources-form-grid"><label>Project<select value={projectId} onChange={(event) => setProjectId(event.target.value)} required><option value="">Select project</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><label>Version<select value={version} onChange={(event) => setVersion(event.target.value)} required>{availableVersions.length ? availableVersions.map((number) => <option key={number} value={number}>v{number}</option>) : <option value="">No versions</option>}</select></label><label>Target id<input value={targetId} onChange={(event) => setTargetId(event.target.value)} placeholder="project-123" /></label><label>Binding mode<select value={mode} onChange={(event) => setMode(event.target.value as "local" | "global")}><option value="local">Local · child can diverge</option><option value="global">Global · shared source</option></select></label><label className="resources-field-wide">Workspace path<input value={path} onChange={(event) => setPath(event.target.value)} placeholder={resource.kind === "file" ? "config/policy.md" : "Optional target path"} /></label><label className="resources-checkbox resources-field-wide"><input type="checkbox" checked={childMayEdit} onChange={(event) => setChildMayEdit(event.target.checked)} /> Allow project child edits <span>Changes must still produce a new project-scoped version.</span></label></div><div className="resources-form-actions"><span className="resources-form-hint">Binding editor is read-only until a confirmed project-binding endpoint and typed scope-admission contract are connected. Local/global mode and pinned version are retained for review.</span><button className="resources-button resources-button--primary" disabled={!service.capabilities.bindResource || busy === "bind-resource" || !selectedProject || !availableVersions.includes(Number(version))} title={!service.capabilities.bindResource ? "No reviewed resource binding API is connected" : undefined}>{busy === "bind-resource" ? "Binding…" : "Bind resource"}</button></div>{!service.capabilities.bindResource && <p className="resources-disabled-note">Binding is read-only: no confirmed project-binding endpoint or typed scope-admission contract is connected.</p>}{(targetId || path || childMayEdit || projectId || mode !== "local" || versionDirty) && <p className="resources-form-hint" role="status">Unsaved binding changes · local draft only.</p>}</form>;
}

function MaterializationView({ snapshot, service, selectedProjectId, busy, onPlan, onSync }: { snapshot: ResourceWorkspaceSnapshot; service: ResourceService; selectedProjectId?: string; busy?: string; onPlan: (projectId: string) => Promise<boolean>; onSync: () => Promise<boolean> }) {
  const plan = snapshot.materialization;
  const [projectId, setProjectId] = useState(selectedProjectId ?? snapshot.projects[0]?.id ?? "");
  useEffect(() => { if (selectedProjectId) setProjectId(selectedProjectId); }, [selectedProjectId]);
  const canPlan = canPlanMaterialization(service.capabilities, projectId);
  const canSync = canSyncMaterialization(service.capabilities, plan);
  const entries = plan?.entries ?? [];
  return <div className="resources-materialization-layout"><section className="resources-surface resources-materialization-main"><SectionHeading eyebrow="Materialization lifecycle" title="Plan, inspect, sync" detail="Every entry carries a target, digest, generation, and source provenance." action={<div className="resources-inline-select"><span>Project</span><select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">Select project</option>{snapshot.projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></div>} />
    <div className="resources-materialization-actions"><div><span className="resources-label">Current plan</span><strong>{plan ? plan.revisionId : "No plan loaded"}</strong><p>{plan ? `${plan.projectName} · ${formatGeneration(plan.desiredGeneration, plan.appliedGeneration)}` : "Build a plan from an authoritative snapshot."}</p></div><div className="resources-form-actions"><button className="resources-button resources-button--quiet" disabled={!canPlan || busy === "plan"} onClick={() => projectId && onPlan(projectId)} title={!canPlan ? "Planning requires a confirmed service and project" : undefined}>{busy === "plan" ? "Planning…" : "Build plan"}</button><button className="resources-button resources-button--primary" disabled={!canSync || busy === "sync"} onClick={onSync} title={!canSync ? "Sync requires an atomic apply adapter" : undefined}>{busy === "sync" ? "Syncing…" : "Sync materialization"}</button></div></div>{!service.capabilities.planMaterialization && <p className="resources-disabled-note">Plan and sync are unavailable: no reviewed materialization-plan adapter or atomic generation-checked apply API is connected. This view is read-only.</p>}
    {plan ? <><div className="resources-plan-status-row"><div className="resources-plan-status"><span className={`resources-status-dot resources-status-dot--${materializationStatusTone(plan.status)}`} /><div><strong>{materializationStatusLabel(plan.status)}</strong><span>{formatGeneration(plan.desiredGeneration, plan.appliedGeneration)}</span></div></div><div className="resources-plan-identifiers"><span>Plan <code>{shortDigest(plan.digest)}</code></span><span>Idempotency <code>{shortDigest(plan.idempotencyKey)}</code></span></div></div><div className="resources-entry-table"><div className="resources-entry-row resources-entry-row--head"><span>Operation</span><span>Target</span><span>Status</span><span>Provenance</span></div>{entries.length ? entries.map((entry) => <div className="resources-entry-row" key={entry.id}><span className="resources-entry-operation"><ResourceIcon kind={entry.kind} /><strong>{resourceKindLabel(entry.kind)}</strong>{entry.required ? <em>required</em> : <em>optional</em>}</span><span className="resources-mono">{entry.target}</span><span><StatusPill tone="neutral">{entry.status}</StatusPill>{entry.diagnostic && <small className="resources-entry-diagnostic">{entry.diagnostic}</small>}</span><span className="resources-provenance-inline"><code>{shortDigest(entry.provenance.digest)}</code><small>{entry.provenance.sourceType} · g{entry.provenance.sourceGeneration}</small></span></div>) : <div className="resources-empty"><strong>Plan has no entries</strong><span>The server returned an empty operation set.</span></div>}</div>{plan.blockers.length > 0 && <div className="resources-plan-blockers"><strong>Plan blockers</strong>{plan.blockers.map((blocker) => <span key={blocker}>• {blocker}</span>)}</div>}</> : <div className="resources-empty resources-empty--tall"><strong>No materialization plan</strong><span>Choose a project and build a plan after the API contract is wired.</span></div>}
  </section><aside className="resources-materialization-side"><section className="resources-surface"><SectionHeading eyebrow="Generation guard" title="Apply evidence" detail="A sync must match the plan generation and full operation digest." /><div className="resources-evidence-list"><div><span>Desired generation</span><strong>{plan ? `g${plan.desiredGeneration}` : "—"}</strong></div><div><span>Applied generation</span><strong>{plan?.appliedGeneration === undefined ? "—" : `g${plan.appliedGeneration}`}</strong></div><div><span>Source revision</span><strong>{plan?.provenance.sourceRevision ?? "—"}</strong></div><div><span>Plan digest</span><code>{shortDigest(plan?.digest)}</code></div></div></section><BlockersPanel blockers={snapshot.contract.blockers} compact /></aside></div>;
}

function EvidenceView({ snapshot, selectedResource, diff, onLoadDiff, service, busy }: { snapshot: ResourceWorkspaceSnapshot; selectedResource?: ResourceRecord; diff?: ResourceDiff; onLoadDiff: (from: number, to: number) => Promise<boolean>; service: ResourceService; busy?: string }) {
  const resourceVersions = selectedResource ? versionsForResource(snapshot.versions, selectedResource.id) : [];
  const [fromVersion, setFromVersion] = useState(resourceVersions[1]?.number ? String(resourceVersions[1].number) : "");
  const [toVersion, setToVersion] = useState(resourceVersions[0]?.number ? String(resourceVersions[0].number) : "");
  useEffect(() => { setFromVersion(resourceVersions[1]?.number ? String(resourceVersions[1].number) : ""); setToVersion(resourceVersions[0]?.number ? String(resourceVersions[0].number) : ""); }, [selectedResource?.id, snapshot.versions]);
  const summary = diff ? summarizeDiff(diff) : undefined;
  return <div className="resources-evidence-layout"><section className="resources-surface"><SectionHeading eyebrow="Provenance & diff" title="Trace every version" detail="Compare immutable revisions and inspect where a resource came from." /><div className="resources-evidence-selector"><label>Resource<select value={selectedResource?.id ?? ""} disabled><option value="">{selectedResource?.title ?? "Select from Resources"}</option></select></label><label>From<select value={fromVersion} onChange={(event) => setFromVersion(event.target.value)} disabled={!selectedResource}>{resourceVersions.map((version) => <option key={version.id} value={version.number}>v{version.number} · {version.label}</option>)}</select></label><label>To<select value={toVersion} onChange={(event) => setToVersion(event.target.value)} disabled={!selectedResource}>{resourceVersions.map((version) => <option key={version.id} value={version.number}>v{version.number} · {version.label}</option>)}</select></label><button className="resources-button resources-button--primary" disabled={!service.capabilities.readDiff || !selectedResource || !fromVersion || !toVersion || fromVersion === toVersion || busy === "diff"} onClick={() => onLoadDiff(Number(fromVersion), Number(toVersion))}>{busy === "diff" ? "Comparing…" : "Compare versions"}</button></div>{!service.capabilities.readDiff && <p className="resources-disabled-note">Compare is disabled: no confirmed exact-version resource diff API is connected.</p>}{diff ? <div className="resources-diff"><div className="resources-diff__summary"><div><strong>{diff.summary}</strong><span>v{diff.fromVersion} → v{diff.toVersion}</span></div><div className="resources-diff__counts"><span className="resources-diff-add">+{summary?.additions}</span><span className="resources-diff-del">−{summary?.deletions}</span><code>{shortDigest(diff.toDigest)}</code></div></div><div className="resources-diff__lines">{diff.lines.map((line, index) => <div className={`resources-diff-line resources-diff-line--${line.type}`} key={`${index}-${line.text}`}><span>{line.type === "addition" ? "+" : line.type === "deletion" ? "−" : " "}</span><code>{line.text}</code></div>)}</div></div> : <div className="resources-empty resources-empty--tall"><strong>No diff loaded</strong><span>Diff stays unavailable until the service returns both exact immutable versions.</span></div>}</section><section className="resources-surface"><SectionHeading eyebrow="Source chain" title="Selected resource provenance" detail="The browser displays evidence returned by the service; it does not infer ownership." />{selectedResource ? <div className="resources-provenance-card"><div className="resources-provenance-card__title"><ResourceIcon kind={selectedResource.kind} /><div><strong>{selectedResource.title}</strong><span>{selectedResource.slug}</span></div><StatusPill tone="neutral">{selectedResource.lifecycle}</StatusPill></div><div className="resources-provenance-grid"><div><span>Source type</span><strong>{selectedResource.provenance.sourceType}</strong></div><div><span>Source id</span><strong className="resources-mono">{selectedResource.provenance.sourceId}</strong></div><div><span>Generation</span><strong>g{selectedResource.provenance.sourceGeneration}</strong></div><div><span>Source revision</span><strong>{selectedResource.provenance.sourceRevision ?? "—"}</strong></div><div className="resources-field-wide"><span>Digest</span><code>{selectedResource.provenance.digest}</code></div></div></div> : <div className="resources-empty"><strong>Select a resource</strong><span>Its provenance chain will be shown here.</span></div>}</section></div>;
}

function AssetsView({ companyId, service, initialAssets }: { companyId?: string; service?: ContentOpsService; initialAssets?: ContentAsset[] }) {
  if (!companyId || !service) return <section className="resources-surface"><SectionHeading eyebrow="Content service" title="Assets unavailable" detail="Asset operations require authenticated company scope and the existing ContentOpsService." /><p className="resources-disabled-note">Read-only: no asset request was made. The host must supply a verified company id and authenticated content service. Content API has no version-history listing or resource-workspace binding endpoint.</p></section>;
  return <ContentOpsPanel companyId={companyId} service={service} initialAssets={initialAssets} />;
}

function WorkspaceNav({ active, onSelect }: { active: WorkspaceTab; onSelect: (tab: WorkspaceTab) => void }) {
  const tabs: Array<{ id: WorkspaceTab; label: string; hint: string }> = [{ id: "overview", label: "Overview", hint: "Profile" }, { id: "resources", label: "Resources", hint: "Prompts · files · MCP" }, { id: "assets", label: "Assets", hint: "Upload · versions" }, { id: "materialization", label: "Materialization", hint: "Plan · sync" }, { id: "evidence", label: "Evidence", hint: "Provenance · diff" }];
  return <nav className="resources-tabs" aria-label="Resource workspace sections">{tabs.map((tab) => <button key={tab.id} type="button" className={active === tab.id ? "resources-tab resources-tab--active" : "resources-tab"} onClick={() => onSelect(tab.id)} aria-current={active === tab.id ? "page" : undefined}><strong>{tab.label}</strong><span>{tab.hint}</span></button>)}</nav>;
}

export function ResourceWorkspace({ service = blockedService, profileId = "", projectId, companyId, contentOpsService, initialAssets, initialTab, initialSnapshot }: ResourceWorkspaceProps) {
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
      setLoadError("Profile id is required to load resources.");
      return;
    }
    if (!service.capabilities.readWorkspace) {
      setLoadError("Resource workspace loading is blocked until the reviewed web facade is wired.");
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
      if (!controller.signal.aborted) setLoadError(blockerMessage(error));
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
      setNotice({ tone: "error", message: blockerMessage(error) });
      return false;
    }).finally(() => setBusy(undefined));
  }

  const blockedWithoutSnapshot = !service.capabilities.readWorkspace && Boolean(profileId);
  if (!snapshot) return <main className="resources-workspace"><div className="resources-page-heading"><div><p className="resources-eyebrow">Agent profile · resources</p><h1>Resource workspace</h1><p className="resources-muted">Loading the authoritative profile snapshot…</p></div><StatusPill tone="warning">{busy === "load" ? "Loading" : "Unavailable"}</StatusPill></div><WorkspaceNav active={activeTab} onSelect={setActiveTab} />{activeTab === "assets" ? <AssetsView companyId={companyId} service={contentOpsService} initialAssets={initialAssets} /> : <><BlockersPanel blockers={RESOURCE_CONTRACT_BLOCKERS} /><div className="resources-empty resources-empty--tall"><strong>{loadError ?? (blockedWithoutSnapshot ? "Resource workspace loading is blocked" : "No workspace snapshot")}</strong><span>{loadError || blockedWithoutSnapshot ? "No data was invented. Wire a reviewed ResourceService to render the profile; Assets remains a separate confirmed Content Service surface." : "Provide a profile id and service boundary to load this workspace."}</span></div></>}</main>;

  const blockers: ResourceContractBlocker[] = snapshot.contract.ready
    ? snapshot.contract.blockers
    : snapshot.contract.blockers.length ? snapshot.contract.blockers : [...RESOURCE_CONTRACT_BLOCKERS];
  const selectedResource = snapshot.resources.find((resource) => resource.id === selectedResourceId);
  // The command handlers below are function declarations, which are hoisted, so the
  // `!snapshot` narrowing above does not reach them; bind the narrowed value once.
  const workspace = snapshot;
  function saveProfile(name: string, description: string) {
    return execute("profile", () => service.updateProfile({ profileId: workspace.profile.id, name, description, expectedRevision: workspace.profile.revision }), (profile) => setSnapshot((current) => current ? updateResourceSnapshot(current, { profile }) : current), "Agent profile updated by the service.");
  }

  function createResource(input: { kind: ResourceKind; slug: string; title: string; description: string; scope: ResourceScope }) {
    return execute("create-resource", () => service.createResource({ profileId: workspace.profile.id, ...input }), (resource) => { setSnapshot((current) => current ? updateResourceSnapshot(current, { resources: [...current.resources, resource] }) : current); setSelectedResourceId(resource.id); setActiveTab("resources"); }, "Resource created and returned by the service.");
  }

  function createVersion(input: { label: string; changeNote: string; payload: ResourcePayload }): Promise<boolean> {
    if (!selectedResource) return Promise.resolve(false);
    return execute("create-version", () => service.createVersion({ resourceId: selectedResource.id, ...input, expectedActiveVersion: selectedResource.activeVersionNumber }), (version) => setSnapshot((current) => current ? updateResourceSnapshot(current, { versions: [...current.versions, version], resources: current.resources.map((resource) => resource.id === version.resourceId ? { ...resource, activeVersionNumber: version.number, versionNumbers: Array.from(new Set([...resource.versionNumbers, version.number])).sort((left, right) => left - right) } : resource) }) : current), "Version created and returned by the service.");
  }

  function bindResource(input: { projectId: string; projectName: string; target: ResourceScope; mode: "local" | "global"; versionNumber: number; workspacePath?: string; childMayEdit: boolean }): Promise<boolean> {
    if (!selectedResource) return Promise.resolve(false);
    return execute("bind-resource", () => service.bindResource({ resourceId: selectedResource.id, ...input }), (binding) => setSnapshot((current) => current ? updateResourceSnapshot(current, { bindings: [...current.bindings, binding], resources: current.resources.map((resource) => resource.id === binding.resourceId ? { ...resource, bindingCount: resource.bindingCount + 1 } : resource) }) : current), "Binding created and returned by the service.");
  }

  function planMaterializationForProject(nextProjectId: string) {
    return execute("plan", () => service.planMaterialization({ profileId: workspace.profile.id, projectId: nextProjectId, expectedGeneration: workspace.materialization?.desiredGeneration }), (plan) => setSnapshot((current) => current ? updateResourceSnapshot(current, { materialization: plan }) : current), "Materialization plan returned by the service.");
  }

  function syncMaterialization() {
    const materialization = workspace.materialization;
    if (!materialization) return Promise.resolve(false);
    return execute("sync", () => service.syncMaterialization({ planId: materialization.id, expectedGeneration: materialization.desiredGeneration }), (plan) => setSnapshot((current) => current ? updateResourceSnapshot(current, { materialization: plan }) : current), "Materialization sync result returned by the service.");
  }

  function loadDiff(fromVersion: number, toVersion: number) {
    if (!selectedResource) return Promise.resolve(false);
    return execute("diff", () => service.loadDiff({ resourceId: selectedResource.id, fromVersion, toVersion }), setDiff, "Version diff returned by the service.");
  }

  return <main className="resources-workspace"><header className="resources-page-heading"><div><p className="resources-eyebrow">Agent profile · resources</p><h1>{snapshot.profile.name}</h1><p className="resources-muted">A focused control surface for prompts, files, MCP, bindings, and workspace materialization.</p></div><div className="resources-heading-meta"><StatusPill tone={snapshot.contract.ready ? "positive" : "danger"}>{snapshot.contract.ready ? "Contract ready" : "Contract blocked"}</StatusPill><span className="resources-revision">{snapshot.profile.revision}</span></div></header>{notice && <div className={`resources-notice resources-notice--${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>{notice.message}</div>}<WorkspaceNav active={activeTab} onSelect={setActiveTab} />{activeTab === "overview" && <ProfileOverview snapshot={{ ...snapshot, contract: { ...snapshot.contract, blockers } }} service={service} busy={busy} onSaveProfile={saveProfile} onSelectTab={setActiveTab} />}{activeTab === "resources" && <><ResourceCatalog snapshot={snapshot} service={service} selectedResourceId={selectedResourceId} onSelectResource={(id) => { setSelectedResourceId(id); setDiff(undefined); }} onOpenDiff={(from, to) => { setActiveTab("evidence"); void loadDiff(from, to); }} onCreateResource={createResource} busy={busy} /><div className="resources-secondary-forms"><VersionEditor resource={selectedResource} versions={snapshot.versions} service={service} busy={busy} onCreateVersion={createVersion} /><BindingForm resource={selectedResource} projects={snapshot.projects} service={service} busy={busy} onBind={bindResource} /></div></>}{activeTab === "assets" && <AssetsView companyId={companyId} service={contentOpsService} initialAssets={initialAssets} />}{activeTab === "materialization" && <MaterializationView snapshot={{ ...snapshot, contract: { ...snapshot.contract, blockers } }} service={service} selectedProjectId={projectId} busy={busy} onPlan={planMaterializationForProject} onSync={syncMaterialization} />}{activeTab === "evidence" && <EvidenceView snapshot={{ ...snapshot, contract: { ...snapshot.contract, blockers } }} selectedResource={selectedResource} diff={diff} onLoadDiff={loadDiff} service={service} busy={busy} />}<div className="resources-bottom-blockers"><BlockersPanel blockers={blockers} /></div><footer className="resources-footer"><span>Evidence is read from the typed service boundary.</span><span>Digests and generations remain visible on every lifecycle surface.</span></footer></main>;
}

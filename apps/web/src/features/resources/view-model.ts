import type { ResourceServiceCapabilities } from "./contracts";
import type { MaterializationPlan, ResourceDiff, ResourceKind, ResourceRecord, ResourceVersion } from "./types";

export type ResourceKindFilter = ResourceKind | "all";

export function resourceKindLabel(kind: ResourceKind): string {
  return kind === "prompt" ? "Prompt" : kind === "file" ? "File" : "MCP server";
}

export function resourceKindGlyph(kind: ResourceKind): string {
  return kind === "prompt" ? "Aa" : kind === "file" ? "⌁" : "↗";
}

export function resourceLifecycleLabel(status: ResourceRecord["lifecycle"]): string {
  return status === "published" ? "Published" : status === "draft" ? "Draft" : status === "retired" ? "Retired" : "Blocked";
}

export function materializationStatusLabel(status: MaterializationPlan["status"]): string {
  return status === "applied" ? "Applied" : status === "applying" ? "Syncing" : status === "planned" ? "Plan ready" : status === "stale" ? "Stale" : status === "failed" ? "Failed" : "Blocked";
}

export function materializationStatusTone(status: MaterializationPlan["status"]): "positive" | "warning" | "danger" | "neutral" {
  if (status === "applied") return "positive";
  if (status === "failed" || status === "blocked") return "danger";
  if (status === "stale" || status === "applying") return "warning";
  return "neutral";
}

export function selectVisibleResources(resources: readonly ResourceRecord[], search: string, kind: ResourceKindFilter): ResourceRecord[] {
  const normalized = search.trim().toLowerCase();
  return resources.filter((resource) => {
    if (kind !== "all" && resource.kind !== kind) return false;
    if (!normalized) return true;
    return [resource.title, resource.slug, resource.description, resource.scope.label, resource.scope.id]
      .filter(Boolean)
      .some((value) => value!.toLowerCase().includes(normalized));
  });
}

export function versionsForResource(versions: readonly ResourceVersion[], resourceId: string): ResourceVersion[] {
  return versions.filter((version) => version.resourceId === resourceId).sort((left, right) => right.number - left.number);
}

export function latestVersionNumber(resource: ResourceRecord, versions: readonly ResourceVersion[]): number | undefined {
  return resource.activeVersionNumber ?? versionsForResource(versions, resource.id)[0]?.number;
}

export function summarizeDiff(diff: ResourceDiff): { additions: number; deletions: number; context: number } {
  return diff.lines.reduce(
    (summary, line) => ({
      additions: summary.additions + (line.type === "addition" ? 1 : 0),
      deletions: summary.deletions + (line.type === "deletion" ? 1 : 0),
      context: summary.context + (line.type === "context" ? 1 : 0),
    }),
    { additions: 0, deletions: 0, context: 0 },
  );
}

export function canPlanMaterialization(capabilities: ResourceServiceCapabilities, projectId?: string): boolean {
  return capabilities.planMaterialization && Boolean(projectId);
}

export function canSyncMaterialization(capabilities: ResourceServiceCapabilities, plan?: MaterializationPlan): boolean {
  return capabilities.syncMaterialization && Boolean(plan) && (plan?.status === "planned" || plan?.status === "stale");
}

export function formatGeneration(desired?: number, applied?: number): string {
  if (desired === undefined) return "—";
  return applied === undefined ? `g${desired} desired · unapplied` : `g${desired} desired · g${applied} applied`;
}

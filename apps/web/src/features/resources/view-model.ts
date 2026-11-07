import type { ResourceServiceCapabilities } from "./contracts";
import type { MaterializationPlan, ResourceDiff, ResourceKind, ResourceRecord, ResourceVersion } from "./types";

export type ResourceKindFilter = ResourceKind | "all";

/**
 * Icons only: the words for a kind, a status or a generation are copy, so they
 * live in `i18n/units/resources.ts` and are asked for by key at the call site.
 */
export function resourceKindGlyph(kind: ResourceKind): string {
  return kind === "prompt" ? "Aa" : kind === "file" ? "⌁" : "↗";
}

/** `active` serves traffic, `draft` is being edited, the other two are non-serving. */
export function resourceLifecycleTone(status: ResourceRecord["lifecycle"]): "positive" | "warning" | "danger" | "neutral" {
  switch (status) {
    case "active": return "positive";
    case "draft": return "warning";
    case "quarantined": return "danger";
    case "archived": return "neutral";
  }
}

export function materializationStatusTone(status: MaterializationPlan["status"]): "positive" | "warning" | "danger" | "neutral" {
  return status === "ready" ? "positive" : status === "blocked" ? "danger" : "neutral";
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
  return capabilities.syncMaterialization && Boolean(plan) && plan?.status === "ready";
}

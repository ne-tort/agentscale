import type { ResourceContractBlocker } from "./types";

/**
 * These blockers mirror the migration evidence available to the web boundary.
 * They are intentionally visible in the product surface until an API adapter
 * supplies an authoritative workspace snapshot and command results.
 */
export const RESOURCE_CONTRACT_BLOCKERS = [
  {
    code: "resource_web_facade_missing",
    severity: "blocking",
    title: "Canonical resource web facade is not confirmed",
    detail: "No reviewed web/API contract is available for canonical profile snapshots, resource versions, bindings, provenance, or materialization. Project-bound first-party module APIs remain a separate data workflow.",
    owner: "api",
  },
  {
    code: "resource_scope_gate_blocked",
    severity: "blocking",
    title: "Canonical resource scope admission is blocked",
    detail: "The canonical resource contract lacks a reviewed tenant and typed project/session resolver. Project-scoped module runtime and Content APIs remain separate workflows and do not satisfy canonical resource authorization.",
    owner: "scope",
  },
  {
    code: "resource_snapshot_adapter_missing",
    severity: "blocking",
    title: "Canonical resource selection snapshot is unavailable",
    detail: "The resource API does not return one trusted binding/version/scope/generation snapshot. Module row reads are independent and must not be treated as canonical materialization inputs.",
    owner: "persistence",
  },
  {
    code: "materialization_apply_adapter_missing",
    severity: "blocking",
    title: "Materialization apply is unavailable",
    detail: "Only an atomic generation-checked apply adapter may perform sync; partial or conflict outcomes must remain non-ready.",
    owner: "persistence",
  },
  {
    code: "resource_promotion_blocked",
    severity: "blocking",
    title: "Promotion gate is closed",
    detail: "Import, positive resolution, and runtime activation remain fail-closed until trusted persistence, CAS, provenance, and review evidence are supplied.",
    owner: "verification",
  },
] as const satisfies readonly ResourceContractBlocker[];

export type ResourceBlockerCode = (typeof RESOURCE_CONTRACT_BLOCKERS)[number]["code"];

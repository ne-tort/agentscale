# ADR — Project Container independence

## Status

Accepted (canon docs). Implementation pending phases P1–P4.

## Context

As-is, «container» is an opaque `object-ws` prefix owned by `ProjectService`. Admin «Bundles» showed starter cabinet zips, confusing operators. Kubernetes has only a PVC probe, not per-project isolation. Product needs a **powerful, isolated** sandbox mechanism with clear hierarchy: Project ↔ Container related, **not** fused.

## Decision

1. Introduce BC **14 Project Containers** with entity `ProjectContainer` and port `ContainerRuntimePort`.
2. **Only** this module writes project-sandbox objects to Kubernetes (`managed-by=container-runtime`).
3. Product cascades: UI/Admin and key/idle suspend go through **Project** lifecycle; Project calls the Port. Never key→k8s, never UI→kubectl.
4. Isolation: dedicated SA + NetworkPolicy (internet egress only) + hydrate from object store; no platform secret mounts.
5. Admin chrome: **Контейнеры** + **Кабинеты** (stub); remove **Бандлы** from admin nav. Starter `cabinet.bundle` remains a Cabinets packaging concern.
6. Transitional `object-ws` is allowed behind the Port until P3 Pod isolator ships; docs must not claim isolator is done.

## Consequences

- New Alembic table / API surfaces in later PRs.
- `container_lifecycle.pause_container` becomes adapter to Port.
- Probe Job stays infra-only.
- Requires explicit «ok» on this ADR before Flutter/API/k8s implementation PRs.

# Project controls

`ProjectControlsPanel` is an independently mountable project detail settings/lifecycle surface under `features/projects/controls/**`. It does not edit or replace the existing `features/projects/overview/**` feature and does not register or change application routes.

## Confirmed API contract

The service is limited to routes present in `apps/api/src/prodavan/api/v1/projects.py`:

- `GET /api/v1/projects/{project_id}` returns the project record, including project lifecycle status and runtime data when available.
- `PATCH /api/v1/projects/{project_id}` accepts the supported project fields `name`, `about`, and `budget_tokens`.
- `POST /api/v1/projects/{project_id}/launch`
- `POST /api/v1/projects/{project_id}/pause`
- `POST /api/v1/projects/{project_id}/resume`
- `POST /api/v1/projects/{project_id}/reload`
- `POST /api/v1/projects/{project_id}/sync`
- `POST /api/v1/projects/{project_id}/complete`

`sync` returns a materialization result (`project_id`, workspace paths and hydrate generation), not a Project DTO. The following project `GET` remains the authoritative status read. Lifecycle DTOs must identify the requested project and report a route-compatible status; GET must agree before a result is verified. An idempotent/in-flight resume may still report `paused`; a verified pending resume also requires runtime observation of its Pod. Reload can return `error`; the UI reports backend status and never infers Pod readiness.

## Lifecycle capabilities

The status matrix below describes the upper bound before intersecting with observed runtime state:

| Project status | Available commands |
| --- | --- |
| `draft` | launch |
| `active` | pause, reload, sync, complete |
| `paused` | resume, sync, complete |
| `error` | reload, complete |
| `completed` | resume |
| `deleted` | none |

The matrix is intersected with the backend-observed runtime state when that state is present, so transitional states (`preparing`, `hydrating`, `provisioning`, `pausing`, and similar) do not inherit commands just from the higher-level lifecycle status. Resume for paused/completed projects is hidden when runtime state is absent or unknown because the route requires an existing Pod.

Disabled commands expose the reason through their accessible label and tooltip. Completing a project requires an inline confirmation.

## What the screen shows first

The page answers "what is this project and what can I do with it": the name, the state
in words, when it last changed, and the lifecycle actions. Everything diagnostic — the raw
runtime value, the environment reference, the project id, the settings form — sits in a
collapsed `<details>` labelled "Дополнительно" / "Advanced". Nothing is removed, so every
control stays reachable, but none of it competes with the actions for attention.

Copy comes from the `projects` i18n unit; state values are translated there rather than
printed as backend enums.

## Host integration

The parent can create the authenticated service and mount the panel without modifying this feature boundary:

```tsx
<ProjectControlsPanel
  projectId={projectId}
  service={createDefaultProjectControlsService(() => tokens?.access_token)}
  initialProject={project}
  links={{ runtime: `${projectPath}/runtime`, chat: `${projectPath}/chat`, files: `${projectPath}/files`, models: `${projectPath}/models` }}
/>
```

Links default to the concrete project routes already declared by the host app (`/runtime`, `/chat`, `/files`, and `/models`) and can be overridden by a host router. The component has no dependency on app context or the router.

## Tests

Service tests cover the exact GET/PATCH and lifecycle routes, auth headers, supported payload fields, backend errors, and malformed success payloads. View-model tests cover the full state/capability matrix, links, and nullable/zero budget semantics. Operations tests cover write/readback ordering, authoritative readback, explicit unverified state, and write failure behavior. The acceptance test covers the summary, the collapsed disclosure, the settings form fields, lifecycle gating, loading state, and links.

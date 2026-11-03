# Project overview

`ProjectsOverview` is isolated under `apps/web/src/features/projects/overview/**`. It does not register a route or modify shared API, app context, pages, or global styles. Its parent must supply the authenticated `ProjectOverviewService` and employee id; this feature deliberately does not reach into shared auth/context.

## Backend contract

The service uses the existing employee API:

- `GET /api/v1/cabinets` -> `{ items: [{ id, name }] }`
- `GET /api/v1/cabinets/{cabinet_id}/projects` -> `{ items: Project[] }`
- `GET /api/v1/cabinets/{cabinet_id}/me/selection` -> `{ project_id }`
- `PUT /api/v1/cabinets/{cabinet_id}/me/selection` with `{ project_id }`
- `POST /api/v1/cabinets/{cabinet_id}/projects` with the minimal `{ name }` body; creation success waits for cabinet-list and project-GET readback
- `POST /api/v1/projects/{project_id}/{launch|pause|resume|reload|complete}`
- `GET /api/v1/projects/{project_id}` for project readback

`createProjectOverviewService` accepts a token getter and fetch implementation. It validates lifecycle values, cabinet ownership, and project identity; duplicate list rows retain the freshest `updated_at`. Selection writes are verified by a selection GET. The overview persists `?cabinet=...` and an employee-scoped cabinet preference, discards late cabinet/project/mutation responses, and clears stale rows after list errors. Lifecycle results are checked against the route DTO, refreshed cabinet list, and project GET. Resume is offered for paused/completed projects only when runtime observation confirms a resumable Pod; an accepted in-progress resume is not presented as Pod readiness. Project links enter runtime, chat, and files directly. Favorites remain a browser-local employee-scoped preference.

## Acceptance coverage

`service.test.ts` covers request paths, auth header, selection payload, create body, lifecycle routes, project mapping, de-duplication, cabinet mismatch, and API errors. `view-model.test.ts` covers filters, status/runtime capabilities, transition result interpretation, and favorites. `ProjectsOverview.test.tsx` covers loading, empty/error cabinets, retry, create/readback mismatch, cabinet deep links, lifecycle states, and runtime/chat/files navigation. Tests are authored but intentionally not run during the migration verification freeze.

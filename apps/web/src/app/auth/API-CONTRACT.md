# Web authentication contract

The web client calls the mounted FastAPI v1 routes below. These are API-owned
contracts; the web auth lifecycle must not invent cookie or `/auth/session`
endpoints.

| Method and path | Request | Success | Expiry / failure |
| --- | --- | --- | --- |
| `GET /api/v1/auth/config` | none | `auth_mode`, `brokers`, `features` | Standard API error |
| `POST /api/v1/auth/login` | `{ "username": string, "password": string }` | Access/refresh/id token fields, `expires_in`, `token_type`, `sub`, `roles`, `email`, `username` | Standard API error, including 401 for rejected credentials |
| `POST /api/v1/auth/refresh` | `{ "refresh_token": string }` | Same token response as login; refresh token is rotated by the identity provider | 401 means the refresh credential is no longer usable; other API/network failures are transient unless contract says otherwise |
| `POST /api/v1/auth/logout` | Optional `refresh_token`, `access_token`, `id_token` | `204 No Content` after revocation/end-session | Local logout must still complete when the network is unavailable |
| `GET /api/v1/me` | `Authorization: Bearer <access_token>` | Current-user JSON below | 401 means the access token is expired/invalid; other failures must not erase locally persisted credentials |

## Current-user response

The live `/me` handler returns a top-level object, not a `{ data: ... }`
envelope. Fields are:

- `active: boolean` and `sub: string` (the authenticated subject).
- `email: string | null`, `roles: string[]`, `contours: string[]`.
- `company: null | { id, name, username, contact_email }`.
- `employee: null | { id, status, email, contact_email, memberships }`, where
  each membership has `company_id`, `company_name`, and `role`.
- `work_context: { cabinet_id: string | null, project_id: string | null }`.

`id`, `subject`, `employee_id`, and `display_name` are not guaranteed as
top-level backend fields. The existing API client normalization projects
`sub` to `subject`, derives an id from the employee id or subject, and derives
`employee_id` from `employee.id`. The auth lifecycle uses employee id when
present and falls back to email/subject for display because `/me` does not
return `display_name`.

The API router mounts `auth.router` and `identity.router` at v1; therefore
`/me` is `/api/v1/me`, not `/api/v1/auth/me`. For a disabled employee, the
`get_current_employee` dependency can reject `/me` with 403 before the handler
returns a body; the lifecycle treats that authenticated-but-forbidden result as
revocation and clears local credentials. A returned `active: false` is handled
the same way. Invalid/expired access or refresh tokens are HTTP 401.

## Browser UX boundaries

The API exposes broker start/callback routes for `vk` and `yandex`, but a
browser redirect returns token material in the callback URL and is not wired
into this web app. External SSO buttons/flows are therefore explicitly
unsupported in the web client until a safe callback handoff is designed. The
available interactive path is username/password login through `/auth/login`.
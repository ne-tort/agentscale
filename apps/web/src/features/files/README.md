# Project Pod files

`ProjectFilesWorkspace` is the live project Pod workspace surface. It reads and mutates files only through the routes implemented in `apps/api/src/prodavan/api/v1/projects.py`:

- `GET /api/v1/projects/:projectId/container/workspace/entries?path=...`
- `GET /api/v1/projects/:projectId/container/workspace/preview?path=...`
- `GET /api/v1/projects/:projectId/container/workspace/content?path=...`
- `PUT /api/v1/projects/:projectId/container/workspace/content?path=...`
- `POST /api/v1/projects/:projectId/container/workspace/mkdir` with `{ "path": "..." }`
- `DELETE /api/v1/projects/:projectId/container/workspace/entries?path=...`
- `POST /api/v1/projects/:projectId/container/workspace/move`
- `POST /api/v1/projects/:projectId/container/workspace/copy`

The feature has no MinIO, object-store, local-storage, or fixture fallback. `POD_NOT_RUNNING`, `PROJECT_PAUSED`, offline transport failures, binary preview failures, and permission errors stay visible to the user. Mutating controls are paused while the live workspace is unavailable.

`createDirectory` calls the authenticated project-scoped mkdir route. Folder names are checked against the live current-directory listing before creation, and the operation is reported as successful only when the route result and a refreshed listing both confirm the requested direct child. Existing files and folders are conflicts; no marker files or alternate storage are used.

The API supports both runtime modes through their workspace adapters: k8s exec runs the in-Pod `workspace_fs mkdir` command, while the runtime HTTP adapter uses `POST /v1/workspace/mkdir` and k8s mode can fall back to exec when an older runtime omits that route. Sandbox mode requires the agent-runtime mkdir endpoint and fails explicitly if it is unavailable.

The route preserves the current folder as `?path=...` and the selected file as `?selected=...`; direct nested file links such as `/projects/:projectId/files/docs/notes.txt` are also accepted and restored on reload. Path and project transitions abort outstanding list/preview/download/destination-check requests and discard late responses. Mutations are only reported as successful after a readback confirms the expected listing or file contents; uploads and file creation check the live directory before writing, upload replacement is explicit, and move/copy checks the destination before asking for destructive confirmation.

The component is intentionally not connected to the shared router, global stylesheet, shared API client, or application context. The consuming project page can construct `createWorkspaceService({ getAccessToken })` and render `ProjectFilesWorkspace` inside the project route.

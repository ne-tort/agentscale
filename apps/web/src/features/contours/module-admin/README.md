# Module administration feature

`ModuleAdminPage` is a standalone platform-admin or company/employee module catalog. It uses `createModuleAdminService` with an optional access-token callback:

```tsx
<ModuleAdminPage mode="admin" service={service} />
<ModuleAdminPage mode="company" companyId={companyId} service={service} />
```

The owner routing task mounts this feature and provides the company context for company mode. This feature deliberately does not modify shared auth/context, routes, or styles.

## Confirmed route map

| Workflow | Route | Authority / response |
|---|---|---|
| Browse platform catalog and companies | `GET /api/v1/admin/modules`, `GET /api/v1/admin/companies` | Platform admin; `{items}` |
| Read platform module, existing company grants and cabinet bindings | `GET /api/v1/admin/modules/{module_id}` | Platform admin; detail includes `company_ids`, `companies`, `cabinet_ids`, `cabinets`, scope/counts |
| Grant/revoke selected companies | `PATCH /api/v1/admin/modules/{module_id}` with `{company_ids: string[]}` | Platform admin only; replace-list operation. Revoke removes that company's cabinet and dependent project bindings |
| Browse modules visible to a company | `GET /api/v1/companies/{company_id}/modules` | Company actor; `{items}` filtered to active grant, all-scope or company-local modules |
| Read company module and current cabinet bindings | `GET /api/v1/companies/{company_id}/modules/{module_id}`, `GET /api/v1/companies/{company_id}/modules/{module_id}/cabinets` | Company actor; module detail and cabinet catalog with `bound` flags |
| Bind/unbind module to company cabinets | `PATCH /api/v1/companies/{company_id}/modules/{module_id}` with `{cabinet_ids: string[]}` | Company actor; replaces company-scope cabinet bindings and returns readback. Removing a cabinet also uninstalls and revokes dependent project bindings |
| Read project bindings | `GET /api/v1/companies/{company_id}/modules/{module_id}/projects` or platform-admin counterpart `/api/v1/admin/modules/{module_id}/projects` | Company actor / platform admin; project, bind kind, child rights and usage |
| Manage project binding | Project routes: `PATCH /api/v1/projects/{project_id}/modules` with `{module_ids: string[]}` to replace the project list, `POST /api/v1/projects/{project_id}/modules/{module_id}/bind` with `{bind_kind?, child_may_edit?}`, or `DELETE /api/v1/projects/{project_id}/modules/{module_id}` | Requires project write access; IDs must already be bound to that project's cabinet. Project rows link to `/projects/{project_id}/settings` (replace-list settings are exposed by the existing Project API client). |

There is no company-scoped grant/revoke route: company owners cannot grant a platform module to themselves. The company list route only returns modules already visible through a selected grant, all-company scope, or company-local ownership; there is no company-authorized endpoint to browse the ungranted platform catalog. The UI explicitly names this limitation and exposes grant changes only in admin mode. Source and write rights are rendered only when returned. Platform module detail does not return an effective source or version; the page renders an explicit API gap. No module status mutation or version mutation route/body field exists, so enable/disable and version controls are not offered. Cabinet module listing provides installed/bound presence and status; there is no separate installation-status or available-template catalog endpoint. Templates are read from the module's `tables` meta document and `templates` cabinet data table when declared.

Company grants and cabinet bindings are replace-list operations. Removing a company grant clears its cabinet and dependent project bindings; removing a cabinet binding uninstalls the module and revokes dependent project binds. The UI confirms these effects, reloads the detail/catalog routes, and verifies the returned grant IDs or cabinet `bound` flags before reporting success. Project bindings are read-only in this feature and link to project settings for their owning lifecycle.

Browser behavior tests cover catalog/search, grant and cabinet bind lifecycle, destructive confirmation, readback, 403 authorization, request failure/retry, and unsupported project/version/status controls. Per migration verification freeze they were not executed; no tests, build, lint, compilation, formatting, or Git checks were run.
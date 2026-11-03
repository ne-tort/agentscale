# Live FastAPI route inventory

Статический inventory live decorators + `include_router`, baseline 2026-11-02. Источник регистрации — `apps/api/src/prodavan/main.py`, `api/v1/router.py`; prefix `/api/v1` и реальные `APIRouter(prefix=...)`. Source file:line указан у каждой строки.

- Main app: **407 registered `/api/v1` operations**, including **4 `/internal/pods/*`** operations mounted in main app under separate Pod-Bridge scopes.
- Additional root probes: **3** (`GET /health`, `/health/live`, `/health/ready`); health handlers also mount under `/api/v1`.
- `main_pod.py` is a separate Pod-only listener/application; it remounts a reduced subset and is not a browser origin. See README for boundary notes.
- Registered route collision: both `projects.router` and `pod_modules.router` declare `GET /api/v1/projects/{project_id}/modules`. The public app includes the OIDC project-settings router first; Starlette dispatches the first full path/method match, then its `PrincipalDep` rejects a Pod Bridge JWT instead of reaching the later bridge handler. `main_pod.py` mounts `pod_modules` without `projects.router`, so the Pod-only listener has no duplicate.
- Success status/response reflects declared decorator and handler return. JSON dicts are dynamic unless a response model is shown. Transport-specific bytes/SSE/multipart notes are in README.

| Family | Registered operations |
|---|---:|
| platform AI model catalog (`admin_ai_models`) | 5 |
| platform cabinet administration (`admin_cabinets`) | 8 |
| platform reference catalogs (`admin_catalogs`) | 4 |
| platform and company administration (`admin_companies`) | 14 |
| platform and company administration (`admin_companies.company_router`) | 10 |
| platform containers/workspace (`admin_containers`) | 15 |
| platform document store operations (`admin_document_store`) | 6 |
| platform metrics (`admin_metrics`) | 3 |
| platform-owned modules (`admin_modules`) | 21 |
| platform object store operations (`admin_object_store`) | 1 |
| platform events (`admin_platform_events`) | 1 |
| platform profile (`admin_profile`) | 3 |
| platform project operations (`admin_projects`) | 3 |
| recycle bin (`admin_recycle`) | 3 |
| platform search index operations (`admin_search_index`) | 9 |
| platform trigger operations (`admin_triggers`) | 2 |
| agent sessions, chat and events (`agent`) | 26 |
| durable agent runs, approvals and event timeline (`agent_runs`) | 7 |
| platform AI keys (`ai_keys`) | 17 |
| authentication (`auth`) | 7 |
| B2B tender compatibility (`b2b`) | 6 |
| employee/company cabinet scope (`cabinets`) | 25 |
| company AI keys (`company_ai_keys`) | 18 |
| company AI models (`company_ai_models`) | 4 |
| company containers/workspace (`company_containers`) | 14 |
| company-owned modules (`company_modules`) | 18 |
| content assets, versions, aliases and ACL (`content`) | 22 |
| Pod document tools (`documents`) | 5 |
| employee project search (`employee_search`) | 1 |
| app-key API, employee webhooks and incoming integrations (`external`) | 21 |
| ops health (`health`) | 3 |
| identity and employees (`identity`) | 10 |
| private Pod credential/workspace bridge (`internal.pods`) | 4 |
| Pod runtime module bridge (`pod_modules`) | 13 |
| employee project scope (`projects`) | 54 |
| employee project scope (`projects.cabinet_projects_router`) | 2 |
| stub (`stub`) | 1 |
| Pod runtime bridge infra (`tenant_infra`) | 21 |

## Registered operations

| Method | Live path | Router owner | Body / media | Query | Required non-body headers | Success / response | Auth boundary | Source |
|---|---|---|---|---|---|---|---|
| GET | /api/v1/health | health | - | - | - | 200 / JSON: dict[str, Any] | public | `apps/api/src/prodavan/api/v1/health.py:27` |
| GET | /api/v1/health/live | health | - | - | - | 200 / JSON: dict[str, str] | public | `apps/api/src/prodavan/api/v1/health.py:33` |
| GET | /api/v1/health/ready | health | - | - | - | 200 / JSON: dict[str, Any] | public | `apps/api/src/prodavan/api/v1/health.py:39` |
| GET | /api/v1/stub | stub | - | - | - | 200 / JSON: dict[str, str] | public | `apps/api/src/prodavan/api/v1/stub.py:9` |
| GET | /api/v1/auth/config | auth | - | - | - | 200 / JSON: dict | public | `apps/api/src/prodavan/api/v1/auth.py:41` |
| GET | /api/v1/auth/health | auth | - | - | - | 200 / JSON: dict | public | `apps/api/src/prodavan/api/v1/auth.py:47` |
| POST | /api/v1/auth/login | auth | LoginBody | - | - | 200 / JSON: dict | public | `apps/api/src/prodavan/api/v1/auth.py:52` |
| POST | /api/v1/auth/refresh | auth | RefreshBody | - | - | 200 / JSON: dict | public | `apps/api/src/prodavan/api/v1/auth.py:58` |
| POST | /api/v1/auth/logout | auth | LogoutBody | - | - | 204 / empty (204) | public | `apps/api/src/prodavan/api/v1/auth.py:64` |
| GET | /api/v1/auth/broker/{idp}/start | auth | - | format='redirect' | - | 200/302 (handler-dependent) / redirect/JSON/bytes; handler-dependent | public | `apps/api/src/prodavan/api/v1/auth.py:74` |
| GET | /api/v1/auth/broker/callback | auth | - | code=None, state=None, error=None, error_description=None | - | 302 (success; error responses vary) / redirect/JSON/bytes; handler-dependent | public | `apps/api/src/prodavan/api/v1/auth.py:88` |
| GET | /api/v1/me | identity | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/identity.py:75` |
| POST | /api/v1/companies | identity | CreateCompanyBody | - | - | 201 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/identity.py:116` |
| POST | /api/v1/companies/{company_id}/employees | identity | CreateEmployeeBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/identity.py:157` |
| POST | /api/v1/employees/{employee_id}/disable | identity | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/identity.py:185` |
| POST | /api/v1/employees/{employee_id}/enable | identity | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/identity.py:227` |
| DELETE | /api/v1/employees/{employee_id} | identity | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/identity.py:269` |
| POST | /api/v1/employees/{employee_id}/restore | identity | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/identity.py:312` |
| POST | /api/v1/session/switch-company | identity | - | company_id | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/identity.py:356` |
| PUT | /api/v1/me/password | identity | MePasswordBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/identity.py:390` |
| PATCH | /api/v1/me/contact-email | identity | MeContactEmailBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/identity.py:404` |
| GET | /api/v1/companies/{company_id}/ai-keys | company_ai_keys | - | - | - | 200 / JSON: list[dict] | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:63` |
| GET | /api/v1/companies/{company_id}/ai-keys/{key_id} | company_ai_keys | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:74` |
| POST | /api/v1/companies/{company_id}/ai-keys | company_ai_keys | CreateCompanyKeyBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:86` |
| PATCH | /api/v1/companies/{company_id}/ai-keys/{key_id} | company_ai_keys | PatchCompanyKeyBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:111` |
| DELETE | /api/v1/companies/{company_id}/ai-keys/{key_id} | company_ai_keys | - | - | - | 204 / empty (204) | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:126` |
| POST | /api/v1/companies/{company_id}/ai-keys/{key_id}/renew | company_ai_keys | RenewBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:140` |
| POST | /api/v1/companies/{company_id}/ai-keys/{key_id}/rotate-secret | company_ai_keys | RotateSecretBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:155` |
| GET | /api/v1/companies/{company_id}/ai-keys/{key_id}/scope-bindings | company_ai_keys | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:178` |
| PUT | /api/v1/companies/{company_id}/ai-keys/{key_id}/scope-bindings | company_ai_keys | KeyScopeBindingsBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:190` |
| GET | /api/v1/companies/{company_id}/ai-keys/{key_id}/models | company_ai_keys | - | - | - | 200 / JSON: list[dict] | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:215` |
| PUT | /api/v1/companies/{company_id}/ai-keys/{key_id}/models | company_ai_keys | KeyModelSelectionBody | - | - | 200 / JSON: list[dict] | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:229` |
| GET | /api/v1/companies/{company_id}/ai-keys/{key_id}/models/live | company_ai_keys | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:250` |
| POST | /api/v1/companies/{company_id}/ai-keys/{key_id}/probe | company_ai_keys | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:264` |
| GET | /api/v1/companies/{company_id}/ai-keys/{key_id}/probe | company_ai_keys | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:288` |
| POST | /api/v1/companies/{company_id}/ai-keys/{key_id}/probe/model | company_ai_keys | CompanyProbeModelBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:311` |
| POST | /api/v1/companies/{company_id}/ai-keys/{key_id}/oauth/xai/device | company_ai_keys | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:335` |
| GET | /api/v1/companies/{company_id}/ai-keys/{key_id}/oauth/xai/device | company_ai_keys | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:351` |
| DELETE | /api/v1/companies/{company_id}/ai-keys/{key_id}/oauth/xai/device | company_ai_keys | - | - | - | 204 / empty (204) | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_keys.py:367` |
| GET | /api/v1/companies/{company_id}/ai-models | company_ai_models | - | - | - | 200 / JSON: list[dict] | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_models.py:57` |
| POST | /api/v1/companies/{company_id}/ai-models | company_ai_models | CreateModelBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_models.py:68` |
| PATCH | /api/v1/companies/{company_id}/ai-models/{model_id} | company_ai_models | PatchModelBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_models.py:94` |
| DELETE | /api/v1/companies/{company_id}/ai-models/{model_id} | company_ai_models | - | - | - | 204 / empty (204) | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_ai_models.py:120` |
| GET | /api/v1/companies/{company_id}/containers | company_containers | - | limit=200 | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:24` |
| GET | /api/v1/companies/{company_id}/containers/{project_id}/metrics | company_containers | - | window='1h' | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:36` |
| GET | /api/v1/companies/{company_id}/containers/{project_id} | company_containers | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:52` |
| POST | /api/v1/companies/{company_id}/containers/{project_id}/pause | company_containers | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:64` |
| POST | /api/v1/companies/{company_id}/containers/{project_id}/resume | company_containers | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:78` |
| POST | /api/v1/companies/{company_id}/containers/{project_id}/reload | company_containers | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:92` |
| GET | /api/v1/companies/{company_id}/containers/{project_id}/workspace/entries | company_containers | - | path='' | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:106` |
| GET | /api/v1/companies/{company_id}/containers/{project_id}/workspace/content | company_containers | - | path | - | 200 / raw Response (binary/redirect; inspect handler for media/headers) | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:125` |
| PUT | /api/v1/companies/{company_id}/containers/{project_id}/workspace/content | company_containers | raw request bytes | path | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:145` |
| GET | /api/v1/companies/{company_id}/containers/{project_id}/workspace/preview | company_containers | - | path | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:166` |
| DELETE | /api/v1/companies/{company_id}/containers/{project_id}/workspace/entries | company_containers | - | path | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:185` |
| POST | /api/v1/companies/{company_id}/containers/{project_id}/workspace/move | company_containers | WorkspaceMoveBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:204` |
| POST | /api/v1/companies/{company_id}/containers/{project_id}/workspace/copy | company_containers | WorkspaceCopyBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:224` |
| DELETE | /api/v1/companies/{company_id}/containers/{project_id} | company_containers | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_containers.py:244` |
| GET | /api/v1/companies/{company_id}/modules | company_modules | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:69` |
| POST | /api/v1/companies/{company_id}/modules | company_modules | CreateCompanyModuleBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:81` |
| GET | /api/v1/companies/{company_id}/modules/{module_id} | company_modules | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:93` |
| PATCH | /api/v1/companies/{company_id}/modules/{module_id} | company_modules | PatchCompanyModuleBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:105` |
| DELETE | /api/v1/companies/{company_id}/modules/{module_id} | company_modules | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:123` |
| POST | /api/v1/companies/{company_id}/modules/{module_id}/copy | company_modules | CopyModuleBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:135` |
| GET | /api/v1/companies/{company_id}/modules/{module_id}/projects | company_modules | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:152` |
| GET | /api/v1/companies/{company_id}/modules/{module_id}/cabinets | company_modules | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:170` |
| GET | /api/v1/companies/{company_id}/modules/{module_id}/meta/documents | company_modules | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:188` |
| GET | /api/v1/companies/{company_id}/modules/{module_id}/meta/documents/{slug} | company_modules | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:201` |
| PUT | /api/v1/companies/{company_id}/modules/{module_id}/meta/documents/{slug} | company_modules | MetaDocumentBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:216` |
| POST | /api/v1/companies/{company_id}/modules/{module_id}/content/upload | company_modules | multipart/form-data: file | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:232` |
| POST | /api/v1/companies/{company_id}/modules/{module_id}/secrets/upload | company_modules | SecretUploadBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:254` |
| POST | /api/v1/companies/{company_id}/modules/{module_id}/actions/{action_id}/invoke | company_modules | ActionInvokeBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:274` |
| GET | /api/v1/companies/{company_id}/modules/{module_id}/data/{table_slug} | company_modules | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:300` |
| POST | /api/v1/companies/{company_id}/modules/{module_id}/data/{table_slug} | company_modules | DataRowBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:320` |
| PATCH | /api/v1/companies/{company_id}/modules/{module_id}/data/{table_slug}/{row_id} | company_modules | DataRowBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:343` |
| DELETE | /api/v1/companies/{company_id}/modules/{module_id}/data/{table_slug}/{row_id} | company_modules | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/company_modules.py:366` |
| GET | /api/v1/content/assets | content | - | company_id, limit=50 | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:101` |
| POST | /api/v1/content/assets | content | CreateAssetBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:118` |
| GET | /api/v1/content/assets/{asset_id} | content | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:136` |
| PATCH | /api/v1/content/assets/{asset_id} | content | PatchAssetBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:148` |
| DELETE | /api/v1/content/assets/{asset_id} | content | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:166` |
| POST | /api/v1/content/assets/{asset_id}/versions | content | BeginVersionBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:178` |
| PUT | /api/v1/content/assets/{asset_id}/versions/{version_id}/upload | content | raw request bytes | - | - | 200 / JSON mutation result | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:195` |
| POST | /api/v1/content/assets/{asset_id}/versions/{version_id}/finalize | content | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:223` |
| GET | /api/v1/content/assets/{asset_id}/download | content | - | blob_version_id, proxy=False | - | 200/302 (handler-dependent) / redirect/JSON/bytes; handler-dependent | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:239` |
| GET | /api/v1/content/aliases | content | - | company_id, limit=50 | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:261` |
| POST | /api/v1/content/aliases | content | CreateAliasBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:278` |
| GET | /api/v1/content/aliases/{alias_id_or_slug} | content | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:297` |
| PATCH | /api/v1/content/aliases/{alias_id} | content | PatchAliasBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:311` |
| DELETE | /api/v1/content/aliases/{alias_id} | content | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:331` |
| POST | /api/v1/content/aliases/{alias_id}/bind | content | BindAliasBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:343` |
| DELETE | /api/v1/content/aliases/{alias_id}/bind | content | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:360` |
| GET | /api/v1/content/aliases/{alias_id}/bindings | content | - | limit=20 | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:372` |
| GET | /api/v1/content/aliases/{slug}/resolve | content | - | - | - | 200/302 (handler-dependent) / redirect/JSON/bytes; handler-dependent | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:387` |
| GET | /api/v1/content/assets/{asset_id}/acl | content | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:400` |
| PUT | /api/v1/content/assets/{asset_id}/acl | content | ReplaceAclBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:416` |
| GET | /api/v1/content/aliases/{alias_id}/acl | content | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:434` |
| PUT | /api/v1/content/aliases/{alias_id}/acl | content | ReplaceAclBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/content.py:450` |
| POST | /api/v1/projects/{project_id}/documents/convert | documents | ConvertBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/documents.py:182` |
| POST | /api/v1/projects/{project_id}/documents/read | documents | ReadBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/documents.py:219` |
| POST | /api/v1/projects/{project_id}/documents/create | documents | CreateBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/documents.py:245` |
| POST | /api/v1/projects/{project_id}/documents/fill-template | documents | FillTemplateBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/documents.py:270` |
| GET | /api/v1/documents/health | documents | - | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/documents.py:303` |
| GET | /api/v1/admin/companies | admin_companies | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:113` |
| GET | /api/v1/admin/companies/{company_id} | admin_companies | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:119` |
| DELETE | /api/v1/admin/companies/{company_id} | admin_companies | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:124` |
| POST | /api/v1/admin/companies/{company_id}/restore | admin_companies | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:133` |
| DELETE | /api/v1/admin/companies/{company_id}/purge | admin_companies | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:144` |
| PATCH | /api/v1/admin/companies/{company_id} | admin_companies | CompanyPatchBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:155` |
| PUT | /api/v1/admin/companies/{company_id}/cabinet-quotas | admin_companies | CabinetQuotaBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:168` |
| PUT | /api/v1/admin/companies/{company_id}/tenant-infra-quotas | admin_companies | TenantInfraQuotaBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:183` |
| GET | /api/v1/admin/companies/{company_id}/agent-policy | admin_companies | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:194` |
| PUT | /api/v1/admin/companies/{company_id}/agent-policy | admin_companies | AgentPolicyBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:199` |
| POST | /api/v1/admin/companies/{company_id}/idle-pause/sweep | admin_companies | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:228` |
| PUT | /api/v1/admin/companies/{company_id}/subscription | admin_companies | SubscriptionBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:239` |
| GET | /api/v1/admin/companies/{company_id}/metrics | admin_companies | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:275` |
| PUT | /api/v1/admin/companies/{company_id}/password | admin_companies | CompanyPasswordBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_companies.py:280` |
| GET | /api/v1/admin/profile | admin_profile | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_profile.py:27` |
| PUT | /api/v1/admin/profile/login | admin_profile | AdminLoginBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_profile.py:32` |
| PUT | /api/v1/admin/profile/password | admin_profile | AdminPasswordBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_profile.py:41` |
| GET | /api/v1/admin/metrics/companies | admin_metrics | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_metrics.py:14` |
| GET | /api/v1/admin/metrics/series | admin_metrics | - | metric=Ellipsis, entity_type=Ellipsis, entity_id=Ellipsis, window='7d' | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_metrics.py:22` |
| POST | /api/v1/admin/metrics/rebuild | admin_metrics | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_metrics.py:41` |
| GET | /api/v1/companies/{company_id}/cabinets | admin_companies.company_router | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/admin_companies.py:295` |
| GET | /api/v1/companies/{company_id}/cabinets/{cabinet_id}/assignments | admin_companies.company_router | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/admin_companies.py:315` |
| POST | /api/v1/companies/{company_id}/cabinets/{cabinet_id}/assignments | admin_companies.company_router | AssignCabinetEmployeeBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/admin_companies.py:333` |
| DELETE | /api/v1/companies/{company_id}/cabinets/{cabinet_id}/assignments/{employee_id} | admin_companies.company_router | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/admin_companies.py:352` |
| GET | /api/v1/companies/{company_id}/employees/{employee_id}/metrics | admin_companies.company_router | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/admin_companies.py:371` |
| GET | /api/v1/companies/{company_id}/employees | admin_companies.company_router | - | include_deleted=False | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/admin_companies.py:396` |
| PUT | /api/v1/companies/{company_id}/employees/{employee_id}/password | admin_companies.company_router | EmployeePasswordBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/admin_companies.py:411` |
| PATCH | /api/v1/companies/{company_id}/employees/{employee_id}/contact-email | admin_companies.company_router | EmployeeContactEmailBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/admin_companies.py:426` |
| GET | /api/v1/companies/{company_id}/summary | admin_companies.company_router | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/admin_companies.py:441` |
| PUT | /api/v1/companies/{company_id}/password | admin_companies.company_router | CompanyPasswordBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/admin_companies.py:452` |
| POST | /api/v1/admin/triggers/drain | admin_triggers | - | max_projects=20, max_per_project=10 | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_triggers.py:18` |
| POST | /api/v1/admin/triggers/idle-pause/sweep | admin_triggers | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_triggers.py:37` |
| GET | /api/v1/admin/platform-events | admin_platform_events | - | company_id, project_id, event_type, limit=50 | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_platform_events.py:14` |
| POST | /api/v1/admin/projects/container-refs/backfill-object-ws | admin_projects | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_projects.py:18` |
| GET | /api/v1/admin/projects/sandbox-k8s | admin_projects | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_projects.py:32` |
| POST | /api/v1/admin/projects/sandbox-k8s/pvc-probe | admin_projects | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_projects.py:38` |
| GET | /api/v1/admin/containers | admin_containers | - | limit=200 | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:21` |
| GET | /api/v1/admin/containers/{project_id} | admin_containers | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:31` |
| POST | /api/v1/admin/containers/{project_id}/pause | admin_containers | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:40` |
| POST | /api/v1/admin/containers/{project_id}/resume | admin_containers | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:49` |
| POST | /api/v1/admin/containers/{project_id}/reload | admin_containers | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:58` |
| GET | /api/v1/admin/containers/{project_id}/workspace/entries | admin_containers | - | path='' | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:67` |
| GET | /api/v1/admin/containers/{project_id}/workspace/content | admin_containers | - | path | - | 200 / raw Response (binary/redirect; inspect handler for media/headers) | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:83` |
| PUT | /api/v1/admin/containers/{project_id}/workspace/content | admin_containers | raw request bytes | path | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:100` |
| GET | /api/v1/admin/containers/{project_id}/workspace/preview | admin_containers | - | path | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:118` |
| DELETE | /api/v1/admin/containers/{project_id}/workspace/entries | admin_containers | - | path | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:134` |
| POST | /api/v1/admin/containers/{project_id}/workspace/move | admin_containers | WorkspaceMoveBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:150` |
| POST | /api/v1/admin/containers/{project_id}/workspace/copy | admin_containers | WorkspaceCopyBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:167` |
| DELETE | /api/v1/admin/containers/{project_id} | admin_containers | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:184` |
| POST | /api/v1/admin/containers/pods/{pod_id}/force-kill | admin_containers | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:194` |
| POST | /api/v1/admin/containers/reconcile | admin_containers | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_containers.py:203` |
| GET | /api/v1/admin/cabinets | admin_cabinets | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_cabinets.py:39` |
| POST | /api/v1/admin/cabinets | admin_cabinets | CreateAdminCabinetBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_cabinets.py:45` |
| POST | /api/v1/admin/cabinets/gc-orphan-schemas | admin_cabinets | - | dry_run=True, limit=50, enqueue=False | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_cabinets.py:58` |
| GET | /api/v1/admin/cabinets/{cabinet_id} | admin_cabinets | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_cabinets.py:79` |
| PATCH | /api/v1/admin/cabinets/{cabinet_id} | admin_cabinets | PatchAdminCabinetBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_cabinets.py:84` |
| DELETE | /api/v1/admin/cabinets/{cabinet_id} | admin_cabinets | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_cabinets.py:103` |
| POST | /api/v1/admin/cabinets/{cabinet_id}/restore | admin_cabinets | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_cabinets.py:112` |
| DELETE | /api/v1/admin/cabinets/{cabinet_id}/purge | admin_cabinets | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_cabinets.py:121` |
| GET | /api/v1/admin/recycle | admin_recycle | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_recycle.py:14` |
| POST | /api/v1/admin/recycle/{entity}/{entity_id}/restore | admin_recycle | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_recycle.py:19` |
| DELETE | /api/v1/admin/recycle/{entity}/{entity_id}/purge | admin_recycle | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_recycle.py:28` |
| GET | /api/v1/admin/modules | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:79` |
| POST | /api/v1/admin/modules | admin_modules | CreateAdminModuleBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:85` |
| GET | /api/v1/admin/modules/{module_id} | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:94` |
| PATCH | /api/v1/admin/modules/{module_id} | admin_modules | PatchAdminModuleBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:99` |
| DELETE | /api/v1/admin/modules/{module_id} | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:114` |
| POST | /api/v1/admin/modules/{module_id}/copy | admin_modules | CopyAdminModuleBody | - | - | 201 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:119` |
| GET | /api/v1/admin/modules/{module_id}/meta/documents | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:129` |
| GET | /api/v1/admin/modules/{module_id}/meta/documents/{slug} | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:135` |
| PUT | /api/v1/admin/modules/{module_id}/meta/documents/{slug} | admin_modules | MetaDocumentBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:145` |
| DELETE | /api/v1/admin/modules/{module_id}/meta/documents/{slug} | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:160` |
| GET | /api/v1/admin/modules/{module_id}/projects | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:171` |
| GET | /api/v1/admin/modules/{module_id}/cabinets | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:184` |
| POST | /api/v1/admin/modules/{module_id}/projects/{project_id} | admin_modules | BindProjectBody \| None | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:197` |
| DELETE | /api/v1/admin/modules/{module_id}/projects/{project_id} | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:217` |
| POST | /api/v1/admin/modules/{module_id}/content/upload | admin_modules | multipart/form-data: file | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:227` |
| POST | /api/v1/admin/modules/{module_id}/secrets/upload | admin_modules | SecretUploadBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:245` |
| POST | /api/v1/admin/modules/{module_id}/actions/{action_id}/invoke | admin_modules | ActionInvokeBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:261` |
| GET | /api/v1/admin/modules/{module_id}/data/{table_slug} | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:284` |
| POST | /api/v1/admin/modules/{module_id}/data/{table_slug} | admin_modules | DataRowBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:300` |
| PATCH | /api/v1/admin/modules/{module_id}/data/{table_slug}/{row_id} | admin_modules | DataRowBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:319` |
| DELETE | /api/v1/admin/modules/{module_id}/data/{table_slug}/{row_id} | admin_modules | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_modules.py:338` |
| POST | /api/v1/admin/object-store/gc-orphan-blobs | admin_object_store | - | dry_run=True, limit=50, scan_limit=500, enqueue=False | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_object_store.py:17` |
| GET | /api/v1/admin/document-store/health | admin_document_store | - | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_document_store.py:60` |
| POST | /api/v1/admin/document-store/upsert | admin_document_store | UpsertBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_document_store.py:73` |
| POST | /api/v1/admin/document-store/get | admin_document_store | FilterBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_document_store.py:98` |
| POST | /api/v1/admin/document-store/find | admin_document_store | FilterBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_document_store.py:118` |
| POST | /api/v1/admin/document-store/delete | admin_document_store | FilterBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_document_store.py:140` |
| POST | /api/v1/admin/document-store/ensure-indexes | admin_document_store | IndexBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_document_store.py:160` |
| POST | /api/v1/admin/search-index/reconcile-equipment | admin_search_index | - | company_id=None | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_search_index.py:100` |
| GET | /api/v1/admin/search-index/health | admin_search_index | - | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_search_index.py:117` |
| POST | /api/v1/admin/search-index/indexes/ensure | admin_search_index | EnsureIndexBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_search_index.py:130` |
| POST | /api/v1/admin/search-index/indexes/delete | admin_search_index | IndexRefBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_search_index.py:155` |
| POST | /api/v1/admin/search-index/documents/index | admin_search_index | IndexDocumentBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_search_index.py:174` |
| POST | /api/v1/admin/search-index/documents/bulk | admin_search_index | BulkDocumentBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_search_index.py:196` |
| POST | /api/v1/admin/search-index/documents/get | admin_search_index | GetDocumentBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_search_index.py:223` |
| POST | /api/v1/admin/search-index/documents/delete | admin_search_index | DeleteDocumentBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_search_index.py:243` |
| POST | /api/v1/admin/search-index/search | admin_search_index | SearchBody | - | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_search_index.py:264` |
| GET | /api/v1/admin/catalogs/{catalog_id}/entries | admin_catalogs | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_catalogs.py:38` |
| POST | /api/v1/admin/catalogs/{catalog_id}/entries | admin_catalogs | CreateEntryBody | - | - | 201 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_catalogs.py:48` |
| PATCH | /api/v1/admin/catalogs/{catalog_id}/entries/{entry_id} | admin_catalogs | PatchEntryBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_catalogs.py:66` |
| DELETE | /api/v1/admin/catalogs/{catalog_id}/entries/{entry_id} | admin_catalogs | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_catalogs.py:81` |
| GET | /api/v1/admin/ai-keys | ai_keys | - | - | - | 200 / JSON: list[dict] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:73` |
| GET | /api/v1/admin/ai-keys/audit-events | ai_keys | - | key_id=None, limit=50 | - | 200 / JSON: list[dict] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:78` |
| POST | /api/v1/admin/ai-keys | ai_keys | CreateKeyBody | - | - | 201 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:88` |
| GET | /api/v1/admin/ai-keys/{key_id} | ai_keys | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:105` |
| PATCH | /api/v1/admin/ai-keys/{key_id} | ai_keys | PatchKeyBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:110` |
| POST | /api/v1/admin/ai-keys/{key_id}/renew | ai_keys | RenewBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:115` |
| POST | /api/v1/admin/ai-keys/{key_id}/rotate-secret | ai_keys | RotateSecretBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:120` |
| PUT | /api/v1/admin/ai-keys/{key_id}/companies | ai_keys | CompaniesBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:125` |
| DELETE | /api/v1/admin/ai-keys/{key_id} | ai_keys | - | - | - | 204 / empty (204) | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:130` |
| POST | /api/v1/admin/ai-keys/{key_id}/probe | ai_keys | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:135` |
| GET | /api/v1/admin/ai-keys/{key_id}/probe | ai_keys | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:148` |
| POST | /api/v1/admin/ai-keys/{key_id}/probe/model | ai_keys | ProbeModelBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:163` |
| GET | /api/v1/admin/ai-keys/{key_id}/models | ai_keys | - | - | - | 200 / JSON: list[dict] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:182` |
| PUT | /api/v1/admin/ai-keys/{key_id}/models | ai_keys | AdminKeyModelSelectionBody | - | - | 200 / JSON: list[dict] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:196` |
| POST | /api/v1/admin/ai-keys/{key_id}/oauth/xai/device | ai_keys | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:219` |
| GET | /api/v1/admin/ai-keys/{key_id}/oauth/xai/device | ai_keys | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:226` |
| DELETE | /api/v1/admin/ai-keys/{key_id}/oauth/xai/device | ai_keys | - | - | - | 204 / empty (204) | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/ai_keys.py:233` |
| GET | /api/v1/admin/ai-models | admin_ai_models | - | - | - | 200 / JSON: list[dict] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_ai_models.py:55` |
| POST | /api/v1/admin/ai-models | admin_ai_models | CreateModelBody | - | - | 201 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_ai_models.py:60` |
| GET | /api/v1/admin/ai-models/{model_id} | admin_ai_models | - | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_ai_models.py:78` |
| PATCH | /api/v1/admin/ai-models/{model_id} | admin_ai_models | PatchModelBody | - | - | 200 / JSON: dict | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_ai_models.py:83` |
| DELETE | /api/v1/admin/ai-models/{model_id} | admin_ai_models | - | - | - | 204 / empty (204) | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/admin_ai_models.py:100` |
| GET | /api/v1/cabinets/{cabinet_id}/me/selection | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:76` |
| PUT | /api/v1/cabinets/{cabinet_id}/me/selection | cabinets | ProjectSelectionBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:92` |
| GET | /api/v1/cabinets/{cabinet_id}/chats/sidebar | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:117` |
| POST | /api/v1/cabinets | cabinets | CreateCabinetBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:133` |
| POST | /api/v1/cabinets/{cabinet_id}/copy | cabinets | CopyCabinetBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:158` |
| GET | /api/v1/cabinets | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:184` |
| GET | /api/v1/cabinets/{cabinet_id}/metrics | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:196` |
| GET | /api/v1/cabinets/{cabinet_id}/metrics/series | cabinets | - | metric='agent_tokens', window='7d' | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:211` |
| POST | /api/v1/cabinets/{cabinet_id}/presence/heartbeat | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:233` |
| GET | /api/v1/cabinets/{cabinet_id} | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:263` |
| PATCH | /api/v1/cabinets/{cabinet_id} | cabinets | RenameCabinetBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:275` |
| POST | /api/v1/cabinets/{cabinet_id}/archive | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:288` |
| DELETE | /api/v1/cabinets/{cabinet_id} | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:300` |
| POST | /api/v1/cabinets/{cabinet_id}/restore | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:312` |
| DELETE | /api/v1/cabinets/{cabinet_id}/purge | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:324` |
| GET | /api/v1/cabinets/{cabinet_id}/modules | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:336` |
| GET | /api/v1/cabinets/{cabinet_id}/modules/{module_id}/bound-projects | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:349` |
| GET | /api/v1/cabinets/{cabinet_id}/modules/{module_id}/meta/documents/{slug} | cabinets | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:389` |
| GET | /api/v1/cabinets/{cabinet_id}/modules/{module_id}/data/{table_slug} | cabinets | - | page=1, page_size=50, search='', in_stock_only=False | X-Prodavan-Session-Id | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:415` |
| POST | /api/v1/cabinets/{cabinet_id}/modules/{module_id}/data/{table_slug} | cabinets | DataRowBody | - | X-Prodavan-Session-Id | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:457` |
| PATCH | /api/v1/cabinets/{cabinet_id}/modules/{module_id}/data/{table_slug}/{row_id} | cabinets | DataRowBody | - | X-Prodavan-Session-Id | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:479` |
| DELETE | /api/v1/cabinets/{cabinet_id}/modules/{module_id}/data/{table_slug}/{row_id} | cabinets | - | - | X-Prodavan-Session-Id | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:503` |
| POST | /api/v1/cabinets/{cabinet_id}/modules/{module_id}/secrets/upload | cabinets | SecretUploadBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:525` |
| POST | /api/v1/cabinets/{cabinet_id}/modules/{module_id}/actions/{action_id}/invoke | cabinets | ActionInvokeBody | - | X-Prodavan-Session-Id | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:544` |
| POST | /api/v1/cabinets/{cabinet_id}/content/upload | cabinets | multipart/form-data: file | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/cabinets.py:568` |
| POST | /api/v1/cabinets/{cabinet_id}/projects | projects.cabinet_projects_router | CreateProjectBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:104` |
| GET | /api/v1/cabinets/{cabinet_id}/projects | projects.cabinet_projects_router | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:123` |
| GET | /api/v1/projects/{project_id}/metrics | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:136` |
| GET | /api/v1/projects/{project_id} | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:153` |
| GET | /api/v1/projects/{project_id}/container | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:163` |
| GET | /api/v1/projects/{project_id}/container/metrics | projects | - | window='1h' | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:175` |
| POST | /api/v1/projects/{project_id}/reload | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:191` |
| GET | /api/v1/projects/{project_id}/container/workspace/entries | projects | - | path='' | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:203` |
| GET | /api/v1/projects/{project_id}/container/workspace/content | projects | - | path | - | 200 / raw Response (binary/redirect; inspect handler for media/headers) | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:219` |
| PUT | /api/v1/projects/{project_id}/container/workspace/content | projects | raw request bytes | path | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:236` |
| GET | /api/v1/projects/{project_id}/container/workspace/preview | projects | - | path | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:255` |
| DELETE | /api/v1/projects/{project_id}/container/workspace/entries | projects | - | path | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:271` |
| POST | /api/v1/projects/{project_id}/container/workspace/move | projects | WorkspaceMoveBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:287` |
| POST | /api/v1/projects/{project_id}/container/workspace/copy | projects | WorkspaceCopyBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:304` |
| PATCH | /api/v1/projects/{project_id} | projects | PatchProjectBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:321` |
| GET | /api/v1/projects/{project_id}/modules | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:348` |
| GET | /api/v1/projects/{project_id}/modules/{module_id} | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:368` |
| PATCH | /api/v1/projects/{project_id}/modules/{module_id}/profile | projects | ProjectModuleProfileBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:386` |
| PATCH | /api/v1/projects/{project_id}/modules | projects | PatchProjectModulesBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:406` |
| POST | /api/v1/projects/{project_id}/modules/{module_id}/bind | projects | BindProjectModuleBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:433` |
| DELETE | /api/v1/projects/{project_id}/modules/{module_id} | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:482` |
| GET | /api/v1/projects/{project_id}/mcp-aliases | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:523` |
| GET | /api/v1/projects/{project_id}/runtime-modules | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:540` |
| GET | /api/v1/projects/{project_id}/runtime-modules/{module_id}/meta/documents/{slug} | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:557` |
| GET | /api/v1/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug} | projects | - | chats=None, page=1, page_size=50, search='', in_stock_only=False | X-Prodavan-Session-Id | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:579` |
| POST | /api/v1/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug} | projects | ProjectRuntimeDataBody | - | X-Prodavan-Session-Id | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:642` |
| PATCH | /api/v1/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug}/{row_id} | projects | ProjectRuntimeDataBody | - | X-Prodavan-Session-Id | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:668` |
| DELETE | /api/v1/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug}/{row_id} | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:696` |
| GET | /api/v1/projects/{project_id}/ai-keys/available | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:720` |
| GET | /api/v1/projects/{project_id}/models/live | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:737` |
| POST | /api/v1/projects/{project_id}/launch | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:760` |
| POST | /api/v1/projects/{project_id}/materialize | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:772` |
| POST | /api/v1/projects/{project_id}/sync | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:784` |
| POST | /api/v1/projects/{project_id}/dismiss-workspace-outdated | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:796` |
| POST | /api/v1/projects/{project_id}/agent/reset | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:808` |
| POST | /api/v1/projects/{project_id}/pause | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:838` |
| POST | /api/v1/projects/{project_id}/resume | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:848` |
| POST | /api/v1/projects/{project_id}/complete | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:858` |
| PUT | /api/v1/projects/{project_id}/visibility | projects | VisibilityBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:872` |
| POST | /api/v1/projects/{project_id}/assignments | projects | AssignmentBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:888` |
| DELETE | /api/v1/projects/{project_id}/assignments/{employee_id} | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:904` |
| DELETE | /api/v1/projects/{project_id} | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:920` |
| POST | /api/v1/projects/{project_id}/restore | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:930` |
| DELETE | /api/v1/projects/{project_id}/purge | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:942` |
| POST | /api/v1/projects/{project_id}/triggers | projects | TriggerBody | - | - | 202 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:954` |
| POST | /api/v1/projects/{project_id}/schedules | projects | ScheduleBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:973` |
| GET | /api/v1/projects/{project_id}/schedules | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:992` |
| PATCH | /api/v1/projects/{project_id}/schedules/{schedule_id} | projects | SchedulePatchBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:1005` |
| DELETE | /api/v1/projects/{project_id}/schedules/{schedule_id} | projects | - | - | - | 204 / empty (204) | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:1024` |
| GET | /api/v1/projects/{project_id}/triggers | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:1039` |
| GET | /api/v1/projects/{project_id}/attachments | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:1053` |
| POST | /api/v1/projects/{project_id}/attachments | projects | AttachmentBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:1066` |
| DELETE | /api/v1/projects/{project_id}/attachments/{attachment_id} | projects | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:1084` |
| GET | /api/v1/projects/{project_id}/attachments/{attachment_id}/content | projects | - | - | - | 200 / raw Response (binary/redirect; inspect handler for media/headers) | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/projects.py:1100` |
| POST | /api/v1/projects/{project_id}/webhooks/http | projects | raw request bytes | - | X-Prodavan-Signature | 202 / JSON: dict | HMAC X-Prodavan-Signature; rate limited + active project/secret | `apps/api/src/prodavan/api/v1/projects.py:1122` |
| POST | /api/v1/projects/{project_id}/webhooks/telegram | projects | raw request bytes | - | X-Prodavan-Signature | 202 / JSON: dict | HMAC X-Prodavan-Signature; rate limited + active project/secret | `apps/api/src/prodavan/api/v1/projects.py:1145` |
| GET | /api/v1/projects/{project_id}/infra/cache/{key:path} | tenant_infra | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:70` |
| PUT | /api/v1/projects/{project_id}/infra/cache/{key:path} | tenant_infra | CacheSetBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:82` |
| DELETE | /api/v1/projects/{project_id}/infra/cache/{key:path} | tenant_infra | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:100` |
| POST | /api/v1/projects/{project_id}/infra/cache/{key:path}/incr | tenant_infra | CacheIncrBody \| None | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:112` |
| PUT | /api/v1/projects/{project_id}/infra/docs/{collection}/{doc_id} | tenant_infra | DocUpsertBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:130` |
| GET | /api/v1/projects/{project_id}/infra/docs/{collection}/{doc_id} | tenant_infra | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:149` |
| GET | /api/v1/projects/{project_id}/infra/docs/{collection} | tenant_infra | - | limit=50, skip=0 | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:166` |
| DELETE | /api/v1/projects/{project_id}/infra/docs/{collection}/{doc_id} | tenant_infra | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:185` |
| POST | /api/v1/projects/{project_id}/infra/userdb/tables/{table} | tenant_infra | UserDbCreateTableBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:202` |
| GET | /api/v1/projects/{project_id}/infra/userdb/tables | tenant_infra | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:219` |
| POST | /api/v1/projects/{project_id}/infra/userdb/tables/{table}/rows | tenant_infra | UserDbInsertBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:230` |
| GET | /api/v1/projects/{project_id}/infra/userdb/tables/{table}/rows | tenant_infra | - | limit=50 | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:247` |
| DELETE | /api/v1/projects/{project_id}/infra/userdb/tables/{table} | tenant_infra | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:264` |
| POST | /api/v1/projects/{project_id}/infra/events | tenant_infra | EventPublishBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:276` |
| GET | /api/v1/projects/{project_id}/infra/events | tenant_infra | - | consumer='default', limit=50, commit=True | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:292` |
| PUT | /api/v1/projects/{project_id}/infra/objects | tenant_infra | ObjectPutBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:311` |
| GET | /api/v1/projects/{project_id}/infra/objects | tenant_infra | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:338` |
| GET | /api/v1/projects/{project_id}/infra/objects/{asset_id} | tenant_infra | - | - | - | 200 / raw Response (binary/redirect; inspect handler for media/headers) | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:347` |
| DELETE | /api/v1/projects/{project_id}/infra/objects/{asset_id} | tenant_infra | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:363` |
| GET | /api/v1/projects/{project_id}/infra/search/health | tenant_infra | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:375` |
| POST | /api/v1/projects/{project_id}/infra/search/query | tenant_infra | SearchQueryBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/tenant_infra.py:383` |
| POST | /api/v1/projects/{project_id}/search/query | employee_search | EmployeeSearchQueryBody | - | - | 200 / JSON: dict[str, Any] | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/employee_search.py:32` |
| GET | /api/v1/projects/{project_id}/modules | pod_modules | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:52` |
| GET | /api/v1/projects/{project_id}/modules/{module_id}/meta/documents | pod_modules | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:64` |
| GET | /api/v1/projects/{project_id}/modules/{module_id}/meta/documents/{slug} | pod_modules | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:79` |
| PUT | /api/v1/projects/{project_id}/modules/{module_id}/meta/documents/{slug} | pod_modules | PodModuleMetaBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:95` |
| GET | /api/v1/projects/{project_id}/modules/{module_id}/data/{table_slug} | pod_modules | - | - | X-Prodavan-Session-Id | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:113` |
| POST | /api/v1/projects/{project_id}/modules/{module_id}/data/{table_slug} | pod_modules | PodModuleDataBody | - | X-Prodavan-Session-Id | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:132` |
| PATCH | /api/v1/projects/{project_id}/modules/{module_id}/data/{table_slug}/{row_id} | pod_modules | PodModuleDataBody | - | X-Prodavan-Session-Id | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:152` |
| DELETE | /api/v1/projects/{project_id}/modules/{module_id}/data/{table_slug}/{row_id} | pod_modules | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:174` |
| POST | /api/v1/projects/{project_id}/modules/{module_id}/actions/{action_id}/invoke | pod_modules | PodModuleActionBody \| None | - | X-Prodavan-Session-Id | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:192` |
| GET | /api/v1/projects/{project_id}/modules/mod_equipment/equipment/catalog-sources | pod_modules | - | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:213` |
| POST | /api/v1/projects/{project_id}/modules/mod_equipment/equipment/catalog-search | pod_modules | EquipmentCatalogSearchBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:224` |
| POST | /api/v1/projects/{project_id}/web-search | pod_modules | WebSearchBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:251` |
| POST | /api/v1/projects/{project_id}/web-fetch | pod_modules | WebFetchBody | - | - | 200 / JSON: dict[str, Any] | scoped Pod-Bridge JWT; project/pod and route scopes | `apps/api/src/prodavan/api/v1/pod_modules.py:284` |
| GET | /api/v1/projects/{project_id}/chat-error-policy | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:109` |
| PUT | /api/v1/projects/{project_id}/chat-error-policy | agent | ChatErrorPolicyBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:129` |
| POST | /api/v1/projects/{project_id}/agent/sessions | agent | CreateSessionBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:156` |
| GET | /api/v1/projects/{project_id}/agent/sessions | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:175` |
| GET | /api/v1/projects/{project_id}/agent/sessions/{session_id} | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:188` |
| PATCH | /api/v1/projects/{project_id}/agent/sessions/{session_id} | agent | PatchSessionBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:215` |
| DELETE | /api/v1/projects/{project_id}/agent/sessions/{session_id} | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:236` |
| GET | /api/v1/projects/{project_id}/agent/sessions/{session_id}/composer-draft | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:259` |
| PUT | /api/v1/projects/{project_id}/agent/sessions/{session_id}/composer-draft | agent | ComposerDraftBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:277` |
| DELETE | /api/v1/projects/{project_id}/agent/sessions/{session_id}/composer-draft | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:297` |
| GET | /api/v1/projects/{project_id}/composer-draft | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:315` |
| PUT | /api/v1/projects/{project_id}/composer-draft | agent | ComposerDraftBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:331` |
| DELETE | /api/v1/projects/{project_id}/composer-draft | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:352` |
| POST | /api/v1/projects/{project_id}/chat | agent | ChatTurnBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:368` |
| POST | /api/v1/projects/{project_id}/chat/stream | agent | ChatTurnBody | - | - | 200 / SSE: text/event-stream | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:388` |
| GET | /api/v1/projects/{project_id}/chat/transcript | agent | - | session_id, limit=500, before_seq=None, after_seq=None | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:453` |
| POST | /api/v1/projects/{project_id}/agent/sessions/{session_id}/send | agent | SendMessageBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:485` |
| GET | /api/v1/projects/{project_id}/agent/sessions/{session_id}/events | agent | - | - | - | 200 / JSON: dict | route-specific OIDC or scoped Pod-Bridge JWT; inspect AgentAuth handler | `apps/api/src/prodavan/api/v1/agent.py:505` |
| POST | /api/v1/projects/{project_id}/agent/sessions/{session_id}/events | agent | AppendAgentEventBody | - | - | 201 / JSON: dict | route-specific OIDC or scoped Pod-Bridge JWT; inspect AgentAuth handler | `apps/api/src/prodavan/api/v1/agent.py:528` |
| GET | /api/v1/projects/{project_id}/agent/sessions/{session_id}/transcript | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:555` |
| POST | /api/v1/projects/{project_id}/agent/sessions/{session_id}/cancel | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:571` |
| GET | /api/v1/projects/{project_id}/agent/sessions/{session_id}/pending-approvals | agent | - | - | - | 200 / JSON: dict | route-specific OIDC or scoped Pod-Bridge JWT; inspect AgentAuth handler | `apps/api/src/prodavan/api/v1/agent.py:587` |
| POST | /api/v1/projects/{project_id}/agent/sessions/{session_id}/tool-approvals | agent | ToolApprovalBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:610` |
| POST | /api/v1/projects/{project_id}/agent/sessions/{session_id}/fork | agent | - | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:629` |
| GET | /api/v1/projects/{project_id}/agent/sessions/{session_id}/sidechains/{tool_use_id}/transcript | agent | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:647` |
| POST | /api/v1/projects/{project_id}/triggers/dispatch | agent | - | max=1 | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent.py:665` |
| GET | /api/v1/projects/{project_id}/agent/runs | agent_runs | - | limit=50, offset=0 | - | 200 / JSON: AgentRunList | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent_runs.py:175` |
| POST | /api/v1/projects/{project_id}/agent/runs | agent_runs | CreateAgentRunBody | - | Idempotency-Key | 201 / JSON: AgentRunCreated | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent_runs.py:244` |
| GET | /api/v1/projects/{project_id}/agent/runs/{run_id} | agent_runs | - | - | - | 200 / JSON: AgentRunRecord | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent_runs.py:298` |
| POST | /api/v1/projects/{project_id}/agent/runs/{run_id}/cancel | agent_runs | CancelAgentRunBody | - | Idempotency-Key | 200 / JSON: AgentRunCancelled | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent_runs.py:323` |
| GET | /api/v1/agent/approvals/inbox | agent_runs | - | project_id, limit=50 | - | 200 / JSON: ApprovalInbox | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent_runs.py:387` |
| POST | /api/v1/projects/{project_id}/agent/approvals/{approval_id}/decision | agent_runs | ApprovalDecisionBody | - | Idempotency-Key | 200 / JSON: ApprovalDecisionResult | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent_runs.py:486` |
| GET | /api/v1/projects/{project_id}/agent/runs/{run_id}/events | agent_runs | - | limit=100, aggregate_type, aggregate_id, after_sequence | - | 200 / JSON: EventTimeline | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/agent_runs.py:579` |
| GET | /api/v1/b2b/projects/{project_id}/tenders | b2b | - | lifecycle_status, work_status, category, q='', sort='published_at', order='desc', page=1, page_size=50 | - | 200 / JSON: dict[str, Any] | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/b2b.py:42` |
| GET | /api/v1/b2b/projects/{project_id}/tenders/{tender_id} | b2b | - | - | - | 200 / JSON: dict[str, Any] | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/b2b.py:95` |
| PATCH | /api/v1/b2b/projects/{project_id}/tenders/{tender_id} | b2b | TenderPatchBody | - | - | 200 / JSON: dict[str, Any] | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/b2b.py:116` |
| DELETE | /api/v1/b2b/projects/{project_id}/tenders/{tender_id} | b2b | - | - | - | 200 / JSON: dict[str, Any] | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/b2b.py:139` |
| POST | /api/v1/b2b/projects/{project_id}/tenders/{tender_id}/take-to-work | b2b | TakeToWorkBody \| None | - | - | 200 / JSON: dict[str, Any] | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/b2b.py:157` |
| POST | /api/v1/b2b/import | b2b | - | project_id, force=False | - | 200 / JSON: dict[str, Any] | Bearer + platform.admin | `apps/api/src/prodavan/api/v1/b2b.py:174` |
| GET | /api/v1/external/cabinets | external | - | - | - | 200 / JSON: dict[str, Any] | Bearer app key; scope + company/cabinet/project ownership | `apps/api/src/prodavan/api/v1/external.py:119` |
| GET | /api/v1/external/cabinets/{cabinet_id}/projects | external | - | - | - | 200 / JSON: dict[str, Any] | Bearer app key; scope + company/cabinet/project ownership | `apps/api/src/prodavan/api/v1/external.py:145` |
| GET | /api/v1/external/projects/{project_id}/chats | external | - | - | - | 200 / JSON: dict[str, Any] | Bearer app key; scope + company/cabinet/project ownership | `apps/api/src/prodavan/api/v1/external.py:165` |
| POST | /api/v1/external/projects/{project_id}/chats | external | CreateChatBody | - | - | 201 / JSON: dict[str, Any] | Bearer app key; scope + company/cabinet/project ownership | `apps/api/src/prodavan/api/v1/external.py:198` |
| GET | /api/v1/external/chats/{session_id} | external | - | - | - | 200 / JSON: dict[str, Any] | Bearer app key; scope + company/cabinet/project ownership | `apps/api/src/prodavan/api/v1/external.py:216` |
| POST | /api/v1/external/chats/{session_id}/messages | external | SendMessageBody | - | - | 201 / JSON: dict[str, Any] | Bearer app key; scope + company/cabinet/project ownership | `apps/api/src/prodavan/api/v1/external.py:242` |
| GET | /api/v1/external/chats/{session_id}/messages | external | - | after_seq, limit=200 | - | 200 / JSON: dict[str, Any] | Bearer app key; scope + company/cabinet/project ownership | `apps/api/src/prodavan/api/v1/external.py:285` |
| GET | /api/v1/external/chats/{session_id}/answer | external | - | - | - | 200 / JSON: dict[str, Any] | Bearer app key; scope + company/cabinet/project ownership | `apps/api/src/prodavan/api/v1/external.py:309` |
| POST | /api/v1/external/projects/{project_id}/tenders | external | ExternalTenderBody | - | - | 201 / JSON: dict[str, Any] | Bearer app key; scope + company/cabinet/project ownership | `apps/api/src/prodavan/api/v1/external.py:452` |
| POST | /api/v1/external/app-keys | external | CreateAppKeyBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:679` |
| GET | /api/v1/external/app-keys | external | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:714` |
| DELETE | /api/v1/external/app-keys/{key_id} | external | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:741` |
| POST | /api/v1/external/webhooks | external | CreateWebhookBody | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:769` |
| GET | /api/v1/external/webhooks | external | - | project_id | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:795` |
| GET | /api/v1/external/webhook-deliveries | external | - | limit=100 | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:821` |
| POST | /api/v1/external/webhook-deliveries/{delivery_id}/retry | external | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:853` |
| PATCH | /api/v1/external/webhooks/{subscription_id} | external | UpdateWebhookBody | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:874` |
| DELETE | /api/v1/external/webhooks/{subscription_id} | external | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:893` |
| GET | /api/v1/external/webhooks/incoming | external | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:909` |
| POST | /api/v1/external/webhooks/incoming/{channel}/secret | external | - | - | - | 201 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:923` |
| DELETE | /api/v1/external/webhooks/incoming/{channel}/secret | external | - | - | - | 200 / JSON: dict | Bearer OIDC principal; role/tenant policy varies | `apps/api/src/prodavan/api/v1/external.py:941` |
| GET | /api/v1/internal/pods/{pod_id}/workspace-archive | internal.pods | - | - | - | 200 / raw Response (binary/redirect; inspect handler for media/headers) | scoped Pod-Bridge JWT; internal credential/hydrate scope | `apps/api/src/prodavan/api/internal/pods.py:55` |
| GET | /api/v1/internal/pods/{pod_id}/credentials | internal.pods | - | - | - | 200 / JSON: dict | scoped Pod-Bridge JWT; internal credential/hydrate scope | `apps/api/src/prodavan/api/internal/pods.py:102` |
| POST | /api/v1/internal/pods/{pod_id}/credentials/{key_id}/lease | internal.pods | LeaseBody | - | - | 201 / JSON: dict | scoped Pod-Bridge JWT; internal credential/hydrate scope | `apps/api/src/prodavan/api/internal/pods.py:126` |
| DELETE | /api/v1/internal/pods/{pod_id}/credentials/leases/{lease_id} | internal.pods | - | - | - | 204 / empty (204) | scoped Pod-Bridge JWT; internal credential/hydrate scope | `apps/api/src/prodavan/api/internal/pods.py:146` |

## Root health registrations

| Method | Path | Response | Source |
|---|---|---|---|
| GET | `/health` | JSON (same handler as `/api/v1/health`) | `apps/api/src/prodavan/api/v1/health.py:27` |
| GET | `/health/live` | JSON (same handler as `/api/v1/health/live`) | `apps/api/src/prodavan/api/v1/health.py:33` |
| GET | `/health/ready` | JSON (same handler as `/api/v1/health/ready`) | `apps/api/src/prodavan/api/v1/health.py:39` |

## Parsing notes

- Source-based static extraction of route decorators and registration calls. Dependency arguments are not request inputs; auth is summarized in its own column.
- Query defaults are recorded where declared. Pydantic request models are named, not expanded; inspect source schemas for exact fields.
- Response body envelopes are not inferred for dynamic dict returns. Review handler/service for exact shape.

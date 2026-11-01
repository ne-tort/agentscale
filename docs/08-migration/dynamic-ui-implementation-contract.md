# Dynamic UI Implementation Contract

Статус: implementation contract для React/TypeScript migration stage.

Этот документ фиксирует границы реализации dynamic UI в `apps/web`. Он не добавляет новых
backend capabilities сам по себе: любая capability должна иметь отдельный typed backend
contract, policy definition, audit event и владельца. Declarative app является default;
произвольный JavaScript разрешён только в code-plugin runtime.

## 1. Scope and invariants

Dynamic UI состоит из трёх доверенных зон:

```text
platform shell (apps/web)
  -> app registry + schema validator + capability broker
  -> declarative renderer OR isolated code-plugin iframe
  -> capability gateway
  -> typed domain adapter + ACL/RLS/quota/audit
```

Обязательные инварианты:

- Shell остаётся единственным владельцем auth session, tenant/cabinet/project/session
  context, маршрутизации и navigation host.
- Declarative app получает только валидированный schema snapshot и typed data/capability
  responses. Она не исполняет JavaScript из manifest.
- Code plugin никогда не исполняется в React shell, не получает shell DOM, cookies,
  `localStorage`, raw access token или произвольный network access.
- Manifest содержит только requested contract. Он не является grant.
- Gateway повторно проверяет context, grant, resource ownership, schema, quota и policy
  на каждом вызове, включая replay и прямой вызов endpoint.
- Dynamic UI не получает generic SQL, Redis, Kafka, MongoDB, OpenSearch, MinIO или
  Kubernetes API.
- Все dynamic artifacts и manifests immutable по digest; публикация, disable, rollback и
  kill switch являются серверными операциями с audit trail.

## 2. `apps/web` responsibilities

### 2.1 Shell boundary

В `apps/web` должны быть отдельные модули (названия могут быть адаптированы к фактической
структуре):

```text
src/dynamic/
  registry.ts          // registry snapshot and route resolution
  schema.ts            // runtime validation and discriminated unions
  declarative/         // allowlisted component renderers
  broker.ts            // shell-side capability broker
  plugin-host.ts       // iframe lifecycle and postMessage RPC
  protocol.ts          // versioned envelopes and correlation state
  policy.ts            // client-side visibility only; server remains authoritative
```

Shell-side policy is a UX guard, not an authorization boundary. It may hide a route or
disable a command from the effective grant, but a plugin or browser client must be assumed
able to call the gateway directly. Network responses are validated at the boundary before
entering React state.

Each request receives an immutable context object; no mutable singleton headers:

```ts
type RequestContext = {
  tenantId: string;
  cabinetId?: string;
  projectId?: string;
  sessionId?: string;
  traceId: string;
};
```

A route must declare which context dimensions it requires. The shell must reject a route
that tries to silently infer `projectId` or `sessionId` from an unrelated selected item.

### 2.2 Registry lookup

The shell resolves an app by `(appId, version, route)` from a signed registry snapshot. The
snapshot is fetched through the normal authenticated API and cached only for its declared
ETag/TTL. A route cannot supply an arbitrary URL or version. Registry entries are filtered
by tenant, cabinet and effective grant before navigation.

Unknown app type, unsupported schema version, invalid digest, missing signature status or
unknown route produces an explicit error state and an audit-safe diagnostic; it must not
fall back to a privileged hardcoded screen.

## 3. Declarative app contract

### 3.1 Immutable manifest

The persisted manifest is the requested contract. It is content-addressed and validated
before publication:

```json
{
  "kind": "declarative-app",
  "schema_version": "1.0",
  "app_id": "equipment-review",
  "version": "1.2.0",
  "artifact_digest": "sha256:...",
  "routes": [
    { "path": "/equipment/review", "screen": "review" }
  ],
  "requested_capabilities": [
    { "id": "equipment.requests.read", "version": "1" },
    { "id": "equipment.offers.read", "version": "1" },
    { "id": "equipment.offers.select", "version": "1" }
  ],
  "limits": {
    "requests_per_minute": 120,
    "max_concurrent_queries": 4,
    "max_response_bytes": 1048576
  },
  "data_policy": {
    "project_scope": "current",
    "session_scope": "optional"
  },
  "screens": [
    {
      "id": "review",
      "root": {
        "type": "stack",
        "children": [
          { "type": "table", "query": "request-lines" },
          { "type": "action-bar", "actions": ["select-offer"] }
        ]
      }
    }
  ]
}
```

The exact storage DTO may contain metadata, labels and localization references, but the
following fields are mandatory and immutable for a published version: `kind`,
`schema_version`, `app_id`, `version`, `artifact_digest`, routes, requested capabilities,
limits, data policy and screen tree.

### 3.2 Schema rules

The schema is a closed discriminated union. Every node has a `type`; unknown types,
unknown action kinds, undeclared query names, recursive references, duplicate IDs and
extra fields are rejected at validation time. The initial allowlist should be small:

- layout: `stack`, `split`, `tabs`, `panel`;
- display: `text`, `badge`, `markdown`, `empty`, `error`;
- data: `table`, `detail`, `form`, `pagination`, `query-state`;
- interaction: `button`, `action-bar`, `link`, `confirm`;
- shell integration: `file-download`, `chat-link`.

A declarative node references a named query or action declared in the same manifest. It
cannot embed a URL, JavaScript expression, SQL fragment, arbitrary CSS/HTML, network
request, component import or callback body. Values use typed bindings (for example
`row.field`, `context.projectId`, or a server-issued value), never expression evaluation.

Query and action declarations include input/output schema references, required capability
IDs, scope requirements, server-side limits and idempotency semantics. The renderer must
pass only schema-validated inputs and must validate the response before rendering.

### 3.3 Data and command boundary

The frontend uses typed domain operations, for example:

```text
module.rows.list   { table, filter, page, project_id, session_id }
module.row.patch   { table, row_id, patch, idempotency_key }
equipment.offers.select { offer_id, request_line_id, idempotency_key }
files.download     { file_ref }
```

These are examples of capability-backed operations, not generic endpoints. Server policy
resolves the effective owner and scope. The browser must not send a caller-selected
connection string, database/table name outside a registered resource, or an unbounded
filter/order expression. Pagination, row count, bytes and query cost are bounded by the
server even when the client declares smaller limits.

`file_ref` and download responses are typed metadata plus a short-lived download action;
raw object-store URLs or credentials are not stored in app state. Secret values are never
returned to a declarative app; a form may receive a `secret_ref` status and invoke a
purpose-specific rotate/test action.

## 4. Code-plugin iframe contract

### 4.1 Artifact and origin

A code plugin is uploaded as source but executed only as a built, scanned and signed
artifact. The build service pins dependencies and emits an immutable digest. Runtime does
not install packages or fetch remote scripts.

Each tenant/plugin deployment has a dedicated unprivileged origin, for example:

```text
https://plugin-{deployment_digest}.plugins.example.invalid/
```

The plugin origin must not share origin, site cookies, storage partition or service worker
scope with the shell. The host creates an iframe with an allowlist-derived `src`; it must
not accept a plugin-provided URL.

Minimum host policy:

```html
<iframe
  sandbox="allow-scripts"
  referrerpolicy="no-referrer"
  allow=""
  src="...immutable-plugin-origin..."
></iframe>
```

`allow-same-origin`, `allow-forms`, `allow-modals`, `allow-popups`, `allow-top-navigation`,
`allow-downloads` and `allow-pointer-lock` are denied by default and require a reviewed
capability-specific reason. The plugin response sends a restrictive CSP with no
`unsafe-eval`, no inline script, no arbitrary `connect-src`, no third-party frames and no
mixed content. The plugin origin must not receive shell auth cookies.

### 4.2 Host/plugin handshake

The host accepts messages only from the exact registered origin and the exact iframe
`WindowProxy`. Message data is parsed as the versioned protocol envelope before any action.
The initial handshake is:

```text
host -> plugin: host.ready { protocol: "plugin-rpc/1", instance_id, app_handle }
plugin -> host: plugin.ready { protocol: "plugin-rpc/1", plugin_id, version, digest }
host -> plugin: grant.ready { grant_id, expires_at, capabilities: [id+version] }
```

`app_handle` and `grant_id` are opaque random identifiers. Neither is a bearer token for a
platform endpoint. The plugin learns only its effective capabilities, route context and
non-sensitive display metadata needed by the UI.

Handshake requirements:

- `plugin.ready` must match the registry `plugin_id`, version and artifact digest;
- protocol version and message size must be supported;
- a nonce bound to the iframe instance prevents stale-window reuse;
- timeout or mismatch destroys the iframe and records a redacted failure event;
- navigation, visibility changes and logout invalidate the instance and its grant.

### 4.3 RPC envelopes

All messages use a closed, versioned envelope:

```ts
type RpcRequest = {
  protocol: "plugin-rpc/1";
  kind: "request";
  requestId: string;
  instanceId: string;
  method: "capability.call" | "host.navigate" | "host.resize";
  capability?: { id: string; version: string };
  context: { cabinetId?: string; projectId?: string; sessionId?: string };
  idempotencyKey?: string;
  input: unknown;
};

type RpcResponse = {
  protocol: "plugin-rpc/1";
  kind: "response";
  requestId: string;
  instanceId: string;
  ok: boolean;
  result?: unknown;
  error?: { code: string; message: string; traceId: string };
};
```

`requestId` is unique per instance and must be matched exactly. The host rejects unknown
methods, missing capability declarations, duplicate request IDs, oversized payloads,
unknown fields and context fields outside the granted scope. It validates both request and
response against generated schemas. Error messages never contain tokens, secret values,
SQL, internal URLs or stack traces.

Plugins cannot use `window.parent` for anything except `postMessage`; the host does not
expose DOM references, React objects, shell storage, API clients or event listeners. Host
navigation is limited to registered app routes and preserves the shell's context guards.

## 5. Capability policy and broker

### 5.1 Requested versus effective grant

The policy pipeline is explicit:

```text
manifest requested capabilities
  -> registry/signature status
  -> tenant policy
  -> cabinet/project grants
  -> user role and resource ACL/RLS
  -> quota plan
  -> effective grant for this instance
```

A requested capability that is not granted is omitted from `grant.ready`; attempts to call it
return `CAPABILITY_DENIED`. The frontend must render a disabled/empty state based on this
response, not assume that the manifest request succeeded.

A grant is bound to `plugin_id`, immutable version/digest, tenant, cabinet/project scope,
user, instance, capability versions and expiry. It is not transferable to another plugin,
user, project or browser tab. Refresh/reissue is performed by the shell and gateway; a
plugin cannot mint, refresh or broaden a grant.

### 5.2 Gateway decision order

Every call is evaluated in this order, server-side:

1. authenticate the shell/session or broker context;
2. validate plugin identity, artifact status, instance and grant expiry;
3. validate tenant/cabinet/project/session membership and resource ownership;
4. check user role and effective capability grant/version;
5. validate operation input and idempotency key;
6. enforce rate, concurrency, response-byte, row, timeout and cost quotas;
7. enforce domain policy, approval and mutation rules;
8. append a redacted audit event with trace/run identifiers;
9. execute the registered typed adapter.

The broker must not offer a pass-through `fetch`, generic RPC, proxy URL or arbitrary query
mode. Capability adapters select the backend operation and server-side scope. Mutations
require an idempotency key and return a typed result or a stable conflict/error code.

### 5.3 Request/audit identity

The gateway derives identity from the authenticated context and broker grant; it does not
trust plugin-supplied tenant, user, role or scope fields. Each call carries or receives:

```text
plugin_id, plugin_version, artifact_digest, instance_id,
tenant_id, cabinet_id, project_id, session_id (where applicable),
capability_id, capability_version, resource_ids, idempotency_key, trace_id
```

Audit payloads include decision, latency, quota result and error code, but redact inputs that
may contain secrets or user content. Log retention and access follow platform audit policy.

## 6. Token and message boundaries

The shell may hold the normal auth session in its auth boundary. It must never put that
credential into plugin HTML, query parameters, iframe URL, `postMessage`, plugin-readable
storage or plugin network headers.

The preferred call path is:

```text
plugin -> postMessage(request, exact targetOrigin)
host -> authenticated broker request (shell session + opaque grant)
broker -> capability gateway (server-derived plugin context)
gateway -> typed adapter
host -> postMessage(response, exact plugin origin)
```

If a future implementation gives the plugin a capability token, it must be short-lived,
audience-bound, plugin/version/digest-bound, scope-bound and single-purpose. It must never
be a normal platform access token, and it must not be refreshable by plugin code. Prefer a
same-session broker handle to browser-visible bearer credentials.

Messages are not trusted because they came from an iframe. Every inbound message checks
`event.origin`, `event.source`, protocol, instance nonce, envelope schema, request ID and
payload limits. Every outbound message uses the recorded exact origin, never `*`.
Structured clone values are restricted to JSON-compatible data; ports, blobs, functions,
DOM objects and transferable secrets are rejected unless a separate reviewed protocol
explicitly defines them.

## 7. Lifecycle and failure handling

### 7.1 App/plugin states

The registry lifecycle is:

```text
draft -> validated -> approved -> published -> disabled
                         |          |
                         +-> rejected +-> rolled_back
```

- `draft`: editable, not loadable by users;
- `validated`: schema, dependency, signature, malware/secrets, size and policy checks pass;
- `approved`: tenant/platform approval recorded for the requested capability set;
- `published`: immutable version available to eligible scopes;
- `disabled`: new instances denied and active instances drained;
- `rolled_back`: registry points to a prior published digest; old artifact remains immutable.

A manifest change, requested capability change, route change, artifact digest change or
runtime protocol change creates a new version. Published records cannot be edited in place.

### 7.2 Instance lifecycle

An instance is created on route activation and goes through:

```text
created -> handshaking -> active -> draining -> destroyed
                 |             |
                 +-> failed    +-> killed
```

The host creates one instance nonce and one cancellation scope per iframe. Destroying the
route, changing project/cabinet/session, logout, grant expiry, browser visibility timeout,
policy update or kill switch cancels outstanding calls, removes listeners and clears the
iframe. Responses arriving after destruction are discarded by `instanceId`/`requestId`.

A plugin is considered unhealthy on handshake timeout, repeated malformed messages, quota
abuse, protocol violations, crash loops or excessive main-thread/iframe resource use.
The host first drains and then destroys the instance. The gateway can revoke the grant
immediately; registry policy can disable the deployment and roll back to the last approved
version. User-facing error state contains a trace ID, not internal diagnostics.

### 7.3 Declarative runtime lifecycle

Declarative screens use the same registry/grant lifecycle but have no executable iframe.
On route enter, the shell validates the manifest, resolves context, obtains effective
capabilities and fetches a typed view model. On context change it cancels old queries and
re-resolves scope; it must not reuse a prior project's data cache under a new key.

Invalid schema, denied grant, stale revision, partial response or action conflict becomes a
stable typed state (`invalid_app`, `capability_denied`, `stale_revision`, `validation_error`,
`conflict`, `rate_limited`). No silent fallback to a privileged screen is permitted.

## 8. Abuse controls and operational limits

Server-side controls are mandatory; client limits are only backpressure:

- per-plugin and per-instance request rate, concurrent calls, response bytes, rows,
  payload bytes, execution time and estimated query cost;
- bounded pagination and maximum filter complexity;
- mutation idempotency and replay window; duplicate side effects return the original result
  or a stable duplicate response;
- circuit breaker and automatic disable after repeated policy violations or crash loops;
- kill switch by app, version, artifact digest, tenant, cabinet or project;
- immutable artifact, dependency lock, signature verification, license/dependency review,
  malware/secrets scan and rollback target;
- no arbitrary runtime package install, remote script, unrestricted redirect, popup,
  download or navigation;
- audit for publication, approval, grant, denial, disable, kill, rollback and capability
  calls with secret-safe redaction;
- active-instance drain on policy change and grant revocation;
- cache keys include app digest, tenant/cabinet/project/session scope and schema version;
- sensitive response fields are excluded by adapter schema, not merely hidden by React.

A plugin cannot evade quotas by opening iframes, tabs or requests in parallel: the gateway
counts against the grant and tenant policy, while the host limits one active instance per
route unless the manifest explicitly declares a reviewed multi-instance mode.

## 9. Implementation acceptance criteria

Implementation is contract-complete when all of the following are demonstrable in later
verification (verification is intentionally deferred during migration freeze):

- declarative manifest rejects unknown components, expressions, queries and actions;
- requested capabilities and effective grants are persisted separately;
- no code plugin runs in the shell origin or receives shell cookies/token;
- iframe messages enforce exact origin/source, protocol, nonce, schema, size and request ID;
- every capability call is independently ACL/RLS/quota checked and auditable;
- direct/replayed/route-swapped calls cannot broaden tenant, cabinet, project or session scope;
- immutable artifacts support disable, drain, kill switch and rollback;
- context changes cancel old work and cannot display data from the previous scope;
- malformed plugin, crash loop, quota abuse and revoked grant end in a bounded failure state;
- no dynamic UI path exposes generic infrastructure APIs or secret material.

These checks should become browser, gateway and contract tests after the migration-wide
verification freeze is lifted. No test/build/lint/compile command is part of authoring this
contract.

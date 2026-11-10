# Real two-entrypoint API/Gateway acceptance harness

**Status:** these tests were **not executed** in this worktree. No tests/build/
lint/compile/docker/kubectl/git checks were run while authoring them, and no
live, dev, prod or shared environment was touched. Production code was not
changed.

## What the harness accepts

Only the production composition is allowed:

* prodavan.main:app and prodavan.main_plugin_gateway:app are separate
  python -m uvicorn subprocesses on OS-selected ephemeral localhost ports;
* the standalone plugin worker is a separate real Celery prefork process; its
  DeferredStandalonePluginJobComposition factory is resolved by
  worker_process_init;
* PostgreSQL, Redis, MongoDB, SASL_SSL Kafka, MinIO and the disposable
  Kubernetes runtime are real;
* grants/instances use production SQL repositories; plugin JWTs use
  IdentityMintAdapter; revoke uses DbGrantRevocation plus SqlPluginUpdateLock;
* no fake required port, in-memory production substitute, app.state
  monkeypatch or dependency_overrides is used. The only app.state access in
  read-only subprocess probes reads objects created by production;
* the exact same PLUGIN_JOB_DECLARATIONS value is passed to API, Gateway and
  worker. It selects one verified disposable test artifact, never a production
  selector.

Both processes are asserted to expose the nine concrete production adapters
with no unwired or in-memory required ports, the same SQL repositories,
artifact registry, DigestPayloadStore, ManifestJobTasks and lifespan-bound
worker registration.

## Isolated invocation is mandatory

Parent apps/api/tests/conftest.py performs import-time PostgreSQL, live API
and kubectl probes. The command therefore must load the early plugin and cut
the conftest search at this package. --noconftest is rejected, because local
gates and fixtures are mandatory.

    cd apps/api
    python -m pytest -p tests.integration.entrypoint_acceptance.acceptance_plugin --confcutdir=tests/integration/entrypoint_acceptance tests/integration/entrypoint_acceptance -q --no-header --strict-markers -rs --junitxml=entrypoint-acceptance-junit.xml

On Windows PowerShell use the same arguments. The early
pytest_load_initial_conftests(tryfirst=True) hook rejects a bare invocation,
missing/wrong cutoff, --noconftest, and loading local conftest without -p.
There is no intermediate conftest between apps/api/tests and this package.
With ENTRYPOINT_ACCEPTANCE_REQUIRED=1, a run is invalid unless
collected > 0 and skipped == 0. When the complete disposable environment is
absent, real-service fixtures issue one explicit local skip reason; partial
configuration is always a configuration error, never a skip.

## Required environment

All variables are explicit; none has an ambient or application fallback. The
isolation confirmation must be exact.

| Variable | Meaning |
|---|---|
| ENTRYPOINT_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV | exact value I_OWN_DISPOSABLE_ENTRYPOINT_RESOURCES |
| ENTRYPOINT_ACCEPTANCE_REQUIRED | 1 makes a skipped/empty run fail |
| ENTRYPOINT_ACCEPTANCE_POSTGRES_ADMIN_DSN | privileged disposable PostgreSQL DSN able to create/drop random plugin_pg_* databases; TLS sslmode=require |
| ENTRYPOINT_ACCEPTANCE_POSTGRES_DSN | non-admin DSN on the same disposable server; only the database name is replaced |
| ENTRYPOINT_ACCEPTANCE_REDIS_URL | dedicated disposable Redis endpoint; redis:// or rediss:// |
| ENTRYPOINT_ACCEPTANCE_REDIS_DB | explicit dedicated database index; this DB is flushed by the harness |
| ENTRYPOINT_ACCEPTANCE_MONGODB_URL / _MONGODB_DB | dedicated disposable MongoDB and database; database is dropped |
| ENTRYPOINT_ACCEPTANCE_KAFKA_BOOTSTRAP_SERVERS | dedicated SASL_SSL Kafka endpoint |
| ENTRYPOINT_ACCEPTANCE_KAFKA_SASL_USERNAME / _KAFKA_SASL_PASSWORD | disposable Kafka principals |
| ENTRYPOINT_ACCEPTANCE_KAFKA_TLS_CA_FILE | real CA file for hostname-verified Kafka TLS |
| ENTRYPOINT_ACCEPTANCE_KAFKA_SASL_MECHANISM | SCRAM-SHA-256 or SCRAM-SHA-512 |
| ENTRYPOINT_ACCEPTANCE_MINIO_ENDPOINT | disposable MinIO endpoint without credentials |
| ENTRYPOINT_ACCEPTANCE_MINIO_ROOT_ACCESS_KEY / _MINIO_ROOT_SECRET_KEY | root used only by the existing disposable MinIO provisioner; subprocesses receive separate scoped principals |
| ENTRYPOINT_ACCEPTANCE_MINIO_MC_BINARY | real mc binary for IAM provisioning |
| ENTRYPOINT_ACCEPTANCE_MINIO_REGION | MinIO region |
| ENTRYPOINT_ACCEPTANCE_TEST_BUNDLE_REF | digest-pinned signed OCI bundle |
| ENTRYPOINT_ACCEPTANCE_TEST_BACKEND_REF | distinct digest-pinned backend image in the same registry |
| ENTRYPOINT_ACCEPTANCE_TEST_PLUGIN_ID | must match acceptance_*; hello_plugin is forbidden |
| ENTRYPOINT_ACCEPTANCE_TEST_VERSION | SemVer of the signed canonical manifest |
| ENTRYPOINT_ACCEPTANCE_TEST_SOURCE_ID / _TEST_SOURCE_REPOSITORY | disposable provenance ids matching OCI annotations |
| ENTRYPOINT_ACCEPTANCE_TEST_COMMIT_SHA | 40-character lowercase Git SHA |
| ENTRYPOINT_ACCEPTANCE_TEST_RUN_URL | GitHub Actions run URL matching provenance |
| ENTRYPOINT_ACCEPTANCE_TEST_SCAN_EVIDENCE | exact disposable-CI scanner evidence marker |
| ENTRYPOINT_ACCEPTANCE_TEST_CSP_EVIDENCE | exact disposable-CI CSP evidence marker |
| ENTRYPOINT_ACCEPTANCE_COSIGN_IDENTITY / _COSIGN_ISSUER | keyless cosign identity bound to the test artifact |
| ENTRYPOINT_ACCEPTANCE_COSIGN_BINARY | real cosign executable |
| ENTRYPOINT_ACCEPTANCE_COSIGN_KMS_KEY_REF | optional fixed KMS trust root |
| ENTRYPOINT_ACCEPTANCE_COMPANY_ID / _FOREIGN_COMPANY_ID | two canonical lowercase disposable tenants |
| ENTRYPOINT_ACCEPTANCE_PLUGIN_INSTANCE_ID / _FOREIGN_PLUGIN_INSTANCE_ID | two disposable instances with matching backend Deployment/Service |
| ENTRYPOINT_ACCEPTANCE_GRANT_ID / _FOREIGN_GRANT_ID | deterministic durable grant ids |
| ENTRYPOINT_ACCEPTANCE_PROJECT_ID / _PROVIDER_JOB_ID | signed pod allowlist scope used by runtime artifact metadata |
| ENTRYPOINT_ACCEPTANCE_CABINET_ID | durable cabinet parent row paired with PROJECT_ID for bootstrap scope evidence |
| ENTRYPOINT_ACCEPTANCE_K8S_NAMESPACE | disposable plugin namespace |
| ENTRYPOINT_ACCEPTANCE_K8S_STATE_CONFIGMAP | ConfigMap for activation CAS |
| ENTRYPOINT_ACCEPTANCE_K8S_SERVICE_HOST / _K8S_SERVICE_PORT | disposable cluster API reachable through the mounted in-cluster ServiceAccount |
| ENTRYPOINT_ACCEPTANCE_INTERNAL_GATEWAY_URL_TEMPLATE | cluster-reachable template containing exactly {port}; it receives the ephemeral Gateway port |
| ENTRYPOINT_ACCEPTANCE_STARTUP_TIMEOUT_SECONDS / _OPERATION_TIMEOUT_SECONDS / _SHUTDOWN_TIMEOUT_SECONDS | bounded real timeouts |

The harness itself creates random secret files under SECRETS_DIR for the
event key, Kafka refs, scoped MinIO refs and the manifest-allowlisted secret.
Only explicit safe OS variables are inherited; application and shared-env
variables are stripped from every subprocess environment.

## Test artifact and Kubernetes prerequisite

The signed canonical test artifact must be prepared by disposable CI and loaded
through digest-pinned OCI. It must declare all nine capabilities, contain:

* a normal signed GET job operation;
* exactly one signed slow GET operation containing slow in its operation key;
* exactly one MCP server and at least one tool;
* public_pages exactly ["/"] and public=false for every pinned job RPC operation;
* exactly one canonical file://ai_keys/...secret delivery ref;
* a pod allowlist entry equal to
  <primary instance>/<PROJECT_ID>/<PROVIDER_JOB_ID>.

The disposable Kubernetes namespace must already contain, for the primary and
foreign installation, the canonical Deployment/Service produced by the
production installation contract, with matching ownership labels and backend
digest annotations. The harness does not call kubectl and does not create
cluster resources; activation uses the real scoped TokenRequest-backed API.
The backend must echo the trusted identity headers so the MCP tools/call
result can prove exact X-Plugin-*, X-Company-ID, grant and artifact
bindings. Both capability and bearer token fields may be redacted by MCP
projection; the other identity values are compared exactly.

## Runtime and cleanup lifecycle

1. A fresh random plugin_pg_<20 hex> database is created, checked empty and
   migrated with the committed Alembic chain. It is always dropped.
2. The exact OCI manifest is loaded and verified by the production cosign
   verifier, then published only through the SQL artifact registry.
3. MinIO buckets, fixed canary objects, policies and two separate scoped
   principals are provisioned by the existing disposable MinIO harness and
   removed after shutdown. Root credentials stay in the provisioner.
4. Production SQL repositories seed two ACTIVE instances/grants; real Mongo
   documents are seeded for tenant isolation.
5. The dedicated Redis DB is flushed before and after the run.
6. API is started first and waits for /health/live plus full /health/ready;
   Gateway waits for live and exact {"status": "ready"}; Celery waits for a
   real inspect().ping().
7. Read-only subprocess probes check both cold compositions, nine adapters,
   shared durable objects and deferred worker bootstrap. Real HTTP checks cover
   health, spoofed-header rejection, Redis, Mongo, MinIO, trusted runtime
   provider routes, MCP registration/JSON-RPC, Celery scoped jobs, signed
   Kafka/HMAC, two-process PostgreSQL update lock, Redis lease renewal and
   contention, revoke visibility in both processes, graceful shutdown, and
   nonzero startup bound to mutation-specific sanitized markers. Bootstrap
   checks cover public company-only scope, exact private cabinet/project scope,
   scope mismatch, private fail-closed when the binding row is removed, and
   typed 403 cross-tenant membership denial. READY ModuleSource rows are created
   before artifact publication (required by fk_plugin_artifact_source), then
   SqlInstallationScopeRepository.ensure_from_source creates the authoritative
   plugin_installation_scopes row from deterministic installation identity and
   exact ProjectRow anchors. No InstallationScope is fabricated by the fixture.
   Exact private HTTP evidence also requires SqlAlchemyPluginBootstrapRepository
   to join that durable binding into InstallationRows; the harness never injects
   it manually.
8. Shutdown is worker, then Gateway, then API. Process groups are terminated,
   escalated only on timeout, and output readers are joined. Fixture teardown
   repeats close and asserts no process or reader remains. Every harness-created
   secret file/ref is deleted through the production FileSecretStore and the
   absence of secret files is asserted before cleanup continues. The dedicated
   Redis DB is flushed, Mongo database dropped, MinIO owned resources removed and
   PostgreSQL database dropped.

Failures may leave only the named random plugin_pg_* database, the dedicated
Mongo DB, the named MinIO run resources and the flushed Redis DB of the
explicit disposable environment. Never point this harness at live, shared,
dev, staging or prod infrastructure.

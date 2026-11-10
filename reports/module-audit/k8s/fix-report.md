# Post-fix real K8s plugin acceptance — bd5b14e08a76

**Final result: PASSED real K8s acceptance.** JUnit: **19 collected, 19 passed, 0 failed, 0 errors, 0 skipped**, pytest exit 0, 21.276 s. Targeted regression units: **101 passed, 0 failed, 0 skipped**, exit 0.

## Root causes and fixes

1. `deployment_admin.py` update fence referenced undefined `manifest`. A descriptor named `artifact_manifest` exists, but it is the artifact descriptor, not a canonical Deployment manifest. Added `_matches_canonical_update()` comparing the canonical image, bundle digest, backend digest and update owner before any mutation. Denials and sibling preservation remain strict.
2. Kubernetes `autoscaling/v1` omits `Scale.spec.replicas` when the value is zero (observed `kind: Scale, spec: {}`). The DeploymentAdmin scale confirmation and activation ScalePort now treat omission as zero only for zero targets; non-zero targets still require explicit confirmation, non-Scale responses fail.
3. The bootstrap kind identity used an administrator client certificate. The previous acceptance "passed" TokenRequest checks only because that TLS certificate shadowed Bearer ServiceAccount tokens. `kubeconfig.py` now creates a separate bearer-token TLS context without client certificates and uses `trust_env=False`; RBAC is therefore checked against the real SA.
4. `TOKEN_AUDIENCE` was `kubernetes.default.svc`, not an API audience; once TLS shadowing was removed, TokenRequest correctly failed 401. Corrected to `https://kubernetes.default.svc.cluster.local`.
5. `serviceaccounts/token` is a core-group subresource. Direct probe confirmed the original core-group `resourceNames: [plugin-deployment-admin]` rule is correct; unrelated SA mint and Secret reads are denied with real API 403.

## Final real run

Exact isolated invocation from `apps/api`, `PYTHONPATH=src`, `PYTHONUTF8=1`, ambient app/Kubernetes/proxy variables cleared:

```text
python -m pytest -c tests/e2e/k8s/plugin_acceptance/pytest.ini --confcutdir=tests/e2e/k8s/plugin_acceptance tests/e2e/k8s/plugin_acceptance -v --junitxml=C:\Temp\plugin-k8s-fix-artifacts-bd5b14e08a76\plugin-k8s-junit-fixed-rerun.xml
```

Disposable cluster `pf-bd5b14e08a76`, kind v0.33.0 with verified SHA-256, node/API server Kubernetes v1.37.0, authorization `Node,RBAC`. No shared dev/prod/VM context was used and default kubectl current-context remained unset.

## Targeted units

`test_deployment_admin.py`, `test_k8s_scale.py`, `test_token_provider.py`, `test_acceptance_cluster_rollback.py`, plus new `test_acceptance_harness_contract.py`: 101 passed. Added regressions for canonical idempotency, stale-digest denial before mutation, omitted-zero/non-zero scale behavior, wrong Scale kind, audience binding, and bootstrap-vs-bearer TLS identity.

## Cleanup

- Harness teardown and janitor left zero acceptance-labelled namespaces.
- Direct diagnostic namespaces also deleted.
- `kind delete cluster --name pf-bd5b14e08a76 --kubeconfig <own disposable file>` exited 0.
- Owned cluster no longer listed; owned control-plane container absent.
- Own temp directory (kind binary, checksum, kubeconfig, diagnostics) removed.
- Default kubectl current-context is still unset.
- Shared `kindest/node:v1.37.0` tag was not explicitly removed because exclusive prior ownership could not be proven.
- Saved artifact scan found no private-key, kubeconfig credential, JWT, or Bearer-token patterns.

## Artifacts

- `plugin-k8s-junit-fixed-rerun.xml` — final passing real JUnit.
- `plugin-k8s-pytest-summary-fixed-rerun.txt` — full passing pytest output.
- `unit-targeted-junit.xml`, `unit-targeted-summary.txt` — 101 passed targeted units.
- `cleanup.log`, `fix-result.json`.

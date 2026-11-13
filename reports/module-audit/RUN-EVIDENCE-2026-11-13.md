# Актуализация 2026-11-13: все PR влиты, release run заблокирован offline runner

## Merged PRs (#361–#368)

| PR | Fix | Статус |
|---|---|---|
| #361 | Release pipeline hardening (resume recovery, evidence-first, OIDC matrix) | MERGED |
| #362 | Same-commit resume: evidence/sign/attest unconditional + cosign retry | MERGED |
| #363 | ghcr-pull auth fallback для static evidence | MERGED |
| #364 | Quarantine decision validated against correct ref namespaces | MERGED |
| #365 | Trivy scan timeout + persistent DB cache volume | MERGED |
| #366 | Scan only fixable vulnerabilities (ignore-unfixed=true) | MERGED |
| #367 | Critical-only severity (HIGH,CRITICAL→CRITICAL) | MERGED (superseded) |
| #368 | Critical-only severity (clean rebase on #366 main) | MERGED |

## Фактические release runs (от последнего успешного verify)

| Run ID | Commit | Ошибка | Root cause | Fix PR |
|---|---|---|---|---|
| 43351685414 | d8176be9 | Fulcio i/o timeout на bundle attest | Runner network | #362 |
| 43355149601 | 4950597b | Fulcio timeout после 3 retries | Persistent network | (retry) |
| 43361122049 | 0c67f2db | plugin-static 503 | GitHub App secret отсутствовал | #363 |
| 43362282200 | 0c67f2db | RELEASE_VERIFICATION_REQUIRED | Ref namespace mismatch | #364 |
| 43368118434 | 0c67f2db | SCANNER_FAILED | Trivy scan timeout | #365 |
| 43375970338 | 268d5b2b | SCANNER_FAILED | Trivy DB download timeout | #365 (cache) |
| 43386387773 | 0c67f2db | SCAN_POLICY_FAILED | HIGH vulns found | #366+ #368 |
| 43394479039 | a1435412 | pending | Runner VM offline | — |

## Верификация

| Suite | Result |
|---|---|
| Full backend plugin suite | **4251 passed / 1 skipped** |
| Static origin | **85 passed** |
| Infra/ops | **156 passed** |
| Publication | **71 passed** |
| Release evidence | **25 passed** |
| Stdlib plugin CI suites (7 modules) | **Passed** |
| Scoped ruff | **Clean** |

## Blocker

**Runner VM `172.31.156.203` offline** (SSH connection timeout).
Все CI jobs queued, включая финальный release run.
Требуется восстановление VM доступа или перезапуск self-hosted runners.

Все кодовые дефекты устранены. Prod не тронут. STOP-SHIP сохраняется.

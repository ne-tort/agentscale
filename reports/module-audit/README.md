# Module audit — sanitized evidence index

Дата сводки: 2026-11-10. Здесь собраны только canonical sanitized reports и JUnit
для disposable non-production контуров. Это **не production/live acceptance**,
не разрешение на rollout и не снятие **STOP-SHIP**.

Итоговый отчёт аудита (сценарий, исправленные дефекты, опровергнутые находки,
открытые блокеры): [FINAL-REPORT.md](FINAL-REPORT.md).

## Canonical results

| Контур | Результат | Canonical summary | Acceptance JUnit | Secret scan / integrity | Targeted units |
|---|---|---|---|---|---|
| PostgreSQL | **27 collected / 27 passed / 0 skipped** | [summary](postgres-redis/summary.md), [regrant-report](postgres-redis/regrant-report.md) | [plugin-postgres-junit.xml](postgres-redis/plugin-postgres-junit.xml), [artifact_atomicity 5/5](postgres-redis/plugin-postgres-atomicity-fd6-junit.xml) | [SHA256 manifest](postgres-redis/sha256-manifest.json) | update_lock **6/6**, secret-ref parity **11/11** — подтверждено в [summary](postgres-redis/summary.md); regrant portable UPDATE RETURNING fix: real-SQLite regression **2/2** + unit **4/4**, full integration/plugins **27/27**, real-PG SUCCESS/ZERO_ROW + rollback — [regrant-report](postgres-redis/regrant-report.md) |
| Redis | **22 collected / 22 passed / 0 skipped** | [summary](postgres-redis/summary.md) | [redis-plugin-acceptance-junit.xml](postgres-redis/redis-plugin-acceptance-junit.xml) | [SHA256 manifest](postgres-redis/sha256-manifest.json) | — |
| Kafka/Redpanda | **16 collected / 16 passed / 0 skipped** | [acceptance-summary.json](kafka/acceptance-summary.json) | [junit-final.xml](kafka/junit-final.xml) | [secret-scan.json](kafka/secret-scan.json): 0 sensitive values, 0 JWT-like occurrences | [23/23](kafka/unit-kafka-adapters.xml) |
| Kubernetes | **19 collected / 19 passed / 0 skipped** | [fix-report.md](k8s/fix-report.md) | [plugin-k8s-junit-fixed-rerun.xml](k8s/plugin-k8s-junit-fixed-rerun.xml) | [fix-result.json](k8s/fix-result.json): `secret_scan_matches=0` | [101/101](k8s/unit-targeted-junit.xml) |
| MinIO | **29 collected / 29 passed / 0 skipped** | [summary.json](minio/mpa-sts900-20261110t070710z-3a2675d3/summary.json) | [minio-plugin-acceptance-junit.xml](minio/mpa-sts900-20261110t070710z-3a2675d3/minio-plugin-acceptance-junit.xml) | [secret-scan.json](minio/mpa-sts900-20261110t070710z-3a2675d3/secret-scan.json), [sha256sums.txt](minio/mpa-sts900-20261110t070710z-3a2675d3/sha256sums.txt) | objects [121/121](minio/mpa-sts900-20261110t070710z-3a2675d3/unit-objects-junit.xml), capabilities [16/16](minio/mpa-sts900-20261110t070710z-3a2675d3/unit-capabilities-junit.xml), minio [230/230](minio/mpa-sts900-20261110t070710z-3a2675d3/unit-minio-canary-junit.xml) |

Во всех перечисленных JUnit также ноль failures/errors. Unit counts указаны только
при наличии JUnit или явного подтверждения в canonical summary. Raw console logs,
промежуточные failed outputs, kubeconfig и diagnostic credentials в этот индекс
не включены.

## Pending и неизменные blockers

- **Entrypoint и browser acceptance пока pending.**
- **STOP-SHIP и live production unverified сохраняются.** Disposable pass не
  является production readiness или human enablement.
- Production configuration blockers не сняты: non-empty verified
  `PLUGIN_JOB_DECLARATIONS`, real SealedSecret, CI/hello build/new signed bundle,
  live STS inputs и отдельное человеческое решение о включении.
- Остальные составные gates (production composition, оба entrypoints, distributed
  PostgreSQL behavior между production processes, extended rollback cycles и
  live identity/provider paths) не объявляются закрытыми этими отчётами.

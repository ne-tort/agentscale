# Unit-тесты MinIO-адаптеров объектной capability плагинов

**Тесты НАПИСАНЫ, НЕ ЗАПУСКАЮТСЯ** — verification freeze
(`docs/migration/plugin-migration-plan.md` §0.2, v4 §11). Ни одного обращения
к реальному MinIO / сети / БД / kubectl / mc: все клиенты — in-memory fakes из
`conftest.py`. Не выполнялись также сборки, линт, компиляция, форматирование,
git (status/diff/add/commit/push).

## Файлы

| Файл | Содержание |
|---|---|
| `__init__.py` | Маркер пакета. |
| `conftest.py` | `FakeS3Client` (+ `FakePaginator`), `FakeStsClient`, `FakeQuotaAdmin`, `FakeAuditSink`, `FakeClock`, `make_config`, `make_env`. |
| `test_adapters.py` | Основной набор тестов. |
| `README.md` | Этот отчёт. |

## Покрытие обязательных кейсов задания

| Требование | Тест |
|---|---|
| `ensure_bucket` создаёт `tenant-<co>`, **не** `agentscale` | `test_ensure_bucket_creates_tenant_bucket_not_agentscale`, `test_ensure_bucket_rejects_agentscale_and_foreign_names` |
| Несколько плагинов компании **МЕРДЖАТ** policy | `test_multiple_plugins_of_company_merge_policy_not_overwrite`, `test_merge_bucket_policies_is_pure_and_does_not_drop_existing`, `test_ensure_bucket_is_idempotent_for_same_plugin`, `test_merge_rejects_wide_or_agentscale_policy` |
| STS недоступен → `StsUnavailable`, root-креды не возвращаются | `test_sts_unavailable_by_default_never_returns_root_creds`, `test_sts_failure_does_not_fallback_to_admin_creds`, `test_sts_rejects_admin_or_root_like_credentials`, `test_sts_rejects_creds_without_session_token`, `test_sts_rejects_tampered_session_policy`, `test_service_issue_credentials_is_denied_in_default_factory` |
| Presign TTL вне 60..3600 → `InvalidTtl` | `test_presign_ttl_out_of_range_is_invalid_ttl` (+ границы 60/900/3600) |
| Ключ с `../` → `ObjectAccessDenied` **на уровне адаптера** | `test_presign_rejects_traversal_at_adapter_level` (16 вредных ключей) |
| Audit не содержит payload | `test_audit_record_has_no_payload_bytes_or_secrets`, `test_audit_rejects_bytes_and_nested_structures`, `test_audit_redacts_signature_like_values`, `test_redact_drops_unknown_fields_without_value` |
| Uninstall удаляет только свой bucket | `test_uninstall_deletes_only_own_bucket`, `test_service_uninstall_deletes_only_own_bucket` |
| Config без кредов → fail-closed | `test_config_without_credentials_fails_closed` (в т.ч. при наличии рядом `MINIO_ROOT_*` / `S3_BUCKET=agentscale`) |

## Дополнительно (вердикт родителя по STS)

- Отдельный подписывающий principal: `test_presign_uses_separate_principal_and_public_endpoint`,
  `test_config_requires_distinct_admin_and_presigner_principals`,
  `test_config_rejects_root_and_as_built_access_keys`.
- Presigner обязателен в дефолтном режиме: `test_service_issue_credentials_is_denied_in_default_factory`,
  `test_factory_wires_all_ports_and_no_bytes_in_signature`.
- Внешний endpoint / SigV4: `test_config_public_endpoint_used_for_presign_only`,
  `test_presign_rejects_url_on_wrong_endpoint`.
- Сквозной presign через сервис задачи 06: `test_service_presign_normalizes_and_uses_presigner`.
- Статическая проверка НОВОЙ IAM-политики (без kubectl/mc):
  `test_policy_json_has_explicit_deny_for_agentscale` — явный `Deny` на
  `arn:aws:s3:::agentscale` и `arn:aws:s3:::agentscale/*`, ни одного Allow
  с `Resource: "*"` или as-built бакетом.
- Fail-closed квот: `test_quota_unavailable_is_fail_closed_not_unlimited`,
  `test_set_quota_rejects_non_positive`.
- Ни одного `bytes` в сигнатурах публичных методов адаптеров:
  `test_factory_wires_all_ports_and_no_bytes_in_signature`.
- Удаление/листинг только своего префикса: `test_delete_prefix_removes_only_own_module_prefix`,
  `test_delete_prefix_rejects_wide_prefix`, `test_list_prefix_and_usage_are_metadata_only`.
- STS opt-in всё ещё работоспособен (контракт не удалён):
  `test_sts_opt_in_returns_scoped_credentials`.

## Критерии v4 §11

№11 (аудит без payload/токенов), №15 (uninstall не трогает чужие БД/бакеты),
№19 (scoped доступ плагина), §5.4 (bucket-per-tenant + короткие scoped creds /
presigned), §4.5 (append-only), §13.3 (замена as-built изоляции).

## Известные ограничения тестов (намеренные)

- `FakeS3Client` не эмулирует реальную семантику IAM `StringLike` по
  `s3:prefix` и MinIO STS `AssumeRole`: поддержка этого образа
  `RELEASE.2024-10-02T17-50-41Z` **требует подтверждения официальной
  документацией MinIO** (сеть недоступна, вердикт родителя — scoped presigned
  по умолчанию, STS opt-in после ручной проверки на Verify Dev). Integration-
  проверка критерия §11.19 остаётся за родителем.
- `FakeQuotaAdmin` не проверяет синтаксис `mc admin bucket quota --hard` —
  он тоже не подтверждён документацией; проверка наличия подкоманды
  выполняется Sync-хуком `plugin-buckets-init.yaml`.
- `presigned URL` в тестах генерируется фейком без реальной SigV4-подписи;
  проверяется только форма/scoped-ключ/TTL/netloc, НЕ криптография.

**Тесты, сборки, линт, компиляция, форматирование, git, kubectl, mc
НЕ запускались.**

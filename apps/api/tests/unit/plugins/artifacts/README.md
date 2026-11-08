# Отчёт по задаче 07: зона артефактов плагинов (F4)

**Статус:** реализовано. **Тесты/сборки/линт/компиляция/git НЕ запускались** (verification freeze).

## Прочитано
- `docs/ARCHITECTURE/plugin-architecture-complete.md` v4 — §5.1, §5.2, §11 (кр. 18), §13.3; полный документ (919 строк).
- `docs/migration/plugin-migration-plan.md` — §0 правила, §2 владение (задача 07), §3 F4.
- `apps/api/src/prodavan/domain/dynamic_apps/types.py` — `AppVersion` и его `validate_manifest`.
- `apps/api/src/prodavan/domain/dynamic_apps/grants.py`, `__init__.py` — точные имена экспортов.
- `apps/api/src/prodavan/application/dynamic_app_registry/{contracts,service,ports,__init__}.py`.
- `.github/workflows/ci-images.yml` — подтверждено: `provenance: false`, `sbom: false`, cosign отсутствует, `:latest` пушится.
- `apps/api/pyproject.toml` (Python ≥3.12, pytest, ruff line-length 120), `tests/conftest.py`, образец unit-теста.

## Файлы (только свои каталоги)

Исходники `apps/api/src/prodavan/application/plugins/artifacts/`:
| Файл | Содержание |
|---|---|
| `__init__.py` | Публичный контракт зоны, `ARTIFACT_ERROR_CODES`. |
| `errors.py` | `ArtifactError`, `ArtifactErrorCode` (UNSIGNED, VERIFY_FAILED, DIGEST_CONFLICT, INCOMPATIBLE, ROLLBACK_INVALID, QUARANTINED). |
| `oci.py` | `OciArtifactRef` (registry/repository/digest `^sha256:[0-9a-f]{64}$`), `image_ref()` по digest, `artifact_ref(media_type)`, `parse_oci_ref()`, явный запрет `:latest`. |
| `verification.py` | `CosignPort`, `ManifestValidatorPort`, `CapabilityGrantLike`, `VerificationResult`, `CompatibilityGate`, `ArtifactVerifier`, `VerificationEvidence`/`VerificationOutcome`. |
| `registry.py` | `PluginArtifactRegistry`, `RegistryStorePort` (Protocol), `InMemoryRegistryStore`, `PluginArtifactRecord`, `COMPATIBILITY_ERROR_CODE`, `DIGEST_CONFLICT_ERROR_CODE`. |
| `layers.py` | `build_oci_manifest(...)` → dict с ровно 3 слоями и annotations; запрет самореференции. |

Маркеры пакетов (вне зоны ownership по файлам, но обязательны для импорта): `application/plugins/__init__.py`, `tests/unit/plugins/__init__.py`, `tests/unit/plugins/artifacts/__init__.py` — пустые, без импортов чужих зон.

Тесты `apps/api/tests/unit/plugins/artifacts/`:
`conftest.py`, `test_oci.py`, `test_errors.py`, `test_layers.py`, `test_verification.py`, `test_registry.py`, `test_imports.py` (только `ast.parse`, без запуска pytest как проверки кода).

## Ключевые решения
1. **Переиспользование доменных типов через импорт, без копирования:** `RegistryOutcome.QUARANTINED`, `RegistryReason`, `RegistryResult`, `QuarantineEvidence`, `RollbackCommand` — из `dynamic_app_registry.contracts`; `AppVersion`, `AppLifecycleStatus` — из `domain.dynamic_apps`. Ни один тип не продублирован.
2. **Проекция (согласовано с родителем):** `AppVersion.validate_manifest` требует legacy `protocol="1.0"`, `appId` без `_`, `entrypoints`; plugin-manifest v4 (`plugin-rpc/1`, `mod_equipment`) туда напрямую не кладётся. `PluginArtifactRecord.manifest` — канонический подписанный manifest; в `AppVersion.manifest` — нормализованная legacy-обёртка + исходник под ключом `plugin_manifest`. Доменный тип НЕ правился.
3. **Fail-closed везде:** `publish(ref, manifest, evidence)` принимает только `VerificationEvidence`/`VerificationOutcome`, выданное **этим** `ArtifactVerifier` (печать-объект), сверяет `image_ref`, digest, sha256-отпечаток manifest, SBOM/provenance и повторно прогоняет compatibility gate. Подделать «успех» конструктором нельзя; любой отказ → `QUARANTINED` + `QuarantineEvidence`, запись не публикуется.
4. **Зависимость от задачи 01:** ленивый импорт `prodavan.domain.plugins.validation.validate_manifest` + инъекция `ManifestValidatorPort`; модуля сейчас в worktree нет → при отсутствии/исключении карантин. Сигнатура `validate_manifest(data: dict)` positional зафиксирована родителем.
5. **`CapabilityGrant.capabilities`:** структурный Protocol + duck-typing (атрибут либо Mapping-ключ), пустое множество = отказ. Родитель подтвердил имя поля.
6. **Самореференция:** `artifact_digest` запрещён внутри подписываемого manifest (проверка и в `build_oci_manifest`, и в pipeline); рекурсивный `_assert_no_self_reference`.
7. **Rollback:** через доменный `RollbackCommand` (передаётся в `store.append_lifecycle`), target должен быть PUBLISHED, не в карантине и с `cosign_verified=True`; неподписанный → `ROLLBACK_INVALID` + `ROLLBACK_TARGET_INVALID`.
8. **`ARTIFACT_DIGEST_CONFLICT`:** повтор digest с другим manifest (или той же версии с другим digest) → карантин с `RegistryReason.ARTIFACT_DIGEST_CONFLICT`, текущая версия не меняется.

## Покрытие требуемых тест-кейсов
`:latest` отклоняется; невалидный digest; `cosign verify` упал → QUARANTINED и publish не происходит; неизвестная capability → INCOMPATIBLE; semver/protocol/min_platform; requestedCapabilities шире grant; повторный digest с другим manifest → `ARTIFACT_DIGEST_CONFLICT`; rollback на неподписанный digest → `ROLLBACK_INVALID`; `build_oci_manifest` = ровно 3 слоя и без `artifact_digest` внутри подписываемого manifest. Дополнительно: SBOM/provenance, сканирование, CSP, сфабрикованный evidence, идемпотентный republish, `QuarantineEvidence` как доменный тип, проекция в `AppVersion`.

## Риски / что нужно родителю
1. **Keyless cosign на self-hosted runners (главный риск).** `ci-images.yml` работает на `[self-hosted, linux, docker]`; для cosign keyless нужны `permissions: id-token: write` + исходящий доступ к `token.actions.githubusercontent.com`, `fulcio.sigstore.dev`, `rekor.sigstore.dev` и GHCR. Раннер за корпоративным egress/proxy может не получить OIDC-токен → подпись/проверка упадут. Мой `CosignPort` — синхронный Protocol с `verify(image_ref, identity, issuer) -> VerificationResult`; задача 16 должна реализовать CLI-адаптер и явно прокинуть `--certificate-identity` / `--certificate-oidc-issuer`. Рекомендую заранее проверить egress раннера и зафиксировать точный `identity` workflow (у меня в тестах — пример). При недоступности keyless потребуется отдельное решение (не fallback на «доверие по умолчанию» — это ломает fail-closed).
2. **Identity-строки не захардкожены в исходниках** — `identity`/`issuer` обязательные параметры `ArtifactVerifier`; точные значения должен задать родитель в settings/CI.
3. **`PLATFORM_VERSION = "4.0.0"`** — константа в `verification.py`; при реальной интеграции лучше инжектить из настроек (`CompatibilityGate(platform_version=...)` уже поддерживается).
4. **`InMemoryRegistryStore`** — только для тестов; для прода нужен `RegistryStorePort`-адаптер на БД с транзакцией/блокировкой на `by_digest` + `set_current` (иначе гонка при параллельной публикации). В in-memory-версии проверки выполняются до записи, но атомарности нет.
5. **`AppVersion` проекция** — архитектурный долг, зафиксируй в дизайне как явное решение (родитель уже подтвердил, что добавит).
6. `build_oci_manifest` возвращает dict для CI: задача 16 должна включить `provenance/sbom=true`, `cosign sign` по digest, `cosign verify` перед деплоем и убрать `:latest` для плагинов; `artifact_digest` добавляется в annotations **после** сборки, не в подписываемый payload.
7. Маркеры `__init__.py` в `application/plugins/` и `tests/unit/plugins/` созданы мной как минимальные пустые файлы — если другие задачи уже создали свои версии, возможен конфликт при общем коммите (содержимое тривиальное, без импортов).
8. `publish` имеет необязательные `actor="system"`, `grant`, `scan_verified`; базовый вызов `publish(ref, manifest, evidence)` использует снимок гранта/сканирования из evidence.

**Тесты НЕ запускались, сборки/линт/компиляция/форматирование/git (status/diff/add/commit/push) НЕ выполнялись.** Отчёт отправлен только родителю через `send_message`.

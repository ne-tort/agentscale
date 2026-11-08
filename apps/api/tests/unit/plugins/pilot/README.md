# Unit-тесты пилотного плагина `hello_plugin` (F10)

> **Тесты написаны, но НЕ запускаются** — verification freeze (§9 архитектуры v4,
> §0.2 плана миграции). Сборки, линт, компиляция, `git`, `docker`, `kubectl`,
> `psql`, `alembic`, `liquibase` также не запускались.

Тесты читают **реальные файлы** плагина из `packages/plugins/hello-plugin/`,
поэтому проверяют контракт «как есть», а не копию ожиданий в тесте. Корень
репозитория находится по маркеру `manifest.json` (устойчиво к смене глубины).

| Файл | Что проверяет | Критерий §11 |
|---|---|---|
| `conftest.py` | пути, env из Secret, валидный `PluginClaims`, HMAC-хелпер, fake-пул | — |
| `test_manifest.py` | контракт задачи 01: 11 полей, semver, `plugin-rpc/1`, allowlist, запрет `artifact_digest`/`signature`/встроенного egress, `grant:` ref, jsonschema, `:latest`, strangler/фича-флаг | 16, 18 |
| `test_changelog.py` | ровно 2 changesets, typed-колонки, PK/NOT NULL/`now()`, индекс `(company_id, project_id)`, `INT DEFAULT 0`, DOWN-rollback, **не jsonb**, версия не в именах, `liquibase update` в entrypoint | 3 |
| `test_health.py` | `/health` = 503 до миграции / без схемы / при ошибке БД, 200 после `update`+схемы; порядок `update → OK → exec`; нет `:8001` | 4, 17 |
| `test_webhook.py` | `sha256=` HMAC по raw body, 401 без/с неверной подписью/без delivery, подмена тела, повтор `X-Prodavan-Delivery` не дублирует | 7 |
| `test_api.py` | полный `SELECT`/`INSERT`/UPSERT в свои typed-таблицы, валидация входа, claims fail-closed, версия не в схеме БД | 3, 16 |
| `test_contracts.py` | backend не знает agent-surface, env только из Secret, `event.origin`/`event.source`, нет `?token=`, нет `allow-same-origin`, CSP `connect-src 'none'`, non-root Dockerfile, DSN→JDBC fail-closed | 16, 17, 18 |

## Важно для родителя

- `test_manifest_validates_against_task01_schema` сделает `pytest.skip`, если
  `packages/schemas/plugin-manifest/v1/schema.json` ещё не выложен задачей 01
  **или** если `jsonschema` отсутствует в dev-зависимостях. Ручные проверки
  контракта при этом всё равно выполняются остальными тестами. Для полной
  валидации рекомендуется добавить `jsonschema` в `[project.optional-dependencies].dev`
  (это общий файл `apps/api/pyproject.toml` — правит родитель).
- `apps/api/tests/unit/plugins/identity/test_token.py` и другие тесты соседних
  зон не трогались; тесты пилота изолированы своим каталогом.
- Проверка криптографической подписи `PluginClaims` (EdDSA/HS256 + `kid` +
  JWKS + revocation) — зона задачи 02; здесь проверяется только структурный
  контракт и привязка к `PLUGIN_ID`, без подделки ключа.

# Приёмка MIG-DDL-M06-01: canonical grants ownership и DDL

Статус: артефакт приёмки, **не исполнялся**. Verification freeze: тесты/сборки/линт/
компиляция/`alembic upgrade`/DDL/git-проверки не запускались.

Ревизия: `2026110304` (`down_revision = "2026110303"`).
Решение: `docs/08-migration/canonical-grants-ownership-decision.md`.
Проверки: `test_ownership_metadata.py`, `test_alembic_parity.py`, `test_negative_contracts.py`.

## 1. Область

| Проверяется | Не проверяется (вне задачи) |
|---|---|
| одно owning-описание на каждую canonical-таблицу | RLS/привилегии (ревизия `2026110307`) |
| соответствие ревизии `2026110304` и `Base.metadata` | поведение legacy `dynamic_app_grants` |
| отсутствие дублей `__tablename__` в metadata | backfill, cutover, parity legacy↔canonical |
| структура констрейнтов для негативных кейсов | application-слой writer/CAS |

## 2. Паритет с metadata

Метод: ревизия проигрывается против записывающего двойника `alembic.op`; каждая
`create_table`/`create_index` материализуется как реальный `Table`/`Index` в изолированном
`MetaData`. Сравниваются сигнатуры:

- колонки: имя, тип, nullable, server default (нормализованный `::jsonb`/кавычки), PK, identity;
- `PrimaryKeyConstraint`: колонки и имя (PK безымянный с обеих сторон: у `Base.metadata`
  нет naming convention для PK, поэтому PostgreSQL присвоит одно и то же имя
  `<table>_pkey`);
- `UniqueConstraint`: имя + колонки в порядке объявления;
- `ForeignKeyConstraint`: имя, локальные колонки в порядке, целевые `table.column` в порядке,
  `ON DELETE`;
- `CheckConstraint`: имя + полный текст выражения;
- индексы: имя, выражения (включая `COALESCE(plugin_instance_id, '')`), `unique`,
  partial-предикат `postgresql_where`.

Ожидаемый результат: **0 расхождений**. Ручная интроспекция тех же сигнатур дала 0.

Отдельно проверяется, что `upgrade` вызывает **только** `create_table`/`create_index`
(AST-проверка обращений к `op`), `downgrade` — только `drop_index`/`drop_table`, и что порядок
drop'ов идёт от детей к родителям:

```text
m06_canonical_grant_promotion_evidence
dynamic_app_capability_writer_epochs
dynamic_app_capability_audit
dynamic_app_capability_grant_quarantine
dynamic_app_capability_grant_mappings
dynamic_app_capability_grants
```

## 3. Отсутствие дублирующих `__tablename__`

Инвариант: для каждой физической таблицы в `Base.registry.mappers` ровно один mapper.

| Таблица | Ожидаемый единственный владелец |
|---|---|
| `dynamic_app_capability_grants` | `models.canonical_grants.CanonicalGrantRow` |
| `dynamic_app_capability_grant_mappings` | `CanonicalGrantMappingRow` |
| `dynamic_app_capability_grant_quarantine` | `CanonicalGrantQuarantineRow` |
| `dynamic_app_capability_audit` | `CanonicalGrantAuditRow` |
| `dynamic_app_capability_writer_epochs` | `CanonicalGrantWriterEpochRow` |
| `m06_canonical_grant_promotion_evidence` | `M06CanonicalGrantPromotionEvidenceRow` |
| `dynamic_app_grants` | `models.dynamic_apps.AppGrantRow` (projection, другая таблица) |

Остаток отсутствует: файл `models/dynamic_app_grants.py` удалён в задаче
MIG-OWNERSHIP-CLEANUP-01, и тест `test_no_module_declares_a_canonical_table_twice`
требует пустого множества нарушителей (`offenders == {}`). `M06_RETIRED_DUPLICATES`
остаётся только исторической записью в модели и в проверках не участвует.

Fail-closed проверяется обращением к `models.DynamicAppCapabilityGrantRow`: module-level
`__getattr__` обязан поднять `AttributeError` с текстом про retired, а не вернуть класс.
Прямой `import prodavan...models.dynamic_app_grants` теперь обязан упасть
`ModuleNotFoundError` — модуля больше нет на диске.

## 4. Негативные кейсы: ожидаемое поведение БД

Кейсы сформулированы как будущие SQL-проверки. Структурная часть (схема, которая обязана
произвести такое поведение) покрыта `test_negative_contracts.py` уже сейчас.

### 4.1 Grant на чужой instance

```text
-- предусловие: instance_a принадлежит tenant A, scope project P, version V, definition D
INSERT INTO dynamic_app_capability_grants (..., tenant_id='tenant-B', plugin_instance_id='instance_a', ...)
```

Ожидание: `foreign key violation` на `fk_cap_grant_instance_exact` — не совпадает хотя бы одна
из шести колонок (`app_definition_id`, `app_version_id`, `tenant_id`, `scope_kind`, `scope_id`
или сам `plugin_instance_id`).

Варианты, которые обязаны падать так же:

| Подмена | Колонка, которая ловит |
|---|---|
| чужой tenant | `tenant_id` в composite FK |
| чужой scope (cabinet вместо project) | `scope_kind`/`scope_id` |
| instance другой версии | `app_version_id` |
| instance другой definition | `app_definition_id` |
| `scope_kind='plugin_instance'`, но `scope_id <> plugin_instance_id` | `ck_cap_grant_scope_shape` |
| `plugin_instance_id IS NULL`, `session_id` задан | `ck_cap_grant_plugin_binding` |

### 4.2 Устаревшая генерация

```text
-- epoch partition = ('tenant-A','definition-D','project','project-P') = 7
UPDATE dynamic_app_capability_grants SET revoked_at=now(), ... WHERE writer_generation = 5
```

Ожидание: 0 строк (writer сравнивает `writer_generation` с текущим epoch partition и
отказывает), плюс deny в `2026110307` policy.

Границы, которые держит **схема** этой ревизии: `writer_generation`/`grant_generation`
`NOT NULL` и `> 0`; epoch уникален на partition и его `partition_id` детерминированно
выводится из `tenant:definition:scope_kind:scope_id`; epoch привязан к реальному
`scope_registry` и `m04e_dynamic_app_definitions`.

Граница, которую схема держать **не может**: `CHECK` не читает другую таблицу, поэтому
сравнение с epoch физически невыразимо constraint'ом. Оно принадлежит trusted-writer
транзакции и policy `2026110307`. Это зафиксировано как явная граница, а не спрятано.

### 4.3 Дубль активного гранта

```text
INSERT ... (tenant, version, capability, subject, instance, scope, 4 digests)   -- ok
INSERT ... <то же самое>                                                       -- conflict
```

Ожидание: `unique_violation` на `uq_cap_grant_active_identity`.

Обязующие условия:

| Сценарий | Ожидание |
|---|---|
| повтор того же identity при `revoked_at IS NULL` | `unique_violation` |
| тот же identity, но `revoked_at` установлен у первой строки | insert разрешён (история сохранена) |
| тот же identity, истёкший `expires_at`, revoke не сделан | `unique_violation` — истечение не освобождает identity |
| тот же `tenant_id`+`idempotency_key`, другой payload | `unique_violation` на `uq_cap_grant_idempotency`; writer обязан вернуть прежний результат либо quarantine-конфликт, но не перезапись |
| instance-independent vs instance-bound строка того же capability | различаются `COALESCE(plugin_instance_id,'')`, обе допустимы |

### 4.4 Каскад против retention

```text
DELETE FROM m04e_dynamic_app_versions WHERE id = :v;   -- на версии есть грант
DELETE FROM m05_plugin_instances      WHERE id = :i;   -- на instance есть грант
DELETE FROM dynamic_app_capability_grants WHERE id = :g; -- есть audit/mapping
```

Ожидание:

| Операция | Результат |
|---|---|
| удаление version/definition/instance со ссылающимся грантом | `RESTRICT` — отказ |
| удаление гранта с audit/mapping | разрешено, но `capability_grant_id`/`target_grant_id` → `NULL`; сами evidence-строки остаются |
| удаление definition с promotion evidence | `RESTRICT` — отказ |
| любая попытка `CASCADE` | невозможна: в canonical-схеме нет `ON DELETE CASCADE` |

Дополнительно: audit/mapping/quarantine сохраняют `source_kind`, `source_id`,
`schema_version`, `source_payload_digest` и уникальность наблюдения, поэтому «удалить историю,
чтобы скрыть расхождение» не проходит даже после `SET NULL`.

## 5. Границы и риски

См. §8 `canonical-grants-ownership-decision.md`. Кратко:

- ~~cross-zone долг: устаревшие `tests/migration/model_registration/{test_metadata_registration,
  test_static_contract}.py` и `blocker.md`~~ — закрыто в MIG-OWNERSHIP-CLEANUP-01;
- ~~cross-zone долг: удаление `models/dynamic_app_grants.py`~~ — файл удалён в
  MIG-OWNERSHIP-CLEANUP-01, `ModuleNotFoundError` на прямой импорт;
- legacy `dynamic_app_grants` сохраняет `ON DELETE CASCADE` — решение вне зоны M06;
- `MATCH SIMPLE` на exact-instance FK — осознанный компромисс, закрытый checks + policy;
- дрейф денормализованного `app_id` требует writer-проверки в `2026110307`.

## 6. Порядок фактической проверки после снятия freeze

1. `alembic upgrade 2026110304` на пустой БД → `alembic check` должен не находить drift по
   этим шести таблицам (индексы/уникальности исключены из drift-сравнения в `env.py`, поэтому
   паритет индексов держится тестом `test_revision_indexes_match_metadata`).
2. `pytest tests/migration/canonical_grants` — все три файла.
3. SQL-кейсы §4 на PostgreSQL (не SQLite: нужны `jsonb_*`, regex `~`, partial index и
   `MATCH SIMPLE`).
4. После `2026110307`: повторить §4.2 с реальными сессиями tenant/trusted-writer и убедиться,
   что отсутствие контекста даёт 0 строк, а не ошибку.

# Canonical grants: ownership decision, DDL scope и приёмка

Статус: migration-stage decision + DDL artifact, MIG-DDL-M06-01, baseline 2026-11-04.
Задача: снять известный blocker «`CanonicalGrantRow` дублирует legacy
`DynamicAppCapabilityGrantRow`», зафиксировать одно owning-описание на каждую физическую
таблицу и выпустить ревизию `2026110304`.

Документ не является разрешением на применение DDL к shared environment, backfill,
cutover или снятие verification freeze. Тесты/сборки/линт/компиляция/`alembic upgrade`/
git-проверки для этой задачи намеренно **не запускались**.

Связанные контракты:

- `canonical-grant-target-ddl.md` — целевая форма canonical grant/audit/mapping/quarantine;
- `atomic-grant-projection-handoff.md` — единая транзакция grant decision → projection;
- `dynamic-grants-ownership.md` — ownership/фазы cutover и rollback;
- `dynamic-app-migration-gaps.md` §1, §8 — уже принятое ownership-решение;
- `plugin-instance-scope-decision.md` §3.3 — effective scope canonical grants;
- `migration-completion-wave-plan.md` — линейная цепочка ревизий волны 1.

## 1. Решение (подтверждено с одной мотивированной поправкой)

**Подтверждается** принятое ранее решение:

| Физическая таблица | Роль |
|---|---|
| `dynamic_app_capability_grants` | **canonical policy owner** effective authorization после bounded cutover |
| `dynamic_app_grants` | **read-only legacy compatibility projection**, никогда не authorization source |

**Поправка к формулировке blocker'а.** Blocker был описан как «`CanonicalGrantRow`
дублирует legacy `DynamicAppCapabilityGrantRow`». Это неточно и вело к неверной развилке:

- `models/dynamic_app_grants.py::DynamicAppCapabilityGrantRow` объявляет
  `__tablename__ = "dynamic_app_capability_grants"`, то есть описывает **ту же самую
  canonical-таблицу**, а не legacy projection. Это не «legacy owner», а второе, более узкое
  описание canonical-таблицы, оставшееся от pre-cutover дизайна.
- Настоящая legacy projection — `models/dynamic_apps.py::AppGrantRow` с
  `__tablename__ = "dynamic_app_grants"`: JSONB `grants`, `(app_version_id, scope_kind,
  scope_id)` uniqueness, `ON DELETE CASCADE` на версию.

Отсюда точная постановка: **дубль — это две модели на один `__tablename__`, а не две таблицы
на одну роль**. Узкая модель не может быть canonical owner, потому что в ней физически нет
`capability_key`, `subject_kind/subject_id`, `plugin_instance_id`, `grant_generation`,
`writer_generation`, четырёх payload-дайджестов и `idempotency_key`; без них canonical строка
не способна авторизовать v1 user/plugin call (`canonical-grant-target-ddl.md` §2, §3).

## 2. Выбор варианта: (a), не (b)

Задача допускала (a) — canonical-модель становится единственным описанием canonical-таблицы,
legacy-модель помечается read-only projection без DDL-претензий; либо (b) — canonical-модель
удаляется/маппится на существующую таблицу.

Выбран **(a)**. Обоснование:

1. **Физическая таблица в БД ещё не создана.** Alembic head до этой волны — `2026110202`;
   `dynamic_app_capability_grants` не существует ни в одной ревизии. Значит «duplicate
   ownership» — конфликт только в `Base.metadata`, а не в схеме. Мы выбираем описание до
   первого `CREATE TABLE`, что дешевле любого последующего `ALTER`.
2. **Узкая модель невыводима в canonical форму.** Вариант (b) означал бы маппинг
   canonical-контракта на таблицу без capability/subject/instance/generation-колонок, то есть
   либо отказ от canonical policy (недопустимо: `dynamic-grants-ownership.md` §Phase E), либо
   «догоняющий» `ALTER TABLE` сразу после создания — худший из возможных порядков.
3. **Один владелец = одна модель.** `Base.metadata` не допускает двух `Table`-объектов на одно
   имя, поэтому «пометить legacy как projection» в рамках одной metadata можно только так:
   оставить его объявление в файле как исторический артефакт, но **не импортировать** в пакет
   моделей. Именно это и сделано; удаление файла — отдельная задача зоны dynamic-app.
4. **Legacy projection остаётся ровно тем, чем должна быть** — отдельной таблицей
   `dynamic_app_grants` (`AppGrantRow`), к которой canonical-ревизия не прикасается.

Итог: единственное owning-описание `dynamic_app_capability_grants` —
`models/canonical_grants.py::CanonicalGrantRow`.

### 2.1 Карта владения (статический инвариант)

| Таблица | Owning-модель | Ревизия |
|---|---|---|
| `dynamic_app_capability_grants` | `CanonicalGrantRow` | `2026110304` |
| `dynamic_app_capability_grant_mappings` | `CanonicalGrantMappingRow` | `2026110304` |
| `dynamic_app_capability_grant_quarantine` | `CanonicalGrantQuarantineRow` | `2026110304` |
| `dynamic_app_capability_audit` | `CanonicalGrantAuditRow` | `2026110304` |
| `dynamic_app_capability_writer_epochs` | `CanonicalGrantWriterEpochRow` | `2026110304` |
| `m06_canonical_grant_promotion_evidence` | `M06CanonicalGrantPromotionEvidenceRow` | `2026110304` |
| `dynamic_app_grants` (projection) | `AppGrantRow` (зона dynamic-app) | ранее, не трогается |

Инвариант зафиксирован машинно-читаемо в модуле: `M06_TABLE_OWNERS`,
`M06_RETIRED_DUPLICATES`, `M06_LEGACY_PROJECTION`, `M06_DDL_OWNERSHIP_CASES`.

## 3. Изменения моделей

### 3.1 `models/canonical_grants.py`

1. Docstring теперь фиксирует ownership и судьбу дубля (иначе следующий агент снова
   «зарегистрирует» обе модели).
2. **Version FK усилен**: вместо `(app_version_id, app_definition_id) -> (id,
   definition_id)` используется `(app_version_id, app_definition_id, app_artifact_digest) ->
   m04e_dynamic_app_versions(id, definition_id, artifact_digest)`
   (`fk_cap_grant_version_definition_artifact`). Ключ-цель объявлен в
   `2026110302` (`uq_m04e_version_id_definition_artifact`). Это закрывает требование
   `canonical-grant-target-ddl.md` §3 правило 3 («artifact digest must equal the immutable
   version artifact digest») **на уровне БД**, а не только в адаптере, и не даёт ссылаться на
   версию другой definition одновременно с чужим artifact.
3. **Ширина `scope_id` приведена к 40** (было 80) — как у `scope_registry.scope_id` и
   `m05_plugin_instances.scope_id`, на которые указывают FK. Иначе типизированный registry-key
   сравнивался бы с ключом другой ширины.
4. **`M06CanonicalGrantPromotionEvidenceRow` получил FK** `(definition_id, tenant_id) ->
   m04e_dynamic_app_definitions(id, tenant_id)` с `RESTRICT`
   (`fk_m06_grant_promotion_definition_tenant`). Без него запись promotion evidence могла
   ссылаться на несуществующую definition и «доказывать» продвижение чужого partition.
5. Добавлены статические артефакты `M06_TABLE_OWNERS`, `M06_RETIRED_DUPLICATES`,
   `M06_LEGACY_PROJECTION`, `M06_DDL_OWNERSHIP_CASES` — review-входы, не исполняемые проверки.

`RLS_POLICY_DDL` и `M06_PRIVILEGE_INVENTORY` оставлены как есть: это инвентарь для ревизии
`2026110307`, он не исполняется ревизией `2026110304`.

### 3.2 `models/__init__.py`

- импорт `dynamic_app_grants.DynamicAppCapabilityGrantRow` заменён на импорт шести canonical
  классов; `__all__` обновлён;
- добавлен module-level `__getattr__`, который на обращение к
  `DynamicAppCapabilityGrantRow` поднимает `AttributeError` с явной причиной. Это fail-closed:
  `from ...models import DynamicAppCapabilityGrantRow` падает на импорте, а не создаёт вторую
  `Table` в metadata (что дало бы `InvalidRequestError` при импорте пакета и сломало бы
  `alembic/env.py` целиком).

Проверено интроспекцией (не verification): после правки `Base.metadata` содержит 99 таблиц,
каждая canonical-таблица ровно один раз, `Base.metadata.sorted_tables` корректно ставит
`scope_registry` и `m05_plugin_instances` перед `dynamic_app_capability_grants`.

## 4. Ревизия `2026110304_canonical_grants.py`

`revision = "2026110304"`, `down_revision = "2026110303"` — строго линейно по волне 1.

### 4.1 Что создаётся

Шесть таблиц из §2.1, ровно те, что объявлены моделями. Ничего больше: таблицы
`dynamic_app_definitions`/`dynamic_app_versions`/`dynamic_app_instances` (legacy) не
создаются и не изменяются.

### 4.2 Foreign keys

| Имя | Колонки | Цель | ON DELETE |
|---|---|---|---|
| `fk_cap_grant_version_definition_artifact` | `app_version_id, app_definition_id, app_artifact_digest` | `m04e_dynamic_app_versions(id, definition_id, artifact_digest)` | RESTRICT |
| `fk_cap_grant_definition` | `app_definition_id` | `m04e_dynamic_app_definitions(id)` | RESTRICT |
| `fk_cap_grant_definition_tenant` | `app_definition_id, tenant_id` | `m04e_dynamic_app_definitions(id, tenant_id)` | RESTRICT |
| `fk_cap_grant_scope_registry` | `scope_registry_id` | `scope_registry(id)` | RESTRICT |
| `fk_cap_grant_instance_exact` | `plugin_instance_id, app_definition_id, app_version_id, tenant_id, scope_kind, scope_id` | `m05_plugin_instances(id, definition_id, version_id, tenant_id, scope_kind, scope_id)` | RESTRICT |
| `fk_cap_grant_mapping_target` | `target_grant_id` | `dynamic_app_capability_grants(id)` | SET NULL |
| `fk_cap_audit_grant` | `capability_grant_id` | `dynamic_app_capability_grants(id)` | SET NULL |
| `fk_cap_grant_writer_epoch_definition_tenant` | `app_definition_id, tenant_id` | `m04e_dynamic_app_definitions(id, tenant_id)` | RESTRICT |
| `fk_cap_grant_writer_epoch_scope` | `scope_kind, scope_id` | `scope_registry(scope_kind, scope_id)` | RESTRICT |
| `fk_m06_grant_promotion_definition_tenant` | `definition_id, tenant_id` | `m04e_dynamic_app_definitions(id, tenant_id)` | RESTRICT |

`fk_cap_grant_instance_exact` объявлен с семантикой `MATCH SIMPLE` (значение по умолчанию):
если `plugin_instance_id IS NULL`, FK не проверяется. Это намеренно —
instance-independent declarative grant разрешён только отдельной versioned policy
(`plugin-instance-scope-decision.md` §3.3 правило 4), а `ck_cap_grant_plugin_binding`
(`plugin_instance_id IS NOT NULL OR session_id IS NULL`) не даёт такому grant'у притвориться
session-bound. Как только instance указан, проверяются **все шесть** колонок: грант на чужой
instance (другой tenant, scope, version или definition) физически невозможен.

### 4.3 Active uniqueness и индексы

```text
uq_cap_grant_active_identity  UNIQUE (
    tenant_id, app_version_id, capability_key, subject_kind, subject_id,
    COALESCE(plugin_instance_id, ''), scope_kind, scope_id,
    operations_digest, resources_digest, domains_digest, constraints_digest
) WHERE revoked_at IS NULL

ix_cap_grant_authorize        (...) WHERE revoked_at IS NULL
ix_cap_grant_validity         (tenant_id, issued_at, expires_at, revoked_at)
ix_cap_grant_instance_generation (plugin_instance_id, writer_generation, grant_generation)
                              WHERE plugin_instance_id IS NOT NULL
ix_cap_grant_source           (source_kind, source_id, schema_version)
uq_cap_grant_idempotency      UNIQUE (tenant_id, idempotency_key)
uq_cap_grant_mapping_source   UNIQUE (source_kind, source_id, canonical_schema_version)
uq_cap_audit_event            UNIQUE (event_id)
uq_cap_grant_quarantine_observation UNIQUE (source_kind, source_id, schema_version, source_payload_digest)
uq_cap_grant_writer_epoch_partition UNIQUE (tenant_id, app_definition_id, scope_kind, scope_id)
uq_cap_grant_writer_epoch_partition_id UNIQUE (partition_id)
uq_m06_grant_promotion_revision UNIQUE (tenant_id, definition_id, partition_id, revision_id)
ix_cap_grant_quarantine_open / _tenant, ix_cap_audit_tenant_time / _grant_time / _trace,
ix_cap_grant_mapping_checkpoint, ix_cap_grant_writer_epoch_lookup,
ix_m06_grant_promotion_partition_time
```

Ключевые свойства:

- **`expires_at` не входит в предикат** active-uniqueness: expiry — read-time deny
  (`now >= expires_at`), а moving clock в индексе сделал бы идентичность недетерминированной.
  Истёкшая строка остаётся evidence и остаётся уникальной, пока её не закроет явный
  revoke/supersede.
- **`COALESCE(plugin_instance_id, '')`** — sentinel только для выражения уникальности;
  authorization по-прежнему требует instance-bound path или отдельного policy-разрешения.
- `uq_cap_grant_idempotency` — replay с тем же ключом возвращает исходный результат;
  тот же binding с другим `payload_digest` уходит в quarantine, а не перезаписывает строку
  (это проверяет writer, constraint лишь не даёт двум строкам делить ключ).

### 4.4 Checks

Полный набор: `ck_cap_grant_scope_kind` (`tenant|cabinet|project|plugin_instance`),
`ck_cap_grant_scope_shape` (четыре формы scope, `plugin_instance` требует
`scope_id = plugin_instance_id`), `ck_cap_grant_subject_kind`, `ck_cap_grant_plugin_binding`,
`ck_cap_grant_capability_nonempty`, `ck_cap_grant_{operations,resources,domains}_nonempty`,
`ck_cap_grant_constraints_object`, `ck_cap_grant_expiry`, `ck_cap_grant_revoke` (revoke требует
actor и непустую причину), `ck_cap_grant_generation` (`> 0`), `ck_cap_grant_identity_nonempty`,
шесть `*_sha256` checks и `ck_cap_grant_*` для mapping/quarantine/audit/epoch/promotion.

SQL NULL против JSONB `null` учтён: все JSONB-колонки `NOT NULL` с server default, checks над
`jsonb_typeof` выполняются только на непустом значении, а `COALESCE` не требуется там, где
колонка `NOT NULL` (`dynamic-app-migration-gaps.md` §4).

### 4.5 Что ревизия намеренно НЕ делает

- **Нет RLS**: ни `ENABLE`/`FORCE ROW LEVEL SECURITY`, ни `CREATE POLICY`. Это ревизия
  `2026110307`. До неё таблицы существуют, но не являются authorization source.
- **Нет привилегий и ролей** (`GRANT`/`REVOKE`), нет триггеров append-only, нет
  `trusted_grant_writer`.
- **Нет data step**: ни `INSERT`/`UPDATE`, ни backfill, ни `alembic stamp`.
- **Нет изменения legacy-таблиц**: `dynamic_app_grants` и её cascade не тронуты.
- Ревизия вызывает только `create_table`/`create_index` в `upgrade` и
  `drop_index`/`drop_table` в `downgrade`; это проверяется AST-тестом.

`downgrade()` удаляет объекты строго от детей к родителям (promotion → epochs → audit →
quarantine → mappings → grants). Он допустим только до появления tenant data; после импорта
evidence rollback — forward repair (`M06_ROLLBACK_GATE`).

## 5. Мотивированные отклонения от `canonical-grant-target-ddl.md` §3

| Пункт целевого DDL | В ревизии | Причина |
|---|---|---|
| FK на `dynamic_app_versions` / `dynamic_app_definitions` / `dynamic_app_instances` | FK на `m04e_dynamic_app_versions` / `m04e_dynamic_app_definitions` / `m05_plugin_instances` | Документ писался до foundation-ревизий. Промотированная identity волны 1 — `m04e_*` и `m05_*` (`2026110301`–`2026110303`). Ссылаться на legacy-таблицы значило бы строить canonical authorization на непромотированном источнике и создать второй ownership-граф. Требования §3 (same-definition proof, exact instance, artifact) сохранены и усилены. |
| `fk_cap_grant_version_definition` (2 колонки) | `fk_cap_grant_version_definition_artifact` (3 колонки) | Требование §3 правило 3 («artifact digest must equal the immutable version artifact digest») перенесено из адаптера в БД; ключ-цель уже существует (`uq_m04e_version_id_definition_artifact`). Усиление, не ослабление. |
| `scope_id text` без фиксированной ширины | `String(40)` | Совпадение с `scope_registry.scope_id`/`m05_plugin_instances.scope_id`, на которые указывают FK. |
| `ck_cap_grant_revoke ... revoke_reason IS NOT NULL` | `... length(btrim(revoke_reason)) > 0` | Пустая причина — не причина; `NOT NULL` пропускал бы `''`. |
| `id bigserial` для evidence-таблиц | `BigInteger` + `Identity()` | Соответствует остальным ревизиям репозитория и не создаёт sequence-owner конфликтов при downgrade. |
| Отдельная таблица `dynamic_app_capability_writer_epochs` и `m06_canonical_grant_promotion_evidence` не описаны в §3 | Созданы | Они объявлены owning-моделями и без DDL оставались бы «висячими» метаданными; epoch — единственный durable носитель fencing-состояния (`plugin-instance-scope-decision.md` §3.3 правило 7). |

## 6. Согласование с `atomic-grant-projection-handoff.md`

Handoff требует, чтобы grant decision, mapping, quarantine, projection и audit писались
**одной транзакцией** и чтобы «no caller-visible intermediate commit» было гарантировано БД.
Что даёт эта ревизия:

- все пять структур живут в одной схеме и не имеют межуровневых `CASCADE`, поэтому частичный
  commit не «упрощается» каскадом;
- `fk_cap_grant_mapping_target`/`fk_cap_audit_grant` — `SET NULL`, то есть evidence переживает
  удаление гранта (retention §5 целевого DDL);
- `uq_cap_grant_mapping_source` и `uq_cap_grant_quarantine_observation` не дают двум
  наблюдениям одного source с разным digest'ом тихо сосуществовать: второй digest — отдельная
  строка quarantine, а не перезапись;
- `uq_cap_grant_idempotency` + active-uniqueness дают CAS-точку вместо `SELECT`-then-`INSERT`.

Чего ревизия **не** даёт и что остаётся gate'ом `2026110307` + application layer: запрет
`UPDATE`/`DELETE` на audit для API-ролей, deny-by-default при отсутствии tenant context,
сравнение `writer_generation` с текущим epoch partition. `CHECK` не может читать другую
таблицу, поэтому stale-generation denial физически невыразим constraint'ом — это policy +
trusted-writer transaction. Ревизия поставляет для них состояние (таблица epoch, положительные
generations, уникальность partition) и не притворяется, что этого достаточно.

## 7. Приёмочные артефакты

Каталог `apps/api/tests/migration/canonical_grants/`. Все тесты **написаны, не исполнены**
(verification freeze).

### 7.1 `test_ownership_metadata.py` — паритет владения

| Проверка | Что доказывает |
|---|---|
| `test_each_canonical_table_has_exactly_one_owning_model` | для каждой из 6 таблиц: `__tablename__` совпадает, `Base.metadata.tables[name] is model.__table__`, ровно один mapper на таблицу |
| `test_canonical_module_declares_only_the_owned_tables` | модуль объявляет ровно 6 `__tablename__`, ни одного лишнего |
| `test_metadata_has_no_duplicate_tablename_claims` | в `Base.registry.mappers` нет таблицы с двумя владельцами |
| `test_no_module_declares_a_canonical_table_twice` | ни один модуль не объявляет каноническую таблицу дважды; `offenders == {}`, файла-дубля нет на диске |
| `test_models_package_does_not_import_the_retired_duplicate_module` | пакет не импортирует дубль, имени нет в `__all__`, обращение падает `AttributeError(retired)`, прямой импорт модуля — `ModuleNotFoundError` |
| `test_legacy_dynamic_app_grants_projection_is_a_separate_read_only_table` | `AppGrantRow` — другая таблица, без canonical-колонок и без FK на canonical grants |

### 7.2 `test_alembic_parity.py` — паритет ревизии с metadata

Ревизия проигрывается против записывающего двойника `alembic.op`: `create_table`/
`create_index` материализуются как настоящие `Table`/`Index` в изолированном `MetaData`
(без БД, без диалекта, без DDL).

| Проверка | Что доказывает |
|---|---|
| `test_revision_identity_matches_the_wave_plan` | `2026110304` ← `2026110303`, без branch labels |
| `test_revision_calls_only_table_and_index_operations` | AST: только `create_table`/`create_index`/`drop_table`/`drop_index`, никакого `op.execute` |
| `test_revision_creates_exactly_the_owned_tables` | набор таблиц ревизии == `M06_TABLE_OWNERS`, лишних op-вызовов нет |
| `test_revision_table_matches_metadata` (×6) | колонки/типы/nullable/server default/PK/identity, UNIQUE, FK (колонки, цель, `ON DELETE`), CHECK-выражения — побайтово равны metadata |
| `test_revision_indexes_match_metadata` (×6) | выражения, `unique`, partial `WHERE` совпадают |
| `test_downgrade_drops_only_this_revision_objects` | порядок drop'ов children→parents и полный список индексов |

Паритет подтверждён и вручную (read-only интроспекция тех же сигнатур): расхождений 0.

### 7.3 `test_negative_contracts.py` — негативные и граничные кейсы

| Кейс | Структурное доказательство |
|---|---|
| **grant на чужой instance** | `fk_cap_grant_instance_exact` — шесть колонок против `uq_m05_plugin_instance_grant_identity`; одно-колоночных FK на `plugin_instance_id`/`app_version_id` нет; `ck_cap_grant_scope_shape` требует `scope_id = plugin_instance_id` для `plugin_instance`-scope; `ck_cap_grant_plugin_binding` не даёт instance-independent строке притвориться session-bound |
| **устаревшая генерация** | `grant_generation`/`writer_generation` `NOT NULL` + `> 0`; epoch таблица уникальна по partition и `partition_id = tenant:definition:scope_kind:scope_id`; сравнение с epoch — policy `2026110307`, и это зафиксировано как граница (`test_stale_generation_denial_is_documented_as_policy_owned`) |
| **дубль активного гранта** | `uq_cap_grant_active_identity` — полный authorization tuple + `COALESCE(plugin_instance_id,'')`, `WHERE revoked_at IS NULL`, без `expires_at`; `uq_cap_grant_idempotency (tenant_id, idempotency_key)` |
| **каскад против retention** | ни одна canonical-таблица не имеет `ON DELETE CASCADE`; все grant-FK `RESTRICT`; audit/mapping `SET NULL`; promotion evidence `RESTRICT`; `ck_m06_grant_no_positive_writes` |
| **version другой definition / другой artifact** | трёхколоночный FK на `m04e_dynamic_app_versions(id, definition_id, artifact_digest)` |
| **сырой payload в quarantine** | таблица имеет только `redacted_metadata`; колонок `payload`/`token`/`secret`/`arguments` нет |

`acceptance.md` в том же каталоге описывает ожидаемое поведение БД для каждого кейса и порядок
их фактической проверки после снятия freeze.

### 7.4 Что осталось непокрытым этим артефактом

- Фактическое поведение Postgres (insert/update/delete, `MATCH SIMPLE`, partial index
  conflict, `RESTRICT` из-под каскадного удаления родителя) — только после `2026110307` и
  снятия freeze.
- RLS-кейсы (`test_acceptance_contract.py` M06) остаются за ревизией `2026110307`.

## 8. Риски и открытые пункты

1. ~~**Cross-zone follow-up: устаревшие тесты `tests/migration/model_registration/`.**~~
   **Закрыто в MIG-OWNERSHIP-CLEANUP-01.** Гейты переписаны: владелец
   `dynamic_app_capability_grants` — `CanonicalGrantRow`, все шесть canonical-классов
   зарегистрированы, `canonical_grants` присутствует в импортах, retired-имя падает через
   `AttributeError`, `blocker.md` переписан как resolved.
2. ~~**Cross-zone follow-up: удаление `models/dynamic_app_grants.py`.**~~ **Закрыто в
   MIG-OWNERSHIP-CLEANUP-01.** Файл удалён; проверка ужесточена до `offenders == {}`, прямой
   импорт модуля даёт `ModuleNotFoundError`. Число таблиц не изменилось (102 → 102): модуль
   никогда не импортировался, исчезла только латентная декларация.
3. **Legacy projection имеет `ON DELETE CASCADE` на версию** (`AppGrantRow`,
   `fk_dynamic_app_grant_version_definition`). Это противоречит retention-требованию
   (`dynamic-app-migration-gaps.md` §3.2). Вне зоны M06: таблица не canonical-таблица, и
   менять её cascade в этой ревизии значило бы менять чужой ownership. Требует отдельного
   решения в зоне dynamic-app до cutover.
4. **Цепочка ревизий сходится.** На момент финальной сверки на диске присутствуют
   `2026110301`–`2026110303` и `2026110305`–`2026110307`; `2026110305` объявляет
   `down_revision = "2026110304"`, то есть линейный порядок волны 1
   (`0202 → 0301 → 0302 → 0303 → 0304 → 0305 → 0306 → 0307`) не форкнут. Проверены
   обязательные ключи-цели моего revision'а в родительских файлах:
   `uq_scope_registry_kind_id (scope_kind, scope_id)` в `2026110301`,
   `uq_m04e_definition_id_tenant` и `uq_m04e_version_id_definition_artifact` в
   `2026110302`, `uq_m05_plugin_instance_grant_identity` в `2026110303`. Все три совпадают
   по колонкам и порядку с моими `ForeignKeyConstraint`.
5. **Согласование с ревизией `2026110307`.** Её RLS-реестр
   (`persistence/rls/policies.py`) уже содержит `TablePolicySpec` для всех шести моих таблиц с
   `owner_revision="2026110304"`, и ожидаемые tenant-колонки существуют:
   `dynamic_app_capability_grants.tenant_id/cabinet_id/project_id`,
   `dynamic_app_capability_grant_mappings.target_grant_id` (parent hop),
   `dynamic_app_capability_grant_quarantine.resolved_tenant_id`,
   `dynamic_app_capability_audit.tenant_id`, `dynamic_app_capability_writer_epochs.tenant_id`,
   `m06_canonical_grant_promotion_evidence.tenant_id`. Пересечения DDL нет: та ревизия не
   создаёт таблиц, эта — не создаёт политик.
5. **`MATCH SIMPLE` на `fk_cap_grant_instance_exact`** — осознанный компромисс: полный
   `MATCH FULL` запретил бы instance-independent grants, которые `plugin-instance-scope-decision.md`
   §3.3 правило 4 допускает по отдельной policy. Риск «NULL instance + конкретный session»
   закрыт `ck_cap_grant_plugin_binding`, риск «NULL instance как wildcard» — policy слоем.
6. **`app_id` — денормализованное lookup-поле**, ownership доказывает definition FK.
   Дрейф `app_id` относительно `m04e_dynamic_app_definitions.app_id` не запрещён constraint'ом
   (нужен составной ключ, которого у definitions нет). Требует writer-проверки или trigger'а в
   `2026110307`; зафиксировано как остаточный риск, а не как закрытый пункт.

## 9. Verification freeze

Тесты, сборки, линтеры, форматтеры, компиляция, `alembic upgrade`/`check`, DDL к живой БД и
git-проверки для этой задачи **не запускались**. Разрешённая интроспекция использована только
для импорта моделей и печати metadata (сверка сигнатур, сортировка `sorted_tables`).
Приёмка выше — артефакты для последующей формальной проверки, а не заявка на зелёный прогон.

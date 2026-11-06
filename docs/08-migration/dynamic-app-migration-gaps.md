# Dynamic app migration gaps

Статус: migration-stage design/gap artifact, только документация. Дата baseline: 2026-11-01.

Документ фиксирует порядок переноса dynamic app definition/version/grant/instance в PostgreSQL.
Он не является Alembic-ревизией и не меняет ORM или существующие core-документы. Источником DDL
после согласования остаются SQLAlchemy-модели; migration-файлы должны быть сгенерированы,
пересмотрены и применены отдельным migration PR.

## 1. Scope и инварианты

Целевая модель состоит из четырёх сущностей:

- `dynamic_app_definitions` — стабильная identity приложения в tenant/scope и его lifecycle;
- `dynamic_app_versions` — append-only manifest/artifact snapshots;
- `dynamic_app_grants` — grant decision для версии и scope;
- `dynamic_app_instances` — scoped installation/deployment выбранной версии.

В коде также присутствует отдельная capability-oriented таблица
`dynamic_app_capability_grants`. По `dynamic-grants-ownership.md` именно она является
canonical owner effective authorization после cutover; `dynamic_app_grants` остаётся
read-only legacy compatibility projection до отдельной cleanup-ревизии. Это не два
одновременно действующих policy owner: новые решения и записи идут через canonical table,
а legacy JSONB не должен использоваться для authorization. До DDL всё ещё нужно закрыть
mapping/protocol gap: текущая canonical ORM-строка не имеет `capability`/`name` key и
простым преобразованием `methods -> operations` не сохраняет идентичность v1 capability.
Нельзя импортировать такую запись положительно, пока не утверждены one-row-per-capability
key/constraint (или эквивалентное namespace-правило), mapping всех constraints и
compatibility mapping/audit.

Общие инварианты:

1. Definition — стабильный идентификатор; version и artifact immutable после публикации.
2. Manifest — requested contract, не authorization grant. Effective grant проверяется на
   gateway при каждом вызове и сохраняет audit/revocation history.
3. Published definition всегда указывает на существующую `current_version` той же definition.
4. Version, grant и instance не могут пересекать `app_definition_id`; простого FK на
   `app_version_id` недостаточно. Для canonical `dynamic_app_capability_grants` нужно
   дополнительно доказать, что `app_version_id` разрешается в правильную definition и
   tenant: текущая ORM-модель имеет только простой FK на version и не enforce-ит эту
   composite consistency.
5. Rollback создаёт новую операционную запись/переход к старому immutable snapshot, а не
   переписывает manifest, digest или историю grant.
6. V1 `GrantSet` сохраняет capability `name`, decision и constraints (`resources`,
   `methods`, `domains`, `eventTypes`, `maxBytesPerRequest`). Denied entries не являются
   positive grants; отсутствие constraint не означает wildcard. Если target schema не
   сохраняет capability identity и enforceable constraints, запись quarantine, а не allow.
7. Scope-поля (`tenant`, `cabinet`, `project`) сейчас полиморфны. Обычный FK по `scope_id`
   невозможен без отдельного scope registry; ownership/RLS должны быть частью cutover.
8. Disable/revoke не означает физическое удаление: audit, grants и deployment provenance
   удерживаются до retention boundary.

## 2. Lifecycle transition matrix

Статусы сейчас используют набор `draft | published | disabled | revoked`. Матрица ниже
описывает допустимые переходы и обязательные данные. Запрет перехода должен быть явным, а не
результатом отсутствующей строки или невалидного JSONB.

| Объект | Начальное состояние | Допустимые переходы | Предусловия/эффект | Rollback semantics |
|---|---|---|---|---|
| Definition | `draft` | `draft -> published` | `current_version_id` задан и указывает на version той же definition; version опубликована/валидирована; scope принадлежит tenant | Не возвращать published definition в draft ради отката; создать/зафиксировать новый transition к предыдущей версии |
| Definition | `published` | `published -> disabled` | серверная операция, причина и actor в audit; новые grants/installs блокируются | Existing instances не удаляются; их перевод/дренирование отдельная операция |
| Definition | `disabled` | `disabled -> published` | policy re-enable, current version всё ещё существует и не revoked; повторная проверка grants | Возврат к прежней immutable version допустим только через explicit publish transition |
| Definition | `published/disabled` | `published/disabled -> revoked` | irreversible security/product decision, audit reason; new access denied | Только forward repair/new definition; downgrade delete запрещён |
| Definition | `draft` | `draft -> revoked` | cleanup до публикации, audit желательно сохранять | Можно удалить только orphan rows после retention policy; не удалять referenced versions |
| Version | `draft` | `draft -> published` | manifest schema/protocol, artifact digest и requested capabilities валидны; digest immutable; definition существует | Published version не редактируется; rollback выбирает её как target |
| Version | `published` | `published -> disabled` | version больше не выдаётся для новых grants/instances; existing instance policy явно определена | Instance переводится на прежнюю published version отдельным operation |
| Version | `published/disabled` | `published/disabled -> revoked` | security revoke; все grants deny/revoke; instances stop/drain policy выполнена или quarantined | Не восстанавливать revoked version; publish replacement snapshot |
| Version | `draft` | `draft -> revoked` | invalid/quarantined artifact | Удаление допустимо только если нет grant/instance/rollback references |
| Grant | absent | `absent -> issued` | referenced version/definition pair существует; scope принадлежит tenant; requested capability subset разрешённой policy; expiry > issue | Grant row не переписывать; revoke/expire сохраняет решение и actor |
| Grant | `issued` | `issued -> expired` | наступил `expires_at`; read path должен deny без удаления строки | Не продлевать историческую строку; выдать новый grant с новым issue/audit |
| Grant | `issued` | `issued -> revoked` | actor/reason обязательны; revoke timestamp >= issue | Forward-only: повторная выдача — новая строка/новый idempotency decision |
| Grant | `expired/revoked` | `expired/revoked -> issued` | запрещено; новый grant с новым identity | Старый grant остаётся evidence |
| Instance | `draft` | `draft -> published` | definition/version/grant доступны для instance scope; materialization/runtime ref подготовлены | Не делать implicit install во время schema backfill |
| Instance | `published` | `published -> disabled` | stop/drain/kill policy и audit; runtime ref сохраняется для forensics | Re-enable только explicit compatibility check и valid grant |
| Instance | `published/disabled` | `published/disabled -> revoked` | version или deployment security revoke; runtime stopped/quarantined | Новый instance/deployment на replacement version; не переписывать старый row |
| Instance | `draft` | `draft -> revoked` | failed provisioning or rejected package | Delete только orphan draft после проверяемого retention окна |

`current_version_id` — pointer на serving default, а не журнал rollback. Rollback provenance
(`rollback_from_version_id`, `rollback_to_version_id`, reason/time) должно быть интерпретируемо
как transition event: `from` и `to` принадлежат той же definition, а target version остаётся
immutable. Для production-grade audit рекомендуется отдельная transition/event table; если
её пока нет, нельзя считать две nullable rollback-ссылки полной историей.

## 3. FK cycle и `use_alter`

### 3.1 Циклы

Есть два уровня связей:

```text
definition 1 -> N versions
    definition.current_version_id -> version.id       (cycle)
version.rollback_from/to_version_id -> version.id     (self references)
version <- grants / instances                         (composite version+definition FK)
```

`dynamic_app_definitions` нельзя создать с немедленно проверяемым FK на
`dynamic_app_versions`, если versions одновременно требуют FK на definitions. В SQLAlchemy
это должен быть явно названный constraint с `use_alter=True` (либо эквивалентный ручной
`ALTER TABLE ... ADD CONSTRAINT` в отдельной Alembic-операции). Не полагаться на порядок,
который случайно выберет autogenerate.

### 3.2 Рекомендуемый create order

1. Создать `dynamic_app_definitions` без `fk_dynamic_app_definition_current_version`;
   `current_version_id` nullable.
2. Создать `dynamic_app_versions` с FK `app_definition_id -> definitions.id`.
3. Создать self/composite rollback FKs version-to-version после создания уникального ключа
   `(id, app_definition_id)`. На первом этапе rollback columns nullable.
4. Создать legacy `dynamic_app_grants` и `dynamic_app_instances` после versions,
   используя composite FKs `(app_version_id, app_definition_id) -> (id, app_definition_id)`.
   Создать canonical `dynamic_app_capability_grants` также после versions, но до backfill/
   cutover; его простой ORM FK на `dynamic_app_versions.id` не доказывает same-definition
   consistency и не заменяет ownership validation. Canonical table должна быть единственным
   policy owner после cutover, legacy grants — read-only projection.
5. Создать compatibility mapping/quarantine/audit structures до import, если они не входят
   в отдельную data job; mapping должен быть idempotent по `(legacy_grant_id,
   canonical_schema_version)` и хранить source/target digests.
6. Backfill/validate definition current pointers, version pairs и canonical grant mappings.
7. Отдельным `ALTER TABLE` добавить `fk_dynamic_app_definition_current_version` с
   `ON DELETE RESTRICT`; для невалидных исторических данных сначала quarantine/repair.
8. Добавить/валидировать status и JSONB checks, затем включить NOT NULL/tightening только
   после успешного backfill.

`use_alter=True` решает DDL dependency cycle, но не data consistency. Это относится не
только к `fk_dynamic_app_definition_current_version`, но и к self/composite rollback FKs:
их имена, `use_alter`/отдельный `ALTER` и downgrade order должны быть заданы явно, иначе
autogenerate может оставить порядок случайным. Для legacy grants/instances composite FK
предотвращает ссылку на version другой definition; для canonical grants текущий простой FK
только по `id` оставляет cross-definition риск и требует adapter/ownership validation либо
отдельного reviewed composite key. Также canonical `dynamic_app_capability_grants` сейчас
имеет `ON DELETE CASCADE` на version; до DDL нужно заменить/обосновать это с учётом retention
canonical grants и audit provenance (обычный cleanup definition/version не должен молча
удалять authorization evidence).

### 3.3 Drop/downgrade order

Удаление идёт строго обратно: transition/audit and grants/instances references -> rollback
constraints -> current-version FK -> versions -> definitions. `ON DELETE CASCADE` допустим
для derived grant/instance rows только если retention и audit policy это прямо разрешают;
для published versions предпочтителен `RESTRICT`. Operational rollback не должен быть
schema downgrade.

## 4. PostgreSQL JSONB check migration caveats

Текущие checks используют PostgreSQL JSONB operators/functions, среди них:

- `manifest->>'protocol' = '1.0'`;
- `manifest ? 'appId'` и обязательные manifest keys;
- `jsonb_typeof(value) = 'array' AND jsonb_array_length(value) > 0` для grants.

Перед добавлением checks нужно учесть:

1. `CHECK` считается успешным, когда выражение `TRUE` **или NULL**. Выражение с nullable
   JSONB/path может поэтому пропустить malformed row. Для обязательного поля сначала делать
   `NOT NULL`, а для path проверять `COALESCE(..., false)`/явную null-ветку.
2. `jsonb_typeof('null'::jsonb)` возвращает `null`, а SQL `NULL` и JSONB `null` — разные
   состояния. `jsonb_array_length` нельзя вызывать до безопасного type guard; malformed
   legacy values нужно quarantine, а не кастовать вслепую. Для текущих ORM checks над
   `operations/resources/domains` нужно явно учесть SQL NULL/JSON null и использовать
   `COALESCE(..., false)` либо staged `NOT NULL` перед `jsonb_typeof`; иначе PostgreSQL
   `CHECK` может принять NULL как успешное условие.
3. `server_default='{}'/'[]'` должен быть PostgreSQL-совместимым JSONB выражением и не должен
   превращать отсутствие requested capabilities в разрешение. Пустые arrays и unknown keys
   должны иметь отдельную policy semantics.
4. Проверки protocol/required keys валидируют форму, но не полную schema, capability IDs,
   digest linkage или canonical ordering. Полная validation остаётся domain/service gate.
5. `ADD CONSTRAINT ... NOT VALID` позволяет staged rollout для большой таблицы; после repair
   выполнить `VALIDATE CONSTRAINT` отдельным контролируемым шагом. Это не заменяет row-level
   backfill и не делает constraint пригодным для новых invalid writes без корректного DDL.
6. Нельзя рассчитывать на SQLite или generic Alembic emulation: `?`, `->>`, regex operator
   и `jsonb_*` требуют PostgreSQL migration target. CI позже должен проверять именно PostgreSQL.
7. JSONB GIN index не является заменой check и не нужен для первичного cutover без измеренного
   query path. Indexes/checks, добавленные одним большим DDL, могут создать lock budget risk.

## 5. Alembic M-order

Ниже логический M-order внутри migration stage. Это не готовые revision IDs и не повод
создавать branch heads. Каждая ревизия должна иметь одну линейную `down_revision` цепочку
от актуального repository head.

| Порядок | Логическое имя | Содержание | Data/backfill gate |
|---:|---|---|---|
| M00 | `dynamic_app_anchors` | Проверить tenant/scope/audit conventions; зарегистрировать metadata; создать только общие prerequisites | Inventory definitions/versions/grants/instances и orphan/cross-scope counts |
| M01 | `dynamic_app_definitions` | Создать definitions, nullable `current_version_id`, lifecycle/scope checks и indexes; current FK пока не добавлять | Нет destructive backfill; legacy source остаётся read-only/reference |
| M02 | `dynamic_app_versions` | Создать versions, definition FK, unique `(definition_id, manifest_version)`, unique `(definition_id, digest)`, `(id, definition_id)` key | Import immutable snapshots; invalid manifests в quarantine |
| M03 | `dynamic_app_version_rollback_refs` | Добавить nullable rollback columns и named self/composite FKs; pair check после cleanup | Проверить same-definition pairs и source provenance |
| M04 | `dynamic_app_grants_legacy_and_canonical` | Создать legacy `dynamic_app_grants` projection и canonical `dynamic_app_capability_grants`; canonical uniqueness/mapping/audit/quarantine boundary must precede canonical writes; do not claim simple FK is enough for same-definition ownership | Backfill only auditable, representable capability grants; protocol entries missing capability identity or enforceable constraints denied/quarantined; legacy remains decision owner until cutover |
| M05 | `dynamic_app_instances` | Создать instances, composite version-definition FK, status/scope indexes, rollback columns | Backfill installations only with valid version and explicit scope |
| M06 | `dynamic_app_json_checks` | Add/validate manifest/grant JSONB checks, defaults and NOT NULL tightening in staged operations; include protocol grant identity/constraint mapping policy | Repair/quarantine invalid JSONB and unrepresentable GrantSet entries; `VALIDATE CONSTRAINT` evidence |
| M07 | `dynamic_app_current_version_fk` | Backfill/validate current pointers; add `use_alter` FK `definition.current_version -> version` with RESTRICT | Every published definition has same-definition published current version |
| M08 | `dynamic_app_cutover_markers` | Record source/target mapping, quarantine and parity checkpoints; mark canonical ownership per tenant/app/scope; retain legacy projection through rollback window | Dual-read parity and rollback checkpoint; no silent legacy deletion or dual policy owner |

Если capability table `dynamic_app_capability_grants` остаётся отдельной canonical table, её
нужно разместить после M02 и до cutover с явным relation to definition/version; если она
заменяется `dynamic_app_grants`, это отдельный compatibility migration, а не rename в
autogenerate без data mapping.

## 6. Backfill plan

Backfill выполняется отдельным явно названным Alembic data step или управляемой job, а не
скрывается в autogenerate DDL.

1. **Inventory:** сохранить counts, source IDs, scope ownership, lifecycle, digest и
   current-version references. Зафиксировать orphan versions, grants без definition,
   instances без valid version и rollback pairs across definitions.
2. **Definitions:** импортировать stable identity с `status=draft` по умолчанию, если
   published state нельзя доказать. Не заполнять current pointer догадкой; сохранить source
   pointer/provenance.
3. **Versions:** импортировать append-only snapshots по `(definition, manifest_version)` и
   digest. Повторный запуск идемпотентен по source ID/content digest. Никакого overwrite
   опубликованного manifest.
4. **Rollback refs:** переносить только пары, которые существуют и принадлежат одной
   definition. Остальные строки quarantine с reason; не подставлять latest/first version.
5. **Grants:** импортировать только grants с однозначным tenant/scope и valid version.
   Empty/invalid capability arrays не становятся allow-all; они reject/quarantine.
6. **Instances:** импортировать только установки с valid version и проверенным scope. Если
   runtime deployment не может быть подтверждён, ставить `disabled`/`draft` согласно policy,
   но не `published`.
7. **Current pointers:** после versions и backfill выбрать current только из подтверждённых
   published versions; затем добавить current FK и enforce published check.
8. **Parity marker:** записать source counts, imported counts, quarantine counts и digest
   mismatch. Cutover запрещён при неизвестных расхождениях.

Backfill должен быть chunked/idempotent, с bounded locks и resumable checkpoint. Секреты,
raw tokens и artifact bytes в audit/backfill logs не копируются; сохраняются references/digest.

## 7. Rollback и failure handling

### DDL failure

Каждая DDL-ревизия должна быть повторяемой по состоянию Alembic и оставлять named objects
либо полностью созданными, либо в recoverable state. Non-transactional/external artifact
операции вынести из Alembic в job с marker. Не выполнять shared-env ручные `ALTER`, `stamp`
или `kubectl exec` migration.

### Data/backfill failure

Остановить writer/import job, сохранить checkpoint и quarantine. Новая схема может оставаться
пустой/частично импортированной при выключенных readers; не удалять исходные rows и не
переписывать immutable snapshots. Исправление — forward migration/job с тем же idempotency
key.

### Application cutover rollback

До read-authority boundary: отключить target reader/writer и вернуться к legacy read path,
сохранив target snapshots, grants и audit. После boundary: не считать legacy mirror
rollback authority; остановить promotion, оставить target authoritative, исправить через
forward reconciliation. Runtime rollback переключает instance/current serving pointer на
предыдущую valid version и записывает transition, не меняя version payload.

### Schema downgrade

Downgrade допустим только до появления tenant data/references или после export/retention
preflight. Безопасный operational rollback — feature flag, reader/writer routing и forward
repair. Destructive downgrade обязан сначала удалить child references, проверить counts,
сохранить export/quarantine и иметь отдельное approval; удаление published versions,
grants или instance history в обычном downgrade запрещено.

## 8. Open gaps before implementation

- Зафиксировать уже принятое ownership-решение: `dynamic_app_capability_grants` —
  canonical policy owner, `dynamic_app_grants` — read-only legacy projection; закрыть
  migration mapping, capability identity/constraint representation и audit semantics.
- Определить scope registry/tenant FKs или формально принять policy-only polymorphic scope;
  отдельно определить subject/instance binding, поскольку gateway contract проверяет
  `subject_id`, а обе persistence grant-модели сейчас его не хранят.
- Исправить/обосновать canonical grant version FK (same-definition composite consistency)
  и `ON DELETE CASCADE` против retention/audit требований.
- Уточнить, является ли rollback pair достаточным audit trail либо нужна transition table.
- Утвердить exact lifecycle transition policy для existing instances при disable/revoke.
- Согласовать quarantine/retention, chunk size, lock budget и parity thresholds.
- Создать Alembic chain только после актуального head inventory и регистрации всех models;
  staged PostgreSQL JSONB checks должны учитывать SQL NULL/JSON null и безопасный type guard.

До закрытия этих gaps нельзя применять dynamic-app DDL на shared environment. Verification
freeze сохраняется: tests/build/lint/compile/git checks для этой документационной задачи
намеренно не выполнялись.

# Кросс-ревиз DDL-цепочки `2026110301`–`2026110311`

**Task ID:** MIG-REVIEW-DDL-01 · **Статус:** done (ревиз завершён, дефекты задокументированы)
**Тип работы:** ревиз, не правка. Ни одна ревизия, модель, RLS-реестр и чужой тест не изменялись.
**Дата:** 2026-11-04.

Девять ревизий (фактически одиннадцать файлов `2026110301`…`2026110311`) писали разные агенты
параллельно. Этот документ — сквозная статическая проверка цепочки: форма графа, паритет с
моделями, межревизионные ссылки, дубли, downgrade, RLS-покрытие. Основной результат — таблица
«проверка → результат → локация», затем дефекты по severity и список подтверждённых инвариантов.

## Метод (и что он НЕ включает)

Проверка выполнена статически, без БД и без прогона миграций:

1. **AST по всем файлам** `apps/api/alembic/versions/*.py` — граф ревизий, `revision` /
   `down_revision` (включая аннотированные присваивания `revision: str = …`, которые есть в
   legacy-файлах и которые ломают наивный парсер).
2. **Database-free replay** через существующий harness
   [`harness.py`](../apps/api/tests/migration/ddl_cross_review/harness.py): подмена `alembic.op`
   рекордером, таблицы материализуются как реальные `sqlalchemy.Table` в изолированном
   `MetaData`. Соединения нет (`get_bind()` намеренно падает), alembic-контекст фиктивный
   (`as_sql=True`), `alembic upgrade`/DDL к БД не выполняются.
3. **Импорт metadata** (`prodavan.infrastructure.persistence.models.Base.metadata`, 104 таблицы) и
   RLS-реестра (`persistence/rls/policies.py`), сравнение объявленного DDL с моделями тем же
   SQLAlchemy-рендерингом.
4. Ручное чтение всех одиннадцати ревизий, `models/*`, `rls/policies.py` и релевантных
   legacy-ревизий (`2026082306_projects`, `2026082702_cabinet_grants`).

**Не запускались:** `pytest`, `alembic upgrade/downgrade/check`, любые DDL к БД, `pnpm build`,
`tsc`, линтеры/форматтеры, git-команды. Verification freeze соблюдён.

**Затронутые файлы (только своя зона):**

* `docs/08-migration/ddl-chain-cross-review.md` — этот отчёт (новый);
* `apps/api/tests/migration/ddl_cross_review/test_chain_contract.py` — контрактный тест (новый).

## Результат: проверка → результат → локация

| № | Проверка | Результат | Локация |
|---|----------|-----------|---------|
| 1 | Цепочка `0301→0302→…→0311` непрерывна | **OK.** 202 ревизии в `alembic/versions`, один корень `stub_bootstrap`, один head `2026110311`, форков нет, отсутствующих родителей нет | `apps/api/alembic/versions/` (AST по всем файлам) |
| 2 | `down_revision` каждого звена = предыдущий | **OK.** `0301→2026110202`, `0302→0301`, …, `0311→0310`; `branch_labels`/`depends_on` пусты у всех 11 | заголовки ревизий |
| 3 | Паритет колонок (имя/тип/nullable/server_default) для 38 созданных таблиц | **OK.** 0 расхождений с `Base.metadata` | `test_column_parity_with_models` |
| 4 | Паритет PK | **OK** | `test_primary_key_parity_with_models` |
| 5 | Паритет UNIQUE (имена + порядок колонок) | **OK**, включая 5 уникальных ключей `scope_registry` (2 из `0301`, 3 из `0303`) | `0301:367-379`, `0303:88-98`; `test_unique_constraint_parity_with_models` |
| 6 | Паритет FK (локальные колонки/цель/`ondelete`/`deferrable`/`initially`) | **OK** по существу; у `0309`/`0311` расходится только *имя* констрейнта (см. m2) | `0302:190-201,258-268`, `0303:102-192`, `0304:131-182`, `0309:72-77`, `0311:107-112`; `test_foreign_key_parity_with_models` |
| 7 | Паритет текстов CHECK | **OK.** 0 расхождений (включая длинные `ck_m04e_version_manifest_*` и `ck_*_sha256`) | `0302:169-189`, `0303`, `0304`, `0306`; `test_check_constraint_parity_with_models` |
| 8 | Паритет индексов (в т.ч. partial и expression-индексы) | **OK.** `uq_cap_grant_active_identity` (`COALESCE(plugin_instance_id,'')` + `WHERE revoked_at IS NULL`), `uq_resource_binding_target_without_session`, все `…_DESC` — совпадают | `0304:266-317`, `0306:115-121`; `test_index_parity_with_models` |
| 9 | Каждый FK-таргет существует раньше или retained | **OK, 0 проблем** на 0301–0311 (таргеты в retained: `companies`, `cabinet_instances`, `cabinet_company_grants`, `projects`, `agent_runs`) | `2026082306_projects:20-43`, `2026082702_cabinet_grants:40` |
| 10 | У каждой композитной цели есть PRIMARY/UNIQUE ровно на эти колонки | **OK, 0 проблем**, в т.ч. для целей, созданных raw SQL (`ALTER TABLE … ADD CONSTRAINT … UNIQUE` в `0303`) | `0303:256-299` |
| 11 | Ни один объект не создаётся дважды | **OK для таблиц/индексов/ключей.** Дубли только у имён политик RLS: 62 имени создаются и `0307`, и `0310` — это идемпотентная переустановка (`DROP POLICY IF EXISTS` + `CREATE POLICY`), не дефект | `0307:119-135`, `0310:44-49`, `policies.py:265-308` |
| 12 | `downgrade` в обратном порядке, снимает только своё | **OK** для `0301`–`0306`, `0307`, `0309`, `0311` (набор drop == набор create, порядок таблиц — точное отражение create-порядка). `0308` — документированный no-op. `0310` — no-op при наличии upgrade-дельты (см. M3) | `0301:604-635`, `0302:271-285`, `0303:635-649`, `0304:667-715`, `0305:237-270`, `0306:461-522`, `0307:138-142`, `0309:92-95`, `0311:135-145` |
| 13 | Каждая таблица RLS-реестра есть в metadata | **OK.** 31 таблица реестра, все присутствуют в `Base.metadata` | `policies.py:333-637` |
| 14 | `owner_revision` соответствует ревизии-создателю | **OK** для всех 31 записи (`0301`×7, `0302`×3, `0303`×3, `0304`×6, `0305`×8, `0306`×2, `0309`×1, `0311`×1) | `policies.py:335-636` |
| 15 | `uncovered_relations()` пуст | **OK механически** (для metadata и для набора 38 таблиц цепочки — пусто), **но** механизм слеп к трём таблицам `0306` (см. M1) | `policies.py:734-749`, `0307:101-116` |
| 16 | Таблицы, созданные после `0307`, покрыты повторной установкой | `agent_run_leases` (`0309`) — **покрыт** `0310`. `agent_run_provider_attempts` (`0311`) — **НЕ покрыт** (B1) | `0310:44-49`, `0311:60` |
| 17 | Хрупкое: `uq_projects_scope_owner_parent_tuple` на retained `projects` (`0301`) | **OK.** Модель ставит ровно этот ключ `(id, company_id, cabinet_id)` через `_install_parent_compatibility_keys()`; `0301` создаёт его первым и снимает последним | `models/owner_registry.py:34-43`, `0301:66-70,635` |
| 18 | Хрупкое: `uq_scope_registry_m05_instance_*` (`0303` по договорённости с `0301`) | **OK.** `0301` явно делегирует их `0303`; `0303` создаёт `IF NOT EXISTS`, удаляет на downgrade; имена/колонки совпадают с моделью один-в-один, FK-таргеты валидны | `0301:43-46`, `0303:88-98,256-299,642` |
| 19 | Хрупкое: отложенный FK текущей версии + триггер (`0302`) | **OK.** `(current_version_id, id) → (id, definition_id)`, `DEFERRABLE INITIALLY DEFERRED`, добавляется после обеих таблиц; триггер `trg_m04e_version_artifact_immutable` создаётся в миграции и снимается до `drop_table` | `0302:205-213,254-268,278-279` |
| 20 | Хрупкое: семь DEFERRABLE-FK цикла (`0303`) | **OK.** Ровно 7 ключей; цели — `uq_m05_plugin_instance_exact_identity` и `uq_m05_instance_registry_{tenant,cabinet,project}_link`; все создаются после обеих сторон, все снимаются первыми на downgrade | `0303:102-192,302-314,639-642` |
| 21 | Хрупкое: exact-instance FK и partial unique (`0304`) | **OK.** `fk_cap_grant_instance_exact` (6 колонок) → `uq_m05_plugin_instance_exact_identity`; `uq_cap_grant_active_identity` — partial, без `expires_at` в предикате | `0304:163-182,266-285` |
| 22 | Хрупкое: RLS опирается на имена таблиц (`0307`) | **OK** по именам (все 31 совпадают с созданными), но именно имя-основанность делает слепую зону M1 невидимой | `policies.py:150-159,289-296` |
| 23 | Хрупкое: повторная установка политик (`0310`) | **OK по идемпотентности**, недостаточна по позиции в цепочке (B1) и необратима (M3) | `0310:44-60` |
| 24 | Хрупкое: `agent_run_leases` (`0309`) и `agent_run_provider_attempts` (`0311`) после `0307` | `0309` покрыт `0310`; `0311` — нет (B1) | см. B1 |
| 25 | Гейт `0308`: инвентарь отложенных констрейнтов | **OK.** 9 записей (1 из `0302` + 7 из `0303` + 1 из `0304`), совпадают с фактически объявленными; `NOT VALID` в цепочке нет ни одного; downgrade — документированный no-op | `0308:113-168,321-352` |
| 26 | Verification freeze | **соблюдён**: тесты/сборки/линтеры/компиляция/git не запускались | — |

## Дефекты

### Blocker

**B1. `agent_run_provider_attempts` остаётся без RLS: таблица создана после последнего прохода
политик.**
Таблица зарегистрирована в RLS-реестре (`owner_revision="2026110311"`), то есть контракт требует
для неё `ENABLE/FORCE ROW LEVEL SECURITY` и две политики. Но политики ставит только `0307`, а
повторно применяет `0310`; `0311` создаёт таблицу **после** обоих. На свежем `upgrade head`
таблица с `tenant_id`/`project_id` остаётся без RLS и без политик, а `uncovered_relations()` этого
не увидит: членство в реестре считается покрытием. Заметить проблему можно только по NOTICE/warning
в `0310`, который выполняется, когда таблицы ещё нет.
Локация: `apps/api/alembic/versions/2026110311_agent_run_provider_attempts.py:60`;
`apps/api/src/prodavan/infrastructure/persistence/rls/policies.py:626-636`;
`apps/api/alembic/versions/2026110310_rls_after_lease_fence.py:44-49`;
`apps/api/alembic/versions/2026110307_rls_policies.py:126-129`.
Направление починки (не выполнено, вне зоны ревиза): новая ревизия `2026110312`, повторяющая
`upgrade_guard_sql(spec)` для `agent_run_provider_attempts` (или полный проход реестра), либо
перестановка `0311` перед `0310`. Автоматизировано тестом
`test_chain_contract.py::test_every_registered_table_is_policied_after_its_creating_revision`
(strict `xfail`, снимется вместе с фиксом).

### Major

**M1. Три таблицы `0306` с `project_id` не покрыты RLS и не внесены в исключения; отчёт
покрытия их не видит.**
`runtime_revision_heads`, `runtime_materialization_attempts`, `runtime_materialization_audit`
созданы `0306`, имеют `project_id` (а последняя — ещё и append-only журнал), но отсутствуют и в
реестре, и в `EXCLUDED_TABLES`. Дополнительно они не попадают ни под один
`CANONICAL_TABLE_PREFIXES` (`materialization_` не ловит `runtime_materialization_*`,
`project_runtime_revision` не ловит `runtime_revision_heads`), поэтому `uncovered_relations()` в
`0307` физически не может их назвать: инвариант «пусто» выполняется вырожденно. Это тот же класс
дефекта, что уже честно зафиксирован для `resources`/`resource_versions`/`resource_bindings`
(«deferred to a dedicated RLS review»), но без записи об исключении.
Локация: `2026110306_resources_runtime.py:208-258,266-353,375-421`;
`policies.py:645-663` (исключения), `policies.py:667-676` (префиксы).

**M2. `validate_registry()` не может подтвердить порядок установки политик.**
`owner_revision` — свободная строка, валидируется только на непустоту; ни реестр, ни `0307`/`0310`
не проверяют, что для каждой записи существует ревизия-установщик политик позже владельца таблицы.
Именно поэтому B1 проходит все внутренние валидации реестра.
Локация: `policies.py:131-146,679-696`.

**M3. `0310.downgrade()` — no-op при наличии upgrade-дельты.**
`upgrade` повторно ставит весь реестр, то есть создаёт дельту (политики `agent_run_leases_*`),
а `downgrade` не снимает ничего. Формально «downgrade снимает только свои объекты» нарушено.
Практически безвредно: имена совпадают с `0307`, а на ревизии `0309` таблица аренд ещё существует
(политики ей нужны), так что откат не оставляет «мусорных» объектов. Требует явного решения, а не
молчаливого no-op.
Локация: `2026110310_rls_after_lease_fence.py:44-60`.

### Minor

**m1. Устаревшие docstring после роста цепочки до 11 звеньев.** `0307` называет себя «the last link
of the wave-1 DDL chain» (`0307:3-5`), `0308` — «closes the wave `2026110301`–`2026110307`» и
объявляет гейт терминальным (`0308:1-5,45,321-331`). После появления `0309`–`0311` оба утверждения
неверны: гейт `0308` не последний, а `0307` не последний установщик политик (последний — `0310`).

**m2. Асимметрия имён FK у `0309`/`0311`.** Миграции дают явные имена
(`fk_agent_run_lease_run`, `fk_agent_run_attempt_run`), модели объявляют те же FK безымянными —
в БД по умолчанию получилось бы `agent_run_leases_run_id_fkey`. Autogenerate имена FK не
сравнивает, дрейфа не будет, но конвенция «явное имя для ревью» на стороне моделей не соблюдена.
Локация: `2026110309_agent_run_leases.py:72-77`, `2026110311_agent_run_provider_attempts.py:107-112`
против `persistence/agent_control_plane/lease_registry.py:64-68`,
`…/attempt_registry.py:96-100`.

**m3. Колонка с именем `metadata`.** `agent_run_provider_attempts.metadata` (`0311:82-84`) —
ORM-модель спасает `metadata_json` с `mapped_column("metadata", …)`, но колонка с этим именем в
декларативном классе остаётся ловушкой для будущих `select(Row.metadata)`/`getattr`.

**m4. `use_alter=True` в `op.create_foreign_key` (`0302:258-268`) — no-op.** Параметр значим
только для DDL-сортировки в metadata/autogenerate, в явном вызове `op` не действует. Не дефект,
но читается как гарантия, которой нет.

**m5. `harness.py` (чужая зона, ревью-инструмент) даёт зашумлённый отчёт.** Три независимых
причины: (а) `WAVE_REVISIONS` заканчивается на `0308` (`harness.py:54-63`), поэтому `0309`–`0311`
не реплеятся; (б) заголовки парсятся только как `ast.Assign`, поэтому legacy-файлы с
`revision: str = "stub_bootstrap"` выпадают и CLI печатает 8 фантомных head'ов (реальный граф — один
head, `2026110311`); (в) индексы и «отложенные» FK записываются вне объекта `Table`, из-за чего
`parity_index`/`parity_fk` печатают десятки ложных расхождений (`migration () vs model (...)`).
Итог CLI: 49 findings, из которых по существу верны только M1/B1-родственные. Разбор — в §
«Ложные срабатывания harness».

**m6. `0307` строит отчёт покрытия по `current_schema()` (`0307:65-69`)**, тогда как сами политики
рендерятся без схемной квалификации (`policies.py:232-255`). При нестандартном `search_path` отчёт
и установка могут смотреть в разные схемы.

## Ложные срабатывания harness (важно для следующего ревьюера)

Отчёт `python -c "…harness._main()"` содержит 49 findings. Не являются дефектами:

* `chain: expected exactly one head, found (2026082301…2026082308, 2026110311)` — артефакт парсинга
  `ast.Assign` (m5б). Реальный граф проверен отдельно: 202 ревизии, корень `stub_bootstrap`,
  единственный head `2026110311`.
* `fk_target_key: 2026110303 … scope_registry (id, plugin_instance_id, tenant_id) has no PK/UNIQUE` —
  harness не учитывает unique-ключи, созданные raw SQL (`ALTER TABLE … ADD CONSTRAINT … UNIQUE`,
  `0303:256-299`). С учётом их цель существует; проверка с дополнением даёт **0 проблем**.
* `parity_index: … migration () vs model (...)` (≈20 записей) и `parity_unique/parity_fk` для
  `scope_registry`, `m04e_dynamic_app_definitions`, `m05_plugin_instances`,
  `m05_plugin_instance_registry_links` — индексы и отложенные FK лежат в `Replay.indexes` /
  `Replay.detached_foreign_keys`, а не в `Table` (m5в). При корректном сравнении — **0 расхождений**.
* `rls_owner: agent_run_leases / agent_run_provider_attempts is policied but no wave revision creates
  it` — следствие усечённого `WAVE_REVISIONS` (m5а), не дефект.

Верными оказались только: `rls_uncovered` для трёх таблиц `0306` (M1) и сигнал о
`agent_run_provider_attempts` в RLS-реестре (B1, в терминах отчёта — как `rls_owner`).

## Подтверждённые инварианты

1. Цепочка `2026110301`→`2026110311` линейна: один родитель на звено, один корень, один head,
   ветвей и отсутствующих родителей нет (202 ревизии суммарно).
2. Цепочка объявляет 38 таблиц, 63 индекса, 2 функции, 3 триггера, 3 unique-констрейнта через raw SQL
   и 62 политики (124 эмиссии: по 62 в `0307` и `0310`); пересечений имён нет ни у одного класса
   объектов, кроме политик, и там это идемпотентная переустановка.
3. Паритет с моделями (колонки/типы/nullable/server_default, PK, UNIQUE, CHECK-тексты, FK с
   `ondelete`/`deferrable`, индексы с предикатами и выражениями) — **точный, 0 расхождений**.
4. Каждый FK-таргет либо создан более ранней ревизией, либо retained, и для каждой композитной цели
   есть PRIMARY/UNIQUE ровно на её колонки (включая ключи, созданные raw SQL в `0303`).
5. `downgrade` всех ревизий, владеющих таблицами, является точным обратным отображением `upgrade`
   (набор и порядок объектов), кроме документированных no-op (`0308`) и `0310` (M3).
6. Retained-таблица `projects` получает ровно тот compatibility-ключ
   `uq_projects_scope_owner_parent_tuple`, который объявлен моделью; ключ создаётся первым и
   снимается последним.
7. Разделение владения `scope_registry` (`0301` — таблица, `0303` — три M05-якоря и три
   reciprocal FK) согласовано: `0301` явно делегирует, `0303` создаёт `IF NOT EXISTS` и удаляет
   свои объекты; суммарно объявленный DDL совпадает с моделью.
8. Цикл `m04e_dynamic_app_definitions` ↔ `m04e_dynamic_app_versions` разорван одним отложенным
   FK, добавленным после обеих таблиц; цикл `scope_registry` ↔ `m05_plugin_instances` — семью
   отложенными FK; downgrade снимает их до `drop_table`.
9. Immutability-инварианты вынесены в БД: триггер артефактов `0302` (включая запрет rewind в
   `draft`), append-only триггеры аудита `0306` (UPDATE/DELETE/TRUNCATE).
10. Гейт `0308` валидирует ровно 9 констрейнтов: 8 отложенных (1 из `0302` + 7 из `0303`) и
    `fk_cap_grant_instance_exact` из `0304` (inline, без `NOT VALID`, но без доказательств на момент
    волны). `NOT VALID` в цепочке не встречается ни разу.
11. RLS-реестр согласован сам с собой: 31 таблица, 62 уникальных имени политик, каждая таблица
    присутствует в metadata, `owner_revision` совпадает с ревизией-создателем, `uncovered_relations()`
    пуст для metadata и для набора таблиц цепочки.

## Автоматизация (`apps/api/tests/migration/ddl_cross_review/test_chain_contract.py`)

Контракт написан как статический тест: никакой БД, никакого `alembic`, только AST + replay
`upgrade()`/`downgrade()` через существующий harness + импорт `Base.metadata` и RLS-реестра.
Повторная реализация парсинга заголовков и агрегации схемы (а не вызовы `harness.chain_problems`
и т.п.) сделана намеренно — она не наследует три дефекта harness (m5).

| Тест | Что фиксирует |
|------|---------------|
| `test_every_chain_revision_has_exactly_one_file` | ровно один файл на ревизию цепочки |
| `test_chain_headers_are_linear_and_correctly_linked` | `0301→2026110202`, дальше каждое звено = предыдущее, `branch_labels`/`depends_on` пусты |
| `test_the_whole_versions_tree_has_a_single_head` | единственный head `2026110311`, обход до `stub_bootstrap` покрывает все ревизии, форков и циклов нет |
| `test_no_table_index_or_constraint_is_created_twice` | дубли имён таблиц/индексов/констрейнтов запрещены (политики исключены — они идемпотентны) |
| `test_policy_reinstallation_is_idempotent_and_owned_by_one_revision` | ровно 62 политики реестра, вводит `0307`, повторяет `0310`, блок = drop-then-create |
| `test_every_foreign_key_target_exists_earlier_or_is_retained` | 64 FK цепочки: таргет создан раньше или retained |
| `test_every_composite_foreign_target_has_an_exact_primary_or_unique_key` | у каждой композитной цели есть PK/UNIQUE ровно на её колонки (включая raw-SQL ключи `0303`) |
| `test_deferred_cycle_constraints_are_complete_and_deferrable` | ровно 8 отложенных FK, все `DEFERRED` + `RESTRICT`, все указывают на уже существующую таблицу |
| `test_validation_gate_covers_exactly_the_staged_constraints` | инвентарь `0308` (9 записей) независимо пересчитывается из `0301`–`0304` |
| `test_every_chain_table_is_registered_in_the_model_metadata` | 38 таблиц цепочки есть в `Base.metadata` |
| `test_column_parity_with_models` / `…_primary_key…` / `…_unique…` / `…_check…` / `…_foreign_key…` / `…_index…` | паритет по колонкам, PK, UNIQUE, CHECK-текстам, форме FK, индексам (включая partial/expression) |
| `test_retained_project_compatibility_key_matches_the_model` | `uq_projects_scope_owner_parent_tuple` создаётся на retained `projects`, объявлен моделью, снимается последним |
| `test_m05_registry_anchor_keys_are_owned_by_0303_alone` | `0301` их не создаёт, `0303` создаёт/снимает ровно 3, модель их объявляет |
| `test_rls_registry_tables_exist_and_name_their_creating_revision` | 31 запись реестра ↔ ревизия-создатель |
| `test_rls_registry_is_internally_consistent` | `validate_registry()`, уникальность имён, `false`-предикат без tenant-якоря, GUC в предикатах |
| `test_uncovered_relations_is_empty_for_the_model_metadata` / `…_for_the_chain_tables` | `uncovered_relations()` пуст для обеих выборок |
| `test_table_downgrades_mirror_upgrades` | для 8 ревизий-владельцев: набор и порядок drop == reverse(create), индексы и именованные объекты симметричны |
| `test_policy_revision_downgrade_removes_exactly_its_policies` | `0307` снимает ровно свои 62 политики и не владеет таблицами |
| `test_validation_revision_downgrade_is_a_documented_no_op` | `0308` ничего не удаляет и не переписывает; решение задокументировано |
| `test_chain_creates_no_rls_object_before_the_policy_revision` | до `0307` политик нет |
| `test_no_chain_revision_emits_not_valid` | `NOT VALID` не встречается (с word-boundary, чтобы не ловить «not validated» в сообщении гейта) |

**Негативные/граничные кейсы внутри теста.** Три теста помечены **strict** `xfail` — они
фиксируют именно дефекты B1, M1, M3 и превратятся в `XPASS` (падение), как только появится
починочная ревизия; это защита от «немого» принятия дефекта:

* `test_every_registered_table_is_policied_after_its_creating_revision` — B1;
* `test_every_scoped_chain_table_is_registered_or_explicitly_excluded` — M1;
* `test_policy_reinstallation_revision_rolls_back_its_own_delta` — M3.

## Остаточные риски (вне DDL-ревиза)

* B1 и M1 требуют **новой ревизии** — правка `0310`/`0307` запрещена (применённые ревизии не
  редактируются). До её появления любая БД, поднятая до `head`, имеет таблицу без RLS.
* `resources`/`resource_versions`/`resource_bindings` и `capability_bundles` остаются без политик
  по документированному решению (`policies.py:645-663`) — это принятый долг, не дефект ревиза, но
  он должен попасть в cutover-гейт.
* Harness в `tests/migration/ddl_cross_review/` (m5) стоит починить до следующего ревиза, иначе
  ревью-инструмент продолжит давать 40+ ложных findings и скрывать реальные.
* Валидация «ровно один head» должна проверяться с учётом аннотированных заголовков legacy-файлов —
  иначе любой будущий ревиз получит тот же фантомный результат.

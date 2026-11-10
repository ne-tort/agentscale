# Безопасный cutover declarative-app → plugin v4

**Статус:** **STOP для текущего cutover** после независимого аудита HEAD
`6ee720dd`. S1 рабочий; plugin v4 не является подтверждённым vertical slice.
**Действует сейчас:** никаких schema removals, переносов pending revisions или
выполнения removal-чек-листов. Это документ безопасных **будущих** gates, а не
разрешение на работу с dev/prod.

## Немедленные запреты

1. **Оставить** `apps/api/alembic/pending/2026110802_remove_declarative_app.py`
   в `pending/`: не переносить в `versions/`, не регистрировать и не активировать;
   никакой env flag не снимает этот запрет.
2. **Не использовать** `application/plugins/strangler/removal.py` как runbook:
   это неисполняемый historical/dormant inventory кандидатов для отдельного S4 review.
3. **Не удалять** migration-only `product_module_upsert`, product seeds и
   `management_module_merge`, даже если новый runtime их больше не вызывает.
4. **Не редактировать applied migrations.** Исправление истории только новыми
   ревизиями после отдельного решения; `alembic stamp` и ручные изменения shared
   БД запрещены.
5. Не включать plugin platform и не считать feature flag доказательством
   совместимости.

## Актуальный dynamic inventory после legacy host cleanup

Следующие host-only файлы **уже удалены** и не должны повторно появляться в
`CODE_FIRST_REMOVAL_INVENTORY` как будущие deletion targets:

- `apps/web/src/dynamic/declarative/host.tsx`;
- `apps/web/src/dynamic/declarative/index.ts`;
- `apps/web/src/dynamic/plugin-host.ts`;
- `apps/web/src/dynamic/broker.ts`;
- `apps/web/src/dynamic/plugin-host.test.ts`;
- `apps/web/src/dynamic/declarative/acceptance.test.tsx`.

Под declarative-app deletion **не попадают** сохранённые
`PRESERVED_DYNAMIC_INVENTORY`:

- `apps/web/src/dynamic/declarative/renderer.ts` и его `README.md`;
- `apps/web/src/dynamic/{schema,protocol,policy}.ts` и
  `apps/web/src/dynamic/registry-client/**`;
- dynamic-apps: `application/dynamic_app*/**`, `domain/dynamic_apps/**`,
  `infrastructure/persistence/models/dynamic_app*.py` и таблицы
  `dynamic_app_*`.

`PRESERVED_PLUGIN_INVENTORY` отдельно сохраняет M05 model/migration и таблицы
`m05_plugin_instances`, `m05_plugin_instance_registry_links`,
`m05_plugin_instance_promotion_evidence`. Они не являются legacy deletion targets.

Сохранённые защиты (`PRESERVED_DYNAMIC_GUARDS`):

- [renderer-only tests](../../apps/web/src/dynamic/declarative/renderer.test.ts)
  заменяют ссылку на удалённый acceptance suite и проверяют только renderer;
- [legacy host import-graph guard](../../apps/web/src/dynamic/legacy-host-import-graph.test.ts)
  сохраняет static import-graph проверку текущего plugin bootstrap/RPC path и
  запрещает возврат удалённых legacy host modules/imports.

Эти source-level guards не являются live plugin acceptance и не снимают
STOP-SHIP. Они не разрешают автоматическое удаление `declarative/**`.

## Канон: code-first expand/contract A→B

- **A — expand:** добавить новые plugin-возможности аддитивно, сохранив S1,
  старые таблицы, ORM и historical migrations. Ничего не дропать.
- **Переход:** переносить bounded contexts по одному, доказывая live новый путь и
  отсутствие регрессии S1.
- **B — contract:** сначала убрать старые runtime readers/writers/routes/imports,
  затем отдельным решением убрать схему. Migration history остаётся читаемой.

**Schema-first/drop-first запрещён.** Удаление таблицы до удаления её readers,
writers, ORM models и historical dependencies оставляет падающие запросы и
невоспроизводимый fresh upgrade.

## Раздельные gates S1/S2/S3/S4/S5

Обозначения ниже относятся к **этому cutover-плану** и не подменяют продуктовый
S1 из PRODUCT.md.

| Gate | Содержание | Отдельное решение и обязательные evidence |
|---|---|---|
| **S1 — сохранить рабочий baseline** | Текущий agent/declarative product, additive plugin state без удаления | Рабочий по независимому аудиту. Никакого cutover действия. |
| **S2 — expand / plugin readiness** | Отдельно завершить remediation и доказать plugin vertical slice без удаления declarative-app | Отдельное architecture/security решение, снятие verification freeze, CI и live acceptance. Сейчас не выполнено. |
| **S3 — strangler по одному bounded context** | Перенести выбранный context за явным флагом, сохранив остальной S1 | **Для каждого context отдельно:** CI, live route/data evidence, import graph старых и новых readers/writers, rollback кода. Нельзя объединять с S2 или S5 «заодно». |
| **S4 — code-first cleanup** | После доказанного S3 удалить старые runtime entry points, readers/writers, ORM usage и web imports, но ещё не дропать таблицы | CI + import graph + поиск зависимостей + fresh/historical upgrade. Migration-only helpers и старые revisions остаются. |
| **S5 — schema contract** | Только после S4 принять и применить финальную schema revision | Отдельный reviewed PR/ADR/acceptance, full migration matrix и live proof отсутствия старых consumers. Не является автоматическим продолжением S3. |

**S2, S3 и S5 — разные решения.** Нельзя считать успех предыдущего gate
автоматическим разрешением следующего.

## Сохранение migration-only helpers

Ранние Alembic revisions импортируют и вызывают:

- `product_module_upsert` / `upsert_product_modules`;
- product module seeds, включая `seed_rows`;
- `management_module_merge`.

Они нужны не только текущему runtime, но и **fresh database upgrade** и
**historical upgrade** с более ранней ревизии. Поэтому удаление runtime
declarative-app не означает удаление этих helpers. Их возможное удаление — отдельная
историческая миграционная политика за пределами текущего cutover.

## Исправление ошибки про «90 ревизий пишут на каждом деплое»

Прежнее обобщение неверно:

```text
alembic upgrade head
  → выполняет только ещё НЕ применённые revisions
  → повторный upgrade head при уже записанной alembic_version их пропускает
```

Writer внутри seed/migration revision срабатывает при **fresh upgrade** или
**historical upgrade**, когда эта ревизия действительно применяется. Он **не**
срабатывает на каждом повторном `upgrade head`, если ревизия уже записана.

Следовательно, «90 ревизий пишут на каждом деплое» — ложное утверждение. Но при
fresh/historical replay эти writers действительно выполняются, поэтому их imports
и helpers всё равно обязаны оставаться доступными и идемпотентными по контракту
соответствующих revisions.

## Финальная S5 revision — требования target

Существующий pending `2026110802` — **неисполняемый historical/dormant inventory**,
а не готовая Alembic revision: у него нет revision/down_revision identifiers,
upgrade/downgrade entrypoints или DDL. Его запрещено переносить, копировать,
регистрировать и активировать. Новая финальная ревизия проектируется после S4 и
отдельного review:

- создаётся заново **от тогдашней current head**, без переиспользования `2026110802`;
- **без `PLUGIN_CUTOVER_CONFIRMED` или другого env guard** — защита обеспечивается
  code-first PR gates, а не миграцией, которая отказывается выполняться в обычном
  `upgrade head`;
- содержит только необходимый schema contract в правильном dependency order;
- не редактирует applied revisions;
- сохраняет migration-only helpers и воспроизводимость fresh/historical upgrade;
- имеет **schema-only downgrade**: recreate пустых таблиц/constraints/indexes,
  необходимых для технического schema rollback, **без восстановления данных** и
  без обещания вернуть старый runtime;
- не используется как автоматический rollback кода. Code rollback и data/schema
  rollback — разные saga/решения;
- после неё ORM metadata и код уже согласованы, поэтому нет намеренного
  `alembic check` drift.

Schema-only downgrade — техническая возможность откатить DDL, а не способ сделать
удаление данных обратимым. Lossy data semantics должны быть явно отражены в
review и evidence.

## Матрица проверок перед S5 (после снятия freeze)

Сейчас эти команды **не запускаются**. До S5 потребуется отдельная CI/evidence
матрица:

| Проверка | Что должно быть доказано |
|---|---|
| Fresh upgrade | Пустая БД проходит всю историю; historical helpers доступны; финальная схема согласована с кодом |
| Historical upgrade | Переход с поддерживаемой прежней ревизии проходит writers один раз и не ломается на удалённых imports |
| Repeat `upgrade head` | Уже применённые revisions и их writers не выполняются повторно |
| `alembic check` | Нет неучтённого drift после code-first removal и S5 |
| Schema-only downgrade/upgrade | Пустая schema восстанавливается технически; данные не обещаются |
| Import graph | Нет runtime imports старых module models/readers/writers; historical migration-only imports намеренно сохранены |
| CI и live S1 | Новый путь и оставшийся S1 работают; нет обращений к удалённым таблицам |
| Code rollback saga | Возврат артефактов/routes не требует автоматического destructive DB rollback |

## Связанные документы

- [STOP-SHIP status](plugin-migration-status.md)
- [Live acceptance map](plugin-acceptance-map.md)
- [ADR-006](../architecture-migration/ADR-006-plugin-modules.md),
  [ADR-007](../architecture-migration/ADR-007-direct-plugin-db.md),
  [ADR-008](../architecture-migration/ADR-008-lifecycle-and-boundaries.md)
- [ADR-009 — **Предложено**](../architecture-migration/ADR-009-module-source-authoring.md):
  deploy/rollback saga плагина не заменяет этот platform schema cutover.

**В рамках этой документной задачи pending revision не перемещалась,
`removal.py` не выполнялся, код/CI/NetworkPolicy/prod не изменялись, а tests,
build, lint, compile и git status/diff намеренно не запускались.**

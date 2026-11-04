# PostgreSQL integration suite — migration concurrency (MIG-INT-PG-01)

Артефакты приёмочного сьюта, который **будет** запускаться против реального
PostgreSQL после снятия verification freeze. Сейчас (baseline `2026-11-04`)
он написан, но намеренно **не запускался**: см. «Почему сейчас не запускается».

Сьют доказывает ровно то, что нельзя доказать на моках и на SQLite: поведение
схемы и реальных адаптеров под конкуренцией — при живых транзакциях, блокировках
и откатах.

## Что покрыто

| Файл | Критерий | Суть |
|---|---|---|
| `test_idempotent_reservation.py` | 1 | Два запроса с одним `Idempotency-Key` → ровно один `applied`, второй — replay исходной квитанции; ноль дублей run/event/outbox/intent; отдельно форсируется гонка на PK квитанции |
| `test_aggregate_cas_race.py` | 2 | Два перехода с одинаковым `expected_sequence` → один `applied`, один `conflict`; курсор агрегата сдвигается ровно на шаг; отдельный CAS-тест на одной строке с форсированным interleaving |
| `test_transaction_abort.py` | 3 | Сбой записи event / outbox / intent откатывает run, курсор, событие и **квитанцию**; после снятия сбоя тот же ключ даёт `applied` |
| `test_lease_claim.py` | 4 | Два воркера не берут один run; `FOR UPDATE SKIP LOCKED` действительно пропускает заблокированную строку; истёкший lease переиспользуется со сдвигом `lease_generation`; `retry_at` в будущем не клеймится; отсутствие курсора/рассинхрон статуса — fail-closed |
| `test_m05_deferrable_cycle.py` | 5 | Обе стороны цикла M05 в одной транзакции проходят (в любом порядке), нарушение ловится на `COMMIT` (SQLSTATE 23503), `SET CONSTRAINTS ALL IMMEDIATE` роняет ту же вставку раньше, `pg_constraint` подтверждает `DEFERRABLE INITIALLY DEFERRED` |
| `test_grant_partial_unique.py` | 6 | Два активных гранта с одинаковой identity невозможны (partial unique `WHERE revoked_at IS NULL`), после `revoked_at` — можно; гонка форсируется на самом индексе; индекс не «шире» identity |
| `test_alembic_cycle.py` | 7 | `upgrade head` → `downgrade 2026110202` → `upgrade head` на чистой БД; проверяются таблицы волны, выжившие таблицы control-plane, RLS-политики и append-only триггер; плюс идемпотентность `upgrade head` и линейность цепочки ревизий |

Вспомогательные модули (не тесты, а разделяемый каркас):

- `conftest.py` — DSN из env, скипы, изоляция схемы на тест, `SchemaHarness`;
- `_support.py` — `Gate`, `advisory_gate`/`hold_until_advisory`, `wait_until`,
  `blocked_backends`, `sha256_digest`/`sha256_of_text`, `sqlstate_of`,
  `violated_constraint`;
- `_seeds.py` — фикстуры строк для цепочек FK (tenant anchor → M04.e definition →
  version → M05 instance ↔ scope registry → canonical grant), run/lease/cursor,
  и production-проводка `DurableAgentControlPlane` на конкретную схему.

## Как поднять БД

Нужен PostgreSQL 13+ (используются `pg_stat_activity`, `FOR UPDATE SKIP LOCKED`,
partial unique index, deferrable composite FK, session/xact advisory locks; всё
это есть в 13+). Какая именно версия используется в CI — не проверялось: прогон
по этому каталогу ещё не выполнялся. Пример ниже — 16.

```bash
docker run --rm -d --name pgint \
  -e POSTGRES_USER=prodavan \
  -e POSTGRES_PASSWORD=prodavan \
  -e POSTGRES_DB=prodavan_test \
  -p 55432:5432 postgres:16
```

Одна база для сьюта (схемы создаются и удаляются автоматически):

```bash
export PRODAVAN_TEST_DATABASE_URL=postgresql+asyncpg://prodavan:prodavan@127.0.0.1:55432/prodavan_test
```

Отдельная **пустая** база для alembic-цикла (рекомендуется — см. ниже):

```bash
docker exec pgint createdb -U prodavan prodavan_migrate
export PRODAVAN_TEST_MIGRATION_DATABASE_URL=postgresql+asyncpg://prodavan:prodavan@127.0.0.1:55432/prodavan_migrate
```

Схему сьют строит сам из ORM-моделей (`Base.metadata`), как и предписывает
[`docs/07-infrastructure/alembic.md`](../../../../../docs/07-infrastructure/alembic.md):
модели — источник истины, ревизии — версионированный DDL.

Windows: те же `export`-и заменяются на `$env:PRODAVAN_TEST_DATABASE_URL = "..."`.
DSN принимается и в форме `postgresql://`, драйвер нормализуется к `asyncpg`.

## Как запустить

```bash
cd apps/api
pytest tests/migration/postgres_integration -v
```

Только один файл / один критерий:

```bash
pytest tests/migration/postgres_integration/test_lease_claim.py -v
pytest tests/migration/postgres_integration -k "deferrable or partial_unique" -v
```

Без `PRODAVAN_TEST_DATABASE_URL` весь каталог **скипается** с явной причиной —
это не ошибка конфигурации, а осознанный fail-safe: сьют не имеет дефолтного
DSN, чтобы случайно не указывать на чужую/рабочую базу. Единственное
исключение — `test_wave_revisions_above_the_downgrade_target_form_a_linear_chain`
в `test_alembic_cycle.py`: он читает только script-directory Alembic (никакой БД
не требует) и потому выполняется всегда.

### Изоляция и откат

- Каждый тест получает свежую схему `pgint_<12 hex>`; `search_path` пинится на
  **каждое** соединение через `server_settings`, поэтому пул (`NullPool`) не
  может «протечь» схемой между тестами.
- Схема дропается в teardown; если дроп не удался (утечка соединения), это
  `warnings.warn`, а не маскировка реального падения теста.
- `public` не трогается никогда — сьют безопасно запускать на базе, где рядом
  живут другие артефакты, и на базе, которую используют несколько прогонов.
- RLS-политики в схему не ставятся (`create_all` их не создаёт): иначе проверка
  конкуренции превратилась бы в проверку политики. RLS покрыт
  `tests/migration/rls/`.
- Alembic-цикл — исключение: он работает в БД целиком (см. ниже), поэтому для
  него нужна отдельная пустая база.

### Детерминизм

Ни одна гонка не строится через `sleep`:

- `Gate` — `asyncio`-handshake между корутинами;
- `advisory_gate` + `hold_until_advisory` — session-level advisory lock на
  отдельном соединении и `pg_advisory_xact_lock` внутри транзакции воркера:
  «записал, но не закоммитил» становится стабильным состоянием, а не окном
  времени;
- `blocked_backends` — ожидание *наблюдаемого* условия в `pg_stat_activity`
  (`wait_event_type = 'Lock'` + фильтр по тексту запроса): тест идёт дальше ровно
  тогда, когда второй backend реально стоит в очереди блокировки, и падает с
  внятным сообщением, если этого не случилось.

## Почему сейчас не запускается

1. **Verification freeze.** В `AGENTS.md` (раздел «Migration-stage work
   protocol») запрещены запуски тестов, сборок, компиляции, линтеров,
   форматтеров, `alembic upgrade` и DDL к живой БД до отдельного решения о
   снятии freeze. Приёмка этой волны пишется как артефакты и проверяется
   рассуждением по коду и контрактам. Настоящий сьют — часть этой приёмки.
2. **`test_alembic_cycle.py` меняет базу целиком.** `alembic/env.py` берёт URL из
   `settings.database_url` и работает в первой схеме `search_path` — передать ему
   одноразовую схему штатно нельзя. Поэтому фикстура `migration_database_url`
   принимает только доказуемо пустую БД (нет `alembic_version` и **ни одной**
   таблицы в `public`) и **скипается**, если БД не пуста. Это защита от того,
   чтобы `downgrade 2026110202` не откатил чью-то рабочую базу.

Иными словами: «не запускается» здесь — это (а) политика фазы и (б) то, что
запуск требует явно подготовленной пустой БД. Ни одну из причин нельзя обойти
конфигом.

## Что докажет зелёный прогон

Зелёный прогон (после снятия freeze) будет означать, что на реальном PostgreSQL
подтверждены все семь свойств, а не только их формулировки в коде:

1. **Идемпотентность выдерживает конкурентность.** Повторный `Idempotency-Key`
   не создаёт вторую работу: второй ответ — replay той же квитанции, а число
   событий/outbox/intent остаётся равным одному. Отдельно доказано, что
   арбитром служит PK квитанции, а не «повезло со временем».
2. **CAS агрегата — настоящий compare-and-set.** Один `expected_sequence`
   расходуется один раз; проигравший получает `conflict` и не оставляет
   частичных записей; курсор двигается только вперёд.
3. **Атомарность транзакции реальна.** Сбой любой из записей (event, outbox,
   intent) откатывает всё, включая квитанцию, — значит повтор с тем же ключом
   возможен и приводит к `applied`, а не к «полу-применённому» состоянию.
4. **Lease-контракт держит двух воркеров.** Один run — один владелец;
   `SKIP LOCKED` пропускает, а не блокирует; истёкший lease переиспользуется с
   новым поколением (старый воркер зафенсен); `retry_at` в будущем и отсутствие
   committed-курсора — отказ, а не деградация.
5. **Цикл M05 работоспособен и не ослаблен.** Обе стороны взаимных FK пишутся в
   одной транзакции, нарушение всплывает на `COMMIT`, а не молча, и deferral
   подтверждён в `pg_constraint`.
6. **Partial unique грантов ведёт себя как контракт ротации.** Два активных
   гранта невозможны, после `revoked_at` — возможны, и конкурентная вставка
   проигрывает с 23505, не повреждая живой грант.
7. **DDL-цепочка обратима.** `upgrade → downgrade → upgrade` даёт один и тот же
   состав таблиц `public` до и после: таблицы волны исчезают и возвращаются,
   таблицы `2026110202` и его append-only триггер выживают, RLS-политики
   снимаются и ставятся снова.

## Границы сьюта

- Не проверяет RLS-политики (см. `tests/migration/rls/`), parity/backfill
  (`*_parity*`), HTTP-слой (`tests/api/**`) и k8s e2e.
- Не запускает `alembic revision --autogenerate` и `alembic check`: это отдельный
  CI-гейт, а не предмет конкуренции.
- `agent_run_leases` проверяется на уровне реального адаптера
  (`SqlAlchemyRunLeaseStore` + `DurableRunLeaseManager`), а не через worker-loop:
  сам цикл воркера покрыт `tests/migration/agent_runs_worker/`.

## Известные ограничения (осознанные)

- **Обратимость ревизий `2026110301`…`2026110311` не проверялась запуском.** Сейчас
  это утверждение из их `downgrade()`-функций; зелёный
  `test_upgrade_downgrade_upgrade_cycle` — первое его доказательство. Если
  какая-то ревизия окажется необратимой, упадёт именно этот тест, а фикстура
  best-effort вернёт БД на `head`.
- **Порог зависимостей по версии PostgreSQL.** Partial unique, deferrable
  composite FK и `SKIP LOCKED` требуют PostgreSQL; SQLite (на котором работает
  часть `persistence`-тестов) эти свойства не воспроизводит — поэтому сьют
  скипается без DSN, а не подменяет БД.
- **Один кластер/одна база на прогон.** Тесты используют `pg_stat_activity`
  текущей БД, поэтому параллельный `pytest -n` по этому каталогу может увидеть
  чужие backend'ы; фильтр по тексту запроса это компенсирует, но запуск в `xdist`
  здесь не рекомендован без отдельной БД на воркер.

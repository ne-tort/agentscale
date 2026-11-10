# Redis plugin real-service acceptance harness

**Статус (2026-11-10): выполнено против уникального disposable Docker redis:7-alpine контейнера (явный DB 14): 22 collected / 22 passed / 0 failed / 0 skipped по точной команде ниже (PYTHONPATH=src, PYTHONUTF8=1). Sanitized JUnit + summary + SHA256: reports/module-audit/postgres-redis/. Контейнер удалён после прогона.** Ранее harness authoring был static-only; актуальные результаты
`passed`/`collected`/`skipped` зафиксированы в указанных artifacts.

Harness использует настоящий `redis.asyncio` и production-адаптеры. Он не подменяет
NX/Lua/GET/SET/SCAN-ответы Redis и не считает in-memory double доказательством
Redis-поведения. Это не live/prod/shared-environment проверка и не запуск двух
процессов: два независимых клиента `api`/`gateway` моделируют их общий durable Redis.

## Только explicit test env

| Env | Назначение |
| --- | --- |
| `REDIS_PLUGIN_ACCEPTANCE_REDIS_URL` | DSN **выделенного test Redis**. Поддерживаются `redis://`/`rediss://`; test-only credentials, если нужны, передаются только здесь. |
| `REDIS_PLUGIN_ACCEPTANCE_REDIS_DB` | Явный неотрицательный database index. Не используется молчаливый DB 0. |
| `REDIS_PLUGIN_ACCEPTANCE_REQUIRED` | `1` для обязательного acceptance. Тогда отсутствие endpoint — ошибка, а не skip. |
| `REDIS_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV` | Должно быть точно `I_OWN_ISOLATED_TEST_RESOURCES` для любого запуска с endpoint. Без этого явного подтверждения harness не подключается. |

Никакие application/dev/prod Redis env автоматически не читаются. URL и credentials
не печатаются fixture'ами и не должны попадать в README, командную историю или логи.
Пример ниже содержит только локальный endpoint без секрета.

Redis ACL пользователя должен разрешать команды, реально нужные production-адаптерам
и очистке: `PING`, `GET`, `SET`, `DEL`, `EVAL`, `ZCARD`, `PTTL`, `EXISTS`, `PUBLISH`,
`SCAN`. Нужны Redis с Lua/cjson, Python 3.12+, `redis>=5`, `pytest`,
`pytest-asyncio` и `jsonschema` из `apps/api/pyproject.toml`. В `apps/api/uv.lock`
зафиксирован redis-py 6.4.0 (установленную runtime-версию не проверяли). Его
`redis.asyncio SET ... NX` возвращает `True` при создании и `None`, если key уже
существует; durable MCP store обязан превратить этот `None` в `False` для re-read,
а не считать операцию подтверждённой или выбрасывать backend error.

## Точная команда обязательного acceptance (PowerShell)

Выполнять только против выделенного test Redis, после явного решения снять
verification freeze:

```powershell
Set-Location 'C:\Users\qwerty\git\agentscale\wt-migration\.agents\worktrees\audit-plugin-modules\apps\api'
$env:PYTHONPATH = (Resolve-Path 'src').Path
$env:REDIS_PLUGIN_ACCEPTANCE_REDIS_URL = 'redis://127.0.0.1:6379'
$env:REDIS_PLUGIN_ACCEPTANCE_REDIS_DB = '14'
$env:REDIS_PLUGIN_ACCEPTANCE_REQUIRED = '1'
$env:REDIS_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV = 'I_OWN_ISOLATED_TEST_RESOURCES'
python -m pytest tests/integration/redis_plugin_acceptance --confcutdir=tests/integration/redis_plugin_acceptance -q -p no:cacheprovider
```

Для required-запуска локальный `conftest.py` отклоняет прогон с `collected=0` или
`skipped>0` ненулевым exit status. Настроенный, но недоступный Redis — failure,
а не skip. Нужно смотреть итоговый pytest summary и exit code, а не только отдельные
assertions.

### Локальный запуск без endpoint (не acceptance)

Если `REDIS_PLUGIN_ACCEPTANCE_REQUIRED` не установлен и URL/DB отсутствуют,
сценарии могут быть skipped:

```powershell
Set-Location 'C:\Users\qwerty\git\agentscale\wt-migration\.agents\worktrees\audit-plugin-modules\apps\api'
$env:PYTHONPATH = (Resolve-Path 'src').Path
Remove-Item Env:REDIS_PLUGIN_ACCEPTANCE_REQUIRED -ErrorAction SilentlyContinue
Remove-Item Env:REDIS_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV -ErrorAction SilentlyContinue
Remove-Item Env:REDIS_PLUGIN_ACCEPTANCE_REDIS_URL -ErrorAction SilentlyContinue
Remove-Item Env:REDIS_PLUGIN_ACCEPTANCE_REDIS_DB -ErrorAction SilentlyContinue
python -m pytest tests/integration/redis_plugin_acceptance --confcutdir=tests/integration/redis_plugin_acceptance -q -p no:cacheprovider
```

Такой результат нельзя выдавать за acceptance evidence.

## Collection isolation

Поддерживаются только команды с exact
`--confcutdir=tests/integration/redis_plugin_acceptance`; отдельный `-p` plugin не
нужен. При таком cutoff parent `apps/api/tests/conftest.py` находится вне границы и
не загружается, поэтому его application imports и PostgreSQL/live API/kubectl probes
не выполняются.

**Bare/no-cutoff и wrong-cutoff запуски unsupported.** Локальный validator не может
отклонить их early: без exact cutoff parent conftest может быть импортирован раньше
локального guard, включая `prodavan.config.settings` и `prodavan.main`, и успеть
выполнить probes. Поэтому нельзя полагаться на bare `python -m pytest ...` или на
последующий отказ validator — используйте только команды выше. `--noconftest` также
unsupported: он отключает локальные real-Redis fixtures и no-skip gate. Между
`apps/api/tests` и каталогом harness промежуточных conftest нет.

## Изоляция Redis и завершение

* Каждый тест получает случайный namespace `agentscale:redis-plugin-acceptance:<uuid>`
  в явно заданном `REDIS_PLUGIN_ACCEPTANCE_REDIS_DB`.
* Два отдельных `redis.asyncio` клиента получают один namespace. Тонкий harness proxy
  добавляет prefix только к key/channel аргументам; команды и Lua выполняются в
  настоящем Redis, ответы не изменяются.
* В `finally` выполняется `SCAN` только собственного namespace и пакетный `DEL`
  найденных keys. `FLUSHDB`, `FLUSHALL` и удаление чужих keys отсутствуют.
* Оба клиента закрываются через `aclose()` в `finally`. Настроенные MCP-адаптеры
  также закрываются.

## Покрываемый контракт

### `test_invocation_isolation.py`

Static checks не требуют Redis или отдельного `-p`: каждая README-команда обязана
содержать exact confcutdir; локальная validation отклоняет missing/wrong cutoff и
`--noconftest` без утверждения о невозможной early-защите bare invocation. Отдельно
проверяются отсутствие промежуточных conftest, parent `apps/api/tests/conftest.py`
среди загруженных файлов и отсутствие `prodavan.config.settings`/`prodavan.main`
в `sys.modules` при exact cutoff.

### `test_job_lease_acceptance.py`

Production `RedisJobExecutionStore` + `ManifestJobTasks`:

* конкурентный `SET NX`: реальный `None` у проигравшего клиента приводит к `GET`
  состояния `running` и `JobTaskRetryRequired`; после completion тот же путь даёт
  `completed` + сохранённый `result_digest`;
* Lua renew/complete — CAS по `owner` **и** `binding_digest`; чужие owner/binding
  получают `JobTaskLeaseLost` и не меняют running-запись;
* renew продолжается во время асинхронной post-validation; второй owner в это время
  отклонён, completion происходит один раз, replay не вызывает handler повторно;
* renewal uncertainty и внешняя отмена не делают release/completion, оставляют
  running lease и не разрешают retry вторым владельцем; watchdog останавливается.

### `test_quota_acceptance.py`

Production `RedisPluginQuota` + `PluginCapabilityService` + `RedisCacheAdapter`:

* все шесть полей exact grant: `ops_per_minute`, `max_rows`, `max_bytes`, `max_keys`,
  `max_object_bytes`, `max_egress_bytes`;
* параметризованные превышения проходят реальный quota/service/typed adapter path
  и возвращают `DecisionCode.QUOTA_EXCEEDED`, HTTP projection `429`;
* лимиты берутся из grant и сравниваются с `limits.limit_for(op)`, а не с hardcoded
  policy/defaults; denied invoke не доходит до cache IO и не увеличивает counter;
* sliding-window OPS сначала пропускает реальные Redis GET в пределах grant, затем
  возвращает 429 без лишнего adapter call.

### `test_mcp_registration_acceptance.py`

Production `RedisMcpRegistrationStore`, два `DurableMcpAdapter`,
`ArtifactGrantMcpManifestSource`, `DurableMcpDiscovery` и `DurableMcpBroker`:

* real `SET NX` replay проверяется детерминированно: create → False → False без
  overwrite; одновременная одинаковая registration через API/Gateway clients и
  exact replay оставляют один canonical Redis entry; конфликт definitions отклонён
  и не затирает сохранённое значение;
* registration scope включает immutable artifact digest; новый digest не наследует
  старые tools и требует отдельной registration;
* stale digest, foreign grant binding и revoked grant отклоняются до backend call;
* discovery пересекает signed manifest и durable registration. Tool без уникального
  canonical signed `rpc_operations` mapping отклонён; вызов `list` проецируется
  именно в signed `GET /api/greetings/list`, а не в путь из Redis.

Grant/artifact/backend ports — детерминированные test doubles, потому что их
долговременные SQL/OIDC/HTTP-системы не являются предметом этого harness. Все
решения, зависящие от Redis state, выполняются только против настоящего Redis.

Глобальный `apps/api/tests/conftest.py` и production code не изменялись.

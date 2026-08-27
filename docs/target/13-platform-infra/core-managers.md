# Backend core — managers & lifespan

Целевой каркас (ещё не обязан существовать в коде; отклонение = P0 gap).

## Пакет

```text
apps/api/src/prodavan/core/
  lifespan/
    manager.py          # LifespanManager
    resource.py         # LifespanResource ABC
  infra/
    redis_manager.py
    kafka_manager.py
    object_storage_manager.py   # removed → infrastructure/files/manager.py (FileStoreManager)
    worker_manager.py           # Celery app facade
  registry.py           # optional shared register helpers
```

Имена могут отличаться; **семантика** обязательна.

## LifespanResource

Базовый контракт:

- `name: str`
- `async def startup(self) -> None`
- `async def shutdown(self) -> None`
- опционально `async def health(self) -> bool`

Порядок: register → startup в порядке регистрации (или явном priority) → yield → shutdown reverse.

## LifespanManager

- Центральный реестр `LifespanResource`.
- Единая точка для FastAPI `lifespan=` (factory в `main.py` только делегирует менеджеру).
- Запрещено размазывать `start_trigger_worker` / `dispose_engine` вручную по `main.py` без регистрации как ресурсов.

## Infrastructure managers

| Manager | Обязанности |
|---------|-------------|
| `RedisManager` | pool/client, ping, settings |
| `KafkaManager` | producer/consumer factory, topic config |
| `FileStoreManager` | S3/local blob I/O, presign, prefix ops (`blobs/`, `projects/`, `cabinet_packages/`) |
| `WorkerManager` | Celery app, task register facade для application |

Application/domain **не** импортируют `aiokafka` / `redis` / `boto3` / `celery` напрямую — только через managers (или тонкие ports в `infrastructure/`, создаваемые managers).

## Register-паттерн (общий дух)

Тот же подход, что UI-core для виджетов:

| Область | Registry |
|---------|----------|
| Lifespan | `LifespanManager.register(resource)` |
| Infra clients | managers в core, один на вид ресурса |
| Middleware (целевой) | единый register/composition, не разрозненный `add_middleware` без учёта порядка в core |

## Связь со слоями

```text
api (FastAPI) → core (lifespan + managers)
application → ports / managers
domain ← pure
infrastructure ← adapters constructed by managers
```

L00 as-built должен отражать переход на этот каркас в Gaps, пока код не догнал.

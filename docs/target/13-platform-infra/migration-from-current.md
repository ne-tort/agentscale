# Миграция: текущий код → канон 13

Карта переходного состояния. As-built слоёв остаётся источником «что есть сейчас»; эта таблица — целевой рефакторинг **P0**.

| Сейчас | Где | Цель | Контракт |
|--------|-----|------|----------|
| PG `project_triggers` outbox-lite + claim/lease | L07 | Kafka topics для trigger envelopes (+ transactional outbox → Kafka при необходимости) | C-EVENT-BUS / C-TRIGGERS |
| Platform events fan-out in-process | L07/L06 | Kafka platform-events; consumers (API/Celery/packages) | C-EVENT-BUS |
| `data/storage/projects/...`, `local-ws:` | L07 | **partial:** attachments/packages via `ObjectStorageManager` (`object://`); materialize tree still local FS | C-OBJECT-STORE / C-MATERIALIZE / C-ATTACH |
| `file://` secrets на диске | L03 | остаётся routing; product **blobs** не через secrets_dir | C-KEY-RESOLVE (без изменения) + C-OBJECT-STORE для файлов |
| `TRIGGER_WORKER_ENABLED` asyncio в lifespan | L07/L08 [`TriggerWorkerResource`](../../../apps/api/src/prodavan/core/infra/trigger_worker_resource.py) | Celery workers + beat/cron enqueue | C-JOBS |
| Idle-pause / admin drain HTTP-only | L07/L09 | Celery tasks; CronJob/k8s только триггерит или schedule в beat | C-JOBS |
| Ad-hoc lifespan start/stop | L00 | **done:** `LifespanManager` + resources via `core.wiring` | L00 / core |
| Нет Redis | — | **partial:** `RedisManager` + REDIS_URL; обязателен в prod (`REDIS_REQUIRED`) | C-CACHE |

## Волны (см. план)

1. **core + lifespan** — **done** (каркас + DB/worker resources)  
2. **Redis** — **done** (subset: manager + health; URL optional)  
3. **MinIO** — **done** (subset: manager + attachments/packages; materialize FS hole)  
4. **Celery** — перенос workers  
5. **Kafka** — triggers + platform events  

Допускается значительный рефакторинг; временные dual-write/adapters — только с явным сроком выпила в as-built Gaps.

## Критерий «переход завершён»

- Нет продуктовой зависимости от локального `storage_root` для blobs.  
- Нет in-process trigger worker как единственного исполнителя.  
- Межмодульные события trigger/platform — через Kafka (или documented dual-write до cutover).  
- `main.py` lifespan только делегирует `LifespanManager`.

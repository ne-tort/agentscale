# Миграция: текущий код → канон 13

Карта переходного состояния. As-built слоёв остаётся источником «что есть сейчас»; эта таблица — целевой рефакторинг **P0**.

| Сейчас | Где | Цель | Контракт |
|--------|-----|------|----------|
| PG `project_triggers` outbox-lite + claim/lease | L07 | **partial:** dual-write Kafka envelopes; PG still claim/drain SoT | C-EVENT-BUS / C-TRIGGERS |
| Platform events fan-out in-process | L07/L06 | **partial:** dual-write Kafka; SPI fan-out still in-process | C-EVENT-BUS |
| `data/storage/projects/...`, `local-ws:` | L07 | **partial:** attachments/packages via `ObjectStorageManager` (`object://`); materialize tree still local FS | C-OBJECT-STORE / C-MATERIALIZE / C-ATTACH |
| `file://` secrets на диске | L03 | остаётся routing; product **blobs** не через secrets_dir | C-KEY-RESOLVE (без изменения) + C-OBJECT-STORE для файлов |
| `TRIGGER_WORKER_ENABLED` asyncio в lifespan | L07/L08 | **partial:** Celery tasks + WorkerManager; in-process fallback when Celery off | C-JOBS |
| Idle-pause / admin drain HTTP-only | L07/L09 | Celery beat/enqueue + HTTP drain remain | C-JOBS |
| Ad-hoc lifespan start/stop | L00 | **done:** `LifespanManager` + resources via `core.wiring` | L00 / core |
| Нет Redis | — | **partial:** `RedisManager` + REDIS_URL; обязателен в prod (`REDIS_REQUIRED`) | C-CACHE |

## Волны (см. план)

1. **core + lifespan** — **done** (каркас + DB/worker resources)  
2. **Redis** — **done** (subset: manager + health; URL optional)  
3. **MinIO** — **done** (subset: manager + attachments/packages + materialize text/zip; sandbox extract hole)  
4. **Celery** — **done** (subset: WorkerManager + tasks; deploy worker/beat hole)  
5. **Kafka** — **done** (subset: dual-write + consumer drain-kick; full cutover hole)  

Допускается значительный рефакторинг; временные dual-write/adapters — только с явным сроком выпила в as-built Gaps.

## Критерий «переход завершён»

- Нет продуктовой зависимости от локального `storage_root` для blobs.  
- Нет in-process trigger worker как единственного исполнителя.  
- Межмодульные события trigger/platform — через Kafka (или documented dual-write до cutover).  
- `main.py` lifespan только делегирует `LifespanManager`.

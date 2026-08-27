# Lifecycle: pause · soft-delete · purge

Единая двухмерная модель для Company / Employee / Cabinet / Project.

## Две оси

```text
visibility:  live | soft_deleted     ← deleted_at IS NULL | NOT NULL  (Cabinet/Project: status=deleted)
runtime:     active | paused         ← status / disabled / archived
```

| Состояние | UI | Counters (active) | Логика / agent | Pod | Данные |
|-----------|-----|-------------------|----------------|-----|--------|
| live + active | виден | да | полная | running | keep |
| live + paused | **виден** | нет в active | **inert** | **stop** | keep |
| soft_deleted | **скрыт** | нет | **inert** | **stop** | keep |
| hard_purged | нет | нет | нет | stop | wipe |

`inert = soft_deleted OR paused` — один gate на write/agent/triggers. Отличие soft vs pause — только visibility.

## Операции

| Op | Эффект |
|----|--------|
| `pause` / `resume` | runtime only; UI остаётся |
| `soft_delete` (`DELETE`) | tombstone + inert + stop Pod; **без wipe** |
| `restore` | → live + **paused** (явный resume отдельно); **без** cascade revive children |
| `purge` | wipe MinIO / DROP schema / KC; только после soft_delete |

## Каскады soft-delete

| Родитель | Дети |
|----------|------|
| Company soft_delete | soft_delete employees + projects + cabinets (**без wipe**) |
| Cabinet soft_delete | soft_delete projects (**без wipe**); schema keep |
| Project soft_delete | stop pod/sessions; blobs keep |
| Employee soft_delete | Auth disable; **не** cascade projects |

Hard-purge каскадирует wipe вниз только явным `purge` (или 409 пока дети не purged).

## Async

REST → Kafka (`*.soft_deleted` / `*.paused` / …) → KafkaManager → Celery (wipe/pod). См. [13-platform-infra/principles.md](13-platform-infra/principles.md) §3a.

Карта сущностей: [00-entities.md](00-entities.md).

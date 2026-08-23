# 13 — Platform infrastructure

| Поле | Значение |
|------|----------|
| Priority | **P0** — закрывать в первую очередь |
| Refactor | **Significant refactor allowed** (L00/L03/L07/L08 и смежные) |
| Status | canon; код: **w1–w5 subset done** (managers + Kafka dual-write); holes: consumer cutover, materialize SoT, deploy |
| Plan | [P0-platform-infra](../11-implementation-plan/P0-platform-infra.md) |

Канон платформенной инфраструктуры и backend core. Отклонение в коде — **дефект** относительно канона (как UI-принципы в §2 [00-principles](../00-principles.md)).

## Документы

| Файл | Содержание |
|------|------------|
| [principles.md](principles.md) | 6 жёстких правил |
| [stack.md](stack.md) | Kafka / MinIO / Celery / Redis / k3s; запреты |
| [core-managers.md](core-managers.md) | `prodavan.core`: manager–register, lifespan |
| [migration-from-current.md](migration-from-current.md) | Сейчас → цель |

## Стек (якорь)

| Роль | Технология |
|------|------------|
| Межмодульные события / контракты | **Kafka** |
| Object storage (blobs) | **MinIO** (S3 API) |
| Фоновые jobs | **Celery** + Redis |
| Кэш / эфемерное / Celery broker | **Redis** |
| Transactional truth | **PostgreSQL** (без изменений роли) |
| Backend wiring | **LifespanManager** + infra managers в `core` |

## Когда читать

Перед любой крупной backend-задачей, затрагивающей storage, очереди, workers или `main.py` lifespan.

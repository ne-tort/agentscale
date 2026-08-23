# Platform infra — принципы

Шесть жёстких правил. Любое отклонение в коде или деплое — дефект относительно канона.

---

## 1. Kafka — шина межмодульных контрактов

- Асинхронные контракты **между слоями/модулями (BC)** идут через **Kafka**, где это уместно (события, команды с отложенной обработкой, fan-out).
- Цель: изоляция ответственности и готовность к выносу BC в отдельные сервисы без смены контрактов.
- **Не** смешивать с Celery: Kafka = контракт/событие; Celery = исполнение job.
- Project triggers и platform events в каноне — durable bus на Kafka (не PG-таблица как prod-шина).

Детали: [stack.md](stack.md), [triggers](../06-projects-runtime/triggers.md).

---

## 2. Object storage — строго без локального продуктового хранилища

- **Запрещено** использовать локальный диск хоста API (`data/storage`, `local-ws` paths) как каноническое хранилище blobs.
- Workspace projects, inbox attachments, MCP package artifacts, прочие файлы продукта — только **S3-совместимое** хранилище.
- Канон для k3s: **MinIO**; клиентский контракт — S3 API (позже тот же код на AWS S3 / аналог).
- Локальный FS допустим только как ephemeral cache / temp, не source of truth.

Детали: [stack.md](stack.md).

---

## 3. Celery — унифицированные фоновые задачи

- Все фоновые / отложенные / периодические jobs платформы — через **Celery** (единый паттерн), не через ad-hoc asyncio loops в процессе API.
- Broker/result backend: **Redis** (см. §4).
- Примеры миграции: trigger drain, idle-pause sweep, rematerialize batches, длинные package handlers.
- In-process worker в API lifespan — **переходный костыль**, не канон.

Детали: [stack.md](stack.md), [migration-from-current.md](migration-from-current.md).

---

## 4. Redis — обязателен

- Redis **должен** быть в платформе и использоваться там, где логичен: Celery broker/result, кэш, rate-limit, короткие блокировки, эфемерные сессионные маркеры.
- **Не** заменяет PostgreSQL как transactional source of truth.
- Доступ только через унифицированный менеджер в `core` (§5).

---

## 5. Core infrastructure managers

- Для каждой внешней инфраструктуры — **один менеджер** в `prodavan.core` (или согласованном `core` пакете API): подключение, health, конфиг, клиент для application/infrastructure слоёв.
- Высокоуровневый код **не** создаёт сырые клиенты Kafka/Redis/S3/Celery вразброс.
- Паттерн: **manager + register** — центральный реестр, единый lifecycle.

Детали: [core-managers.md](core-managers.md).

---

## 6. FastAPI lifespan — manager + resource register

- Lifespan приложения — через **`LifespanManager`**, не разрозненные `start_/stop_` в `main.py`.
- Ресурсы наследуют базовый **`LifespanResource`** (startup/shutdown) и **регистрируются** в менеджере.
- Тот же дух register-паттерна — для middleware и прочих wiring-точек backend core (как UI-core унифицирует виджеты).
- Чистая архитектура backend: `core` (wiring/infra facades) → `application` / `domain` ← `infrastructure` adapters.

Детали: [core-managers.md](core-managers.md).

---

## Приоритет

Эти принципы — **P0**. Допускается значительный рефакторинг существующего кода ради соответствия. План: [P0-platform-infra](../11-implementation-plan/P0-platform-infra.md). Gap: [09-gap-map](../09-gap-map.md) § P0.

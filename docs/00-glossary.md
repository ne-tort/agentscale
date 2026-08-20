# Глоссарий Prodavan

Единый словарь терминов платформы **Prodavan** — многопользовательской SaaS-системы для AI-ассистированных бизнес-процессов с жёсткой изоляцией арендаторов. Документ согласован с архитектурой `docs/02-architecture/` и продуктовым видением `docs/01-vision/`.

---

## Иерархия и доступ

| Термин | Определение |
|--------|-------------|
| **Tenant (арендатор, тенант)** | Изолированная организация-клиент платформы. Имеет собственных пользователей, кабинеты, биллинг и квоты. Данные одного tenant недоступны другому на уровне БД (RLS), файловой системы и сети. |
| **Cabinet (кабинет)** | Логическое рабочее пространство внутри tenant с собственным **профилем** (набор UI, capabilities, MCP-ACL). Один tenant может иметь несколько кабинетов: «Закупки электроники», «Юридический отдел», «HR». Переключение кабинета меняет доступные инструменты и манифест UI. |
| **Project (проект)** | Единица работы внутри кабинета: спека, прогон, сессия агента, артефакты на диске. Аналог `projects/<имя>/` в Commerce MVP, но с привязкой к `tenant_id` + `cabinet_id`. |
| **Session (сессия агента)** | Одна непрерывная работа Cursor-агента в контексте проекта. На платформе — отдельный worker pod с изолированным FS-sandbox. |
| **Operator (оператор)** | Человек, управляющий прогоном: загружает спеки, ревьюит позиции, запускает КП. Не путать с platform admin. |
| **Tenant admin** | Администратор организации: пользователи tenant, кабинеты, интеграции (S4B-креды), квоты. Не имеет доступа к другим tenant. |
| **Platform admin** | Оператор инфраструктуры Prodavan (k3s, миграции, pack registry). Не видит содержимое проектов tenant без аудируемого break-glass. |

---

## Профили и расширяемость

| Термин | Определение |
|--------|-------------|
| **Cabinet profile (профиль кабинета)** | Декларативный манифест (`cabinet-profile.json`): UI-модули, capabilities, MCP tool ACL, seed-данные. Определяет «что умеет» кабинет. |
| **Capability** | Атомарное право профиля: `s4b.search`, `kp.export`, `web.shops`. Gateway проверяет capability перед проксированием MCP-вызова. |
| **Cabinet pack (пак кабинета)** | Версионируемый артеfact: профиль + UI assets + seed SQL + domain hooks. Устанавливается в tenant admin UI. |
| **Domain plugin** | Расширение доменной логики (например, пайплайн закупок электроники): Python-модули, схемы, MCP-адаптеры. Подключается pack'ом, не ядром. |
| **Core platform** | Неизменяемое ядро: auth, multi-tenancy, RLS, agent orchestrator, MCP gateway, API. Без доменной логики закупок. |
| **Seed pack** | Начальные данные при создании кабинета: шаблоны, trusted-sellers, профили задач. |

---

## Агент и изоляция

| Термин | Определение |
|--------|-------------|
| **Worker pod** | Kubernetes Pod, в котором выполняется одна сессия агента. Жизненный цикл = сессия. |
| **FS sandbox** | Путь `tenants/{tid}/cabinets/{cid}/projects/{pid}/` — единственная writable-область агента. |
| **Escape test catalog** | Набор автотестов, проверяющих, что агент не читает/не пишет за пределами sandbox и не обходит network policy. |
| **Deny-by-default network** | Сетевой профиль pod: egress только к allowlist (MCP gateway, model API). Всё остальное — DROP. |
| **SecurityContext** | K8s-ограничения: non-root, read-only root FS, dropped capabilities, seccomp. |

---

## MCP и интеграции

| Термин | Определение |
|--------|-------------|
| **MCP Gateway** | Единая точка входа для tool-вызовов агента. Проверяет JWT, ACL профиля, rate limits, пишет audit log. |
| **Tool ACL** | Список разрешённых MCP-инструментов для данного cabinet profile. |
| **Request envelope** | Обёртка каждого MCP-запроса: `tenant_id`, `cabinet_id`, `project_id`, `session_id`, `trace_id`, payload. |
| **S4B** | Внешний B2B-каталог электроники (s4b.ru). Доступен **только** в профиле `electronics-procurement`. |
| **Commerce MVP** | Предшественник Prodavan: Telegram-бот + локальные `projects/`, KP, S4B. Prodavan переносит домен в SaaS с полной изоляцией. |

---

## Данные и API

| Термин | Определение |
|--------|-------------|
| **RLS (Row Level Security)** | Политики PostgreSQL: строки фильтруются по `tenant_id` (и часто `cabinet_id`) на уровне СУБД. |
| **Run (прогон)** | Один проход пайплайна по спеке: ingest → classify → search → rank → sqlite → review. Артефакты в `runs/<id>/`. |
| **Line item (позиция)** | Строка спецификации после классификации: категория, P/N, constraints, confidence. |
| **Offer (оффер)** | Предложение поставщика: продавец, цена, наличие, источник. Только из артеfactов поиска, не «из головы» агента. |
| **KP (коммерческое предложение)** | XLSX по шаблону из вариантов в SQLite проекта. В Prodavan — через API/UI, не прямой write агентом. |
| **X-Cabinet-Id** | HTTP-заголовок, указывающий активный кабинет пользователя. Обязателен для project-scoped endpoints. |

---

## Безопасность

| Термин | Определение |
|--------|-------------|
| **Trust boundary (граница доверия)** | Интерфейс, где меняется уровень доверия к данным: браузер → API, API → БД, pod → MCP gateway. |
| **STRIDE** | Модель угроз: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege. |
| **Cross-cabinet leak** | Утечка данных между кабинетами одного или разных tenant — критическая уязвимость платформы. |
| **Break-glass** | Аудируемый аварийный доступ platform admin к данным tenant (инцидент, поддержка). |

---

## Технологический стек

| Термин | Определение |
|--------|-------------|
| **Prodavan** | Целевая платформа: Flutter (web/desktop/mobile) + FastAPI + PostgreSQL + k3s. |
| **FastAPI** | Backend REST/WS/SSE, JWT auth, orchestration API. |
| **Flutter** | Клиентское приложение; UI рендерится по cabinet profile manifest. |
| **k3s** | Лёгкий Kubernetes для worker pods, network policies, secrets. |
| **OpenAPI** | Спецификация REST API платформы; codegen для Flutter-клиента. |

---

## Сокращения

| Сокращение | Расшифровка |
|------------|-------------|
| P/N, PN | Part number — партномер |
| ACL | Access Control List |
| JWT | JSON Web Token |
| SSE | Server-Sent Events |
| WS | WebSocket |
| UI manifest | JSON-описание экранов и навигации кабинета |

---

## Связанные документы

- [product-vision.md](01-vision/product-vision.md)
- [domain-model.md](01-vision/domain-model.md)
- [overview.md](02-architecture/overview.md)
- [multi-tenancy.md](02-architecture/multi-tenancy.md)

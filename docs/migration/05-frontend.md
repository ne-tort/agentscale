# 05 — Frontend: миграция Flutter

## 5.1 Принцип: контракт фронтенда — источник истины

Фронт **не знает о k8s** — только REST-поля `observed_state`/`runtime.*` и SSE-протокол. Миграция фронтенда = точечная адаптация под **сохранённый** контракт (04 гарантирует словарь `observed_state` и все ручки). Цель: **минимальный diff**, чистка legacy-долгов из frontend-report §5–7.

## 5.2 Контракт фронтa — гарантируется бекендом (не менять без версионирования)

| Контракт | Гарантия после миграции |
|---|---|
| Ручки `/launch /pause /resume /reload /sync /agent/reset /container /container/metrics /metrics`, admin/company-зеркала | unchanged |
| `observed_state` словарь: preparing, provisioning, pulling, hydrating, starting, running, degraded, failed, paused, absent, unknown | unchanged (маппер conditions→словарь, 04.2) |
| `runtime`-поля: `pod_id, k8s_pod_name, runtime_ref, restarts, last_error, started_at, pod_created_at, metrics, metrics_available` | unchanged; `stub` всегда false (k8s/sandbox); `object-ws:` префикс исчезает навсегда |
| SSE: `type/data` события + `_session/_turn_complete/_error` | unchanged |
| Sidebar `new_chat_enabled` и wake-UX | unchanged (sendable = active && running) |

**Пауза = новый UX-момент**: resume теперь быстрый (suspend/resume, секунды). `pollProjectContainerUntilSettled` (1 с интервал) остаётся — таймаут 12 мин станет избыточным, сократить до 3 мин.

## 5.3 Обязательные изменения (малые)

1. **`lib/core/containers/container_runtime_presenter.dart`**:
   - удалить `object-ws:` отсечку в `containerK8sPodName`/`runtime_ref` (источник исчез);
   - `runtime.stub` логика: читаем поле, но трактуем `stub:false` — код стаба удалить;
   - **двойное чтение** `observed_state` (top-level + runtime.*) — оставить на переходный период (бекенд шлёт оба — дешёво), но парсинг собрать в один `ContainerRuntime.fromJson` (typизированная модель) — центральная точка (см. 5.4);
   - `phaseSubtitle`: фазы `pulling/hydrating` станут редкими (cold start) — строки оставить, добавить `waking`-подпись не вводим (лишний l10n-ключ).
2. **`project_container_poll.dart`**: терминальные статусы `running|failed|paused` — unchanged; после `resume` фронт может увидеть `pausing`→`paused`→`provisioning`→`running` (suspend/resume быстрый) — poll терпит, ок.
3. **`project_lifecycle_jobs.dart`**: строковые матчинги `409 pod_already_exists` / `422 not paused` → **заменить на `code` из JSON-body** (бекенд добавляет стабильные коды ошибок: `POD_ALREADY_EXISTS`, `NOT_PAUSED`, `POD_NOT_RUNNING` — уже есть как AppError codes; пробросить в REST-ответ). Это чинит хрупкость №5 из frontend-report.
4. **Чат**: `ChatSessionController` — добавить **один автоматический retry** SSE при обрыве до `_error` (resume из suspend может разорвать соединение посреди turn): повторный `send` с `resume_stream=true`-флагом? — нет, проще: при `AgentStreamError(bridge_unreachable)` и `observed_state==running` — один повтор через 1 с. UI показывает «переподключение…» (новый l10n-ключ `chatReconnecting`).
5. **l10n**: ключ `errorAgentStubResponse` — удалить (стаб-агентов нет); `containerObserved*` — оставить.

## 5.4 Техдолг-чистка (в той же волне, низкий приоритет)

- **Декомпозиция `prodavan_api.dart`** (2063 строк): вынести auth-классы (`AuthApiClient/SessionStore/AuthHttp` → `lib/core/auth/`), SSE-клиент → `lib/core/api/project_chat_stream.dart`, контейнерные ручки → `lib/core/api/container_api.dart`. Контракт не меняется — чисто структура.
- **`ContainerRuntime` typизированная модель** (`lib/core/containers/container_runtime.dart`): парсинг всех runtime-полей в одном месте; presenter-функции → методы модели. Все экраны (5 файлов двойного чтения) мигрируют на модель.
- Attachments base64 → multipart: **отложить** (не рантайм-проблема, отдельный PR).
- Тесты: обновить `container_runtime_presenter_test` (object-ws кейсы удалить), `chat_projection_test` (retry), новые — модель.

## 5.5 Что НЕ делаем на фронте

- Не вводим WS вместо SSE (poll+SSE достаточно; router WS-проксирование — запас на будущее).
- Не показываем warm/cold launch_type (пользователю всё равно; в admin-метриках видно).
- Не меняем job-store, auto-refresh, auth-стек — переиспользуются как есть.
- `pod_ip`/`generation` фронт не использует — можно не присылать (бекенд перестаёт слать pod_ip — router сам резолвит; **проверить admin_container_detail** — там k8s_pod_name остаётся).

## 5.6 nginx.conf / Dockerfile

- Без изменений. `proxy_pass http://prodavan-api:8000` валиден (имя сервиса не меняется). Если API-деплой переименуется — один diff в nginx.conf.

## 5.7 Приёмка фронта (S7)

- [ ] Launch job: `preparing→provisioning→running` ≤ 5 с (warm), прогресс-подписи корректны
- [ ] Pause→Resume: транскрипт цел, файлы workspace доступны после resume (S4 через UI files)
- [ ] Chat при suspend-обрыве: auto-retry отработал, сообщение не потеряно (PG SoT)
- [ ] Метрики контейнера: CPU/mem из metrics_available (как раньше)
- [ ] `object-ws:`/`stub` кейсы удалены из тестов; коды ошибок из body
- [ ] i18n: нет битых ключей (flutter gen-l10n + анализ unused)

# Web SSE bridge contract

Статус: migration contract. Этот документ описывает переход от legacy
`POST /projects/{project_id}/chat/stream` к canonical `web_facade`/`web_api`
контрактам. Он не меняет legacy route и не является обещанием, что текущий
route уже отдает canonical wire format.

## 1. Границы и источники истины

Bridge располагается между legacy `AgentSessionService.iter_chat_turn_sse()`
и application `WebFacadeAdapter.run_chat()`. Legacy transport сейчас отдает
только `data: <JSON>\n\n`, причем idle keepalive является SSE comment
`: keepalive\n\n`; canonical application event имеет
`RunStreamEvent(type, run_id, seq, text, block, data)`.

Canonical transport envelope (`web_api.SseEventEnvelope`) является wire
контрактом для браузера:

```json
{
  "event_id": "uuid-v5",
  "event_type": "text_delta",
  "schema_version": 1,
  "occurred_at": "2026-01-01T00:00:00Z",
  "project_id": "project-1",
  "session_id": "session-1",
  "run_id": "run-1",
  "sequence": 7,
  "payload": {"text": "hello"},
  "resume_after": 7
}
```

В настоящем SSE кадре bridge должен сериализовать envelope так:

```text
id: <event_id>
event: <event_type>
data: <JSON envelope>

```

`id` и `event` являются SSE transport-полями; те же значения дублируются в
JSON как `event_id` и `event_type`. `event_id` стабилен для одной пары
`(run_id, sequence)`: canonical `web_api` использует UUIDv5 от
`prodavan:web-run:{run_id}:{sequence}`. Поэтому повторная доставка после
reconnect имеет тот же ID.

## 2. Legacy translation

Для legacy object `raw` bridge сначала нормализует mapping и затем валидирует
canonical модель. Разрешены следующие aliases:

| Legacy `type`/смысл | Canonical `RunEventType` | Wire `event_type` |
| --- | --- | --- |
| `start`, `started` | `run_started` | `run_started` |
| `text_delta`, `delta` | `text_delta` | `text_delta` |
| `block` | `block` | `transcript_block` |
| `tool_call` | `tool_call` | `tool_call` |
| `tool_result` | `tool_result` | `tool_result` |
| `done`, `complete` | `run_completed` | `done` |
| `error` | `run_failed` | `error` |
| `cancel` | `run_cancelled` | `status` |
| `keepalive` | `heartbeat` | `keepalive` |

`type` берется из `raw.type`, затем `raw.event`, затем `data.type`; alias
удаляется из `data`, чтобы не дублировать управляющее поле. `text`, `block`,
`run_id` и `seq` могут находиться на верхнем уровне либо в `data`. Остальные
поля остаются в canonical `RunStreamEvent.data`. `block` должен пройти
валидацию `ChatBlock`.

Legacy control frames `type: "_session"` и `type: "_turn_complete"` не
являются canonical run events. Bridge обязан преобразовать их в допустимое
canonical событие с тем же run/session context (обычно `run_started` для
session metadata и `run_completed` для turn completion), либо поместить их
метаданные в `data` соответствующего события. Нельзя отдавать эти legacy type
напрямую в `event_type`: browser contract их не знает. В текущем legacy потоке
`_session` содержит `session_id`/`turn_id`, а `_turn_complete` содержит
`session_id`/`turn_id`/итоговые поля, но ни один frame не содержит `run_id` или
`seq`; поэтому bridge не может сделать их replayable envelope без назначенного
run context и последовательности. Пока такой context не определён, выбором
является structured non-replayable protocol error, а не синтез UUID/sequence.

Idle comment `: keepalive` не содержит event ID и sequence и не является
replayable application event. При canonical transport его следует заменить на
replayable `heartbeat` envelope только если heartbeat действительно имеет
выделенный sequence. Если sequence для idle heartbeat нет, оставить SSE
comment допустимо, но клиент должен игнорировать comment и не менять cursor.

## 3. Identity и cursor compatibility

Есть две совместимые формы cursor:

* canonical external `event_id` — UUIDv5 из `(run_id, sequence)`;
* legacy/application cursor `run_id:sequence` (также допускается bare
  non-negative `sequence`, только когда endpoint уже однозначно связан с одним
  run).

`Last-Event-ID` принимает обе формы. UUID нельзя трактовать как sequence и
нельзя сравнивать лексикографически. Для UUID bridge разрешает его только
после детерминированного обратного сопоставления с известным stream/run; если
такого сопоставления нет, ответ `400 INVALID_LAST_EVENT_ID`. Для migration
внутренний adapter может принимать `run_id:seq` напрямую, как делает
`parse_last_event_id`.

Cursor означает **exclusive** lower bound: после `run-1:7` выдаются только
события `run-1` с `seq > 7`. `resume_after` в wire envelope равен sequence
этого события и не заменяет `event_id`.

`run_id` и `seq` образуют составной cursor. В canonical application model это
sequence конкретного run. Legacy persistence сейчас хранит `AgentEventRow.seq`
только в области `session_id`: он увеличивается по всей сессии и не содержит
`run_id`/`turn_id` в каждой записи. Поэтому legacy `seq` нельзя объявлять
canonical run sequence без отдельного run mapping; bridge обязан либо получить
такой mapping из detached-turn context/index, либо явно оставаться в
session-local compatibility режиме. В session-local режиме UUIDv5 и replay
должны быть помечены как недоступные, а не вычисляться из ложного `run_id`.

`run_id` и `seq` образуют составной cursor. Sequence из другого run никогда не
может продолжить текущий stream. Если cursor содержит run ID, bridge обязан:

1. передать `after_seq` в legacy service для эффективного tail;
2. отфильтровать каждый результат по тому же `run_id`;
3. отфильтровать `event.seq <= after_seq` после translation;
4. не подставлять cursor run ID в событие, где legacy явно сообщил другой
   `run_id`.

Fallback `fallback_run_id=cursor.run_id` допустим только для legacy frame без
run ID и только в рамках уже авторизованного stream. Если одновременно
присутствуют разные run IDs, это malformed/conflicting frame, а не повод
«выбрать первый».

Bare sequence (`Last-Event-ID: 7`) совместим с legacy endpoint только когда
run был однозначно создан текущим запросом. При multi-run/session tail bridge
должен отклонять такой cursor (`400 AMBIGUOUS_LAST_EVENT_ID`), чтобы не
пропустить или не выдать события другого run.

## 4. session_id и cross-run filtering

`RunStreamEvent` сам по себе несет `run_id`, но не `session_id`; canonical wire
adapter добавляет `session_id` из авторизованного request и `project_id` из
route. Bridge обязан проверить, что run относится к запрошенным
`project_id/session_id`, прежде чем сериализовать envelope. Нельзя принимать
`session_id` из произвольного payload как замену scope check.

События другого run отбрасываются до выдачи браузеру, даже если их sequence
больше cursor. События другого session/project являются scope violation или
server-side data-integrity failure, а не обычным empty replay; они не должны
утечь в SSE.

При reconnect с cursor старого run новый `POST` не должен незаметно начать
другой run и продолжить старый cursor. Реализация выбирает одно из двух
явных поведений: (a) reattach к тому же run; (b) `409 RUN_NOT_RESUMABLE` с
указанием заново получить transcript. Смешивание cursor старого run с новым
run запрещено.

## 5. malformed frames и ошибки

Ошибки до открытия stream возвращаются обычным HTTP JSON error envelope
(`code`, `title`, `status`, `detail`, при наличии `trace_id`):

| Условие | HTTP | Code |
| --- | ---: | --- |
| отсутствует/невалиден bearer | 401 | `UNAUTHENTICATED` |
| нет web chat scope | 403 | `FORBIDDEN` |
| project/session вне tenant/resource scope | 403 (или 404 по policy сокрытия) | `FORBIDDEN`/`NOT_FOUND` |
| invalid body / missing prompt | 422 | `VALIDATION_ERROR` |
| malformed или ambiguous `Last-Event-ID` | 400 | `INVALID_LAST_EVENT_ID` / `AMBIGUOUS_LAST_EVENT_ID` |
| cursor/run не может быть продолжен | 409 | `RUN_NOT_RESUMABLE` |

После открытия SSE connection ошибка приложения является canonical `error`
событием, а не HTTP status, поскольку HTTP status уже отправлен. Envelope
должен сохранять `project_id`, `session_id`, `run_id` если известен,
`sequence` если ошибка принадлежит persisted event, и payload вида:

Legacy route currently emits both `type: "_error"` for caught `AppError` and
generic exceptions, while `iter_turn_tail()` may emit bare `type: "error"` with
only `data.code`/`data.message`. The bridge must map both forms to the same
canonical `run_failed`/wire `error` payload, normalize `message` to the safe
`detail` field when no detail is present, and never expose raw exception text.
A caught error after headers is terminal for that SSE attempt; automatic browser
reconnect is allowed only when the error code is explicitly retryable and the
same detached turn can be reattached.

```json
{
  "code": "INTERNAL",
  "title": "Internal error",
  "detail": "safe diagnostic text",
  "status": 500
}
```

Legacy `_error` преобразуется в `run_failed`/wire `error`; его `data.code`,
`title`, `detail`, `status` сохраняются в `payload`. Legacy bare `error` без
run/seq может быть отправлен только как terminal error с назначенными
bridge run ID и sequence из stream context. Если context отсутствует,
bridge не должен синтезировать replayable event: логирует malformed frame и
закрывает stream после best-effort non-replayable protocol error. Исключение
не должно ронять соединение без structured terminal indication, если
canonical context уже установлен.

Malformed frame: не-mapping, неизвестный type, отсутствующий run ID без
допустимого fallback, отсутствующий/нечисловой/отрицательный seq,
некорректный block или невалидный payload. Такой frame не пропускается
частично. Для ошибки до первого canonical события допустим HTTP 502
`UPSTREAM_STREAM_MALFORMED`; после открытия — wire `error` с code
`UPSTREAM_STREAM_MALFORMED` и закрытие. Не выдавать `str(exc)` без redaction в
production payload.

## 6. Auth and scope boundary

Авторизация выполняется до вызова legacy service и отдельно от translation.
Минимальный scope для run: `web:chat_run` в `AdapterScope` либо
`web:chat_stream`/эквивалентный transport scope в `WebApiScope`; отсутствие
scope deny-by-default. Требуется authenticated user с непустым ID.

`WebRequestContext` проверяет project и session; `ScopeBoundary.session()`
должен быть вызван с обоими route/request IDs. `WebApiAuthContext` строится
из проверенного Bearer/JWKS principal и active employee entitlement; IDs из
JWT claims не заменяют route/work context. Platform admin может обходить
выбор project там, где это разрешено policy, но не получает implicit access к
явно не связанному session.

Не передавать в legacy service данные до scope checks. Не использовать
`run_id`, `session_id` или `project_id` из непроверенного event payload для
расширения доступа. Replay обязан повторно пройти тот же auth/scope boundary,
что и initial run.

## 7. Idempotency, reconnect и browser client

Start request и reconnect различаются. `turn_id` является idempotency key для
создания detached turn: повторная отправка того же `(project_id, session_id,
turn_id)` должна reattach/replay существующий turn, а не запускать второй
turn. Если legacy не может гарантировать это, client должен не повторять POST
после сетевого разрыва без явного server response.

Browser client (`apps/web/src/api/client.ts`) должен:

* отправлять `Accept: text/event-stream`, Bearer и `Last-Event-ID` только при
  reconnect;
* парсить SSE `id:` и `event:` вместе с `data:`; не полагаться только на JSON
  поля и не считать comments событиями;
* валидировать envelope и игнорировать duplicate `event_id`/sequence,
  сохраняя последний принятый cursor только после успешной обработки;
* использовать `event_id` для следующего `Last-Event-ID`, а не
  `resume_after` и не только sequence;
* при `error` показать terminal error и прекратить автоматическое повторение,
  если code не помечен retryable;
* при EOF/сетевом разрыве переподключаться с тем же `turn_id` и последним
  cursor с bounded backoff; не создавать новый turn автоматически; поскольку
  POST сейчас не принимает отдельный `last_event_id` и legacy route игнорирует
  `Last-Event-ID`, reconnect wiring должен передать cursor в adapter до вызова
  legacy service, иначе повторный POST стартует/reattach-ит turn, но не имеет
  гарантированного replay boundary;
* считать `run_completed`, `run_failed` и `run_cancelled` terminal states;
* принимать `keepalive`/SSE comments как liveness и не сдвигать cursor для
  comment без `id`.

SSE frame processing должен поддерживать многострочный `data:` (строки
объединяются через `\n`) и CRLF. Простое разделение только по `\n\n` без
обработки `id/event/comments` не является полным canonical browser contract.
Парсер должен также корректно обработать финальный frame без завершающего
пустого разделителя при EOF, игнорировать неизвестные SSE-поля, сохранять
`id`/`event` transport values до JSON validation и считать frame malformed,
если transport `id`/`event` конфликтует с `event_id`/`event_type` envelope.

## 8. Migration acceptance matrix

| Сценарий | Обязательное поведение |
| --- | --- |
| legacy `{"type":"delta", "run_id":"r", "seq":8, "data":{"text":"x"}}` | canonical `text_delta`, wire `event_type=text_delta`, stable ID для `r:8` |
| reconnect `Last-Event-ID: r:7` | service получает `after_seq=7`, выдаются только `r` и `seq>7` |
| reconnect с UUID event ID | UUID разрешается в известный `(run,seq)` либо HTTP 400; numeric compare UUID запрещён |
| stream содержит `r2:99` после cursor `r1:7` | frame отбрасывается или integrity error; в browser не уходит |
| idle legacy comment | не меняет cursor; optional canonical heartbeat только с валидным seq |
| legacy `_error` после headers | canonical wire `error`, structured payload, terminal handling |
| malformed no-run/no-seq frame (включая legacy `_session`/`_turn_complete` без seq) | не сериализуется; HTTP 502 до stream или terminal protocol error после открытия |
| scope mismatch | legacy service не вызывается, HTTP 401/403/404 по policy |
| duplicate reconnect delivery | одинаковый event ID; browser deduplicates без повторного UI effect |
| retry same `turn_id` | reattach/replay, не второй detached turn |

## 9. Known implementation gaps

1. Legacy `agent.py` пока не выставляет SSE `id`/`event` и не принимает
   `Last-Event-ID`; route остается raw compatibility source.
2. Legacy service emits `_session` и `_turn_complete`, а текущий adapter
   parser знает только canonical event aliases; bridge wiring должен явно
   нормализовать эти control frames.
3. `web_facade.SseEventEnvelope` использует `id/event` и `run_id:seq`, тогда
   как `web_api.SseEventEnvelope` использует UUID `event_id` и
   `event_type`. Они не должны смешиваться на одном wire boundary: facade
   envelope является application envelope, web_api envelope — browser wire
   adapter.
4. Текущий browser parser читает только `data:` JSON и не обрабатывает
   `id:`/`event:`/comments как полноценный SSE parser. Это обязательная
   follow-up часть transport cutover, но не выполняется этим migration
   документом.
5. Текущий `WebApiFacade.run_chat` protocol не принимает отдельный
   `last_event_id`; reconnect propagation должен быть добавлен на transport
   wiring, сохранив данный contract как целевой behavior.
6. Legacy `AgentEventRow` связывает записи с `session_id` и session-local
   `seq`, но public event из `_event_public()` не передаёт даже `session_id` и
   не содержит `run_id`; `DetachedTurn.turn_id` не связывается с каждой
   записью.
   Поэтому UUIDv5 `(run_id, sequence)` и безопасная фильтрация нескольких run
   пока не могут быть восстановлены из обычного tail без отдельного run
   context/индекса. Нельзя считать `session_id` заменой `run_id`.
7. `legacy_event_to_run_event()` сейчас отклоняет `_session`, `_turn_complete`
   и `_error` как неизвестные types; кроме того, `_session`, `_turn_complete` и
   bare terminal `error` от `iter_turn_tail()` не имеют `seq`. Их перевод
   требует явной bridge-политики (назначенный run/sequence или
   non-replayable protocol error), а не простого вызова текущего parser.
8. `parse_last_event_id()` сейчас принимает только `run_id:sequence` или bare
   non-negative sequence. UUID `event_id` не декодируется, а `ValueError` из
   parser не является автоматически оформленным `400 INVALID_LAST_EVENT_ID`.
   Для UUID reconnect нужны детерминированный resolver и transport-level error
   mapping; до этого UUID acceptance scenario остаётся незакрытым.
9. Adapter фильтрует `event.run_id` по cursor только если cursor содержит
   `run_id`; при initial stream без cursor он не может проверить принадлежность
   каждого legacy event к requested run, project и session. `LegacyServices`
   также передаёт только `after_seq`, без run identity. Cross-run rejection и
   integrity error должны быть добавлены в wiring, а не подразумеваться
   текущим `run_chat()`.
10. `agent.py` при ошибке после headers сериализует legacy `_error` без `id`,
    `event`, `run_id` и `seq`; generic exception также раскрывает усечённый
    `str(exc)`. Целевой structured/error и redaction behavior из раздела 5
    пока не реализованы на legacy route.

11. Legacy persisted stream содержит расширенные `AgentEventType` (`usage`,
    `status`, `thinking_*`, `tool_*_delta`, `subagent_*`, `task_progress`,
    `permission_denial` и другие), тогда как adapter `RunEventType` и
    `legacy_event_to_run_event()` принимают только небольшой frozen-набор.
    Bridge должен иметь явную таблицу mapping этих типов в `block`/`tool_*`/
    `status`/`error` или объявить их unsupported с structured protocol error;
    молчаливое отбрасывание нарушает transcript/replay semantics.
12. Scope contracts содержат два разных run scope: adapter принимает
    `AdapterScope.CHAT_RUN`, а `WebApiScope` отдельно объявляет `CHAT_RUN` и
    `CHAT_STREAM`; transport wiring пока отсутствует. Canonical policy должна
    зафиксировать deny-by-default проверку transport scope (`CHAT_STREAM`) и
    application capability (`CHAT_RUN`) до вызова legacy service, включая
    replay/reconnect, а не считать наличие любого одного enum достаточным.
13. `WebApiFacade.run_chat()` не принимает `last_event_id`, тогда как adapter
    принимает его; без transport-level propagation `Last-Event-ID` теряется.
    Кроме того, `WebApiAuthContext.to_facade_context()` переносит только
    project context, а `session_id` проверяется лишь если он уже установлен в
    `WebRequestContext`; wiring обязан установить request session до
    `ScopeBoundary.session()` и не полагаться на JWT claims.

До устранения этих gaps legacy и canonical endpoints нельзя считать
поведенчески взаимозаменяемыми.

# 08 — Аудит согласованности и чек-листы

Модуль для финального аудита (субагенты) после реализации. Также — known-gaps, требующие решений.

## 8.1 Матрица согласованности фронт–бекенд–девопс

Каждая строка — точка пересечения трёх слоёв; проверяется аудитом на реализуемость и непротиворечивость.

| # | Точка | Фронт | Бекенд | Девопс | Статус |
|---|---|---|---|---|---|
| C1 | `observed_state` словарь | presenter читает 11 статусов | маппер conditions→ObservedState (04.2) | conditions контроллера | спроектировано |
| C2 | Ручки lifecycle | 14 ручек (frontend-report §2) | роутеры unchanged | — | ✅ без изменений |
| C3 | SSE-протокол | chat_projection | session_service unchanged | router WS/SSE passthrough | ✅ (проверить router timeout=180s против длинных turn — **gap G1**) |
| C4 | `:8001` доступность | — | main_pod surface | NetworkPolicy ns-wide egress 8001 | спроектировано |
| C5 | Bridge JWT доставка | — | post-Ready push | X-Prodavan-Runtime-Token через router (router стрипает Authorization — **gap G2**) | спроектировано |
| C6 | Образ runtime | — | settings → шаблон (только для сведений) | SandboxTemplate image tag, IfNotPresent | спроектировано |
| C7 | `k8s_pod_name` | admin-UI читает | sandbox.pod name через get_status | контроллер именует | ✅ |
| C8 | Метрики | ContainerMetricsWrap | PodMetricsPort (metrics-server) | metrics.k8s.io RBAC сохранён | ✅ |
| C9 | Файлы workspace | workspace-files 3 API | файлы через :8001 (runtime) или files-API | router | ✅ (канал не меняется) |
| C10 | wake-флоу | `_wakeProject` | resume = suspend→running | опер. seconds | ✅ |
| C11 | Pause семантика | paused-строка | sync_desired ABSENT→suspend | PVC выживает | ✅ |
| C12 | reload | rate-limited ручка | terminate+create+seed | Recreate-пул подхватит новый образ | ✅ |
| C13 | Job persistence | app_job_store | job-статусы из observed_state | — | ✅ |
| C14 | prodavan-claw submodule | — | identity endpoint | bump submodule в PR-5 | координация |

## 8.2 Known gaps (открытые вопросы)

| ID | Gap | Решение к фазе |
|---|---|---|
| G1 | Router `--proxy-timeout 180s` (default) не применяется к upgraded-соединениям, но к обычным HTTP-стримам — применяется: длинные агент-ходы > 3 мин могут рваться | Router config: `--proxy-timeout 600`; или SSE через WS-upgrade. **PR-2 включить** |
| G2 | Router стрипает `Authorization` перед форвардом: API→runtime токен нужно в `X-Prodavan-Runtime-Token` (X-* не стрипается) — проверить фактический стрип-лист роутера v1.0.4 | PR-5: прочитать код `sandbox-router/proxy/headers.go` (уже в отчёте: стрипает Host/Authorization) — X-* проходит ✅ |
| G3 | prodavan-claw (nested submodule) — endpoint identity: отдельный PR в claw-репо, версия образа должна совпасть с SandboxTemplate tag | PR-5: coordinate, submodule bump |
| G4 | RWO PVC: suspend-под с PVC на ноде A, resume-scheduling на ноду B — multi-attach violation. Single-node k3s — ок; multinode dev (если появится) — режим `Retain`+recreate или RWX | Не блокер; задокументировано в 03.8 |
| G5 | `degrade` статуса: контроллер не даёт «degraded»-сигнала; наш promote/demote flap-grace остаётся источником | 04.6 — сохранить promote_or_demote |
| G6 | HPA external-metrics требует Prometheus+adapter, в dev-кластере нет | 06.4 — ops-CLI autoscale CronJob как dev-вариант |
| G7 | `Sandbox.name` ≤ 63 chars (DNS) — имя генерит SDK `sandbox-claim-…`; ок; но наши лейблы `prodavan.io/workspace-key` — values DNS-safe уже (sanitize_dns есть) | ✅ нет действия |
| G8 | Старые «object-ws» проекты: миграционный путь (pause → seed) — data-loss риск если MinIO-снапшот протух | PR-7: миграцию делать только со свежим checkpoint |
| G9 | E2E тесты: `tests/e2e/k8s/` завязаны на старый адаптер — переписать | PR-4 |
| G10 | `probe-pod` остаётся на старом паттерне Deployment — консистентность: перевести на SandboxTemplate? | Опционально Wave 4 |

## 8.3 Аудит-чек-лист (для субагентов после реализации)

**Слой-согласованность:**
- [ ] C1: словарь observed_state в маппере == словарь presenter'а (diff списков)
- [ ] C3: SSE-события из sandbox-чата идентичны событиям из k8s-чата (e2e сравнение протоколов)
- [ ] C5: токен Bridge отсутствует в `kubectl get pod -o yaml` (env/volume/secret-ref)
- [ ] NetworkPolicy: egress из сандбокса к minio:9000/mongo:27017/opensearch:9200/5432-incluster — denied; к :8001 — 401 (не connection-refused)
- [ ] Router: чат-стрим 5+ минут не рвётся (G1)
- [ ]claim deletion: PVC удалён (no orphan), Service удалён
- [ ] Suspend: pod gone, PVC present, Service present; resume: тот же PVC (volumeName match)

**Код-аудит:**
- [ ] Нет обращений к `infrastructure/k8s/sandbox/client.py` из живого кода (после PR-8)
- [ ] Нет `imagePullPolicy: Always` в шаблоне; тег образа не `:latest`
- [ ] `POD_SANDBOX_IMAGE*` не в ConfigMap
- [ ] zombie-reaper/cluster-heal удалены; CronJob нет
- [ ] settings: нет мёртвых POD_* ключей; env-matrix.md обновлён
- [ ] Frontend: grep `object-ws`, `runtime.stub` — только в legacy-комментариях/удалён; grep `pod_already_exists` — заменён на code
- [ ] Тесты e2e зелёные в CI (PR gate), включая sandbox-lifecycle

**Документация:**
- [ ] docs/migration/ отражает реализованное (as-built сдвиг: 12-layer-docs?)
- [ ] runbook: pool-status, pod-migrate, rollback-инструкции
- [ ] agent-runtime-delivery.md: путь доставки обновлён (template-tag flow)

## 8.4 Definition of Done миграции (вся)

- [ ] S1–S8 из 00-overview измерены и зелёные на dev
- [ ] Все PR-1..8 смержены, CI зелёный, Verify Dev прошёл
- [ ] Аудит 8.3 без блокеров
- [ ] Легаси-код удалён (PR-8), ревью-протокол docs/09-checklists
- [ ] 2 недели стабильной работы dev-кластера

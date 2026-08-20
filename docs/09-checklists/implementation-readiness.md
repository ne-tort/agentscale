# Implementation readiness — протокол оценки

Документация (фазы P0–P9) и готовность к коду (итерации I0–I9) оцениваются **раздельно**.

## Две шкалы

| Шкала | Вопрос | 10 | 8 (приёмка docs) | 6 | 4 |
|-------|--------|-----|------------------|---|---|
| **Doc** | Спека полная и согласована? | Все разделы, ADR, чеклисты | Достаточно для ревью | Пробелы в модулях | Противоречия |
| **Impl** | Можно начать код модуля без новых арх. решений? | Код + тесты в CI | Контракты + scaffold + seeds; dev стартует завтра | Docs есть, нет контрактов/спайков | Только markdown |

**Gate док-итерации (P9):** Doc avg ≥ 8.5, ни один пункт Doc &lt; 6.

**Gate code-итерации (I0):** Impl avg ≥ 7.0, ни один модуль Impl &lt; 5, blockers из [`module-readiness.md`](../10-implementation/module-readiness.md) закрыты.

---

## Критерии Impl (чеклист на пункт)

Для каждой задачи / модуля:

1. **Контракт** — OpenAPI path, JSON Schema, ERD column, или MCP tool signature зафиксированы в repo (не «позже»).
2. **Acceptance** — `checklist-implementation.md` с конкретными тестами (unit/integration/e2e).
3. **Зависимости** — upstream модуль Impl ≥ 7 или явно mock.
4. **Seeds** — pack files, fixtures, migration `0001` sketch.
5. **Risk spikes** — для Codex/Claude/RLS/agent: spike выполнен или помечен blocker с owner.
6. **CI path** — как прогонять локально и в GHA (даже если workflow ещё stub).

---

## Переаудит 2026-08-20

| Phase | Doc avg | Impl avg (до) | Impl avg (после bridge) | Bridge-артефакты |
|-------|---------|---------------|-------------------------|------------------|
| P0 | 10 | 10 | 10 | — |
| P1 | 9 | 5 | **8** | infra dirs, prompt seeds |
| P2 | 8.9 | 6 | **8** | `packages/schemas/cabinet-profile/v1.json` |
| P3 | 8.5 | 5 | **7** | per-module checklists (без кода) |
| P4 | 8.0 | 4 | **5** | spikes не выполнены |
| P5 | 8.4 | 3 | **4** | нет `flutter create` |
| P6 | 8.6 | 5 | **7** | `apps/api/openapi/openapi.yaml` stub |
| P7 | 8.1 | 3 | **5** | infra README stubs |
| P8 | 8.7 | 6 | **8** | pack + prompts copied |
| P9 | 9.0 | 6 | **9** | этот протокол + roadmap |
| **Overall** | **8.7** | **5.1** | **6.7** | |

**Вывод:** док-фoundation принят (Doc 8.7). Код **ещё рано** как единый релиз (Impl 6.7 &lt; 7). Можно параллельно стартовать **I0 scaffold** (API + Flutter shell) и **P4 spikes**.

---

## Blockers → owner

| ID | Blocker | Блокирует | Действие |
|----|---------|-----------|----------|
| BL-01 | Codex/Claude headless auth не проверен | M07 provider switch | Spike по [`codex-cli-spike.md`](../06-agent-runtime/codex-cli-spike.md) |
| BL-02 | Нет FastAPI/Alembic scaffold | M08, M00–M02 | I0-T01 |
| BL-03 | Нет Flutter project | M00 UI, NavGate | I0-T02 |
| BL-04 | Capability naming: pack `procurement.*` vs ADR `search.*` | M00 auth, FeatureGate | См. [`module-readiness.md`](../10-implementation/module-readiness.md) § M00 |
| BL-05 | OpenAPI ~5% paths | Flutter codegen | I1–I3 по модулям |
| BL-06 | Нет `.tf` / k8s YAML | staging deploy | I7 |
| BL-07 | AGENTS.md seed — Commerce wording (Telegram) | M03 seed installer | Адаптация в I3 |

---

## Процесс обновления

После каждой code-итерации I*:

1. Обновить **Impl** в [`PROGRESS.md`](PROGRESS.md).
2. Отметить пункты в `docs/03-modules/M*/checklist-implementation.md`.
3. Поднять OpenAPI / schema version при breaking change.
4. Прогнать [review-protocol.md](review-protocol.md) § Implementation.

См. также: [`10-implementation/roadmap.md`](../10-implementation/roadmap.md).

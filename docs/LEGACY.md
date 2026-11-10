# LEGACY — карта и политика документации

## Главное правило

**Legacy — статус конкретного документа, а не «всё остальное» и не имя каталога.**
Исторические документы сохраняются; актуальные accepted ADR и ops остаются
действующими независимо от расположения.

| Категория | Что входит | Как использовать |
|---|---|---|
| **Current product** | [PRODUCT.md](PRODUCT.md), код S1 и его backend/web contracts, [employee-ui/README.md](employee-ui/README.md), актуальные [07-infrastructure/](07-infrastructure/) runbooks | Продуктовый ориентир и текущая эксплуатация. Код важнее старого gap map. |
| **Target design** | ADR-006/007/008, [PLUGIN-AUTHORING.md](PLUGIN-AUTHORING.md), [ARCHITECTURE/plugin-architecture-complete.md](ARCHITECTURE/plugin-architecture-complete.md) **с errata**, предложенный ADR-009 | Спроектировано или предложено, но не доказано как работающее. Target нельзя пересказывать как as-built. |
| **Accepted ADR** | [architecture-migration/README.md](architecture-migration/README.md) и отдельные принятые решения вне него, перечисленные ниже | Фиксирует архитектурное решение, **не** факт реализации или live acceptance. Supersede читается явно. |
| **As-built** | Текущие код, манифесты и машинные схемы; проверенные live результаты с датой, HEAD и evidence | Единственный способ сказать «реализовано сейчас». Написанный код ≠ подтверждённый live сценарий. |
| **Migration reports** | Текущие [plugin-migration-status.md](migration/plugin-migration-status.md), [plugin-acceptance-map.md](migration/plugin-acceptance-map.md), [plugin-cutover-safety.md](migration/plugin-cutover-safety.md); остальные migration reports — со своей датой и статусом | Сначала читать STOP-SHIP и acceptance. Старые числа тестов и Running — не текущие результаты. |
| **History / legacy** | Старый AI-канон, tenant/M00–M09/desktop/Flutter-описания, gap maps и superseded проекты | Справка и история решений. Не блокер и не причина удалять файл. |

## Accepted ADR вне plugin-реестра — НЕ legacy по каталогу

Следующие документы остаются актуальными accepted/as-built-phase решениями,
пока отдельный ADR явно не отменит их:

| Решение | Документ |
|---|---|
| Document Store BC на MongoDB | [02-architecture/ADR-document-store-mongo.md](02-architecture/ADR-document-store-mongo.md) |
| Search Index BC на OpenSearch | [02-architecture/ADR-search-index-opensearch.md](02-architecture/ADR-search-index-opensearch.md) |
| Single-container project agent-runtime | [06-agent-runtime/adr-pod-agent-runtime.md](06-agent-runtime/adr-pod-agent-runtime.md) |

Это не означает, что **каждый** файл в 02-architecture или 06-agent-runtime
автоматически актуален: статус проверяется по шапке и supersede-ссылкам. Но
массово списывать эти решения в legacy по имени каталога запрещено.
ADR-backlog-module-instance-mongo.md — отдельный backlog, а не разрешение на
cutover.

## As-built и 12-layer snapshot

[target/12-layer-docs/](target/12-layer-docs/) — **устаревший исторический
snapshot до перерасчёта**, а не живая полная карта текущего кода. Его quality
scores и статусы не являются текущей приёмкой. Для current state использовать
[PRODUCT.md](PRODUCT.md), код, [STOP-SHIP status](migration/plugin-migration-status.md)
и [acceptance map](migration/plugin-acceptance-map.md).

Другие материалы [ARCHITECTURE/](ARCHITECTURE/) сочетают as-is-разбор на дату и
target design. Они не заменяют ни PRODUCT, ни текущий acceptance.

## История: что сохраняется как legacy

1. docs/target/01…15, gap maps и связанный AI-generated канон — справка, не
   блокер. Исключение 12-layer-docs описано выше как отдельный snapshot.
2. Старые tenant/M00–M09/desktop-материалы в docs/01-vision …
   docs/10-implementation — история, **кроме явно перечисленных актуальных ADR,
   agent-runtime contracts и 07-infrastructure**.
3. Корневой [00-glossary.md](00-glossary.md) — superseded.
4. Датированные docs/migration-audit-*.md, retrospective, integration/test
   reports и прошлые зелёные таблицы — исторические claims на свою дату; они не
   повышают текущий acceptance без нового live evidence.

docs/07-infrastructure — актуальные ops/runbooks, а не legacy; wsl-dev.md внутри
него — отдельная историческая справка об удалённом WSL.

## Правила изменения и чтения

1. Новые продуктовые требования — в [PRODUCT.md](PRODUCT.md).
2. Новые plugin-решения сначала получают явный статус «Предложено» / «Принято»;
   принятие ADR не превращается в отметку live acceptance.
3. Legacy **не удалять**: это история и источники ссылок из PR.
4. При правке действительно legacy-файла добавлять баннер HISTORY со ссылкой на
   PRODUCT и запретом использовать текст как as-built.
5. Не создавать новые BC-модули в старом docs/target/01…15 без явной задачи.
6. При конфликте проверять статус документа, accepted/superseded ADR, текущий код
   и свежие live evidence, а не выбирать файл по каталогу.

Термины старых документов **Tenant** ≈ **Company**, **Operator** ≈ **Employee**
применимы только при чтении истории.
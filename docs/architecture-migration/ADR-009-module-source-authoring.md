# ADR-009: ModuleSource authoring и trusted delivery

**Статус:** Предложено (2026-11-09)
**Не принято.** До явного перевода в «Принято» этот документ не supersede
ADR-006/007/008, не создаёт as-built контракт и не разрешает деплой или включение
plugin platform.
**Связанные:** ADR-006 (plugin-модули), ADR-007 (direct plugin DB), ADR-008
(lifecycle/boundaries), ADR-001 (dynamic UI sandbox), ADR-004 (runtime
revision), PLUGIN-AUTHORING.

## Контекст

Принятые ADR-006/007/008 описывают целевой runtime плагина, но не целевой путь от
авторского исходника до обновляемого и откатываемого артефакта. Ранние попытки
превратить plugin v4 в один вертикальный кусок не прошли независимый аудит:
на audited HEAD `6ee720dd` нет подтверждённого сквозного сценария. Plugin
platform остаётся emergency disabled после security audit.

Отдельно нужно закрыть безопасность authoring:
- agent не должен получать Git credentials, подписи или tokens публикации в pod;
- trusted CI должен быть не подменяемым вызывающим source;
- опубликованный artifact должен позволять согласованное обновление backend и
  frontend bundle;
- rollback кода и rollback БД — разные операции.

Машинные schema/protocol остаются зоной отдельного RPC-агента. Этот ADR не правит
их и не объявляет незавершённые детали фактом; после стабилизации машинная схема
является контрактом.

## Решение (Предложено)

### 1. ModuleSource как metadata, не второй runtime registry

Платформа ведёт `ModuleSource` — авторскую запись об исходнике и намерении сборки.
Минимальный смысловой набор:

- plugin id и человеческое имя;
- source repository и ref в platform org;
- reviewed source commit sha;
- путь к manifest и к backend/bundle/migrations источникам;
- шаблон/boilerplate, из которого создан source;
- ссылка на последнюю review/PR и решение review.

`ModuleSource` **не** дублирует runtime registry и не является вторым источником
истины по deploy-состоянию. В нём нет runtime routes, grants, health, activation
state, текущего deploy digest как независимого реестра и способа обойти artifact
registry. После публикации runtime registry связывает source coordinates и
проверенную artifact/revision запись.

### 2. GitHub App в platform org

Source живёт в приватных repositories platform org или создаётся из
platform-owned template. GitHub App принадлежит platform org и имеет минимально
необходимый scope только на доверенном control plane. Он:

- применяет подготовленный агентом patch к short-lived branch;
- открывает protected PR в защищённую default branch;
- не выдаёт Git credentials агенту или plugin backend;
- пишет audit-запись source/PR/actor/evidence.

Прямой push в default branch запрещён; review и required checks обязательны.

### 3. Agent snapshot/patch MVP без Git credentials в pod

MVP authoring:

```text
reviewed source snapshot
  → безопасная рабочая копия для agent без .git и credentials
  → agent patch/diff
  → platform control plane валидирует и передаёт GitHub App
  → protected PR → human review
```

Agent работает по snapshot/patch: не клонирует repository, не mint/push commit,
не имеет tokens GitHub и не является доверенным авторизатором. Patch не может сам
мерджить PR. Source snapshot — authoring-механизм; он не означает snapshot/перенос
tenant данных.

### 4. Protected PR и default branch

Default branch защищена: только PR, required human review, required status
checks, запрет force-push и deletion. Merge создаёт immutable source commit, по
которому запускается только approved trusted workflow.

### 5. Trusted reusable workflow, pinned SHA

Публикация выполняется platform-owned reusable workflow в trusted CI, вызываемый
по **pinned SHA**, не по mutable tag/branch. Вызывающий source workflow фиксирует
event, repository, ref, sha и не расширяет permissions workflow. Изменение
trusted workflow — отдельный защищённый PR и отдельно аудируемая смена pin.

### 6. OIDC trust constraints

Trusted CI получает короткоживущий OIDC token. Publisher/control plane проверяет
точные claims, а не подстроки:

- `repository` = ожидаемый platform-org repository;
- `ref` = ожидаемый protected ref;
- `sha` = reviewed merged commit;
- `job_workflow_ref` = platform reusable workflow на pinned SHA.

Несовпадение или недоступность проверяемого OIDC — fail-closed quarantine, без
fallback «верим запуску».

### 7. Backend + bundle digest pair

CI публикует и подписывает пару:

```text
backend OCI image digest      sha256:...
frontend/static bundle digest sha256:...
```

Пара атомарна для install/update/rollback: деплой невозможен по одному digest без
совместимого второго, pairing/fingerprint фиксируется в runtime registry вместе с
provenance/SBOM/signature evidence. `latest` запрещён; tenant deployment получает
только явные immutable digests. Пара digest — принятое/предложенное уточнение
контракта артефактов через этот ADR, пока не as-built.

### 8. Deploy saga

Deploy/activate — упорядоченная saga с idempotency key и явными компенсациями:

1. verify signatures/provenance/scan/CSP и compatibility pair;
2. persist artifact pair + evidence;
3. provisioning tenant DB/schema/role и secret rotation;
4. deploy по pair с `replicas: 0`;
5. plugin выполняет собственные migrations до readiness;
6. health/contract checks;
7. регистрация routes/grants и включение в menu;
8. public surface — только если он явно объявлен, отдельно проверен и разрешён;
9. post-activate audit и promotion/quarantine outcome.

**Что из этого реализовано (as-built 2026-11-10).** Шаги 1–6 и 8 — в коде:
grant выдаёт `webhook_path` и `pluginpage_path`, `public_pages` ограничены
манифестом и отдают 403 на всё остальное. Шаг 7 реализован **наполовину**:
маршруты регистрируются, меню отсутствует — ни поля в схеме манифеста v1
(закрыта `additionalProperties: false`), ни контракта, ни UI. Поэтому «включение
в menu» из этого шага остаётся невыполненным, а не забытым.

Ошибка в любом шаге не оставляет меню/public активным. Компенсации возвращают
предыдущую согласованную pair или оставляют instance явно disabled/quarantined;
частичная установка не считается success.

### 9. Update и rollback saga

Update — тот же trusted source path, новый PR и новая pair; in-place mutation
артефакта запрещён. Plugin migrations планируются code-first expand/contract так,
чтобы старая и новая pair могли сосуществовать на этапе перехода.

Rollback кода:

```text
current pair → verify previous pair → scale down
  → activate previous pair → health/routes checks
  → revoke new routes/public при необходимости → audit
```

Rollback БД не выполняется автоматически. Изменение схемы должно оставаться
совместимым по заданной retention/compat policy; destructive contract делается
отдельной миграцией после отказа от старого кода. Saga отличает rollback кода,
contract migration и lossy data operation; lossy операция не является
автоматическим откатом деплоя.

## Status и границы

- Статус **Предложено** до явного human/architecture решения.
- До принятия не реализуется как контракт и не отменяет ADR-006/007/008.
- После принятия отдельные задачи определяют storage fields, API, CI inputs и
  acceptance; написанный код не становится работающим без live evidence.
- Машинная schema/protocol не изменяется этим ADR и не описывается как
  стабилизированный контракт, пока отдельный RPC-agent не сообщит обратное.

## Acceptance sketch (после принятия, только live)

1. Agent snapshot не содержит Git credentials/signing secrets; попытка push из pod невозможна.
2. GitHub App создаёт protected PR только через control plane; direct push в default branch отклонён.
3. Trusted reusable workflow вызван pinned SHA; OIDC repository/ref/sha/job_workflow_ref проверены.
4. Publish отвергает отсутствие/несовместимость любого digest пары и плохой OIDC/provenance.
5. Deploy saga после ошибки не оставляет активными menu/public и пишет компенсацию.
6. Update создаёт новую pair без in-place mutation; rollback возвращает согласованную pair и фиксирует, что DB rollback не выполнялся автоматически.
7. Все шаги имеют idempotency key, audit и явный terminal outcome.

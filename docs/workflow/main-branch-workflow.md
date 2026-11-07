# Работа на main (dev) без feature-веток

**Статус:** правила для миграции plugin-modules (2026-11-07).

## Принцип

Все изменения (код, тесты, документация) вносятся **сразу в main** (dev-ветка). Отдельные feature-ветки не создаются.

Это отличается от предыдущей миграции (declarative-app), которая велась на feature-ветках (`feat/admin-keys-modules`, `fix/management-merge-scope` и т.д.).

## Почему

1. **Скорость** — не тратить время на создание/ребейз/force-push feature-веток.
2. **Прозрачность** — все изменения видны в `main` сразу; история линейна.
3. **CI Gate** — каждый PR проходит CI Gate → Auto-merge → CI Images → Argo sync → Verify Dev.
4. **Откат** — при проблеме откатывается merge-commit в main (или revert PR).

## Правила

### 1. PR обязателен

**Любое изменение** — даже "мелкий фикс" или "добавление ключа в i18n" — оформляется как PR в main.

- Нельзя коммитить напрямую в main без PR.
- Нельзя пушить в main без CI Gate.

### 2. Commit hygiene

- Commit message должен быть информативным (что изменено, зачем).
- Не коммитить `_commit.txt`, `pr-*.md`, `_*.mjs` helper-файлы (они в `.gitignore`).
- Не коммитить секреты, `.env`, артефакты сборки.

### 3. CI Gate

Каждый PR проходит:
1. **CI Gate** (lint, typecheck, unit tests, migration checks)
2. **Auto-merge** (при зелёном Gate)
3. **CI Images** (сборка образов)
4. **Argo sync** (деплой в dev)
5. **Verify Dev** (e2e + dns-check + smoke)

Если CI Gate красный — чинить в той же ветке (push в PR), дождаться green Verify Dev.

### 4. Документация

- Все изменения в коде, которые влияют на поведение, должны быть отражены в `docs/PRODUCT.md` или `docs/architecture/*.md`.
- ADR (Architecture Decision Records) создаются в `docs/architecture-migration/` для значимых решений.

### 5. Тесты

- Unit-тесты пишутся как часть реализации (verification freeze не применяется к plugin-modules миграции).
- Integration/e2e тесты — обязательны для capability adapters (ACL, квоты, аудит).

### 6. Откат

При проблеме после merge:
- Revert merge-commit в main (создаёт revert-PR).
- Или hotfix в отдельном PR (если revert слишком разрушителен).

## Исключения

### 1. Hotfix в production

Если нужно срочно починить prod (не dev), hotfix идёт в `prod` ветку (отдельный процесс, не описан здесь).

### 2. Экспериментальный код

Если нужно проверить гипотезу, которая может быть отклонена:
- Создать feature-ветку, но **не мержить** в main без явного решения.
- После эксперимента — удалить ветку или смержить как "experiment: ...".

## Инструменты

- `gh pr create --base main --head <branch>` — создать PR
- `gh pr view <number>` — статус PR
- `gh run list` — статус CI
- `git commit -m "..."` — commit с сообщением
- `git push origin main` — push в main (только после CI Gate)

## Пример workflow

```bash
# 1. Внести изменения
git add -A
git commit -m "Capability DB adapter: tenant-aware connection + RLS"

# 2. Push в main (или в feature-ветку, если нужно несколько коммитов)
git push origin main

# 3. Создать PR
gh pr create --base main --head main --title "Capability DB adapter" --body-file pr.md

# 4. Дождаться CI Gate
gh pr view 123 --json statusCheckRollup

# 5. При зелёном Gate — Auto-merge
# 6. Verify Dev — проверить на dev-контуре
```

## Связанные документы

- `docs/PRODUCT.md` — раздел "Целевая модель: plugin-модули"
- `docs/architecture/plugin-modules.md` — детальное описание архитектуры
- `docs/architecture-migration/ADR-002-plugin-modules.md` — решение о переходе

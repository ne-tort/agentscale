# M03 — Домен: промпты

## PromptFile

```typescript
interface PromptFile {
  path: string;              // relative to prompts/, e.g. profiles/kp/04-search.md
  content: string;
  sha256: string;
  size_bytes: number;
  updated_at: ISO8601;
  updated_by: user_id;
}
```

## PromptBundle

Снимок всего дерева prompts/ на момент версии.

```typescript
interface PromptBundle {
  version_id: string;        // ver_{ulid}
  cabinet_id: string;
  created_at: ISO8601;
  created_by: user_id;
  label?: string;            // "before S4B policy update"
  files: PromptFileManifest[];
  parent_version_id?: string;
}

interface PromptFileManifest {
  path: string;
  sha256: string;
  size_bytes: number;
}
```

## Иерархия rôles

| Уровень | Файл | Назначение |
| --- | --- | --- |
| Master | AGENTS.md | Глобальные правила агента кабинета |
| Profile | profiles/{id}/README.md | Оглавление профиля задач |
| Module | profiles/{id}/NN-topic.md | Пошаговые инструкции фазы |
| Skill | skills/{name}/SKILL.md | Опциональные скиллы |

Для `electronics-procurement` pack включает `profiles/kp/` (совместимость с Commerce).

## Versioning rules

1. Каждый save single file → optional auto-snapshot (debounced 5min)
2. Explicit «Сохранить версию» → new PromptBundle
3. Rollback = copy bundle files → working tree + new version «rollback to ver_X»
4. Max versions per cabinet: 500 (config), prune oldest

## Import modes

| Mode | Behavior |
| --- | --- |
| zip_replace | Replace entire prompts/ (confirm destructive) |
| zip_merge | Add/update by path, no delete |
| files | Multipart selected paths |

Export: zip with manifest.json + all files.

## Инварианты

| ID | Инвариант |
| --- | --- |
| INV-PRM-001 | Paths relative, no `..`, no absolute |
| INV-PRM-002 | AGENTS.md always exists after seed |
| INV-PRM-003 | Only .md and .json under prompts/ (allowlist ext) |
| INV-PRM-004 | Version immutability — bundle files read-only |
| INV-PRM-005 | Cross-cabinet: prompts cid_A invisible from cid_B |
| INV-PRM-006 | Max file size 512 KiB per MD |
| INV-PRM-007 | Switch cabinet invalidates editor buffer |

## Negative test IDs

| Test ID | Сценарий |
| --- | --- |
| NEG-PRM-001 | Path `../../etc/passwd` in save |
| NEG-PRM-002 | Import zip with executable |
| NEG-PRM-003 | Read prompts other cabinet |
| NEG-PRM-004 | Delete AGENTS.md without confirm |
| NEG-PRM-005 | Rollback without admin on prod |

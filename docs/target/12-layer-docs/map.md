# Карта связей (as-built)

Сводка **по факту кода**. По мере поставки: `-.->` → `-->`.

Канонический целевой граф: [11 sequence](../11-implementation-plan/sequence.md).  
Реестр контрактов: [contracts-index](../11-implementation-plan/contracts-index.md).

## Статус графа

`Status: partial` — L00 live; остальные planned.

```mermaid
flowchart TB
  L00[L00_skeleton]
  L01[L01_identity]
  L02[L02_ui_core]
  L03[L03_ai_keys]
  L04[L04_admin_company]
  L05[L05_employee_shell]
  L06[L06_cabinet_runtime]
  L07[L07_projects_runtime]
  L08[L08_agent]
  L09[L09_vertical]

  L00 --> L01
  L00 --> L02
  L00 --> L03
  L00 --> L06
  L01 -.-> L04
  L01 -.-> L05
  L01 -.-> L06
  L02 -.-> L04
  L02 -.-> L05
  L03 -.-> L04
  L03 -.-> L08
  L06 -.-> L05
  L06 -.-> L07
  L07 -.-> L08
  L04 -.-> L09
  L05 -.-> L09
  L06 -.-> L09
  L07 -.-> L09
  L08 -.-> L09
```

## Живые контракты

| ID | Поставщик | Потребители | Статус |
|----|-----------|-------------|--------|
| C-API-HEALTH | L00 | все | live |

## Заметки по интеграции

- L00 закрыт (Quality 8). Следующий: L02 UI core.
- См. [STUB.md](../../../STUB.md) / [L00 as-built](L00-platform-skeleton.md).

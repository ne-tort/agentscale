# Default cabinets

В поставке платформы — **два** кабинета.

## 1. Универсальный (`generic-assistant`)

| Аспект | Содержание |
|--------|------------|
| Назначение | Эталон «костей»: модульные промпты + MCP + чат |
| UI | Chat, prompts editor, MCP status |
| БД | Минимальная: sessions metadata, prompt versions |
| Специфика домена | Нет (намеренно) |
| Код сегодня | `prodavan/cabinets/generic_assistant` |

На нём накручивается специфика новых кабинетов (copy contract + add domain).

## 2. Подбор оборудования (`equipment-procurement`)

| Аспект | Содержание |
|--------|------------|
| Назначение | Спека → поиск → ранжирование → КП / характеристики |
| UI | Projects, specs, variants, KP, equipment cards |
| БД | runs, lineitems, offers, catalogs hooks |
| Legacy id | `electronics-procurement` (map 1:1 при миграции docs/code) |
| Код сегодня | `prodavan/cabinets/electronics_procurement` |

Product name в UI: **«Подбор оборудования»**. Технический `profile_id` целевой: `equipment-procurement`; до переименования в коде допустим alias на `electronics-procurement`.

## Регистрация

Оба модуля перечислены в platform cabinet catalog; Admin выдаёт компаниям grants; Company — сотрудникам.

# Default cabinets

В поставке — **два** профиля. Код apps сейчас stub; ниже — целевой смысл.

## 1. Base: `generic-assistant`

Полноценный **независимый** кабинет и **эталон клонирования**. Не «пустой шаблон без UX».

| Аспект | Содержание |
|--------|------------|
| Роль | Основа продукта: универсальная автоматизация задач агентом |
| Projects | EntityCollection |
| Chat | Streaming + файлы/картинки |
| Context UI | Prompts, skills, rules, MCP, seeds, AGENTS — пользователь управляет в UI |
| БД | Своя schema: версии промптов/skills/rules, MCP configs, seed refs, … |
| Домен | Нет закупочной/иной зашивки |

Новый кабинет = **copy** этого pack (BE+FE+assets) → сменить `profile_id` → достроить.  
Опционально позже: shared `_base` kit для helpers без копипасты низкоуровневого materialize.

Подробнее: [packaging.md](packaging.md) § Base.

## 2. Domain: `equipment-procurement`

| Аспект | Содержание |
|--------|------------|
| Роль | Подбор оборудования (спека → поиск → КП / характеристики) |
| База | **Копия/надстройка** base surfaces (projects, chat, context UI) |
| UI доп. | Specs, variants, KP, equipment, catalogs — manifest tabs |
| БД | Своя schema: runs, lineitems, offers, … |
| Legacy id | `electronics-procurement` → map на `equipment-procurement` |

UI name: **«Подбор оборудования»**.

## Регистрация

Оба в cabinet catalog; Admin → Company grants; Company → Employee assign.

## Связь

- [packaging.md](packaging.md) · [frontend.md](frontend.md) · [backend.md](backend.md)

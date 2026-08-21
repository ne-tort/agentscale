# Chat attachments

Чат проекта принимает вложения по образцу ChatGPT / Ollama project UI.

## Типы

| Тип | Примеры | Хранение |
|-----|---------|----------|
| Image | png, jpeg, webp | object store + path в inbox |
| Document | pdf, xlsx, csv, txt, md | object store |
| Archive | zip (по политике кабинета) | object store |

## UX

- Composer на chat page: кнопка «Файл» → системный picker (не modal приложения).
- Превью вложений в ленте сообщений.
- Лимиты размера/типа — из company policy + cabinet manifest.

## Runtime

1. Upload → platform storage under `project_id/inbox/…`.
2. Metadata в platform DB (`chat_attachments`).
3. При `chat.message` trigger пути файлов попадают в workspace (bind/mount или copy).
4. Агент (Cursor SDK multimodal и т.д.) получает ссылки согласно adapter capabilities.

Кабинет может дополнительно прогонять файл через свой pipeline (например xlsx → rows) по trigger `chat.message` с attachment kind.

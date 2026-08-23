# Chat attachments

Чат проекта принимает вложения по образцу ChatGPT / Ollama project UI.

## Типы

| Тип | Примеры | Хранение |
|-----|---------|----------|
| Image | png, jpeg, webp | **MinIO** (S3) + object key в inbox; не локальный path API |
| Document | pdf, xlsx, csv, txt, md | **MinIO** (S3) |
| Archive | zip (по политике кабинета) | **MinIO** (S3) |

Канон: [13-platform-infra](../13-platform-infra/). Локальный `storage/.../inbox` как SoT — дефект до закрытия P0.

## UX

- Composer на chat page: кнопка «Файл» → системный picker (не modal приложения).
- Превью вложений в ленте сообщений.
- Лимиты размера/типа — из company policy + cabinet manifest.

## Runtime

1. Upload → object store key `…/projects/{project_id}/inbox/…` (MinIO).
2. Metadata в platform DB (`chat_attachments`) — object ref, не host path.
3. При `chat.message` trigger объекты попадают в workspace pod (sync/mount из object store).
4. Агент (Cursor SDK multimodal и т.д.) получает ссылки согласно adapter capabilities.

Кабинет может дополнительно прогонять файл через свой pipeline (например xlsx → rows) по trigger `chat.message` с attachment kind.

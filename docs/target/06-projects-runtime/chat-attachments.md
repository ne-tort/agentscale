# Chat attachments

Чат проекта принимает вложения по образцу ChatGPT / Ollama project UI.

## Типы

| Тип | Примеры | Хранение / доставка |
|-----|---------|---------------------|
| Image | png, jpeg, webp | MinIO inbox + hot-push в Pod `/workspace/inbox/` |
| Document | pdf, txt, md, json, zip | MinIO inbox + hot-push; путь в prompt агенту |
| Tabular | csv, tsv, xlsx, xls, xml | **→ JSON**: ≤200 строк inline в bridge (+ UI spoiler); больше — `inbox/*.json` в Pod |

Канон object store: [13-platform-infra](../13-platform-infra/).

## UX

- Composer: «Файл» → системный picker.
- Превью: имя файла; inline JSON — спойлер, не простыня.
- Лимиты: company `max_attachment_mb`; чат — до **500k** знаков и **32** вложения на сообщение.

## Runtime

1. Upload → MinIO `projects/{workspace_key}/workspace/inbox/…` + row в `project_attachments`.
2. На `send` / chat turn: `AttachmentDeliveryService` готовит вложения, пишет в live Pod (k8s exec `workspace_fs write`), обогащает bridge message.
3. Агент видит либо путь `/workspace/inbox/…`, либо JSON в сообщении.

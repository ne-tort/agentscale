# Identity Brokers (VK / Yandex)

## Вердикт

| Тема | Канон |
|------|--------|
| Соцлогин | Только **Keycloak Identity Broker** |
| Prodavan API / Flutter | **Не** хранят VK/Yandex client secrets, **не** делают свой OAuth к IdP |
| Старт / callback | **Auth Service** `GET /auth/broker/{idp}/start` + `/auth/broker/callback` |
| Flutter | Открывает URL Auth Service; **не** ходит в Keycloak authorize/token |
| Для кого | Люди: **Employee** / **Platform Admin** |
| Company | **Без** broker — `company_id` + password через `POST /auth/login` |

## Поток

```text
Flutter → GET /auth/broker/vk|yandex/start
  → Auth Service 302 → Keycloak authorize (kc_idp_hint)
  → Broker → VK | Yandex
  → KC callback → Auth Service /auth/broker/callback
  → code exchange (in-cluster) → redirect app with tokens
  → API JWKS → Employee.keycloak_sub (authz из DB)
```

Broker **прозрачен** для authz: один `employees.keycloak_sub` на человека.

## Aliases

| Alias | Назначение |
|-------|------------|
| `vk` | VK IdP (включить позже, secrets вне git) |
| `yandex` | Yandex IdP |

Realm scaffold: пустой `identityProviders` + checklist в [`infra/keycloak/README.md`](../../../infra/keycloak/README.md).

## Flutter

- Password «Вход» → `POST /auth/login` ([`auth_api_client.dart`](../../../apps/flutter/lib/core/auth/auth_api_client.dart)).
- Соцкнопки (UI later) → `AuthApiClient.brokerStartUrl(...)` / open browser.
- Запрещено: `kc_idp_hint` напрямую на Keycloak из Flutter.

## Account linking и конфликты email

- First broker login / account linking — **в Keycloak**, не silent merge в Prodavan.
- Конфликт email (уже есть KC user) → linking UI / required actions KC.
- API **не** склеивает аккаунты по email без явного `sub`.

## Audit в app DB (опционально)

Таблица `identity_links` (`employee_id`, `provider`, `provider_subject`) — UX/support, **не** authz.

## Запреты

- App-level OAuth к VK/Yandex из Flutter (или прямые KC authorize/token/revoke).
- Broker для Company org principal.
- Коммит IdP secrets в git / realm JSON.

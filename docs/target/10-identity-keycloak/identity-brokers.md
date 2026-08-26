# Identity Brokers (VK / Yandex)

## Вердикт

| Тема | Канон |
|------|--------|
| Соцлогин | Только **Keycloak Identity Broker** |
| Prodavan API / Flutter | **Не** хранят VK/Yandex client secrets, **не** делают свой OAuth |
| Flutter client | Один OIDC-клиент `prodavan-flutter`; соцкнопки = `kc_idp_hint` |
| Для кого | Люди: **Employee** / **Platform Admin** |
| Company | **Без** broker — `company_id` + password |

## Поток

```text
Flutter (optional kc_idp_hint=vk|yandex)
  → Keycloak authorize
  → Broker → VK | Yandex
  → KC user (same realm)
  → access_token (sub)
  → API JWKS → Employee.keycloak_sub (authz из DB)
```

Broker **прозрачен** для API: один `employees.keycloak_sub` на человека, независимо от локального пароля или соцпровайдера.

## Aliases

| Alias | Назначение |
|-------|------------|
| `vk` | VK IdP (включить позже, secrets вне git) |
| `yandex` | Yandex IdP |

Realm scaffold: пустой `identityProviders` + checklist в [`infra/keycloak/README.md`](../../../infra/keycloak/README.md).

## Flutter

- Кнопка «Войти» → authorize без hint (KC login form).
- Будущие соцкнопки → тот же `signIn`, параметр `kcIdpHint: 'vk' | 'yandex'`.
- Реализация: [`oidc_auth_service.dart`](../../../apps/flutter/lib/core/auth/oidc_auth_service.dart).

## Account linking и конфликты email

- First broker login / account linking — **в Keycloak**, не silent merge в Prodavan.
- Конфликт email (уже есть KC user) → linking UI / required actions KC.
- API **не** склеивает аккаунты по email без явного `sub`.

## Audit в app DB (опционально)

Таблица `identity_links` (`employee_id`, `provider`, `provider_subject`) — UX/support, **не** authz.  
Заполнение — later (event/admin sync из KC). Authz только `employees.keycloak_sub`.

## Запреты

- App-level OAuth к VK/Yandex из API или Flutter.
- Broker для Company org principal.
- Коммит IdP secrets в git / realm JSON.

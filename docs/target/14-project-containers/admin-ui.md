# ProjectContainer — control-plane UI

Один паттерн list/detail; разные scope.

## Admin

Nav: Обзор · Компании · AI-ключи · **Контейнеры** · Кабинеты.  
Scope: **все** проекты платформы.

## Company (канон parity)

Nav: … · **Контейнеры** · … ([03 ux](../03-companies/ux-contract.md)).  
Scope: только сотрудники / проекты **своей** компании.  
Те же actions: pause / resume / delete → Project → Port.

## List

Сорт: running/active сверху. Колонки: status, project, company, employee, provider, cabinet.

## Detail

Runtime · Resources (k8s) · Project actions · Org · AI · Usage · Ops.

Confirm — `AppConfirmPage`. Каскад через ProjectService → ContainerRuntimePort.

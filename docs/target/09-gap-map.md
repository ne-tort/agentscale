# Gap map — target ↔ legacy ↔ код

Сводка расхождений. Не backlog задач с оценками — карта для реализации.

| Target | Legacy docs | Код сейчас | Gap |
|--------|-------------|------------|-----|
| Platform Admin UI + metrics | M08 / admin screens | Flutter Admin (users-heavy) | Companies / keys / catalog shells по [ux-contract](01-platform-admin/ux-contract.md) |
| Company / Employee | Tenant / membership | User≈Company ещё в коде | Schema + shells по [session](10-identity-keycloak/session.md) |
| AI Provider Keys | env secrets | Models/API scaffold | Enforce resolve policy; UI |
| Mobile UI, no modals | widget-catalog | Core primitives + часть screens | ListTile→AppListItem; chat-first |
| Cabinet SPI + materialize | ADR-001 | SPI + materialize есть | Import ban specs/catalogs; grants enforce |
| `equipment-procurement` | electronics-procurement | Alias в registry | Stable id + deprecate electronics |
| Project container | agent-isolation | `local-ws` placeholder | Pod lifecycle + idle policy |
| Triggers / attachments | — | Scaffold API | Durable bus; chat attach UI |
| AgentProviderPort | bot SDK | Stub adapters | Sidecar + persist + AgentEvent |
| Keycloak OIDC | HS256 login | Dual-verify scaffold | Cutover + AppAuth |
| OpenClaw / GLM | mentions | Нет | Не внедрять |

## Решённые противоречия канона

| Было | Решение (зафиксировано) |
|------|-------------------------|
| Cabinets/company в JWT vs no reissue | Access token только OIDC; контекст `X-Cabinet-Id` / `X-Project-Id` + DB ([session](10-identity-keycloak/session.md)) |
| Password в Company create vs Keycloak-only | Invite = email → Keycloak; нет password в Prodavan API |
| `cli_subscription` vs ban personal CLI runtime | Учётная метка биллинга; **не** runtime credential ([02 domain](02-ai-provider-keys/domain.md)) |
| Company = «вид пользователя» vs KC roles | Company = org; Company account = Employee + `company.admin` |
| Admin создаёт «учётку» vs KC provisioning | Admin создаёт Company + KC invite company.admin |
| CompanyApi → Projects на диаграмме | Projects создаёт Employee в cabinet; Company смотрит metrics read-only |
| Admin metrics без usage events | `AgentEvent.usage` обязателен в порте ([adapter-port](08-agent-providers/adapter-port.md)) |
| Project triggers vs platform events | Две шины ([triggers](06-projects-runtime/triggers.md)) |
| AI key preference UI | `CompanyAgentPolicy` / Project override в resolve policy |
| «Отклонение = дефект» vs multi-wave gap | Канон docs finished; код догоняет волнами ниже |

## Декомпозиция BC + контракты

Изолируемые bounded contexts. Слои: `api` → `application` → `domain` ← `infrastructure`.

```mermaid
flowchart TB
  subgraph identity [Identity]
    KC[Keycloak_OIDC]
  end
  subgraph control [Control_plane]
    Admin[PlatformAdmin]
    Keys[AiProviderKeys]
    Catalog[CabinetCatalog]
  end
  subgraph org [Org_plane]
    Company[Company]
    Employees[Employees]
    Grants[CabinetGrants]
  end
  subgraph work [Work_plane]
    CabHost[CabinetHost]
    Pack[CabinetPack]
    Project[Project]
    Runtime[Container_Triggers]
    Agent[AgentProviderPort]
  end

  KC --> Admin
  KC --> Company
  KC --> Employees
  Admin --> Keys
  Admin --> Catalog
  Admin --> Company
  Catalog --> Grants
  Company --> Employees
  Company --> Grants
  Employees --> CabHost
  Grants --> CabHost
  CabHost --> Pack
  Employees --> Project
  Project --> Runtime
  Pack -->|"materialize"| Project
  Keys --> Agent
  Runtime --> Agent
```

| BC | Ответственность | Запрещено |
|----|-----------------|-----------|
| Identity | OIDC, JWKS, Principal→Employee | Cabinets, AI keys |
| Admin + Keys | Companies, grants catalog, keys, metrics | Workspace files, pack domain |
| Company / Employee | Invite/assign, enter cabinet | Pack SQL, issue JWT |
| Cabinets | SPI + UiModule | Container lifecycle, raw AI secrets |
| Projects / Runtime | Project, container_ref, triggers, attachments | Procurement logic |
| Agent | Port + adapters + usage | GLM, OpenClaw, CLI sub as key |
| UI core | Primitives + UX system | Feature-specific ListTile zoos |

### Волны кода (после канона)

1. Identity schema Company/Employee + headers enforcement  
2. Admin/Company/Employee shells по UX contracts  
3. Key resolve + grants enforce  
4. SPI import ban + equipment id  
5. Agent sidecar + persist + AgentEvent  
6. Container/triggers/attachments productionize  

### Явно не делать

- OpenClaw; GLM; personal Max/Pro как tenant runtime credentials  
- Канонизация `POST /auth/login`  
- Platform `api/v1` → pack service imports  
- Admin/Company логика внутри Employee screens  
- JWT reissue на switch/open  

## Ссылки

- Identity session: [10-identity-keycloak/session.md](10-identity-keycloak/session.md)
- UX system: [07-ui-mobile-core/ux-system.md](07-ui-mobile-core/ux-system.md)
- Keys policy: [02-ai-provider-keys/domain.md](02-ai-provider-keys/domain.md)
- Cabinet contract: [05-cabinets/module-contract.md](05-cabinets/module-contract.md)
- Agent port: [08-agent-providers/adapter-port.md](08-agent-providers/adapter-port.md)
- Legacy: [../LEGACY.md](../LEGACY.md)

# Package layout (L00–L08)

```text
prodavan/
  api/              # HTTP adapters (routes, exception handlers, deps)
  application/      # identity, ai_keys, cabinets, admin, projects, agent use-cases
  domain/           # errors, identity, ai_keys, cabinets, admin, projects, agent
  infrastructure/   # persistence, auth/jwt, keycloak, secrets, cabinets, projects, agent
  core/             # P0: LifespanManager + infra managers (Redis, …)
  config/           # Settings / env
  main.py           # FastAPI factory (lifespan → core.wiring)
```

Inbound: `api` → `application` → `domain` ← `infrastructure`.  
`core` — wiring/lifespan facades; application uses managers, not raw redis/kafka clients.  
Do not import `api` from `domain` / `application`.

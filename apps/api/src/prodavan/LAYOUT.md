# Package layout (L00–L08)

```text
prodavan/
  api/              # HTTP adapters (routes, exception handlers, deps)
  application/      # identity, ai_keys, cabinets, admin, projects, agent use-cases
  domain/           # errors, identity, ai_keys, cabinets, admin, projects, agent
  infrastructure/   # persistence, auth/jwt, keycloak, secrets, cabinets, projects, agent
  config/           # Settings / env
  main.py           # FastAPI factory
```

Inbound: `api` → `application` → `domain` ← `infrastructure`.  
Do not import `api` from `domain` / `application`.

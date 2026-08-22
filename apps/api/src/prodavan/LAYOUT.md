# Package layout (L00)

```text
prodavan/
  api/              # HTTP adapters (routes, exception handlers)
  application/      # use-cases (L01+)
  domain/           # entities + AppError (L01+)
  infrastructure/   # DB, future vault/KC clients
  config/           # Settings / env
  main.py           # FastAPI factory
```

Inbound: `api` → `application` → `domain` ← `infrastructure`.  
Do not import `api` from `domain` / `application`.

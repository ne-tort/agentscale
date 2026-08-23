# Package layout (L00–L08)

prodavan/
  api/              # HTTP adapters (routes, exception handlers, deps)
  application/      # identity, ai_keys, cabinets, admin, projects, agent use-cases
  domain/           # errors, identity, ai_keys, cabinets, admin, projects, agent
  infrastructure/   # persistence, auth/jwt, keycloak, secrets, cabinets, projects, agent
  core/             # P0: LifespanManager + infra managers (Redis, object store, Kafka, Celery)
    jobs/           # C-JOBS task names + Celery tasks + enqueue helpers
    events/         # C-EVENT-BUS envelopes + publish helpers
    middleware.py   # register_cors (and future middleware register)
    infra/cache.py  # C-CACHE helpers
  config/           # Settings / env
  main.py           # FastAPI factory (lifespan → core.wiring)
```

Inbound: `api` → `application` → `domain` ← `infrastructure`.  
`core` — wiring/lifespan facades; application uses managers, not raw redis/kafka/celery clients.  
Do not import `api` from `domain` / `application`.

Celery worker (separate process)::

```text
celery -A prodavan.core.infra.worker_manager.celery_app worker -l info -B
```
